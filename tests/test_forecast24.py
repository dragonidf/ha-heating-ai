import unittest
from datetime import datetime
from zoneinfo import ZoneInfo
from heating_model import plan, DEFAULT_MODEL

class Forecast24Tests(unittest.TestCase):
    def settings(self):
        return dict(start="23:00",end="07:00",strategy="Тільки в дозволені години",
                    lower=21.5,upper=22.5,target=22,night_price=None,day_price=None)
    def run_plan(self,now,settings,weather=()):
        return plan(now,21,25,5,0,DEFAULT_MODEL,list(weather),settings)
    def test_short_schedule_does_not_truncate(self):
        now=datetime(2026,10,12,17,tzinfo=ZoneInfo("Europe/Kiev"))
        s=self.settings(); end=now.timestamp()+7*3600
        s['outage_schedule']=dict(valid=True,known_until=end,outages=[],slots=[],updated='test')
        p=self.run_plan(now,s)
        self.assertEqual(p['horizon_hours'],24)
        self.assertEqual(len(p['curve']),48)
        self.assertEqual(p['curve'][-1]['ts'],now.timestamp()+86400)
        self.assertFalse(p['schedule_complete'])
        self.assertTrue(any(not row['schedule_known'] for row in p['curve']))
        self.assertTrue(all(seg['conditional']==(seg['end']>end) for seg in p['heat_segments']))
    def test_no_schedule_or_weather_is_explicitly_uncertain(self):
        now=datetime(2026,10,12,23,tzinfo=ZoneInfo("Europe/Kiev"))
        p=self.run_plan(now,self.settings())
        self.assertEqual(p['horizon_hours'],24)
        self.assertIsNone(p['schedule_known_until'])
        self.assertIsNone(p['weather_known_until'])
        self.assertTrue(all(seg['conditional'] for seg in p['heat_segments']))
    def test_known_outage_remains_blocked(self):
        now=datetime(2026,10,12,23,tzinfo=ZoneInfo("Europe/Kiev")); start=now.timestamp()
        s=self.settings();s['outage_schedule']=dict(valid=True,known_until=start+86400,
            outages=[dict(start=start,end=start+3600)],slots=[],updated='test')
        p=self.run_plan(now,s,[dict(ts=start+i*3600,temperature=5) for i in range(24)])
        self.assertTrue(p['schedule_complete']); self.assertTrue(p['weather_complete'])
        self.assertFalse(p['heat'])
        self.assertTrue(all(seg['start']>=start+3600 for seg in p['heat_segments']))
        self.assertEqual(p['conditional_heat_minutes'],0)
