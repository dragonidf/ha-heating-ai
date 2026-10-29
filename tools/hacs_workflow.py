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


def protect_watchdog(config,cfg):
    """Keep an existing watchdog's triggers and guards; align fallback targets.

    Used through the automation API, not installed as another AppDaemon app.
    """
    from copy import deepcopy
    result=deepcopy(config)
    actions=result['actions']
    if [a.get('action') for a in actions[:2]]!=['climate.set_temperature','climate.set_hvac_mode']:
        raise ValueError('Unexpected watchdog actions; review before updating')
    keys=('thermostat','room','upper','night_upper','sleep_start','sleep_end','start','end','strategy','native_setpoint')
    bindings={k:cfg[k] for k in keys if cfg.get(k)}
    bindings['room_max_age']=cfg.get('room_max_age',14400)
    # Static entity bindings only. Power topology, settings and limits remain in HA.
    result.setdefault('variables',{})['heating_bindings']=bindings
    target="""{% set c=heating_bindings %}
{% set t=now().strftime('%H:%M:%S') %}
{% set a=states(c.start) %}{% set b=states(c.end) %}
{% set allowed=(a<=t<b) if a<b else (t>=a or t<b) if a>b else false %}
{% set s=states(c.sleep_start) if c.sleep_start is defined else '' %}
{% set e=states(c.sleep_end) if c.sleep_end is defined else '' %}
{% set sleep=(s<=t<e) if s<e else (t>=s or t<e) if s>e else false %}
{% set upper=states(c.night_upper if sleep and c.night_upper is defined else c.upper)|float(none) %}
{% set room=states(c.room)|float(none) %}
{% set fresh=room is not none and as_timestamp(states[c.room].last_reported,0)>=as_timestamp(now())-c.room_max_age %}
{% set current=state_attr(c.thermostat,'current_temperature')|float(none) %}
{% set minimum=state_attr(c.thermostat,'min_temp')|float(5) %}
{% set maximum=state_attr(c.thermostat,'max_temp')|float(35) %}
{% set step=state_attr(c.thermostat,'target_temp_step')|float(0.5) %}
{% set ns=namespace(off=false) %}
{% for interval in state_attr('sensor.heating_outage_schedule','outages')|default([],true) %}
{% if interval.start<=as_timestamp(now())<interval.end %}{% set ns.off=true %}{% endif %}
{% endfor %}
{% set inhibit=(is_state(c.strategy,'Тільки в дозволені години') and not allowed) or room is none or not fresh or upper is none or room>=upper or ns.off %}
{% set target=(current-2 if current is not none else minimum) if inhibit else states(c.native_setpoint)|float(26) %}
{{ [minimum,[maximum,((target/step)|round(0))*step]|min]|max }}"""
    actions[0]['data']['temperature']=target
    # Recheck permissions and heartbeat before each physical command; recovery or
    # a manual pause during the first service must stop the second writer.
    guards=deepcopy(result['conditions'])+[dict(condition='template',value_template=
        "{{ as_timestamp(states('sensor.heating_heartbeat'),0)<as_timestamp(now())-180 }}")]
    result['actions']=[*deepcopy(guards),actions[0],*deepcopy(guards),*actions[1:]]
    return result
