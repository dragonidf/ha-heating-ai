"""Replay a synthetic event history or print aggregate audit of local V1 telemetry."""
import argparse
from datetime import datetime, timedelta
import json
import math
from pathlib import Path
import sys
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT/"apps"/"heating_ai"))

from heating_learning_windows import (audit_transitions,build_observation_windows,
    evaluate_solar,evaluate_windows,freeze_forecast,compare_frozen,prospective_gate,
    promotion_evidence,qualify_frozen)
from heating_model import DEFAULT_MODEL, step

TZ = ZoneInfo("Europe/Kyiv")
MODEL = dict(DEFAULT_MODEL, version=6, validated=True, floor_air=.0284, room_loss=.008192,
             floor_decay=.248832, heat_gain=1.0368)
TRUE_MODEL = dict(MODEL, floor_air=MODEL["floor_air"]*1.01, room_loss=MODEL["room_loss"]*.99)


def fixture(days=16):
    initial = datetime(2026,11,1,tzinfo=TZ)
    rows = []
    # Separate physical report pairs. 5-minute rows only contain sampled
    # inputs and (except at the endpoints) a cached room report.
    for day in range(days):
        date = initial+timedelta(days=day)
        for hour,length,kw,sun in ((0,60,0.,0.),(3,120,2.4,0.),(9,60,0.,.45),(13,60,0.,.6)):
            initial_room = room = 21.2
            return_at = 19. if kw else 23.5
            floor = return_at
            outside = (day%6)-.5 if not sun else 8.+day%4
            start = date.replace(hour=hour).timestamp()
            for i in range(length//5+1):
                at = start+i*300
                endpoint = i in (0,length//5)
                observed = room if endpoint else initial_room
                rows.append(dict(ts=at,room=observed,room_valid=True,source="Вітальня",
                    quality=endpoint,environment_version=6,astronomy_valid=True,
                    room_reported_at=at if endpoint else start,report_verified=endpoint,
                    grid="on", voltage_qualified=True, power=kw, interval_kw=kw,
                    power_source="kws_phases",power_quality="measured",
                    execution="automatic" if kw else "idle", manual_extra_active=False,
                    fire=0., fire_valid=True, outside=outside, shadow_ventilation=[0.,0.],
                    weather_issue_at=start-60,solar_factor=sun,daylight_weight=sun,
                    epoch="synthetic",thermal_model_version=6,equipment_version=1,
                    boiler_power_mode="fixed_6", **{"return":floor}))
                room,floor=step(room,floor,outside,kw,0.,TRUE_MODEL,1/12)
                room+=.2*sun/12
        # Two short, independent legacy reports in each day. These are
        # explicitly physical measurements and too short for the new windows.
        a=dict(rows[-1], ts=date.replace(hour=19).timestamp(),room=21.,solar_factor=0.,daylight_weight=0.)
        b=dict(a, ts=a["ts"]+300,room=20.99)
        for row in (a,b):
            row.update(room_reported_at=row["ts"],weather_issue_at=row["ts"]-120)
        rows.extend((a,b))
    return rows


def frozen_fixture(candidate, days=16, *, capture=False, perturb=True):
    """Issue-time weather and exact-H endpoints in later, untouched days."""
    initial = datetime(2026,11,18,tzinfo=TZ)
    results=[]
    for day in range(days):
        date = initial+timedelta(days=day)
        now = date.replace(hour=0 if day%2 else 9)
        start = now.timestamp()
        room0=21.3
        floor0=23.
        outside=-3. if day%3==0 else 5.
        heat = day%2==0
        weather=[dict(ts=start+i*3600,temperature=outside,condition="cloudy") for i in range(7)]
        segments=[dict(start=start,end=start+1800,power_kw=2.4)] if heat else []
        settings=dict(target=22.,lower=20.,upper=23.5,night_target=21.,
            night_lower=19.,night_upper=23.,sleep_start="23:00",sleep_end="07:00",
            equipment_version=1,modulation_profile={"physical_mode":"fixed_6"})
        rows, curve = [], []
        room=actual=room0
        floor=true_floor=floor0
        for i in range(73):
            at = start+300*i
            kw = 2.4 if heat and i<6 else 0.
            endpoint=i in (0,12,36,72)
            observed = actual + (.004*math.sin(day*.7+i) if i and perturb else 0.)
            rows.append(dict(ts=at,room=observed,room_valid=True,source="Вітальня",
                room_reported_at=at if endpoint else None,report_verified=endpoint,
                grid="on",voltage_qualified=True,power=kw,interval_kw=2.4 if heat and i<=6 else 0.,
                power_source="kws_phases",power_quality="measured",
                execution="automatic" if kw else "idle",manual_extra_active=False,
                fire=0.,fire_valid=True,outside=outside,shadow_ventilation=[0.,0.],
                weather_issue_at=max(start-60,at-1800),solar_factor=0.,
                epoch="synthetic",thermal_model_version=6,equipment_version=1,
                boiler_power_mode="fixed_6",**{"return":true_floor}))
            if i:
                curve.append(dict(ts=at,temperature=room,upper_temperature=room))
            room,floor=step(room,floor,outside,kw,0.,MODEL,1/12)
            actual,true_floor=step(actual,true_floor,outside,kw,0.,TRUE_MODEL,1/12)
        snapshot=freeze_forecast(now,room0,floor0,outside,0.,weather,start-60,settings,
                                  dict(curve=curve,heat_segments=segments),MODEL,candidate,"synthetic")
        if capture:
            results.append(dict(snapshot=snapshot,checks=qualify_frozen(snapshot,rows,TZ)))
        else:
            results.extend(compare_frozen(snapshot,rows,TZ))
    return results


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--telemetry",type=Path,help="Owner-approved local JSONL. Output aggregates only.")
    opts=parser.parse_args()
    if opts.telemetry:
        with opts.telemetry.open() as source:
            rows=[json.loads(line) for line in source if line.strip()]
    else:
        rows=fixture()
    old=audit_transitions(rows,include_pairs=False)
    old.pop("pairs")
    new=build_observation_windows(rows,TZ)
    windows=new.pop("windows")
    output=dict(source="local_telemetry" if opts.telemetry else "synthetic_only",old=old,new=new)
    if opts.telemetry:
        output.update(frozen="not_measured_without_paired_issue_time_snapshots",
                      thermal="not_measured_without_matching_model_snapshot")
    else:
        output["thermal"]=thermal=evaluate_windows(windows,MODEL)
        output["solar"]=evaluate_solar(windows,MODEL)
        if thermal.get("coefficients"):
            issued=datetime(2026,11,18,tzinfo=TZ).timestamp()
            candidate=dict(coefficients=thermal["coefficients"])
            comparisons=frozen_fixture(candidate)
            output["synthetic_frozen"]=prospective_gate(comparisons,issued_at=issued,now=issued+17*86400)
            output["unified_promotion_gate"]=promotion_evidence(comparisons)
    print(json.dumps(output,ensure_ascii=False,indent=2,allow_nan=False))


if __name__=="__main__":
    main()
