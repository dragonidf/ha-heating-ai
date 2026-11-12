"""Native private V1+ storage and V2-style fenced ledger with no sender.

Storage creation is *only* inside HA Core's .storage and is never allowed to
reinitialize the old AppDaemon model, archive or meter. Every restart fences
pending commands. No physical authority is handed out here.
"""
from __future__ import annotations

from pathlib import Path
import os
from uuid import uuid4

from .critical_ledger import CriticalLedger, FILENAME
from .native_store import NativeStore


def bootstrap(directory: str) -> tuple[NativeStore, CriticalLedger, dict]:
    path = Path(directory).absolute()
    path.mkdir(mode=0o700, parents=True, exist_ok=True)
    if not path.is_dir() or path.is_symlink() or path.resolve() != path:
        raise ValueError("Native V1 private directory must be trusted")
    os.chmod(path, 0o700)
    store = NativeStore(path)
    # Every application startup has a fresh boot id. Fenced journals cannot
    # be mistaken for a live owner merely because the previous Core exited.
    boot_id = uuid4().hex
    ledger_file = path / FILENAME
    ledger = (
        CriticalLedger(path) if ledger_file.exists() else
        CriticalLedger(path, owner_id="heating_ai_legacy",
                       boot_id=boot_id, owner_epoch=0)
    )
    previous = ledger.status()
    ledger.reconcile_restart(
        boot_id, expected_revision=previous["revision"],
        owner_id=previous["owner_id"], boot_id=previous["boot_id"],
        owner_epoch=previous["owner_epoch"],
    )
    status = ledger.status()
    if (status.get("fenced") is not True or
            status.get("physical_commands_enabled") is not False):
        raise ValueError("Native V1 journal did not fence on startup")
    return store, ledger, status


def observe_room(store: NativeStore, at: float, room: float,
                 *, counter_kwh=None, boiler_power_w=None) -> None:
    """Persist HA-cache observations, never reinterpret estimated readings.

    A genuine KWS hardware total is the only energy cursor. Missing KWS
    remains None (a gap), never 0, never a synthetic accumulation from
    nominal power, and never a duplicate integral.
    """
    import math

    if type(at) not in (float, int) or not math.isfinite(at) or at <= 0:
        raise ValueError("Unqualified observation clock")
    if type(room) not in (float, int) or not math.isfinite(room) or not -20 <= room <= 55:
        raise ValueError("Unqualified room sample")
    if counter_kwh is not None and (
        type(counter_kwh) not in (float,int) or
        not math.isfinite(counter_kwh) or not 0 <= counter_kwh <= 1e9
    ):
        raise ValueError("Unqualified hardware energy counter")
    if boiler_power_w is not None and (
        type(boiler_power_w) not in (float,int) or
        not math.isfinite(boiler_power_w) or not 0 <= boiler_power_w <= 36000
    ):
        raise ValueError("Unqualified KWS active power")
    store.observe(
        at=at, room=room, counter=counter_kwh,
        power=boiler_power_w, counter_at=at,
    )
