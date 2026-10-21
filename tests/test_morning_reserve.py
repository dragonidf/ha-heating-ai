"""Synthetic regression inputs; these are not a replay of the 21 Oct HA incident."""
import unittest
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo
from heating_model import DEFAULT_MODEL, plan


class MorningReserveRegressionTests(unittest.TestCase):
    def setUp(self):
        self.tz=ZoneInfo('Europe/Kiev')
        self.model=dict(DEFAULT_MODEL,floor_air=.044238523742432284,
                        room_loss=.0128,floor_decay=.1728,heat_gain=.72,validated=True)
        self.settings=dict(start='23:00',end='07:00',strategy='Тільки в дозволені години',
            target=21.5,lower=21,upper=22,night_target=21,night_lower=20.5,night_upper=21.5,
            sleep_start='23:00',sleep_end='06:30',split_comfort=True,morning_min_minutes=30,
            day_price=4.32,night_price=2.16)

    def test_current_morning_is_not_replaced_by_tomorrows_horizon_slots(self):
        for hour,minute in [(5,0),(5,30),(6,0),(6,30)]:
            with self.subTest(hour=hour,minute=minute):
                now=datetime(2026,10,21,hour,minute,tzinfo=self.tz)
                result=plan(now,21.4,22,10,0,self.model,[],self.settings,9)
                end=now.replace(hour=7,minute=0).timestamp()
                reserved=sum(max(0,min(end,s['end'])-max(end-1800,s['start']))
                             for s in result['heat_segments'])
                self.assertGreaterEqual(reserved,1800-.01)

    def test_reserve_exposes_cycle_and_componentwise_daynight_deficit(self):
        now=datetime(2026,10,21,6,30,tzinfo=self.tz)
        result=plan(now,21.4,22,10,0,self.model,[],self.settings,9)
        self.assertIn('morning_reserve',result)
        reserve=result['morning_reserve']
        self.assertEqual(reserve['cycle_end'],now.replace(hour=7,minute=0).timestamp())
        self.assertEqual(reserve['next_allowed_heat_at'],now.replace(hour=23,minute=0).timestamp())
        self.assertGreaterEqual(reserve['assessment_until'],reserve['next_allowed_heat_at'])
        self.assertGreaterEqual(reserve['deficit_degree_hours'],0)
        self.assertEqual(result['horizon_hours'],24)
        self.assertEqual(len(result['curve']),48)

class ReservePolicyTests(unittest.TestCase):
    setUp=MorningReserveRegressionTests.setUp
    def test_seconds_remaining_are_not_rounded_to_completed_minimum(self):
        now=datetime(2026,10,21,6,30,2,tzinfo=self.tz)
        result=plan(now,21.4,22,10,0,self.model,[],self.settings,9)
        reserve=result['morning_reserve']
        self.assertAlmostEqual(reserve['planned_minutes'],29.97,places=2)
        self.assertGreater(reserve['minimum_unmet_minutes'],0)
        self.assertEqual(reserve['confirmed_completed_minutes'],0)
        self.assertTrue(result['heat'])

    def test_cycle_and_elapsed_horizon_across_both_dst_dates(self):
        from heating_model import heating_cycle
        for month,day,length in [(3,28,7),(10,24,9)]:
            at=datetime(2026,month,day,23,tzinfo=self.tz)
            context=heating_cycle(at,self.settings)
            self.assertEqual((context['cycle_end']-context['cycle_start'])/3600,length)
            p=plan(at,21.1,23,10,0,self.model,[],self.settings,9)
            self.assertEqual(p['curve'][-1]['ts']-at.timestamp(),86400)
            self.assertEqual(len(p['curve']),48)
            self.assertTrue(all(s['end']<=at.timestamp()+86400 for s in p['heat_segments']))

    def test_morning_reserve_reduces_coast_daytime_deficit(self):
        now=datetime(2026,10,21,6,30,tzinfo=self.tz)
        p=plan(now,21.4,22,10,0,self.model,[],self.settings,9)
        r=p['morning_reserve']
        self.assertGreater(r['no_morning_counterfactual']['deficit_degree_hours'],0)
        self.assertTrue(p['heat'])
        self.assertLess(r['deficit_degree_hours'],r['no_morning_counterfactual']['deficit_degree_hours'])
        self.assertFalse(r['reserve_sufficient'])  # cannot promise impossible comfort
        self.assertIsNotNone(r['impossibility_reason'])

    def test_horizon_validation_does_not_use_hour_mae(self):
        from heating_model import uncertainty_reserve
        settings=dict(self.settings,reserve_errors=[dict(hours=1,error=.04,validated=True)]*50)
        self.assertIsNone(uncertainty_reserve(settings,18))
        settings['reserve_errors']=[dict(hours=24,error=.8,validated=True)]*12
        self.assertEqual(uncertainty_reserve(settings,18),.25)
        self.assertIsNone(uncertainty_reserve(settings,36))

    def test_hot_air_cold_return_cannot_force_heat_and_unknown_is_honest(self):
        now=datetime(2026,10,21,6,30,tzinfo=self.tz)
        p=plan(now,23.5,21,20,12,self.model,[],self.settings,9)
        self.assertFalse(p['heat'])
        self.assertEqual(p['morning_reserve']['planned_minutes'],0)
        self.assertTrue(p['morning_reserve']['conditional'])
        self.assertIsNotNone(p['morning_reserve']['cancellation_reason'])

    def test_night_updates_and_restart_keep_current_morning_date(self):
        import json
        from heating_stability import stable_plan,expected
        at=datetime(2026,10,20,23,tzinfo=self.tz)
        p,ref=stable_plan(at,21.1,23,10,0,self.model,[],self.settings,9)
        for offset in range(5,481,5):
            now=at+timedelta(minutes=offset)
            if now.hour==7:break
            p,ref=stable_plan(now,expected(ref,now.timestamp(),'temperature','room'),
                expected(ref,now.timestamp(),'return','floor'),10,0,self.model,[],self.settings,9,ref)
            # Simulated persisted state.json reconstruction, no gap credit.
            if now.strftime('%H:%M') in ('05:45','06:30'):
                ref=json.loads(json.dumps(ref))
            reserve=p['morning_reserve']
            self.assertEqual(datetime.fromtimestamp(reserve['cycle_end'],self.tz).date().isoformat(),'2026-10-21')
            if now.hour==6 and now.minute<=30:
                self.assertTrue(any(s['start']<reserve['cycle_end'] and s['end']>now.timestamp()
                                    for s in p['heat_segments']))

    def test_safe_reserve_covers_day_and_reports_validated_sufficiency(self):
        now=datetime(2026,10,21,6,30,tzinfo=self.tz)
        model=dict(self.model,room_loss=.006)
        weather=[dict(ts=now.timestamp()+i*3600,temperature=10) for i in range(40)]
        settings=dict(self.settings,outage_schedule=dict(mode='normal',valid=True,outages=[]),
            reserve_errors=[dict(hours=24,error=.02,validated=True)]*12)
        p=plan(now,21.4,22,10,0,model,weather,settings,9)
        r=p['morning_reserve']
        self.assertGreater(r['no_morning_counterfactual']['deficit_degree_hours'],0)
        self.assertEqual(r['deficit_degree_hours'],0)
        self.assertTrue(r['reserve_sufficient'])
        self.assertLessEqual(p['maximum'],settings['upper'])

    def test_unknown_current_heat_cannot_be_certified_by_known_next_window(self):
        now=datetime(2026,10,21,6,30,tzinfo=self.tz)
        next_at=now.replace(hour=23,minute=0).timestamp()
        settings=dict(self.settings,reserve_errors=[dict(hours=24,error=.02,validated=True)]*12,
            outage_schedule=dict(mode='scheduled',valid=True,outages=[],known_until=next_at+86400,
                slots=[dict(start=next_at,end=next_at+86400,off=False)]))
        weather=[dict(ts=now.timestamp()+i*3600,temperature=10) for i in range(40)]
        p=plan(now,21.4,22,10,0,dict(self.model,room_loss=.006),weather,settings,9)
        self.assertTrue(p['morning_reserve']['predicted_sufficient'])
        self.assertTrue(p['morning_reserve']['conditional'])
        self.assertIsNone(p['morning_reserve']['reserve_sufficient'])


if __name__=='__main__':
    unittest.main()
