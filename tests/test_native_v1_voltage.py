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
        status=guard.evaluate(1191.,True,phases,
                              minimum_v=190.,maximum_v=250.)
        self.assertEqual(status["status"],"STABLE")
        self.assertTrue(status["voltage_qualified"])
        self.assertFalse(status["physical_commands_enabled"])

    def test_freshness_loss_voltage_excursion_and_policy_change_latch(self):
        guard=self.module.VoltageGuard()
        kw=dict(minimum_v=190.,maximum_v=250.,recovery_seconds=180.)
        self.assertEqual(guard.evaluate(1000.,True,[230.,230.,230.],**kw)["status"],"RECOVERING")
        self.assertEqual(guard.evaluate(1190.,True,[230.,230.,230.],**kw)["status"],"STABLE")
        self.assertEqual(guard.evaluate(1200.,True,[260.,230.,230.],**kw)["status"],"VOLTAGE_OUT_OF_RANGE")
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


if __name__=="__main__":
    unittest.main()
