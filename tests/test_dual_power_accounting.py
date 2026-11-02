"""Two non-overlapping electrical balances, not two sums of the same loads."""
from copy import deepcopy
import unittest
from unittest.mock import patch
import test_inlet_accounting as inlet_fixture
import heating_fallback as power


class DualPowerTests(unittest.TestCase):
    def setUp(self):
        self.at=1800000000.
        self.config=dict(total='sensor.total',phases=['sensor.a','sensor.b','sensor.c'],
            loads=[dict(physical_id=k,entities=['sensor.'+k],signed=k=='grid')
                   for k in ('grid','dhw','dishwasher','washer','dryer')],
            groups=[dict(physical_id='group'+str(i),entities=['sensor.g'+str(i),'sensor.cloud'+str(i)]) for i in range(3)])
        values=dict(total=10000,a=4000,b=3000,c=3000,grid=100,dhw=1200,dishwasher=1500,washer=800,dryer=400,g0=2000,g1=1000,g2=900)
        self.readings={'sensor.'+k:dict(value=v,at=self.at,unit='W') for k,v in values.items()}

    def assess(self):
        return power.inlet_power(self.config,self.readings,self.at,'fixed_6','heat',25.,23.,controller_request=True)

    def test_primary_uses_groups_once_not_groups_plus_appliances(self):
        result=self.assess()
        self.assertEqual(result['watts'],6000.)
        self.assertEqual(result['method'],'group_balance')
        self.assertEqual(result['deducted_w'],4000.)
        self.assertEqual(result['reserve']['watts'],6000.)
        self.assertEqual(result['quality'],'estimated')
        self.assertFalse(result['independent'])

    def test_each_failed_local_group_can_use_its_cloud_alias_in_kw(self):
        for i in range(3):
            readings=deepcopy(self.readings)
            self.readings['sensor.cloud'+str(i)]=dict(value=readings['sensor.g'+str(i)]['value']/1000,at=self.at,unit='kW')
            self.readings['sensor.g'+str(i)]['value']='unavailable'
            self.assertEqual(self.assess()['method'],'group_balance')
            self.assertEqual(self.assess()['watts'],6000.)
            self.readings=readings

    def test_missing_any_group_falls_back_to_all_appliances_without_faza(self):
        for i in range(3):
            saved=self.readings.pop('sensor.g'+str(i))
            result=self.assess()
            self.assertEqual(result['method'],'appliance_stage')
            self.assertEqual(result['watts'],6000.)
            self.assertIn('sensor.dishwasher',result['selected_sources'])
            self.assertIn('sensor.washer',result['selected_sources'])
            self.assertIn('sensor.dryer',result['selected_sources'])
            self.readings['sensor.g'+str(i)]=saved

    def test_primary_does_not_require_separately_metered_appliance_to_be_online(self):
        del self.readings['sensor.dryer']
        self.assertEqual(self.assess()['watts'],6000.)
        self.assertEqual(self.assess()['method'],'group_balance')

    def test_both_methods_missing_never_invent_zero(self):
        del self.readings['sensor.dryer'];del self.readings['sensor.g0']
        self.assertIsNone(self.assess()['watts'])

    def test_primary_reports_variable_actual_residual_instead_of_nominal_rating(self):
        self.readings['sensor.total']['value']=10210
        self.assertEqual(self.assess()['watts'],6210.)
        self.assertEqual(self.assess()['stage_kw'],6)

    def test_impossible_six_kw_on_one_input_phase_is_not_a_boiler_stage(self):
        self.readings['sensor.a']['value']=10000
        self.readings['sensor.b']['value']=self.readings['sensor.c']['value']=0
        self.assertIsNone(self.assess()['watts'])

    def test_group_balance_can_expose_heating_without_a_controller_request(self):
        result=power.inlet_power(self.config,self.readings,self.at,'fixed_6','off',20.,23.)
        self.assertEqual(result['watts'],6000.)
        self.assertEqual(result['reserve']['reason'],'power_without_demand')

    def test_stale_total_and_signed_export_invalidate_both_methods(self):
        self.readings['sensor.total']['at']-=301
        self.assertIsNone(self.assess()['watts'])
        self.readings['sensor.total']['at']=self.at
        self.readings['sensor.grid']['value']=-1800
        self.assertIsNone(self.assess()['watts'])

    def test_conflicting_aliases_do_not_silently_pick_one(self):
        self.readings['sensor.cloud0']=dict(value=6.,at=self.at,unit='kW')
        self.assertEqual(self.assess()['method'],'appliance_stage')
        self.assertEqual(self.assess()['primary']['reason'],'load_alias_conflict')

    def test_stale_standby_is_only_an_explicit_estimate_with_live_same_device_voltage(self):
        self.readings['sensor.g0'].update(value=0,at=self.at-3600)
        self.readings['sensor.g1']['value']=2000
        self.readings['sensor.g2']['value']=1900
        self.config['heartbeats']={'sensor.g0':['sensor.g0_voltage']}
        self.readings['sensor.g0_voltage']=dict(value=230,at=self.at,unit='V')
        result=self.assess()
        self.assertEqual(result['method'],'group_balance')
        self.assertEqual(result['watts'],6000.)
        self.assertEqual(result['held_sources'][0]['power_at'],self.at-3600)
        self.assertEqual(self.readings['sensor.g0']['at'],self.at-3600)
        self.readings['sensor.g0']['value']='unavailable'
        self.assertNotEqual(self.assess().get('method'),'group_balance')

    def test_more_loads_are_bounded_and_duplicate_aliases_rejected(self):
        self.assertEqual(len(power.validate_inlet_config(self.config)['loads']),5)
        self.config['groups'][0]['entities']=['sensor.dhw']
        with self.assertRaises(ValueError):power.validate_inlet_config(self.config)

    def test_adapter_debounces_stage_not_exact_watts_and_never_rereads_legacy_counter(self):
        fixture=inlet_fixture.InletAccountingTests();fixture.setUp();app=fixture.app()
        app.cfg['power_inlet_config']=self.config
        app.live_source_snapshot=lambda stamp:(setattr(app,'power_snapshot_at',stamp) or
            {**{e:dict(r,at=stamp) for e,r in self.readings.items()},
             'climate.boiler':dict(value='heat',at=stamp,target=25.,temperature=23.)})
        for sec in (0,15,30):
            self.readings['sensor.total']['value']=10000+sec
            with patch('heating_climate.time.time',return_value=self.at+sec):value=app.update_heating_power()
        self.assertEqual(value,6030.)
        raw=app.raw
        app.raw=lambda key,*a,**k:(_ for _ in ()).throw(AssertionError('Legacy counter reread after seed')) if key=='energy' else raw(key,*a,**k)
        with patch('heating_climate.time.time',return_value=self.at+45):app.update_heating_power()


if __name__=='__main__':unittest.main()
