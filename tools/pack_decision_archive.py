"""Offline, non-destructive export/restore of private decision archive snapshots.

Use a stopped controller or a consistent backup as source. No live migration,
no original deletion, and no overwrite of existing output directories.
"""
import argparse
import gzip
import json
import math
import os
from pathlib import Path
import shutil
import tempfile

from benchmark_archive_packing import canonical,digest,pack,unpack,MAX_RAW,MAX_ROWS,MAX_PACKED

SCHEMA=1


def identifier(value):
    return isinstance(value,str) and len(value)==64 and all(c in "0123456789abcdef" for c in value)


def write_file(path,raw,mtime=None):
    with path.open("xb") as stream:
        stream.write(raw)
        stream.flush()
        os.fsync(stream.fileno())
    if mtime is not None:
        os.utime(path,(mtime,mtime))


def sync_dir(path):
    fd=os.open(path,os.O_RDONLY)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


def build_copy(source,destination,writer):
    source=Path(source).resolve()
    destination=Path(destination).resolve()
    if (not source.is_dir() or destination.exists() or destination==source
            or source in destination.parents or destination in source.parents):
        raise ValueError("source and new destination must be distinct, non-nested directories")
    with tempfile.TemporaryDirectory(prefix=".heating-export-",dir=destination.parent) as tmp:
        staged=Path(tmp)/"archive"
        staged.mkdir()
        result=writer(source,staged)
        sync_dir(staged)
        if destination.exists():
            raise FileExistsError(destination)
        staged.rename(destination)
        sync_dir(destination.parent)
    return result


def read_gzip(path,key):
    with gzip.open(path,"rb") as stream:
        raw=stream.read(MAX_RAW+1)
    if len(raw)>=MAX_RAW or digest(raw)!=key or b"\n" in raw:
        raise ValueError("invalid, oversized, or corrupt snapshot: "+path.name)
    # Preserve canonical bytes verbatim; validate JSON independently.
    json.loads(raw)
    return raw


def export_archive(source,destination):
    """Copy all regular files, packing only exact content-addressed snapshots."""
    def writer(source,staged):
        block=[];meta=[];size=0;blocks=0;snapshots=0;original_bytes=0;packed_bytes=0
        def flush():
            nonlocal block,meta,size,blocks,snapshots,packed_bytes
            if not block:return
            blob=pack(block)
            ids=[p["sha256"] for p in meta]
            if unpack(blob,ids)!=block:
                raise ValueError("packed snapshot verification failed")
            key=digest(blob)
            header=canonical(dict(schema=SCHEMA,sha256=key,snapshots=meta))
            write_file(staged/(key+".xz"),blob)
            write_file(staged/(key+".pack.json"),header)
            blocks+=1;snapshots+=len(block);packed_bytes+=len(blob)+len(header)
            block=[];meta=[];size=0
        for path in sorted(source.iterdir(),key=lambda p:(p.stat().st_mtime,p.name)):
            if path.is_symlink() or not path.is_file():
                raise ValueError("unsupported archive entry: "+path.name)
            key=path.name[:-8] if path.name.endswith(".json.gz") else ""
            if not identifier(key):
                # Daily logs, non-addressed metadata and other evidence remain
                # byte-for-byte intact. Refuse reserved output extensions.
                if path.name.endswith((".pack.json",".xz")):
                    raise ValueError("reserved pack filename: "+path.name)
                shutil.copy2(path,staged/path.name)
                with (staged/path.name).open("rb") as stream:os.fsync(stream.fileno())
                continue
            before=path.stat()
            raw=read_gzip(path,key)
            after=path.stat()
            if (before.st_size,before.st_mtime_ns)!=(after.st_size,after.st_mtime_ns):
                raise ValueError("source changed during export")
            if block and (len(block)>=MAX_ROWS or size+len(raw)+1>MAX_RAW):
                flush()
            block.append(raw)
            meta.append(dict(sha256=key,mtime=before.st_mtime))
            size+=len(raw)+1;original_bytes+=before.st_size
        flush()
        return dict(snapshots=snapshots,blocks=blocks,original_snapshot_bytes=original_bytes,
                    packed_snapshot_bytes=packed_bytes)
    return build_copy(source,destination,writer)


def restore_archive(source,destination):
    """Verify each independent block and restore original content addresses."""
    def writer(source,staged):
        seen=set();blocks=0;restored=0
        for path in sorted(source.iterdir()):
            if path.is_symlink() or not path.is_file():
                raise ValueError("unsupported archive entry")
            if not path.name.endswith(".pack.json"):
                if not path.name.endswith(".xz"):
                    shutil.copy2(path,staged/path.name)
                    with (staged/path.name).open("rb") as stream:os.fsync(stream.fileno())
                continue
            if path.stat().st_size>16384:
                raise ValueError("oversized manifest")
            key=path.name[:-10]
            manifest=json.loads(path.read_bytes())
            if not identifier(key) or manifest.get("schema")!=SCHEMA or manifest.get("sha256")!=key:
                raise ValueError("invalid pack manifest")
            metadata=manifest.get("snapshots")
            if not isinstance(metadata,list) or not 1<=len(metadata)<=MAX_ROWS:
                raise ValueError("invalid snapshot index")
            ids=[]
            for row in metadata:
                snapshot=row.get("sha256")
                mtime=row.get("mtime")
                if (not identifier(snapshot) or snapshot in seen or not isinstance(mtime,(int,float))
                        or not math.isfinite(mtime) or mtime<0):
                    raise ValueError("invalid or repeated snapshot identity")
                ids.append(snapshot);seen.add(snapshot)
            blob_path=source/(key+".xz")
            if blob_path.is_symlink() or blob_path.stat().st_size>MAX_PACKED:
                raise ValueError("invalid block size")
            blob=blob_path.read_bytes()
            if digest(blob)!=key:
                raise ValueError("block checksum mismatch")
            for raw,row in zip(unpack(blob,ids),metadata):
                target=staged/(row["sha256"]+".json.gz")
                write_file(target,gzip.compress(raw,mtime=0),row["mtime"])
                if read_gzip(target,row["sha256"])!=raw:
                    raise ValueError("restored snapshot mismatch")
                restored+=1
            blocks+=1
        if len(list(source.glob("*.xz")))!=blocks:
            raise ValueError("unindexed packed block")
        return dict(snapshots=restored,blocks=blocks)
    return build_copy(source,destination,writer)


def restore_mixed_archive(source,destination):
    """Materialize legacy gzip from a consistent backup of native mixed storage.

    Only live per-digest pointers own packed evidence. Old members of a shared
    block may already have expired. Never resurrect them or alter the source.
    """
    from heating_recovery import DecisionJournal

    def writer(source,staged):
        journal=DecisionJournal(source.parent)
        journal.directory=source
        refs=[];copied=0;restored=0;verified=0
        folder=source/"packed_v1"
        if folder.is_symlink():
            raise ValueError("symlinked pack folder")
        for path in sorted(source.iterdir()):
            if path.is_symlink():
                raise ValueError("symlinked archive entry")
            if path==folder and path.is_dir():
                for item in path.iterdir():
                    if item.is_symlink() or not item.is_file():
                        raise ValueError("unsupported pack entry")
                continue
            if not path.is_file():
                raise ValueError("unsupported archive entry")
            if path.name.endswith(".snapshot-ref"):
                key=path.name[:-13]
                if not identifier(key):
                    raise ValueError("invalid snapshot reference")
                refs.append((key,path))
                continue
            key=path.name[:-8] if path.name.endswith(".json.gz") else ""
            if identifier(key):
                # Verify original evidence before copying, without requiring a
                # staged duplicate to be usable in order to recover legacy.
                with gzip.open(path,"rb") as stream:
                    raw=stream.read()
                if digest(raw)!=key:
                    raise ValueError("legacy snapshot checksum mismatch")
                json.loads(raw);verified+=1
            shutil.copy2(path,staged/path.name)
            with (staged/path.name).open("rb") as stream:os.fsync(stream.fileno())
            copied+=1
        for key,pointer in refs:
            target=staged/(key+".json.gz")
            if target.exists():
                latest=max(target.stat().st_mtime,pointer.stat().st_mtime)
                os.utime(target,(latest,latest))
                continue
            raw=journal._packed_snapshot(key)
            if digest(raw)!=key:
                raise ValueError("packed snapshot checksum mismatch")
            json.loads(raw)
            write_file(target,gzip.compress(raw,mtime=0),pointer.stat().st_mtime)
            if read_gzip(target,key)!=raw:
                raise ValueError("legacy materialization mismatch")
            restored+=1
        return dict(copied_files=copied,verified_legacy_snapshots=verified,
                    restored_packed_snapshots=restored,live_references=len(refs))
    return build_copy(source,destination,writer)


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument("operation",choices=("export","restore","restore-mixed"))
    parser.add_argument("source",type=Path)
    parser.add_argument("destination",type=Path)
    args=parser.parse_args()
    operation={"export":export_archive,"restore":restore_archive,"restore-mixed":restore_mixed_archive}[args.operation]
    result=operation(args.source,args.destination)
    print(json.dumps(result,indent=2))


if __name__=="__main__":
    main()
