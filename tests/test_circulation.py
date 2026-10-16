"""Circulation is independent of the boiler's grid supply and respects manual use."""
import sys
import types
import unittest
from datetime import datetime
from zoneinfo import ZoneInfo

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

    def test_manual_running_fan_is_not_adopted_when_speed_is_adjusted(self):
        app=self.app();app.states.update(fan='on',percentage=25)
        app.fan_tick('on',True)
        self.assertFalse(app.memory.get('fan_owned',False))
        app.calls=[];app.fan_tick('on',False)
        self.assertEqual(app.calls,[])

    def test_night_limit_and_clamping(self):
        app=self.app();app.states.update(fan_speed=150,fan_night_limit=25)
        app.now=lambda:datetime(2026,10,16,23,30,tzinfo=ZoneInfo('Europe/Kyiv'))
        app.fan_tick('on',True)
        self.assertEqual(app.calls[0][1]['percentage'],25)

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

