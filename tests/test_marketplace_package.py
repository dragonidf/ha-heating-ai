"""Marketplace package remains an inert, byte-identical V1 distribution."""
import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
import zipfile

from tools.build_marketplace import build, EXPECTED, ROOT


class MarketplaceCarrierTests(unittest.TestCase):
    def test_package_exact_legacy_source_without_activation(self):
        with TemporaryDirectory() as directory:
            path = Path(directory) / "heating_ai_v1_marketplace.zip"
            result = build(path)
            self.assertEqual(result["files"], 12)
            with zipfile.ZipFile(path) as archive:
                names = set(archive.namelist())
                self.assertEqual(
                    names, {"__init__.py", "manifest.json",
                            "legacy_app/release.json",
                            *(f"legacy_app/{name}" for name in EXPECTED)}
                )
                recorded = json.loads(archive.read("legacy_app/release.json"))
                self.assertEqual(recorded["version"], "0.3.23")
                self.assertEqual(recorded["version"], (ROOT / "VERSION").read_text().strip())
                self.assertNotIn("config.yaml", names)
                self.assertFalse(any(name.endswith(".yaml") for name in names))
                self.assertNotIn(b"call_service", archive.read("__init__.py"))

    def test_repeated_archives_have_identical_digest(self):
        with TemporaryDirectory() as directory:
            first = build(Path(directory) / "1.zip")
            second = build(Path(directory) / "2.zip")
            self.assertEqual(first["sha256"], second["sha256"])


if __name__ == "__main__":
    unittest.main()
