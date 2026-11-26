"""Disposable large-directory benchmark of the actual online cold-pack path.

Measure archive thread CPU, process RSS and concurrent journal reader latency.
Synthetic fixture only: not a full HA restart or total controller CPU test.
"""
import argparse
import hashlib
import json
import math
from pathlib import Path
from random import Random
import resource
import sys
from tempfile import TemporaryDirectory
from threading import Event, Thread
from time import perf_counter, thread_time, time

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/"apps/heating_ai"))
from heating_recovery import DecisionJournal


def summary(values):
    ordered=sorted(values)
    return dict(count=len(ordered),p95_ms=1000*ordered[math.ceil(.95*len(ordered))-1],
                max_ms=1000*ordered[-1]) if ordered else dict(count=0)


def run(entries):
    with TemporaryDirectory() as folder:
        journal=DecisionJournal(folder,max_bytes=128*1024*1024)
        stamp=time()
        rng=Random(42)
        common=[rng.random() for _ in range(6000)]
        cold=[dict(i=i,values=common) for i in range(8)]
        ids=[journal.snapshot(value,stamp-4*86400+i) for i,value in enumerate(cold)]
        hot=dict(i=100,values=common)
        hot_id=journal.snapshot(hot,stamp)
        # Names exercise the real candidate-selection path, but empty,
        # undersized placeholders are never candidates or read as evidence.
        for i in range(entries):
            digest=hashlib.sha256(str(i).encode()).hexdigest()
            (journal.directory/(digest+".json.gz")).touch()
        journal.used_bytes=None
        scan_cpu=thread_time();scan_wall=perf_counter()
        journal._prepare(stamp)
        hourly_reconciliation=dict(cpu_s=thread_time()-scan_cpu,wall_s=perf_counter()-scan_wall)
        before=journal.used_bytes
        rss_before=resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
        baseline=[]
        for _ in range(100):
            start=perf_counter()
            assert journal.read_snapshot(hot_id)==hot
            baseline.append(perf_counter()-start)
        stop=Event(); done=Event(); started=Event()
        result={}
        def worker():
            cpu=thread_time(); wall=perf_counter()
            started.set()
            try:
                result.update(journal.compact_cold_snapshot_batch(stamp,allow_remove=True,stop=stop))
            except Exception as exc:
                result.update(status="error",reason=str(exc))
            finally:
                result.update(cpu_s=thread_time()-cpu,wall_s=perf_counter()-wall)
                done.set()
        thread=Thread(target=worker)
        thread.start()
        started.wait(2)
        concurrent=[]
        try:
            while not done.wait(.01):
                start=perf_counter()
                assert journal.read_snapshot(hot_id)==hot
                concurrent.append(perf_counter()-start)
        finally:
            stop.set()
            thread.join()
        if result.get("status")!="promoted":
            raise AssertionError(result)
        assert result["removed"]==8
        assert journal.used_bytes==before-result["removed_bytes"]+result["packed_bytes"]
        for key,value in zip(ids,cold):
            assert journal.read_snapshot(key)==value
        restarted=DecisionJournal(folder)
        for key,value in zip(ids,cold):
            assert restarted.read_snapshot(key)==value
        rss_after=resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
        # Second pass measures exhausted shortlist, no directory scan.
        cpu=thread_time(); wall=perf_counter()
        empty=journal.compact_cold_snapshot_batch(stamp,allow_remove=True,stop=Event())
        empty.update(cpu_s=thread_time()-cpu,wall_s=perf_counter()-wall)
        assert empty["status"]=="no_eligible_batch"
        return dict(entries=entries+9,baseline_read=summary(baseline),
            concurrent_read=summary(concurrent),maintenance=result,empty_scan=empty,
            hourly_reconciliation_including_existing_work=hourly_reconciliation,
            daily_cpu_upper_estimate_s=96*max(result["cpu_s"],empty["cpu_s"]),
            rss_before_kib=rss_before,rss_after_kib=rss_after,
            exact_restart_reads=True,scope="synthetic archive + concurrent journal read; excludes HA tick and real disk")

if __name__=="__main__":
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--entries",type=int,default=100000)
    parser.add_argument("--output",type=Path)
    args=parser.parse_args()
    if not 0<=args.entries<=100000:
        parser.error("entries must be 0..100000")
    result=run(args.entries)
    raw=json.dumps(result,indent=2)
    print(raw)
    if args.output:
        args.output.write_text(raw+"\n")
