"""Production command evidence is fenced across crashes and bounded safely."""
import importlib
from pathlib import Path
import sys
from tempfile import TemporaryDirectory
from types import ModuleType
import unittest


ROOT=Path(__file__).resolve().parents[1]/"custom_components"/"heating_ai_legacy"
PKG="critical_ledger_fixture"


class CriticalLedgerTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        package=ModuleType(PKG)
        package.__path__=[str(ROOT)]
        sys.modules[PKG]=package
        cls.module=importlib.import_module(PKG+".critical_ledger")

    @classmethod
    def tearDownClass(cls):
        for name in tuple(sys.modules):
            if name==PKG or name.startswith(PKG+"."):
                sys.modules.pop(name,None)

    def test_restart_marks_sent_heat_unknown_until_confirmed_stop(self):
        with TemporaryDirectory() as folder:
            root=Path(folder)
            ledger=self.module.CriticalLedger(root,owner_id="owner",boot_id="boot-1",owner_epoch=0)
            prepared=ledger.prepare("heat-1","HEAT",{"service":"climate/set_hvac_mode"},
                expected_revision=0,owner_id="owner",boot_id="boot-1",owner_epoch=0,at=1.)
            revision=ledger.record_sent("heat-1",at=2.,expected_revision=prepared["revision"],
                owner_id="owner",boot_id="boot-1",owner_epoch=0)
            self.assertEqual(revision,2)

            reopened=self.module.CriticalLedger(root)
            crashed=reopened.status()
            self.assertEqual(crashed["commands"]["heat-1"]["stage"],"UNKNOWN")
            self.assertTrue(crashed["fenced"])
            claimed=reopened.claim_boot("boot-2",expected_owner_id="owner")
            with self.assertRaises(self.module.LedgerConflict):
                reopened.prepare("heat-2","HEAT",{},expected_revision=claimed["revision"],
                    owner_id="owner",boot_id="boot-2",owner_epoch=claimed["owner_epoch"],at=3.)

            stop=reopened.prepare("stop-1","STOP",{"domain":"climate","service":"set_hvac_mode","hvac_mode":"off"},
                expected_revision=claimed["revision"],owner_id="owner",boot_id="boot-2",
                owner_epoch=claimed["owner_epoch"],at=3.)
            revision=reopened.record_sent("stop-1",at=4.,expected_revision=stop["revision"],
                owner_id="owner",boot_id="boot-2",owner_epoch=claimed["owner_epoch"])
            revision=reopened.record_ha_echo("stop-1",at=5.,expected_revision=revision,
                owner_id="owner",boot_id="boot-2",owner_epoch=claimed["owner_epoch"])
            revision=reopened.complete_safe_stop("stop-1",expected_revision=revision,
                owner_id="owner",boot_id="boot-2",owner_epoch=claimed["owner_epoch"])
            ready=reopened.status()
            self.assertEqual(ready["revision"],revision)
            self.assertFalse(ready["fenced"])
            accepted=reopened.prepare("heat-2","HEAT",{},expected_revision=revision,
                owner_id="owner",boot_id="boot-2",owner_epoch=claimed["owner_epoch"],at=6.)
            self.assertTrue(accepted["created"])

    def test_confirmed_retention_never_removes_unknown_evidence(self):
        body={"commands":{
            "unknown":dict(stage="UNKNOWN",command_id="unknown",ha_echo_at=None),
            **{f"ok-{index}":dict(stage="HA_ECHO",command_id=f"ok-{index}",ha_echo_at=index)
               for index in range(140)}
        }}
        self.module.CriticalLedger._trim_confirmed(body,keep=128)
        self.assertIn("unknown",body["commands"])
        self.assertEqual(len(body["commands"]),128)


if __name__=="__main__":
    unittest.main()
