import unittest
from datetime import datetime
from zoneinfo import ZoneInfo
from unittest.mock import patch
from test_adapter_modulation import HeatingClimate

class FallbackRecoveryTests(unittest.TestCase):
    def app(self,hour=19,grid='on'):
        app=HeatingClimate()
        app.climate_tz=ZoneInfo('Europe/Kiev')
        app.at=datetime(2026,10,12,hour,10,tzinfo=app.climate_tz)
        app.now=lambda:app.at
        app.next_compute=app.at.timestamp()+300
        app.cfg=dict(allow_actuation=True,thermostat='climate.test',mode='mode')
        app.memory=dict(owned=True,last_command='off',command_at=app.at.timestamp()-1000,command_generation=1)
        app.command_pending_until=0
        app.values=dict(thermostat='heat',mode='Автоматичне керування',armed='on',strategy='Тільки в дозволені години',start='23:00',end='07:00')
        app.attrs=dict(current_temperature=23,temperature=26,min_temp=5,max_temp=35,target_temp_step=.5)
        app.raw=lambda key,attribute=None:dict(attributes=app.attrs) if key=='thermostat' and attribute=='all' else app.values.get(key)
        app.get_state=lambda *args,**kwargs:None
        app.val=lambda key,default=None,attribute=None:app.attrs.get(attribute,default) if key=='thermostat' else dict(upper=22.5,native_setpoint=26,power=0).get(key,default)
        app.fresh=lambda *args:False
        app.source_temperature=lambda:(21.8,'Вітальня',21.8,23)
        app.grid_state=lambda:grid
        app.save=lambda:None
        app.publish=lambda *args,**kwargs:None
        app.journal=lambda *args:None
        app.notify_once=lambda *args:None
        app.run_in=lambda *args,**kwargs:None
        app.calls=[]
        def call(service,**kwargs):
            app.calls.append((service,kwargs))
            if service=='climate/set_temperature':
                app.attrs['temperature']=kwargs['temperature']
                if 'hvac_mode' in kwargs:app.values['thermostat']=kwargs['hvac_mode']
            if service=='climate/set_hvac_mode':app.values['thermostat']=kwargs['hvac_mode']
        app.call_service=call
        return app
    def run_action(self,app,action,stamp=None):
        with patch('heating_climate.time.time',return_value=stamp or app.at.timestamp()):app.apply_action(action,False)
    def test_stable_available_temperature_does_not_trigger_auto(self):
        app=self.app();self.run_action(app,'off')
        self.assertEqual(app.values['thermostat'],'heat')
        self.assertEqual(app.attrs['temperature'],21)
        self.assertNotIn('temporary_fallback',app.memory)
    def test_night_only_fallback_uses_safe_target_and_separate_auto_call(self):
        app=self.app();self.run_action(app,'fallback')
        self.assertEqual(app.attrs['temperature'],21)
        self.assertEqual(app.values['thermostat'],'auto')
        self.assertEqual([x[0] for x in app.calls],['climate/set_temperature','climate/set_hvac_mode'])
        self.assertEqual(app.memory['temporary_fallback']['retry_at'],app.next_compute)
    def test_fallback_inside_window_uses_native_target(self):
        app=self.app(23);app.attrs['temperature']=21
        self.run_action(app,'fallback')
        self.assertEqual(app.attrs['temperature'],26)
        self.assertEqual(app.values['thermostat'],'auto')
    def test_auto_is_held_between_full_updates_then_control_is_retried(self):
        app=self.app();self.run_action(app,'fallback')
        self.run_action(app,'off',app.at.timestamp()+15)
        self.assertEqual(app.values['thermostat'],'auto')
        with patch('heating_climate.time.time',return_value=app.next_compute):
            app.resume_temporary_fallback();app.apply_action('off',False)
            app.verify_command(dict(expected='heat',target=21,generation=app.memory['command_generation']))
        self.assertEqual(app.values['thermostat'],'heat')
        self.assertNotIn('temporary_fallback',app.memory)
        self.assertFalse(app.memory.get('recovering_from_fallback'))
    def test_failed_heat_command_goes_to_auto_without_observation(self):
        app=self.app()
        with patch('heating_climate.time.time',return_value=app.at.timestamp()):
            app.verify_command(dict(expected='heat',target=21,generation=1))
        self.assertEqual(app.values['thermostat'],'auto')
        self.assertEqual(app.values['mode'],'Автоматичне керування')
        self.assertFalse(any(x[0]=='input_select/select_option' for x in app.calls))
    def test_missing_power_response_uses_same_temporary_recovery(self):
        app=self.app();app.memory['last_command']='heat'
        with patch('heating_climate.time.time',return_value=app.at.timestamp()):app.verify_boiler_response()
        self.assertEqual(app.values['thermostat'],'auto')
        self.assertIn('temporary_fallback',app.memory)
    def test_grid_loss_never_requests_auto(self):
        app=self.app(grid='off');self.run_action(app,'fallback')
        self.assertEqual(app.values['thermostat'],'heat')
        self.assertEqual(app.attrs['temperature'],21)
        self.assertNotIn('temporary_fallback',app.memory)
    def test_manual_disarm_and_observation_are_not_overridden(self):
        app=self.app();app.values['armed']='off'
        with patch('heating_climate.time.time',return_value=app.at.timestamp()):
            app.verify_command(dict(expected='heat',target=21,generation=1))
        self.assertFalse(app.calls)
        app=self.app();self.run_action(app,'fallback');app.values['mode']='Спостереження'
        with patch('heating_climate.time.time',return_value=app.next_compute):app.resume_temporary_fallback()
        self.assertIn('temporary_fallback',app.memory)
        self.run_action(app,'observe')
        self.assertNotIn('temporary_fallback',app.memory)
    def test_late_reports_in_auto_do_not_permanently_pause_the_model(self):
        app=self.app();app.memory['temporary_fallback']=dict(retry_at=app.next_compute)
        with patch('heating_climate.time.time',return_value=app.at.timestamp()):
            app.climate_changed('climate.test','all',None,dict(state='auto',attributes=dict(temperature=26)))
        self.assertFalse(app.calls)
    def test_refused_auto_is_rate_limited_and_keeps_recovery(self):
        app=self.app()
        app.call_service=lambda service,**kwargs:app.calls.append((service,kwargs))
        self.run_action(app,'fallback');count=len(app.calls)
        with patch('heating_climate.time.time',return_value=app.at.timestamp()+12):
            app.verify_command(dict(expected='auto',target=21,generation=app.memory['command_generation']))
        self.assertEqual(len(app.calls),count)
        self.assertIn('temporary_fallback',app.memory)
        self.assertEqual(app.values['mode'],'Автоматичне керування')
    def test_cycle_failure_enters_auto_and_can_retry_next_full_update(self):
        app=self.app();app.log=lambda *args,**kwargs:None
        def failure():raise ValueError('test cycle fault')
        app.tick_inner=failure
        with patch('heating_climate.time.time',return_value=app.at.timestamp()):app.tick()
        self.assertEqual(app.values['thermostat'],'auto')
        self.assertEqual(app.memory['temporary_fallback']['retry_at'],app.next_compute)
    def test_recovery_explicitly_exits_auto_even_when_target_matches(self):
        app=self.app();app.values['thermostat']='auto';app.attrs['temperature']=21
        self.run_action(app,'off')
        self.assertEqual([c[0] for c in app.calls],['climate/set_hvac_mode'])
        self.assertEqual(app.values['thermostat'],'heat')
        self.assertEqual(app.attrs['temperature'],21)
    def test_full_update_can_retry_earlier_when_new_data_arrives(self):
        app=self.app();self.run_action(app,'fallback')
        with patch('heating_recovery.time.time',return_value=app.at.timestamp()+60):
            app.resume_temporary_fallback()
        self.assertNotIn('temporary_fallback',app.memory)
        self.assertTrue(app.memory['recovering_from_fallback'])

if __name__=='__main__':unittest.main()
