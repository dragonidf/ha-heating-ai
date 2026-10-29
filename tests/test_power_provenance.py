"""Power-source contracts with fake HA states, never live commands."""
import unittest
from datetime import datetime
from zoneinfo import ZoneInfo
from unittest.mock import patch
from test_adapter_modulation import HeatingClimate
from heating_model import HeatingPowerEstimator

class PowerProvenanceTests(unittest.TestCase):
    def app(self):
        app=HeatingClimate();app.memory={};app.at=datetime(2026,10,29,12,tzinfo=ZoneInfo('Europe/Kiev'))
        app.now=lambda:app.at;app.stage='off';app.power_source='unavailable'
        app.cfg=dict(power_balance_sources=['total','local1','local2','local3','inverter'],
            fallback_phase_power=['input1','input2','input3'],input_heartbeat=['voltage'],inverter_heartbeat=['alive'])
        app.stale=set();app.fresh=lambda entity,seconds:entity not in app.stale
        app.get_state=lambda entity,**kw:4000.
        app.raw=lambda key:'on' if key in ('connection','grid') else 'heat'
        app.val=lambda key,default=None,attribute=None:dict(power_balance=3000.,grid_voltage=230.,thermostat=20.).get(key,default)
        app.power_estimator=HeatingPowerEstimator(confirm=10.);app.fallback_interval=False
        app.published={};app.publish=lambda entity,state,**attrs:app.published.update({entity:dict(state=state,**attrs)})
        return app

    def run_power(self,app):
        for offset in (0,5,10,15,20):
            with patch('heating_climate.time.time',return_value=app.at.timestamp()+offset):power=app.update_heating_power()
        return power,app.published['sensor.heating_power_confirmed']

    def test_stale_component_cannot_be_confirmed_by_a_fresh_balance_or_voltage(self):
        app=self.app();app.stale.add('local2');power,attrs=self.run_power(app)
        self.assertNotEqual(attrs['power_source'],'balance')
        self.assertIn('local2',attrs['stale_sources'])
        self.assertNotEqual(attrs['power_quality'],'measured')

    def test_stale_input_phase_cannot_be_refreshed_by_another_phase_or_voltage(self):
        app=self.app();app.stale.update(('local2','input2'));original=app.raw
        app.raw=lambda key:'auto' if key=='thermostat' else original(key)
        power,attrs=self.run_power(app)
        self.assertIsNone(power)
        self.assertEqual(attrs['power_quality'],'unknown')

    def test_unrequested_actual_power_is_retained_and_labelled(self):
        app=self.app();power,attrs=self.run_power(app)
        self.assertEqual(power,3000.)
        self.assertEqual(attrs['power_quality'],'measured')
        self.assertTrue(attrs['power_without_request'])
        self.assertFalse(attrs['requested_heating'])

    def test_each_real_stage_and_zero_survive_the_existing_filter(self):
        for watts in (0.,3000.,6000.,9000.):
            app=self.app();app.val=lambda key,default=None,attribute=None:watts if key=='power_balance' else 230.
            power,attrs=self.run_power(app);self.assertEqual(power,watts)
            self.assertEqual(attrs['power_quality'],'measured')

    def test_legacy_config_and_missing_timestamps_do_not_crash(self):
        app=self.app();app.cfg.pop('power_balance_sources');self.assertEqual(self.run_power(app)[0],3000.)
        app=HeatingClimate();app.get_state=lambda *a,**kw:dict(state='1',last_updated=None)
        self.assertFalse(app.fresh('test',300))
