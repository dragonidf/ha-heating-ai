"""Offline A/B evidence: energy budgets can trigger replanning.

This test never uses a Home Assistant connection, service call or actual
building data. The snapshot is the existing checked-in synthetic audit case.
"""
import json
import unittest
from datetime import datetime, timedelta
from pathlib import Path
from heating_stability import stable_plan
from heating_model import in_window


class BudgetABTests(unittest.TestCase):
    def test_existing_reference_with_and_without_budget(self):
        fixture = json.loads(Path(__file__).with_name("audit_case.json").read_text())
        at = datetime.fromisoformat("2026-10-12T21:00:30+03:00")
        settings = dict(
            start="23:00", end="07:00", strategy="Тільки в дозволені години",
            target=22., lower=21.5, upper=22.5,
            night_price=1.32, day_price=2.64,
            split_comfort=True, morning_min_minutes=30,
            modulation_profile=fixture["profile"],
        )
        args = (22.1, 22.8, 10.73, 0., fixture["model"], fixture["weather"])
        first, reference = stable_plan(at, *args, settings, power_kw=3.)
        self.assertTrue(first["heat_segments"], "Fixture needs heating to exercise budget")
        now = at + timedelta(minutes=5)
        baseline, _ = stable_plan(now, *args, settings, power_kw=3., previous=reference)
        # Exact same inputs; only the *hypothetical* budget changes.
        def scenario(remaining):
            options = dict(settings, cycle_energy_budget=dict(
                start=now.timestamp(), end=now.timestamp()+86400,
                remaining_kwh=remaining, initial_kwh=remaining,
            ))
            result, _ = stable_plan(now, *args, options, power_kw=3., previous=reference)
            return result

        generous = scenario(1000.)
        exhausted = scenario(0.)
        self.assertTrue(baseline["plan_stable"])
        self.assertTrue(generous["plan_stable"])
        self.assertFalse(exhausted["plan_stable"],
                         "Existing zero-budget may reject previously stable heating sessions")
        for result in (baseline, generous, exhausted):
            for segment in result["heat_segments"]:
                tm = datetime.fromtimestamp(segment["start"], now.tzinfo)
                self.assertTrue(in_window(tm, settings["start"], settings["end"]))
        def signature(result):
            segments=result["heat_segments"]
            return dict(stable=result["plan_stable"],
                        planned_kwh=result["energy"],
                        minimum_c=result["minimum"], maximum_c=result["maximum"],
                        session_count=len(segments),
                        session_minutes=round(sum(s["end"]-s["start"] for s in segments)/60, 1))
        print("BUDGET_AB_SYNTHETIC="+json.dumps(dict(
            baseline=signature(baseline), generous=signature(generous),
            exhausted=signature(exhausted)), sort_keys=True))


if __name__ == "__main__":
    unittest.main()
