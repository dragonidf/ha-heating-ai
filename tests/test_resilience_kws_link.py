"""KWS transport failure is not a trip and does not authorize a new start."""
import importlib.util
from pathlib import Path
import unittest

_PATH = Path(__file__).resolve().parents[1] / "custom_components/heating_ai_legacy/kws_readiness.py"
_SPEC = importlib.util.spec_from_file_location("resilience_kws_readiness", _PATH)
_MOD = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(_MOD)
KWSReadiness = _MOD.KWSReadiness


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


if __name__ == "__main__":
    unittest.main()
