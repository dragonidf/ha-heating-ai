"""Prove native Marketplace candidate retains exact V1 thermal algorithms.

These tests are offline, never contact production HA, and cannot actuate
a thermostat or fan.
"""
import hashlib
import importlib
import json
from pathlib import Path
import sys
from tempfile import TemporaryDirectory
import unittest
import zipfile

from tools.build_marketplace import (
    build, EXPECTED, PURE_MODULES, ROOT, verify_native_module,
)


class MarketplaceCarrierTests(unittest.TestCase):
    def test_package_exact_legacy_source_without_activation(self):
        with TemporaryDirectory() as directory:
            path = Path(directory) / "heating_ai_v1_marketplace.zip"
            result = build(path)
            self.assertEqual(result["files"], 35)
            with zipfile.ZipFile(path) as archive:
                names = set(archive.namelist())
                wanted = {
                    "__init__.py", "manifest.json", "legacy_app/release.json",
                    "native_engine/__init__.py", "const.py", "config_flow.py",
                    "native_runtime.py", "plan_snapshot.py", "native_planner.py", "native_sources.py", "native_api.py", "native_safety.py",
                    "native_store.py", "native_persistence.py", "native_model.py", "native_fan.py", "native_fan_sources.py",
                    "critical_ledger.py", "sensor.py",
                    "strings.json", "assets/panel.js",
                    *(f"legacy_app/{name}" for name in EXPECTED),
                    *(f"native_engine/{name}" for name in PURE_MODULES),
                }
                self.assertEqual(names, wanted)
                recorded = json.loads(archive.read("legacy_app/release.json"))
                self.assertEqual(recorded["version"], "0.3.23")
                self.assertEqual(recorded["version"], (ROOT / "VERSION").read_text().strip())
                for name in EXPECTED:
                    content = archive.read(f"legacy_app/{name}")
                    self.assertEqual(hashlib.sha256(content).hexdigest(),
                                     recorded["module_hashes"][name])
                for name in PURE_MODULES:
                    verify_native_module(
                        archive.read(f"native_engine/{name}"),
                        archive.read(f"legacy_app/{name}"),
                    )
                self.assertNotIn("config.yaml", names)
                self.assertFalse(any(name.endswith(".yaml") for name in names))
                self.assertNotIn(b"call_service", archive.read("__init__.py"))
                panel = archive.read("assets/panel.js")
                self.assertIn("Температура".encode("utf-8"), panel)
                self.assertIn(b"callApi", panel)
                self.assertNotIn(b"callService", panel)
                self.assertNotIn(b"physical_commands_enabled: true", panel)

    def test_model_produces_identical_results_in_native_package(self):
        with TemporaryDirectory() as directory:
            path = Path(directory) / "review.zip"
            build(path)
            with zipfile.ZipFile(path) as archive:
                archive.extractall(directory)
            v1source = str(ROOT / "apps" / "heating_ai")
            sys.path.insert(0, directory)
            sys.path.insert(0, v1source)
            try:
                legacy = importlib.import_module("heating_model")
                native = importlib.import_module("native_engine.heating_model")
                model = dict(
                    floor_air=.03, room_loss=.01, floor_decay=.12,
                    heat_gain=.5, fire_gain=.025,
                )
                for room, floor, outside, watts, fire in [
                    (21.5, 26.0, 4.0, 0.0, 0.0),
                    (21.8, 27.5, 1.0, 6.0, 0.0),
                    (22.0, 28.0, -3.0, 0.0, 16.0),
                ]:
                    args = (room, floor, outside, watts, fire, model, 1/12)
                    self.assertEqual(legacy.step(*args), native.step(*args))
                    coast_args = (room, floor, outside, fire, model, 3)
                    self.assertEqual(legacy.coast(*coast_args), native.coast(*coast_args))
                for power in (None, 0, 50, 2999, 3000, 6000, 9000):
                    for previous in ("off", "3 кВт", "6 кВт", "9 кВт"):
                        self.assertEqual(legacy.nominal_mode(power, previous),
                                         native.nominal_mode(power, previous))
                # Full 24h heating optimizer parity, not just a 3h coast.
                # Fixed synthetic data make this independent of production.
                from datetime import datetime
                from zoneinfo import ZoneInfo
                from copy import deepcopy
                moment = datetime(2026, 10, 9, 4, 30, tzinfo=ZoneInfo("Europe/Kyiv"))
                base_settings = dict(
                    start="06:00", end="07:00",
                    strategy="Тільки в дозволені години",
                    target=22, lower=21.5, upper=22.5,
                    night_price=.5, day_price=1,
                )
                for scenario in ("ordinary", "scheduled_outage"):
                    settings = deepcopy(base_settings)
                    if scenario == "scheduled_outage":
                        electricity = ["on"] * 48
                        electricity[12:14] = ["off", "off"]
                        record = {
                            "state": "off",
                            "attributes": {
                                "updated": moment.isoformat(),
                                "queue": "3.1",
                                "region": "kiivska-oblast",
                                "today_48half": electricity,
                            },
                        }
                        legacy_schedule = importlib.import_module("heating_outages").schedule
                        settings["outage_schedule"] = legacy_schedule(record, moment)
                    arguments = (moment, 21.8, 24.0, 10.0, 0.0, model, [],
                                 settings)
                    old = legacy.plan(*arguments)
                    new = native.plan(*arguments[:-1], deepcopy(settings))
                    for key in (
                        "curve", "heat_segments", "energy",
                        "minimum", "maximum", "horizon_hours",
                        "outage_shifted", "weather_complete",
                    ):
                        self.assertEqual(
                            old.get(key), new.get(key),
                            (scenario, key),
                        )
            finally:
                sys.path.remove(v1source)
                sys.path.remove(directory)
                for name in list(sys.modules):
                    if name == "native_engine" or name.startswith("native_engine."):
                        sys.modules.pop(name, None)

    def test_repeated_archives_have_identical_digest(self):
        with TemporaryDirectory() as directory:
            first = build(Path(directory) / "1.zip")
            second = build(Path(directory) / "2.zip")
            self.assertEqual(first["sha256"], second["sha256"])


if __name__ == "__main__":
    unittest.main()
