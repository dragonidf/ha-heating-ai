import importlib.util
from pathlib import Path
from datetime import datetime
from types import SimpleNamespace
from zoneinfo import ZoneInfo
import unittest
from jinja2.nativetypes import NativeEnvironment

spec=importlib.util.spec_from_file_location('workflow_safety',Path(__file__).parents[1]/'tools/hacs_workflow.py')
module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)

class WatchdogSafetyTests(unittest.TestCase):
    def setUp(self):
        self.cfg={k:'test.'+k for k in ('thermostat','room','upper','night_upper','start','end','sleep_start','sleep_end','strategy','native_setpoint')}
        self.original=dict(triggers=[dict(trigger='state',entity_id='test.alive',to='off')],
            conditions=[dict(condition='state',entity_id='test.armed',state='on')],actions=[
                dict(action='climate.set_temperature',data={}),dict(action='climate.set_hvac_mode',data=dict(hvac_mode='auto'))])
        self.result=module.protect_watchdog(self.original,self.cfg)
        self.template=next(a['data']['temperature'] for a in self.result['actions'] if a.get('action')=='climate.set_temperature')

    def render(self,hour,minute=0,room=21,stale=False,outages=None,upper=22):
        at=datetime(2026,10,29,hour,minute,tzinfo=ZoneInfo('Europe/Kiev'))
        values={self.cfg[k]:v for k,v in dict(start='23:00:00',end='07:00:00',sleep_start='23:00:00',sleep_end='07:00:00',
            room=room,upper=upper,night_upper=upper,strategy='Тільки в дозволені години',native_setpoint=26).items()}
        class States:
            def __call__(self,key):return values.get(key,'unknown')
            def __contains__(self,key):return key in values
            def __getitem__(self,key):return SimpleNamespace(last_reported=at.timestamp()-(15000 if stale else 0))
        attrs=dict(current_temperature=23,min_temp=5,max_temp=35,target_temp_step=.5)
        env=NativeEnvironment();env.globals.update(states=States(),now=lambda:at,
            as_timestamp=lambda value,default=0:value.timestamp() if isinstance(value,datetime) else float(value),
            state_attr=lambda entity,key:(outages if key=='outages' else attrs.get(key)),
            is_state=lambda entity,value:values.get(entity)==value)
        return env.from_string(self.template).render(heating_bindings=self.result['variables']['heating_bindings'])

    def test_strict_window_boundaries_and_live_upper(self):
        for hour,minute,expected in ((6,59,26),(7,0,21),(22,59,21),(23,0,26)):
            self.assertEqual(self.render(hour,minute),expected)
        self.assertEqual(self.render(23,room=22),21)

    def test_outage_unknown_or_stale_room_inhibits_native_heat(self):
        at=datetime(2026,10,29,23,tzinfo=ZoneInfo('Europe/Kiev')).timestamp()
        self.assertEqual(self.render(23,outages=[dict(start=at-1,end=at+600)]),21)
        self.assertEqual(self.render(23,room=None),21)
        self.assertEqual(self.render(23,stale=True),21)

    def test_each_writer_rechecks_original_guards_and_fresh_heartbeat(self):
        actions=self.result['actions'];indices=[i for i,a in enumerate(actions) if 'action' in a]
        for i in indices:
            self.assertEqual(actions[i-2],self.original['conditions'][0])
            self.assertIn('heating_heartbeat',actions[i-1]['value_template'])
        self.assertEqual(self.result['triggers'],self.original['triggers'])
        self.assertEqual(self.original['actions'][0]['data'],{})
