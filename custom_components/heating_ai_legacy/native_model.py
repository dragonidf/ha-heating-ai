"""Conservative private V1 trained-model checkpoint, no learning or meter writes.

A model cached from the existing V1 HA entity survives native restarts.
It can seed *conditional simulations*, never satisfy fresh source safety.
Checksum and atomic rename protect against partial writes.
"""
from __future__ import annotations

from hashlib import sha256
import json
import math
import os
from pathlib import Path
from typing import Any

FILENAME = "v1_thermal_checkpoint.json"
COEFFICIENTS = ("floor_air", "room_loss", "floor_decay", "heat_gain", "fire_gain")
MAX_SIZE = 8192


def sanitize(parameters: Any) -> dict:
    if not isinstance(parameters, dict):
        raise ValueError("Original V1 model missing")
    result: dict[str, int | float | bool] = {}
    for key in COEFFICIENTS:
        raw = parameters.get(key)
        if type(raw) not in (float, int):
            raise ValueError("Original V1 model has nonnumeric coefficient")
        value = float(raw)
        if not math.isfinite(value) or not -10 < value < 10:
            raise ValueError("Original V1 coefficient is not finite")
        result[key] = value
    version = parameters.get("version")
    if type(version) is not int or not 0 <= version <= 1000000:
        raise ValueError("Original V1 version invalid")
    if parameters.get("validated") not in (True, "true", "True"):
        raise ValueError("Unvalidated model cannot replace learned checkpoint")
    result["version"] = version
    result["validated"] = True
    return result


def _canonical(obj: dict) -> bytes:
    return json.dumps(obj,sort_keys=True,separators=(",",":"),allow_nan=False).encode()


def load(directory: str | Path) -> dict | None:
    path = Path(directory) / FILENAME
    if not path.exists():
        return None
    if path.is_symlink() or not path.is_file() or path.stat().st_size > MAX_SIZE:
        raise ValueError("Unsafe V1 model checkpoint path")
    raw = path.read_bytes()
    try:
        payload = json.loads(raw)
        record = payload["record"]
        if payload["sha256"] != sha256(_canonical(record)).hexdigest():
            raise ValueError("V1 checkpoint checksum mismatch")
        model = sanitize(record["model"])
        stamp = record["observed_at"]
        if type(stamp) not in (int,float) or not math.isfinite(stamp) or stamp <= 0:
            raise ValueError("V1 checkpoint observation timestamp invalid")
        return dict(model=model,observed_at=stamp,source="legacy_HA_cache_checkpoint")
    except (KeyError, TypeError, ValueError, OverflowError) as exc:
        raise ValueError("V1 checkpoint invalid; do not overwrite it") from exc


def remember(directory: str | Path, source: Any, observed_at: float) -> dict:
    """Write one validated source revision, never downgrade or corrupt it."""
    root = Path(directory)
    if root.is_symlink() or not root.is_dir() or root.resolve() != root.absolute():
        raise ValueError("Trusted V1 model directory required")
    if type(observed_at) not in (int,float) or not math.isfinite(observed_at) or observed_at <= 0:
        raise ValueError("Original model source timestamp unavailable")
    model = sanitize(source)
    prior = load(root)
    if prior is not None and (
        model["version"] < prior["model"]["version"] or
        observed_at < prior["observed_at"]
    ):
        raise ValueError("Refusing stale or downgraded learned model")
    if prior is not None and prior["model"] == model:
        return prior
    record = dict(schema=1,model=model,observed_at=observed_at)
    document = dict(record=record,sha256=sha256(_canonical(record)).hexdigest())
    raw = _canonical(document)+b"\n"
    if len(raw)>MAX_SIZE:
        raise ValueError("V1 checkpoint too large")
    dest = root / FILENAME
    temporary = root / (FILENAME + ".tmp")
    if temporary.exists() or temporary.is_symlink():
        raise ValueError("Unsafe leftover V1 checkpoint transaction")
    fd = os.open(temporary,os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW,0o600)
    try:
        with os.fdopen(fd,"wb") as stream:
            stream.write(raw)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary,dest)
        dfd=os.open(root,os.O_RDONLY | os.O_DIRECTORY)
        try:
            os.fsync(dfd)
        finally:
            os.close(dfd)
    except Exception:
        if temporary.exists():
            temporary.unlink()
        raise
    return dict(model=model,observed_at=observed_at,source="legacy_HA_cache_checkpoint")
