"""Circulation is independent of the boiler's grid supply and respects manual use."""
import sys
import types
import unittest
import json
from pathlib import Path
from tempfile import TemporaryDirectory
from datetime import datetime
from zoneinfo import ZoneInfo
from unittest.mock import Mock, patch

for name in ('appdaemon','appdaemon.plugins','appdaemon.plugins.hass','appdaemon.plugins.hass.hassapi'):
    sys.modules.setdefault(name,types.ModuleType(name))
sys.modules['appdaemon.plugins.hass.hassapi'].Hass=object
from heating_climate import HeatingClimate


class CirculationTests(unittest.TestCase):
    def app(self, **states):
        app=HeatingClimate()
        app.cfg=dict(allow_actuation=True,fan='fan.test',connection='binary_sensor.link',inverter_heartbeat=['sensor.heartbeat'])
        app.memory={'fan_owned':False}
        app.states=dict(fan_control='on',fan_mode='Авто',fan='off',connection='on',fan_speed=42,fan_night_limit=30,**states)
        app.raw=lambda key,attribute=None: app.states.get(key)
        app.val=lambda key,default=None,attribute=None: app.states.get('percentage' if attribute=='percentage' else key,default)
        app.get_state=lambda entity,**kw: app.states.get(entity)
        app.heartbeat_fresh=lambda *args: True
        app.now=lambda:datetime(2026,10,16,15,tzinfo=ZoneInfo('Europe/Kyiv'))
        app.calls=[];app.published={}
        app.call_service=lambda service,**data:app.calls.append((service,data))
        app.publish=lambda entity,state,**attrs: app.published.update({entity:dict(state=state,**attrs)})
        app.save=lambda:None
        app.log=lambda *args,**kwargs:None
        return app

    def test_confirmed_backup_helper_allows_fireplace_during_outage(self):
        app=self.app(**{'input_boolean.heating_fan_backup_verified':'on'})
        app.fan_tick('off',True)
        self.assertEqual(app.calls,[('fan/turn_on',dict(entity_id='fan.test',percentage=42))])

    def test_unknown_backup_helper_does_not_fall_back_to_true_config(self):
        app=self.app(**{'input_boolean.heating_fan_backup_verified':'unavailable'})
        app.cfg['fan_backup_verified']=True
        app.fan_tick('off',True)
        self.assertEqual(app.calls,[])

    def test_stale_inverter_never_starts_on_backup(self):
        app=self.app(**{'input_boolean.heating_fan_backup_verified':'on'})
        app.heartbeat_fresh=lambda *args:False
        app.fan_tick('unknown',True)
        self.assertEqual(app.calls,[])

    def test_disabled_control_is_read_only(self):
        app=self.app();app.states['fan_control']='off'
        app.fan_tick('on',True)
        self.assertEqual(app.calls,[])

    def test_stop_mode_stops_and_clears_ownership(self):
        app=self.app();app.states.update(fan='on',fan_mode='Зупинити')
        app.fan_tick('on',True)
        self.assertEqual(app.calls,[('fan/turn_off',dict(entity_id='fan.test'))])
        self.assertFalse(app.memory['fan_owned'])
        self.assertEqual(app.published['sensor.heating_fan']['reason'],'Зупинено вручну')

    def test_auto_stops_preexisting_fan_when_fireplace_request_ends(self):
        app=self.app();app.states.update(fan='on',percentage=25)
        app.fan_tick('on',True)
        self.assertFalse(app.memory.get('fan_owned',False))
        app.calls=[];app.fan_tick('on',False)
        self.assertEqual(app.calls,[('fan/turn_off',dict(entity_id='fan.test'))])

    def test_night_limit_and_clamping(self):
        app=self.app();app.states.update(fan_speed=150,fan_night_limit=25)
        app.now=lambda:datetime(2026,10,16,23,30,tzinfo=ZoneInfo('Europe/Kyiv'))
        app.fan_tick('on',True)
        self.assertEqual(app.calls[0][1]['percentage'],25)

    def test_intense_day_level_returns_to_standard_when_fire_fades(self):
        app=self.app(**{'input_number.heating_fan_intense_speed':65})
        app.memory['fire_phase']='Інтенсивно горить'
        app.fan_tick('on',True)
        self.assertEqual(app.calls[-1][1]['percentage'],65)
        self.assertEqual(app.published['sensor.heating_fan']['selected_level'],3)
        app.states.update(fan='on',percentage=65);app.memory['fire_phase']='Згасає'
        app.fan_tick('on',True)
        self.assertEqual(app.calls[-1][1]['percentage'],42)
        self.assertEqual(app.published['sensor.heating_fan']['selected_level'],2)

    def test_night_level_overrides_intense_burning(self):
        app=self.app(**{'input_number.heating_fan_intense_speed':65})
        app.memory['fire_phase']='Інтенсивно горить'
        app.now=lambda:datetime(2026,10,16,23,tzinfo=ZoneInfo('Europe/Kyiv'))
        app.fan_tick('on',True)
        self.assertEqual(app.calls[-1][1]['percentage'],30)
        self.assertEqual(app.published['sensor.heating_fan']['selected_level'],1)

    def test_missing_intense_helper_has_safe_default_and_custom_mapping_works(self):
        app=self.app();app.memory['fire_phase']='Інтенсивно горить'
        app.fan_tick('on',True)
        self.assertEqual(app.calls[-1][1]['percentage'],60)
        app.cfg['fan_intense_speed']='input_number.custom_intense'
        app.states['input_number.custom_intense']=70
        app.fan_tick('on',True)
        self.assertEqual(app.calls[-1][1]['percentage'],70)

    def test_auto_report_does_not_mislabel_stale_on_state_as_manual(self):
        app=self.app();app.states.update(fan='on',percentage=42)
        app.fan_tick('on',False)
        self.assertFalse(app.published['sensor.heating_fan']['controller_owned'])
        self.assertEqual(app.published['sensor.heating_fan']['request_source'],'none')
        self.assertFalse(app.published['sensor.heating_fan']['manual_running'])
        self.assertEqual(app.published['sensor.heating_fan']['reason'],'Не потрібна')
        self.assertEqual(app.calls,[('fan/turn_off',dict(entity_id='fan.test'))])

    def test_auto_without_demand_stops_regardless_of_saved_ownership(self):
        for mode in ('Авто','Автоматично'):
            for owned in (None,False,True):
                with self.subTest(mode=mode,owned=owned):
                    app=self.app();app.states.update(fan='on',fan_mode=mode)
                    app.memory={} if owned is None else {'fan_owned':owned}
                    app.memory['night_fan_until']=9999
                    with patch('heating_climate.time.time',return_value=10000):
                        app.fan_tick('on',False)
                    self.assertEqual(app.calls,[('fan/turn_off',dict(entity_id='fan.test'))])

    def test_first_tick_with_legacy_state_and_inactive_fireplace_stops_fan(self):
        app=self.app();app.states.update(fan='on',fireplace=22.4)
        keys=('ai_proposal','ai_report','rollback','restore_once','thermostat',
              'target','lower','upper','start','end','strategy','mode','armed','power_balance')
        with TemporaryDirectory() as directory:
            legacy=dict(energy_bins=[dict(day='2026-10-15',kwh=17.5)],last_result='Збережено')
            (Path(directory)/'state.json').write_text(json.dumps(legacy))
            app.args={**app.cfg,**{key:'input_text.'+key for key in keys},
                      'data_dir':directory,'ui_enabled':False,'fireplace_heartbeat':[]}
            app.listen_event=Mock();app.listen_state=Mock();app.run_every=Mock();app.datetime=app.now
            app.initialize()
            self.assertNotIn('fan_owned',app.memory)
            self.assertNotIn('fire_circulation',app.memory)
            self.assertEqual(app.memory['energy_bins'],legacy['energy_bins'])
            self.assertEqual(app.memory['last_result'],legacy['last_result'])
            with patch('heating_climate.time.time',return_value=10000):
                app.fan_tick('on',app.fireplace(22)[1])
        self.assertEqual(app.calls,[('fan/turn_off',dict(entity_id='fan.test'))])
        self.assertEqual(app.published['binary_sensor.heating_fireplace_active']['state'],'off')
        app.states['fan']='off';app.fan_tick('on',False)
        self.assertEqual(app.published['sensor.heating_fan']['state'],'Не потрібна')
        self.assertFalse(app.published['sensor.heating_fan']['manual_running'])

    def test_manual_mode_stays_on_without_fireplace_or_night_request(self):
        for state in ('off','on'):
            with self.subTest(state=state):
                app=self.app();app.states.update(fan=state,fan_mode='Ручний запуск',percentage=42)
                app.fan_tick('on',False)
                self.assertEqual(app.calls,[] if state=='on' else
                                 [('fan/turn_on',dict(entity_id='fan.test',percentage=42))])
                self.assertEqual(app.published['sensor.heating_fan']['manual_running'],state=='on')
                self.assertTrue(app.published['sensor.heating_fan']['requested'])

    def test_auto_stop_respects_disabled_control_and_update_guard(self):
        for guard in ('permission','fan_control','update'):
            with self.subTest(guard=guard):
                app=self.app();app.states['fan']='on'
                if guard=='permission':app.cfg['allow_actuation']=False
                elif guard=='fan_control':app.states['fan_control']='off'
                else:app.update_in_progress=lambda:True
                app.fan_tick('on',False)
                self.assertEqual(app.calls,[])

    def test_auto_stop_retries_after_debounce_and_handles_service_errors(self):
        for fail in (False,True):
            with self.subTest(fail=fail):
                app=self.app();app.states['fan']='on'
                def send(service,**data):
                    app.calls.append((service,data))
                    if fail:raise RuntimeError('Fan command failed')
                app.call_service=send
                for stamp in (10000,10015,10030):
                    with patch('heating_climate.time.time',return_value=stamp):
                        app.fan_tick('on',False)
                self.assertEqual([c[0] for c in app.calls],['fan/turn_off','fan/turn_off'])
                if fail:self.assertIn('повторимо',app.published['sensor.heating_fan']['reason'])

    def test_fireplace_cooldown_sends_stop_for_the_controller_owned_fan(self):
        app=self.app();app.cfg['fireplace_heartbeat']=[];app.samples=[]
        with patch('heating_climate.time.time',return_value=10000):
            app.states['fireplace']=27
            app.fan_tick('on',app.fireplace(22)[1])
        app.states.update(fan='on',percentage=42,fireplace=24.4)
        for stamp in range(10015,10316,15):
            with patch('heating_climate.time.time',return_value=stamp):
                app.fan_tick('on',app.fireplace(22)[1])
        self.assertEqual([call[0] for call in app.calls],['fan/turn_on','fan/turn_off'])

    def test_explicit_manual_and_night_requests_are_distinct_from_fireplace(self):
        app=self.app();app.states['fan_mode']='Ручний запуск'
        app.fan_tick('on',False)
        self.assertEqual(app.published['sensor.heating_fan']['request_source'],'manual')
        self.assertEqual(app.calls[-1][1]['percentage'],42)
        app=self.app();app.memory['night_fan_until']=10900
        app.now=lambda:datetime(2026,10,16,23,tzinfo=ZoneInfo('Europe/Kyiv'))
        with patch('heating_climate.time.time',return_value=10000):app.fan_tick('on',False)
        self.assertEqual(app.published['sensor.heating_fan']['request_source'],'night')
        self.assertEqual(app.calls[-1][1]['percentage'],30)

    def test_daytime_standard_resumes_at_seven(self):
        app=self.app();app.memory['fire_phase']='Горить'
        app.now=lambda:datetime(2026,10,17,7,tzinfo=ZoneInfo('Europe/Kyiv'))
        app.fan_tick('on',True)
        self.assertEqual(app.published['sensor.heating_fan']['selected_level'],2)
        self.assertEqual(app.calls[-1][1]['percentage'],42)

    def test_unavailable_fan_is_not_commanded(self):
        app=self.app();app.states['fan']='unavailable'
        app.fan_tick('on',True)
        self.assertEqual(app.calls,[])

    def test_repeated_pending_command_is_rate_limited(self):
        app=self.app();app.fan_tick('on',True);app.fan_tick('on',True)
        self.assertEqual(len(app.calls),1)

    def test_fan_service_error_does_not_escape_into_boiler_recovery(self):
        app=self.app()
        app.call_service=lambda *args,**kwargs:(_ for _ in ()).throw(RuntimeError('Fan command failed'))
        app.fan_tick('on',True)
        self.assertFalse(app.memory['fan_owned'])

    def test_owned_fan_keeps_ownership_until_stop_is_observed(self):
        app=self.app();app.memory['fan_owned']=True;app.states['fan']='on'
        app.fan_tick('on',False)
        self.assertTrue(app.memory['fan_owned'])
        app.states['fan']='off';app.fan_tick('on',False)
        self.assertFalse(app.memory['fan_owned'])

    def test_late_start_is_cancelled_when_demand_disappears(self):
        app=self.app();app.fan_tick('on',True);app.fan_tick('on',False)
        self.assertEqual([call[0] for call in app.calls],['fan/turn_on','fan/turn_off'])

