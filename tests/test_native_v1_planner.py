"""Native V1 planner is a read-only numerical function with bounded sources."""
from datetime import datetime, timedelta
from importlib import import_module
from types import ModuleType
from pathlib import Path
import sys
from tempfile import TemporaryDirectory
import unittest
import zipfile

from tools.build_marketplace import build


class NativeV1PlannerTests(unittest.TestCase):
    def test_full_24h_optimizer_and_missing_weather_fail_closed(self):
        with TemporaryDirectory() as directory:
            path = Path(directory) / "review.zip"
            build(path)
            with zipfile.ZipFile(path) as archive:
                archive.extractall(directory)
            # Read a ZIP-root integration as a synthetic Python package,
            # without loading Home Assistant or the runtime __init__.py.
            package = ModuleType("reviewed_heating_v1")
            package.__path__ = [directory]
            sys.modules["reviewed_heating_v1"] = package
            try:
                native = import_module("reviewed_heating_v1.native_planner")
                engine = import_module("reviewed_heating_v1.native_engine.heating_model")
                from zoneinfo import ZoneInfo
                now = datetime(2026, 10, 9, 4, 30, tzinfo=ZoneInfo("Europe/Kyiv"))
                snapshot = {
                    "at": now.isoformat(),
                    "room": 21.8,
                    "floor": 24.0,
                    "outside": 5.0,
                    "fire_delta": 0.0,
                    "assumed_kw": 3.0,
                    "model": {key: engine.DEFAULT_MODEL[key] for key in (
                        "floor_air", "room_loss", "floor_decay", "heat_gain",
                        "fire_gain",
                    )},
                    "weather": [
                        {"ts": (now + timedelta(hours=i)).timestamp(),
                         "temperature": 5.0}
                        for i in range(-1, 29)
                    ],
                    "settings": {
                        "target": 22.0, "lower": 21.5, "upper": 22.5,
                        "night_target": 21.0,
                        "night_lower": 20.5, "night_upper": 21.5,
                        "start": "23:00", "end": "07:00",
                        "sleep_start": "23:00", "sleep_end": "06:30",
                        "strategy": "Тільки в дозволені години",
                        "outage_schedule": {
                            "mode": "unknown", "valid": False, "outages": [],
                        },
                    },
                }
                result = native.provisional_plan(snapshot)
                self.assertEqual(result["status"], "VIRTUAL_CONDITIONAL")
                self.assertEqual(result["quality"], "PROVISIONAL_SOURCES")
                self.assertFalse(result["physical_commands_enabled"])
                self.assertFalse(result["proposal_is_command"])
                self.assertEqual(result["commands_sent"], 0)
                self.assertEqual(result["source"], "native_v1_optimizer")
                self.assertEqual(result["horizon_hours"], 24)
                self.assertGreater(len(result["forecast"]), 24)
                self.assertEqual(
                    result["reason"], "schedule_coverage_unverified")
                sources = import_module("reviewed_heating_v1.native_sources")
                hourlies = [
                    {"datetime": (now + timedelta(hours=i)).isoformat(),
                     "temperature": 5.0}
                    for i in range(-1, 29)
                ]
                converted = sources.normalize_hourly_weather(
                    hourlies, now.timestamp()
                )
                self.assertEqual(len(converted), 30)
                for mode, value in (
                    ("fixed_3", 3.), ("fixed_6", 6.), ("fixed_9", 9.)
                ):
                    self.assertEqual(sources.nominal_kw(mode), value)
                for invalid in (None, "unconfirmed", "auto", "fixed_12"):
                    with self.assertRaises(ValueError):
                        sources.nominal_kw(invalid)
                self.assertEqual(converted[0]["temperature"], 5.0)
                with self.assertRaises(ValueError):
                    sources.normalize_hourly_weather(
                        [dict(hourlies[0], temperature=float("nan"))]
                        + hourlies[1:], now.timestamp()
                    )
                no_weather = native.provisional_plan(
                    dict(snapshot, weather=[]))
                self.assertEqual(no_weather["status"], "BLOCKED_INPUTS")
                self.assertEqual(no_weather["commands_sent"], 0)
                invalid_target = dict(
                    snapshot, settings=dict(snapshot["settings"], target=35)
                )
                self.assertEqual(
                    native.provisional_plan(invalid_target)["status"],
                    "BLOCKED_INPUTS")
                malformed = dict(
                    snapshot, weather=[dict(snapshot["weather"][0],
                                            temperature=float("nan"))]
                    + snapshot["weather"][1:]
                )
                self.assertEqual(
                    native.provisional_plan(malformed)["status"],
                    "BLOCKED_INPUTS")
            finally:
                for name in list(sys.modules):
                    if name == "reviewed_heating_v1" or name.startswith("reviewed_heating_v1."):
                        sys.modules.pop(name, None)


if __name__ == "__main__":
    unittest.main()
