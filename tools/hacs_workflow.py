"""Generate a native HA update coordinator; HACS itself has no pre-install hook.

Install through the HA automation API. This file is not an AppDaemon app.
The persistent journal is needed because temporary scenes do not survive HA restarts.
"""
import json


def build(app_slug='a0d7b954_appdaemon', update='update.heating_ai_appdaemon_update',
          running='binary_sensor.appdaemon_running', armed='input_boolean.heating_armed',
          fan='input_boolean.heating_fan_control', journal='input_text.heating_update_journal'):
    def state(entity,value,attribute=None):
        return dict(condition='state',entity_id=entity,state=value,**({'attribute':attribute} if attribute else {}))
    def service(action,data=None,target=None):
        return dict(action=action,**({'data':data} if data else {}),**({'target':dict(entity_id=target)} if target else {}))
    def notice(message):
        return service('persistent_notification.create',dict(title='Heating AI — оновлення',message=message,notification_id='heating_ai_update'))
    def failure(message,failed=False):
        actions=[]
        if failed:
            actions.append(service('input_text.set_value',dict(value="{{ dict(snapshot, phase='failed') | to_json }}"),journal))
        return actions+[notice(message),dict(stop=message)]
    busy=dict(condition='not',conditions=[state(update,False,'in_progress')])
    pending=dict(condition='not',conditions=[state(journal,['idle','unknown','unavailable',''])])
    snapshot="{{ {'armed': states('ARMED'), 'fan': states('FAN'), 'running': is_state('RUNNING','on'), 'old': state_attr('UPDATE','installed_version'), 'phase':'pending'} | to_json }}"
    snapshot=snapshot.replace('ARMED',armed).replace('FAN',fan).replace('RUNNING',running).replace('UPDATE',update)
    idle=[state(journal,'idle')]
    valid=[state(armed,['on','off']),state(fan,['on','off']),state(running,['on','off'])]
    verify="""{{ states('sensor.heating_ui') == (state_attr('UPDATE','installed_version') | default('',true) | regex_replace('^v',''))
      and (state_attr('sensor.heating_ui','module_hashes') | default({},true) | count) == 9
      and (state_attr('sensor.heating_ui','release_verified') in [true,'true','on'] or state_attr('UPDATE','installed_version') == snapshot.old)
      and as_timestamp(states('sensor.heating_heartbeat'),0) > started
      and state_attr('sensor.heating_forecast','planning_policy_version') == 4 }}""".replace('UPDATE',update)
    # A manual turn-on during maintenance is retained instead of overwritten by an old snapshot.
    restored="{{ {'ARMED': {'state': 'on' if snapshot.armed == 'on' or is_state('ARMED','on') else 'off'}, 'FAN': {'state': 'on' if snapshot.fan == 'on' or is_state('FAN','on') else 'off'}} }}".replace('ARMED',armed).replace('FAN',fan)
    return dict(
        alias='Heating AI — автоматичне оновлення HACS',mode='single',max_exceeded='silent',
        description='Зберігає дозволи, призупиняє команди, зупиняє AppDaemon на заміну файлів і відновлює дозволи після перевірки нового циклу. Журнал переживає перезапуск HA.',
        triggers=[dict(trigger='state',entity_id=update,attribute='in_progress'),
                  dict(trigger='homeassistant',event='start'),dict(trigger='time_pattern',minutes='/1')],
        conditions=[dict(condition='or',conditions=[
            dict(condition='and',conditions=[state(update,['on','off']),busy]),
            dict(condition='and',conditions=[pending,"{{ (states('JOURNAL') | from_json({})).get('phase') != 'failed' }}".replace('JOURNAL',journal)])])],
        actions=[
            dict(choose=[dict(conditions=idle,sequence=[dict(condition='and',conditions=valid),
                 service('input_text.set_value',dict(value=snapshot),journal)])]),
            dict(variables=dict(snapshot="{{ states('JOURNAL') | from_json({}) }}".replace('JOURNAL',journal))),
            service('input_boolean.turn_off',target=[armed,fan]),
            dict(**{'if':[state(running,'on')]},then=[service('hassio.app_stop',dict(app=app_slug))]),
            dict(wait_template="{{ is_state_attr('UPDATE','in_progress',false) }}".replace('UPDATE',update),timeout='00:10:00',continue_on_timeout=True),
            dict(**{'if':"{{ not wait.completed }}"},then=failure('HACS ще завантажує файли. Команди призупинено; очікування продовжиться автоматично.')),
            # Retain the original snapshot when retrying after a verification failure.
            service('input_text.set_value',dict(value="{{ dict(snapshot, phase='pending') | to_json }}"),journal),
            dict(**{'if':"{{ snapshot.running }}"},then=[
                dict(variables=dict(started='{{ as_timestamp(now()) }}')),
                service('hassio.app_start',dict(app=app_slug)),
                dict(wait_template=verify,timeout='00:03:00',continue_on_timeout=True),
                dict(**{'if':"{{ not wait.completed }}"},then=failure('Контролер не пройшов перевірку; автоматичні команди залишено вимкненими',True))]),
            service('scene.create',dict(scene_id='heating_ai_before_update',entities=restored)),
            service('scene.turn_on',target='scene.heating_ai_before_update'),
            service('input_text.set_value',dict(value='idle'),journal),
            notice("{{ 'Оновлення встановлено: ' + (state_attr('UPDATE','installed_version') | string) + '. Попередні дозволи відновлено.' if state_attr('UPDATE','installed_version') != snapshot.old else 'HACS залишив попередню версію. AppDaemon і попередні дозволи відновлено.' }}".replace('UPDATE',update)),
        ])


if __name__=='__main__':
    print(json.dumps(build(),ensure_ascii=False,indent=2))
