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
        for bad in (True, False, 0, 10, 1.5, '6', float('nan')):
            with self.subTest(bad=bad), self.assertRaises(ValueError):
                self.m.start(bad, 100, self.t, self.t, 180)
        for good in (1, 6, 9):
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

    def test_long_idle_is_supported_only_with_proven_continuity(self):
        with self.assertRaises(ValueError):
            self.m.start(6, 100, self.t-3600, self.t, 180)
        self.assertEqual(self.m.start(6, 100, self.t-3600, self.t, 180, idle_since=self.t-4000)['target_kwh'], 6)
        with self.assertRaises(ValueError):
            self.m.start(6, 100, self.t-3600, self.t, 180, idle_since=self.t-3500)

    def test_session_deadline_is_a_safety_limit(self):
        s = self.m.start(6, 100, self.t, self.t, 15)
        self.m.advance(s, 101, self.t+900, self.t+900)
        self.assertEqual((s['status'], s['reason']), ('cancelled', 'time_limit'))
