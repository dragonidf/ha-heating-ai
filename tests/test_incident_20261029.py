"""Synthetic contracts for the incident path; no private household snapshots."""
import copy
import unittest
from datetime import datetime,timedelta
from unittest.mock import patch
from zoneinfo import ZoneInfo
from heating_model import DEFAULT_MODEL
from heating_recovery import HeatingRecovery
from test_adapter_modulation import HeatingClimate
import test_planned_execution as execution_fixture


class PredictionContractTests(unittest.TestCase):
    def app(self):
        app=HeatingRecovery();app.memory={};app.model=dict(DEFAULT_MODEL,version=6)
        app.at=datetime(2026,10,29,12,tzinfo=ZoneInfo('Europe/Kiev'))
        app.now=lambda:app.at;app.power_source='balance';app.grid='unknown'
        app.grid_state=lambda:app.grid
        app.raw=lambda key:'on' if key=='armed' else 'Автоматичне керування'
        app.val=lambda key:100.
        app.log=lambda *a,**k:None;app.publish=lambda *a,**k:None
        return app

    def point(self,app,**changes):
        return dict(ts=app.at.timestamp()+3600,hours=6.,temperature=21.4,
            model=dict(app.model),valid=True,segments=[],start_energy=100.,expected_energy=0.,**changes)

    def test_null_observed_under_unknown_grid_does_not_crash_or_gain_validity(self):
        app=self.app();app.memory['reserve_predictions']=[self.point(app,observed=None)]
        app.validate_reserve_predictions(app.at,21.4,0.,True)
        self.assertEqual(app.memory['reserve_predictions'],[])
        self.assertEqual(app.memory['reserve_errors'],[])
        app.grid='on';app.validate_reserve_predictions(app.at+timedelta(seconds=15),21.4,0.,True)
        self.assertEqual(app.memory['reserve_errors'],[])

    def test_missing_empty_and_null_helper_allow_valid_frozen_check(self):
        for change in ({},{'observed':None},{'observed':{}}):
            app=self.app();app.grid='on'
            app.memory['reserve_predictions']=[self.point(app,**change)]
            app.validate_reserve_predictions(app.at,21.4,0.,True)
            self.assertEqual(len(app.memory['reserve_predictions']),1)

    def test_malformed_records_are_locally_rejected_without_losing_good_prediction(self):
        for bad in (None,[],{'ts':None},{'observed':{'valid':False}},
                    {'observed':{'at':'bad','valid':True}}, {'observed':'invalid'}):
            app=self.app();app.grid='on'
            broken=self.point(app,**bad) if isinstance(bad,dict) and 'observed' in bad else bad
            good=self.point(app)
            app.memory['reserve_predictions']=[broken,copy.deepcopy(good)]
            app.validate_reserve_predictions(app.at,21.4,0.,True)
            self.assertEqual(len(app.memory['reserve_predictions']),1)
            self.assertEqual(app.memory['reserve_predictions'][0]['ts'],good['ts'])
            self.assertTrue(app.memory['forecast_validation_rejections'])
            self.assertTrue(app.memory['validation_quarantine'])
            self.assertEqual(app.memory['reserve_errors'],[])

    def test_invalid_observed_numbers_and_types_cannot_certify_a_forecast(self):
        baseline=dict(at=1.,room=21.,floor=23.,outside=10.,fire=0.,power_kw=0.,request_seconds=0.,valid=True,energy=100.)
        for field,value in (('at',None),('room',float('nan')),('floor',True),('request_seconds',-1),('valid','true')):
            app=self.app();app.grid='on';observed=dict(baseline,**{field:value})
            app.memory['reserve_predictions']=[self.point(app,observed=observed),self.point(app)]
            app.validate_reserve_predictions(app.at,21.4,0.,True)
            self.assertEqual(len(app.memory['reserve_predictions']),1)
            self.assertEqual(app.memory['reserve_errors'],[])

    def test_explicit_null_queue_and_forecast_metadata_are_not_successful_checks(self):
        app=self.app();app.memory['reserve_predictions']=None;app.grid='on'
        app.validate_reserve_predictions(app.at,21.4,0.,True,dict(curve=[],morning_reserve=None))
        self.assertEqual(app.memory['reserve_predictions'],[])
        self.assertEqual(app.memory['reserve_errors'],[])


class DiagnosticBoundaryTests(unittest.TestCase):
    def test_accuracy_exception_cannot_stop_planning_or_dispatch(self):
        fixture=execution_fixture.PlannedExecutionTests()
        with patch.object(HeatingClimate,'validate_reserve_predictions',side_effect=ValueError('synthetic validation failure')), \
             patch.object(HeatingClimate,'log',lambda *a,**k:None,create=True):
            actions,status=fixture.cycle()
        self.assertEqual(actions,['heat'])
        self.assertEqual(status['reason'],'Заплановано нагрів')

    def test_one_hour_of_critical_errors_is_aggregated_and_retry_does_not_slide(self):
        from test_fallback_recovery import FallbackRecoveryTests
        app=FallbackRecoveryTests().app();app.logs=[]
        app.log=lambda *a,**k:app.logs.append((a,k))
        def failing():raise RuntimeError('synthetic critical error')
        app.tick_inner=failing;start=app.at
        for seconds in range(0,3601,15):
            app.at=start+timedelta(seconds=seconds)
            with patch('heating_climate.time.time',return_value=app.at.timestamp()):app.tick()
        incident=app.memory['control_incident']
        self.assertEqual(incident['count'],241)
        self.assertLessEqual(len(app.logs),13)
        self.assertAlmostEqual(app.memory['temporary_fallback']['retry_at'],start.timestamp()+3900)
        self.assertEqual(app.values['thermostat'],'auto')
        self.assertEqual(app.values['armed'],'on')
        app.tick_inner=lambda:None
        with patch('heating_climate.time.time',return_value=app.at.timestamp()+15):app.tick()
        self.assertTrue(app.memory['control_incident']['active'])
        def recovered():
            app.resume_temporary_fallback();app.apply_action('off',False)
            app.verify_command(dict(expected='heat',target=21,generation=app.memory['command_generation']))
        app.tick_inner=recovered
        with patch('heating_climate.time.time',return_value=app.at.timestamp()+300):app.tick()
        self.assertFalse(app.memory['control_incident']['active'])

    def test_recorder_success_cannot_hide_fallback_health(self):
        app=PredictionContractTests().app();published=[]
        app.memory['temporary_fallback']=dict(reason='synthetic',retry_at=1.)
        app.memory['decision_recorder_status']='verified_same_state_attributes'
        app.publish=lambda entity,state,**attrs:published.append((state,attrs))
        app.publish_diagnostic_health()
        self.assertEqual(published[-1][1]['control_health'],'fallback')
        self.assertEqual(published[-1][1]['storage_health'],'healthy')


if __name__=='__main__':unittest.main()

class ExecutionEvidenceTests(unittest.TestCase):
    def test_already_matching_thermostat_confirms_recovery_without_extra_service(self):
        from test_fallback_recovery import FallbackRecoveryTests
        app=FallbackRecoveryTests().app();app.attrs['temperature']=21
        app.memory['recovering_from_fallback']=True
        app.memory['plan_execution']=dict(plan_id='synthetic',calculated_at=app.at.timestamp(),status='intended')
        with patch('heating_climate.time.time',return_value=app.at.timestamp()):app.apply_action('off',False)
        self.assertEqual(app.memory['plan_execution']['status'],'applied')
        self.assertFalse(app.memory.get('recovering_from_fallback'))
        self.assertFalse(any(s.startswith('climate/') for s,_ in app.calls))

    def test_failed_command_cannot_mark_calculated_plan_applied(self):
        from test_fallback_recovery import FallbackRecoveryTests
        app=FallbackRecoveryTests().app()
        app.memory['plan_execution']=dict(plan_id='synthetic',calculated_at=app.at.timestamp(),status='intended')
        def fail(*a,**k):raise RuntimeError('synthetic command failure')
        app.call_service=fail
        with patch('heating_climate.time.time',return_value=app.at.timestamp()),self.assertRaises(RuntimeError):app.apply_action('off',False)
        self.assertNotEqual(app.memory['plan_execution']['status'],'applied')
        self.assertNotIn('applied_plan',app.memory)

    def test_compaction_retains_incident_and_causal_fields(self):
        import json,tempfile
        from heating_recovery import DecisionJournal
        with tempfile.TemporaryDirectory() as directory:
            row=DecisionJournal(directory).append(dict(action='off',reason='Дані несвіжі',
                incident_id='synthetic',control_state='fallback',execution_state='unconfirmed',
                reason_code='stale_power',power_quality='unknown',source_timestamp=100.,payload='x'*16000))
            self.assertLessEqual(len(json.dumps(row,ensure_ascii=False).encode()),8192)
            for field in ('incident_id','control_state','execution_state','reason_code','power_quality','source_timestamp'):
                self.assertIn(field,row)
            self.assertEqual(row['reason'],'Дані несвіжі')
