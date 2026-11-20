"""Unit tests for the raw KWS energy limit; no HA or physical commands."""
import importlib.util
from pathlib import Path
import unittest

path = Path(__file__).resolve().parents[1] / 'custom_components/heating_ai_legacy/manual_energy.py'


def load():
    spec = importlib.util.spec_from_file_location('manual_energy', path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class ManualEnergyTest(unittest.TestCase):
    def setUp(self):
        self.m = load()
        self.t = 10000
        self.s = self.m.start(6, 419.37, self.t, self.t, 180)

    def test_whole_kwh_only(self):
        for bad in (True, False, 0, 41, 1.5, '6', float('nan')):
            with self.subTest(bad=bad), self.assertRaises(ValueError):
                self.m.start(bad, 100, self.t, self.t, 180)
        for good in (1, 6, 9, 10, 39, 40):
            self.assertEqual(self.m.start(good, 100, self.t, self.t, 180)['target_kwh'], good)

    def test_exact_decimal_threshold(self):
        for seconds, value in [(900, 420.87), (1800, 422.37), (2700, 423.87), (3600, 425.36)]:
            self.m.advance(self.s, value, self.t+seconds, self.t+seconds)
        self.assertEqual(self.s['status'], 'active')
        self.m.advance(self.s, 425.37, self.t+3610, self.t+3610)
        self.assertEqual((self.s['status'], self.s['consumed_kwh']), ('completed', 6.0))

    def test_no_estimated_energy_and_repeat_report_is_idempotent(self):
        self.m.advance(self.s, 420.87, self.t+900, self.t+900)
        self.m.advance(self.s, 420.87, self.t+900, self.t+915)
        self.assertEqual(self.s['consumed_kwh'], 1.5)

    def test_repeated_unchanged_reports_do_not_shrink_counter_jump_window(self):
        s=self.m.start(6,100,self.t,self.t,180)
        for n in range(1,60):
            self.m.advance(s,100,self.t+n*15,self.t+n*15)
        self.m.advance(s,101.5,self.t+900,self.t+900)
        self.assertEqual((s['status'],s['consumed_kwh']),('active',1.5))

    def test_live_measured_power_progress_stops_small_request_before_counter_report(self):
        s=self.m.start(1,100,self.t,self.t,180)
        for n in range(41):
            at=self.t+n*15
            self.m.advance(s,100,at,at)
            self.m.observe_power(s,6000,at,at)
            if n<40:
                self.assertEqual(s['status'],'active')
                self.assertAlmostEqual(s['progress_kwh'],n*.025)
        self.assertEqual(s['status'],'completed')
        self.assertEqual(s['completion_basis'],'kws_power')
        self.assertAlmostEqual(s['progress_kwh'],1)
        self.assertEqual(s['consumed_kwh'],0)

    def test_power_and_counter_are_alternative_totals_not_added(self):
        s=self.m.start(6,100,self.t,self.t,180)
        for n in range(61):
            at=self.t+n*15
            self.m.advance(s,101.5 if n==60 else 100,at,at)
            self.m.observe_power(s,6000,at,at)
        self.assertEqual(s['consumed_kwh'],1.5)
        self.assertAlmostEqual(s['progress_kwh'],1.5)
        self.assertEqual(s['status'],'active')

    def test_power_observations_never_integrate_missing_duplicate_or_stale_intervals(self):
        s=self.m.start(6,100,self.t,self.t,180)
        self.m.observe_power(s,6000,self.t,self.t)
        self.m.observe_power(s,6000,self.t+15,self.t+15)
        self.m.observe_power(s,6000,self.t+15,self.t+16)
        self.m.observe_power(s,None,self.t+30,self.t+30)
        self.m.observe_power(s,6000,self.t+300,self.t+300)
        self.m.observe_power(s,6000,self.t+315,self.t+400)
        self.assertAlmostEqual(s['integrated_kwh'],.025)
        self.assertEqual(s['status'],'active')

    def test_late_counter_can_overshoot_and_reports_actual_fact(self):
        s = self.m.start(1, 100, self.t, self.t, 180)
        self.m.advance(s, 101.5, self.t+900, self.t+900)
        self.assertEqual((s['status'], s['consumed_kwh']), ('completed', 1.5))

    def test_reset_stale_jump_missing_and_out_of_order_cancel(self):
        for value, at, now, reason in (
            (1, self.t+1, self.t+1, 'counter_reset'),
            (430, self.t+1, self.t+1, 'counter_jump'),
            (419.37, self.t, self.t+1201, 'counter_stale'),
            (None, self.t, self.t, 'counter_invalid'),
            (419.37, self.t-1, self.t, 'counter_order'),
        ):
            with self.subTest(reason=reason):
                s = self.m.start(6, 419.37, self.t, self.t, 180)
                self.m.advance(s, value, at, now)
                self.assertEqual((s['status'], s['reason']), ('cancelled', reason))
                self.assertEqual(s['consumed_kwh'], 0)

    def test_cancel_and_repeated_stop_do_not_rearm(self):
        self.m.finish(self.s, 'user_stop', self.t+5)
        self.m.finish(self.s, 'grid', self.t+6)
        self.m.advance(self.s, 420, self.t+900, self.t+900)
        self.assertEqual((self.s['status'], self.s['reason']), ('cancelled', 'user_stop'))

    def test_old_idle_baseline_requires_proof(self):
        with self.assertRaises(ValueError):
            self.m.start(6, 100, self.t-1000, self.t, 180)
        s = self.m.start(6, 100, self.t-1000, self.t, 180, idle_since=self.t-1200)
        self.assertEqual(s['baseline_kwh'], 100)
        with self.assertRaises(ValueError):
            self.m.start(6, 100, self.t-1000, self.t, 180, idle_since=self.t-500)

    def test_manual_only_stop_and_allowed_handoff(self):
        self.assertEqual(self.m.arbitrate(False, True, False), (True, False))
        self.assertEqual(self.m.arbitrate(False, False, True), (False, True))
        self.assertEqual(self.m.arbitrate(True, False, True), (True, False))
        self.assertEqual(self.m.arbitrate(True, True, False), (True, False))

    def test_session_deadline_is_a_safety_limit(self):
        s = self.m.start(6, 100, self.t, self.t, 15)
        self.m.advance(s, 101, self.t+900, self.t+900)
        self.assertEqual((s['status'], s['reason']), ('cancelled', 'time_limit'))

    def test_40_kwh_can_complete_at_3_6_and_9_kw_without_legacy_limit(self):
        for power in (3, 6, 9):
            with self.subTest(power=power):
                s = self.m.start(40, 100, self.t, self.t,
                                 self.m.watchdog_minutes(40, 180))
                self.assertEqual(s['deadline']-self.t, 830*60)
                at = self.t
                value = 100.
                while value < 140:
                    at += 900
                    value = min(140., value+power/4)
                    self.m.advance(s, value, at, at)
                    if value < 140:
                        self.assertEqual(s['status'], 'active')
                self.assertEqual((s['status'], s['consumed_kwh']), ('completed', 40.))
                self.assertLess(at, s['deadline'])

    def test_manual_watchdog_preserves_existing_floor_and_is_bounded(self):
        self.assertEqual(self.m.watchdog_minutes(1,180),180)
        self.assertEqual(self.m.watchdog_minutes(40,480),830)
        for invalid in (None, 0, 481, True, float('nan')):
            with self.subTest(invalid=invalid), self.assertRaises(ValueError):
                self.m.watchdog_minutes(40,invalid)
