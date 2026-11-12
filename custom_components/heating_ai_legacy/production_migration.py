"""Import an operator-selected V1 archive without touching its source."""
from __future__ import annotations

from hashlib import sha256
from io import BytesIO
import json
import os
from pathlib import Path, PurePosixPath
import shutil
import stat
import zipfile

from .native_migration import _parse_state

MAX_TOTAL = 1200 * 1024 * 1024
MAX_FILE = 160 * 1024 * 1024
REQUIRED_ROLES = ("thermostat", "room", "return", "outside", "grid", "connection",
                  "grid_voltage", "target", "lower", "upper", "mode", "armed",
                  "start", "end", "strategy", "power_balance", "energy", "fan",
                  "ai_proposal", "ai_report", "rollback", "restore_once")


def inspect_bundle(raw: bytes) -> tuple[dict, dict[str, bytes], dict]:
    if not isinstance(raw, bytes) or not 0 < len(raw) <= MAX_TOTAL:
        raise ValueError("Migration archive is empty or too large")
    files = {}
    total = 0
    with zipfile.ZipFile(BytesIO(raw)) as archive:
        if len(archive.infolist()) > 250000:
            raise ValueError("Too many migration files")
        for info in archive.infolist():
            path = PurePosixPath(info.filename)
            if (info.is_dir() or info.filename in files or path.is_absolute()
                    or any(p in (".", "..") for p in path.parts)
                    or "\\" in info.filename or str(path) != info.filename
                    or stat.S_ISLNK(info.external_attr >> 16)):
                raise ValueError("Unsafe migration archive path")
            if info.file_size > MAX_FILE:
                raise ValueError("Migration file is too large")
            total += info.file_size
            if total > MAX_TOTAL:
                raise ValueError("Expanded migration archive is too large")
            if info.filename not in ("config.json", "handoff.json"):
                if (len(path.parts) < 2 or path.parts[0] != "data"
                        or path.suffix not in (".json", ".jsonl", ".gz")):
                    raise ValueError("Migration archive may contain data only")
            files[info.filename] = archive.read(info)
    if not {"config.json", "handoff.json", "data/state.json", "data/telemetry.jsonl"} <= files.keys():
        raise ValueError("Configuration, state and writer handoff are required")
    cfg = json.loads(files["config.json"])
    if isinstance(cfg, dict) and "heating_climate_controller" in cfg:
        cfg = cfg["heating_climate_controller"]
    if not isinstance(cfg, dict) or any(
        not isinstance(cfg.get(k), str) or cfg[k].count(".") != 1
        for k in REQUIRED_ROLES
    ):
        raise ValueError("Original V1 entity configuration is incomplete")
    state = _parse_state(files["data/state.json"])
    handoff = json.loads(files["handoff.json"])
    if (not isinstance(handoff, dict) or handoff.get("legacy_disabled") is not True
            or handoff.get("watchdog_disabled") is not True
            or handoff.get("state_sha256") != sha256(files["data/state.json"]).hexdigest()
            or handoff.get("config_sha256") != sha256(files["config.json"]).hexdigest()):
        raise ValueError("Writer handoff or original checksums are unverified")
    # V1's own startup invalidates unobserved predictions. Do not reinterpret
    # the learned model, energy bins/cursors, journal or forecast history here.
    report = dict(bundle_sha256=sha256(raw).hexdigest(), files=len(files), bytes=total,
                  model_version=state["model"]["version"],
                  model_validated=state["model"]["validated"],
                  state_sha256=handoff["state_sha256"], legacy_disabled=True,
                  config_sha256=handoff["config_sha256"],
                  watchdog_disabled=True, complete=True)
    return cfg, files, report


def install_bundle(raw: bytes, destination: Path) -> dict:
    cfg, files, report = inspect_bundle(raw)
    root = Path(destination)
    if root.exists() or root.is_symlink():
        raise FileExistsError("Existing migrated data must never be overwritten")
    root.parent.mkdir(parents=True, exist_ok=True)
    temporary = root.with_name(root.name + ".importing")
    temporary.mkdir(mode=0o700)
    try:
        for name, data in files.items():
            target = temporary / name
            target.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
            with target.open("xb") as output:
                os.chmod(target, 0o600)
                output.write(data)
                output.flush()
                os.fsync(output.fileno())
        # Keep an immutable byte-for-byte state snapshot alongside the active copy.
        (temporary / "original-state.json").write_bytes(files["data/state.json"])
        (temporary / "import-report.json").write_text(json.dumps(report, allow_nan=False))
        os.replace(temporary, root)
    except BaseException:
        shutil.rmtree(temporary)
        raise
    return report


def load_installation(root: Path) -> tuple[dict, dict]:
    root = Path(root)
    if root.is_symlink() or root.resolve() != root.absolute():
        raise ValueError("Untrusted controller data directory")
    report = json.loads((root / "import-report.json").read_text())
    if (report.get("complete") is not True or report.get("legacy_disabled") is not True
            or report.get("watchdog_disabled") is not True):
        raise ValueError("Native V1 has no completed migration and writer handoff")
    original = (root / "original-state.json").read_bytes()
    if sha256(original).hexdigest() != report.get("state_sha256"):
        raise ValueError("Original migration evidence changed")
    _parse_state((root / "data" / "state.json").read_bytes())
    config_bytes = (root / "config.json").read_bytes()
    if sha256(config_bytes).hexdigest() != report.get("config_sha256"):
        raise ValueError("Original migration configuration changed")
    cfg = json.loads(config_bytes)
    if "heating_climate_controller" in cfg:
        cfg = cfg["heating_climate_controller"]
    return cfg, report
