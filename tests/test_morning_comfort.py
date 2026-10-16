import unittest
from datetime import datetime
from zoneinfo import ZoneInfo
from heating_model import plan, DEFAULT_MODEL, in_window

class MorningComfortTests(unittest.TestCase):
    def setUp(self):
        self.now=datetime(2026,10,12,17,tzinfo=ZoneInfo('Europe/Kiev'))
        self.model=dict(DEFAULT_MODEL,floor_air=.0442385,room_loss=.0128,
                        floor_decay=.1728,heat_gain=.72)
        self.settings=dict(start='23:00',end='07:00',strategy='Тільки в дозволені години',
                           target=22,lower=21.5,upper=22.5,night_price=2.16,day_price=4.32)
    def compute(self,room=22.1,floor=22.4,outside=10,fire=0,split=True):
        return plan(self.now,room,floor,outside,fire,self.model,[],
                    dict(self.settings,split_comfort=split))
    def test_two_sessions_without_extra_energy_or_worse_comfort(self):
        base=self.compute(split=False);p=self.compute()
        self.assertTrue(p['split_comfort_applied'])
        self.assertGreater(p['session_kwh']['evening'],0)
        self.assertGreater(p['session_kwh']['morning'],0)
        self.assertLessEqual(p['energy'],base['energy']+.01)
        self.assertGreaterEqual(p['minimum'],base['minimum']-.01)
        self.assertLessEqual(p['maximum'],max(base['maximum'],22.5)+.01)
    def test_nothing_outside_tariff_window(self):
        for seg in self.compute()['heat_segments']:
            at=datetime.fromtimestamp(seg['start'],self.now.tzinfo)
            last=datetime.fromtimestamp(seg['end']-.001,self.now.tzinfo)
            self.assertTrue(in_window(at,'23:00','07:00'))
            self.assertTrue(in_window(last,'23:00','07:00'))
    def test_fireplace_does_not_force_two_unnecessary_sessions(self):
        p=self.compute(room=22.2,floor=32,outside=10,fire=8)
        self.assertEqual(p['energy'],0)
        self.assertFalse(p['split_comfort_applied'])
    def test_morning_outage_is_never_overridden(self):
        start=self.now.replace(hour=5)+__import__('datetime').timedelta(days=1)
        self.settings['outage_schedule']=dict(valid=True,known_until=self.now.timestamp()+86400,
            outages=[dict(start=start.timestamp(),end=start.timestamp()+7200)],slots=[],updated='test')
        p=self.compute()
        self.assertTrue(all(not (s['start']<start.timestamp()+7200 and s['end']>start.timestamp())
                            for s in p['heat_segments']))
    def test_night_target_does_not_lower_user_hard_limit(self):
        self.settings['lower']=21.8
        p=self.compute()
        self.assertEqual(p['night_air_target'],21.8)
    def test_partial_night_does_not_require_elapsed_evening(self):
        self.now=self.now.replace(hour=3)
        p=self.compute()
        self.assertEqual(p['horizon_hours'],24)
        self.assertTrue(all(s['start']>=self.now.timestamp() for s in p['heat_segments']))
