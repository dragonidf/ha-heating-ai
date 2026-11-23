"""Private evidence for adaptive learning. This module has no control interface."""
from bisect import bisect_left, bisect_right
from collections import Counter
from datetime import datetime
from hashlib import sha256
import json
import math
import os
from pathlib import Path
from statistics import median
from tempfile import NamedTemporaryFile
from threading import Lock

from heating_environment import daylight, forecast_step, weather_factor
from heating_model import BOUNDS, comfort_at, number, outdoor_at, transitions

SCHEMA = 1
# The byte limit is deliberately below the storage ceiling to bound decoding RSS.
MAX_BYTES = 512 * 1024
RETENTION = 60 * 86400
MAX_WEATHER_AGE = 3 * 3600
IDENTITY = ("epoch", "source", "thermal_model_version", "equipment_version", "boiler_power_mode")
FIT_FIELDS = ("floor_air", "room_loss")
_JOB_LOCK = Lock()


def mode_for(value):
    if value in ("disabled", "observe", "candidate"):
        return value, None
    return "disabled", "invalid_adaptive_learning_mode"


def report_time(row):
    """Only source-qualified reports qualify; HA republish times alone do not."""
    stamp, at = number(row.get("ts")), number(row.get("room_reported_at"))
    if (row.get("report_verified") is True and row.get("room_valid") is True
            and row.get("source") == "Вітальня" and stamp is not None and at is not None
            and 0 <= stamp-at <= 300 and number(row.get("room")) is not None):
        return at
    return None


def row_reasons(row, *, strict):
    reasons = set(row.get("shadow_reasons") or ())
    power = number(row.get("power"))
    if power is None or not 0 <= power <= 10.5:
        reasons.add("missing_kws")
    if row.get("power_estimated") or str(row.get("power_source")).startswith("fallback"):
        reasons.add("estimated_power")
    if strict and (row.get("power_source"), row.get("power_quality")) != ("kws_phases", "measured"):
        reasons.add("unqualified_kws")
    if row.get("grid") != "on":
        reasons.add("unknown_grid" if row.get("grid") != "off" else "unqualified_outage")
    if row.get("voltage_qualified") is not True:
        reasons.add("unknown_or_unqualified_voltage")
    if row.get("manual_extra_active") or row.get("execution") == "manual_or_native":
        reasons.add("manual_extra_request")
    if strict and (row.get("manual_extra_active") is not False
                   or row.get("execution") not in ("automatic", "idle")):
        reasons.add("unknown_execution")
    if strict and any(row.get(key) is None for key in IDENTITY):
        reasons.add("missing_identity")
    if strict and row.get("boiler_power_mode") == "unconfirmed":
        reasons.add("unconfirmed_equipment")
    if number(row.get("return")) is None or number(row.get("outside")) is None:
        reasons.add("missing_thermal_input")
    if row.get("room_source_disagreement") or row.get("room_sources_suspect"):
        reasons.add("room_conflict")
    if strict and row.get("room_valid") is not True:
        reasons.add("unavailable_primary")
    fire = number(row.get("fire"))
    if row.get("fire_valid") is not True or fire is None:
        reasons.add("unknown_fireplace")
    elif row.get("fire_active") or fire > (0.2 if strict else 2):
        reasons.add("fireplace")
    if row.get("shadow_ventilation") is None:
        reasons.add("unknown_ventilation")
    if strict and row.get("circulation_active"):
        reasons.add("circulation")
    if (number(row.get("external_gain")) or 0.) > .1 or abs(number(row.get("residual_gain")) or 0.) > .1:
        reasons.add("unexplained_gain")
    issue, stamp = number(row.get("weather_issue_at")), number(row.get("ts"))
    if issue is None or stamp is None:
        reasons.add("missing_weather_issue")
    elif issue > stamp:
        reasons.add("forecast_leakage")
    elif stamp-issue > MAX_WEATHER_AGE:
        reasons.add("stale_weather")
    if strict and number(row.get("solar_factor")) is None:
        reasons.add("missing_solar_context")
    if row.get("interpolated") or row.get("duplicate"):
        reasons.add("interpolation_or_duplicate")
    return reasons


def audit_transitions(samples, *, include_pairs=True):
    """Exact unchanged legacy selection with independent multi-label diagnostics."""
    legacy = transitions(samples)
    selected = {(a["ts"], b["ts"]) for a, b, _ in legacy}
    counts, pairs = Counter(), []
    unique = {at for row in samples if (at := report_time(row)) is not None}
    legacy_minutes = 0.
    for a, b in zip(samples, samples[1:]):
        start, end = number(a.get("ts")), number(b.get("ts"))
        eligible = (start, end) in selected
        if eligible:
            legacy_minutes += (end-start)/60
        reasons = row_reasons(a, strict=False) | row_reasons(b, strict=False)
        if start is None or end is None or not 180 <= end-start <= 420:
            reasons.add("legacy_interval")
        if start is not None and end is not None and end-start > 600:
            reasons.add("telemetry_gap")
        if not (a.get("quality") and b.get("quality")):
            reasons.add("legacy_quality")
        if (a.get("environment_version") and (number(a.get("daylight_weight")) or 0.)>.1
                and (number(a.get("power")) or 0.)<1.5):
            reasons.add("legacy_daylight_idle")
        if any(r.get("environment_version",0)>=6 and not r.get("astronomy_valid") for r in (a,b)):
            reasons.add("missing_astronomy")
        if start is not None and start < datetime.fromisoformat("2026-10-07T00:00:00+03:00").timestamp():
            reasons.add("before_probe_installation")
        ra, rb = number(a.get("room_reported_at")), number(b.get("room_reported_at"))
        if ra is None or rb is None:
            reasons.add("room_report_missing")
        elif rb <= ra:
            reasons.add("room_report_repeated")
        if any(number(row.get("ts")) is not None and number(row.get("room_reported_at")) is not None
               and not 0 <= row["ts"]-row["room_reported_at"] <= 300 for row in (a, b)):
            reasons.add("room_report_stale")
        if any(row.get("room_source_status") not in ("agree",) for row in (a, b)):
            reasons.add("secondary_unconfirmed")
        if any(a.get(key) != b.get(key) for key in IDENTITY):
            reasons.add("identity_changed")
        for key, threshold in (("room", .6), ("return", 5.)):
            x, y = number(a.get(key)), number(b.get(key))
            if x is not None and y is not None and abs(x-y) > threshold:
                reasons.add(key+"_jump")
        if (number(a.get("energy")) is not None and number(b.get("energy")) is not None
                and b["energy"] < a["energy"]):
            reasons.add("counter_reset")
        if b.get("measurement_version") == 3 and number(b.get("interval_power")) is None:
            reasons.add("missing_interval_power")
        counts.update(reasons)
        if include_pairs:
            pairs.append(dict(start=start, end=end, eligible=eligible, reasons=sorted(reasons)))
    total = max(0, len(samples)-1)
    return dict(eligible_pairs=len(selected), eligible_samples=sum(bool(r.get("quality")) for r in samples),
                pairs=pairs, counts=dict(counts), percentages={k: round(v*100/max(1,total),2) for k,v in counts.items()},
                unique_primary_reports=len(unique), observed_independent_minutes=legacy_minutes,
                heat_minutes=sum(dt*60 for a,_,dt in legacy if a["power"]>=1.5),
                idle_minutes=sum(dt*60 for a,_,dt in legacy if a["power"]<=.05),
                independent_days=len({_local_day(a["ts"],None) for a,_,_ in legacy}),
                equipment_states=dict(Counter(str(r.get("boiler_power_mode","unknown")) for r in samples)))


def _local_day(stamp, tz):
    return datetime.fromtimestamp(stamp, tz).date().isoformat()


def _spans(rows, times, start, end):
    left, right = bisect_right(times, start)-1, bisect_left(times, end)
    if left < 0 or right >= len(times):
        return None
    return zip(rows[left:right], rows[left+1:right+1])


def _continuous(rows, times, start, end, identity):
    pairs = _spans(rows, times, start, end)
    if pairs is None:
        return [], {"missing_bracket"}
    reasons, steps = set(), []
    for a, b in pairs:
        x, y = a["ts"], b["ts"]
        if not 0 < y-x <= 600:
            reasons.add("telemetry_gap")
        reasons.update(row_reasons(a, strict=True))
        reasons.update(row_reasons(b, strict=True))
        if any(tuple(row.get(k) for k in IDENTITY) != identity for row in (a,b)):
            reasons.add("identity_changed")
        if a.get("shadow_ventilation") != b.get("shadow_ventilation"):
            reasons.add("ventilation_changed")
        if (number(a.get("return")) is not None and number(b.get("return")) is not None
                and abs(b["return"]-a["return"])>5):
            reasons.add("return_jump")
        if (number(a.get("energy")) is not None and number(b.get("energy")) is not None
                and b["energy"]<a["energy"]):
            reasons.add("counter_reset")
        # Left-observed physical inputs. No power, weather or room data from
        # after either boundary is used to extend the measured trajectory.
        left, right = max(start,x), min(end,y)
        if right>left:
            measured = number(b.get("interval_kw")) if left==x and right==y else None
            if "interval_kw" in b and number(b["interval_kw"]) is None:
                reasons.add("missing_measured_interval")
            kw = measured if measured is not None else a.get("power")
            if number(kw) is None or not 0<=kw<=10.5:
                reasons.add("invalid_interval_energy")
            steps.append([right-left,a.get("outside"),kw,a.get("solar_factor"),a.get("return")])
    if sum(step[0] for step in steps)<.95*(end-start):
        reasons.add("coverage_below_95_percent")
    return steps, reasons


def build_observation_windows(samples, tz):
    rows = [row for row in samples if number(row.get("ts")) is not None]
    times = [row["ts"] for row in rows]
    reports, counts, windows = {}, Counter(), []
    if any(a >= b for a, b in zip(times, times[1:])):
        return dict(windows=[], eligible_minutes=0., unique_primary_reports=0,
                    rejection_counts={"duplicate_or_nonmonotonic_telemetry":1}, independent_days=0,
                    heat_minutes=0., idle_minutes=0.)
    for row in rows:
        if (at := report_time(row)) is not None:
            reports.setdefault(at, row)
    anchor = None
    for end, row in sorted(reports.items()):
        if anchor is None:
            anchor = end, row
            continue
        start, first = anchor
        if end-start < 900:
            continue
        anchor = end, row
        if end-start > 9000:
            counts["anchor_expired"] += 1
            continue
        day = _local_day(start, tz)
        if day != _local_day(end, tz):
            counts["local_midnight"] += 1
            continue
        identity = tuple(first.get(k) for k in IDENTITY)
        steps, reasons = _continuous(rows,times,start,end,identity)
        reasons |= row_reasons(first,strict=True) | row_reasons(row,strict=True)
        if abs(row["room"]-first["room"]) > .6:
            reasons.add("room_jump")
        if reasons:
            counts.update(reasons)
            continue
        heating = sum(seconds for seconds, _, kw, _, _ in steps if kw >= 1.5)
        idle = sum(seconds for seconds, _, kw, _, _ in steps if kw <= .05)
        sun = sum(seconds*factor for seconds, _, _, factor, _ in steps)/(end-start)
        windows.append(dict(start=start,end=end,day=day,room_start=first["room"],room_end=row["room"],
                            floor=steps[0][4],steps=steps,heat=heating,idle=idle,sun=sun,
                            equipment=list(identity[2:]),vent=first["shadow_ventilation"]))
        anchor = None  # an independent physical report belongs to at most one window
    anchor_at = None
    if anchor and rows and rows[-1]["ts"]-anchor[0]<=9000:
        at, row = anchor
        _, issues = _continuous(rows,times,at,rows[-1]["ts"],tuple(row.get(k) for k in IDENTITY))
        if not issues and not row_reasons(row,strict=True):
            anchor_at = at
    observation = rows[-1] if rows else {}
    return dict(windows=windows,rejection_counts=dict(counts),unique_primary_reports=len(reports),
                last_verified_anchor=anchor_at,
                current_observation_quality={
                    "primary_fresh": observation.get("primary_fresh",False),
                    "primary_independent": report_time(observation) is not None,
                    "secondary_corroborated": observation.get("room_source_status")=="agree",
                    "reasons":sorted(row_reasons(observation,strict=True))},
                eligible_minutes=sum((w["end"]-w["start"])/60 for w in windows),
                independent_days=len({w["day"] for w in windows}),
                heat_minutes=sum(w["heat"] for w in windows)/60,
                idle_minutes=sum(w["idle"] for w in windows)/60)


def _metrics(errors):
    if not errors:
        return dict(n=0,mae=None,bias=None,median=None,p95=None)
    absolute = sorted(map(abs,errors))
    return dict(n=len(errors),mae=sum(absolute)/len(errors),bias=sum(errors)/len(errors),
                median=median(absolute),p95=absolute[math.ceil(.95*len(errors))-1])


def _score(windows, coefficients, solar=0., ventilation=None):
    errors = []
    floor_air, loss = coefficients["floor_air"], coefficients["room_loss"]
    heat_gain, decay = coefficients["heat_gain"], coefficients["floor_decay"]
    for window in windows:
        ventilation_loss = loss
        if ventilation and ventilation.get("validated") and ventilation.get("model_version")==coefficients.get("version"):
            ventilation_loss = (ventilation["base_room_loss"] + ventilation["recuperator"]*window["vent"][0]
                                + ventilation["bath"]*window["vent"][1])
        room, floor = window["room_start"], window["floor"]
        for seconds, outside, kw, sun, _ in window["steps"]:
            hours = seconds/3600
            difference = floor-room
            room += (floor_air*difference-ventilation_loss*(room-outside)+solar*sun)*hours
            floor += (heat_gain*kw-decay*difference)*hours
        errors.append(room-window["room_end"])
    return _metrics(errors)


def _split(windows):
    days = sorted({w["day"] for w in windows})
    for count in range(2, len(days)):
        holdout = [w for w in windows if w["day"] >= days[-count]]
        if len(holdout)*5>=len(windows):
            return [w for w in windows if w["day"]<days[-count]], holdout
    return [], []


def _coverage(windows):
    return dict(seconds=sum(w["end"]-w["start"] for w in windows),
                days=len({w["day"] for w in windows}),
                heat=sum(w["heat"] for w in windows), idle=sum(w["idle"] for w in windows),
                heat_days=len({w["day"] for w in windows if w["heat"]}),
                idle_days=len({w["day"] for w in windows if w["idle"]}))


def evaluate_windows(windows, model, ventilation=None):
    """Bounded offline fit. Holdout is selected by whole, later local days."""
    # Fit v6 only with one current equipment and mode identity.
    if not windows or any(number(model.get(k)) is None for k in BOUNDS):
        return dict(status="insufficient_data", blockers=["no_windows"])
    latest = windows[-1]["equipment"]
    windows = [w for w in windows if w["equipment"] == latest and latest[0] == model.get("version")]
    qualified_vent = ventilation and ventilation.get("validated") and ventilation.get("model_version")==model.get("version")
    thermal = [w for w in windows if w["sun"]<=.02 and
               (qualified_vent or max(w["vent"])==0.)]
    coverage = _coverage(thermal)
    returns = [step[4] for w in thermal for step in w["steps"]]
    blockers = [key for key, ok in (
        ("independent_hours", coverage["seconds"]>=8*3600), ("independent_days", coverage["days"]>=5),
        ("heating_coverage", coverage["heat"]>=2*3600 and coverage["heat_days"]>=2),
        ("idle_coverage", coverage["idle"]>=3*3600 and coverage["idle_days"]>=2),
        ("return_excitation", bool(returns) and max(returns)-min(returns)>=3.)) if not ok]
    train, holdout = _split(thermal)
    if (not train or not holdout or not any(w["heat"] for w in train) or not any(w["idle"] for w in train)
            or not any(w["heat"] for w in holdout) or not any(w["idle"] for w in holdout)):
        blockers.append("independent_holdout")
    if blockers:
        return dict(status="insufficient_data", blockers=blockers, coverage=coverage)
    # Bound both the grid and per-candidate displacement; widening belongs to a
    # separate qualification for equipment gain and decay.
    old = _score(train,model,ventilation=ventilation)
    best, objective = model, old["mae"]
    for a in (-.01,0.,.01):
        for b in (-.01,0.,.01):
            candidate = {**model,"floor_air":model["floor_air"]*(1+a),"room_loss":model["room_loss"]*(1+b)}
            if not all(BOUNDS[k][0]<=candidate[k]<=BOUNDS[k][1] for k in FIT_FIELDS):
                continue
            score = _score(train,candidate,ventilation=ventilation)["mae"]
            if score<objective:
                best,objective=candidate,score
    baseline = _score(holdout,model,ventilation=ventilation)
    shadow = _score(holdout,best,ventilation=ventilation)
    qualified = best!=model and shadow["mae"]<baseline["mae"] and shadow["p95"]<=baseline["p95"]
    return dict(status="holdout_passed" if qualified else "holdout_rejected",
                blockers=[] if qualified else ["held_out_no_improvement"],
                coverage=coverage, train_windows=len(train), holdout_days=len({w["day"] for w in holdout}),
                old=baseline,shadow=shadow, coefficients={k:best[k] for k in FIT_FIELDS} if qualified else None,
                equipment=latest)


def evaluate_solar(windows, model, commissioning=.283, ventilation=None):
    windows = [w for w in windows if w["sun"]>0.05 and w["idle"]>=w["end"]-w["start"] and
               (max(w["vent"])==0. or ventilation and ventilation.get("validated")
                and ventilation.get("model_version")==model.get("version"))]
    train, holdout = _split(windows)
    days = len({w["day"] for w in train})
    result = dict(days=days,windows=len(train),comfort_credit=False,model_certificate_validated=False)
    if days<3 or len(train)<12 or not holdout:
        return dict(result,status="insufficient_data")
    coefficient = min((.025*i for i in range(33)),key=lambda c:_score(train,model,c,ventilation)["mae"])
    zero, previous, shadow = (_score(holdout,model,c,ventilation) for c in (0.,commissioning,coefficient))
    qualified = (shadow["mae"]<=.9*min(zero["mae"],previous["mae"])
                 and shadow["p95"]<=min(zero["p95"],previous["p95"]))
    return dict(result,status="holdout_passed" if qualified else "holdout_rejected",coefficient=coefficient,
                zero=zero,commissioning=previous,shadow=shadow,holdout_days=len({w["day"] for w in holdout}))


def solar_context_at(stamp, tz, weather, issue, geometry):
    if (not weather or not geometry or issue is None or not 0<=stamp-issue<=MAX_WEATHER_AGE):
        return None
    current = next((r for r in reversed(weather) if 0<=stamp-r["ts"]<3600),None)
    if current is None:
        return None
    when = datetime.fromtimestamp(stamp,tz)
    if when.date().isoformat() not in (geometry.get("days") or {}):
        return None
    sun = daylight(when,geometry)
    clouds = number(current.get("cloud_coverage"))
    if (sun and not (clouds is not None and 0<=clouds<=100)
            and current.get("condition") not in
            ("sunny","clear","clear-night","partlycloudy","cloudy","rainy","pouring","snowy")):
        return None
    return sun*weather_factor(current.get("condition"),current.get("cloud_coverage"))


def prospective_gate(rows, *, issued_at, now, observation_days=None):
    blockers, results = [], {}
    if observation_days is None:
        observation_days = {r["day"] for r in rows if r.get("valid") and issued_at<=r["start"]<=r["end"]<=now}
    if now-issued_at<14*86400 or len(set(observation_days))<14:
        blockers.append("prospective_14_days")
    used_days = set()
    for h,count,days in ((1,12,3),(3,6,2),(6,1,1)):
        qualifying, end = [], issued_at
        for row in sorted(rows,key=lambda r:r["start"]):
            if (row["horizon"]==h and row.get("valid") and row["start"]>=end
                    and all(number(row.get(key)) is not None for key in ("old_error","shadow_error"))
                    and row["end"]<=now):
                qualifying.append(row)
                end=row["end"]
        day_count = len({r["day"] for r in qualifying})
        used_days.update(r["day"] for r in qualifying)
        old, shadow=(_metrics([r[k] for r in qualifying]) for k in ("old_error","shadow_error"))
        groups = {}
        for row in qualifying:
            for group in row.get("categories",()):
                groups.setdefault(group,[]).append(row)
        results[str(h)]=dict(old=old,shadow=shadow,days=day_count,
            strata={name:{key:_metrics([row[key] for row in group]) for key in ("old_error","shadow_error")}
                    for name,group in groups.items()},
            energy_error=_metrics([r["energy_error"] for r in qualifying if "energy_error" in r]),
            planned_minutes=sum(r.get("planned_minutes",0) for r in qualifying),
            measured_minutes=sum(r.get("actual_minutes",0) for r in qualifying),
            upper_risk_events=sum(r.get("upper_risk",0)>0 for r in qualifying),
            comfort_deficit_events=sum(r.get("comfort_deficit",0)>0 for r in qualifying),
            after_stop_peak_shift=_metrics([r["peak_delta"] for r in groups.get("after_stop",[])
                                           if "peak_delta" in r]))
        if any(r.get("guard_ok") is not True for r in qualifying):
            blockers.append(f"comfort_or_upper_risk_{h}h")
        if (len(qualifying)<count or day_count<days or not old["mae"]
                or shadow["mae"]>(.9 if h in (1,3) else 1.)*old["mae"]
                or shadow["p95"]>old["p95"]):
            blockers.append(f"prospective_{h}h")
        # Require evidence for every constrained category; empty strata are unknown.
        if h in (1,3):
            for group in ("night_idle","cold_or_constrained","heating","after_stop"):
                subset=[r for r in qualifying if group in r.get("categories",())]
                if (not subset or not all(r.get("guard_ok") is True for r in subset)
                        or sum(abs(r["shadow_error"]) for r in subset)>sum(abs(r["old_error"]) for r in subset)):
                    blockers.append(f"{group}_{h}h")
    if len(used_days)<5:
        blockers.append("prospective_5_days")
    return dict(evidence_passed=not blockers,promotion_allowed=False,blockers=blockers,metrics=results)


def review_limits(active, proposed, daily_baseline, *, manual_approval=False, evidence=False,
                  degraded=True, already_applied=False):
    """Check hypothetical deltas for a future review. Always non-actuating."""
    keys_ok = proposed and set(proposed)<=set(FIT_FIELDS) and all(
        (value := number(proposed[k])) is not None and not isinstance(proposed[k],bool)
        and BOUNDS[k][0]<=value<=BOUNDS[k][1] and abs(value-active[k])<=.01*abs(active[k])+1e-12
        and abs(value-daily_baseline[k])<=.02*abs(daily_baseline[k])+1e-12
        for k in proposed)
    return dict(review_eligible=bool(keys_ok and manual_approval and evidence and not degraded
                                    and not already_applied),promotion_allowed=False)


def _encode(value):
    return json.dumps(value,sort_keys=True,separators=(",",":"),allow_nan=False).encode()


def load_evidence(path):
    with Path(path).open("rb") as stream:
        data=stream.read(MAX_BYTES+1)
    if len(data)>MAX_BYTES:
        raise ValueError("adaptive evidence exceeds size limit")
    record=json.loads(data)
    if not isinstance(record,dict) or record.get("schema")!=SCHEMA or "payload" not in record:
        raise ValueError("unknown adaptive evidence schema")
    payload=record["payload"]
    if (not isinstance(payload,dict) or record.get("sha256")!=sha256(_encode(payload)).hexdigest()
            or not all(isinstance(payload.get(key),list) for key in ("windows","forecasts","comparisons"))
            or not isinstance(payload.get("report"),dict)):
        raise ValueError("invalid adaptive evidence checksum or payload")
    return payload


def save_evidence(path, payload):
    path=Path(path)
    raw=_encode(dict(schema=SCHEMA,sha256=sha256(_encode(payload)).hexdigest(),payload=payload))
    if len(raw)>MAX_BYTES:
        raise ValueError("adaptive evidence exceeds size limit")
    temporary=None
    try:
        with NamedTemporaryFile(dir=path.parent,prefix=path.name+".",delete=False) as stream:
            temporary=Path(stream.name)
            stream.write(raw)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary,path)
        temporary=None
        fd=os.open(path.parent,os.O_DIRECTORY)
        try:
            os.fsync(fd)
        finally:
            os.close(fd)
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)


def freeze_forecast(now, room, floor, outside, fire, weather, issue, settings, forecast, model,
                    candidate, epoch):
    """Capture immutable issue-time inputs and the original predictions."""
    start = now.timestamp()
    if not weather or issue is None or not 0<=start-issue<=MAX_WEATHER_AGE:
        return None
    end = start+21600
    relevant = sorted((dict(r) for r in weather if start-3600 < r["ts"] <= end),key=lambda r:r["ts"])
    known = start
    for row in relevant:
        if row["ts"]>known:
            break
        known=max(known,row["ts"]+3600)
    fine = [point for row in forecast.get("_calculation_curve", forecast["curve"])
            for point in row.get("_fine",[row])]
    points = {}
    for horizon in (1,3,6):
        due = start+3600*horizon
        point = next((r for r in fine if r["ts"]>=due-.001),None)
        if (point is None or point["ts"]>due+60 or not relevant or relevant[0]["ts"]>start
                or known<point["ts"]):
            continue
        at = datetime.fromtimestamp(point["ts"]-.001,now.tzinfo)
        _, low, high, zone = comfort_at(at,settings)
        points[str(horizon)] = [point["ts"],point["temperature"],
                point.get("upper_temperature",point["temperature"]),low,high,zone]
    if not points:
        return None
    last = max(p[0] for p in points.values())
    segments = [[max(start,s["start"]),min(last,s["end"]),s["power_kw"]]
                for s in forecast.get("heat_segments",[]) if s["end"]>start and s["start"]<last]
    climate = {k:settings[k] for k in ("environment","thermal_model_version","ventilation_model",
                                      "ventilation_expected") if k in settings}
    # Forecast/reference settings are immutable to the controller after issue.
    # Round-trip the small selected fields to keep the sidecar independent.
    climate = json.loads(_encode(climate))
    return dict(start=start, issue_at=issue, provider="heating_weather_forecast",
                weather=relevant, weather_known_until=known,
                room=room,floor=floor,outside=outside,fire=fire, settings=climate,
                model=dict(model),candidate=candidate,epoch=epoch,points=points,
                segments=segments,identity=[epoch,"Вітальня",model.get("version"),
                    settings.get("equipment_version"),(settings.get("modulation_profile") or {}).get("physical_mode")])


def _frozen_predictions(snapshot, tz, finish):
    start, segments = snapshot["start"], snapshot["segments"]
    weather, settings = snapshot["weather"], snapshot["settings"]
    horizon_targets = {v[0] for v in snapshot["points"].values() if v[0]<=finish}
    boundaries = sorted({start,finish,*horizon_targets,
                         *(start+i*300 for i in range(1,math.ceil((finish-start)/300))),
                         *(t for a,b,_ in segments for t in (a,b) if start<t<finish)})
    room, floor = snapshot["room"], snapshot["floor"]
    states = [(room,floor),(room,floor)]
    models = (snapshot["model"],{**snapshot["model"],**(snapshot["candidate"] or {}).get("coefficients",{})})
    predictions, peaks = {}, [room,room]
    for a,b in zip(boundaries,boundaries[1:]):
        when = datetime.fromtimestamp(a,tz)
        kw = next((kw for lo,hi,kw in segments if lo<=a and b<=hi),0.)
        fire = snapshot["fire"]*math.exp(-(a-start)/7200)
        outside = outdoor_at(weather,when,snapshot["outside"])
        for i,(model, (r,f)) in enumerate(zip(models,states)):
            states[i] = forecast_step(r,f,outside,kw,fire,model,(b-a)/3600,when,weather,settings)
            peaks[i] = max(peaks[i],states[i][0])
        if b in horizon_targets:
            predictions[b] = states[1][0]-states[0][0], peaks[1]-peaks[0]
    return predictions


def compare_frozen(snapshot, rows, tz):
    """Evaluate the frozen plan against qualified reports and measured execution."""
    times = [r["ts"] for r in rows]
    start = snapshot["start"]
    if (not times or not 0<=start-snapshot["issue_at"]<=MAX_WEATHER_AGE
            or any(a>=b for a,b in zip(times,times[1:]))):
        return []
    checks = []
    for horizon,(due,old,upper,low,high,zone) in snapshot["points"].items():
        if due>snapshot["weather_known_until"] or due>times[-1]:
            continue
        candidates = ((at,r) for r in rows[bisect_left(times,due-300):bisect_right(times,due+600)]
                      if (at:=report_time(r)) is not None and abs(at-due)<=60)
        chosen = min(candidates,key=lambda pair:abs(pair[0]-due),default=None)
        if chosen is None:
            continue
        at,row = chosen
        steps,reasons = _continuous(rows,times,start,due,tuple(snapshot["identity"]))
        reasons |= row_reasons(row, strict=True)
        actual_kwh = sum(seconds*kw/3600 for seconds,_,kw,_,_ in steps if kw is not None)
        planned_kwh = sum(max(0.,min(due,b)-a)*kw/3600 for a,b,kw in snapshot["segments"])
        planned_minutes = sum(max(0.,min(due,b)-a)/60 for a,b,_ in snapshot["segments"])
        actual_minutes = sum(seconds/60 for seconds,_,kw,_,_ in steps if kw is not None and kw>=1.5)
        if abs(actual_kwh-planned_kwh)>max(.05,planned_kwh*.05) or abs(actual_minutes-planned_minutes)>5:
            reasons.add("plan_execution_mismatch")
        # Execution timing matters even when aggregate energy happens to agree.
        cursor=start
        for seconds,_,kw,_,_ in steps:
            right=cursor+seconds
            planned=sum(max(0.,min(right,b)-max(cursor,a)) for a,b,_ in snapshot["segments"])
            if kw is None or (planned if kw<1.5 else seconds-planned)>60:
                reasons.add("execution_timing_mismatch")
                break
            cursor=right
        checks.append((int(horizon), due, old, upper, low, high, zone, row["room"], reasons,
                       planned_kwh,actual_kwh,planned_minutes,actual_minutes))
    if not checks:
        return []
    predictions = _frozen_predictions(snapshot,tz,max(check[1] for check in checks))
    results=[]
    for h,due,old,upper,low,high,zone,actual,reasons,plan_kwh,actual_kwh,plan_min,actual_min in checks:
        delta,peak_delta = predictions[due]
        shadow = old+delta
        categories=[]
        if zone=="Сон" and actual_min==0:
            categories.append("night_idle")
        if snapshot["outside"]<=0:
            categories.append("cold_or_constrained")
        if actual_min:
            categories.append("heating")
        if (actual_min and snapshot["segments"] and
                not any(a<due<=b for a,b,_ in snapshot["segments"])):
            categories.append("after_stop")
        results.append(dict(start=start,end=due,day=_local_day(start,tz),horizon=h,
            old_error=old-actual,shadow_error=shadow-actual,valid=not reasons,reasons=sorted(reasons),
            guard_ok=(max(0.,upper+max(delta,peak_delta)-high)<=max(0.,upper-high) and
                      max(0.,low-shadow)<=max(0.,low-old)),
            categories=categories,planned_kwh=plan_kwh,actual_kwh=actual_kwh,
            energy_error=actual_kwh-plan_kwh,planned_minutes=plan_min,actual_minutes=actual_min,
            upper_risk=max(0.,upper+max(delta,peak_delta)-high),comfort_deficit=max(0.,low-shadow),
            peak_delta=peak_delta))
    return results


def run_shadow(path, rows, forecasts, legacy_rows, model, ventilation, mode, now):
    """Existing HA executor job. Serialize private writes across config reloads."""
    if mode not in ("observe","candidate"):
        return dict(candidate=None,warning=None)
    with _JOB_LOCK:
        try:
            return _run_shadow(path, rows, forecasts, legacy_rows, model, ventilation, mode, now)
        except (ValueError, TypeError, KeyError, OSError, IndexError, AttributeError):
            return dict(candidate=None, warning="adaptive_evidence_failed")


def _run_shadow(path, rows, forecasts, legacy_rows, model, ventilation, mode, now):
    now_stamp, tz = now.timestamp(), now.tzinfo
    empty = dict(windows=[],forecasts=[],comparisons=[],report={})
    try:
        previous = load_evidence(path)
    except FileNotFoundError:
        previous = empty
    except (ValueError, TypeError, KeyError, OSError):
        # Keep damaged evidence for diagnosis. Never infer a candidate on it.
        return dict(candidate=None, warning="adaptive_evidence_corrupt")
    prior_windows = [w for w in previous["windows"] if w["end"]>=now_stamp-RETENTION]
    latest = prior_windows[-1]["end"] if prior_windows else 0.
    # Keep physical brackets, but consume each verified endpoint only once.
    fresh = [dict(r,report_verified=False) if (report_time(r) or math.inf)<=latest else r for r in rows]
    recent = build_observation_windows(fresh,tz)
    windows = (prior_windows+recent.pop("windows"))[-2000:]
    audit=audit_transitions(legacy_rows,include_pairs=False)
    audit.pop("pairs")
    old_candidate=(previous["report"].get("candidate") if mode=="candidate" else None)
    identity = windows[-1]["equipment"] if windows else None
    candidate = old_candidate if (old_candidate and old_candidate.get("baseline")==model
                                  and old_candidate.get("equipment")==identity) else None
    available = {s["start"]:s for s in (*previous["forecasts"],*forecasts)}
    comparisons = previous["comparisons"] if candidate else []
    accepted = {(r["start"],r["horizon"]):r for r in comparisons if r["end"]>=now_stamp-RETENTION}
    baseline = {(r["start"],r["horizon"]):r for r in previous["report"].get("baseline_results",[])
                if r["end"]>=now_stamp-RETENTION and r.get("model_version")==model.get("version")}
    retained = {}
    for snapshot in sorted(available.values(),key=lambda s:s["start"]):
        start = snapshot["start"]
        if start<now_stamp-RETENTION:
            continue
        if snapshot["candidate"] is not None and (snapshot["candidate"]!=candidate or
                                                   start<candidate["issued_at"]):
            continue
        retained[start]=snapshot
        if not rows or snapshot["epoch"]!=rows[-1]["epoch"] or start<rows[0]["ts"]:
            continue
        targets = baseline if snapshot["candidate"] is None else accepted
        for result in compare_frozen(snapshot,rows,tz):
            result["model_version"] = snapshot["model"].get("version")
            targets.setdefault((result["start"],result["horizon"]),result)
    baseline_results = list(baseline.values())[-80:]
    baseline_summary = {str(h):_metrics([r["old_error"] for r in baseline_results
                                         if r["horizon"]==h and r["valid"]]) for h in (1,3,6)}
    # Bound CPU and decoded memory before fitting, with headroom for scores.
    windows = windows[-500:]
    steps = sum(len(w["steps"]) for w in windows)
    trim = 0
    while steps>4800:
        steps -= len(windows[trim]["steps"])
        trim += 1
    windows = windows[trim:]
    thermal = evaluate_windows(windows,model,ventilation) if mode=="candidate" else dict(status="observe")
    solar = (evaluate_solar([w for w in windows if w["equipment"]==identity],model,ventilation=ventilation)
             if mode=="candidate" else dict(status="observe",comfort_credit=False,
                                           model_certificate_validated=False))
    if thermal.get("coefficients") and candidate is None:
        candidate = dict(baseline=dict(model),coefficients=thermal["coefficients"],
                         equipment=thermal["equipment"],issued_at=now_stamp)
    comparisons=list(accepted.values())[-360:]
    observed_days = set(previous["report"].get("observation_days",())) if candidate==old_candidate else set()
    if candidate:
        observed_days.update(_local_day(at,tz) for r in rows if
            (at:=report_time(r)) is not None and at>=candidate["issued_at"] and not row_reasons(r,strict=True))
    prospective = (prospective_gate(comparisons,issued_at=candidate["issued_at"],now=now_stamp,
                                   observation_days=observed_days)
                   if candidate else dict(evidence_passed=False,promotion_allowed=False,blockers=["no_candidate"]))
    if candidate and thermal["status"]!="holdout_passed":
        prospective.update(evidence_passed=False,blockers=prospective["blockers"]+["thermal_evidence_degraded"])
    prospective["rejection_counts"] = dict(Counter(reason for row in comparisons
                                                   for reason in row.get("reasons",())))
    report = dict(mode=mode,policy=SCHEMA,updated_at=now_stamp,candidate=candidate,
                  candidate_age_seconds=now_stamp-candidate["issued_at"] if candidate else None,
                  last_promoted=None,baseline_results=baseline_results,baseline_frozen=baseline_summary,
                  observation_days=sorted(observed_days)[-60:],primary_observation="HA state change, cached republish excluded",
                  legacy=audit,recent=recent,retained_coverage=_coverage(windows),thermal=thermal,solar={**solar,"pending_gain":solar.get("coefficient")},
                  prospective=prospective,blockers=list(dict.fromkeys(thermal.get("blockers",[])+prospective["blockers"])))
    state=dict(windows=windows,forecasts=list(retained.values())[-8:],comparisons=comparisons,report=report)
    try:
        save_evidence(path,state)
    except ValueError:
        # Bounded by encoded size as well as the step/query counts.
        state["windows"]=windows[len(windows)//2:]
        save_evidence(path,state)
    return dict(candidate=candidate,warning=None)
