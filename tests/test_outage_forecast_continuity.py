"""Loss of live electrical measurements must not erase the thermal outlook."""
import asyncio
from threading import RLock
import unittest
from unittest.mock import patch
import test_planned_execution as execution


class OutageForecastContinuityTests(unittest.TestCase):
    def test_preoutage_control_reserve_is_not_reused_as_live_watts(self):
        fixture=execution.PlannedExecutionTests()
        actions,_=fixture.cycle(grid='unknown',measured_power=None,reserve_prior=6000.)
        self.assertEqual(actions,['off'])
        self.assertNotEqual(fixture.forecast_status['control_power_basis'],'partial_meter_reserve')
        self.assertIsNone(fixture.created_app.memory['floor_observer']['energy_kwh'])

    def test_missing_power_during_grid_loss_still_plans_without_issuing_heat(self):
        for grid in ('off','unknown'):
            with self.subTest(grid=grid):
                fixture=execution.PlannedExecutionTests();actions,_=fixture.cycle(grid=grid,measured_power=None)
                self.assertEqual(actions,['off'])
                self.assertEqual(fixture.planner_calls,1)
                self.assertEqual(fixture.forecast_status['horizon_hours'],24)
                self.assertTrue(fixture.forecast_status['electrical_inputs_unavailable'])
                self.assertEqual(fixture.forecast_status['grid_state'],grid)
                self.assertIsNone(fixture.created_app.sample_calls[-1][3])
                self.assertFalse(fixture.created_app.sample_calls[-1][8])

    def test_stored_heat_supports_outlook_when_return_sensor_disappears_during_outage(self):
        fixture=execution.PlannedExecutionTests()
        actions,_=fixture.cycle(grid='unknown',measured_power=None,probe_available=False,stored_floor=25.)
        self.assertEqual(actions,['off']);self.assertEqual(fixture.planner_calls,1)
        self.assertGreater(fixture.forecast_status['estimated_floor'],24.)
        self.assertLess(fixture.forecast_status['estimated_floor'],25.)
        self.assertEqual(fixture.created_app.memory['floor_observer']['grid'],'unknown')

    def test_no_stored_heat_and_invalid_probe_does_not_invent_a_forecast(self):
        fixture=execution.PlannedExecutionTests()
        self.assertEqual(fixture.cycle(grid='unknown',measured_power=None,probe_available=False)[0],['off'])
        self.assertEqual(fixture.planner_calls,0)
        self.assertEqual(fixture.forecast_status['state'],'unavailable')

    def test_unknown_power_with_confirmed_grid_keeps_existing_control_protection(self):
        fixture=execution.PlannedExecutionTests();actions,_=fixture.cycle(grid='on',measured_power=None)
        self.assertNotIn('heat',actions);self.assertEqual(fixture.planner_calls,1)

    def test_grid_loss_and_return_force_fresh_replanning_instead_of_reusing_preoutage_commands(self):
        fixture=execution.PlannedExecutionTests()
        actions,_=fixture.cycle(grid_sequence=['on','off','on'])
        self.assertEqual(actions,['heat','off','heat'])
        self.assertEqual(fixture.planner_calls,3)

    def test_real_planner_keeps_full_curve_and_planned_intervals_without_live_watts(self):
        fixture=execution.PlannedExecutionTests()
        actions,_=fixture.cycle(grid='unknown',measured_power=None,room=20.9,real_planner=True)
        self.assertEqual(actions,['off'])
        forecast=fixture.forecast_status
        self.assertEqual(len(forecast['curve']),48)
        self.assertEqual(forecast['horizon_hours'],24)
        self.assertGreater(len(forecast['heat_segments']),0)
        self.assertGreater(forecast['planned_kwh'],0)
        self.assertFalse(forecast['validated'])
        self.assertTrue(forecast['electrical_inputs_unavailable'])

    def test_native_background_plan_finishing_after_timeout_is_published_during_blackout(self):
        fixture=execution.PlannedExecutionTests()
        actions,_=fixture.cycle(grid='off',measured_power=None,room=20.9,real_planner=True)
        app=fixture.created_app
        actions.clear();app.published.clear()
        app.runtime_ready=True;app.next_compute=0
        app.calculation_lock=RLock();app.calculation_jobs={};app.submitted=[]
        app.submit_to_executor=lambda *a,**k:app.submitted.append((a,k))
        app.memory.pop('plan_reference',None)
        app.memory.pop('planned_heat_segments',None)

        app.tick_inner()
        self.assertNotIn('sensor.heating_forecast',app.published)
        plan_args,plan_kwargs=next(item for item in app.submitted if item[0][1]=='plan')
        started=app.calculation_jobs['plan']['at']
        with patch('heating_recovery.time.time',return_value=started+61):
            app.tick_inner()
        self.assertEqual(len([item for item in app.submitted if item[0][1]=='plan']),1)

        result=plan_args[0](*plan_args[1:])
        asyncio.run(plan_kwargs['callback']({'result':result}))
        with patch('heating_recovery.time.time',return_value=started+62):
            app.tick_inner()

        forecast=app.published['sensor.heating_forecast']
        self.assertEqual(forecast['grid_state'],'off')
        self.assertTrue(forecast['electrical_inputs_unavailable'])
        self.assertGreater(len(forecast['heat_segments']),0)
        self.assertNotIn('heat',actions)
