"""Read-only, checksum-verified legacy V1 migration dry-run.

This adapter never opens, modifies, renames or truncates the old AppDaemon
state.json. It does not reset KWS, copy equipment commands, restore stale
auto-resume, or grant a native writer lease.

Dry-run requires the original bytes and produces a bounded summary; the
separate explicitly confirmed archive step writes an OPAQUE evidence copy to
the native .storage directory, not an active model/counter/one-shot state.
"""
from __future__ import annotations

from hashlib import sha256
import json
import math
import os
from pathlib import Path
from typing import Any

MAX_STATE_BYTES = 32 * 1024 * 1024
MAX_TELEMETRY_BYTES = 160 * 1024 * 1024
ARCHIVE_NAME = "v1-original-state.json"
EVIDENCE_NAME = "v1-import-evidence.json"
KEYS = ("floor_air", "room_loss", "floor_decay", "heat_gain", "fire_gain")
SAFE_COLLECTIONS = ("weather", "energy_bins", "journal")


def _number(value: Any, low: float, high: float) -> bool:
    return (type(value) in (int, float) and math.isfinite(value)
            and low <= value <= high)


def _parse_state(raw: bytes) -> dict:
    if not isinstance(raw, bytes) or not 0 < len(raw) <= MAX_STATE_BYTES:
        raise ValueError("Original state bytes missing or too large")
    try:
        data = json.loads(raw)
    except (UnicodeError, ValueError, TypeError) as error:
        raise ValueError("Original state is not valid UTF-8 JSON") from error
    if type(data) is not dict:
        raise ValueError("Original state root is not an object")
    model = data.get("model")
    if type(model) is not dict or any(
        not _number(model.get(key), -10, 10) for key in KEYS
    ):
        raise ValueError("Original V1 model is incomplete or corrupt")
    if type(model.get("version")) is not int or model["version"] < 0:
        raise ValueError("Original V1 model has no version")
    if type(model.get("validated")) is not bool:
        raise ValueError("Original V1 model validity is unverified")
    for key in SAFE_COLLECTIONS:
        if key in data and type(data[key]) is not list:
            raise ValueError(f"Original {key} has an invalid type")
    bins = data.get("energy_bins") or []
    for row in bins:
        if not isinstance(row, dict):
            raise ValueError("Original energy bin is invalid")
    energy_last = data.get("energy_last")
    if type(energy_last) is dict:
        if (not _number(energy_last.get("total"), 0, 1e10)
                or not _number(energy_last.get("ts"), 0, 1e11)):
            raise ValueError("Original energy cursor invalid")
    elif energy_last is not None and not _number(energy_last, 0, 1e10):
        raise ValueError("Original energy cursor invalid")
    return data


def preview(raw: bytes) -> dict:
    """Report exactly what can be preserved without mutating it."""
    state = _parse_state(raw)
    model = state["model"]
    energy_last = state.get("energy_last")
    energy_cursor = energy_last.get("total") if type(energy_last) is dict else energy_last
    energy_cursor_at = energy_last.get("ts") if type(energy_last) is dict else None
    return dict(
        status="DRY_RUN_ONLY",
        sha256=sha256(raw).hexdigest(),
        bytes=len(raw),
        model_version=model["version"],
        model_validated=model["validated"],
        energy_cursor=energy_cursor,
        energy_cursor_at=energy_cursor_at,
        energy_bin_count=len(state.get("energy_bins") or []),
        weather_points=len(state.get("weather") or []),
        journal_count=len(state.get("journal") or []),
        has_floor_observer=type(state.get("floor_observer")) is dict,
        has_solar_observer=type(state.get("solar_observer")) is dict,
        has_ventilation_model=type(state.get("ventilation_model")) is dict,
        has_cycle_energy=type(state.get("cycle_energy")) is dict,
        has_plan_reference=type(state.get("plan_reference")) is dict,
        manual_resume_forced_off=True,
        one_shot_forced_off=True,
        legacy_controller_stays_owner=True,
        native_physical_commands_enabled=False,
    )


def archive_original(raw: bytes, private_dir: str | Path,
                     *, expected_sha256: str, consent: bool) -> dict:
    """Write an opaque original evidence snapshot only with explicit consent.

    Never load this as an executable controller state; never overwrite an
    existing backup or silently adopt its energy cursor into SQLite.
    """
    if consent is not True:
        raise PermissionError("Import needs explicit approval")
    report = preview(raw)
    if (not isinstance(expected_sha256, str)
            or expected_sha256 != report["sha256"]):
        raise ValueError("Source SHA-256 changed after dry run")
    directory = Path(private_dir).absolute()
    if not directory.is_dir() or directory.is_symlink() or directory.resolve()!=directory:
        raise ValueError("Private native directory is untrusted")
    target = directory / ARCHIVE_NAME
    record = directory / EVIDENCE_NAME
    if target.exists() or target.is_symlink() or record.exists() or record.is_symlink():
        raise FileExistsError("Original V1 evidence already archived")
    raw_out = json.dumps(report, sort_keys=True, allow_nan=False).encode()+b"\n"
    opened = []
    try:
        for name,content in ((ARCHIVE_NAME,raw),(EVIDENCE_NAME,raw_out)):
            path=directory/name
            fd=os.open(path,os.O_WRONLY|os.O_CREAT|os.O_EXCL|os.O_NOFOLLOW,0o600)
            opened.append(path)
            with os.fdopen(fd,"wb") as f:
                f.write(content)
                f.flush()
                os.fsync(f.fileno())
        fd=os.open(directory,os.O_RDONLY|os.O_DIRECTORY)
        try:
            os.fsync(fd)
        finally:
            os.close(fd)
    except Exception:
        for path in opened:
            try:
                path.unlink()
            except FileNotFoundError:
                pass
        raise
    return dict(report, archived=True, native_model_promoted=False,
                native_energy_cursor_changed=False)


def read_original(path: str | Path, *, allowed_root: str | Path) -> bytes:
    """Read an explicitly selected existing file, never follow symlinks."""
    root=Path(allowed_root).absolute()
    source=Path(path).absolute()
    if not root.is_dir() or root.is_symlink() or root.resolve()!=root:
        raise ValueError("Allowed source directory is untrusted")
    if source.parent != root or source.name != "state.json":
        raise PermissionError("Only exact original state.json in selected root")
    if source.is_symlink() or not source.is_file():
        raise ValueError("Original state file absent or linked")
    size=source.stat().st_size
    if size<1 or size>MAX_STATE_BYTES:
        raise ValueError("Original state file outside size limit")
    # O_NOFOLLOW prevents an unnoticed symlink replacement at open.
    fd=os.open(source,os.O_RDONLY|os.O_NOFOLLOW)
    try:
        data=os.read(fd,MAX_STATE_BYTES+1)
    finally:
        os.close(fd)
    preview(data)
    return data
