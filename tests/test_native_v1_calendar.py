"""Svitlo calendar intervals are preserved without assuming 24h grid supply."""
import importlib
from datetime import datetime,timedelta,timezone
from pathlib import Path
import sys
from types import ModuleType
import unittest

PKG="heating_calendar_fixture"
ROOT=Path(__file__).resolve().parents[1]/"custom_components"/"heating_ai_legacy"


class CalendarTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        module=ModuleType(PKG)
        module.__path__=[str(ROOT)]
        sys.modules[PKG]=module
        cls.adapter=importlib.import_module(PKG+".native_calendar")

    @classmethod
    def tearDownClass(cls):
        for name in tuple(sys.modules):
            if name==PKG or name.startswith(PKG+"."):
                sys.modules.pop(name,None)

    def test_none_or_empty_feed_is_never_clear_grid(self):
        at=datetime(2026,11,12,tzinfo=timezone.utc).timestamp()
        for events in (None, [], [{}], [{"start":"2026-11-12T12:00:00"}]):
            with self.subTest(events=events):
                result=self.adapter.parse_events(events,at)
                self.assertFalse(result["coverage_proven"])
                self.assertFalse(result["valid"])
                self.assertEqual(result["outages"],[])
                self.assertFalse(result["physical_commands_enabled"])

    def test_known_overlapping_events_merge_but_unpublished_horizon_unknown(self):
        at=datetime(2026,11,12,tzinfo=timezone.utc).timestamp()
        events=[
            {"start":datetime.fromtimestamp(at+3600,timezone.utc),
             "end":datetime.fromtimestamp(at+7200,timezone.utc)},
            {"start":datetime.fromtimestamp(at+5400,timezone.utc),
             "end":datetime.fromtimestamp(at+9000,timezone.utc)},
        ]
        result=self.adapter.parse_events(events,at)
        self.assertTrue(result["valid"])
        self.assertEqual(len(result["outages"]),1)
        self.assertEqual(result["outages"][0],dict(
            start=at+3600,end=at+9000,
        ))
        self.assertEqual(result["known_until"],at+9000)
        self.assertFalse(result["coverage_proven"])
        self.assertEqual(result["mode"],"calendar_events_partial")


if __name__=="__main__":
    unittest.main()
