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
import subprocess
import sys
from tempfile import TemporaryDirectory
from time import perf_counter, process_time
from unittest.mock import patch
from zipfile import ZipFile

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0]=[str(ROOT),str(ROOT/"tests"),str(ROOT/"apps"/"heating_ai")]

from benchmark_planner import summary
from replay_adaptive_learning import MODEL, TZ, fixture
from test_background_control import BackgroundTickSafetyTests
from test_native_v1_production import FakeHA, package


def rss_kib():
    return int(Path("/proc/self/statm").read_text().split()[1])*os.sysconf("SC_PAGE_SIZE")//1024


def measure_core(app, tick, mode, root):
    initial_rss=rss_kib()
    history=fixture(58)
    # Legacy sample ring is 4032 rows. The sidecar job processes its 400-row
    # live ring and up to 60 prior days in the bounded private file.
    recent=tuple(history[-400:])
    legacy=tuple(history[-4032:])
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
    app.cfg["adaptive_learning_v2_mode"]=mode
    output["resident_before_configuration"]=rss_kib()
    app.ventilation_states={k:dict(relay="off",supply="on",grid="on") for k in ("recuperator","bath")}
    if hasattr(app,"configure_adaptive_observer"):
        app.configure_adaptive_observer()
    output["resident_after_configuration"]=rss_kib()
    action_output=[]
    app.apply_action=lambda action,once:action_output.append(action)
    app.next_compute=float("inf")
    stamp=issue
    app.now=lambda: datetime.fromtimestamp(stamp,TZ)
    app.grid_state=lambda:"on"
    app.update_heating_power=lambda:0.
    active=mode in ("observe","candidate")
    sidecar=root/"learning_windows_v1.json"
    if active:
        adaptive=app._adaptive
        windows=adaptive.build_observation_windows(history,TZ)["windows"][-200:]
        # Seed enough persisted windows to exercise the bounded fitter and
        # the path that decodes the sidecar at a learning time.
        state=dict(windows=windows,forecasts=[],comparisons=[],report={})
        adaptive.save_evidence(sidecar,state)
        del windows,state
        # Retain the recent sample ring, exercise fitting and recording at cap.
        app._adaptive_rows.extend(recent)
    output["resident_after_ring"]=rss_kib()
    app.samples=[]
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
                app._adaptive_append(sample,True,(),0.)
                elapsed_aggregation=(process_time()-cpu,perf_counter()-wall)
                if batch:
                    timings["aggregation"].append(elapsed_aggregation)
        if batch:
            timings["tick"].extend(batch_times)
    output["resident_after_tick"]=rss_kib()
    for i in range(11) if active else ():
        wall,cpu=perf_counter(),process_time()
        result=adaptive.run_shadow(sidecar,recent,(),legacy,MODEL,{},mode,now)
        elapsed=(process_time()-cpu,perf_counter()-wall)
        if result["warning"]:
            raise RuntimeError(result["warning"])
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
    return output


async def child(package_path,mode,root):
    with package(package_path) as (component,_):
        fixture_app,_=BackgroundTickSafetyTests().app()
        app=component.NativeController(FakeHA(),{})
        app.__dict__.update(fixture_app.__dict__)
        app.__dict__.pop("sample",None)
        return measure_core(app,component.HeatingClimate.tick_inner,mode,root)


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument("baseline",type=Path)
    parser.add_argument("--child",nargs=2)
    opts=parser.parse_args()
    if opts.child:
        path,mode=opts.child
        print(json.dumps(asyncio.run(child(Path(path),mode,Path(path).parent))))
        return
    with TemporaryDirectory() as folder:
        root=Path(folder)
        baseline=opts.baseline.resolve()
        for name,source in (("baseline",baseline),("candidate",ROOT)):
            target=root/name
            archive=root/(name+".zip")
            subprocess.run([sys.executable,str(source/"tools/build_marketplace.py"),
                            "--output",str(archive)],cwd=source,check=True,stdout=subprocess.DEVNULL)
            with ZipFile(archive) as zf:
                zf.extractall(target)
        results={}
        for name,mode in (("baseline","disabled"),("candidate","disabled"),
                          ("candidate","observe"),("candidate","candidate")):
            result=json.loads(subprocess.check_output(
                [sys.executable,__file__,str(baseline),"--child",str(root/name),mode],text=True))
            if mode in ("observe","candidate"):
                (root/"learning_windows_v1.json").unlink()
            actions=result.pop("command_golden")
            if not results:
                golden=actions
            elif actions!=golden:
                raise AssertionError("Production command sequence changed")
            results[name+":"+mode]=result
        print(json.dumps(results,indent=2))


if __name__=="__main__":
    main()
