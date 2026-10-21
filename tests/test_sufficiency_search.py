"""Synthetic review-section-4 regression; not a full live cache replay."""
import json
import unittest
from copy import deepcopy
from unittest.mock import patch
from pathlib import Path
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo
from heating_model import plan,comfort_at,SEARCH_POLICY_VERSION,search_input_key
from heating_stability import stable_plan,reference,replay,expected


class SufficiencySearchTests(unittest.TestCase):
    def setUp(self):
        self.x=json.loads(Path(__file__).with_name('insufficient_morning_case.json').read_text())
        self.now=datetime.fromisoformat(self.x['now']).astimezone(ZoneInfo('Europe/Kiev'))
        a=self.now.replace(day=22,hour=6,minute=0,second=0).timestamp()
        self.settings=dict(start='23:00',end='07:00',strategy='Тільки в дозволені години',
            target=21.5,lower=21.,upper=22.,night_target=21.,night_lower=20.5,night_upper=21.5,
            sleep_start='23:00',sleep_end='06:30',split_comfort=True,morning_min_minutes=30,
            day_price=4.32,night_price=2.16,modulation_profile=self.x['profile'],
            outage_schedule=dict(valid=True,mode='scheduled',known_until=a+86400,
                slots=[dict(start=self.now.timestamp(),end=a+86400,off=False)],
                outages=[dict(start=a,end=a+5400)]))

    def run_plan(self,**settings):
        x=self.x
        return plan(self.now,x['room'],x['floor'],x['outside'],x['fire'],x['model'],[],
                    dict(self.settings,**settings),3.)

    def test_warm_night_with_truncated_morning_can_store_for_day(self):
        p=self.run_plan()
        self.assertEqual(p['morning_reserve']['deficit_degree_hours'],0)
        end=self.now.replace(day=22,hour=6,minute=0,second=0).timestamp()
        self.assertTrue(any(s['start']<end-3600 and s['end']>self.now.timestamp() for s in p['heat_segments']))
        self.assertTrue(all(not(end<s['end'] and s['start']<end+5400) for s in p['heat_segments']))
        for row in p['_calculation_curve']:
            for point in row['_fine']:
                _,lo,hi,_=comfort_at(datetime.fromtimestamp(point['ts']-.001,self.now.tzinfo),self.settings)
                self.assertGreaterEqual(point['temperature'],lo-.001)
                self.assertLessEqual(point['temperature'],hi+.001)

    def stable(self,previous=None,settings=None):
        x=self.x
        return stable_plan(self.now,x['room'],x['floor'],x['outside'],x['fire'],x['model'],[],
                           settings or self.settings,3.,previous)

    def test_diagnostics_quantify_current_cycle_and_search_evidence(self):
        p=self.run_plan();r=p['morning_reserve']
        self.assertEqual(r['search_policy_version'],SEARCH_POLICY_VERSION)
        self.assertEqual(r['search_status'],'feasible_found')
        self.assertTrue(r['terminal_reserve_checked'])
        self.assertGreater(r['alternatives_evaluated'],0)
        self.assertEqual(r['day_deficit_minutes'],0)
        self.assertEqual(r['last_available_energy_at'],self.now.replace(day=22,hour=6,minute=0,second=0).timestamp())
        self.assertGreater(r['end_cycle_planned_kwh'],r['planned_kwh'])
        self.assertFalse(r['physical_impossibility_proven'])
        self.assertIn('outage_mask',[c['constraint'] for c in r['binding_constraints']])
        self.assertIsNone(r['reserve_sufficient'])  # no long-horizon validation/weather guarantee

    def test_cold_day_without_outage_can_require_earlier_heat(self):
        x=self.x;hour=self.now.replace(minute=0,second=0)
        weather=[dict(ts=(hour+timedelta(hours=i)).timestamp(),
            temperature=12.3 if (hour+timedelta(hours=i)).hour<7 or
                               (hour+timedelta(hours=i)).hour>=23 else 6.) for i in range(37)]
        settings=dict(self.settings,outage_schedule=dict(mode='normal',valid=True,outages=[]))
        p=plan(self.now,x['room'],x['floor'],x['outside'],0,x['model'],weather,settings,3.)
        morning=self.now.replace(day=22,hour=5,minute=0,second=0).timestamp()
        self.assertTrue(any(s['start']<morning for s in p['heat_segments']))
        self.assertTrue(all(23<=datetime.fromtimestamp(s['start'],self.now.tzinfo).hour or
                            datetime.fromtimestamp(s['start'],self.now.tzinfo).hour<7 for s in p['heat_segments']))
        self.assertLess(p['morning_reserve']['deficit_degree_hours'],
                        p['morning_reserve']['no_morning_counterfactual']['deficit_degree_hours'])
        self.assertTrue(p['expanded_search_performed'])
        self.assertLess(p['morning_reserve']['deficit_degree_hours'],p['initial_deficit_degree_hours'])
        self.assertEqual(len(p['search_passes']),3)

    def test_short_morning_does_not_credit_unreceived_energy(self):
        a=self.now.replace(day=22,hour=5,minute=20,second=0).timestamp()
        p=self.run_plan(outage_schedule=dict(valid=True,mode='scheduled',known_until=a+86400,
             slots=[dict(start=self.now.timestamp(),end=a+86400,off=False)],outages=[dict(start=a,end=a+7800)]))
        r=p['morning_reserve']
        self.assertLess(r['planned_minutes'],30)
        self.assertGreater(r['minimum_unmet_minutes'],0)
        self.assertEqual(r['confirmed_completed_minutes'],0)
        self.assertIsNone(r['confirmed_kwh'])
        self.assertTrue(any(s['start']<a-1200 for s in p['heat_segments']))
        self.assertTrue(all(not(a<s['end'] and s['start']<a+7800) for s in p['heat_segments']))

    def test_old_search_policy_is_invalidated_after_persisted_restart(self):
        _,ref=self.stable()
        old=json.loads(json.dumps(ref));old['policy'].pop('search_policy_version')
        old.pop('search',None);old.pop('search_check_key',None)
        result,new=self.stable(old)
        self.assertFalse(result['plan_stable'])
        self.assertNotEqual(result['plan_id'],old['plan_id'])
        self.assertEqual(result['parent_plan_id'],old['plan_id'])
        self.assertEqual(new['policy']['search_policy_version'],SEARCH_POLICY_VERSION)
        self.assertEqual(result['morning_reserve']['deficit_degree_hours'],0)

    def test_unchecked_deficient_reference_is_improved_without_measurement_drift(self):
        p=self.run_plan()
        cutoff=self.now.replace(day=22,hour=5,minute=0,second=0).timestamp()
        # Make a valid old-policy style plan that omits its necessary early prefix.
        segments=[s for s in p['heat_segments'] if s['start']>=cutoff]
        old_result=replay(self.now,self.x['room'],self.x['floor'],self.x['outside'],0,
                          self.x['model'],[],self.settings,segments,True,3.)
        self.assertGreater(old_result['morning_reserve']['deficit_degree_hours'],0)
        old_result['plan_id']='deficient-reference'
        old=reference(self.now,self.x['room'],self.x['floor'],self.x['outside'],0,
                      self.x['model'],[],self.settings,old_result)
        with patch('heating_stability.optimise',wraps=plan) as optimise:
            result,new=self.stable(old)
        self.assertEqual(optimise.call_count,1)
        self.assertFalse(result['plan_stable'])
        self.assertEqual(result['parent_plan_id'],'deficient-reference')
        self.assertEqual(new['morning_reserve']['deficit_degree_hours'],0)

    def test_identical_deficient_inputs_are_checked_once_and_cache_survives_restart(self):
        self.now=self.now.replace(day=22,hour=6,minute=40,second=0)
        self.settings['outage_schedule']=dict(mode='normal',valid=True,outages=[])
        p,ref=self.stable()
        self.assertGreater(p['morning_reserve']['deficit_degree_hours'],0)
        self.assertTrue(p['expanded_search_performed'])
        self.assertEqual(len(p['search_passes']),3)
        ref.pop('search_check_key')  # compatible older/unchecked persisted reference
        with patch('heating_stability.optimise',wraps=plan) as optimise:
            q,checked=self.stable(ref)
            restored=json.loads(json.dumps(checked))
            again,_=self.stable(restored)
        self.assertEqual(optimise.call_count,1)
        self.assertFalse(q['plan_stable'])  # unchecked non-reserved fallback is reconsidered once
        self.assertTrue(again['plan_stable'])
        self.assertEqual(q['plan_id'],again['plan_id'])
        self.assertEqual(q['search_check_key'],checked['search_check_key'])
        self.assertGreater(q['morning_reserve']['day_deficit_minutes'],0)
        self.assertGreater(q['morning_reserve']['day_max_shortfall'],0)
        self.assertIn('фізичну неможливість не доведено',q['morning_reserve']['impossibility_reason'])

        # A reserved deficient reference with no better alternative retains its ID,
        # even without measurement drift; the check is persisted and not repeated.
        ref['split']=True
        with patch('heating_stability.optimise',wraps=plan) as optimise:
            held,checked=self.stable(ref)
            self.now+=timedelta(seconds=15)
            tick,_=self.stable(json.loads(json.dumps(checked)))
        self.assertEqual(optimise.call_count,1)
        self.assertTrue(held['plan_stable']);self.assertTrue(tick['plan_stable'])
        self.assertEqual(held['plan_id'],ref['plan_id'])
        self.assertIn('перевірку збережено',held['stability_reason'])

    def test_search_key_tracks_weather_outages_model_and_validation(self):
        x=self.x
        def key(s=None,m=None,w=None):
            return search_input_key(self.now,x['room'],x['floor'],x['outside'],0,
                                    m or x['model'],w or [],s or self.settings)
        baseline=key()
        for s,m,w in [(dict(self.settings,upper=22.1),None,None),
                      (dict(self.settings,outage_schedule=dict(mode='unknown',valid=False)),None,None),
                      (dict(self.settings,reserve_errors=[dict(hours=24,error=.1,validated=True)]),None,None),
                      (None,dict(x['model'],heat_gain=.7),None),
                      (None,None,[dict(ts=self.now.timestamp(),temperature=5)])]:
            self.assertNotEqual(baseline,key(s,m,w))

    def test_hot_fire_and_solar_do_not_force_minimum_heat(self):
        self.x.update(room=23.5,floor=24.,outside=22.,fire=12.)
        p=self.run_plan(environment=dict(solar=dict(coefficient=.6,validated=True)))
        self.assertFalse(p['heat'])
        self.assertEqual(p['morning_reserve']['planned_minutes'],0)
        self.assertEqual(p['morning_reserve']['confirmed_completed_minutes'],0)

    def test_modulated_slow_floor_with_unknown_inputs_stays_conditional(self):
        self.now=self.now.replace(day=22,hour=6,minute=40,second=0)
        self.x['model'].update(floor_air=.025,floor_decay=.05)
        self.settings['outage_schedule']=dict(valid=False,mode='unknown')
        for kw in (3.,6.,9.):
            with self.subTest(kw=kw):
                profile=deepcopy(self.x['profile'])
                for band in profile['bands']:band['power_kw']=kw
                p=self.run_plan(modulation_profile=profile)
                r=p['morning_reserve']
                self.assertFalse(r['reserve_sufficient'])
                self.assertTrue(r['conditional'])
                self.assertFalse(p['weather_complete']);self.assertFalse(p['schedule_complete'])
                self.assertGreater(r['deficit_degree_hours'],0)
                self.assertTrue(all(s['power_kw']<=kw for s in p['heat_segments']))

    def test_fine_reference_trajectory_is_used_without_rounding(self):
        _,ref=self.stable()
        point=ref['curve'][1]['_fine'][2]
        self.assertEqual(expected(ref,point['ts'],'temperature','room'),point['temperature'])
        self.assertEqual(expected(ref,point['ts'],'return','floor'),point['return'])
