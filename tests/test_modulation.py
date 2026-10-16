import unittest
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo
from heating_model import learn_modulation, modulation_power, plan, _plan, DEFAULT_MODEL

class ModulationTests(unittest.TestCase):
    def rows(self,temp=28,power=3,n=13,tracked=True):
        return [dict(ts=1000+i*300,room=22,return_=temp,**{'return':temp},power=power,
            energy=10+i*power/12,quality=True,measurement_version=3,power_source='balance',
            power_estimated=False,**(dict(heating_requested=True,heat_command_at=800,grid='on') if tracked else {}))
            for i in range(n)]
    def test_three_kw_is_learned_from_actual_energy(self):
        profile=learn_modulation(self.rows())
        self.assertEqual(profile['intervals'],12)
        self.assertEqual(profile['minutes'],60)
        self.assertEqual(modulation_power(28,profile),3)
        self.assertEqual(profile,learn_modulation(self.rows()))
    def test_cold_maximum_and_warm_minimum(self):
        cold=self.rows(24,9)
        warm=self.rows(32,3)
        for row in warm:
            row['ts']+=10000
        p=learn_modulation(cold+warm)
        self.assertEqual(modulation_power(24,p),9)
        self.assertEqual(modulation_power(32,p),3)
        self.assertEqual(modulation_power(45,p),3)
    def test_invalid_and_estimated_sources_do_not_train(self):
        for overrides in [dict(power_source='fallback_manual'),dict(power_estimated=True),
                          dict(quality=False),dict(grid='off'),dict(measurement_version=2)]:
            rows=self.rows()
            for r in rows:r.update(overrides)
            self.assertEqual(learn_modulation(rows)['intervals'],0,overrides)
    def test_resets_gaps_startup_and_stopped_requests_do_not_train(self):
        for overrides in [dict(heating_requested=False),dict(heat_command_at=1100)]:
            rows=self.rows(n=2)
            for r in rows:r.update(overrides)
            self.assertEqual(learn_modulation(rows)['intervals'],0)
        rows=self.rows(n=2);rows[1]['energy']=0
        self.assertEqual(learn_modulation(rows)['intervals'],0)
        rows=self.rows(n=2);rows[1]['ts']+=10000
        self.assertEqual(learn_modulation(rows)['intervals'],0)
        rows=self.rows(n=2);rows[1]['heat_command_at']=900
        self.assertEqual(learn_modulation(rows)['intervals'],0)
    def test_zero_duty_only_counts_when_demand_is_known(self):
        self.assertEqual(learn_modulation(self.rows(power=0))['intervals'],12)
        self.assertEqual(learn_modulation(self.rows(power=0,tracked=False))['intervals'],0)
    def test_energy_average_overrides_instantaneous_stage(self):
        rows=self.rows(power=3)
        for r in rows:r['power']=9
        self.assertEqual(modulation_power(28,learn_modulation(rows)),3)
    def test_fit_does_not_increase_power_with_warmer_return(self):
        rows=self.rows(24,3)+[dict(r,ts=r['ts']+10000) for r in self.rows(26,9)]
        p=learn_modulation(rows)
        self.assertGreaterEqual(modulation_power(24,p),modulation_power(26,p))
    def test_live_anchor_expires_and_does_not_poison_far_forecast(self):
        p=learn_modulation(self.rows(power=9))
        a=dict(return_c=28,power_kw=3)
        self.assertEqual(modulation_power(28,p,anchor=a),3)
        self.assertGreater(modulation_power(28,p,anchor=a,ahead_minutes=120),8.99)
        self.assertEqual(modulation_power(31,p,anchor=a),9)
    def setUp(self):
        self.now=datetime(2026,10,12,23,0,tzinfo=ZoneInfo('Europe/Kiev'))
        self.model=dict(DEFAULT_MODEL,floor_air=.0442385,room_loss=.0128,floor_decay=.1728,heat_gain=.72)
        self.settings=dict(start='23:00',end='07:00',strategy='Тільки в дозволені години',
            target=22,lower=21.5,upper=22.5,night_price=2.16,day_price=4.32,
            split_comfort=True,morning_min_minutes=30)
    def test_half_hour_at_three_kw_delivers_one_and_half_kwh(self):
        settings=dict(self.settings,modulation_profile=learn_modulation([]))
        p=_plan(self.now,20,22,5,0,self.model,[],settings,hours_override=.5)
        self.assertTrue(p['heat'])
        self.assertEqual(p['energy'],1.5)
        self.assertAlmostEqual(sum(s['kwh'] for s in p['curve'][0]['heat_segments']),1.5)
    def test_three_kw_requires_longer_heat_than_nine(self):
        at=self.now.replace(hour=17,minute=40)
        slow=plan(at,22.1,22.4,10,0,self.model,[],dict(self.settings,modulation_profile=learn_modulation([])))
        fast=plan(at,22.1,22.4,10,0,self.model,[],self.settings)
        duration=lambda p:sum(s['end']-s['start'] for s in p['heat_segments'])
        self.assertGreater(duration(slow),duration(fast))
        self.assertAlmostEqual(sum(s['kwh'] for s in slow['heat_segments']),slow['energy'],places=2)
        self.assertAlmostEqual(sum(slow['session_kwh'].values()),slow['energy'],places=2)
        self.assertTrue(all(m>=30 for m in slow['morning_runs_minutes']))
        self.assertTrue(all(datetime.fromtimestamp(s['start'],at.tzinfo).hour>=23 or
                            datetime.fromtimestamp(s['end']-.001,at.tzinfo).hour<7 for s in slow['heat_segments']))
    def test_predicted_power_changes_as_return_warms(self):
        p=dict(bands=[dict(return_c=22,power_kw=9),dict(return_c=24,power_kw=6),dict(return_c=26,power_kw=3)])
        result=_plan(self.now,20,22,5,0,self.model,[],dict(self.settings,modulation_profile=p),hours_override=1)
        powers=[s['power_kw'] for row in result['curve'] for s in row['heat_segments']]
        self.assertGreater(powers[0],powers[-1])
    def test_modulation_never_overrides_outage_or_overheat(self):
        at=self.now.replace(hour=17,minute=40)
        outage=at.replace(hour=6,minute=0)+timedelta(days=1)
        settings=dict(self.settings,modulation_profile=learn_modulation([]),outage_schedule=dict(valid=True,
            outages=[dict(start=outage.timestamp(),end=outage.timestamp()+3600)],slots=[],known_until=at.timestamp()+86400))
        p=plan(at,22.1,22.4,10,0,self.model,[],settings)
        self.assertTrue(all(s['end']<=outage.timestamp() or s['start']>=outage.timestamp()+3600 for s in p['heat_segments']))
        hot=_plan(self.now,24,35,10,0,self.model,[],settings,hours_override=.5)
        self.assertFalse(hot['heat'])

    def test_current_case_reserves_morning_without_spending_extra(self):
        import json
        from pathlib import Path
        x=json.loads(Path(__file__).with_name('live_case.json').read_text())
        at=self.now.replace(hour=18,minute=20)
        settings=dict(self.settings,night_price=1.32,day_price=2.64,modulation_profile=x['profile'])
        args=(at,21.8,22.3,11.74,0,x['model'],x['weather'],settings,3)
        baseline=_plan(*args,hours_override=24)
        chosen=plan(*args)
        self.assertTrue(chosen['split_comfort_applied'])
        self.assertGreater(chosen['session_kwh']['morning'],0)
        self.assertGreater(chosen['session_kwh']['evening'],0)
        self.assertLessEqual(chosen['energy'],baseline['energy']+.01)
        self.assertGreaterEqual(chosen['minimum'],baseline['minimum']-.01)
        self.assertLess(sum(s['end']-s['start'] for s in chosen['heat_segments'] if
            datetime.fromtimestamp(s['start'],at.tzinfo).hour>=23)/60,200)

    def test_nine_kw_actual_warming_reduces_remaining_plan(self):
        from heating_model import step
        at=self.now.replace(minute=30)
        settings=dict(self.settings,modulation_profile=learn_modulation([]))
        states=[]
        for actual in (3,9):
            r,f=21.8,23
            for _ in range(6):r,f=step(r,f,10,actual,0,self.model,1/12)
            states.append((r,f))
        plans=[plan(at,r,f,10,0,self.model,[],settings) for r,f in states]
        minutes=lambda p:sum(s['end']-s['start'] for s in p['heat_segments'])/60
        self.assertLess(minutes(plans[1]),minutes(plans[0]))

if __name__=='__main__':unittest.main()
