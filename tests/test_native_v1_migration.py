"""Migration dry-run may read V1 originals, but must not alter any legacy bytes."""
import importlib
import json
from pathlib import Path
from tempfile import TemporaryDirectory
from types import ModuleType
import sys
import unittest

PKG="isolated_heating_v1_migration"
ROOT=Path(__file__).resolve().parents[1]/"custom_components"/"heating_ai_legacy"


class MigrationTests(unittest.TestCase):
    def setUp(self):
        package=ModuleType(PKG)
        package.__path__=[str(ROOT)]
        sys.modules[PKG]=package
        self.migration=importlib.import_module(PKG+".native_migration")

    def tearDown(self):
        for key in tuple(sys.modules):
            if key==PKG or key.startswith(PKG+"."):
                sys.modules.pop(key,None)

    @staticmethod
    def original():
        return json.dumps({
            "model":{
                "floor_air":.03,"room_loss":.01,"floor_decay":.22,
                "heat_gain":1.03,"fire_gain":.025,"version":7,
                "validated":True
            },
            "energy_last":133.25,
            "energy_bins":[{"ts":1,"kwh":3.7}],
            "weather":[{"ts":1,"temperature":5.0}],
            "journal":[{"reason":"manual mode"}],
            "floor_observer":{"estimate":28.1},
            "solar_observer":{"coefficient":.1},
            "ventilation_model":{"recuperator":.03},
            "cycle_energy":{"2026-11-12":{"kwh":2.0}},
            "plan_reference":{"plan_id":"old"},
            "once":{"armed_at":12},
            "last_command":"heat",
        },allow_nan=False,sort_keys=True).encode()

    def test_dry_run_original_bytes_remain_identical(self):
        with TemporaryDirectory() as directory:
            source=Path(directory)/"source"
            source.mkdir()
            original=source/"state.json"
            raw=self.original()
            original.write_bytes(raw)
            stat=original.stat()
            loaded=self.migration.read_original(original,allowed_root=source)
            self.assertEqual(loaded,raw)
            self.assertEqual(original.read_bytes(),raw)
            self.assertEqual(original.stat().st_mtime_ns,stat.st_mtime_ns)
            report=self.migration.preview(loaded)
            self.assertEqual(report["model_version"],7)
            self.assertEqual(report["energy_cursor"],133.25)
            self.assertEqual(report["energy_bin_count"],1)
            self.assertTrue(report["legacy_controller_stays_owner"])
            self.assertTrue(report["manual_resume_forced_off"])
            self.assertTrue(report["one_shot_forced_off"])
            self.assertFalse(report["native_physical_commands_enabled"])
            self.assertFalse((source/"v1-original-state.json").exists())

    def test_explicit_hash_checked_opaque_backup_and_no_rewrite(self):
        with TemporaryDirectory() as directory:
            dest=Path(directory)/"native"
            dest.mkdir()
            raw=self.original()
            sha=self.migration.preview(raw)["sha256"]
            with self.assertRaises(PermissionError):
                self.migration.archive_original(
                    raw,dest,expected_sha256=sha,consent=False)
            with self.assertRaises(ValueError):
                self.migration.archive_original(
                    raw,dest,expected_sha256="0"*64,consent=True)
            self.assertEqual(list(dest.iterdir()),[])
            report=self.migration.archive_original(
                raw,dest,expected_sha256=sha,consent=True)
            self.assertTrue(report["archived"])
            self.assertFalse(report["native_energy_cursor_changed"])
            self.assertFalse(report["native_model_promoted"])
            self.assertEqual((dest/"v1-original-state.json").read_bytes(),raw)
            with self.assertRaises(FileExistsError):
                self.migration.archive_original(
                    raw,dest,expected_sha256=sha,consent=True)

    def test_bad_state_and_symlink_refused_without_write(self):
        with TemporaryDirectory() as directory:
            source=Path(directory)/"source"
            source.mkdir()
            target=source/"state.json"
            target.write_bytes(b"{corrupt")
            with self.assertRaises(ValueError):
                self.migration.read_original(target,allowed_root=source)
            target.unlink()
            raw=self.original()
            (Path(directory)/"other.json").write_bytes(raw)
            target.symlink_to(Path(directory)/"other.json")
            with self.assertRaises(ValueError):
                self.migration.read_original(target,allowed_root=source)
            with self.assertRaises(ValueError):
                self.migration.preview(json.dumps({
                    "model":{"version":1,"validated":False}
                }).encode())
            data=json.loads(raw)
            data["model"]["floor_air"]=float("nan")
            with self.assertRaises(ValueError):
                self.migration.preview(json.dumps(data).encode())


if __name__=="__main__":
    unittest.main()
