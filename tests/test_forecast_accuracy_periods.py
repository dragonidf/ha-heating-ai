"""Period comparisons use issued forecasts, not reconstructed history."""
import unittest
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo


class ForecastAccuracyPeriodsTests(unittest.TestCase):
    def setUp(self):
        self.now = datetime(2026, 11, 26, 18, tzinfo=ZoneInfo("Europe/Kyiv"))

    def row(self, days, error, **extra):
        reported = (self.now-timedelta(days=days)).timestamp()
        return dict(horizon=6, issued_at=reported-21600, actual_at=reported,
                    actual_horizon_hours=6., error=error, model_version=6,
                    physical_mode="fixed_6", equipment_version=1,
                    actual_temperature_basis="sensor_report",
                    weather_known=True, schedule_known=True, **{}) | extra

    def test_periods_weight_real_reports_not_daily_averages_and_do_not_mix_cohorts(self):
        from heating_recovery import forecast_accuracy_periods
        rows = [self.row(1, .2), self.row(2, -.4), self.row(8, .8),
                self.row(20, -1.),
                self.row(1, 8., model_version=5),
                self.row(1, 9., equipment_version=2),
                self.row(1, 7., physical_mode="fixed_9"),
                self.row(1, 6., weather_known=False),
                self.row(1, 5., schedule_known=False),
                self.row(1, 4., actual_temperature_basis="interpolated_reports"),
                self.row(1, 3., equipment_version=None),
                self.row(31, 100.), self.row(-1, 100.)]
        view = forecast_accuracy_periods(rows, self.now)
        self.assertEqual(len(view["groups"]), 8)
        group = next(g for g in view["groups"] if g["model_version"] == 6
                     and g["equipment_version"] == 1 and g["physical_mode"] == "fixed_6"
                     and g["basis"] == "sensor_report" and g["weather_known"] is True
                     and g["schedule_known"] is True)
        p = group["periods"]
        self.assertEqual(p["7"]["count"], 2)
        self.assertAlmostEqual(p["7"]["mae"], .3)
        self.assertAlmostEqual(p["7"]["bias"], -.1)
        self.assertEqual(p["7"]["days"], 2)
        self.assertEqual(p["7"]["missing_days"], 5)
        self.assertEqual(p["14"]["count"], 3)
        self.assertAlmostEqual(p["14"]["mae"], 1.4/3)
        self.assertEqual(p["30"]["count"], 4)
        self.assertAlmostEqual(p["30"]["mae"], .6)
        self.assertEqual(view["period_days"], [7,14,30])

    def test_excludes_wrong_lead_and_deduplicates_same_report_and_cohort(self):
        from heating_recovery import forecast_accuracy_periods
        first = self.row(1, .2)
        duplicate = dict(first, actual_horizon_hours=6.2,
                         issued_at=first["actual_at"]-6.2*3600, error=5.)
        late = self.row(2, 10., actual_horizon_hours=7.)
        rows = [duplicate, first, late, self.row(9, -.4)]
        result = forecast_accuracy_periods(rows, self.now)["groups"][0]["periods"]
        self.assertEqual(result["7"]["count"], 1)
        self.assertEqual(result["7"]["mae"], .2)
        self.assertEqual(result["14"]["count"], 2)
        self.assertAlmostEqual(result["14"]["mae"], .3)

    def test_missing_recent_period_is_unknown_not_zero_error(self):
        from heating_recovery import forecast_accuracy_periods
        result = forecast_accuracy_periods([self.row(20, .5)], self.now)["groups"][0]["periods"]
        self.assertEqual(result["7"]["count"], 0)
        self.assertEqual(result["7"]["missing_days"], 7)
        self.assertIsNone(result["7"]["mae"])
        self.assertIsNone(result["7"]["bias"])
        self.assertEqual(result["30"]["count"], 1)
        self.assertEqual(forecast_accuracy_periods([], self.now)["groups"], [])
