"""Synthetic engineering contracts; no claims about physical meter accuracy."""
import copy,math,unittest
from heating_fallback import aligned_power,phase_residual,integrate_observation,combine_power
from heating_model import modulation_power,learn_power_modes
from heating_environment import ventilation_loss,fit_ventilation

class MeasurementContracts(unittest.TestCase):
    def readings(self):return {k:dict(value=v,at=100.,unit='W',kind='active') for k,v in dict(a=3400,b=400,c=400,washer=400,inverter=0).items()}
    def topology(self):return dict(verified=True,phases=[dict(input='a',loads=[dict(entity='washer',physical_id='washer')],background_w=0.,uncertainty_w=100.),dict(input='b',loads=[],background_w=400.,uncertainty_w=100.),dict(input='c',loads=[],background_w=400.,uncertainty_w=100.)])
    def test_duplicate_appliance_or_phase_rejected(self):
        cfg=self.topology();cfg['phases'][1]['loads']=[dict(entity='boiler_cloud',physical_id='washer')]
        self.assertEqual(phase_residual(cfg,self.readings(),100.)['reason'],'duplicate_load')
    def test_stale_skew_units_and_unknown_never_become_zero(self):
        for change in (dict(at=0.),dict(at=65.),dict(value=float('nan')),dict(unit='VA')):
            rows=self.readings();rows['a'].update(change)
            self.assertFalse(aligned_power(rows,list(rows),100.,max_age=60,max_skew=30)['valid'])
    def test_residual_is_estimate_not_independent_confirmation(self):
        result=phase_residual(self.topology(),self.readings(),100.)
        self.assertTrue(result['valid']);self.assertEqual(result['watts'],3000.)
        self.assertEqual(result['quality'],'estimated');self.assertFalse(result['independent'])
        self.assertFalse(phase_residual({},self.readings(),100.)['valid'])
    def test_conflict_not_averaged(self):
        a=dict(valid=True,watts=3000.,quality='measured');b=dict(valid=True,watts=6000.,quality='estimated')
        r=combine_power(a,b);self.assertIsNone(r['watts']);self.assertEqual(r['quality'],'unknown')
    def test_actual_time_energy_restart_duplicates_and_unknown(self):
        state={}
        integrate_observation(state,0.,3000.,'measured')
        for t in range(15,3601,15):integrate_observation(state,float(t),3000.,'measured')
        self.assertAlmostEqual(state['measured_kwh'],3.,places=10)
        before=copy.deepcopy(state);integrate_observation(state,3600.,9000.,'measured');self.assertEqual(state,before)
        state=copy.deepcopy(state);integrate_observation(state,4000.,None,'unknown',restart=True)
        self.assertAlmostEqual(state['measured_kwh'],3.);self.assertEqual(state['unknown_seconds'],400.)

class PhysicalModes(unittest.TestCase):
    def test_fixed_modes_do_not_borrow_another_mode_or_live_anchor(self):
        for mode,kw in [('fixed_3',3),('fixed_6',6),('fixed_9',9)]:
            self.assertEqual(modulation_power(27,dict(physical_mode=mode,bands=[]),anchor=dict(return_c=27,power_kw=9)),kw)
    def test_unconfirmed_legacy_rows_are_not_confirmed_auto_training(self):
        samples=[dict(ts=100+i*300,power=6,**{'return':27}) for i in range(20)]
        result=learn_power_modes(samples)
        self.assertFalse(result['profiles']);self.assertIn('insufficient',result['status'])

class VentilationContracts(unittest.TestCase):
    def test_validated_increment_is_not_double_counted_and_warm_air_reverses_sign(self):
        model=dict(validated=True,base_room_loss=.01,recuperator=.002,bath=.005,model_version=6)
        settings=dict(ventilation_model=model,ventilation_expected=dict(recuperator=.5,bath=.2),thermal_model_version=6)
        self.assertAlmostEqual(ventilation_loss(22,12,.02,settings),-.08)
        self.assertAlmostEqual(ventilation_loss(12,22,.02,settings),.08)
        model['validated']=False;self.assertEqual(ventilation_loss(22,12,.02,settings),0.)
    def test_empty_or_correlated_inputs_cannot_be_validated(self):
        self.assertFalse(fit_ventilation([],{'version':6})['validated'])

class SettingsAndLatency(unittest.TestCase):
    def test_description_survives_restart_without_commands_or_rollback_pollution(self):
        from test_system_settings import SystemSettingsTests
        from unittest.mock import Mock
        fixture=SystemSettingsTests();app=fixture.app();app.samples=[];app.climate_tz=app.now().tzinfo;app.call_service=Mock()
        status=fixture.submit(app,dict(boiler_power_mode='fixed_6',recuperator_entity='switch.recuperator',outage_missing_policy='assume_available'))
        self.assertTrue(status['last_success']);self.assertEqual(app.memory['equipment_version'],1)
        self.assertNotIn('boiler_power_mode',app.memory['system_settings']);app.call_service.assert_not_called()
        previous=copy.deepcopy(app.memory);fresh=fixture.app();fresh.memory=previous;fresh.restore_system_settings()
        self.assertEqual(fresh.cfg['boiler_power_mode'],'fixed_6');self.assertEqual(fresh.model,app.model)
        self.assertEqual(fresh.cfg['outage_missing_policy'],'assume_available');self.assertNotIn('outage_missing_policy',app.memory['system_settings'])
        fixture.submit(app,dict(boiler_power_mode='fixed_6'),revision=1)
        self.assertNotIn('boiler_power_mode',app.memory['system_settings'])
        self.assertEqual(app.memory['equipment_version'],1)
    def test_schema_rejects_invalid_mode_malformed_phase_or_duplicate(self):
        from heating_ui import validate_system_settings
        from heating_model import DEFAULT_MODEL
        for values in [dict(boiler_power_mode='heat'),dict(recuperator_entity='climate.any'),dict(power_phase_topology={'verified':True,'phases':[]}),dict(power_phase_topology={'verified':False,'phases':[],'token':'forbidden'})]:
            with self.assertRaises(ValueError):validate_system_settings(values,{},DEFAULT_MODEL)
    def test_low_voltage_residual_and_sampling_delay_bound(self):
        from heating_model import HeatingPowerEstimator
        delays=[]
        for cadence in (1,5,15,30):
            estimator=HeatingPowerEstimator(window=10,confirm=10,threshold=500)
            detected=None
            for at in range(0,121,cadence):
                result=estimator.update(float(at),1800.)
                if result and detected is None:detected=at
            self.assertIsNotNone(detected);self.assertLessEqual(detected,cadence+10+15);delays.append(detected)
        self.assertLessEqual(max(delays),30)
    def test_household_load_subtraction_does_not_become_ten_heating(self):
        cfg=MeasurementContracts().topology();readings=MeasurementContracts().readings()
        for load in (0,1800,3000,6000):
            readings['a']['value']=load;readings['washer']['value']=load
            b=phase_residual(cfg,readings,100.);r=combine_power(dict(valid=True,watts=0.),b)
            self.assertEqual(r['watts'],0.)

class CalibrationContracts(unittest.TestCase):
    def test_validated_ventilation_can_advance_within_daily_bounds(self):
        from test_system_settings import SystemSettingsTests
        from unittest.mock import patch
        app=SystemSettingsTests().app();app.samples=[];app.climate_tz=app.now().tzinfo
        app.memory['ventilation_model']=dict(validated=True,model_version=app.model['version'],fit_day='2026-10-20',base_room_loss=.02,recuperator=.002,bath=.004)
        candidate=dict(validated=True,model_version=app.model['version'],base_room_loss=.021,recuperator=.0021,bath=.0041)
        with patch('heating_climate.fit_ventilation',return_value=candidate):app.refresh_equipment_models()
        self.assertEqual(app.memory['ventilation_model']['fit_day'],'2026-10-21')
        self.assertAlmostEqual(app.memory['ventilation_model']['base_room_loss'],.021)

    def test_separate_mode_sessions_and_average_are_not_an_instant_six_kw_stage(self):
        from heating_model import DEFAULT_MODEL,step
        rows=[];model=dict(DEFAULT_MODEL,version=6)
        for day in range(3):
            start=1793055600.+day*86400;room=21.;floor=25.;energy=100.;command=start-120
            for i in range(19):
                rows.append(dict(ts=start+i*300,room=room,**{'return':floor},floor_estimate=floor,outside=10.,power=3. if i%2 else 9.,
                    energy=energy,fire=0.,quality=True,measurement_version=3,power_source='balance',power_estimated=False,
                    power_quality='measured',grid='on',heating_requested=True,heat_command_at=command,
                    boiler_power_mode='auto',boiler_mode_confirmed=True,equipment_version=1,
                    instantaneous_stage_seconds={'3':150.,'9':150.}))
                room,floor=step(room,floor,10.,6.,0.,model,1/12);energy+=.5
            rows.append(dict(rows[-1],ts=start+19*300,heating_requested=False,heat_command_at=None))
        result=learn_power_modes(rows,model)
        self.assertIn('auto',result['profiles'])
        p=result['profiles']['auto'];self.assertLess(p['holdout_mae_kw'],p['baseline_mae_kw'])
        self.assertEqual(p['stage_probabilities']['6'],0.)
        self.assertGreater(p['stage_probabilities']['3'],0.)
    def ventilation_rows(self,correlated=False):
        rows=[];loss=.018;kr=.003;kb=.007;air=.035
        for day in range(3):
            start=1793055600.+day*86400;room=22.;floor=25.
            for i in range(73):
                u=((max(0,i-1)//6)%4)//2;v=((max(0,i-1)//6)%2) if not correlated else u
                rows.append(dict(ts=start+i*300,room=room,outside=10.,floor_estimate=floor,quality=True,power_quality='measured',
                    grid='on',fire=0.,daylight_weight=0.,power_estimated=False,
                    ventilation={k:dict(on_fraction=value,unknown_fraction=0.) for k,value in [('recuperator',u),('bath',v)]}))
                next_u=((i//6)%4)//2;next_v=((i//6)%2) if not correlated else next_u
                coefficient=loss+kr*next_u+kb*next_v
                equilibrium=(air*floor+coefficient*10)/(air+coefficient)
                room=equilibrium+(room-equilibrium)*math.exp(-(air+coefficient)/12)
        return rows
    def test_ventilation_needs_independent_states_and_whole_heldout_days(self):
        from heating_model import DEFAULT_MODEL
        model=dict(DEFAULT_MODEL,version=6,room_loss=.02)
        fit=fit_ventilation(self.ventilation_rows(),model)
        self.assertTrue(fit['validated'],fit)
        self.assertLess(fit['holdout_mae'],fit['baseline_mae'])
        self.assertFalse(fit_ventilation(self.ventilation_rows()[:146],model)['validated'])
        bad=fit_ventilation(self.ventilation_rows(correlated=True),model)
        self.assertFalse(bad['validated']);self.assertEqual(bad['reason'],'correlated_or_constant_relays')

class AuditedAdapter(unittest.TestCase):
    def test_real_report_timestamps_override_unchanged_appdaemon_cache_without_relaxing_skew(self):
        from unittest.mock import Mock,patch
        app=self.app();app.runtime_ready=True;app.cfg.update(power_balance_sources=['total','load'],power_balance='balance')
        at=app.at.timestamp()
        rows={k:dict(value=v,at=at,unit='W',kind='active') for k,v in [('total','3000'),('load','3000'),('balance','0')]}
        app.render_template=Mock(return_value=rows)
        with patch('heating_climate.time.time',return_value=at):a=app.assess_power_sources(at,None,False)
        self.assertEqual(a['quality'],'measured');self.assertEqual(a['watts'],0.)
        app.assess_power_sources(at+5,None,False);self.assertEqual(app.render_template.call_count,1)
        rows['load']['at']=at-65
        with patch('heating_climate.time.time',return_value=at+11):a=app.assess_power_sources(at+11,None,False)
        self.assertEqual(a['quality'],'unknown');self.assertIsNone(a['watts'])
        app.render_template.side_effect=OSError('HA unavailable');app.record_failure=Mock()
        with patch('heating_climate.time.time',return_value=at+22):a=app.assess_power_sources(at+22,None,False)
        self.assertEqual(a['quality'],'unknown');self.assertIsNone(a['watts'])

    def app(self):
        from test_power_provenance import PowerProvenanceTests
        from heating_model import HeatingPowerEstimator
        app=PowerProvenanceTests().app();app.cfg['power_measurement_policy']='audited'
        app.power_estimator=HeatingPowerEstimator(window=10,confirm=10,threshold=500)
        base=app.at.isoformat();app.fault='off';app.cfg['power_fault']='fault'
        def state(entity,attribute=None):
            if entity=='fault':return app.fault
            if attribute=='all':return dict(state='100',attributes=dict(unit_of_measurement='W'),last_reported=base)
            return 4000.
        app.get_state=state
        return app
    def test_unusable_primary_never_falls_back_to_a_thermostat_zero_or_phase_count(self):
        from test_power_provenance import PowerProvenanceTests
        app=self.app();app.fault='on'
        power,attrs=PowerProvenanceTests().run_power(app)
        self.assertIsNone(power);self.assertEqual(attrs['power_quality'],'unknown')
    def test_low_power_balanced_energy_is_retained(self):
        from test_power_provenance import PowerProvenanceTests
        app=self.app();old=app.val;app.val=lambda key,default=None,attribute=None:1800. if key=='power_balance' else old(key,default,attribute)
        power,attrs=PowerProvenanceTests().run_power(app)
        self.assertEqual(power,1800.);self.assertEqual(attrs['power_quality'],'measured')
    def test_recommendation_is_like_for_like_and_does_not_prove_impossibility(self):
        from datetime import datetime
        from unittest.mock import patch
        from heating_stability import recommend_power
        at=datetime(2026,10,30,5);settings=dict(target=21,lower=20,upper=22)
        results=[dict(morning_reserve=dict(predicted_sufficient=False)),dict(morning_reserve=dict(predicted_sufficient=True)),dict(morning_reserve=dict(predicted_sufficient=True))]
        with patch('heating_stability.optimise',side_effect=results) as mocked:
            r=recommend_power(at,21,25,8,0,{},[],settings)
        self.assertEqual(r['mode'],'fixed_6');self.assertFalse(r['physical_impossibility_proven'])
        self.assertEqual([c.args[-1] for c in mocked.call_args_list],[3,6,9])
        self.assertEqual(settings,dict(target=21,lower=20,upper=22))

class UpgradeEvidence(unittest.TestCase):
    def test_advice_cache_ignores_refresh_timestamp_but_not_changed_outages(self):
        from test_system_settings import SystemSettingsTests
        from unittest.mock import patch
        from datetime import timedelta
        app=SystemSettingsTests().app();app.cfg['boiler_power_mode']='auto'
        at=app.now();settings={'equipment_version':1};slots=((100.,200.,False),)
        with patch('heating_climate.recommend_power',side_effect=lambda *a,**kw:{'mode':'fixed_6'}) as search:
            app.update_power_recommendation(at,21,25,8,0,[],settings,('scheduled',True,'first',False,slots))
            app.update_power_recommendation(at+timedelta(seconds=60),21,25,8,0,[],settings,('scheduled',True,'refreshed',False,slots))
            self.assertEqual(search.call_count,1)
            app.update_power_recommendation(at+timedelta(seconds=75),21,25,8,0,[],settings,('scheduled',True,'refreshed',False,((100.,200.,True),)))
            self.assertEqual(search.call_count,2)
            weather=[{'ts':at.timestamp()+3600,'temperature':-5}]
            changed=('scheduled',True,'refreshed',False,((100.,200.,True),))
            app.update_power_recommendation(at+timedelta(seconds=90),21,25,8,0,weather,settings,changed)
            self.assertEqual(search.call_count,3)
            app.weather=weather
            app.update_power_recommendation(at+timedelta(seconds=95),21,25,8,0,[dict(weather[0],temperature=-4.98)],settings,changed)
            self.assertEqual(search.call_count,3)
            app.update_power_recommendation(at+timedelta(seconds=105),21,25,8,0,weather,dict(settings,target=23),changed)
            self.assertEqual(search.call_count,4)

    def test_explicit_missing_schedule_assumption_preserves_real_schedule_and_emergency(self):
        from heating_outages import planning_schedule,availability,schedule_covered,blocked
        raw={'mode':'unknown','valid':False,'emergency':False,'slots':[],'outages':[],'warning':'missing','source_fresh':False}
        self.assertIs(planning_schedule(raw,'unknown'),raw)
        self.assertIs(planning_schedule(raw,'assume_available',published=True),raw)
        normal=availability(planning_schedule(raw,'assume_available'),100.,90000.)
        self.assertTrue(normal['assumed']);self.assertEqual(normal['mode'],'normal');self.assertFalse(normal['source_fresh'])
        self.assertTrue(schedule_covered(normal,100.,90000.));self.assertFalse(blocked(100.,90000.,normal['outages']))
        self.assertIsNone(normal['warning']);self.assertFalse(raw['valid'])
        emergency=dict(raw,emergency=True)
        self.assertIs(planning_schedule(emergency,'assume_available'),emergency)
        scheduled=dict(raw,mode='scheduled',valid=True,slots=[dict(start=100.,end=200.,off=True)],outages=[dict(start=100.,end=200.)])
        self.assertIs(planning_schedule(scheduled,'assume_available'),scheduled)

    def test_missing_schedule_assumption_never_overrides_actual_grid(self):
        from test_planned_execution import PlannedExecutionTests
        fixture=PlannedExecutionTests()
        self.assertEqual(fixture.cycle(missing_policy='assume_available',grid='off')[0],['off'])
        self.assertEqual(fixture.planner_settings['outage_schedule']['mode'],'normal')
        self.assertTrue(fixture.outage_status['assumed_available'])
        self.assertEqual(fixture.cycle(missing_policy='assume_available',grid='on')[0],['heat'])

    def test_zero_equipment_metrics_survive_transport_without_mutating_counters(self):
        from test_system_settings import SystemSettingsTests
        app=SystemSettingsTests().app()
        app.cfg['boiler_power_mode']='auto'
        app.memory['power_observation_ledger']={'started_at':100.,'last':{'at':100.,'watts':0.,'quality':'measured'},'measured_kwh':0.,'estimated_kwh':0.,'unknown_seconds':0.}
        app.power_observation={'a':{'valid':True,'watts':0.},'watts':0.,'quality':'measured'}
        before=copy.deepcopy(app.memory)
        def transport(value):
            if isinstance(value,dict):return {k:transport(v) for k,v in value.items() if v}
            if isinstance(value,list):return [transport(v) for v in value]
            return value
        app.publish_equipment()
        payload=transport(app.published['sensor.heating_power_model'])
        self.assertEqual(float(payload['energy']['measured_kwh']),0.)
        self.assertEqual(float(payload['energy']['unknown_seconds']),0.)
        self.assertEqual(float(payload['power_observation']['a']['watts']),0.)
        self.assertEqual(app.memory,before)
        app.power_observation={'quality':'unknown','watts':None,'a':{'valid':False,'watts':None}}
        app.publish_equipment();unknown=transport(app.published['sensor.heating_power_model'])
        self.assertNotIn('watts',unknown['power_observation']);self.assertEqual(unknown['power_observation']['quality'],'unknown')

    def test_hand_edited_invalid_topology_is_unknown_not_a_callback_crash(self):
        app=AuditedAdapter().app()
        for value in ('invalid',{'verified':True,'phases':[None]}, {'verified':True,'phases':[]}):
            app.cfg['power_phase_topology']=value
            result=app.assess_power_sources(app.at.timestamp(),3000.,False)
            self.assertFalse(result['b']['valid'])

    def test_migration_copies_state_once_and_rejects_corrupted_backup(self):
        import json,tempfile
        from pathlib import Path
        from types import SimpleNamespace
        from heating_climate import HeatingClimate
        with tempfile.TemporaryDirectory() as folder:
            root=Path(folder);state=root/'state.json';telemetry=root/'telemetry.jsonl'
            state.write_text('{"energy":42}');telemetry.write_text('{"energy":42}\n')
            app=SimpleNamespace(directory=root,statefile=state,telemetry=telemetry,memory={},cfg={})
            HeatingClimate.migrate_equipment_state(app)
            backup=root/'deployment_backups'/'equipment-v1'
            self.assertEqual(json.loads((backup/'state.json').read_text()),{'energy':42})
            state.write_text('{"energy":43}')
            HeatingClimate.migrate_equipment_state(app)
            self.assertEqual(json.loads((backup/'state.json').read_text()),{'energy':42})
            (backup/'state.json').write_text('corrupted');app.memory={}
            with self.assertRaises(OSError):HeatingClimate.migrate_equipment_state(app)

    def test_real_capacity_search_finds_six_when_three_is_insufficient(self):
        from datetime import datetime
        from zoneinfo import ZoneInfo
        from heating_model import DEFAULT_MODEL
        from heating_stability import recommend_power
        at=datetime(2026,10,30,23,tzinfo=ZoneInfo('Europe/Kiev'))
        model=dict(DEFAULT_MODEL,validated=True,floor_air=.04,room_loss=.012,heat_gain=.8,floor_decay=.2)
        settings=dict(target=21.5,lower=21,upper=22,night_target=21,night_lower=20.5,night_upper=21.5,
            sleep_start='23:00:00',sleep_end='06:30:00',start='23:00:00',end='07:00:00',
            strategy='Тільки в дозволені години',split_comfort=True,morning_min_minutes=30,
            day_price=None,night_price=None,outage_schedule=dict(valid=True,mode='normal'))
        result=recommend_power(at,21.3,25,10,0,model,[],settings)
        self.assertEqual(result['mode'],'fixed_6',result)
        self.assertFalse(result['alternatives'][0]['feasible'])
        self.assertEqual(result['confidence'],'conditional_nominal_assumption')

    def test_missing_forecast_still_exposes_release_policy_without_fabricating_curve(self):
        import test_planned_execution as fixture
        test=fixture.PlannedExecutionTests();test.cycle(room=None)
        self.assertEqual(test.forecast_status['planning_policy_version'],4)
        self.assertEqual(test.forecast_status['curve'],[])
