"""V1 manual/AUTO and outage decisions stay identical in the pure preview."""
import importlib
import sys
from pathlib import Path
from types import ModuleType
import unittest

ROOT=Path(__file__).resolve().parents[1]
PKG="native_v1_intent_fixture"


class NativeIntentTests(unittest.TestCase):
    def setUp(self):
        fixture=ModuleType(PKG)
        fixture.__path__=[str(ROOT/"custom_components"/"heating_ai_legacy")]
        sys.modules[PKG]=fixture
        self.module=importlib.import_module(PKG+".native_intent")
        sys.path.insert(0,str(ROOT/"apps"/"heating_ai"))
        self.v1=importlib.import_module("heating_model")

    def tearDown(self):
        sys.path.remove(str(ROOT/"apps"/"heating_ai"))
        for name in list(sys.modules):
            if name==PKG or name.startswith(PKG+"."):
                sys.modules.pop(name,None)
        sys.modules.pop("heating_model",None)

    def scenario(self, mode="Автоматичне керування", kws=0,
                 grid=True, armed=True, end=200.):
        safety=dict(
            legacy_mode=mode,armed=armed,grid_actual=grid,
            room_c=21.7,return_c=28.,kws_power_w=kws,
            thermostat_mode="auto",
        )
        schedule=dict(
            status="VIRTUAL_CONDITIONAL", computed_at=105.,
            heat_segments=[dict(start=100.,end=end,power_kw=3.)]
        )
        return safety,schedule

    def test_heat_requires_one_writer_even_if_v1_decision_would_heat(self):
        safety,plan=self.scenario()
        result=self.module.evaluate(110.,plan,safety,model_validated=True)
        self.assertEqual(result["proposed_action"],"heat")
        self.assertEqual(result["proposed_action"],
                         self.v1.decide("Автоматичне керування",True,True,
                                        21.7,True,"on",True,True,True,True))
        self.assertFalse(result["physical_authority"])
        self.assertFalse(result["physical_commands_enabled"])
        self.assertTrue(result["legacy_writer_stays_owner"])
        self.assertEqual(result["commands_sent"],0)

    def test_manual_auto_and_explicit_stop_never_enable_native(self):
        for mode,expected in (
            ("Штатний auto","fallback"),
            ("Спостереження","observe"),
            ("Вимкнено","release"),
        ):
            with self.subTest(mode=mode):
                safety,plan=self.scenario(mode=mode)
                result=self.module.evaluate(110.,plan,safety,model_validated=True)
                self.assertEqual(result["proposed_action"],expected)
                self.assertFalse(result["physical_authority"])

    def test_missing_meter_and_grid_unknown_remain_fallback_or_off(self):
        safety,plan=self.scenario(kws=None)
        r=self.module.evaluate(110.,plan,safety,model_validated=True)
        self.assertEqual(r["proposed_action"],"fallback")
        safety,plan=self.scenario(grid=None)
        r=self.module.evaluate(110.,plan,safety,model_validated=True)
        self.assertEqual(r["proposed_action"],"off")
        self.assertFalse(r["physical_commands_enabled"])

    def test_missing_or_malformed_plan_not_treated_as_demand(self):
        safety,plan=self.scenario()
        self.assertEqual(
            self.module.evaluate(110.,None,safety,model_validated=True)["proposed_action"],
            "fallback",
        )
        plan["heat_segments"][0]["power_kw"]=float("nan")
        self.assertEqual(
            self.module.evaluate(110.,plan,safety,model_validated=True)["proposed_action"],
            "fallback",
        )
        plan["heat_segments"][0]["power_kw"]=3.
        self.assertEqual(
            self.module.evaluate(210.,plan,safety,model_validated=True)["proposed_action"],
            "off",
        )
        plan["computed_at"]=-10000.
        self.assertEqual(
            self.module.evaluate(210.,plan,safety,model_validated=True)["proposed_action"],
            "fallback",
        )


if __name__=="__main__":
    unittest.main()
