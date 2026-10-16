"""Run the generated HA action graph against simulated HA state and failure events."""
import importlib.util
import json
from pathlib import Path
import re
import unittest
from jinja2.nativetypes import NativeEnvironment

spec=importlib.util.spec_from_file_location('hacs_workflow',Path(__file__).parents[1]/'tools/hacs_workflow.py')
workflow=importlib.util.module_from_spec(spec)
spec.loader.exec_module(workflow)


class StopSequence(Exception):
    pass


class FakeHA:
    def __init__(self,armed='on',fan='on',running=True,download=True,healthy=True,complete=True):
        self.states={'input_boolean.heating_armed':armed,'input_boolean.heating_fan_control':fan,
            'binary_sensor.appdaemon_running':'on' if running else 'off','input_text.heating_update_journal':'idle',
            'update.heating_ai_appdaemon_update':'on','sensor.heating_ui':'0.2.2','sensor.heating_heartbeat':900}
        self.attributes={'update.heating_ai_appdaemon_update':{'in_progress':True,'installed_version':'v0.2.2'},
            'sensor.heating_ui':{'module_hashes':dict.fromkeys(range(9),'hash'),'release_verified':True},
            'sensor.heating_forecast':{'planning_policy_version':4}}
        self.download=download;self.healthy=healthy;self.complete=complete
        self.context={};self.calls=[];self.scenes={};self.now=1000
        self.env=NativeEnvironment()
        self.env.filters.update(from_json=lambda value,default: self.decode(value,default),to_json=json.dumps,
            regex_replace=lambda value,pattern,replacement: re.sub(pattern,replacement,value))
        self.env.globals.update(states=lambda key:self.states.get(key,'unknown'),
            state_attr=lambda key,attr:self.attributes.get(key,{}).get(attr),
            is_state=lambda key,value:self.states.get(key)==value,
            is_state_attr=lambda key,attr,value:self.attributes.get(key,{}).get(attr)==value,
            as_timestamp=lambda value,default=0:float(value) if isinstance(value,(int,float)) else default,
            now=lambda:self.now)
    @staticmethod
    def decode(value,default):
        try:return json.loads(value)
        except (ValueError,TypeError):return default
    def render(self,value):
        if isinstance(value,str) and '{{' in value:return self.env.from_string(value).render(self.context)
        if isinstance(value,dict):return {key:self.render(v) for key,v in value.items()}
        if isinstance(value,list):return [self.render(v) for v in value]
        return value
    def condition(self,value):
        if isinstance(value,str):return bool(self.render(value))
        if isinstance(value,list):return all(self.condition(v) for v in value)
        kind=value['condition']
        if kind in ('or','and','not'):
            children=[self.condition(v) for v in value['conditions']]
            return any(children) if kind=='or' else all(children) if kind=='and' else not all(children)
        if kind=='state':
            actual=self.attributes.get(value['entity_id'],{}).get(value['attribute']) if 'attribute' in value else self.states.get(value['entity_id'])
            expected=value['state']
            return actual in expected if isinstance(expected,list) else actual==expected
        raise AssertionError(kind)
    def run(self):
        config=workflow.build()
        if not self.condition(config['conditions']):return
        try:self.sequence(config['actions'])
        except StopSequence:pass
    def sequence(self,actions):
        for action in actions:
            if 'condition' in action:
                if not self.condition(action):raise StopSequence()
            elif 'choose' in action:
                for choice in action['choose']:
                    if self.condition(choice['conditions']):self.sequence(choice['sequence']);break
            elif 'if' in action:
                self.sequence(action.get('then',[]) if self.condition(action['if']) else action.get('else',[]))
            elif 'variables' in action:
                self.context.update(self.render(action['variables']))
            elif 'wait_template' in action:
                if 'in_progress' in action['wait_template'] and self.complete:
                    self.attributes['update.heating_ai_appdaemon_update'].update(in_progress=False,installed_version='v0.2.3' if self.download else 'v0.2.2')
                self.context['wait']={'completed':bool(self.render(action['wait_template']))}
            elif 'stop' in action:raise StopSequence()
            elif 'action' in action:self.service(self.render(action))
            else:raise AssertionError(action)
    def service(self,action):
        service=action['action'];data=action.get('data',{});target=action.get('target',{}).get('entity_id')
        self.calls.append((service,self.states.copy()))
        if service=='input_text.set_value':
            self.states[target]=json.dumps(data['value']) if isinstance(data['value'],dict) else data['value']
        elif service=='input_boolean.turn_off':
            for entity in target:self.states[entity]='off'
        elif service=='hassio.app_stop':
            assert self.states['input_boolean.heating_armed']=='off' and self.states['input_boolean.heating_fan_control']=='off'
            self.states['binary_sensor.appdaemon_running']='off'
        elif service=='hassio.app_start':
            assert not self.attributes['update.heating_ai_appdaemon_update']['in_progress']
            self.states['binary_sensor.appdaemon_running']='on'
            if self.healthy:
                self.states['sensor.heating_ui']=self.attributes['update.heating_ai_appdaemon_update']['installed_version'].lstrip('v')
                self.states['sensor.heating_heartbeat']=self.now+1
            self.states.update(getattr(self,'manual_changes',{}))
        elif service=='scene.create':self.scenes['scene.'+data['scene_id']]=data['entities']
        elif service=='scene.turn_on':
            for key,value in self.scenes[target].items():self.states[key]=value['state']
        elif service!='persistent_notification.create':raise AssertionError(service)


class UpdateWorkflowTests(unittest.TestCase):
    def test_success_restores_all_four_permission_combinations(self):
        for armed in ('on','off'):
            for fan in ('on','off'):
                with self.subTest(armed=armed,fan=fan):
                    ha=FakeHA(armed=armed,fan=fan);ha.run()
                    self.assertEqual(ha.states['input_boolean.heating_armed'],armed)
                    self.assertEqual(ha.states['input_boolean.heating_fan_control'],fan)
                    self.assertEqual(ha.states['sensor.heating_ui'],'0.2.3')
                    self.assertEqual(ha.states['input_text.heating_update_journal'],'idle')
    def test_failed_download_resumes_old_working_version(self):
        ha=FakeHA(download=False);ha.run()
        self.assertEqual(ha.states['sensor.heating_ui'],'0.2.2')
        self.assertEqual(ha.states['input_boolean.heating_armed'],'on')
    def test_unhealthy_runtime_does_not_enable_commands_or_loop_restarts(self):
        ha=FakeHA(healthy=False);ha.run()
        self.assertEqual(ha.states['input_boolean.heating_armed'],'off')
        self.assertEqual(ha.states['input_boolean.heating_fan_control'],'off')
        self.assertEqual(json.loads(ha.states['input_text.heating_update_journal'])['phase'],'failed')
        before=len(ha.calls);ha.run();self.assertEqual(len(ha.calls),before)
    def test_download_timeout_keeps_stopped_then_recovers(self):
        ha=FakeHA(complete=False);ha.run()
        self.assertEqual(ha.states['binary_sensor.appdaemon_running'],'off')
        self.assertEqual(ha.states['input_boolean.heating_armed'],'off')
        ha.complete=True;ha.run()
        self.assertEqual(ha.states['input_boolean.heating_armed'],'on')
        self.assertEqual(ha.states['input_text.heating_update_journal'],'idle')
    def test_ha_restart_uses_durable_original_snapshot(self):
        ha=FakeHA(armed='off',fan='off')
        ha.states['input_text.heating_update_journal']=json.dumps(dict(armed='on',fan='on',running=True,old='v0.2.2',phase='pending'))
        ha.run()
        self.assertEqual(ha.states['input_boolean.heating_armed'],'on')
        self.assertEqual(ha.states['input_boolean.heating_fan_control'],'on')
    def test_previously_stopped_app_is_not_started(self):
        ha=FakeHA(running=False);ha.run()
        self.assertEqual(ha.states['binary_sensor.appdaemon_running'],'off')
        self.assertFalse(any(service.startswith('hassio.') for service,_ in ha.calls))
    def test_idle_periodic_trigger_does_nothing(self):
        ha=FakeHA();ha.attributes['update.heating_ai_appdaemon_update']['in_progress']=False
        ha.run();self.assertEqual(ha.calls,[])
    def test_stale_heartbeat_cannot_verify_new_version(self):
        ha=FakeHA(healthy=False);ha.states['sensor.heating_ui']='0.2.3';ha.run()
        self.assertEqual(ha.states['input_boolean.heating_armed'],'off')
    def test_incomplete_runtime_proof_cannot_enable_commands(self):
        ha=FakeHA();ha.attributes['sensor.heating_ui']['module_hashes']={}
        ha.run();self.assertEqual(ha.states['input_boolean.heating_armed'],'off')
    def test_new_release_requires_verified_checksums(self):
        ha=FakeHA();ha.attributes['sensor.heating_ui']['release_verified']=False
        ha.run();self.assertEqual(ha.states['input_boolean.heating_armed'],'off')
    def test_appdaemon_serialized_boolean_proof_is_accepted(self):
        ha=FakeHA();ha.attributes['sensor.heating_ui']['release_verified']='true'
        ha.run();self.assertEqual(ha.states['input_text.heating_update_journal'],'idle')
    def test_manual_permission_change_during_update_is_retained(self):
        ha=FakeHA(armed='off');ha.manual_changes={'input_boolean.heating_armed':'on'}
        ha.run();self.assertEqual(ha.states['input_boolean.heating_armed'],'on')
