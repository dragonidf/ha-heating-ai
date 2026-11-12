"""Build/verify a non-actuating Marketplace carrier for Heating AI V1.

The release ZIP installs under custom_components/heating_ai_legacy.
The nine verified AppDaemon modules live in legacy_app/ but are inert until
the owner explicitly repoints ONE existing AppDaemon app to this directory.
No YAML, saved model, counters or personal configuration are included.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import re
from pathlib import Path
import zipfile

ROOT = Path(__file__).resolve().parents[1]
DOMAIN = "heating_ai_legacy"
SOURCE = ROOT / "apps" / "heating_ai"
CARRIER = ROOT / "custom_components" / DOMAIN
EXPECTED = (
    "heating_climate.py",
    "heating_environment.py",
    "heating_fallback.py",
    "heating_model.py",
    "heating_outages.py",
    "heating_recovery.py",
    "heating_stability.py",
    "heating_ui.py",
    "heating_ui_assets.py",
)
RELEASE = "release.json"
ZIP_NAME = "heating_ai_v1_marketplace.zip"

# These five original V1 modules are independent of AppDaemon Hass.
# Copy their algorithms identically, changing only top-level absolute
# imports into explicit package-relative imports; all executable logic is
# preserved byte-for-byte and regression-checked against the V1 source.
PURE_MODULES = (
    "heating_environment.py",
    "heating_outages.py",
    "heating_model.py",
    "heating_fallback.py",
    "heating_stability.py",
)
RELATIVE_IMPORTS = (
    "heating_environment", "heating_outages", "heating_model",
    "heating_fallback", "heating_stability",
)


_ABS_IMPORT = re.compile(r"(?m)^from (heating_(?:environment|outages|model|fallback|stability)) import ")
_REL_IMPORT = re.compile(r"(?m)^from [.](heating_(?:environment|outages|model|fallback|stability)) import ")


def as_native_module(original: bytes) -> bytes:
    text = original.decode("utf-8")
    transformed = _ABS_IMPORT.sub(r"from .\1 import ", text)
    if "from appdaemon" in transformed or "import appdaemon" in transformed:
        raise ValueError("AppDaemon dependency leaked into native pure engine")
    return transformed.encode("utf-8")


def verify_native_module(native: bytes, original: bytes) -> None:
    restored = _REL_IMPORT.sub(r"from \1 import ", native.decode("utf-8"))
    if restored.encode("utf-8") != original:
        raise ValueError("Native engine changed V1 computational logic")


def checksum(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def source_and_manifest() -> tuple[dict[str, bytes], bytes]:
    app_version = (ROOT / "VERSION").read_text(encoding="utf-8").strip()
    published = json.loads((SOURCE / RELEASE).read_text(encoding="utf-8"))
    if published.get("version") != app_version:
        raise ValueError("Legacy release.json and VERSION differ")
    hashes = published.get("module_hashes")
    if not isinstance(hashes, dict) or set(hashes) != set(EXPECTED):
        raise ValueError("Nine-module release manifest is incomplete")
    contents: dict[str, bytes] = {}
    for name in EXPECTED:
        path = SOURCE / name
        if not path.is_file() or path.is_symlink():
            raise ValueError(f"Missing or linked authoritative source: {name}")
        data = path.read_bytes()
        if checksum(data) != hashes[name]:
            raise ValueError(f"Module checksum mismatch: {name}")
        contents[f"legacy_app/{name}"] = data
    contents[f"legacy_app/{RELEASE}"] = (SOURCE / RELEASE).read_bytes()

    # Staged migration: immutable V1 math as a native Python library.
    # Nothing inside this package opens HA connections or sends commands.
    contents["native_engine/__init__.py"] = (
        b'"""Original V1 model in an isolated package; no HA commands."""' + bytes([10])
    )
    for name in PURE_MODULES:
        original = contents[f"legacy_app/{name}"]
        native = as_native_module(original)
        verify_native_module(native, original)
        compile(native, f"native_engine/{name}", "exec")
        contents[f"native_engine/{name}"] = native

    root_manifest = (ROOT / "manifest.json").read_bytes()
    nested_manifest = (CARRIER / "manifest.json").read_bytes()
    if root_manifest != nested_manifest:
        raise ValueError("Version tag root manifest does not match carrier")
    manifest = json.loads(root_manifest)
    if manifest.get("domain") != DOMAIN or manifest.get("version") != "1.0.0":
        raise ValueError("Unexpected Marketplace carrier domain/version")
    if manifest.get("config_flow") is not True:
        raise ValueError("Native integration must register a config flow")
    if manifest.get("requirements") != []:
        raise ValueError("Carrier must not install Python dependencies in HA")
    hacs = json.loads((ROOT / "hacs.json").read_text(encoding="utf-8"))
    if not (hacs.get("zip_release") is True and
            hacs.get("content_in_root") is True and
            hacs.get("filename") == ZIP_NAME and
            hacs.get("hide_default_branch") is True):
        raise ValueError("Marketplace packaging fields are inconsistent")
    contents["manifest.json"] = nested_manifest
    for name in ("__init__.py", "const.py", "config_flow.py",
                 "native_runtime.py", "plan_snapshot.py",
                 "native_planner.py", "native_sources.py", "native_api.py",
                 "native_safety.py", "native_intent.py", "native_store.py", "native_persistence.py",
                 "native_model.py", "native_fan.py", "native_fan_sources.py",
                 "native_migration.py", "critical_ledger.py", "sensor.py", "strings.json",
                 "assets/panel.js"):
        contents[name] = (CARRIER / name).read_bytes()
        if name.endswith(".py"):
            compile(contents[name], name, "exec")
    # A manually-approved cutover is intentionally impossible in this preview.
    if b"async_call" in contents["__init__.py"] or b"call_service" in contents["__init__.py"]:
        raise ValueError("Native preview cannot contain equipment service calls")
    return contents, app_version.encode()


def build(destination: Path) -> dict[str, object]:
    contents, version = source_and_manifest()
    destination.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(destination, "w", compression=zipfile.ZIP_DEFLATED) as output:
        for name, data in sorted(contents.items()):
            if name.startswith("/") or ".." in Path(name).parts:
                raise ValueError("Unsafe archive member")
            info = zipfile.ZipInfo(name, date_time=(2026, 11, 12, 0, 0, 0))
            info.compress_type = zipfile.ZIP_DEFLATED
            info.external_attr = 0o644 << 16
            output.writestr(info, data)
    with zipfile.ZipFile(destination) as check:
        if set(check.namelist()) != set(contents) or check.testzip() is not None:
            raise ValueError("Invalid or incomplete release archive")
        for name, data in contents.items():
            if check.read(name) != data:
                raise ValueError(f"Archive corruption at {name}")
    return dict(legacy_version=version.decode(), carrier_version="1.0.0",
                files=len(contents), bytes=destination.stat().st_size,
                sha256=checksum(destination.read_bytes()))


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--output", default=ZIP_NAME)
    args = ap.parse_args()
    print(json.dumps(build(Path(args.output)), indent=2))
