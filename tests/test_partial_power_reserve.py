"""Missing load power stays unknown; a bounded reserve can support planning."""
import copy
import unittest
import heating_fallback as fallback
from unittest.mock import patch
import test_planned_execution as planned
import test_fallback_recovery as recovery
import test_system_settings as settings_fixture
from datetime import datetime,timedelta
from zoneinfo import ZoneInfo
from heating_recovery import confirmed_morning


class PartialPowerReserveTests(unittest.TestCase):
    entities=['total','group1','group2','group3','grid_feed']
    def readings(self):
        return {key:dict(value=value,at=100.,unit='W',kind='active') for key,value in
            dict(total=7200.,group1=None,group2=20.,group3=1160.,grid_feed=20.).items()}
    def bounds(self,rows=None):
        return fallback.partial_balance_bounds(self.entities,self.readings() if rows is None else rows,100.)
    def test_one_missing_group_keeps_good_groups_and_does_not_invent_watts(self):
        rows=self.readings();before=copy.deepcopy(rows);result=self.bounds(rows)
        self.assertTrue(result['valid']);self.assertIsNone(result['watts'])
        self.assertEqual(result['upper_w'],6000.);self.assertEqual(result['lower_w'],0.)
        self.assertEqual(result['unknown_components'],['group1']);self.assertEqual(rows,before)

    def test_reserve_is_opt_in_validated_and_survives_settings_restart(self):
        from heating_ui import system_values
        fixture=settings_fixture.SystemSettingsTests();app=fixture.app()
        self.assertEqual(system_values(app.cfg)['power_partial_reserve'],'disabled')
        self.assertFalse(fixture.submit(app,{'power_partial_reserve':True})['last_success'])
        status=fixture.submit(app,{'power_partial_reserve':'enabled'})
        self.assertTrue(status['last_success']);self.assertEqual(app.next_compute,0)
        restored=fixture.app();restored.memory=copy.deepcopy(app.memory);restored.restore_system_settings()
        self.assertEqual(restored.cfg['power_partial_reserve'],'enabled');self.assertFalse(restored.cfg['allow_actuation'])
    def test_every_group_can_fail_without_assuming_physical_a_b_c(self):
        for group in ('group1','group2','group3'):
            rows=self.readings();rows['group1']['value']=10.;rows[group]['value']=None
            self.assertTrue(self.bounds(rows)['valid'])
    def test_missing_total_inverter_or_two_groups_is_unsafe(self):
        for key in ('total','grid_feed','group2'):
            rows=self.readings();rows[key]['value']=None;self.assertFalse(self.bounds(rows)['valid'])
    def test_stale_required_sources_skew_and_bad_units_are_rejected(self):
        for key in ('total','grid_feed'):
            rows=self.readings();rows[key]['at']=-400.;self.assertFalse(self.bounds(rows)['valid'])
            rows=self.readings();rows[key]['unit']='VA';self.assertFalse(self.bounds(rows)['valid'])
        rows=self.readings();rows['grid_feed']['at']=50.;self.assertFalse(self.bounds(rows)['valid'])
    def test_kw_and_signed_inverter_grid_keep_the_same_balance(self):
        rows=self.readings();rows['total'].update(value=7.2,unit='kW')
        self.assertEqual(self.bounds(rows)['upper_w'],6000.)
        rows=self.readings();rows['grid_feed']['value']=-100.
        self.assertEqual(self.bounds(rows)['upper_w'],6120.)
    def test_negative_double_subtraction_and_intact_primary_do_not_enable_reserve(self):
        rows=self.readings();rows['group1']['value']=10.;self.assertFalse(self.bounds(rows)['valid'])
        rows=self.readings();rows['total']['value']=200.;self.assertFalse(self.bounds(rows)['valid'])
    def test_healthy_household_load_is_subtracted_once_and_never_called_a_ten(self):
        rows=self.readings();rows['group3']['value']=3000.;rows['total']['value']=3040.
        result=self.bounds(rows);self.assertEqual(result['upper_w'],0.);self.assertIsNone(result['watts'])

    def test_fixed_profile_prior_is_never_a_physical_measurement(self):
        bounds=self.bounds()
        self.assertEqual(fallback.reserve_control_prior(bounds,'heat',24,22,'fixed_6'),6000.)
        self.assertEqual(fallback.reserve_control_prior(bounds,'heat',20,22,'fixed_6'),0.)
        self.assertIsNone(fallback.reserve_control_prior(bounds,'heat',24,22,'auto'))
        self.assertIsNone(fallback.reserve_control_prior(bounds,'heat',24,None,'fixed_6'))

    def test_degraded_planning_continues_but_does_not_credit_unknown_floor_heat(self):
        import heating_climate
        case=planned.PlannedExecutionTests()
        with patch('heating_climate.observe_floor',wraps=heating_climate.observe_floor) as observe:
            actions=case.cycle(measured_power=None,reserve_prior=6000.)[0]
        self.assertEqual(actions,['heat']);self.assertEqual(observe.call_args.args[4],0.)
        self.assertEqual(observe.call_args.args[0]['estimate'],21.5)
        self.assertIsNone(observe.call_args.kwargs['energy_kwh'])
        self.assertEqual(case.created_app.published['sensor.heating_controller']['control_power_basis'],'partial_meter_reserve')
        self.assertIsNotNone(case.created_app.published['sensor.heating_forecast']['state'])

    def test_loss_of_grid_and_upper_temperature_still_veto_reserve_heat(self):
        for grid in ('off','unknown'):
            case=planned.PlannedExecutionTests()
            self.assertEqual(case.cycle(grid=grid,measured_power=None,reserve_prior=6000.)[0],['off'])
        case=planned.PlannedExecutionTests()
        self.assertEqual(case.cycle(room=23.,measured_power=None,reserve_prior=6000.)[0],['off'])

    def test_boiler_response_in_reserve_stays_estimated_and_does_not_force_native_auto(self):
        app=recovery.FallbackRecoveryTests().app();app.memory['last_command']='heat'
        app.cfg.update(power_partial_reserve='enabled',power_measurement_policy='audited')
        app.current_power_w=None;app.control_power_prior=6000.;app.control_power_bounds=dict(valid=True,at=app.at.timestamp(),upper_w=6000.)
        with patch('heating_climate.time.time',return_value=app.at.timestamp()):app.verify_boiler_response()
        self.assertNotIn('temporary_fallback',app.memory)
        self.assertFalse(app.memory['command_diagnostic']['boiler_confirmed'])
        self.assertTrue(app.memory['command_diagnostic']['response_estimated'])

    def test_disabled_stale_reserve_or_measured_zero_cannot_mask_failed_heating(self):
        for variant in ('disabled','stale','zero'):
            app=recovery.FallbackRecoveryTests().app();app.memory['last_command']='heat'
            app.cfg.update(power_partial_reserve='disabled' if variant=='disabled' else 'enabled',power_measurement_policy='audited')
            app.current_power_w=0. if variant=='zero' else None;app.control_power_prior=6000.
            app.control_power_bounds=dict(valid=True,at=app.at.timestamp()-(60 if variant=='stale' else 0),upper_w=6000.)
            with patch('heating_climate.time.time',return_value=app.at.timestamp()):app.verify_boiler_response()
            self.assertIn('temporary_fallback',app.memory)

    def test_reserve_morning_requests_are_separate_and_do_not_repeat_after_stop(self):
        at=datetime(2026,11,3,5,30,tzinfo=ZoneInfo('Europe/Kyiv'));memory={}
        settings=dict(start='23:00',end='07:00',morning_min_minutes=30)
        for i in range(121):
            result=confirmed_morning(memory,at+timedelta(seconds=15*i),settings,None,100.,'on',True,'unavailable',control_reserve=True)
        self.assertEqual(result['confirmed_completed_minutes'],0.);self.assertEqual(result['estimated_morning_request_minutes'],30.)
        self.assertEqual(result['morning_elapsed_minutes'],30.)
        self.assertIsNone(result['confirmed_morning_kwh'])
        after=confirmed_morning(memory,at+timedelta(minutes=31),settings,None,100.,'on',False,'unavailable')
        self.assertEqual(after['morning_elapsed_minutes'],30.);self.assertIsNone(after['confirmed_morning_kwh'])


if __name__=='__main__':unittest.main()
