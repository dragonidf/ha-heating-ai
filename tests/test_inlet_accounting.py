import unittest
from copy import deepcopy
from heating_fallback import inlet_stage, integrate_observation, estimated_power
from unittest.mock import patch
from datetime import datetime, timezone
import test_adapter_modulation as adapter
from heating_model import DEFAULT_MODEL
from heating_ui import validate_system_settings
from heating_recovery import cycle_energy_allowance
from itertools import permutations


class InletAccountingTests(unittest.TestCase):
    def setUp(self):
        self.at=1800000000.
        self.config=dict(total='sensor.total',phases=['sensor.a','sensor.b','sensor.c'],loads=[
            dict(physical_id='inverter_grid',entities=['sensor.grid'],signed=True),
            dict(physical_id='water_heater',entities=['sensor.local','sensor.cloud'])])
        self.readings={e:dict(value=v,at=self.at,unit='W') for e,v in
            dict(total=7300,a=4200,b=3000,c=100,grid=100,local=1200,cloud=1200).items()}

    def stage(self,**kw):
        return inlet_stage(self.config,{'sensor.'+k:v for k,v in self.readings.items()},self.at,kw.pop('mode','fixed_6'),
            kw.pop('thermostat','heat'),kw.pop('target',25.),kw.pop('current',23.),**kw)

    def test_deduplicated_water_heater_and_unmapped_phases(self):
        result=self.stage()
        self.assertEqual(result['watts'],6000.)
        self.assertEqual(result['quality'],'estimated')
        self.assertEqual(result['deducted_w'],1300.)
        self.assertFalse(result['independent'])
        self.assertTrue(estimated_power('fallback_inlet_stage'))

    def test_fixed_stages_and_w_kw(self):
        for stage in (3,6,9):
            for i,e in enumerate(('a','b','c')):self.readings[e]['value']=(3000 if i<stage//3 else 0)+(1300 if i==0 else 0)
            self.readings['total'].update(value=(stage*1000+1300)/1000,unit='kW')
            self.assertEqual(self.stage(mode='fixed_'+str(stage))['watts'],stage*1000)

    def test_phase_names_and_permutations_do_not_change_accounted_stage(self):
        for stage in (3,6,9):
            readings=[(3000 if i<stage//3 else 0)+(1300 if i==0 else 0) for i in range(3)]
            self.readings['total']['value']=stage*1000+1300
            for order in permutations(readings):
                for entity,watts in zip(('a','b','c'),order):self.readings[entity]['value']=watts
                self.assertEqual(self.stage(mode='fixed_'+str(stage))['watts'],stage*1000)

    def test_single_phase_six_kw_is_not_two_tens(self):
        for e,v in dict(a=7300,b=0,c=0).items():self.readings[e]['value']=v
        self.assertIsNone(self.stage()['watts'])
        self.assertEqual(self.stage()['reason'],'phase_stage_conflict')

    def test_averaged_switching_can_estimate_nominal_stage_but_never_certifies_ten_mask(self):
        for e,v in dict(a=3500,b=1600,c=2200).items():self.readings[e]['value']=v
        result=self.stage()
        self.assertEqual(result['watts'],6000.)
        self.assertEqual(result['phase_check'],'bounded_average')
        self.assertEqual(result['quality'],'estimated')
        self.assertFalse(result['independent'])

    def test_demand_gate_and_auto_half_degree_boundary(self):
        self.assertIsNone(self.stage(thermostat='off')['watts'])
        self.assertIsNone(self.stage(thermostat='auto',target=23.49)['watts'])
        self.assertEqual(self.stage(thermostat='auto',target=23.5)['watts'],6000.)
        self.assertIsNone(self.stage(thermostat='heat',target=23.,controller_request=False)['watts'])
        self.assertEqual(self.stage(thermostat='heat',target=23.,controller_request=True)['watts'],6000.)

    def test_fixed_profile_mismatch_and_unconfirmed(self):
        for mode in ('fixed_3','fixed_9','unconfirmed'):
            self.assertIsNone(self.stage(mode=mode)['watts'])

    def test_stale_local_falls_back_to_cloud_but_both_stale_are_unknown(self):
        self.readings['local']['at']-=301
        self.assertEqual(self.stage()['watts'],6000.)
        self.readings['cloud']['at']-=301
        result=self.stage();self.assertIsNone(result['watts'])
        self.assertTrue(result['control_reserve']['valid'])

    def test_stale_core_skew_bad_units_and_alias_conflict(self):
        for e,k,v in [('total','at',self.at-301),('a','at',self.at-31),('b','unit','VA'),('cloud','value',4000)]:
            before=deepcopy(self.readings);self.readings[e][k]=v
            self.assertIsNone(self.stage()['watts'],(e,k,v));self.readings=before

    def test_known_household_load_cannot_become_ten(self):
        self.readings['local']['value']=4200;self.readings['cloud']['value']=4200
        self.assertIsNone(self.stage()['watts'])

    def test_inlet_sum_mismatch_and_negative_residual(self):
        self.readings['total']['value']=100
        self.assertIsNone(self.stage()['watts'])

    def test_unverified_inlet_export_cannot_create_a_false_nine_kw_stage(self):
        self.readings['grid']['value']=-1500
        self.readings['local']['value']=self.readings['cloud']['value']=0
        for e,v in dict(total=7500,a=1500,b=3000,c=3000).items():self.readings[e]['value']=v
        result=self.stage(mode='auto')
        self.assertIsNone(result['watts'])
        self.assertEqual(result['reason'],'inlet_export_unverified')

    def test_auto_model_is_check_not_an_invented_measurement(self):
        provisional=self.stage(mode='auto',model_kw=3.,model_valid=False)
        self.assertEqual(provisional['watts'],6000.)
        self.assertEqual(provisional['model_check'],'provisional')
        self.assertIsNone(self.stage(mode='auto',model_kw=3.,model_valid=True,window_kw=9.)['watts'])
        self.assertEqual(self.stage(mode='auto',model_kw=6.,model_valid=True,window_kw=6.)['watts'],6000.)

    def test_zero_is_not_unknown_and_pump_is_not_heating(self):
        self.readings['total']['value']=1400
        for e,v in dict(a=1300,b=0,c=100).items():self.readings[e]['value']=v
        self.assertEqual(self.stage(thermostat='off')['watts'],0.)

    def test_no_restart_backfill_or_exact_energy_from_nominal(self):
        ledger={}
        integrate_observation(ledger,self.at,6000.,'estimated')
        integrate_observation(ledger,self.at+15,6000.,'estimated')
        self.assertAlmostEqual(ledger['estimated_kwh'],.025)
        integrate_observation(ledger,self.at+600,None,'unknown',restart=True)
        self.assertEqual(ledger['measured_kwh'],0.)
        self.assertEqual(ledger['estimated_kwh'],.025)
        self.assertEqual(ledger['unknown_seconds'],585.)

    def app(self):
        app=adapter.HeatingClimate();app.cfg=dict(power_accounting_method='inlet',power_inlet_config=self.config,
            thermostat='climate.boiler',room='sensor.room',energy='sensor.legacy',boiler_power_mode='fixed_6')
        app.memory=dict(owned=True,last_command='heat',modulation_profile=dict(bands=[],minutes=0,intervals=0))
        app.model=dict(DEFAULT_MODEL);app.now=lambda:datetime.fromtimestamp(self.at,timezone.utc)
        app.raw=lambda key,attribute=None:{'energy':446.1552,'mode':'Автоматичне керування','armed':'on','return':25.}.get(key)
        def snapshot(stamp):
            app.power_snapshot_at=stamp
            return {**{'sensor.'+e:dict(r,at=stamp) for e,r in self.readings.items()},
                'climate.boiler':dict(value='heat',at=stamp,target=25.,temperature=23.)}
        app.live_source_snapshot=snapshot;app.grid_state=lambda:'on'
        app.published={};app.publish=lambda entity,state,**attrs:app.published.update({entity:dict(state=state,**attrs)})
        app.get_state=lambda *a,**k:(_ for _ in ()).throw(AssertionError('No legacy source may be read'))
        return app

    def test_adapter_debounce_counter_continuity_and_no_faza_reads(self):
        app=self.app()
        for offset,expected in ((0,None),(15,6000.),(30,6000.)):
            with patch('heating_climate.time.time',return_value=self.at+offset):self.assertEqual(app.update_heating_power(),expected)
        self.assertAlmostEqual(app.val('energy'),446.1802)
        self.assertEqual(app.memory['inlet_energy']['measured_kwh'],0.)
        self.assertEqual(app.power_quality,'estimated')
        self.assertEqual(app.published['sensor.heating_energy_total']['origin_kwh'],446.1552)
        app.inlet_accounting_started=False
        with patch('heating_climate.time.time',return_value=self.at+600):app.update_heating_power()
        self.assertAlmostEqual(app.val('energy'),446.1802)
        self.assertGreater(app.memory['inlet_energy']['unknown_seconds'],500)

    def test_adapter_grid_off_overrides_inlet_and_unknown_stays_unknown(self):
        for grid,w in (('off',0.),('unknown',None)):
            app=self.app();app.grid_state=lambda:grid
            with patch('heating_climate.time.time',return_value=self.at):self.assertEqual(app.update_heating_power(),w)

    def test_month_boundary_prorates_nominal_energy_and_preserves_legacy_month(self):
        app=self.app();app.cfg['monthly_energy']='legacy_month'
        app.get_state=lambda entity,**kw:17. if entity=='legacy_month' else None
        start=datetime(2026,12,31,23,59,20,tzinfo=timezone.utc).timestamp()
        for offset in (0,15,30,45):
            app.now=lambda:datetime.fromtimestamp(start+offset,timezone.utc)
            with patch('heating_climate.time.time',return_value=start+offset):app.update_heating_power()
        months=app.memory['inlet_energy']['months']
        self.assertEqual(months['2026-12']['origin_kwh'],17.)
        self.assertEqual(months['2027-01']['origin_kwh'],0.)
        self.assertAlmostEqual(sum(m['calculated_kwh'] for m in months.values()),.05)
        self.assertAlmostEqual(months['2027-01']['calculated_kwh'],5*6/3600)

    def test_nominal_response_does_not_read_legacy_power_or_claim_physical_confirmation(self):
        app=self.app();app.current_power_w=6000.;app.power_source='fallback_inlet_stage'
        app.decision_event=lambda *a,**k:None;app.save=lambda:None
        with patch('heating_recovery.time.time',return_value=self.at):app.verify_boiler_response()
        self.assertFalse(app.memory['command_diagnostic']['boiler_confirmed'])
        self.assertTrue(app.memory['command_diagnostic']['response_estimated'])
        self.assertIsNone(app.memory['command_diagnostic']['confirmed_power_w'])

    def test_native_auto_collects_return_model_window_without_controller_ownership(self):
        app=self.app();app.cfg['boiler_power_mode']='auto';app.memory['owned']=False
        for e,v in dict(total=10300,a=4300,b=3000,c=3000).items():self.readings[e]['value']=v
        snapshot=app.live_source_snapshot
        def native(stamp):
            result=snapshot(stamp);result['climate.boiler']['value']='auto';return result
        app.live_source_snapshot=native
        with patch('heating_climate.power_profile',return_value=dict(validated=True,assumed=False)),patch('heating_climate.modulation_power',return_value=3.):
            for sec in range(0,301,15):
                with patch('heating_climate.time.time',return_value=self.at+sec):power=app.update_heating_power()
        self.assertIsNone(power)
        self.assertEqual(app.power_observation['reason'],'return_model_conflict')

    def test_invalid_duplicate_config_and_atomic_method_selection(self):
        cfg=deepcopy(self.config);cfg['loads'][1]['entities']=['sensor.grid']
        with self.assertRaises(ValueError):validate_system_settings(dict(power_inlet_config=cfg),{},DEFAULT_MODEL)
        with self.assertRaises(ValueError):validate_system_settings(dict(power_accounting_method='inlet'),{},DEFAULT_MODEL)
        clean,_=validate_system_settings(dict(power_accounting_method='inlet',power_inlet_config=self.config),{},DEFAULT_MODEL)
        self.assertEqual(clean['power_accounting_method'],'inlet')

    def test_settings_survive_restart_without_losing_other_parameters(self):
        app=self.app();app.cfg={};app.memory.update(equipment_settings=dict(power_accounting_method='inlet',power_inlet_config=self.config),
            system_settings=dict(fire_start_delta=6.))
        app.configure_ventilation=lambda:None;app.log=lambda *a,**k:None
        app.restore_system_settings()
        self.assertEqual(app.cfg['power_accounting_method'],'inlet')
        self.assertEqual(app.cfg['fire_start_delta'],6.)

    def test_calculated_cycle_spend_reduces_soft_remaining_without_becoming_measured(self):
        app=self.app();app.power_source='fallback_inlet_stage'
        now=datetime(2026,11,2,0,0,tzinfo=timezone.utc)
        settings=dict(start='23:00',end='07:00')
        for sec in (0,15,30):
            app.observe_cycle_energy(datetime.fromtimestamp(now.timestamp()+sec,timezone.utc),settings,6000.,100.+sec*6/3600,'on',True)
        entry=next(iter(app.memory['cycle_energy'].values()))
        self.assertEqual(entry['observed_kwh'],0.)
        self.assertAlmostEqual(entry['calculated_kwh'],.05)
        entry['initial_plan']=dict(kwh=1.)
        self.assertAlmostEqual(cycle_energy_allowance(entry)['remaining_kwh'],.95)
        self.assertFalse(entry['complete'])

    def test_native_nominal_energy_is_exported_as_other_not_automatic(self):
        app=self.app();app.power_source='fallback_inlet_stage'
        now=datetime(2026,11,2,0,0,tzinfo=timezone.utc)
        for sec in (0,15,30):
            app.observe_cycle_energy(datetime.fromtimestamp(now.timestamp()+sec,timezone.utc),dict(start='23:00',end='07:00'),6000.,100.+sec*6/3600,'on',False)
        app.publish_plan_history()
        cycle=app.published['sensor.heating_plan_history']['energy_cycles'][0]
        self.assertAlmostEqual(cycle['calculated_other_kwh'],.05)
        self.assertEqual(float(cycle['automatic_kwh']),0.)


if __name__=='__main__':unittest.main()
