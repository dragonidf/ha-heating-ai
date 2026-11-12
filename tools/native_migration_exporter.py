"""One-time AppDaemon data export to HA's shared configuration mount.

Install as a temporary app, after disabling the old heating app. It never
controls equipment, changes the source, or stops AppDaemon/Sky Radar.
"""
from datetime import datetime
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import time

from appdaemon.plugins.hass.hassapi import Hass

MAX_BYTES = 1200 * 1024 * 1024


def digest(path):
    result = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024*1024), b""):
            result.update(block)
    return result.hexdigest()


def export_snapshot(config_path, app_name, source_dir, destination, *, watchdog_disabled):
    config_bytes = Path(config_path).read_bytes()
    config = json.loads(config_bytes)
    if (not isinstance(config, dict) or config.get(app_name, {}).get("disable") is not True
            or watchdog_disabled is not True):
        raise ValueError("Old heating app and watchdog must be disabled before export")
    source = Path(source_dir).absolute()
    if source.is_symlink() or source.resolve() != source or not source.is_dir():
        raise ValueError("Untrusted V1 source directory")
    files = [p for p in source.iterdir() if p.is_file() and p.suffix in (".json", ".jsonl", ".gz")]
    history = source / "decision_history"
    if history.is_symlink():
        raise ValueError("Linked V1 decision archive")
    if history.exists():
        entries = list(history.rglob("*"))
        if any(p.is_symlink() for p in entries):
            raise ValueError("Linked V1 archive entry")
        files += [p for p in entries if p.is_file()]
    if any(p.is_symlink() or p.resolve() != p.absolute() for p in files):
        raise ValueError("Linked V1 data file")
    names = {str(p.relative_to(source)) for p in files}
    if not {"state.json", "telemetry.jsonl"} <= names:
        raise ValueError("Original state and telemetry are required")
    total = sum(p.stat().st_size for p in files)
    if total > MAX_BYTES:
        raise ValueError("V1 source exceeds bounded migration budget")
    destination = Path(destination).absolute()
    destination.parent.mkdir(parents=True, exist_ok=True)
    if destination.parent.resolve() != destination.parent:
        raise ValueError("Linked migration destination")
    if destination.exists() or destination.is_symlink():
        raise FileExistsError("Never overwrite an existing native V1 installation")
    if shutil.disk_usage(destination.parent).free < total+1024*1024*1024:
        raise OSError("Insufficient space for migration and rollback reserve")
    staging = destination.with_name(destination.name+".importing")
    staging.mkdir(mode=0o700)
    manifest = hashlib.sha256()
    try:
        for path in sorted(files):
            relative = path.relative_to(source)
            before = path.stat()
            target = staging / "data" / relative
            target.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
            shutil.copyfile(path, target)
            os.chmod(target, 0o600)
            checksum = digest(target)
            after = path.stat()
            if (before.st_size, before.st_mtime_ns) != (after.st_size, after.st_mtime_ns) or checksum != digest(path):
                raise ValueError("V1 source changed during export")
            manifest.update(str(relative).encode()+b"\0"+checksum.encode()+b"\n")
        if Path(config_path).read_bytes() != config_bytes:
            raise ValueError("Old app configuration changed during export")
        state_bytes = (staging / "data/state.json").read_bytes()
        state = json.loads(state_bytes)
        if not isinstance(state.get("model"), dict):
            raise ValueError("Original V1 model is missing")
        (staging / "config.json").write_bytes(config_bytes)
        (staging / "original-state.json").write_bytes(state_bytes)
        report = dict(complete=True, legacy_disabled=True, watchdog_disabled=True,
            model_version=state["model"].get("version"),
            model_validated=state["model"].get("validated"),
            files=len(files), bytes=total, state_sha256=hashlib.sha256(state_bytes).hexdigest(),
            config_sha256=hashlib.sha256(config_bytes).hexdigest(),
            data_manifest_sha256=manifest.hexdigest(), prepared_at=time.time())
        (staging / "import-report.json").write_text(json.dumps(report, allow_nan=False))
        for name in ("config.json", "original-state.json", "import-report.json"):
            os.chmod(staging / name, 0o600)
        os.replace(staging, destination)
        return report
    except BaseException:
        shutil.rmtree(staging)
        raise


class HeatingNativeMigrationExport(Hass):
    def initialize(self):
        identity = self.args["installation_id"]
        if not re.fullmatch(r"[a-f0-9]{32}", identity):
            raise ValueError("Invalid native installation identity")
        self.run_in(self.export, 2)

    def export(self, kwargs):
        try:
            heartbeat = self.get_state("sensor.heating_heartbeat")
            if heartbeat:
                stamp = datetime.fromisoformat(heartbeat.replace("Z", "+00:00")).timestamp()
                if time.time()-stamp < 45:
                    self.run_in(self.export, 46-(time.time()-stamp))
                    return
            report = export_snapshot(self.args["source_config"], self.args["source_app"],
                self.args["source_data"], Path("/homeassistant/heating_ai_v1")/self.args["installation_id"],
                watchdog_disabled=self.get_state(self.args["watchdog"]) == "off")
            self.set_state("sensor.heating_native_migration", state="Готово", attributes={
                **report, "installation_id": self.args["installation_id"],
                "friendly_name": "Опалення — перенесення приватної історії"})
        except Exception as exc:
            self.set_state("sensor.heating_native_migration", state="Помилка", attributes={"reason":str(exc)})
            self.log("Native V1 migration failed: %s", exc, level="ERROR")
