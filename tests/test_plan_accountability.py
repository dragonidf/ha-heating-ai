"""Energy and frozen-temperature contracts, with synthetic source data only."""
import copy
import json
import unittest
from datetime import datetime,timedelta
from unittest.mock import patch
from zoneinfo import ZoneInfo
from test_adapter_modulation import HeatingClimate
from heating_environment import observe_floor
from heating_model import DEFAULT_MODEL,trajectory_metrics
from heating_recovery import HeatingRecovery


class PlanAccountabilityTests(unittest.TestCase):
    def setUp(self):
        self.at=datetime(2026,10,31,23,tzinfo=ZoneInfo('Europe/Kiev'))
        self.settings=dict(start='23:00',end='07:00')
    def test_cold_return_during_heating_cannot_erase_integrated_heat(self):
        old=dict(at=self.at.timestamp(),estimate=34.,power_kw=6.,heating_since=self.at.timestamp()-600)
        state,value=observe_floor(old,self.at.timestamp()+15,21.,25.,6.,'on',DEFAULT_MODEL)
        self.assertGreater(value,33.9)
        self.assertTrue(state['energy_retained'])
    def test_identical_outage_metadata_does_not_schedule_expensive_recompute(self):
        app=HeatingClimate();app.next_compute=123.;app.memory={};app.climate_tz=self.at.tzinfo
        app.now=lambda:self.at
        app.read_outage_schedule=lambda:dict(mode='normal',valid=True,outages=[],slots=[],emergency=False)
        from heating_outages import signature
        app.outage_signature=signature(app.read_outage_schedule())
        app.outages_changed('calendar.test','all',{},dict(last_updated='new'))
        self.assertEqual(app.next_compute,123.)
        app.read_outage_schedule=lambda:dict(mode='scheduled',valid=True,outages=[dict(start=1,end=2)],slots=[])
        app.outages_changed('calendar.test','all',{},{});self.assertEqual(app.next_compute,0)
    def test_budget_keeps_first_plan_and_finished_cycle_after_window_rollover(self):
        app=HeatingRecovery();app.memory={};app.power_source='balance'
        before=self.at-timedelta(hours=1);app.observe_cycle_energy(before,self.settings,0,100.,'on',False)
        from heating_model import heating_cycle
        c=heating_cycle(before,self.settings)
        f=dict(plan_id='original',morning_reserve=dict(c,end_cycle_planned_kwh=10.))
        a=app.cycle_budget(before,f,{})
        app.cycle_budget(before+timedelta(minutes=5),dict(f,plan_id='new',morning_reserve=dict(c,end_cycle_planned_kwh=16.)),{})
        self.assertEqual(a['initial_plan']['kwh'],10.)
        entry=next(iter(app.memory['cycle_energy'].values()))
        self.assertEqual(entry['initial_plan']['kwh'],10.)
        self.assertEqual(entry['latest_plan']['kwh'],16.)
    def test_legacy_baseline_is_not_replaced_on_upgrade_mid_cycle(self):
        app=HeatingRecovery();app.memory={};app.power_source='balance'
        app.observe_cycle_energy(self.at,self.settings,3000.,100.,'on',True)
        from heating_model import heating_cycle
        c=heating_cycle(self.at,self.settings);entry=next(iter(app.memory['cycle_energy'].values()))
        entry['baseline']=dict(at=self.at.timestamp()-3600,kwh=10.)
        result=app.cycle_budget(self.at,dict(plan_id='after-upgrade',morning_reserve=dict(c,end_cycle_planned_kwh=20.)),{})
        self.assertEqual(result['initial_plan']['kwh'],10.)
        self.assertTrue(result['initial_plan']['legacy'])

    def test_counter_jump_is_unknown_not_a_hundred_kwh_of_heating(self):
        app=HeatingRecovery();app.memory={};app.power_source='balance'
        app.observe_cycle_energy(self.at,self.settings,3000.,100.,'on',True)
        app.observe_cycle_energy(self.at+timedelta(seconds=15),self.settings,3000.,200.,'on',True)
        entry=next(iter(app.memory['cycle_energy'].values()))
        self.assertEqual(entry['observed_kwh'],0.)
        self.assertNotIn('meter_kwh',entry)
        self.assertGreater(entry['unknown_seconds'],0.)

    def test_dispatch_rechecks_session_expiry_after_slow_planning(self):
        from test_fallback_recovery import FallbackRecoveryTests
        app=FallbackRecoveryTests().app(hour=7);app.at=app.at.replace(minute=0,second=10)
        stamp=app.at.timestamp();app.memory['last_command']='heat';app.memory['command_at']=stamp-300
        app.decision_context=dict(forecast=dict(heat_segments=[dict(start=stamp-40,end=stamp-10)]))
        with patch('heating_recovery.time.time',return_value=stamp):app.apply_action('heat',False)
        self.assertEqual(app.memory['last_command'],'off')
        self.assertLess(app.attrs['temperature'],app.attrs['current_temperature'])

    def test_deficit_names_temperature_limit_and_time_in_same_trajectory(self):
        s=dict(target=21.5,lower=21.,upper=22.)
        rows=[dict(ts=self.at.timestamp()+300,temperature=20.8)]
        m=trajectory_metrics(self.at,21.1,rows,s,self.at.timestamp()+300)
        self.assertEqual(m['first_violation_temperature'],20.8)
        self.assertEqual(m['first_violation_lower'],21.)
        self.assertAlmostEqual(m['first_violation_shortfall'],.2)


class FrozenTemperatureTests(unittest.TestCase):
    def setUp(self):
        self.at=datetime(2026,10,31,23,tzinfo=ZoneInfo('Europe/Kiev'))
        self.app=HeatingRecovery();self.app.memory={};self.app.model=dict(DEFAULT_MODEL)
        self.app.power_source='balance';self.app.publish=lambda *a,**k:None
        self.settings=dict(start='23:00',end='07:00')
        self.forecast=dict(plan_id='twelve-hours-early',curve=[dict(ts=self.at.timestamp()+i*1800,temperature=21.+i*.01) for i in range(-23,25)])
    def observe(self,at,power=0.,room=21.,forecast=None):
        self.app.observe_temperature_history(at,self.settings,room,power,room is not None,forecast)
    def test_twelve_hour_forecast_is_frozen_and_survives_restart(self):
        self.observe(self.at-timedelta(hours=12),forecast=self.forecast)
        self.observe(self.at-timedelta(seconds=15))
        self.observe(self.at,power=6000.)
        original=copy.deepcopy(next(iter(self.app.memory['temperature_history'].values())))
        self.assertEqual(original['forecast']['plan_id'],'twelve-hours-early')
        self.app.memory=json.loads(json.dumps(self.app.memory))
        changed=dict(self.forecast,plan_id='late',curve=[dict(ts=self.at.timestamp()+3600,temperature=25.)])
        self.observe(self.at+timedelta(minutes=5),power=6000.,forecast=changed)
        current=next(iter(self.app.memory['temperature_history'].values()))
        self.assertEqual(current['forecast'],original['forecast'])
    def test_missing_advance_forecast_is_not_reconstructed(self):
        self.observe(self.at-timedelta(hours=11),forecast=self.forecast)
        self.observe(self.at-timedelta(seconds=15))
        self.observe(self.at,power=6000.)
        row=next(iter(self.app.memory['temperature_history'].values()))
        self.assertIsNone(row['forecast'])
        self.observe(self.at+timedelta(minutes=5),power=6000.,forecast=self.forecast)
        self.assertIsNone(row['forecast'])

    def test_heat_tail_after_seven_does_not_occupy_tomorrows_forecast_cycle(self):
        self.observe(self.at-timedelta(hours=12),forecast=self.forecast)
        self.observe(self.at-timedelta(seconds=15));self.observe(self.at,power=6000.)
        self.observe(self.at+timedelta(hours=8,seconds=-15),power=6000.)
        self.observe(self.at+timedelta(hours=8),power=6000.)
        self.assertEqual(len(self.app.memory['temperature_history']),1)
        future=copy.deepcopy(self.forecast)
        future['plan_id']='tomorrow-original'
        for point in future['curve']:point['ts']+=86400
        self.observe(self.at+timedelta(hours=12),forecast=future)
        self.observe(self.at+timedelta(days=1,seconds=-15))
        self.observe(self.at+timedelta(days=1),power=6000.)
        entries=sorted(self.app.memory['temperature_history'].values(),key=lambda v:v['started_at'])
        self.assertEqual(entries[-1]['forecast']['plan_id'],'tomorrow-original')
    def test_sensor_loss_is_a_gap_and_grid_loss_does_not_fabricate_temperatures(self):
        self.observe(self.at-timedelta(hours=12),forecast=self.forecast)
        self.observe(self.at-timedelta(seconds=15));self.observe(self.at,power=6000.)
        self.observe(self.at+timedelta(minutes=5),room=None)
        row=next(iter(self.app.memory['temperature_history'].values()))
        self.assertIsNone(row['actual'][-1]['temperature'])
        self.observe(self.at+timedelta(minutes=10),power=0.,room=21.2)
        self.assertEqual(row['actual'][-1]['temperature'],21.2)

if __name__=='__main__':unittest.main()
