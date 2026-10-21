import unittest,json
from pathlib import Path
from datetime import datetime,timedelta
from heating_model import plan,comfort_at,coast_exceeds_profile
from heating_stability import stable_plan,expected

class DayNightTests(unittest.TestCase):
    def setUp(self):
        self.x=json.loads(Path(__file__).with_name('audit_case.json').read_text())
        self.at=datetime.fromisoformat('2026-10-12T23:12:00+03:00')
        self.s=dict(start='23:00',end='07:00',strategy='Тільки в дозволені години',
            target=22,lower=21.5,upper=22.5,night_target=21.5,night_lower=21,night_upper=22,
            sleep_start='23:00',sleep_end='07:00',night_price=1.32,day_price=2.64,
            split_comfort=True,morning_min_minutes=30,modulation_profile=self.x['profile'])
    def test_day_sleep_bounds_and_midnight(self):
        for hour,zone,target in [(22,'День',22),(23,'Сон',21.5),(0,'Сон',21.5),(6,'Сон',21.5),(7,'День',22)]:
            t,lo,hi,z=comfort_at(self.at.replace(hour=hour),self.s)
            self.assertEqual((z,t),(zone,target));self.assertEqual(hi-lo,1)
    def test_sleep_times_are_independent_of_tariff(self):
        s=dict(self.s,sleep_start='22:00',sleep_end='08:00')
        self.assertEqual(comfort_at(self.at.replace(hour=7),s)[3],'Сон')
    def test_morning_inertia_uses_day_upper_after_waking(self):
        self.assertFalse(coast_exceeds_profile(self.at.replace(day=13,hour=6,minute=30),
            21.7,30,10,0,self.x['model'],self.s))
        self.assertTrue(coast_exceeds_profile(self.at,21.7,30,10,0,self.x['model'],self.s))
    def test_screenshot_case_keeps_morning_and_prefers_it(self):
        for minute in (0,5,12,17,25,35,45,55):
            p=plan(self.at.replace(minute=minute),22,23.5,10,0,self.x['model'],self.x['weather'],self.s,3)
            self.assertTrue(p['morning_runs_minutes'],minute)
            self.assertGreater(p['session_kwh']['morning'],p['session_kwh']['evening'],minute)
            # A long night session may cross 05:00; its morning-only tail can
            # be shorter than 30 min without being a short physical session.
            self.assertGreaterEqual(p['morning_reserve']['planned_minutes'],30)
            runs=[]
            for seg in p['heat_segments']:
                if runs and seg['start']<=runs[-1][1]+1:runs[-1][1]=seg['end']
                else:runs.append([seg['start'],seg['end']])
            self.assertTrue(all((b-a)/60>=30-.001 for a,b in runs
                if datetime.fromtimestamp(a,self.at.tzinfo).hour<7 and
                   datetime.fromtimestamp(b-.001,self.at.tzinfo).hour>=5))
    def test_expected_warming_does_not_replan(self):
        p,ref=stable_plan(self.at,22,23.5,10,0,self.x['model'],self.x['weather'],self.s,3)
        def groups(p):
            g=[]
            for s in p['heat_segments']:
                if g and abs(g[-1][1]-s['start'])<.001:g[-1][1]=s['end']
                else:g.append([s['start'],s['end']])
            return g
        # The internal assessment can extend beyond the public 24 h. As the
        # chart rolls, compare it with the same complete persisted sessions.
        initial=groups({'heat_segments':ref['segments']})
        for minute in range(5,85,5):
            now=self.at+timedelta(minutes=minute)
            room=expected(ref,now.timestamp(),'temperature','room')
            floor=expected(ref,now.timestamp(),'return','floor')
            q,newref=stable_plan(now,room,floor,10,0,self.x['model'],self.x['weather'],self.s,3,ref)
            self.assertTrue(q['plan_stable'],minute)
            self.assertEqual(ref,newref)
            self.assertEqual(groups(q),[[max(a,now.timestamp()),min(b,now.timestamp()+86400)]
                for a,b in initial if b>now.timestamp() and a<now.timestamp()+86400])
            self.assertTrue(q['morning_runs_minutes'])
    def test_night_heat_never_predicts_above_sleep_upper(self):
        p=plan(self.at,21.8,24,10,0,self.x['model'],self.x['weather'],self.s,3)
        for r in p['curve']:
            if r['heat_segments'] and r['comfort_zone']=='Сон':self.assertLessEqual(r['temperature'],22)
    def test_morning_outage_still_blocks_priority(self):
        a=self.at.replace(day=13,hour=5,minute=0).timestamp()
        s=dict(self.s,outage_schedule=dict(valid=True,known_until=a+86400,
            outages=[dict(start=a,end=a+7200)],slots=[]))
        p=plan(self.at,22,23.5,10,0,self.x['model'],self.x['weather'],s,3)
        self.assertFalse(p['morning_runs_minutes'])
        self.assertTrue(all(h['end']<=a or h['start']>=a+7200 for h in p['heat_segments']))
    def test_fireplace_does_not_force_unneeded_heat_now(self):
        p=plan(self.at,23,30,15,8,self.x['model'],self.x['weather'],self.s,3)
        self.assertFalse(p['heat'])
    def test_profile_change_rebuilds_plan(self):
        _,ref=stable_plan(self.at,22,23.5,10,0,self.x['model'],self.x['weather'],self.s,3)
        s=dict(self.s,night_upper=21.8)
        p,_=stable_plan(self.at+timedelta(minutes=5),22,23.5,10,0,self.x['model'],self.x['weather'],s,3,ref)
        self.assertFalse(p['plan_stable'])

if __name__=='__main__':unittest.main()
