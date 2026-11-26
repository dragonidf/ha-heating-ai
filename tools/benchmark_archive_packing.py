"""Offline lossless archive packing experiment. Never edits an HA archive.

Compare canonical per-snapshot gzip with bounded solid XZ blocks on actual
synthetic planner outputs. XZ is a candidate, NOT an installed archive format.
"""
import argparse
import gzip
import hashlib
import json
import lzma
from pathlib import Path
import resource
import sys
from time import process_time, perf_counter
from datetime import timedelta

ROOT=Path(__file__).resolve().parents[1]
sys.path[:0]=[str(ROOT/"apps/heating_ai"),str(ROOT/"tests"),str(ROOT/"tools")]

MAX_RAW=8*1024*1024
MAX_ROWS=8
MAX_PACKED=MAX_RAW+1024*1024
MEM_LIMIT=16*1024*1024


def canonical(value):
    return json.dumps(value,ensure_ascii=False,sort_keys=True,separators=(",",":"),
                      allow_nan=False).encode("utf-8")


def digest(raw):
    return hashlib.sha256(raw).hexdigest()


def pack(rows):
    """Independent bounded block, no dependencies on other blocks."""
    rows=tuple(rows)
    if not 1<=len(rows)<=MAX_ROWS or any(not isinstance(r,bytes) or b"\n" in r or not r for r in rows):
        raise ValueError("invalid canonical snapshot block")
    if sum(len(r)+1 for r in rows)>MAX_RAW:
        raise ValueError("snapshot block too large")
    return lzma.compress(b"\n".join(rows)+b"\n",format=lzma.FORMAT_XZ,preset=1)


def unpack(blob,ids):
    """Verify every original content address, and reject truncated/extra data."""
    if not 1<=len(ids)<=MAX_ROWS or len(blob)>MAX_PACKED:
        raise ValueError("invalid snapshot block bounds")
    decoder=lzma.LZMADecompressor(format=lzma.FORMAT_XZ,memlimit=MEM_LIMIT)
    raw=decoder.decompress(blob,max_length=MAX_RAW+1)
    if len(raw)>MAX_RAW or not decoder.eof or decoder.unused_data or not raw.endswith(b"\n"):
        raise ValueError("invalid or oversized snapshot block")
    rows=raw[:-1].split(b"\n")
    if len(rows)!=len(ids) or any(digest(row)!=key for row,key in zip(rows,ids)):
        raise ValueError("snapshot checksum mismatch")
    return rows


def compare(rows):
    # Existing archive already deduplicates exact snapshot hashes.
    unique={digest(row):row for row in rows}
    rows=list(unique.values())
    start=process_time();wall=perf_counter()
    baseline=sum(len(gzip.compress(row,mtime=0)) for row in rows)
    gzip_cpu=process_time()-start;gzip_wall=perf_counter()-wall
    reports={}
    for limit in (1,2,4,8):
        groups=[];batch=[];size=0
        for row in rows:
            if len(row)+1>MAX_RAW:
                raise ValueError("snapshot exceeds pilot bound")
            if batch and (len(batch)>=limit or size+len(row)+1>MAX_RAW):
                groups.append(batch);batch=[];size=0
            batch.append(row);size+=len(row)+1
        if batch:groups.append(batch)
        start=process_time();wall=perf_counter()
        packed=[];index_bytes=0
        for group in groups:
            blob=pack(group)
            ids=[digest(r) for r in group]
            packed.append((blob,ids))
            # Account for a standalone index of content addresses + pack ID.
            block_id=digest(blob)
            index_bytes+=len(canonical(dict(pack=block_id,snapshots=ids)))
            # Include per-digest lookup pointers and retention metadata.
            # No whole-directory scan is acceptable in a live reader.
            index_bytes+=sum(len(canonical(dict(pack=block_id,sha256=key,mtime=1795700000.0)))
                             for key in ids)
        encode_cpu=process_time()-start;encode_wall=perf_counter()-wall
        start=process_time()
        restored=[]
        for blob,ids in packed:
            restored.extend(unpack(blob,ids))
        if restored!=rows:
            raise AssertionError("lossless raw snapshot roundtrip failed")
        decode_cpu=process_time()-start
        # Verify rollback re-materialization in the original gzip format.
        for row in restored:
            if gzip.decompress(gzip.compress(row,mtime=0))!=row:
                raise AssertionError("legacy gzip roundtrip failed")
        total=sum(len(blob) for blob,_ in packed)+index_bytes
        reports[str(limit)]=dict(bytes=total,index_bytes=index_bytes,blocks=len(groups),
            ratio=total/baseline if baseline else None,encode_cpu_s=encode_cpu,
            encode_wall_s=encode_wall,decode_cpu_s=decode_cpu,
            exact_sha_roundtrip=True,legacy_gzip_roundtrip=True)
    gzip_levels={}
    for level in (1,3,6):
        start=process_time()
        blobs=[gzip.compress(row,compresslevel=level,mtime=0) for row in rows]
        cpu=process_time()-start
        if any(gzip.decompress(blob)!=row for blob,row in zip(blobs,rows)):
            raise AssertionError("gzip level roundtrip failed")
        size=sum(map(len,blobs))
        gzip_levels[str(level)]=dict(bytes=size,ratio=size/baseline,encode_cpu_s=cpu)
    return dict(unique_snapshots=len(rows),raw_bytes=sum(map(len,rows)),
        baseline_gzip_bytes=baseline,baseline_cpu_s=gzip_cpu,baseline_wall_s=gzip_wall,
        gzip_levels=gzip_levels,
        variants=reports,process_peak_rss_kib=resource.getrusage(resource.RUSAGE_SELF).ru_maxrss)


def synthetic_snapshots(count):
    from benchmark_planner import snapshot
    from heating_stability import stable_plan,expected
    args=snapshot("scheduled")
    now,room,floor,outside,fire,model,weather,settings=args
    previous=None
    for i in range(count):
        if previous:
            room=expected(previous,now.timestamp(),"temperature","room")
            floor=expected(previous,now.timestamp(),"return","floor")
        plan,reference=stable_plan(now,room,floor,outside,fire,model,weather,settings,
                                   power_kw=3.,previous=previous)
        if plan is None:
            raise RuntimeError("synthetic plan unavailable")
        inputs=dict(now=now.isoformat(),timezone=str(now.tzinfo),room=room,floor=floor,
            outside=outside,fire=fire,model=model,weather=weather,settings=settings,
            power_kw=3.,previous=previous)
        refs={key:digest(canonical(value)) for key,value in dict(model=model,weather=weather,
            outage=settings.get("outage_schedule"),profile=settings.get("modulation_profile"),
            environment=settings.get("environment")).items()}
        yield canonical(dict(inputs=inputs,old_plan=previous,new_plan=plan,input_refs=refs))
        previous=reference
        now+=timedelta(minutes=5)


def journal_lifecycle(rows):
    """Measure actual native journal operations, including fsync and rollback.

    Disposable filesystem, no HA or archive mutation. The pilot is deliberately
    not a production daily CPU/RSS or worst-case disk latency certificate.
    """
    from heating_recovery import DecisionJournal
    from pack_decision_archive import restore_mixed_archive
    from tempfile import TemporaryDirectory
    from threading import RLock
    from time import time
    import math

    class TimedLock:
        def __init__(self):
            self.lock=RLock();self.durations=[]
        def __enter__(self):
            self.lock.acquire();self.started=perf_counter()
            return self
        def __exit__(self,*args):
            self.durations.append(perf_counter()-self.started)
            self.lock.release()

    def measure(fn):
        cpu=process_time();wall=perf_counter()
        result=fn()
        return result,dict(cpu_s=process_time()-cpu,wall_s=perf_counter()-wall)

    def sizes(folder):
        stats=[p.stat() for p in folder.rglob("*") if p.is_file()]
        return dict(files=len(stats),bytes=sum(s.st_size for s in stats),
                    allocated_bytes=sum(s.st_blocks*512 for s in stats))

    def timing_summary(values):
        ordered=sorted(values)
        return dict(count=len(ordered),p95_ms=1000*ordered[math.ceil(.95*len(ordered))-1],
                    max_ms=1000*ordered[-1]) if ordered else dict(count=0)

    rows=list(dict.fromkeys(rows))
    stamp=time()-3600
    ids=[digest(row) for row in rows]
    with TemporaryDirectory() as folder:
        root=Path(folder)
        journal=DecisionJournal(root,max_bytes=128*1024*1024)
        lock=TimedLock();journal.lock=lock
        def write():
            for i,row in enumerate(rows):
                if journal.snapshot(json.loads(row),stamp+i*300)!=ids[i]:
                    raise AssertionError("journal content address differs")
        _,write_cost=measure(write)
        initial=sizes(journal.directory)
        write_cost["lock"]=timing_summary(lock.durations);lock.durations.clear()
        def read():
            for key,row in zip(ids,rows):
                if canonical(journal.read_snapshot(key))!=row:
                    raise AssertionError("journal read differs")
        _,gzip_read=measure(read)
        gzip_read["lock"]=timing_summary(lock.durations);lock.durations.clear()
        groups=[];batch=[];size=0
        for key,row in zip(ids,rows):
            if batch and (len(batch)>=MAX_ROWS or size+len(row)+1>MAX_RAW):
                groups.append(batch);batch=[];size=0
            batch.append(key);size+=len(row)+1
        if batch:groups.append(batch)
        stages=[];promotions=[];skipped=0
        for batch in groups:
            if len(batch)<2:
                skipped+=len(batch);continue
            stage,metric=measure(lambda:journal.stage_snapshot_pack(batch,require_savings=True))
            metric["lock"]=timing_summary(lock.durations);lock.durations.clear()
            metric["status"]=stage["status"];stages.append(metric)
            if stage["status"]!="staged":
                skipped+=len(batch);continue
            promotion,metric=measure(lambda:journal.promote_snapshot_pack(stage["pack"],allow_remove=True))
            metric["lock"]=timing_summary(lock.durations);lock.durations.clear()
            metric["removed"]=promotion["removed"];promotions.append(metric)
        packed=sizes(journal.directory)
        _,packed_read=measure(read)
        packed_read["lock"]=timing_summary(lock.durations);lock.durations.clear()
        # Cold process-state simulation: the reader cannot rely on cached maps.
        restarted=DecisionJournal(root,max_bytes=128*1024*1024)
        for key,row in zip(ids,rows):
            if canonical(restarted.read_snapshot(key))!=row:
                raise AssertionError("restart read differs")
        _,accounting=measure(lambda:journal._prepare(time()))
        if journal.used_bytes!=packed["bytes"]:
            raise AssertionError("archive accounting differs from file sizes")
        restored=root/"legacy"
        rollback,rollback_cost=measure(lambda:restore_mixed_archive(journal.directory,restored))
        for key,row in zip(ids,rows):
            if gzip.decompress((restored/(key+".json.gz")).read_bytes())!=row:
                raise AssertionError("rollback bytes differ")
        return dict(snapshot_count=len(ids),skipped=skipped,initial=initial,packed=packed,
            ratio=packed["bytes"]/initial["bytes"],allocated_ratio=packed["allocated_bytes"]/initial["allocated_bytes"],
            gzip_write=write_cost,gzip_read=gzip_read,staging=stages,promotion=promotions,
            packed_read=packed_read,accounting=accounting,rollback=rollback,rollback_cost=rollback_cost,
            exact_read_restart_rollback=True,peak_rss_kib=resource.getrusage(resource.RUSAGE_SELF).ru_maxrss,
            scope="actual native filesystem journal on synthetic planner snapshots; initial gzip writes plus conversion cost")


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--count",type=int,default=12)
    parser.add_argument("--output",type=Path)
    args=parser.parse_args()
    if not 2<=args.count<=24:
        parser.error("count must be 2..24")
    rows=list(synthetic_snapshots(args.count))
    result=compare(rows)
    result["journal_lifecycle"]=journal_lifecycle(rows)
    result["scope"]="synthetic sequential full-recompute snapshots; offline codec and journal lifecycle pilot"
    report=json.dumps(result,indent=2)
    print(report)
    if args.output:
        args.output.write_text(report+"\n",encoding="utf-8")


if __name__=="__main__":
    main()
