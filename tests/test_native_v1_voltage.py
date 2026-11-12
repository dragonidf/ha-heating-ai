"""3-phase voltage never overrides independent grid and a recovery latch."""
import importlib
from pathlib import Path
import sys
from types import ModuleType
import unittest

ROOT=Path(__file__).resolve().parents[1]/"custom_components"/"heating_ai_legacy"
PKG="native_v1_voltage_fixture"


class VoltageTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        module=ModuleType(PKG)
        module.__path__=[str(ROOT)]
        sys.modules[PKG]=module
        cls.module=importlib.import_module(PKG+".native_voltage")

    @classmethod
    def tearDownClass(cls):
        for name in tuple(sys.modules):
            if name==PKG or name.startswith(PKG+"."):
                sys.modules.pop(name,None)

    def test_outage_displayed_voltages_are_not_live_grid(self):
        guard=self.module.VoltageGuard()
        phases=[229.,230.,223.]
        status=guard.evaluate(1000.,False,phases,
                              minimum_v=190.,maximum_v=250.)
        self.assertEqual(status["status"],"GRID_OFF_OR_UNKNOWN")
        self.assertFalse(status["voltage_qualified"])
        status=guard.evaluate(1010.,True,phases,
                              minimum_v=190.,maximum_v=250.)
        self.assertEqual(status["status"],"RECOVERING")
        self.assertFalse(status["voltage_qualified"])
        guard.evaluate(1070.,True,phases,minimum_v=190.,maximum_v=250.)
        guard.evaluate(1130.,True,phases,minimum_v=190.,maximum_v=250.)
        status=guard.evaluate(1190.,True,phases,
                              minimum_v=190.,maximum_v=250.)
        self.assertEqual(status["status"],"NORMAL")
        self.assertTrue(status["voltage_qualified"])
        self.assertFalse(status["physical_commands_enabled"])

    def test_freshness_loss_voltage_excursion_and_policy_change_latch(self):
        guard=self.module.VoltageGuard()
        kw=dict(minimum_v=190.,maximum_v=250.,recovery_seconds=180.)
        self.assertEqual(guard.evaluate(1000.,True,[230.,230.,230.],**kw)["status"],"RECOVERING")
        guard.evaluate(1060.,True,[230.,230.,230.],**kw)
        guard.evaluate(1120.,True,[230.,230.,230.],**kw)
        self.assertEqual(guard.evaluate(1180.,True,[230.,230.,230.],**kw)["status"],"NORMAL")
        self.assertEqual(guard.evaluate(1200.,True,[260.,230.,230.],**kw)["status"],"HIGH_VOLTAGE")
        self.assertEqual(guard.evaluate(1210.,True,[230.,230.,230.],**kw)["status"],"RECOVERING")
        self.assertEqual(guard.evaluate(1220.,True,[None,230.,230.],**kw)["status"],"VOLTAGE_SENSOR_UNAVAILABLE")
        self.assertEqual(guard.evaluate(1230.,True,[230.,230.,230.],**kw)["status"],"RECOVERING")
        self.assertEqual(guard.evaluate(1231.,True,[230.,230.,230.],
                                        minimum_v=None,maximum_v=None)["status"],"LIMITS_UNCONFIGURED")

    def test_bogus_settings_do_not_open_gate(self):
        for low,high in ((None,None),(230.,225.),(100.,400.)):
            with self.subTest(low=low,high=high):
                result=self.module.VoltageGuard().evaluate(
                    1000.,True,[230.,230.,230.],
                    minimum_v=low,maximum_v=high,
                )
                self.assertFalse(result["voltage_qualified"])
                self.assertFalse(result["physical_authority"])

    def test_operator_voltage_band_validation(self):
        valid=self.module.valid_policy
        self.assertTrue(valid(207,253,180))
        self.assertFalse(valid(253,207,180))
        self.assertFalse(valid(140,144,180))
        self.assertFalse(valid(True,253,180))
        self.assertFalse(valid(190,250,29))
        self.assertFalse(valid(190,250,901))

    def test_early_trip_and_separate_recovery_band_on_all_phases(self):
        guard=self.module.VoltageGuard()
        kw=dict(minimum_v=190.,maximum_v=260.,recovery_seconds=30.)
        self.assertEqual(guard.evaluate(1000.,True,[196.,230.,230.],**kw)["status"],"LOW_VOLTAGE")
        self.assertEqual(guard.evaluate(1010.,True,[197.,230.,230.],**kw)["status"],"RECOVERY_HYSTERESIS")
        self.assertEqual(guard.evaluate(1020.,True,[201.,230.,230.],**kw)["status"],"RECOVERING")
        self.assertFalse(guard.evaluate(1049.,True,[201.,230.,230.],**kw)["voltage_qualified"])
        self.assertEqual(guard.evaluate(1050.,True,[201.,230.,230.],**kw)["status"],"NORMAL")
        self.assertEqual(guard.evaluate(1060.,True,[255.,230.,230.],**kw)["status"],"HIGH_VOLTAGE")
        self.assertEqual(guard.evaluate(1070.,True,[254.,230.,230.],**kw)["status"],"RECOVERY_HYSTERESIS")
        self.assertEqual(guard.evaluate(1080.,True,[250.,230.,230.],**kw)["status"],"RECOVERING")
        self.assertEqual(guard.evaluate(1110.,True,[250.,230.,230.],**kw)["status"],"NORMAL")

    def test_reason_identifies_phase_and_kws_threshold(self):
        result=self.module.VoltageGuard().evaluate(
            1000.,True,[230.,196.,230.],minimum_v=190.,maximum_v=260.,
        )
        self.assertEqual(result["affected_phases"],["L2"])
        self.assertIn("L2",result["reason"])
        self.assertIn("190 В",result["reason"])
        self.assertEqual(result["trip_low_v"],196.)
        self.assertFalse(result["physical_commands_enabled"])

    def test_long_sampling_gap_restarts_continuous_recovery_timer(self):
        guard=self.module.VoltageGuard()
        kw=dict(minimum_v=190.,maximum_v=260.,recovery_seconds=30.)
        guard.evaluate(1000.,True,[230.,230.,230.],**kw)
        result=guard.evaluate(1100.,True,[230.,230.,230.],**kw)
        self.assertEqual(result["status"],"RECOVERING")
        self.assertFalse(result["voltage_qualified"])
        self.assertEqual(guard.evaluate(1130.,True,[230.,230.,230.],**kw)["status"],"NORMAL")


if __name__=="__main__":
    unittest.main()
