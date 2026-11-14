"""Expensive numerical work cannot occupy AppDaemon's command callback."""
import asyncio
import copy
from datetime import timedelta
import unittest
from unittest.mock import patch
from threading import RLock
import test_fallback_recovery as recovery
import test_inlet_accounting as inlet
import test_planned_execution as execution
from heating_recovery import HeatingRecovery


class BackgroundControlTests(unittest.IsolatedAsyncioTestCase):
    def app(self):
        app=HeatingRecovery();app.calculation_lock=RLock();app.calculation_jobs={};app.submitted=[]
        app.submit_to_executor=lambda *a,**k:app.submitted.append((a,k))
        return app

    async def test_pending_work_is_single_flight_and_inputs_are_immutable(self):
        app=self.app();source={'value':6}
        self.assertIsNone(app.poll_calculation('plan','key',lambda row:row,source))
        source['value']=9
        self.assertIsNone(app.poll_calculation('plan','key',lambda row:row,source))
        self.assertEqual(len(app.submitted),1)

        args,kwargs=app.submitted[0]
        result=args[0](*args[1:])
        await kwargs['callback']({'result':result})
        ready=app.poll_calculation('plan','key',lambda row:row,source)
        self.assertEqual(ready['result'],{'value':6})

    async def test_old_result_cannot_commit_after_settings_change(self):
        app=self.app();app.poll_calculation('plan','old',lambda:6)
        args,kwargs=app.submitted[0];await kwargs['callback']({'result':args[0](*args[1:])})
        self.assertIsNone(app.poll_calculation('plan','new',lambda:9))
        self.assertEqual(len(app.submitted),2)

    async def test_failure_is_observable_and_not_a_successful_plan(self):
        app=self.app();app.poll_calculation('plan','key',lambda:1/0)
        args,kwargs=app.submitted[0];await kwargs['callback']({'result':args[0](*args[1:])})
        with self.assertRaisesRegex(RuntimeError,'division by zero'):
            app.poll_calculation('plan','key',lambda:6)

    async def test_wrong_or_duplicate_completion_does_not_replace_another_job(self):
        app=self.app();app.poll_calculation('plan','key',lambda:6)
        await app.calculation_finished({'result':{'kind':'plan','id':'other','result':9}})
        self.assertIsNone(app.poll_calculation('plan','key',lambda:6))
        self.assertEqual(len(app.submitted),1)

    async def test_timed_out_worker_is_not_duplicated_and_late_result_is_accepted_when_key_matches(self):
        app=self.app()
        with patch('heating_recovery.time.time',return_value=100.):
            app.poll_calculation('plan','key',lambda:6)
        args,kwargs=app.submitted[0]
        with patch('heating_recovery.time.time',return_value=161.):
            self.assertIsNone(app.poll_calculation('plan','key',lambda:7))
        self.assertEqual(len(app.submitted),1)
        await kwargs['callback']({'result':args[0](*args[1:])})
        with patch('heating_recovery.time.time',return_value=162.):
            ready=app.poll_calculation('plan','key',lambda:7)
        self.assertEqual(ready['result'],6)
        self.assertEqual(len(app.submitted),1)
        self.assertEqual(app.calculation_timeouts['plan']['job_id'],args[2])

    async def test_timed_out_result_with_changed_key_is_rejected_and_replaced_once(self):
        app=self.app()
        with patch('heating_recovery.time.time',return_value=100.):
            app.poll_calculation('plan','old',lambda:6)
        args,kwargs=app.submitted[0]
        with patch('heating_recovery.time.time',return_value=161.):
            self.assertIsNone(app.poll_calculation('plan','old',lambda:7))
        await kwargs['callback']({'result':args[0](*args[1:])})
        with patch('heating_recovery.time.time',return_value=162.):
            self.assertIsNone(app.poll_calculation('plan','new',lambda:9))
        self.assertEqual(len(app.submitted),2)
        self.assertEqual(app.calculation_stale_results['plan']['reason'],'changed_inputs')


class BackgroundTickSafetyTests(unittest.TestCase):
    def app(self):
        fixture=execution.PlannedExecutionTests();actions,_=fixture.cycle()
        app=fixture.created_app;app.runtime_ready=True;app.next_compute=0
        app.calculation_lock=RLock();app.calculation_jobs={};app.submitted=[]
        app.submit_to_executor=lambda *a,**k:app.submitted.append((a,k))
        actions.clear();return app,actions

    def tick(self,app):
        with patch('heating_climate.time.time',return_value=app.now().timestamp()), \
             patch('heating_climate.stable_plan',side_effect=AssertionError('Planner ran on command thread')), \
             patch('heating_climate.recommend_power',side_effect=AssertionError('Advice ran on command thread')):
            app.tick_inner()

    def test_pending_search_keeps_fast_measurements_heartbeat_and_one_job(self):
        app,actions=self.app();self.tick(app);self.tick(app)
        self.assertEqual(actions,['heat','heat'])
        self.assertEqual(len(app.submitted),1)
        self.assertTrue(app.published['sensor.heating_controller']['calculation_pending'])
        self.assertIn('sensor.heating_heartbeat',app.published)
        self.assertGreaterEqual(len(app.sample_calls),3)

    def test_power_loss_stops_heat_while_search_is_pending(self):
        app,actions=self.app();self.tick(app);app.grid_state=lambda:'off';self.tick(app)
        self.assertEqual(actions,['heat','off'])
        self.assertEqual(app.published['sensor.heating_controller']['grid'],'off')

    def test_manual_pause_is_applied_while_search_is_pending(self):
        app,actions=self.app();self.tick(app);raw=app.raw
        app.raw=lambda key,attribute=None:'Спостереження' if key=='mode' else raw(key,attribute)
        self.tick(app);self.assertEqual(actions,['heat','observe'])
        self.assertEqual(app.published['sensor.heating_controller']['state'],'Спостереження')

    def test_disabled_permissions_stop_heat_while_search_is_pending(self):
        app,actions=self.app();self.tick(app);raw=app.raw
        app.raw=lambda key,attribute=None:'off' if key=='armed' else raw(key,attribute)
        self.tick(app);self.assertNotEqual(actions[-1],'heat')

    def test_upper_limit_stops_heat_while_search_is_pending(self):
        app,actions=self.app();self.tick(app)
        app.source_temperature=lambda:(22.,'Вітальня',22.,22.5)
        self.tick(app);self.assertEqual(actions,['heat','off'])


class BackgroundPlanAcceptanceTests(unittest.IsolatedAsyncioTestCase):
    def app(self):
        app,_=BackgroundTickSafetyTests().app()
        app.memory.pop('plan_reference',None)
        return app,copy.deepcopy(app.decision_context['settings'])

    async def finish(self,app):
        args,kwargs=app.submitted[-1]
        await kwargs['callback']({'result':args[0](*args[1:])})

    async def test_real_result_is_replayed_at_current_absolute_time(self):
        app,settings=self.app();now=app.now()
        self.assertIsNone(app.background_plan(now,21.5,22.,11.,0.,[],settings,app.outage_signature))
        await self.finish(app)
        later=now+timedelta(seconds=15)
        forecast,reference=app.background_plan(later,21.5,22.,11.,0.,[],settings,app.outage_signature)
        self.assertEqual(len(forecast['curve']),48)
        self.assertEqual(forecast['curve'][0]['ts'],later.timestamp()+1800)
        self.assertEqual(reference['at'],later.timestamp())
        self.assertTrue(forecast['plan_id'])

    async def test_changed_model_rejects_completed_result(self):
        app,settings=self.app();now=app.now()
        app.background_plan(now,21.5,22.,11.,0.,[],settings,app.outage_signature)
        await self.finish(app);app.model['version']+=1
        self.assertIsNone(app.background_plan(now,21.5,22.,11.,0.,[],settings,app.outage_signature))
        self.assertEqual(len(app.submitted),2)

    async def test_material_temperature_change_rejects_completed_result(self):
        app,settings=self.app();now=app.now()
        app.background_plan(now,21.5,22.,11.,0.,[],settings,app.outage_signature)
        await self.finish(app)
        self.assertIsNone(app.background_plan(now,20.5,22.,11.,0.,[],settings,app.outage_signature))
        self.assertEqual(len(app.submitted),2)

    async def test_material_weather_change_rejects_completed_result(self):
        app,settings=self.app();now=app.now()
        app.background_plan(now,21.5,22.,11.,0.,[],settings,app.outage_signature)
        await self.finish(app)
        weather=[dict(ts=now.timestamp()+3600,temperature=-5.)]
        self.assertIsNone(app.background_plan(now,21.5,22.,11.,0.,weather,settings,app.outage_signature))
        self.assertEqual(len(app.submitted),2)

class DelayedControlEventTests(unittest.TestCase):
    def test_superseded_watchdog_event_is_not_a_manual_override(self):
        app=recovery.FallbackRecoveryTests().app()
        at=app.at.timestamp();app.memory.update(expected_mode='heat',expected_setpoint=24.,command_at=at)
        app.attrs['temperature']=24.;app.command_pending_until=at+20
        old_event=dict(state='auto',attributes=dict(temperature=21.5),last_updated=app.at.isoformat())
        with patch('heating_recovery.time.time',return_value=at+83):
            app.climate_changed('climate.test','all',None,old_event)
        self.assertTrue(app.memory['owned']);self.assertFalse(app.calls)

    def test_real_current_manual_change_still_pauses_control(self):
        app=recovery.FallbackRecoveryTests().app()
        app.memory.update(expected_mode='heat',expected_setpoint=24.)
        app.values['thermostat']='auto';app.attrs['temperature']=21.5
        app.climate_changed('climate.test','all',None,dict(state='auto',attributes=dict(temperature=21.5)))
        self.assertFalse(app.memory['owned'])
        self.assertEqual(app.calls[-1][0],'input_select/select_option')

    def test_confirmed_same_stage_survives_a_controller_gap_without_filling_energy(self):
        fixture=inlet.InletAccountingTests();fixture.setUp();app=fixture.app()
        for second in (0,15,30):
            with patch('heating_climate.time.time',return_value=fixture.at+second):app.update_heating_power()
        before=app.val('energy')
        with patch('heating_climate.time.time',return_value=fixture.at+300):power=app.update_heating_power()
        self.assertEqual(power,6000.)
        self.assertAlmostEqual(app.val('energy'),before+6000*45/3600000)
        self.assertGreaterEqual(app.memory['inlet_energy']['unknown_seconds'],225)
