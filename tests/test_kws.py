import unittest
from datetime import datetime,timezone
from unittest.mock import patch
from test_adapter_modulation import HeatingClimate

class KWSTests(unittest.TestCase):
    def app(self):
        a=HeatingClimate();a.cfg={'power_accounting_method':'kws','energy':'old'};a.memory={};a.stage='off'
        a.now=lambda:datetime.fromtimestamp(self.at,timezone.utc)
        self.at=10000.;self.rows={};a.get_state=lambda e,**kw:self.rows.get(e)
        a.publish=lambda *args,**kw:None;a.raw=lambda *args:100.;a.save=lambda:None
        for p in 'abc':self.row('potuzhnist_'+p,0,'kW');self.row('napruga_'+p,230,'V')
        self.row('energiia',50,'kWh');return a
    def row(self,key,value,unit,age=0):
        self.rows['sensor.energy_meter_kotel_'+key]={'state':str(value),'attributes':{'unit_of_measurement':unit},'last_updated':datetime.fromtimestamp(self.at-age,timezone.utc).isoformat()}
    def tick(self,a):
        with patch('heating_climate.time.time',return_value=self.at):return a.update_kws_power()
    def test_phases_not_delayed_total_and_modes(self):
        a=self.app()
        for kw in (0,3,6,9,4.9):
            for i,p in enumerate('abc'):self.row('potuzhnist_'+p,min(3,max(0,kw-3*i)),'kW')
            self.assertAlmostEqual(self.tick(a),kw*1000)
        self.assertEqual(a.cfg.get('boiler_power_mode'),None)
    def test_unknown_stale_nonzero_and_quiet_zero(self):
        a=self.app();self.row('potuzhnist_a',1,'kW',200);self.assertIsNone(self.tick(a))
        self.row('potuzhnist_a',0,'kW',1000);self.assertEqual(self.tick(a),0)
        self.rows['sensor.energy_meter_kotel_potuzhnist_a']['state']='unavailable';self.assertIsNone(self.tick(a))
    def test_counter_restart_repeat_gap_and_reset(self):
        a=self.app();self.tick(a);self.assertEqual(a.val('energy'),100)
        self.at+=900;self.row('energiia',51.5,'kWh');self.tick(a);self.assertEqual(a.val('energy'),101.5)
        self.tick(a);self.assertEqual(a.val('energy'),101.5)
        import json
        a.memory=json.loads(json.dumps(a.memory))  # Restart uses the same persisted anchor.
        self.at+=900;self.row('energiia',0,'kWh');self.tick(a);self.assertEqual(a.val('energy'),101.5)
        self.at+=900;self.row('energiia',.5,'kWh');self.tick(a);self.assertEqual(a.val('energy'),102)
        self.at+=900;self.row('energiia',1,'kWh');self.tick(a);self.assertEqual(a.val('energy'),102.5)
    def test_stage_short_skew_does_not_change_confirmed_mode(self):
        a=self.app();a.stage='6 кВт'
        with patch('heating_climate.time.time',return_value=self.at):a.stage_update(3)
        self.at+=5
        with patch('heating_climate.time.time',return_value=self.at):a.stage_update(6)
        self.assertEqual(a.stage,'6 кВт')
        self.at+=20
        with patch('heating_climate.time.time',return_value=self.at):a.stage_update(9)
        self.at+=20
        with patch('heating_climate.time.time',return_value=self.at):a.stage_update(9)
        self.assertEqual(a.stage,'9 кВт')

    def test_units_invalid_energy_and_no_power_integration(self):
        a=self.app();self.tick(a)
        self.row('potuzhnist_a',3,'W');self.assertIsNone(self.tick(a))
        self.at+=900;self.row('energiia','unavailable','kWh');self.tick(a)
        self.assertEqual(a.val('energy'),100)
        self.at+=900;self.row('energiia',51,'kWh');self.tick(a)
        self.assertEqual(a.val('energy'),101)
        self.tick(a);self.assertEqual(a.val('energy'),101)

    def test_hardware_energy_bins_keep_interval_and_split_tariff_boundary(self):
        a=self.app();a.cfg['tariff']='tariff'
        a.raw=lambda key,attribute=None:4.32 if key=='tariff' else 100
        self.at=datetime(2026,11,11,6,50,tzinfo=timezone.utc).timestamp()
        self.row('energiia',50,'kWh');self.tick(a)
        for _ in range(3):
            self.at+=15
            with patch('heating_climate.time.time',return_value=self.at):a.energy_tick()
        self.at+=855;self.row('energiia',51,'kWh');self.tick(a)
        with patch('heating_climate.time.time',return_value=self.at):a.energy_tick()
        bins=a.memory['energy_bins'];self.assertAlmostEqual(sum(b['kwh'] for b in bins),1)
        self.assertEqual(len(bins),2);self.assertTrue(bins[0]['night']);self.assertFalse(bins[1]['night'])

    def test_cycle_fact_uses_delayed_counter_without_false_reset(self):
        from datetime import timedelta
        a=self.app();a.power_source='kws_phases';settings=dict(start='23:00',end='07:00')
        at=datetime(2026,11,11,6,50,tzinfo=timezone.utc)
        a.observe_cycle_energy(at,settings,6000,100,'on',True)
        a.observe_cycle_energy(at+timedelta(seconds=15),settings,6000,100,'on',True)
        a.observe_cycle_energy(at+timedelta(minutes=15),settings,6000,101.5,'on',True)
        self.assertAlmostEqual(sum(e['observed_kwh'] for e in a.memory['cycle_energy'].values()),1.0)  # Only 06:50–07:00 belongs to the heating cycle.
        self.assertFalse(any(e.get('counter_reset')for e in a.memory['cycle_energy'].values()))

    def test_repeated_fresh_reports_do_not_shorten_counter_interval(self):
        a=self.app();self.tick(a)
        self.at+=885;self.row('energiia',50,'kWh');self.tick(a)
        self.at+=15;self.row('energiia',51.5,'kWh');self.tick(a)
        self.assertEqual(a.val('energy'),101.5)

    def test_source_quality_separates_value_time_liveness_unit_and_provenance(self):
        a=self.app();self.row('potuzhnist_a',3,'kW',age=12)
        self.assertEqual(self.tick(a),3000)
        row=a.kws_source_quality['sensor.energy_meter_kotel_potuzhnist_a']
        self.assertEqual(row['value_reported_at'],self.at-12)
        self.assertEqual(row['observed_at'],self.at)
        self.assertEqual(row['unit'],'kW')
        self.assertEqual(row['quality'],'measured')
        self.assertTrue(row['online']);self.assertTrue(row['finite'])
        self.assertFalse(row['raw_ack'])
        self.row('potuzhnist_a',3,'W')
        self.assertIsNone(self.tick(a))
        self.assertEqual(a.kws_source_quality['sensor.energy_meter_kotel_potuzhnist_a']['reason'],'unit_mismatch')

    def test_K04_response_requires_fresh_measured_phase_sum_not_legacy_power(self):
        a=self.app();a.memory.update(owned=True,last_command='heat')
        a.raw=lambda key,*args:{'mode':'Автоматичне керування','armed':'on'}.get(key)
        a.grid_state=lambda:'on';a.decision_event=lambda *args,**kwargs:None
        a.notify_once=lambda *args:None
        a.enter_temporary_fallback=lambda *args:None
        for watts,quality,age,expected in ((3000,'measured',0,True),(6000,'measured',0,True),
                (9000,'measured',0,True),(0,'measured',0,False),(9000,'estimated',0,False),
                (9000,'measured',181,False)):
            a.current_power_w=watts;a.power_observation=dict(quality=quality,at=self.at-age)
            with patch('heating_climate.time.time',return_value=self.at):a.verify_boiler_response()
            self.assertEqual(a.memory['command_diagnostic']['boiler_confirmed'],expected)
