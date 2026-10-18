import unittest
from datetime import datetime
from zoneinfo import ZoneInfo
from unittest.mock import patch
from threading import RLock
from test_adapter_modulation import HeatingClimate


class PlannedExecutionTests(unittest.TestCase):
    def cycle(self, planned=True, room=21.5, peak=21.5, overheat=False, grid='on', once=False,weather_missing=42,outage_sequence=None):
        app=HeatingClimate()
        at=datetime(2026,10,14,0,5,tzinfo=ZoneInfo('Europe/Kiev'))
        app.climate_tz=at.tzinfo
        app.now=lambda:at
        app.cfg=dict(allow_actuation=True,return_heartbeat=[],monthly_energy='monthly',night_target='night')
        app.memory=dict(modulation_profile=dict(bands=[],intervals=0,minutes=0))
        app.model=dict(validated=True,version=4)
        app.samples=[];app.weather=[];app.power_source='balance';app.next_compute=0
        app.ai_summary_lock=RLock();app.ai_summary_attributes=None
        app.outage_signature=(None,None)
        app.source_temperature=lambda:(room,'Вітальня',room,22.5)
        app.val=lambda key,default=None,attribute=None:dict(
            **{'return':22},outside=11,target=22,lower=21.5,upper=22.5,
            night_target=21.5,night_lower=21,night_upper=22,monthly_budget=0,tariff=1.32).get(key,default)
        app.raw=lambda key,attribute=None:dict(start='23:00',end='07:00',sleep_start='23:00',sleep_end='07:00',
            strategy='Тільки в дозволені години',armed='on',mode='Автоматичне керування',thermostat='heat').get(key)
        outage=[None]
        if outage_sequence:
            app.cfg.update(outage_source='sensor.svitlo',outage_emergency='binary_sensor.emergency')
        app.get_state=lambda entity,**kwargs:outage[0] if entity=='sensor.svitlo' else 'off' if entity=='binary_sensor.emergency' else None
        app.heartbeat_fresh=lambda *a:True
        app.grid_state=lambda:grid
        app.update_heating_power=lambda:0
        app.stage_update=lambda *a:None
        app.fireplace=lambda *a:(0,False)
        app.once_state=lambda *a:(once,'test')
        app.sample=lambda *a:None;app.energy_tick=lambda:None;app.fan_tick=lambda *a:None
        app.resume_temporary_fallback=lambda:None;app.save=lambda:None;app.journal=lambda *a:None
        app.hour_mae=lambda:0.1;app.summary=lambda:{}
        app.actual_session_energy=lambda:None
        app.published={}
        app.publish=lambda entity,state,**attrs:app.published.update({entity:dict(state=state,**attrs)})
        actions=[];app.apply_action=lambda action,once:actions.append(action)
        forecast=dict(heat=planned,minimum=21.3,energy=3,horizon_hours=24,outage_shifted=False,
            plan_stable=True,stability_reason='test',outage_preheat=False,outside_minutes=0,comfort_shortfall=False,
            outage_status='test',heat_segments=[dict(start=at.timestamp(),end=at.timestamp()+1800)] if planned else [],
            schedule_updated=None,schedule_known_until=None,weather_known_until=None,conditional_heat_minutes=0,
            schedule_complete=True,weather_complete=True,weather_missing_minutes=weather_missing,
            split_comfort_applied=True,session_kwh={},night_air_target=21.5,
            split_reason='test',morning_min_minutes=30,morning_runs_minutes=[60],curve=[])
        with patch('heating_climate.time.time',return_value=at.timestamp()), \
             patch('heating_climate.stable_plan',return_value=(forecast,{})) as planner, \
             patch('heating_climate.coast',return_value=(peak,[])), \
             patch('heating_climate.coast_exceeds_profile',return_value=overheat):
            for phase in outage_sequence or (None,):
                outage[0]=dict(state='No data' if phase=='normal' else 'Grid ON',attributes=dict(
                    region='kiivska-oblast',queue='3.1',updated=at.isoformat(),
                    now_status='nosched' if phase=='normal' else 'on',
                    today_48half=[] if phase=='normal' else ['on']*48)) if phase else None
                app.tick_inner()
            self.planner_calls=planner.call_count
            self.planner_settings=planner.call_args.args[7]
        self.forecast_status=app.published['sensor.heating_forecast']
        self.outage_status=app.published['sensor.heating_outage_schedule']
        return actions,app.published['sensor.heating_controller']

    def test_midnight_session_runs_at_night_target(self):
        actions,status=self.cycle()
        self.assertEqual(actions,['heat'])
        self.assertEqual(status['reason'],'Заплановано нагрів')
    def test_slightly_above_soft_target_still_obeys_safe_plan(self):
        self.assertEqual(self.cycle(room=21.6,peak=21.6)[0],['heat'])
    def test_sleep_upper_still_stops(self):
        self.assertEqual(self.cycle(room=22)[0],['off'])
    def test_coast_overheating_still_stops(self):
        self.assertEqual(self.cycle(overheat=True)[0],['off'])
    def test_missing_grid_still_stops(self):
        self.assertEqual(self.cycle(grid='off')[0],['off'])
    def test_no_planned_heat_does_not_start(self):
        self.assertEqual(self.cycle(planned=False)[0],['off'])
    def test_one_shot_keeps_existing_soft_coast_guard(self):
        self.assertEqual(self.cycle(planned=False,once=True)[0],['off'])
    def test_forecast_diagnostic_is_published_with_tolerated_missing_minutes(self):
        for missing in (0,42,None):
            with self.subTest(missing=missing):
                self.cycle(weather_missing=missing)
                self.assertEqual(self.forecast_status['weather_missing_minutes'],missing)
                self.assertTrue(self.forecast_status['weather_complete'])
                self.assertEqual(self.forecast_status['horizon_hours'],24)
    def test_normal_mode_does_not_override_actual_grid_loss(self):
        for grid,expected in [('on','heat'),('off','off'),('unknown','off')]:
            with self.subTest(grid=grid):
                actions,_=self.cycle(grid=grid,outage_sequence=['normal'])
                self.assertEqual(actions,[expected])
                self.assertEqual(self.outage_status['mode'],'normal')
                self.assertIsNone(self.outage_status['warning'])
                self.assertTrue(self.planner_settings['outage_schedule']['valid'])
    def test_mode_transitions_recompute_with_unchanged_payload_timestamp(self):
        self.cycle(outage_sequence=['normal','scheduled','normal'])
        self.assertEqual(self.planner_calls,3)
        self.assertEqual(self.outage_status['mode'],'normal')
        self.assertEqual(self.outage_status['outages'],[])
    def test_unchanged_normal_mode_does_not_recompute_for_virtual_slot_movement(self):
        self.cycle(outage_sequence=['normal','normal'])
        self.assertEqual(self.planner_calls,1)

if __name__=='__main__':unittest.main()
