import sys
import types
import unittest
from datetime import datetime
from zoneinfo import ZoneInfo
from unittest.mock import patch

# AppDaemon is not installed in the offline test runtime. All HA I/O is fake.
for name in ('appdaemon','appdaemon.plugins','appdaemon.plugins.hass','appdaemon.plugins.hass.hassapi'):
    sys.modules.setdefault(name,types.ModuleType(name))
sys.modules['appdaemon.plugins.hass.hassapi'].Hass=object
from heating_climate import HeatingClimate
from heating_outages import schedule, signature
from heating_model import DEFAULT_MODEL

class AdapterModulationTests(unittest.TestCase):
    def fast_tick(self,at,segments,once=False,initial_demand=True,before_tick=None):
        app=HeatingClimate()
        app.climate_tz=ZoneInfo('Europe/Kiev')
        app.cfg=dict(allow_actuation=True,return_heartbeat=[],monthly_energy='monthly')
        app.memory=dict(demand=initial_demand,planned_heat_segments=segments,
                        modulation_profile=dict(bands=[],intervals=0,minutes=0))
        app.model=dict(DEFAULT_MODEL,validated=True)
        app.samples=[];app.weather=[]
        app.power_source='balance'
        app.next_compute=at.timestamp()+300
        app.outage_signature=signature(schedule(None,at))
        app.now=lambda:at
        app.source_temperature=lambda:(21.8,'Вітальня',21.8,22)
        app.val=lambda key,default=None,attribute=None:dict(return_=24,**{'return':24},outside=10,
            target=22,lower=21.5,upper=22.5,monthly_budget=0,tariff=4.32).get(key,default)
        app.raw=lambda key,attribute=None:dict(start='23:00',end='07:00',strategy='Тільки в дозволені години',
            armed='on',mode='Автоматичне керування',thermostat='heat').get(key)
        app.get_state=lambda *args,**kwargs:None
        app.heartbeat_fresh=lambda *args:True
        app.grid_state=lambda:'on'
        app.update_heating_power=lambda:3000
        app.stage_update=lambda *args:None
        app.fireplace=lambda *args:(0,False)
        app.once_state=lambda *args:(once,'test')
        app.publish=lambda *args,**kwargs:None
        app.sample=lambda *args:None
        app.energy_tick=lambda:None
        app.fan_tick=lambda *args:None
        actions=[]
        app.apply_action=lambda action,once:actions.append(action)
        if before_tick:before_tick(app)
        with patch('heating_climate.time.time',return_value=at.timestamp()):
            app.tick_inner()
        return actions
    def test_expired_session_stops_even_without_outage_data(self):
        at=datetime(2026,10,13,7,0,tzinfo=ZoneInfo('Europe/Kiev'))
        self.assertEqual(self.fast_tick(at,[dict(start=at.timestamp()-1800,end=at.timestamp())]),['off'])
    def test_old_primary_report_is_not_a_fresh_post_stop_peak(self):
        from unittest.mock import Mock
        at=datetime(2026,11,15,9,tzinfo=ZoneInfo('Europe/Kyiv'))
        for age,expected in ((0,True),(3600,False)):
            captured=[]
            def setup(app):
                app.temperature_quality={'primary_reported_at':at.timestamp()-age}
                app.observe_stop_peak=Mock();captured.append(app)
            self.fast_tick(at,[],before_tick=setup)
            self.assertEqual(captured[0].observe_stop_peak.call_args.args[-1],expected)
    def test_residual_gate_requires_fresh_actual_outdoor_source(self):
        from unittest.mock import Mock
        at=datetime(2026,11,15,9,tzinfo=ZoneInfo('Europe/Kyiv'))
        for fresh in (True,False):
            captured=[]
            def setup(app):
                app.sample=Mock();captured.append(app)
                app.cfg['outside']='sensor.outside'
                app.fresh=lambda *args:fresh
                app.temperature_quality={'primary_reported_at':at.timestamp()}
                app.learning_environment=lambda *args:({},dict(status='agree'))
                app.update_heating_power=lambda:0.
                app.equipment_status={'ready':True}
                app.memory.update(fire_phase='Неактивний',weather_at=at.timestamp())
                app.weather=[dict(ts=at.timestamp(),temperature=10.)]
            with patch('heating_climate.observe_residual',return_value={}) as observer:
                self.fast_tick(at,[],before_tick=setup)
            self.assertEqual(observer.call_args.args[7],fresh)
            self.assertEqual(captured[0].sample.call_args.args[8],fresh)
    def test_active_session_keeps_demand_and_one_shot_is_preserved(self):
        at=datetime(2026,10,13,6,30,tzinfo=ZoneInfo('Europe/Kiev'))
        self.assertEqual(self.fast_tick(at,[dict(start=at.timestamp(),end=at.timestamp()+1800)]),['heat'])
        self.assertEqual(self.fast_tick(at,[],once=True),['heat'])
    def test_future_session_starts_on_time_between_full_computations(self):
        at=datetime(2026,10,13,5,30,tzinfo=ZoneInfo('Europe/Kiev'))
        self.assertEqual(self.fast_tick(at,[dict(start=at.timestamp()-15,end=at.timestamp()+1800)],initial_demand=False),['heat'])
    def test_extra_energy_uses_cached_plan_without_imposing_a_new_schedule(self):
        at=datetime(2026,10,13,6,30,tzinfo=ZoneInfo('Europe/Kiev'))
        stamp=at.timestamp()
        for active,plan,expected in ((True,False,'heat'),(False,False,'off'),(False,True,'heat')):
            with self.subTest(active=active,plan=plan):
                def setup(app):
                    app.memory.update(last_command='heat',command_at=stamp-60,owned=True,
                                      manual_energy=dict(status='active' if active else 'completed',
                                                         origin_was_idle=True, target_kwh=40))
                    app.manual_extra_demand=lambda *_:active
                    app._manual_energy_ending=not active
                segments=[dict(start=stamp,end=stamp+600)] if plan else []
                self.assertEqual(self.fast_tick(at,segments,before_tick=setup),[expected])

    def test_actual_session_energy_is_counter_delta_not_nominal_power(self):
        app=HeatingClimate()
        app.memory=dict(last_command='heat',heat_session_start_energy=100)
        app.val=lambda key:101.5
        self.assertEqual(app.actual_session_energy(),1.5)
        app.memory['last_command']='off'
        self.assertIsNone(app.actual_session_energy())

if __name__=='__main__':unittest.main()
