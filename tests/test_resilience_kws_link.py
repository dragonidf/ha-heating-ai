"""KWS transport failure is not a trip and does not authorize a new start."""
import importlib.util
from pathlib import Path
import unittest

_PATH = Path(__file__).resolve().parents[1] / "custom_components/heating_ai_legacy/kws_readiness.py"
_SPEC = importlib.util.spec_from_file_location("resilience_kws_readiness", _PATH)
_MOD = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(_MOD)
KWSReadiness = _MOD.KWSReadiness
KWSRestorePolicy = _MOD.KWSRestorePolicy


def rows(at, *, relay="on", fault="off", code=0, status="on", voltages=(220., 230., 225.)):
    data = {
        "relay": {"at": at, "value": relay},
        "status": {"at": at, "value": status},
        "fault": {"at": at, "value": fault, "fault_code": code},
    }
    data.update({phase: {"at": at, "value": v} for phase, v in zip("abc", voltages)})
    return data


class KWSLinkResilienceTests(unittest.TestCase):
    def qualified(self):
        guard = KWSReadiness()
        self.assertFalse(guard.evaluate(1000, "on", True, rows(1000), grid_epoch=800)["ready"])
        self.assertTrue(guard.evaluate(1121, "on", True, rows(1121), grid_epoch=800)["ready"])
        return guard

    def test_short_gap_retains_only_continuity(self):
        for delay in (5, 30, 60, 180, 300):
            with self.subTest(delay=delay):
                guard = self.qualified()
                report = guard.evaluate(1121+delay, "on", True, {})
                self.assertEqual(report["reason"], "kws_link_unknown")
                self.assertFalse(report["ready"])
                self.assertTrue(report["continue_allowed"])

    def test_gap_expiry_cannot_be_extended_by_polling(self):
        guard = self.qualified()
        for delay in (5, 30, 60, 180, 300):
            self.assertTrue(guard.evaluate(1121+delay, "on", True, {})["continue_allowed"])
        self.assertFalse(guard.evaluate(1422, "on", True, {})["continue_allowed"])
        self.assertFalse(guard.evaluate(1721, "on", True, {})["continue_allowed"])

    def test_link_recovery_no_new_power_reset_delay(self):
        guard = self.qualified()
        self.assertTrue(guard.evaluate(1150, "on", True, {})["continue_allowed"])
        self.assertTrue(guard.evaluate(1155, "on", True, rows(1155))["ready"])

    def test_trip_or_manual_off_cancels_continuity_even_if_other_rows_missing(self):
        for negative in (
            {"fault": {"at": 1150, "value": "on", "fault_code": 2}},
            {"relay": {"at": 1150, "value": "off"}},
            {"status": {"at": 1150, "value": "standby"}},
            {"a": {"at": 1150, "value": 189.}},
        ):
            with self.subTest(negative=negative):
                guard = self.qualified()
                report = guard.evaluate(1150, "on", True, negative)
                self.assertFalse(report["ready"])
                self.assertFalse(report["continue_allowed"])
                self.assertFalse(guard.evaluate(1151, "on", True, {})["continue_allowed"])
                self.assertFalse(guard.evaluate(1152, "on", True, rows(1152))["ready"])

    def test_grid_loss_voltage_loss_and_restart_clear_continuity(self):
        for grid, volts in (("off", True), ("unknown", True), ("on", False)):
            with self.subTest(grid=grid, volts=volts):
                guard = self.qualified()
                self.assertFalse(guard.evaluate(1150, grid, volts, {})["continue_allowed"])
                self.assertFalse(guard.evaluate(1151, "on", True, {})["continue_allowed"])
        self.assertFalse(KWSReadiness().evaluate(1150, "on", True, {})["continue_allowed"])

    def test_confirmed_link_timestamp_not_tick_time(self):
        guard = self.qualified()
        self.assertTrue(guard.evaluate(1250, "on", True, rows(1121))["ready"])
        self.assertFalse(guard.evaluate(1422, "on", True, {})["continue_allowed"])

    def test_manual_off_is_not_automatic_reclose_permission(self):
        guard = self.qualified()
        report = guard.evaluate(1150, "on", True, rows(1150, relay="off", status="off"))
        self.assertEqual(report["reason"], "kws_relay_off")
        self.assertFalse(report["ready"])
        self.assertFalse(report["continue_allowed"])
        self.assertEqual(report["kws_relay_state"], "off")


class KWSRestorePolicyTests(unittest.TestCase):
    def outage(self):
        policy = KWSRestorePolicy()
        policy.observe(1000, {"grid_state": "on", "ready": True,
                              "kws_relay_state": "on", "kws_status": "on"}, True)
        policy.observe(1015, {"grid_state": "off", "ready": False,
                              "kws_relay_state": "off", "kws_fault_state": "on",
                              "kws_fault": 2, "kws_fault_at": 1015}, True)
        return policy

    @staticmethod
    def restored(**overrides):
        return {
            "grid_state": "on", "ready": False, "voltage_qualified": True,
            "kws_link_fresh": True, "kws_relay_state": "off", "kws_status": "off",
            "kws_fault_state": "off", "kws_fault": 0,
            "grid_since": 1030, "stable_since": 1030,
            "kws_relay_at": 1149, "kws_status_at": 1149, "kws_fault_at": 1149,
            "kws_phases_v": [220., 230., 225.], **overrides
        }

    voltage = {"recovery_low_v": 201, "recovery_high_v": 250,
               "phases_v": [220., 230., 225.]}

    def test_waits_hardware_delay_and_clear_fault(self):
        p = self.outage()
        bad = self.restored(kws_fault_state="on", kws_fault=2, kws_status="standby")
        p.observe(1123, bad, True)
        self.assertFalse(p.eligible(1123, bad, self.voltage, 120))
        self.assertFalse(p.eligible(1150, bad, self.voltage, 120))
        healthy = self.restored(kws_relay_at=1122, kws_status_at=1122, kws_fault_at=1122)
        self.assertFalse(p.eligible(1123, healthy, self.voltage, 120))  # yesterday: 93s
        self.assertTrue(p.eligible(1150, healthy, self.voltage, 120))

    def test_one_attempt_no_retry_and_dispatch_recheck(self):
        p = self.outage()
        r = self.restored()
        p.observe(1150, r, True)
        self.assertTrue(p.eligible(1150, r, self.voltage, 120))
        self.assertTrue(p.claim())
        self.assertFalse(p.eligible(1150, r, self.voltage, 120))
        self.assertTrue(p.eligible(1150, r, self.voltage, 120, claimed=True))
        self.assertFalse(p.eligible(1150, self.restored(kws_fault_state="on", kws_fault=2),
                                    self.voltage, 120, claimed=True))
        p.finish(False)
        self.assertFalse(p.claim())
        self.assertFalse(p.eligible(1160, r, self.voltage, 120, claimed=True))

    def test_manual_off_and_other_fault_are_hard_vetoes(self):
        p = self.outage()
        p.manual_intent("turn_off")
        self.assertFalse(p.eligible(1150, self.restored(), self.voltage, 120))
        p = self.outage()
        p.observe(1050, self.restored(kws_fault_state="on", kws_fault=3), True)
        self.assertFalse(p.eligible(1150, self.restored(), self.voltage, 120))

    def test_unknown_or_stale_is_not_permission(self):
        for key, value in (("grid_state", "unknown"), ("kws_link_fresh", False),
                           ("kws_fault", "unknown"), ("kws_fault_at", 1029),
                           ("kws_relay_at", 1029), ("kws_status_at", 1029),
                           ("kws_phases_v", [220., 255., 225.])):
            with self.subTest(key=key):
                p = self.outage()
                self.assertFalse(p.eligible(1150, self.restored(**{key:value}), self.voltage, 120))
        self.assertFalse(self.outage().eligible(1150, self.restored(), self.voltage, None))

    def test_no_reclose_from_boot_or_without_observed_prior_on(self):
        p = KWSRestorePolicy()
        p.observe(1015, {"grid_state": "off", "ready": False}, True)
        self.assertFalse(p.eligible(1150, self.restored(), self.voltage, 120))
        p = self.outage()
        p.observe(1031, {"grid_state": "unknown"}, True)
        self.assertFalse(p.eligible(1150, self.restored(), self.voltage, 120))

    def test_no_reclose_without_witnessed_undervoltage_trip(self):
        p = KWSRestorePolicy()
        p.observe(1000, {"grid_state": "on", "ready": True,
                         "kws_relay_state": "on"}, True)
        p.observe(1015, {"grid_state": "off", "ready": False,
                         "kws_relay_state": "off", "kws_fault_state": "off"}, True)
        self.assertFalse(p.eligible(1150, self.restored(), self.voltage, 120))
        self.assertFalse(p.claim())

    def test_expired_recovery_epoch_and_stale_fault_do_not_authorize(self):
        p = self.outage()
        self.assertFalse(p.eligible(1800, self.restored(
            kws_relay_at=1799, kws_status_at=1799, kws_fault_at=1799),
            self.voltage, 120))
        self.assertFalse(p.eligible(1400, self.restored(
            kws_relay_at=1399, kws_status_at=1399, kws_fault_at=1100),
            self.voltage, 120))

    def test_native_auto_restore_needs_no_command(self):
        p = self.outage()
        p.observe(1150, self.restored(kws_relay_state="on", kws_status="on", ready=True), True)
        self.assertEqual(p.status, "restored")
        self.assertFalse(p.claim())


if __name__ == "__main__":
    unittest.main()
