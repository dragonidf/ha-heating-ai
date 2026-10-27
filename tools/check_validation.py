"""Bounded offline measurements with fake HA I/O. Never contacts a real home."""
import argparse
import json
import sys
import tempfile
import time
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
sys.path[:0]=[str(ROOT/'apps/heating_ai'),str(ROOT/'tests')]
import test_sufficiency_search as fixture
import test_adapter_modulation as adapter
from heating_recovery import DecisionJournal
from heating_stability import stress_scenarios

def measured(function):
    start=time.perf_counter();value=function()
    return value,round((time.perf_counter()-start)*1000,3)

def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output',type=Path)
    args=parser.parse_args()
    case=fixture.SufficiencySearchTests();case.setUp()
    (plan,reference),full=measured(case.stable)
    x=case.x
    scenarios,stress=measured(lambda:stress_scenarios(case.now,x['room'],x['floor'],x['outside'],
        x['fire'],x['model'],[],case.settings,plan))
    plan['stress_scenarios']=scenarios
    # A known-deficient small remaining morning exercises all three bounded passes.
    case.now=case.now.replace(day=22,hour=6,minute=40,second=0)
    case.settings['outage_schedule']=dict(mode='normal',valid=True,outages=[])
    (expanded,checked),wide=measured(case.stable)
    (held,_),cached=measured(lambda:case.stable(checked))
    tick=adapter.AdapterModulationTests()
    stamp=case.now.timestamp()
    _,fast=measured(lambda:tick.fast_tick(case.now,[dict(start=stamp,end=stamp+300)]))
    with tempfile.TemporaryDirectory() as directory:
        journal=DecisionJournal(directory)
        before=time.perf_counter()
        for i in range(12):
            journal.append(dict(event='full_recompute',reserve=plan['morning_reserve'],
                policy=reference['policy'],learning_attempt=dict(status='pending',source='synthetic')),
                dict(inputs=dict(now=stamp+i*300,previous=reference),new_plan=plan),stamp+i*300)
        writes=round((time.perf_counter()-before)*1000/12,3)
        size=sum(p.stat().st_size for p in journal.directory.iterdir() if p.is_file())
    report=dict(scope='synthetic offline; fake HA I/O; single run, not a production latency guarantee',
        planning_ms=full,expanded_planning_ms=wide,cached_replay_ms=cached,fast_tick_with_fixture_ms=fast,
        shadow_stress_ms=stress,
        expanded_passes=len(expanded['search_passes']),cached_plan_retained=held['plan_stable'],
        journal_average_write_ms=writes,journal_one_hour_bytes=size,
        journal_14day_extrapolated_mib=round(size*24*14/1024/1024,2),
        journal_configured_budget_mib=256,protected_retention_days=7)
    text=json.dumps(report,ensure_ascii=False,indent=2)
    print(text)
    if args.output:
        args.output.parent.mkdir(parents=True,exist_ok=True)
        args.output.write_text(text+'\n')

if __name__=='__main__':main()
