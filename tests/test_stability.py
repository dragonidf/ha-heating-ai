import json
import unittest
from pathlib import Path
from datetime import datetime,timedelta
from heating_stability import stable_plan,reference

class StabilityTests(unittest.TestCase):
    def setUp(self):
        self.x=json.loads(Path(__file__).with_name('audit_case.json').read_text())
        self.now=datetime.fromisoformat('2026-10-12T21:00:30+03:00')
        self.settings=dict(start='23:00',end='07:00',strategy='Тільки в дозволені години',
            target=22.,lower=21.5,upper=22.5,night_price=1.32,day_price=2.64,
            split_comfort=True,morning_min_minutes=30,modulation_profile=self.x['profile'])
    def call(self,minute=0,previous=None,room=22.1,floor=22.8,fire=0,settings=None,weather=None):
        return stable_plan(self.now+timedelta(minutes=minute),room,floor,10.73,fire,
            self.x['model'],self.x['weather'] if weather is None else weather,
            self.settings if settings is None else settings,3.,previous)
    def test_regression_2105_initial_plan_has_morning(self):
        p,_=self.call(5)
        self.assertTrue(p['morning_runs_minutes'])
        self.assertGreaterEqual(min(p['morning_runs_minutes']),30)
    def test_repeated_updates_keep_session_times_and_refresh_curve(self):
        p,ref=self.call(); self.assertTrue(p['morning_runs_minutes'])
        def sessions(p):
            groups=[]
            for s in p['heat_segments']:
                if groups and abs(groups[-1][1]-s['start'])<.001:
                    groups[-1][1]=s['end']
                else:
                    groups.append([s['start'],s['end']])
            return groups
        starts=sessions(p)
        for minute in range(5,100,5):
            q,newref=self.call(minute,ref,room=22.1+(minute%10)/1000)
            self.assertTrue(q['plan_stable'],minute)
            self.assertEqual(sessions(q),starts)
            self.assertEqual(ref,newref)
            self.assertEqual(q['horizon_hours'],24)
            self.assertEqual(len(q['curve']),48)
        self.assertNotEqual(p['curve'][0]['ts'],q['curve'][0]['ts'])
    def test_small_measurement_changes_are_resimulated(self):
        _,ref=self.call()
        a,_=self.call(5,ref);b,_=self.call(5,ref,room=22.15)
        self.assertTrue(a['plan_stable']);self.assertTrue(b['plan_stable'])
        self.assertNotEqual(a['curve'][0]['temperature'],b['curve'][0]['temperature'])
    def test_cumulative_measurement_change_reoptimises(self):
        _,ref=self.call()
        q,newref=self.call(10,ref,room=21.8)
        self.assertFalse(q['plan_stable']);self.assertNotEqual(ref,newref)
    def test_new_outage_invalidates_reserved_morning_immediately(self):
        _,ref=self.call()
        a=self.now.replace(day=13,hour=6,minute=0,second=0).timestamp()
        settings=dict(self.settings,outage_schedule=dict(valid=True,known_until=a+86400,
            outages=[dict(start=a,end=a+3600)],slots=[]))
        q,_=self.call(5,ref,settings=settings)
        self.assertFalse(q['plan_stable'])
        self.assertTrue(all(s['end']<=a or s['start']>=a+3600 for s in q['heat_segments']))
    def test_upper_temperature_bypasses_stability(self):
        _,ref=self.call();q,_=self.call(5,ref,room=23.)
        self.assertFalse(q['plan_stable']);self.assertFalse(q['heat'])
    def test_fireplace_and_weather_change_bypass_stability(self):
        _,ref=self.call()
        q,_=self.call(5,ref,fire=5);self.assertFalse(q['plan_stable'])
        weather=[dict(x,temperature=x['temperature']-3) for x in self.x['weather']]
        q,_=self.call(5,ref,weather=weather);self.assertFalse(q['plan_stable'])
    def test_settings_change_bypasses_stability(self):
        _,ref=self.call()
        q,_=self.call(5,ref,settings=dict(self.settings,end='06:00'))
        self.assertFalse(q['plan_stable'])
    def test_replay_energy_is_sum_of_live_modulated_intervals(self):
        _,ref=self.call();q,_=self.call(5,ref)
        self.assertAlmostEqual(q['energy'],sum(s['kwh'] for s in q['heat_segments']),places=2)
        self.assertAlmostEqual(q['energy'],sum(q['session_kwh'].values()),places=2)

if __name__=='__main__':unittest.main()
