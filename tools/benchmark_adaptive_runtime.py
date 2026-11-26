"""Measure isolated packaged tick, observation capture and bounded daily job.

The tick uses the existing HA-free background-control fixture. Samples, energy
and commands in this benchmark are synthetic. Each mode gets its own process.
"""
import argparse
import asyncio
from datetime import datetime
import json
import os
from pathlib import Path
import resource
import random
import statistics
from itertools import permutations
import subprocess
import sys
from tempfile import TemporaryDirectory
from time import perf_counter, process_time
from unittest.mock import patch
from zipfile import ZipFile

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0]=[str(ROOT),str(ROOT/"tests"),str(ROOT/"apps"/"heating_ai")]

from benchmark_planner import summary, paired_statistics
from replay_adaptive_learning import MODEL, TZ, fixture, frozen_fixture
from test_background_control import BackgroundTickSafetyTests
from test_native_v1_production import FakeHA, package


def rss_kib():
    return int(Path("/proc/self/statm").read_text().split()[1])*os.sysconf("SC_PAGE_SIZE")//1024


def measure_core(app, tick, mode, root, *, full_ring=False):
    initial_rss=rss_kib()
    history=fixture(58)
    # Legacy sample ring is 4032 rows. The sidecar job processes its 400-row
    # live ring and up to 60 prior days in the bounded private file.
    recent=tuple(history[-400:])
    sample=history[0]
    now=datetime.fromtimestamp(history[-1]["ts"]+300,TZ)
    timings={"tick":[],"aggregation":[],"training":[]}
    output=dict(mode=mode,resident_start=initial_rss)
    issue=sample["ts"]
    app.last_sample=issue
    app.samples=[]
    original_raw=app.raw
    app.raw=lambda key,attribute=None: ({"state":sample["room"],"last_changed":
        datetime.fromtimestamp(issue,TZ).isoformat()} if key=="room" else
        "off" if key=="fan" else original_raw(key,attribute))
    app.climate_tz=TZ
    app.memory.update(weather_at=issue-300,owned=True,last_command="off")
    app.voltage_status={"voltage_qualified":True}
    app.equipment_status={"ready":True}
    app.planning_grid_state="on"
    app.power_source="kws_phases"
    app.power_quality="measured"
    app._writer=True
    output["resident_before_configuration"]=rss_kib()
    app.ventilation_states={k:dict(relay="off",supply="on",grid="on") for k in ("recuperator","bath")}
    output["resident_after_configuration"]=rss_kib()
    action_output=[]
    app.apply_action=lambda action,once:action_output.append(action)
    app.next_compute=float("inf")
    stamp=issue
    app.now=lambda: datetime.fromtimestamp(stamp,TZ)
    app.grid_state=lambda:"on"
    app.update_heating_power=lambda:0.
    active=mode=="online"
    sidecar=root/"online_learning_v1.json"
    if active:
        app._native_solar_fit=False
        adaptive=sys.modules[type(app).__module__].learning
        windows=adaptive.build_observation_windows(history,TZ)["windows"][-200:]
        # Seed enough persisted windows to exercise the bounded fitter and
        # the path that decodes the sidecar at a learning time.
        state=dict(windows=windows,forecasts=frozen_fixture(None,capture=True),comparisons=[],report={})
        adaptive.save_evidence(sidecar,state)
        del windows,state
        # Retain the recent sample ring, exercise fitting and recording at cap.
        app._learning_rows.extend(recent)
    app.samples=[]
    if full_ring:
        # Same measured-OFF duty semantics on both revisions; the workload
        # changes ring size only, not the command or thermal safety policy.
        for i in range(4032):
            at=issue-(4032-i)*300
            row=dict(history[i%len(history)],ts=at,room_reported_at=at,
                     ventilation={k:dict(seconds=300,unknown_fraction=0.,supply_unknown_fraction=0.,
                         powered_relay_on_fraction=0.,on_fraction=0.,supply_on_fraction=1.,
                         effective_on_fraction=0.,effective_unknown_fraction=0.,
                         grid_on_fraction=1.,grid_unknown_fraction=0.)
                         for k in ("recuperator","bath")})
            app.samples.append(row)
    output["sample_ring_size"]=len(app.samples)
    output["resident_after_ring"]=rss_kib()
    time_module=sys.modules[tick.__module__].time
    # All 10 warm groups include 19 ordinary ticks and one aggregation slot.
    # Each tick uses the packaged core; disk/controller writes are fixture mocks.
    for batch in range(11):
        batch_times=[]
        for i in range(20):
            stamp=issue+(batch*20+i+1)*15
            due=i==19
            if due:
                sample=dict(history[i],ts=stamp,room_reported_at=stamp,
                            ventilation={k:dict(seconds=300,unknown_fraction=0.,supply_unknown_fraction=0.,
                                powered_relay_on_fraction=0.,on_fraction=0.,supply_on_fraction=1.)
                                for k in ("recuperator","bath")})
                app.last_sample=stamp-300
            def fake_sample(*args,**kwargs):
                if due:
                    app.last_sample=stamp
                    if full_ring:
                        app.samples=(app.samples+[sample])[-4032:]
                    else:
                        app.samples[:]=[sample]
            with patch.object(time_module,"time",return_value=stamp), patch.object(
                    type(app).__mro__[1],"sample",side_effect=fake_sample):
                wall,cpu=perf_counter(),process_time()
                tick(app)
                elapsed=(process_time()-cpu,perf_counter()-wall)
            batch_times.append(elapsed)
            # Isolate the added aggregation from the full legacy control slot.
            if due and active:
                wall,cpu=perf_counter(),process_time()
                app._learning_append(sample,True,(),0.)
                elapsed_aggregation=(process_time()-cpu,perf_counter()-wall)
                if batch:
                    timings["aggregation"].append(elapsed_aggregation)
        if batch:
            timings["tick"].extend(batch_times)
    output["resident_after_tick"]=rss_kib()
    for i in range(11) if active else ():
        wall,cpu=perf_counter(),process_time()
        result=adaptive.run_learning(sidecar,recent,(),MODEL,{},{},now,True)
        elapsed=(process_time()-cpu,perf_counter()-wall)
        if result["status"]=="frozen":
            raise RuntimeError(result["reason"])
        if i:
            timings["training"].append(elapsed)
    if active:
        output["storage_bytes"]=sidecar.stat().st_size
    output["peak_rss"]=resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
    output["resident_after_training"]=rss_kib()
    output["cpu_wall"]={key:[summary([row[i] for row in value]) for i in (0,1)]
                        for key,value in timings.items() if value}
    # Compare the complete action sequence, not wall timings/IDs in diagnostics.
    output["command_golden"]=action_output
    output["raw_timings"]=timings
    # The native tick includes capture on its sample ticks. Do not add the
    # separately timed diagnostic aggregation a second time.
    output["daily_tick_training_cpu_s"]=(
        statistics.mean(row[0] for row in timings["tick"])*5760+
        (2*statistics.mean(row[0] for row in timings["training"]) if timings["training"] else 0.))
    return output


async def child(package_path,mode,root,*,full_ring=False):
    with package(package_path) as (component,_):
        fixture_app,_=BackgroundTickSafetyTests().app()
        app=component.NativeController(FakeHA(),{})
        app.__dict__.update(fixture_app.__dict__)
        app.__dict__.pop("sample",None)
        return measure_core(app,component.HeatingClimate.tick_inner,mode,root,full_ring=full_ring)


def controlled_runtime(root,baseline,repeat,*,full_ring=False):
    if len(os.sched_getaffinity(0))!=1 or os.getenv("PYTHONHASHSEED")!="0":
        raise ValueError("pin one CPU and set PYTHONHASHSEED=0")
    if repeat<30 or repeat%6:
        raise ValueError("repeat must be >=30 and divisible by six")
    names=("baseline","control","candidate")
    orders=list(permutations(names));rng=random.Random(518)
    blocks=[]
    for _ in range(repeat//6):
        rng.shuffle(orders);blocks.extend(orders)
    rows=[];golden=None
    for index,order in enumerate([names,names[::-1],names,*blocks]):
        block={}
        for name in order:
            result=json.loads(subprocess.check_output(
                [sys.executable,__file__,str(baseline),"--child",str(root/name),"online"]+
                (["--full-ring"] if full_ring else []),text=True))
            (root/"online_learning_v1.json").unlink()
            actions=result.pop("command_golden")
            if golden is None:golden=actions
            if golden!=actions:raise AssertionError("Production command sequence changed")
            block[name]=result
        if index>=3:rows.append(block)
        print(f"completed triplet {max(0,index-2)}/{repeat}",file=sys.stderr,flush=True)
    operations={}
    for label in ("tick","aggregation","training","daily_tick_training_cpu_s","peak_rss"):
        def value(row,name):
            item=row[name]
            return item[label] if label in ("daily_tick_training_cpu_s","peak_rss") else item["cpu_wall"][label][0][1]
        aa=paired_statistics([(value(row,"baseline"),value(row,"control")) for row in rows])
        ab=paired_statistics([(value(row,"baseline"),value(row,"candidate")) for row in rows])
        calibrated=aa["lower_95"]>=-.05 and aa["upper_95"]<=.05 and abs(aa["p95_change"])<=.05
        gate=("inconclusive_noise" if not calibrated else
              "pass" if ab["p95_change"]<=.05 and ab["upper_95"]<=.05 else
              "regression" if ab["lower_95"]>.05 else "inconclusive_effect")
        operations[label]=dict(a_a=aa,a_b=ab,gate=gate,
            medians={name:statistics.median(value(row,name) for row in rows) for name in names})
    return dict(scope="synthetic HA-free tick and training; excludes planner, real I/O and archive; block p95 distribution",
        exact_command_match=True,affinity=sorted(os.sched_getaffinity(0)),repeat=repeat,full_ring=full_ring,
        operations=operations,orders=blocks,samples=rows,
        gate_pass=all(v["gate"]=="pass" for v in operations.values()))


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument("baseline",type=Path)
    parser.add_argument("--child",nargs=2)
    parser.add_argument("--both-online",action="store_true",help="Paired A/A and A/B of active learning against a release")
    parser.add_argument("--controlled",type=Path,help="write randomized paired 30+ triplets, including raw measurements")
    parser.add_argument("--repeat",type=int,default=30)
    parser.add_argument("--full-ring",action="store_true",help="keep the full 4032-row historical sample buffer")
    opts=parser.parse_args()
    if opts.controlled:opts.both_online=True
    if opts.child:
        path,mode=opts.child
        print(json.dumps(asyncio.run(child(Path(path),mode,Path(path).parent,full_ring=opts.full_ring))))
        return
    with TemporaryDirectory() as folder:
        root=Path(folder)
        baseline=opts.baseline.resolve()
        sources=(("baseline",baseline),("control",baseline),("candidate",ROOT)) if opts.both_online else (("baseline",baseline),("candidate",ROOT))
        for name,source in sources:
            target=root/name
            archive=root/(name+".zip")
            subprocess.run([sys.executable,str(source/"tools/build_marketplace.py"),
                            "--output",str(archive)],cwd=source,check=True,stdout=subprocess.DEVNULL)
            with ZipFile(archive) as zf:
                zf.extractall(target)
        if opts.controlled:
            report=controlled_runtime(root,baseline,opts.repeat,full_ring=opts.full_ring)
            opts.controlled.write_text(json.dumps(report,indent=2)+"\n")
            print(json.dumps({k:v for k,v in report.items() if k not in ("samples","orders")},indent=2))
            raise SystemExit(0 if report["gate_pass"] else 2)
        results={}
        variants=(("baseline","online"),("control","online"),("candidate","online")) if opts.both_online else (("baseline","legacy"),("candidate","online"))
        for name,mode in variants:
            result=json.loads(subprocess.check_output(
                [sys.executable,__file__,str(baseline),"--child",str(root/name),mode]+
                (["--full-ring"] if opts.full_ring else []),text=True))
            if mode=="online":
                (root/"online_learning_v1.json").unlink()
            actions=result.pop("command_golden")
            if not results:
                golden=actions
            elif actions!=golden:
                raise AssertionError("Production command sequence changed")
            results[name+":"+mode]=result
        if opts.both_online:
            baseline=results["baseline:online"];control=results["control:online"];candidate=results["candidate:online"]
            comparison={}
            for operation in ("tick","aggregation","training"):
                base=baseline["cpu_wall"][operation][0][1]
                reference=control["cpu_wall"][operation][0][1]
                changed=candidate["cpu_wall"][operation][0][1]
                comparison[operation]=dict(cpu_p95_baseline=base,cpu_p95_control=reference,
                    cpu_p95_candidate=changed,aa_percent=round((reference/base-1)*100,2),
                    ab_percent=round((changed/base-1)*100,2))
            comparison["rss_kib"]=dict(baseline=baseline["peak_rss"],control=control["peak_rss"],
                                        candidate=candidate["peak_rss"])
            print(json.dumps(dict(comparison=comparison,exact_command_match=True,full_ring=opts.full_ring),indent=2))
        else:
            print(json.dumps(results,indent=2))


if __name__=="__main__":
    main()
