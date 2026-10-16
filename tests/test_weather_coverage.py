"""Hourly coverage tolerance is identical for rebuilt and retained 24-hour plans."""
import unittest
from datetime import datetime
from heating_model import DEFAULT_MODEL, plan
from heating_stability import replay


class WeatherCoverageTests(unittest.TestCase):
    def setUp(self):
        self.now=datetime.fromisoformat('2026-10-16T15:42:00+03:00')
        self.finish=self.now.timestamp()+86400
        self.settings=dict(start='23:00',end='07:00',strategy='Тільки в дозволені години',
                           lower=21.5,upper=22.5,target=22,night_price=None,day_price=None)

    def assert_coverage(self,missing,complete):
        weather=[] if missing is None else [
            dict(ts=self.finish-missing*60-86400+i*3600,temperature=5) for i in range(24)]
        for kind in ('new','retained'):
            with self.subTest(kind=kind,missing=missing):
                args=(self.now,21,25,5,0,DEFAULT_MODEL,weather,self.settings)
                result=plan(*args) if kind=='new' else replay(*args,[],False)
                self.assertIsNotNone(result)
                self.assertEqual(result['weather_complete'],complete)
                self.assertEqual(result['weather_missing_minutes'],missing)
                self.assertEqual(result['horizon_hours'],24)
                self.assertEqual(result['curve'][-1]['ts'],self.finish)
                self.assertEqual(result['weather_known_until'],
                                 None if missing is None else self.finish-missing*60)
                # Tolerance suppresses a warning, without claiming actual tail data.
                self.assertEqual(result['curve'][-1]['weather_known'],missing==0)
                self.assertFalse(result['schedule_complete'])

    def test_24_hourly_points_short_by_30_to_59_minutes_are_sufficient(self):
        for missing in (30,42,59):self.assert_coverage(missing,True)

    def test_exactly_one_hour_short_is_sufficient(self):
        self.assert_coverage(60,True)

    def test_more_than_one_hour_short_is_incomplete(self):
        for missing in (61,90,95):self.assert_coverage(missing,False)

    def test_full_coverage_has_zero_missing_minutes(self):
        self.assert_coverage(0,True)

    def test_absent_forecast_keeps_fallback_and_is_incomplete(self):
        self.assert_coverage(None,False)
