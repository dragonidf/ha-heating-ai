"""Safety, immutable model, and durable journal work without real HA/devices."""
import importlib
from datetime import datetime, timezone, timedelta
from pathlib import Path
import sys
from tempfile import TemporaryDirectory
from types import ModuleType, SimpleNamespace
import unittest

PKG = "heating_v1_safety_fixture"
ROOT = Path(__file__).resolve().parents[1] / "custom_components" / "heating_ai_legacy"


class GuardAndPersistenceTests(unittest.TestCase):
    def setUp(self):
        module = ModuleType(PKG)
        module.__path__ = [str(ROOT)]
        sys.modules[PKG] = module
        self.safety = importlib.import_module(PKG + ".native_safety")
        self.persist = importlib.import_module(PKG + ".native_persistence")
        self.model = importlib.import_module(PKG + ".native_model")

    def tearDown(self):
        for name in list(sys.modules):
            if name == PKG or name.startswith(PKG + "."):
                sys.modules.pop(name, None)

    def states(self, *, manual=True, kws_ok=True):
        now = datetime.now(timezone.utc)
        def row(value, age=1):
            return SimpleNamespace(
                state=str(value), last_reported=now-timedelta(seconds=age),
                attributes={},
            )
        values = {
            "sensor.room": row(21.3),
            "sensor.return": row(26.1),
            "sensor.outside": row(3.4),
            "input_select.mode": row("Штатний auto" if manual else "Автоматичне керування"),
            "input_boolean.armed": row("on"),
            "input_select.grid": row("Наявна мережа"),
            "climate.kotel": row("auto"),
            "sensor.heating_controller": row("Тимчасовий auto"),
            "sensor.fireplace": row("Неактивний"),
            "fan.circulation": row("off"),
        }
        for i in range(1, 4):
            values["sensor.kws_"+str(i)] = row(0 if kws_ok else "unavailable")
        values["sensor.kws_energy"] = row(100.0 if kws_ok else "unavailable")
        mapping = dict(room="sensor.room", **{
            "return": "sensor.return", "outside": "sensor.outside",
            "mode": "input_select.mode", "armed": "input_boolean.armed",
            "fan": "fan.circulation",
        })
        source = dict(
            grid="input_select.grid", thermostat="climate.kotel",
            fireplace="sensor.fireplace",
            kws_l1="sensor.kws_1", kws_l2="sensor.kws_2",
            kws_l3="sensor.kws_3", kws_energy="sensor.kws_energy",
        )
        return SimpleNamespace(states=SimpleNamespace(get=values.get)),mapping,source

    def test_manual_auto_stays_non_actuating_with_zero_watts(self):
        hass,mapping,sources = self.states()
        result = self.safety.observe(hass,mapping,sources)
        self.assertTrue(result["manual_observed"])
        self.assertFalse(result["physical_authority"])
        self.assertFalse(result["physical_commands_enabled"])
        self.assertIn("explicit_manual_mode", result["source_warnings"])
        self.assertEqual(result["kws_power_w"], 0)
        self.assertEqual(result["kws_total_kwh"], 100.0)
        self.assertIn("legacy_controller_active", result["source_warnings"])

    def test_missing_kws_does_not_become_zero(self):
        hass,mapping,sources = self.states(manual=False,kws_ok=False)
        result = self.safety.observe(hass,mapping,sources)
        self.assertIsNone(result["kws_power_w"])
        self.assertIsNone(result["kws_total_kwh"])
        self.assertIn("kws_phase_missing_or_stale", result["source_warnings"])
        self.assertFalse(result["physical_commands_enabled"])

    def test_fireplace_hysteresis_manual_stop_and_unverified_backup(self):
        fan = importlib.import_module(PKG + ".native_fan")
        t=1234567890.
        initial=fan.project(None,t,5.5,mode="Авто",enabled=True,
             grid=True,fan_readable=True,standard=38,night_limit=28,
             intense=65,night_window=False)
        self.assertTrue(initial.request)
        self.assertEqual(initial.percent,38)
        self.assertEqual(initial.source,"fireplace")
        self.assertFalse(initial.physical_commands_enabled)
        stopped=fan.project(initial.state,t+60,11.0,mode="Зупинити",
             enabled=True,grid=True,fan_readable=True,standard=38,
             night_limit=28,intense=65,night_window=False)
        self.assertFalse(stopped.request)
        self.assertIsNone(stopped.percent)
        blackout=fan.project(initial.state,t+60,11.0,mode="Авто",
             enabled=True,grid=None,fan_readable=True,standard=38,
             night_limit=28,intense=65,night_window=False)
        self.assertTrue(blackout.request)
        self.assertIsNone(blackout.percent)
        self.assertEqual(blackout.reason,"fan_supply_unknown")
        cold=fan.project(initial.state,t+60,2.0,mode="Авто",
             enabled=True,grid=True,fan_readable=True,standard=38,
             night_limit=28,intense=65,night_window=False)
        self.assertFalse(cold.state.burning)
        self.assertFalse(cold.request)

    def test_private_model_sha256_and_reject_downgrade(self):
        with TemporaryDirectory() as folder:
            coeff = dict(floor_air=.03,room_loss=.01,floor_decay=.20,
                         heat_gain=1.,fire_gain=.025,version=6,validated="true")
            saved = self.model.remember(folder,coeff,1234567890.)
            self.assertEqual(saved["model"]["version"],6)
            self.assertEqual(self.model.load(folder),saved)
            same = self.model.remember(folder,coeff,1234567895.)
            self.assertEqual(same,saved)
            with self.assertRaises(ValueError):
                self.model.remember(folder,dict(coeff,version=5),1234567896.)
            file=Path(folder)/self.model.FILENAME
            file.write_text(file.read_text()+"bad")
            with self.assertRaises(ValueError):
                self.model.load(folder)

    def test_hardware_kws_only_energy_and_gap_reconciliation(self):
        with TemporaryDirectory() as folder:
            store, _ledger, _status = self.persist.bootstrap(
                Path(folder)/"private"
            )
            # A real cumulative hardware kWh cursor, never kW-time integral.
            self.persist.observe_room(
                store,1234567890.,21.3,
                counter_kwh=100.,boiler_power_w=3000.,
            )
            self.assertEqual(store.status()["counter"],100.)
            self.persist.observe_room(
                store,1234568790.,21.4,
                counter_kwh=100.5,boiler_power_w=0.,
            )
            self.assertEqual(store.status()["counter"],100.5)
            self.persist.observe_room(
                store,1234569690.,21.4,
                counter_kwh=None,boiler_power_w=None,
            )
            self.assertTrue(store.status()["counter_gap"])
            self.persist.observe_room(
                store,1234570590.,21.5,
                counter_kwh=101.5,boiler_power_w=3000.,
            )
            recovered=store.status()
            self.assertEqual(recovered["counter"],101.5)
            self.assertFalse(recovered["counter_gap"])
            history=store.history(
                1234567890.,1234570590.,1234570590.
            )
            self.assertAlmostEqual(
                sum(row["kwh"] for row in history["energy"]),1.5,
            )
            self.assertTrue(any(
                row["category"]=="recovered"
                for row in history["energy"]
            ))
            # A meter reset must not become negative kWh.
            self.persist.observe_room(
                store,1234571490.,21.5,
                counter_kwh=0.,boiler_power_w=0.,
            )
            self.assertIsNotNone(store.status()["energy_fault"])

    def test_reboots_fence_durable_journal_and_keep_counter_unknown(self):
        with TemporaryDirectory() as folder:
            store,ledger,a = self.persist.bootstrap(Path(folder)/"private")
            self.assertTrue(a["fenced"])
            self.assertFalse(a["physical_commands_enabled"])
            self.persist.observe_room(store,1234567890.,21.3)
            first=store.status()
            self.assertIsNone(first["counter"])
            self.assertTrue(first["counter_gap"])
            store2,ledger2,b = self.persist.bootstrap(Path(folder)/"private")
            self.assertTrue(b["fenced"])
            self.assertGreater(b["owner_epoch"], a["owner_epoch"])
            self.assertFalse(b["physical_commands_enabled"])
            self.assertIsNone(store2.status()["counter"])


if __name__=="__main__":
    unittest.main()
