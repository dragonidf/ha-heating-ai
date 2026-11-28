"""Policy tests for the native scheduled learning decision."""
from datetime import datetime, timedelta
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from zoneinfo import ZoneInfo

from heating_learning_windows import promotion_evidence, rollback_reason, promotion_allowed, run_learning, save_evidence
from heating_model import DEFAULT_MODEL

TZ = ZoneInfo("Europe/Kyiv")
START = datetime(2026,11,1,tzinfo=TZ)
MODEL = {**DEFAULT_MODEL,"version":6,"validated":True}


def pairs(old_error=.5, new_error=.3):
    results=[]
    for h,n in ((1,12),(3,6)):
        for i in range(n):
            at=START+timedelta(days=i//3,hours=(i%3)*4)
            start=at.timestamp()
            results.append(dict(start=start,end=start+h*3600,day=at.date().isoformat(),
                horizon=h,old_error=old_error,shadow_error=new_error,
                valid=True,guard_ok=True,stratum="night_idle",violation=False))
    return results


class UnifiedLearningPolicyTest(unittest.TestCase):
    def test_both_horizons_and_shared_valid_evidence_required(self):
        evidence=pairs()
        self.assertEqual(promotion_evidence(evidence)["status"],"accepted")
        self.assertNotEqual(promotion_evidence([r for r in evidence if r["horizon"]==1])["status"],"accepted")
        self.assertNotEqual(promotion_evidence(pairs(new_error=.495))["status"],"accepted")

    def test_safety_and_bias_are_independent_gates(self):
        evidence=pairs()
        evidence[1]["guard_ok"]=False
        self.assertEqual(promotion_evidence(evidence)["status"],"rejected")
        evidence=pairs()
        for i,row in enumerate(evidence):
            row["old_error"]*=(-1 if i%2 else 1)
        self.assertEqual(promotion_evidence(evidence)["status"],"rejected")

    def test_atomic_callback_must_recheck_version_daily_cap_and_cooldown(self):
        evidence=promotion_evidence(pairs())
        proposed={"floor_air":MODEL["floor_air"]*1.01}
        now=START+timedelta(days=10)
        self.assertTrue(promotion_allowed(MODEL,MODEL,proposed,evidence,{},now))
        for state in ({"promotion_day":now.date().isoformat()},{"frozen_until":now.timestamp()+1}):
            self.assertFalse(promotion_allowed(MODEL,MODEL,proposed,evidence,state,now))
        self.assertFalse(promotion_allowed(MODEL,{**MODEL,"version":7},proposed,evidence,{},now))
        self.assertFalse(promotion_allowed(MODEL,MODEL,{"floor_air":.1},evidence,{},now))
        self.assertFalse(promotion_allowed(MODEL,MODEL,{"heat_gain":MODEL["heat_gain"]},evidence,{},now))

    def test_rollback_requires_qualified_independent_degradation(self):
        baseline={"1:night_idle":{"mae":.3,"violation":False}}
        bad=pairs(old_error=.5)
        self.assertEqual(rollback_reason(bad,baseline),"forecast_degradation")
        self.assertIsNone(rollback_reason(bad[:10],baseline))
        self.assertIsNone(rollback_reason([{**r,"old_error":.38} for r in bad],baseline))
        self.assertIsNone(rollback_reason([{**r,"stratum":"unmatched"} for r in bad],baseline))
        for row in bad[:2]:
            row["violation"]=True
        self.assertEqual(rollback_reason(bad[:2],baseline),"confirmed_comfort_violations")


    def test_qualified_archives_produce_one_bounded_proposal_in_scheduled_job(self):
        from tools.replay_adaptive_learning import MODEL as baseline, TZ, fixture, frozen_fixture
        rows=fixture()
        archive=frozen_fixture(None,capture=True,perturb=False)
        now=datetime(2026,12,4,20,45,tzinfo=TZ)
        with TemporaryDirectory() as directory:
            path=Path(directory)/"online_learning_v1.json"
            save_evidence(path,dict(windows=[],forecasts=archive,comparisons=[],report={}))
            result=run_learning(path,rows,[],baseline,{},now,True)
            self.assertEqual(result["status"],"accepted",result)
            self.assertEqual(set(result["proposed"]),{"floor_air","room_loss"})
            self.assertTrue(promotion_allowed(baseline,baseline,result["proposed"],result["evidence"],{},now))
            frozen=run_learning(path,rows,[],baseline,{"frozen_until":now.timestamp()+1},now,True)
            self.assertEqual(frozen["status"],"frozen")
            self.assertNotIn("proposed",frozen)

    def test_startup_uses_only_latest_complete_telemetry_records(self):
        from test_adapter_modulation import HeatingClimate
        with TemporaryDirectory() as directory:
            path=Path(directory)/"telemetry.jsonl"
            prefix=("застарілий\n"*20000).encode()
            latest=[b'{"ts":1}',b'', '{"text":"Київ"}'.encode()]
            path.write_bytes(prefix+b"\n".join(latest))
            self.assertEqual(HeatingClimate.telemetry_tail(path,3),latest)
            path.write_bytes(prefix+b"\n".join(latest)+b"\n")
            self.assertEqual(HeatingClimate.telemetry_tail(path,3),latest)

    def test_executed_plan_can_violate_comfort_despite_an_in_range_forecast(self):
        from tools.replay_adaptive_learning import TZ, frozen_fixture
        from heating_learning_windows import score_frozen
        entry=frozen_fixture(None,days=1,capture=True)[0]
        check=list(entry["checks"][0])
        self.assertLess(check[2],check[5])
        check[7]=check[5]+.2
        result=score_frozen(entry["snapshot"],[check],TZ)[0]
        self.assertTrue(result["valid"])
        self.assertTrue(result["violation"])
        check[8]=["execution_timing_mismatch"]
        result=score_frozen(entry["snapshot"],[check],TZ)[0]
        self.assertFalse(result["valid"])
        self.assertIsNone(rollback_reason([result],{f"1:{result['stratum']}":{"mae":0.,"violation":False}}))
