"""Independent floor heat observer using UNCHANGED numerical V1 function.

Derived from measured return, cumulative KWS, current boiler power,
and the accepted learned V1 model. Storage is private and non-actuating.
"""
from __future__ import annotations

from hashlib import sha256
import json
import math
import os
from pathlib import Path
from typing import Any

from .native_engine.heating_environment import observe_floor

FILENAME="v1_floor_observer.json"
MAX_SIZE=8192


def _numeric(value: Any, low: float, high: float) -> float:
    if type(value) not in (float,int) or not math.isfinite(value) or not low<=value<=high:
        raise ValueError("floor observer input not qualified")
    return float(value)


def _canonical(value: Any) -> bytes:
    return json.dumps(value,sort_keys=True,separators=(",",":"),allow_nan=False).encode()


def _validate(state: Any) -> dict:
    if type(state) is not dict:
        raise ValueError("Missing floor observer")
    if len(state)>20:
        raise ValueError("Oversized floor observer")
    result=dict(state)
    result["at"]=_numeric(state.get("at"),1,1e12)
    result["estimate"]=_numeric(state.get("estimate"),-20,90)
    last=state.get("power_kw")
    if last is not None:
        result["power_kw"]=_numeric(last,0,12)
    energy=state.get("energy_kwh")
    if energy is not None:
        result["energy_kwh"]=_numeric(energy,0,1e9)
    return result


def advance(previous: dict | None, stamp: float, room: float, probe: float,
            power_w: float, grid: bool | None,
            model: dict, energy_kwh: float | None,
            *, legacy_estimate: float | None = None) -> dict:
    """Propose next floor inventory; unavailable KWS cannot be coerced to 0."""
    stamp=_numeric(stamp,1,1e12)
    room=_numeric(room,-20,55)
    probe=_numeric(probe,0,90)
    power_w=_numeric(power_w,0,36000)
    if energy_kwh is not None:
        energy_kwh=_numeric(energy_kwh,0,1e9)
    if grid is not True and grid is not False:
        raise ValueError("Unqualified grid for measured floor inventory")
    if type(model) is not dict:
        raise ValueError("Unqualified learned model")
    if previous is not None:
        previous=_validate(previous)
        if stamp <= previous["at"]:
            raise ValueError("Non-monotonic physical floor observation")
        if stamp-previous["at"]>14*86400:
            # A long offline interval cannot be silently integrated as heat.
            previous=None
    state,estimated = observe_floor(
        previous,stamp,room,probe,power_w/1000,
        "on" if grid else "off",model,energy_kwh=energy_kwh,
    )
    state=_validate(state)
    if previous is None and legacy_estimate is not None:
        # During overlap, inherit the real observer inventory rather than
        # resetting it to a stagnant return pipe at cutover.
        state["estimate"]=_numeric(legacy_estimate,-20,90)
        state["source"]="qualified_legacy_floor_seed"
    else:
        state["estimate"]=_numeric(estimated,-20,90)
    return _validate(state)


def load(directory: str | Path) -> dict | None:
    path=Path(directory)/FILENAME
    if not path.exists():
        return None
    if path.is_symlink() or not path.is_file() or path.stat().st_size>MAX_SIZE:
        raise ValueError("Unsafe private floor checkpoint path")
    try:
        record=json.loads(path.read_bytes())
        state=record["floor"]
        if sha256(_canonical(state)).hexdigest()!=record["sha256"]:
            raise ValueError("Floor checkpoint checksum mismatch")
        return _validate(state)
    except (KeyError,TypeError,ValueError,OverflowError) as exc:
        raise ValueError("Floor checkpoint invalid; do not overwrite") from exc


def remember(directory: str | Path, state: dict) -> dict:
    root=Path(directory).absolute()
    if not root.is_dir() or root.is_symlink() or root.resolve()!=root:
        raise ValueError("Private floor directory not trusted")
    valid=_validate(state)
    prior=load(root)
    if prior is not None and prior["at"]>valid["at"]:
        raise ValueError("Refusing older floor observation")
    if prior==valid:
        return valid
    record=dict(floor=valid,sha256=sha256(_canonical(valid)).hexdigest())
    raw=_canonical(record)+b"\n"
    if len(raw)>MAX_SIZE:
        raise ValueError("Floor checkpoint too large")
    dest=root/FILENAME
    temporary=root/(FILENAME+".tmp")
    if temporary.exists() or temporary.is_symlink():
        raise ValueError("Incomplete floor checkpoint transaction")
    fd=os.open(temporary,os.O_CREAT|os.O_EXCL|os.O_WRONLY|os.O_NOFOLLOW,0o600)
    try:
        with os.fdopen(fd,"wb") as stream:
            stream.write(raw)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary,dest)
        parent=os.open(root,os.O_RDONLY|os.O_DIRECTORY)
        try:
            os.fsync(parent)
        finally:
            os.close(parent)
    except Exception:
        if temporary.exists():
            temporary.unlink()
        raise
    return valid
