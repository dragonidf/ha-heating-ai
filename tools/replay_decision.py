"""Replay one private decision snapshot offline. No HA access or actuator I/O.

python tools/replay_decision.py /private/heating_data SNAPSHOT_SHA256
The snapshot must remain private: do not upload it or telemetry to GitHub.
"""
import argparse
import json
import sys
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'apps/heating_ai'))
from heating_recovery import DecisionJournal
from heating_stability import stable_plan


def replay_snapshot(snapshot):
    data=snapshot['inputs']
    now=datetime.fromisoformat(data['now']).astimezone(ZoneInfo(data['timezone']))
    result,reference=stable_plan(now,data['room'],data['floor'],data['outside'],data['fire'],
        data['model'],data['weather'],data['settings'],data['power_kw'],data.get('previous'))
    return dict(morning_reserve=result.get('morning_reserve'),heat=result['heat'],
                heat_segments=result['heat_segments'],energy=result['energy'],
                minimum=result['minimum'],maximum=result['maximum'],plan_stable=result['plan_stable'])


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('data_directory',type=Path)
    parser.add_argument('snapshot_id')
    args=parser.parse_args()
    snapshot=DecisionJournal(args.data_directory).read_snapshot(args.snapshot_id)
    print(json.dumps(replay_snapshot(snapshot),ensure_ascii=False,indent=2))


if __name__=='__main__':
    main()
