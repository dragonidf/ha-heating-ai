"""Audit counterexamples, not replays of private incident snapshots."""
import unittest
from datetime import datetime,timedelta
from unittest.mock import patch
from zoneinfo import ZoneInfo
from test_adapter_modulation import HeatingClimate
from heating_environment import observe_gain,gain_at
from heating_model import DEFAULT_MODEL,uncertainty_reserve,step,plan
from heating_recovery import HeatingRecovery
from heating_stability import replay,stress_scenarios


class GainAuditTests(unittest.TestCase):
    def test_cached_primary_cannot_confirm_persistence_and_second_sensor_can_reject_it(self):
        at=datetime(2026,10,27,10,tzinfo=ZoneInfo('Europe/Kiev'))
        state={}
        for minute,room,other,reported in ((0,21.,23.,0),(60,21.7,23.,60),(80,21.7,23.,60)):
            state=observe_gain(state,(at+timedelta(minutes=minute)).timestamp(),room,22.,10.,0.,False,
                DEFAULT_MODEL,at.tzinfo,'sunny',secondary=other,reported_at=(at+timedelta(minutes=reported)).timestamp())
        self.assertEqual(state['observations'],[])
        state=observe_gain(state,(at+timedelta(minutes=90)).timestamp(),21.7,22.,10.,0.,False,
            DEFAULT_MODEL,at.tzinfo,'sunny',secondary=22.,reported_at=(at+timedelta(minutes=90)).timestamp())
        self.assertEqual(state['observations'],[])
    def test_fast_reversal_removes_recent_credit_and_training_spike(self):
        at=datetime(2026,10,27,10,tzinfo=ZoneInfo('Europe/Kiev'))
        state={}
        for minutes,room in ((0,21.),(60,21.7),(70,21.2)):
            state=observe_gain(state,(at+timedelta(minutes=minutes)).timestamp(),room,22.,10.,0.,False,
                               DEFAULT_MODEL,at.tzinfo,'sunny')
        self.assertLessEqual(state.get('observed_gain',0.),.1)
        self.assertFalse(any(x['gain']>.5 for x in state['observations']))
    def test_unvalidated_observed_gain_is_not_lower_scenario_free_heat(self):
        at=datetime(2026,10,27,12,tzinfo=ZoneInfo('Europe/Kiev'))
        settings={'environment':{'solar':dict(validated=False,observed_gain=.8,observed_at=at.timestamp())}}
        self.assertEqual(gain_at(at,[],settings),0.)
        self.assertGreater(gain_at(at,[],settings,upper=True),0.)


class CoverageAuditTests(unittest.TestCase):
    def test_comfort_integral_uses_recorded_day_night_limits_on_both_dst_dates(self):
        for month,day in ((3,29),(10,25)):
            app=HeatingClimate();app.climate_tz=ZoneInfo('Europe/Kiev')
            end=datetime(2026,month,day,7,5,tzinfo=app.climate_tz).timestamp()
            policy=dict(target=22.,lower=21.5,upper=22.5,night_target=21.,night_lower=20.5,night_upper=21.5,
                        sleep_start='23:00',sleep_end='07:00')
            app.samples=[dict(ts=end-offset,room=21.,quality=True,power=0.,energy=100.,comfort_policy=policy)
                         for offset in (600,300,0)]
            app.hour_mae=lambda:.1;app.memory={};app.now=lambda:datetime.fromtimestamp(end,app.climate_tz)
            with patch('heating_climate.time.time',return_value=end):result=app.summary()
            self.assertAlmostEqual(result['below_degree_hours'],.042,places=3)
            self.assertEqual(result['above_degree_hours'],0.)
            self.assertEqual(result['comfort_covered_minutes'],10.)
    def test_missing_half_hour_is_not_full_time_coverage(self):
        app=HeatingClimate();end=datetime(2026,10,27,12,tzinfo=ZoneInfo('Europe/Kiev')).timestamp()
        app.samples=[dict(ts=end-86400+i*300,room=21.,quality=True,power=0.,energy=100.)
                     for i in range(289) if not 80<=i<86]
        app.hour_mae=lambda:.1;app.memory={};app.now=lambda:datetime.fromtimestamp(end,ZoneInfo('Europe/Kiev'))
        with patch('heating_climate.time.time',return_value=end):result=app.summary()
        self.assertLess(result['coverage'],.99)
        self.assertGreaterEqual(result['max_gap_seconds'],1800)


class VentilationAuditTests(unittest.TestCase):
    def app(self):
        app=HeatingClimate();app.cfg=dict(ventilation=dict(recuperator=dict(entity='switch.recuperator'),
            exhaust=dict(entity='switch.exhaust',power_entity='binary_sensor.exhaust_supply')))
        app.states={'switch.recuperator':'on','switch.exhaust':'on','binary_sensor.exhaust_supply':'on'}
        app.get_state=lambda entity:app.states.get(entity,'unknown');app.grid_state=lambda:'off'
        return app
    def test_on_off_unknown_and_supply_are_independent_fractional_inputs(self):
        app=self.app();app.ventilation_tick(1000.)
        for t in range(1015,1301,15):
            if t==1105:app.states['switch.recuperator']='off'
            if t==1210:app.states['switch.recuperator']='unavailable'
            app.ventilation_tick(float(t))
        rows=app.ventilation_sample(1300.)
        self.assertEqual(rows['recuperator']['seconds'],300.)
        self.assertAlmostEqual(rows['recuperator']['on_fraction'],.35)
        self.assertAlmostEqual(rows['recuperator']['unknown_fraction'],.3)
        self.assertIsNone(rows['recuperator']['powered_relay_on_fraction'])
        self.assertEqual(rows['recuperator']['supply_unknown_fraction'],1.)
        self.assertEqual(rows['exhaust']['powered_relay_on_fraction'],1.)
        self.assertEqual(rows['exhaust']['grid_on_fraction'],0.)
    def test_restart_and_long_unobserved_gap_do_not_integrate_cached_on_as_truth(self):
        app=self.app();app.ventilation_tick(1000.);app.ventilation_tick(1600.)
        row=app.ventilation_sample(1600.)['recuperator']
        self.assertEqual(row['unknown_fraction'],1.);self.assertEqual(row['on_fraction'],0.)
        restarted=self.app();restarted.ventilation_tick(2000.)
        row=restarted.ventilation_sample(2000.)['recuperator']
        self.assertEqual(row['seconds'],0.)
        self.assertIsNone(row['on_fraction'])


class LongHorizonAuditTests(unittest.TestCase):
    def app(self):
        app=HeatingRecovery();app.memory={};app.model=dict(DEFAULT_MODEL,version=6)
        app.raw=lambda key:'on' if key=='armed' else 'Автоматичне керування'
        app.grid_state=lambda:'on';app.val=lambda key:100.
        return app
    def test_existing_validated_reserve_is_preserved_when_legacy_metadata_is_missing(self):
        app=self.app();at=datetime(2026,10,27,0,tzinfo=ZoneInfo('Europe/Kiev'))
        original=dict(hours=36,error=.2,validated=True)
        app.memory['reserve_errors']=[dict(original)]
        app.validate_reserve_predictions(at,21.4,0.,True)
        self.assertEqual(app.memory['reserve_errors'],[original])
    def test_required_39_hour_target_can_generate_reserve_after_ten_finishes(self):
        app=self.app();at=datetime(2026,10,27,0,tzinfo=ZoneInfo('Europe/Kiev'))
        end=at.timestamp()+39*3600
        forecast=dict(curve=[],_calculation_curve=[dict(ts=at.timestamp()+i*300,temperature=21.4,heat_segments=[]) for i in range(1,469)],
                      morning_reserve=dict(assessment_until=end))
        settings=dict(target=21.5,lower=21.,upper=22.,reserve_errors=[])
        for i in range(10):
            app.memory['reserve_validation_at']=0
            app.validate_reserve_predictions(at,21.4,0.,True,forecast)
            for seconds in range(30,39*3600+1,30):
                app.validate_reserve_predictions(at+timedelta(seconds=seconds),21.2,0.,True)
            at+=timedelta(hours=40);end=at.timestamp()+39*3600
            forecast['_calculation_curve']=[dict(ts=at.timestamp()+j*300,temperature=21.4,heat_segments=[]) for j in range(1,469)]
            forecast['morning_reserve']['assessment_until']=end
        settings['reserve_errors']=app.memory['reserve_errors']
        self.assertIsNotNone(uncertainty_reserve(settings,39))
    def test_known_grid_off_zero_outside_heat_is_not_an_unknown_measurement(self):
        app=self.app();at=datetime(2026,10,27,12,tzinfo=ZoneInfo('Europe/Kiev'))
        app.grid_state=lambda:'off'
        app.power_source='fallback_grid_off'
        app.memory['reserve_predictions']=[dict(ts=at.timestamp()+3600,hours=39,temperature=21.4,
            model=dict(app.model),valid=True,segments=[],start_energy=100.,expected_energy=0.)]
        app.validate_reserve_predictions(at,21.3,0.,True)
        self.assertEqual(len(app.memory['reserve_predictions']),1)
    def test_known_outage_preserves_measured_input_rollout_when_frozen_execution_changes(self):
        app=self.app();at=datetime(2026,10,27,0,tzinfo=ZoneInfo('Europe/Kiev'))
        start=at.timestamp();room,floor=21.4,23.
        app.memory['last_command']='off';app.grid_state=lambda:'off'
        forecast=dict(curve=[dict(ts=start+6*3600,temperature=21.4,
            heat_segments=[dict(start=start,end=start+3600,kwh=3.)])])
        app.validate_reserve_predictions(at,room,0.,True,forecast,dict(floor=floor,outside=10.,fire=0.))
        for seconds in range(30,6*3600+1,30):
            room,floor=step(room,floor,10.,0.,0.,app.model,30/3600)
            app.validate_reserve_predictions(at+timedelta(seconds=seconds),room,0.,True,
                                            measurements=dict(floor=floor,outside=10.,fire=0.))
        rows=app.memory['forecast_validation']
        self.assertEqual({r['kind'] for r in rows},{'frozen_plan','measured_inputs'})
        frozen=next(r for r in rows if r['kind']=='frozen_plan')
        self.assertFalse(frozen['reserve_eligible']);self.assertIn('execution_changed',frozen['reasons'])
        measured=next(r for r in rows if r['kind']=='measured_inputs')
        self.assertTrue(measured['valid']);self.assertAlmostEqual(measured['error'],0.)
        self.assertEqual(app.memory['reserve_errors'],[])
    def test_modulation_zero_during_known_heat_request_does_not_drop_prediction(self):
        app=self.app();at=datetime(2026,10,27,0,tzinfo=ZoneInfo('Europe/Kiev'))
        app.memory['last_command']='heat'
        app.memory['reserve_predictions']=[dict(ts=at.timestamp()+3600,hours=6,temperature=21.4,
            model=dict(app.model),valid=True,segments=[dict(start=at.timestamp()-60,end=at.timestamp()+3600)],
            start_energy=100.,expected_energy=3.)]
        app.validate_reserve_predictions(at,21.4,0.,True)
        self.assertEqual(len(app.memory['reserve_predictions']),1)
    def test_unknown_power_or_gap_is_explicitly_rejected(self):
        for power,seconds in ((None,15),(0.,90)):
            app=self.app();at=datetime(2026,10,27,0,tzinfo=ZoneInfo('Europe/Kiev'))
            app.memory['reserve_predictions']=[dict(ts=at.timestamp()+3600,hours=6,temperature=21.4,
                checked_at=at.timestamp()-seconds,model=dict(app.model),valid=True,segments=[],
                start_energy=100.,expected_energy=0.)]
            app.validate_reserve_predictions(at,21.4,power,True)
            self.assertEqual(app.memory['reserve_predictions'],[])
            self.assertTrue(app.memory['forecast_validation_rejections'])


class StopPeakAuditTests(unittest.TestCase):
    def test_peak_error_needs_a_complete_window_without_reheating(self):
        for reheat in (False,True):
            app=HeatingRecovery();app.model=dict(version=6);app.memory=dict(last_command='heat')
            app.decision_context=dict(coast_peak=22.2)
            at=datetime(2026,10,27,7,tzinfo=ZoneInfo('Europe/Kiev'))
            app.observe_stop_peak(at,21.5,3000.,True)
            app.memory['last_command']='off';app.observe_stop_peak(at+timedelta(seconds=15),21.5,0.,True)
            for seconds in range(30,3*3600+16,15):
                if reheat and seconds==300:app.memory['last_command']='heat'
                app.observe_stop_peak(at+timedelta(seconds=seconds),22.1 if seconds==3600 else 21.8,0.,True)
            records=app.memory['forecast_validation'];self.assertEqual(len(records),1)
            self.assertEqual(records[0]['valid'],not reheat)
            if reheat:self.assertIsNone(records[0]['error'])
            else:self.assertAlmostEqual(records[0]['error'],.1)


class ActuatorAuditTests(unittest.TestCase):
    def test_actuator_action_does_not_replace_numeric_comfort_target(self):
        now=datetime(2026,10,27,21,tzinfo=ZoneInfo('Europe/Kiev'))
        settings=dict(target=21.5,lower=21.,upper=22.5,night_target=21.,night_lower=20.5,night_upper=21.5,
            sleep_start='23:00',sleep_end='07:00',start='23:00',end='07:00',strategy='Тільки в дозволені години',
            day_price=4.32,night_price=2.16,actuator=dict(action='off',changed_at=now.timestamp()-60))
        result=plan(now,21.,22.,10.,0.,DEFAULT_MODEL,[],settings,3.)
        self.assertIsNotNone(result)

    def scenario(self,last,segments,outages=None):
        now=datetime(2026,10,27,3,tzinfo=ZoneInfo('Europe/Kiev'))
        settings=dict(target=21.5,lower=21.,upper=22.5,start='23:00',end='07:00',strategy='Тільки в дозволені години',
            actuator=dict(action=last,changed_at=now.timestamp()-60),
            outage_schedule=dict(mode='normal',valid=True,outages=outages or []))
        return now,replay(now,21.,22.,10.,0.,DEFAULT_MODEL,[],settings,
            [dict(start=now.timestamp()+a,end=now.timestamp()+b) for a,b in segments],False,3.)
    def test_recent_off_delays_forecast_exactly_until_actual_minimum_off_expires(self):
        at,result=self.scenario('off',[(0,1800)])
        self.assertIsNotNone(result)
        self.assertAlmostEqual(result['heat_segments'][0]['start'],at.timestamp()+840)
    def test_recent_heat_commitment_is_in_forecast_even_when_desired_plan_is_off(self):
        at,result=self.scenario('heat',[])
        self.assertIsNotNone(result)
        self.assertAlmostEqual(sum(s['end']-s['start'] for s in result['heat_segments']),540.)


class CycleEnergyAuditTests(unittest.TestCase):
    def test_window_change_with_same_end_does_not_reuse_previous_budget(self):
        app=HeatingRecovery();app.memory={};app.power_source='balance'
        at=datetime(2026,10,27,3,tzinfo=ZoneInfo('Europe/Kiev'))
        app.observe_cycle_energy(at,dict(start='23:00',end='07:00'),3000.,100.,'on',True)
        app.observe_cycle_energy(at+timedelta(seconds=15),dict(start='00:00',end='07:00'),3000.,100.0125,'on',True)
        self.assertEqual(len(app.memory['cycle_energy']),2)
        self.assertTrue(all(not e['complete'] for e in app.memory['cycle_energy'].values()))

    def test_dst_cycle_uses_real_seconds_and_does_not_double_count_on_next_cycle(self):
        app=HeatingRecovery();app.memory={};app.power_source='balance'
        start=datetime(2026,10,24,23,tzinfo=ZoneInfo('Europe/Kiev'))
        end=datetime(2026,10,25,7,tzinfo=start.tzinfo)
        settings=dict(start='23:00',end='07:00')
        for offset in range(-15,int(end.timestamp()-start.timestamp())+31,15):
            stamp=start.timestamp()+offset;energy=100+3*max(0.,min(offset,9*3600))/3600
            app.observe_cycle_energy(datetime.fromtimestamp(stamp,start.tzinfo),settings,3000.,energy,'on',True)
        finished=next(e for e in app.memory['cycle_energy'].values() if e['end']==end.timestamp())
        self.assertTrue(finished['complete']);self.assertAlmostEqual(finished['observed_kwh'],27.)
        self.assertAlmostEqual(sum(e['observed_kwh'] for e in app.memory['cycle_energy'].values()),27.)
    def test_reload_preserves_totals_and_marks_gap_instead_of_crediting_it(self):
        app=HeatingRecovery();app.memory={};app.power_source='balance'
        at=datetime(2026,10,27,3,tzinfo=ZoneInfo('Europe/Kiev'));settings=dict(start='23:00',end='07:00')
        app.observe_cycle_energy(at,settings,3000.,100.,'on',True)
        app.observe_cycle_energy(at+timedelta(seconds=15),settings,3000.,100.0125,'on',True)
        before=sum(e['observed_kwh'] for e in app.memory['cycle_energy'].values())
        app.memory.pop('cycle_energy_observation')
        app.observe_cycle_energy(at+timedelta(seconds=60),settings,3000.,101.,'on',True)
        self.assertAlmostEqual(sum(e['observed_kwh'] for e in app.memory['cycle_energy'].values()),before)
        self.assertFalse(next(iter(app.memory['cycle_energy'].values()))['complete'])


class StressAuditTests(unittest.TestCase):
    def test_zero_cold_stress_keeps_sessions_beyond_public_24h(self):
        from heating_model import reserve_context
        now=datetime(2026,10,27,21,tzinfo=ZoneInfo('Europe/Kiev'))
        settings=dict(target=21.5,lower=21.,upper=22.5,start='23:00',end='07:00',
            strategy='Тільки в дозволені години',morning_min_minutes=0,risk_cold_delta=0.,
            outage_schedule=dict(mode='normal',valid=True,outages=[]))
        context=dict(reserve_context(now,settings,DEFAULT_MODEL),assessment_until=now.timestamp()+30*3600)
        nominal=replay(now,21.,22.,10.,0.,DEFAULT_MODEL,[],settings,
            [dict(start=now.timestamp()+26*3600,end=now.timestamp()+27*3600)],False,3.,context_override=context)
        self.assertIsNotNone(nominal)
        self.assertEqual(nominal['heat_segments'],[])
        rows=stress_scenarios(now,21.,22.,10.,0.,DEFAULT_MODEL,[],settings,nominal,3.)
        self.assertAlmostEqual(rows[0]['deficit_degree_hours'],nominal['morning_reserve']['deficit_degree_hours'])

    def test_three_strategies_and_four_supply_modes_preserve_masks_and_24h(self):
        from test_sufficiency_search import SufficiencySearchTests
        from heating_outages import blocked
        fixture=SufficiencySearchTests();fixture.setUp();x=fixture.x
        for i,strategy in enumerate(('Тільки в дозволені години','Переважно вночі','Комфорт протягом доби')):
            for mode in ('normal','scheduled','unknown','emergency'):
                with self.subTest(strategy=strategy,mode=mode):
                    now=fixture.now
                    end=now.replace(day=22,hour=7,minute=0,second=0).timestamp()
                    start=end-(1,2,8)[i]*3600
                    outages=[dict(start=start,end=end)] if mode in ('scheduled','emergency') else []
                    settings=dict(fixture.settings,strategy=strategy,modulation_profile=None,
                        actuator=dict(action='off',changed_at=now.timestamp()-1000),
                        outage_schedule=dict(mode='scheduled' if mode=='emergency' else mode,
                            valid=mode!='unknown',emergency=mode=='emergency',known_until=now.timestamp()+172800,
                            slots=[dict(start=now.timestamp(),end=now.timestamp()+172800,off=False)],outages=outages))
                    result=plan(now,x['room'],x['floor'],x['outside'],0.,x['model'],[],settings,(3.,6.,9.)[i])
                    self.assertEqual(result['horizon_hours'],24.)
                    self.assertEqual(result['curve'][-1]['ts']-now.timestamp(),86400.)
                    self.assertTrue(all(not blocked(s['start'],s['end'],outages) for s in result['heat_segments']))
                    scenarios=stress_scenarios(now,x['room'],x['floor'],x['outside'],0.,x['model'],[],settings,result,(3.,6.,9.)[i])
                    self.assertEqual(len(scenarios),3 if mode in ('unknown','emergency') else 1)

    def test_partial_morning_blackout_is_evaluated_without_changing_nominal_policy(self):
        now=datetime(2026,10,27,6,30,tzinfo=ZoneInfo('Europe/Kiev'))
        settings=dict(target=21.5,lower=21.,upper=22.5,start='23:00',end='07:00',
            strategy='Тільки в дозволені години',morning_min_minutes=30,risk_blackout_short_minutes=15,
            risk_blackout_long_minutes=30,outage_schedule=dict(mode='unknown',valid=False,outages=[]))
        nominal=replay(now,21.,22.,10.,0.,DEFAULT_MODEL,[],settings,
            [dict(start=now.timestamp(),end=now.timestamp()+1800)],False,3.)
        import copy
        before=copy.deepcopy(settings)
        rows=stress_scenarios(now,21.,22.,10.,0.,DEFAULT_MODEL,[],settings,nominal,3.)
        self.assertEqual(settings,before)
        self.assertTrue(all(r['available'] for r in rows),rows)
        partial=next(r for r in rows if r['name']=='blackout_15')
        self.assertAlmostEqual(partial['energy'],.75)
        self.assertTrue(all(r['assessment_until']==nominal['morning_reserve']['assessment_until'] for r in rows))

if __name__=='__main__':unittest.main()
