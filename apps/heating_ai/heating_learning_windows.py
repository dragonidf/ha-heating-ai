"""Private evidence and bounded policy for the single scheduled learner."""
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
                holdout_start=min(w["day"] for w in holdout),
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
    guards = []
    for point in fine:
        if start<point["ts"]<=min(last,start+10860):
            at=datetime.fromtimestamp(point["ts"]-.001,now.tzinfo)
            _,low,high,_=comfort_at(at,settings)
            guards.append([point["ts"],point["temperature"],point.get("upper_temperature"),low,high])
    return dict(start=start, issue_at=issue, provider="heating_weather_forecast",
                weather=relevant, weather_known_until=known,
                room=room,floor=floor,outside=outside,fire=fire, settings=climate,
                model=dict(model),candidate=candidate,epoch=epoch,points=points,
                segments=segments,guards=guards,identity=[epoch,"Вітальня",model.get("version"),
                    settings.get("equipment_version"),(settings.get("modulation_profile") or {}).get("physical_mode")])


def _frozen_predictions(snapshot, tz, finish):
    start, segments = snapshot["start"], snapshot["segments"]
    weather, settings = snapshot["weather"], snapshot["settings"]
    horizon_targets = {v[0] for v in snapshot["points"].values() if v[0]<=finish}
    guard_targets = {v[0] for v in snapshot.get("guards",()) if v[0]<=finish}
    boundaries = sorted({start,finish,*horizon_targets,*guard_targets,
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
        if b in horizon_targets or b in guard_targets:
            predictions[b] = states[1][0]-states[0][0], peaks[1]-peaks[0]
    return predictions


def qualify_frozen(snapshot, rows, tz):
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
    return [row[:-5]+(sorted(row[-5]),)+row[-4:] for row in checks]


def compare_frozen(snapshot, rows, tz):
    return score_frozen(snapshot, qualify_frozen(snapshot,rows,tz), tz)


def score_frozen(snapshot, checks, tz):
    if not checks:
        return []
    start = snapshot["start"]
    predictions = (_frozen_predictions(snapshot,tz,max(check[1] for check in checks))
                   if snapshot["candidate"] else {})
    results=[]
    for h,due,old,upper,low,high,zone,actual,reasons,plan_kwh,actual_kwh,plan_min,actual_min in checks:
        delta,peak_delta = predictions.get(due,(0.,0.))
        guards = [g for g in snapshot.get("guards",()) if g[0]<=due]
        trajectory_ok = bool(guards and guards[-1][0]==due
            and all(0<b-a<=301 for a,b in zip([start]+[g[0] for g in guards], [g[0] for g in guards])))
        if trajectory_ok and predictions:
            trajectory_ok=all(high_risk is not None
                and max(0.,high_risk+max(predictions[t])-hi)<=max(0.,high_risk-hi)
                and max(0.,lo-(nom+predictions[t][0]))<=max(0.,lo-nom)
                for t,nom,high_risk,lo,hi in guards)
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
        outside=snapshot["outside"]
        stratum=":".join((zone,str(math.floor(outside/4)), "heat" if actual_min else "idle",
                         "after_stop" if "after_stop" in categories else "steady"))
        # Count measured violations only in qualified, executed plans. A model
        # can miss the violation itself, so its prediction is no extra veto.
        violation=actual>high+.1 or actual<low-.1
        results.append(dict(start=start,end=due,day=_local_day(start,tz),horizon=h,
            old_error=old-actual,shadow_error=shadow-actual,valid=not reasons,reasons=sorted(reasons),
            trajectory_guard_ok=trajectory_ok,stratum=stratum,violation=violation,
            guard_ok=(max(0.,upper+max(delta,peak_delta)-high)<=max(0.,upper-high) and
                      max(0.,low-shadow)<=max(0.,low-old)),
            categories=categories,planned_kwh=plan_kwh,actual_kwh=actual_kwh,
            energy_error=actual_kwh-plan_kwh,planned_minutes=plan_min,actual_minutes=actual_min,
            upper_risk=max(0.,upper+max(delta,peak_delta)-high),comfort_deficit=max(0.,low-shadow),
            peak_delta=peak_delta))
    return results




def independent_checks(rows, horizon):
    end=-math.inf
    for row in sorted(rows,key=lambda r:(r["end"],r["start"])):
        if (row["horizon"]==horizon and row.get("valid") is True and row["start"]>end
                and all(number(row.get(k)) is not None for k in ("old_error","shadow_error"))):
            yield row
            end=row["end"]


def promotion_evidence(rows):
    """Both physical horizons and all available guarded trajectories must pass."""
    metrics, strata = {}, {}
    for horizon,minimum,days in ((1,12,3),(3,6,2)):
        qualified=list(independent_checks(rows,horizon))
        if len(qualified)<minimum or len({r["day"] for r in qualified})<days:
            return dict(status="insufficient_data",reason=f"holdout_{horizon}h",metrics=metrics)
        old,new=(_metrics([r[key] for r in qualified]) for key in ("old_error","shadow_error"))
        metrics[str(horizon)]={"old":old,"new":new}
        if (not old["mae"] or new["mae"]>.98*old["mae"] or abs(new["bias"])>abs(old["bias"])
                or any(r.get("guard_ok") is not True or r.get("trajectory_guard_ok",True) is not True
                       for r in qualified)):
            return dict(status="rejected",reason=f"quality_{horizon}h",metrics=metrics)
        for group in {r["stratum"] for r in qualified}:
            members=[r for r in qualified if r["stratum"]==group]
            strata[f"{horizon}:{group}"]=dict(mae=_metrics([r["old_error"] for r in members])["mae"],
                                             violation=any(r["violation"] for r in members))
    return dict(status="accepted",metrics=metrics,strata=strata)


def promotion_allowed(active, fitted_base, proposed, evidence, state, now, *, legacy_day=None):
    date=now.date().isoformat()
    baseline=(legacy_day or {}).get("baseline",active) if (legacy_day or {}).get("date")==date else active
    return (active==fitted_base and active.get("validated") is True and bool(proposed)
            and evidence.get("status")=="accepted"
            and any(proposed[k]!=active.get(k) for k in proposed)
            and state.get("promotion_day")!=date
            and now.timestamp()>=state.get("frozen_until",0)
            and all(k in FIT_FIELDS and not isinstance(proposed[k],bool) and number(proposed[k]) is not None
                    and BOUNDS[k][0]<=proposed[k]<=BOUNDS[k][1]
                    and abs(proposed[k]-active[k])<=.01*abs(active[k])+1e-12
                    and abs(proposed[k]-baseline[k])<=.02*abs(baseline[k])+1e-12
                    and abs(proposed[k]-baseline[k])<=.05*abs(baseline[k])+1e-12
                    for k in proposed))


def rollback_reason(rows, strata):
    qualified={h:[(r,strata[f"{h}:{r['stratum']}"]) for r in independent_checks(rows,h)
                  if f"{h}:{r['stratum']}" in strata] for h in (1,3)}
    events=sorted((r for cohort in qualified.values() for r,b in cohort
                   if r["violation"] and not b["violation"]),key=lambda r:r["end"])
    end=-math.inf
    count=0
    for row in events:
        if row["start"]>end:
            count+=1
            end=row["end"]
            if count>=2:
                return "confirmed_comfort_violations"
    hour=qualified[1]
    if len(hour)>=12 and len({r["day"] for r,b in hour})>=3:
        expected=sum(b["mae"] for r,b in hour)/len(hour)
        observed=sum(abs(r["old_error"]) for r,b in hour)/len(hour)
        if observed>1.15*expected and observed-expected>.10:
            return "forecast_degradation"
    return None


def run_learning(path, rows, forecasts, model, ventilation, checkpoint, now, eligible):
    """One bounded executor task; return a proposal and never touch HA state."""
    with _JOB_LOCK:
        try:
            return _run_learning(path,rows,forecasts,model,ventilation,checkpoint,now,eligible)
        except Exception as exc:
            return dict(status="frozen",reason="learning_storage_or_data_error",fault=type(exc).__name__)


def _run_learning(path, rows, forecasts, model, ventilation, checkpoint, now, eligible):
    try:
        saved=load_evidence(path)
    except FileNotFoundError:
        saved=dict(windows=[],forecasts=[],comparisons=[],report={})
    stamp=now.timestamp()
    cutoff=stamp-RETENTION
    windows=[w for w in saved["windows"] if w["end"]>=cutoff]
    latest=windows[-1]["end"] if windows else cutoff
    fresh=[dict(r,report_verified=False) if (report_time(r) or math.inf)<=latest else r for r in rows]
    window_report=build_observation_windows(fresh,now.tzinfo)
    windows=(windows+window_report["windows"])[-500:]
    steps=sum(len(w["steps"]) for w in windows)
    trim=0
    while steps>4800:
        steps-=len(windows[trim]["steps"])
        trim+=1
    windows=windows[trim:]
    # Archive only original issues plus independently measured endpoints.
    # Parameters are simulated exclusively inside this bounded scheduled job.
    available={item["snapshot"]["start"]:item for item in saved["forecasts"]
               if item["snapshot"]["start"]>=cutoff}
    for snapshot in forecasts:
        available.setdefault(snapshot["start"],dict(snapshot=snapshot,checks=[]))
    archive=[]
    for stamp_key,item in sorted(available.items())[-192:]:
        snapshot=item["snapshot"]
        checks={r[0]:r for r in item["checks"]}
        if rows and snapshot["epoch"]==rows[-1]["epoch"] and rows[0]["ts"]<=stamp_key:
            for row in qualify_frozen(snapshot,rows,now.tzinfo):
                checks.setdefault(row[0],row)
        if checks or (rows and snapshot["epoch"]==rows[-1]["epoch"]
                      and stamp-max(p[0] for p in snapshot["points"].values())<600):
            archive.append(dict(snapshot=snapshot,checks=list(checks.values())))
    coverage=_coverage(windows)
    cooldown=stamp<checkpoint.get("frozen_until",0)
    result=dict(status="frozen" if not eligible or cooldown else "insufficient_data",
                reason="rollback_cooldown" if cooldown else "qualified_inputs_required",independent_windows=len(windows),
                qualified_minutes=round(coverage["seconds"]/60,1))
    monitoring=checkpoint.get("monitoring")
    if monitoring and monitoring["version"]==model.get("version"):
        assessments={(r["start"],r["horizon"]):r for r in monitoring.get("checks",())}
        assessed=[r for item in archive
                  if item["snapshot"]["model"]==model and item["snapshot"]["start"]>=monitoring["at"]
                  for r in score_frozen(item["snapshot"],item["checks"],now.tzinfo)]
        assessments.update({(r["start"],r["horizon"]):r for r in assessed if r["valid"]})
        assessed=list(assessments.values())
        reason=rollback_reason(assessed,monitoring["strata"]) if eligible else None
        if reason:
            result.update(status="rolled_back",reason=reason,rollback=True)
        elif eligible:
            result.update(status="collecting",reason="post_promotion_monitoring")
            # Allow further updates only after accumulating comparable checks.
            for horizon,min_count,days in ((1,12,3),(3,6,2)):
                same=[r for r in independent_checks(assessed,horizon) if f"{horizon}:{r['stratum']}" in monitoring["strata"]]
                if len(same)<min_count or len({r["day"] for r in same})<days:
                    eligible=False
    if (eligible and not result.get("rollback") and model.get("validated") is True
            and stamp>=checkpoint.get("frozen_until",0)
            and checkpoint.get("promotion_day")!=now.date().isoformat()):
        thermal=evaluate_windows(windows,model,ventilation)
        result.update(status=thermal["status"] if thermal["status"]=="insufficient_data" else "rejected",
                      reason=",".join(thermal.get("blockers",[])) or "holdout_forecasts_required")
        if thermal.get("coefficients"):
            scored=[r for item in archive if item["snapshot"]["model"]==model
                    and item["snapshot"]["start"]>=checkpoint.get("evidence_after",0)
                    and _local_day(item["snapshot"]["start"],now.tzinfo)>=thermal["holdout_start"]
                    and item["snapshot"]["identity"][2:]==thermal["equipment"]
                    for r in score_frozen(dict(item["snapshot"],candidate={"coefficients":thermal["coefficients"]}),
                                          [r for r in item["checks"] if r[0] in (1,3)],now.tzinfo)]
            gate=promotion_evidence(scored)
            result.update(status=gate["status"],reason=gate.get("reason","independent_holdout_passed"),
                          metrics=gate.get("metrics",{}))
            if gate["status"]=="accepted":
                result.update(proposed=thermal["coefficients"],evidence=gate,
                              evidence_fingerprint=sha256(_encode(scored)).hexdigest())
        result["solar"]=evaluate_solar([w for w in windows if w["equipment"][0]==model.get("version")],
                                      model,ventilation=ventilation)
    payload=dict(windows=windows,forecasts=archive,comparisons=[],report={
        "status":result["status"], "reason":result["reason"], "updated_at":stamp,
        "independent_windows":len(windows), "qualified_minutes":result["qualified_minutes"]})
    # Keep more than a week of compact issue snapshots where the byte cap permits.
    while len(_encode(payload))>MAX_BYTES-256:
        if payload["windows"] and (not payload["forecasts"] or len(payload["windows"])>70):
            del payload["windows"][:min(20,max(1,len(payload["windows"])-70))]
        elif payload["forecasts"]:
            del payload["forecasts"][:8]
        else:
            raise ValueError("learning evidence limit")
    save_evidence(path,payload)
    return result
