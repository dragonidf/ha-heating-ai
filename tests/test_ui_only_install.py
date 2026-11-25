"""Exercise the UI-only publisher in an isolated fake HA config."""
import hashlib
import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch
import zipfile

from tools.install_ui_only import DOMAIN, LOADER, POINTER, install, restore


class UiOnlyInstallTests(unittest.TestCase):
    def setUp(self):
        self.temp = TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.config = Path(self.temp.name)
        self.component = self.config / "custom_components" / DOMAIN
        self.assets = self.component / "assets"
        self.assets.mkdir(parents=True)
        self.manifest = json.dumps({"domain": DOMAIN, "version": "0.5.21"}).encode()
        (self.component / "manifest.json").write_bytes(self.manifest)
        (self.component / "production.py").write_bytes(b"unchanged controller")
        (self.assets / "full-panel.js").write_bytes(b"original bundle")
        (self.assets / LOADER).write_bytes(b"original loader")

    def package(self, bundle=b"new tested UI", version="0.5.21", corrupt=False):
        path = self.config / "candidate.zip"
        digest = hashlib.sha256(bundle).hexdigest()[:16]
        with zipfile.ZipFile(path, "w") as archive:
            archive.writestr("manifest.json", json.dumps({"domain": DOMAIN, "version": version}))
            archive.writestr("assets/full-panel.js", bundle)
            archive.writestr("assets/full-panel-" + digest + ".js", b"wrong" if corrupt else bundle)
            archive.writestr("assets/" + LOADER, b"new loader")
            archive.writestr("assets/" + POINTER, json.dumps({"digest": digest}))
        return path, digest

    def test_install_update_and_rollback_preserve_backend_and_legacy_asset(self):
        path, digest = self.package()
        backup = install(self.config, path)
        self.assertEqual((self.assets / ("full-panel-" + digest + ".js")).read_bytes(), b"new tested UI")
        self.assertEqual((self.assets / LOADER).read_bytes(), b"new loader")
        self.assertEqual(json.loads((self.assets / POINTER).read_bytes())["digest"], digest)
        path, next_digest = self.package(b"another UI")
        second = install(self.config, path)
        self.assertNotEqual(digest, next_digest)
        restore(self.assets, second)
        self.assertEqual(json.loads((self.assets / POINTER).read_bytes())["digest"], digest)
        restore(self.assets, backup)
        self.assertFalse((self.assets / POINTER).exists())
        self.assertEqual((self.assets / LOADER).read_bytes(), b"original loader")
        self.assertEqual((self.assets / "full-panel.js").read_bytes(), b"original bundle")
        self.assertEqual((self.component / "manifest.json").read_bytes(), self.manifest)
        self.assertEqual((self.component / "production.py").read_bytes(), b"unchanged controller")

    def test_reject_mismatch_and_corruption_without_mutation(self):
        for path, _ in (self.package(version="0.5.22"),):
            with self.assertRaisesRegex(ValueError, "backend version"):
                install(self.config, path)
        path, _ = self.package(corrupt=True)
        with self.assertRaisesRegex(ValueError, "integrity"):
            install(self.config, path)
        self.assertEqual((self.assets / LOADER).read_bytes(), b"original loader")
        self.assertFalse((self.assets / POINTER).exists())

    def test_partial_publication_restores_original_pointer_and_loader(self):
        from tools.install_ui_only import atomic_write
        path, _ = self.package()
        def fail_loader_once(dest, data):
            if dest == self.assets / LOADER and data == b"new loader":
                raise OSError("simulated write failure")
            return atomic_write(dest, data)
        with patch("tools.install_ui_only.atomic_write", side_effect=fail_loader_once):
            with self.assertRaisesRegex(OSError, "simulated"):
                install(self.config, path)
        self.assertFalse((self.assets / POINTER).exists())
        self.assertEqual((self.assets / LOADER).read_bytes(), b"original loader")
        self.assertEqual((self.assets / "full-panel.js").read_bytes(), b"original bundle")
