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


def observe_room(store: NativeStore, at: float, room: float) -> None:
    """Only a HA-cache measurement; no kWh reconstruction or meter reset."""
    store.observe(at=at, room=room, counter=None, power=None)
