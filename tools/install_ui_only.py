"""Publish only compatible, locally tested Heating AI frontend assets.

Run from a trusted HA host/terminal with access to /config. Never installs
Python, reloads a config entry, changes HA settings, or makes HA API calls.
Use a reviewed Marketplace ZIP built for the installed backend version.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import tempfile
import zipfile

LOADER = "full-panel-loader.js"
POINTER = "full-panel-version.json"
DOMAIN = "heating_ai_legacy"


def atomic_write(path: Path, data: bytes) -> None:
    if path.is_symlink():
        raise ValueError(f"Refusing linked UI file: {path}")
    temp = None
    try:
        with tempfile.NamedTemporaryFile(dir=path.parent, prefix=".heating-ui-", delete=False) as out:
            temp = Path(out.name)
            out.write(data)
            out.flush()
            os.fsync(out.fileno())
        # Assets are served by HA; preserve permissions when replacing a file.
        temp.chmod((path.stat().st_mode & 0o777) if path.exists() else 0o644)
        os.replace(temp, path)
    finally:
        if temp is not None:
            temp.unlink(missing_ok=True)


def restore(assets: Path, backup: Path) -> None:
    meta = json.loads((backup / "backup.json").read_text(encoding="utf-8"))
    if meta["assets"] != str(assets.resolve()):
        raise ValueError("Backup belongs to a different installation")
    # Manifest first: the new loader can still use the old stable bundle.
    for name in (POINTER, LOADER):
        dest = assets / name
        if dest.is_symlink():
            raise ValueError(f"Refusing linked UI file: {dest}")
        if meta["present"][name]:
            atomic_write(dest, (backup / name).read_bytes())
        else:
            dest.unlink(missing_ok=True)


def install(config: Path, archive: Path) -> Path:
    component = config / "custom_components" / DOMAIN
    assets = component / "assets"
    installed = json.loads((component / "manifest.json").read_text(encoding="utf-8"))
    if installed.get("domain") != DOMAIN or not (assets / "full-panel.js").is_file():
        raise ValueError("Native Heating AI installation not found")
    # Strict same-version gate: a newer backend release requires a separate
    # compatibility review, not an implicit UI-only installation.
    with zipfile.ZipFile(archive) as package:
        release = json.loads(package.read("manifest.json"))
        if release.get("domain") != DOMAIN or release.get("version") != installed.get("version"):
            raise ValueError("Frontend package is not for the installed backend version")
        pointer = package.read("assets/" + POINTER)
        digest = json.loads(pointer)["digest"]
        if not isinstance(digest, str) or not re.fullmatch(r"[a-f0-9]{16}", digest):
            raise ValueError("Invalid UI digest")
        name = f"full-panel-{digest}.js"
        bundle = package.read("assets/" + name)
        if (hashlib.sha256(bundle).hexdigest()[:16] != digest
                or bundle != package.read("assets/full-panel.js")):
            raise ValueError("UI bundle integrity check failed")
        loader = package.read("assets/" + LOADER)

    # Preserve the legacy full-panel.js and every backend module. Publish a
    # new immutable asset first, then its pointer, then bootstrap the loader.
    for name_to_check in (name, POINTER, LOADER):
        if (assets / name_to_check).is_symlink():
            raise ValueError("Refusing linked UI asset")
    dest = assets / name
    if dest.exists() and dest.read_bytes() != bundle:
        raise ValueError("Existing immutable asset has different content")
    backup_root = config / ".heating-ai-ui-backups"
    backup_root.mkdir(mode=0o700, exist_ok=True)
    backup = Path(tempfile.mkdtemp(prefix="ui-", dir=backup_root))
    present = {}
    for item in (POINTER, LOADER):
        path = assets / item
        present[item] = path.is_file()
        if present[item]:
            atomic_write(backup / item, path.read_bytes())
    atomic_write(backup / "backup.json", json.dumps({
        "assets": str(assets.resolve()), "present": present,
        "backend": installed["version"], "digest": digest,
    }).encode())
    try:
        if not dest.exists():
            atomic_write(dest, bundle)
        atomic_write(assets / POINTER, pointer)
        atomic_write(assets / LOADER, loader)
        if (dest.read_bytes() != bundle or (assets / POINTER).read_bytes() != pointer
                or (assets / LOADER).read_bytes() != loader):
            raise OSError("Post-write verification failed")
    except BaseException:
        restore(assets, backup)
        raise
    return backup


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path, default=Path("/config"))
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--package", type=Path)
    group.add_argument("--rollback", type=Path)
    args = parser.parse_args()
    if args.package:
        backup = install(args.config, args.package)
        print(f"UI published; backup: {backup}")
        print("Fully refresh the HA client (clear old loader cache on first use). Do not restart HA.")
    else:
        restore(args.config / "custom_components" / DOMAIN / "assets", args.rollback)
        print("Previous UI restored. Refresh the HA client; no HA restart.")


if __name__ == "__main__":
    main()
