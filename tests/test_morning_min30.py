import unittest
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo
from heating_model import plan, DEFAULT_MODEL, morning_runs_valid

class MorningMin30Tests(unittest.TestCase):
    def setUp(self):
        self.now=datetime(2026,10,12,17,40,tzinfo=ZoneInfo('Europe/Kiev'))
        self.model=dict(DEFAULT_MODEL,floor_air=.0442385,room_loss=.0128,floor_decay=.1728,heat_gain=.72)
        self.settings=dict(start='23:00',end='07:00',strategy='Тільки в дозволені години',
            target=22,lower=21.5,upper=22.5,night_price=2.16,day_price=4.32,
            split_comfort=True,morning_min_minutes=30)
    def rows(self,at,minutes):
        return [dict(heat_segments=[dict(start=at.timestamp(),end=at.timestamp()+minutes*60)])]
    def test_fifteen_minutes_are_rejected(self):
        at=self.now.replace(hour=6,minute=40)+timedelta(days=1)
        self.assertFalse(morning_runs_valid(self.rows(at,15),self.now,30))
    def test_two_separate_fifteen_minute_runs_are_rejected(self):
        at=self.now.replace(hour=5,minute=0)+timedelta(days=1)
        rows=self.rows(at,15)+self.rows(at+timedelta(minutes=30),15)
        self.assertFalse(morning_runs_valid(rows,self.now,30))
    def test_remaining_fifteen_after_fifteen_actual_minutes_are_valid(self):
        at=self.now.replace(hour=6,minute=45)
        self.assertTrue(morning_runs_valid(self.rows(at,15),at,30,elapsed=15))
        self.assertFalse(morning_runs_valid([],at,30,elapsed=15))
    def test_full_forecast_has_no_short_morning_sessions(self):
        p=plan(self.now,22.1,22.4,10,0,self.model,[],self.settings)
        self.assertTrue(p['morning_runs_minutes'])
        self.assertTrue(all(m>=30 for m in p['morning_runs_minutes']))
        self.assertTrue(p['split_comfort_applied'])
    def test_late_new_session_is_not_squeezed_into_fifteen_minutes(self):
        at=self.now.replace(hour=6,minute=45)
        p=plan(at,21.5,22,5,0,self.model,[],self.settings)
        self.assertTrue(all(seg['start']>=at.timestamp()+900 for seg in p['heat_segments']))
    def test_outage_cannot_fragment_morning_into_short_runs(self):
        at=self.now.replace(hour=5,minute=15)+timedelta(days=1)
        self.settings['outage_schedule']=dict(valid=True,known_until=self.now.timestamp()+86400,
            outages=[dict(start=at.timestamp(),end=at.timestamp()+90*60)],slots=[],updated='test')
        p=plan(self.now,22.1,22.4,10,0,self.model,[],self.settings)
        self.assertTrue(all(m>=30 for m in p['morning_runs_minutes']))
        self.assertTrue(all(not (seg['start']<at.timestamp()+90*60 and seg['end']>at.timestamp())
                            for seg in p['heat_segments']))
