"""Svitlo no-schedule is clear only with positive, fresh source evidence."""
import unittest
from datetime import datetime, timedelta
from heating_outages import schedule, covered, blocked
from heating_model import DEFAULT_MODEL, plan
from heating_stability import replay, stable_plan


class OutageModeTests(unittest.TestCase):
    def setUp(self):
        self.now=datetime.fromisoformat('2026-10-18T04:30:00+03:00')
        self.settings=dict(start='00:00',end='23:59',strategy='Комфорт',
                           target=22,lower=21.5,upper=22.5,night_price=None,day_price=None)

    def source(self,state='No data',now_status='nosched',**attrs):
        return dict(state=state,attributes=dict(region='kiivska-oblast',queue='3.1',
                    updated=self.now.isoformat(),now_status=now_status,
                    today_48half=[],tomorrow_48half=[],**attrs))

    def read(self,record=None,emergency='off',**kwargs):
        return schedule(record or self.source(),self.now,emergency=emergency,**kwargs)

    def test_fresh_no_schedule_is_normal_with_virtual_coverage(self):
        for state,status in [('No data','nosched'),('Grid ON','on'),('off','nosched')]:
            with self.subTest(state=state):
                data=self.read(self.source(state,status),horizon_seconds=36*3600)
                self.assertEqual(data['mode'],'normal');self.assertTrue(data['valid'])
                self.assertTrue(data['source_available']);self.assertTrue(data['source_fresh'])
                self.assertEqual(data['outages'],[]);self.assertIsNone(data['warning'])
                self.assertTrue(covered(self.now.timestamp(),self.now.timestamp()+36*3600,data['slots']))
                self.assertFalse(blocked(self.now.timestamp(),self.now.timestamp()+36*3600,data['outages']))

    def test_missing_arrays_are_also_normal_with_positive_evidence(self):
        record=self.source();record['attributes'].pop('today_48half');record['attributes'].pop('tomorrow_48half')
        self.assertEqual(self.read(record)['mode'],'normal')

    def test_normal_covers_new_and_retained_plans_without_conditional_heat(self):
        data=self.read();settings=dict(self.settings,outage_schedule=data)
        args=(self.now,21,24,5,0,DEFAULT_MODEL,[],settings)
        p=plan(*args);self.assertTrue(p['heat_segments'])
        for result in (p,replay(*args,[],False)):
            self.assertTrue(result['schedule_complete'])
            self.assertTrue(all(row['schedule_known'] for row in result['curve']))
            self.assertTrue(all(not s['conditional'] for s in result['heat_segments']))
            self.assertEqual(result['conditional_heat_minutes'],0)
            self.assertEqual(result['outage_mode'],'normal')
        # A held plan's rolling horizon must not outgrow the original virtual end.
        later=self.now+timedelta(minutes=5)
        result=replay(later,*args[1:],[],False)
        self.assertTrue(result['schedule_complete'])
        self.assertEqual(result['schedule_known_until'],later.timestamp()+86400)

    def test_active_schedule_and_emergency_advisory_are_preserved(self):
        record=self.source('Emergency','on');record['attributes']['today_48half']=['on']*48
        record['attributes']['today_48half'][12:14]=['off','off']
        data=self.read(record,emergency='on')
        self.assertEqual(data['mode'],'scheduled');self.assertTrue(data['valid'])
        self.assertTrue(data['emergency']);self.assertIn('Аварійні',data['warning'])
        p=plan(self.now,21,24,5,0,DEFAULT_MODEL,[],dict(self.settings,outage_schedule=data))
        self.assertTrue(all(not blocked(s['start'],s['end'],data['outages']) for s in p['heat_segments']))
        self.assertFalse(p['schedule_complete'])  # Tomorrow is still unpublished.

    def test_emergency_without_schedule_is_unknown(self):
        for state in ('Emergency','No data'):
            data=self.read(self.source(state),emergency='on')
            self.assertEqual(data['mode'],'unknown');self.assertFalse(data['valid'])
            self.assertTrue(data['emergency']);self.assertIn('Аварійні',data['warning'])

    def test_unavailable_unknown_stale_and_wrong_location_are_unknown(self):
        cases=[]
        for state in ('unavailable','unknown',None):cases.append(self.source(state))
        for key,value in [('queue','3.2'),('region','other'),('updated',(self.now-timedelta(hours=4)).isoformat()),
                          ('updated','bad'),('today_48half',['maybe']*48),('tomorrow_48half','bad')]:
            record=self.source();record['attributes'][key]=value;cases.append(record)
        for record in cases:
            with self.subTest(record=record):
                data=self.read(record)
                self.assertEqual(data['mode'],'unknown');self.assertFalse(data['valid'])
                self.assertTrue(data['warning']);self.assertEqual(data['slots'],[])

    def test_no_data_without_nosched_or_with_unconfirmed_emergency_is_unknown(self):
        for state,status,emergency in [('No data',None,'off'),('Grid OFF','off','off'),
                                       ('Grid ON','off','off'),('Normal','nosched','off'),
                                       ('No data','nosched','unknown'),('No data','nosched','unavailable')]:
            with self.subTest(state=state,status=status,emergency=emergency):
                self.assertEqual(self.read(self.source(state,status),emergency)['mode'],'unknown')
        failed=self.source('No data','on');failed['attributes']['today_48half']=['on']*48
        self.assertEqual(self.read(failed)['mode'],'unknown')

    def test_legacy_config_without_emergency_remains_safe(self):
        self.assertEqual(schedule(self.source(),self.now)['mode'],'unknown')
        record=self.source('Grid ON','on');record['attributes']['today_48half']=['on']*48
        self.assertEqual(schedule(record,self.now)['mode'],'scheduled')

    def test_transitions_replace_old_outages_and_revalidate_held_plan(self):
        normal=self.read()
        args=(self.now,21,24,5,0,DEFAULT_MODEL,[])
        initial,ref=stable_plan(*args,dict(self.settings,outage_schedule=normal))
        first=initial['heat_segments'][0]
        record=self.source('Grid ON','on');record['attributes']['today_48half']=['on']*48
        index=int((first['start']-self.now.replace(hour=0,minute=0).timestamp())/1800)
        record['attributes']['today_48half'][index]='off'
        scheduled=self.read(record)
        p,_=stable_plan(*args,dict(self.settings,outage_schedule=scheduled),previous=ref)
        self.assertFalse(p['plan_stable'])
        self.assertTrue(all(not blocked(s['start'],s['end'],scheduled['outages']) for s in p['heat_segments']))
        clear=self.read()
        self.assertEqual(clear['mode'],'normal');self.assertEqual(clear['outages'],[])
        self.assertIsNone(clear['warning'])

    def test_tomorrow_only_does_not_claim_the_missing_today_as_known(self):
        record=self.source('Grid ON','on');record['attributes']['tomorrow_48half']=['on']*48
        data=self.read(record)
        p=plan(self.now,21,24,5,0,DEFAULT_MODEL,[],dict(self.settings,outage_schedule=data))
        self.assertEqual(data['mode'],'scheduled');self.assertFalse(p['schedule_complete'])
        self.assertFalse(p['curve'][0]['schedule_known'])
        self.assertTrue(any(s['conditional'] for s in p['heat_segments']))

