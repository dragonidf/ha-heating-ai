"""Keep session times stable; always simulate them with current measurements.

Pure functions: no HA writes and no override of the controller's runtime guards.
The adapter persists the returned reference beside existing plan memory.
"""
from datetime import datetime, timedelta
import math
from uuid import uuid4
from heating_model import (held_action,number,plan as optimise, step, outdoor_at, in_window,
                           modulation_power, morning_runs, morning_runs_valid,comfort_at,weather_coverage,
                           reserve_context,attach_reserve,coast_reserve_needed,local_boundary,
                           SEARCH_POLICY_VERSION,SEARCH_FIELDS,search_input_key,apply_search_diagnostics,power_profile)
from heating_outages import blocked, availability, schedule_covered
from heating_environment import forecast_step,gain_at,safe_upper,ENVIRONMENT_POLICY_VERSION


def policy(settings):
    keys=('target','lower','upper','start','end','strategy','split_comfort',
          'morning_min_minutes','day_price','night_price','budget_multiplier',
          'night_target','night_lower','night_upper','sleep_start','sleep_end','upper_margin','equipment_version','ventilation_model','ventilation_expected')
    return {**{k:settings.get(k) for k in keys},'environment_policy_version':ENVIRONMENT_POLICY_VERSION,
            'outage_mode':(settings.get('outage_schedule') or {}).get('mode'),
            'search_policy_version':SEARCH_POLICY_VERSION}


def reference(now, room, floor, outside, fire, model, weather, settings, result):
    return dict(at=now.timestamp(),room=room,floor=floor,outside=outside,fire=fire,
                model=dict(model),policy=policy(settings),weather=list(weather),
                segments=[dict(s) for r in result.get('_calculation_curve',result['curve']) for s in r['heat_segments']],
                plan_id=result.get('plan_id'),parent_plan_id=result.get('parent_plan_id'),
                morning_reserve=result.get('morning_reserve'),
                search={k:result.get(k) for k in (*SEARCH_FIELDS,'candidate_rejections')},
                search_check_key=result.get('search_check_key'),
                split=result.get('split_comfort_applied',False),
                minimum=result['minimum'],maximum=result['maximum'],
                profile=settings.get('modulation_profile'),curve=list(result.get('_calculation_curve',result['curve'])),
                environment=settings.get('environment',{}))

def expected(previous,stamp,key,initial):
    points=[(previous['at'],previous[initial])]+[(p['ts'],p[key])
        for r in previous.get('curve',[]) for p in r.get('_fine',[r])]
    for (a,v),(b,w) in zip(points,points[1:]):
        if a<=stamp<=b:
            return v+(w-v)*(stamp-a)/(b-a)
    return points[-1][1]


def replay(now, room, floor, outside, fire, model, weather, settings, segments, split, power_kw=9., reserve_checked=False,context_override=None):
    """Integrate at <=5 min, splitting at exact session and forecast boundaries."""
    start=now.timestamp(); public_finish=start+86400
    context=context_override or reserve_context(now,settings,model)
    priority=bool(settings.get('split_comfort') and 'night_target' in settings)
    count=max(48,math.ceil((context['assessment_until']-start)/1800)) if (priority or context_override) and context else 48
    finish=start+count*1800
    settings=dict(settings,_reserve_context=context,_reserve_needed=bool(context and
        coast_reserve_needed(now,room,floor,outside,fire,model,weather,settings,context,power_kw)))
    priority=bool(settings.get('split_comfort') and 'night_target' in settings)
    if priority and not split and not reserve_checked:
        # A safety fallback without a morning reservation must be reconsidered.
        return None
    data=availability(settings.get('outage_schedule') or {},start,finish)
    outages=data.get('outages',[]) if data.get('valid') else []
    segments=[dict(s,start=max(start,s['start']),end=min(finish,s['end']))
              for s in segments if s['end']>start and s['start']<finish]
    for s in segments:
        a=datetime.fromtimestamp(s['start'],now.tzinfo)
        b=datetime.fromtimestamp(s['end']-.001,now.tzinfo)
        if (blocked(s['start'],s['end'],outages) or
            settings['strategy']=='Тільки в дозволені години' and
            not (in_window(a,settings['start'],settings['end']) and
                 in_window(b,settings['start'],settings['end']))):
            return None
    boundaries={s[k] for s in segments for k in ('start','end')}
    boundaries.update(r['ts'] for r in weather if start<r['ts']<finish)
    for day in range(5):
        for key in ('start','end','sleep_start','sleep_end'):
            value=settings.get(key)
            if value:
                boundary=local_boundary(now.date()+timedelta(days=day),value,now.tzinfo).timestamp()
                if start<boundary<finish:
                    boundaries.add(boundary)
    rows=[]; r,f=room,floor; low=high=room; energy=0.; all_segments=[]
    actuator=settings.get('actuator') or {};command=actuator.get('action');command_at=actuator.get('changed_at',0.)
    for block in range(count):
        a=start+1800*block; b=a+1800
        cuts=sorted({a,b,*[a+300*j for j in range(1,6)],
                     *[t for t in boundaries if a<t<b]})
        heats=[]; used=0.; fine=[]
        for begin,finish in zip(cuts,cuts[1:]):
            deadline=(number(command_at) or 0)+(600 if command=='heat' else 900)
            pieces=[begin,deadline,finish] if actuator and begin<deadline<finish else [begin,finish]
            for t,u in zip(pieces,pieces[1:]):
                active=any(s['start']<=t and s['end']>=u for s in segments)
                tm=datetime.fromtimestamp(t,now.tzinfo); dt=(u-t)/3600
                kw=(modulation_power(f,settings.get('modulation_profile'),3.,
                                    settings.get('modulation_anchor'),(t-start)/60)
                    if settings.get('modulation_profile') is not None else power_kw)
                wanted,lo,hi,zone=comfort_at(tm,settings)
                hi=safe_upper(hi,zone,settings)
                if actuator:
                    permitted=(not blocked(t,u,outages) and (settings['strategy']!='Тільки в дозволені години' or
                        in_window(tm,settings['start'],settings['end']) and
                        in_window(datetime.fromtimestamp(u-.001,now.tzinfo),settings['start'],settings['end'])))
                    stop=not permitted or r>=hi or actuator.get('safety_stop') and t<start+300
                    wanted='heat' if active and not stop else 'off'
                    applied=held_action(command,command_at,t,wanted,urgent=stop)
                    if applied!=command:command,command_at=applied,t
                    active=applied=='heat' and permitted
                if active and (r>=hi or f>=50):
                    return None
                r,f=forecast_step(r,f,outdoor_at(weather,tm,outside),kw if active else 0.,
                         fire*math.exp(-(t-start)/7200),model,dt,tm,weather,settings)
                low=min(low,r); high=max(high,r)
                fine.append(dict(ts=u,temperature=r,**{'return':f}))
                if r>max(room,hi)+.01 and (active or all_segments):
                    return None
                if active:
                    if r>hi or f>50:
                        return None
                    s=dict(start=t,end=u,power_kw=round(kw,3),kwh=kw*dt)
                    s['conditional']=not schedule_covered(data,t,u)
                    heats.append(s);used+=s['kwh']
        energy+=used;all_segments.extend(heats)
        wanted,lo,hi,zone=comfort_at(datetime.fromtimestamp(b-.001,now.tzinfo),settings)
        rows.append(dict(ts=b,temperature=round(r,2),return_=round(f,2),
                         target=wanted,lower=lo,upper=hi,comfort_zone=zone,
                         **{'return':round(f,2)},heat=bool(heats),kwh=round(used,4),
                         heat_segments=heats,outside_minutes=0.,_fine=fine,
                         schedule_known=schedule_covered(data,a,b),
                         weather_known=bool(weather and max(x['ts'] for x in weather)+3600>=b)))
    if not priority and not morning_runs_valid(rows,now,settings.get('morning_min_minutes',30),
                              settings.get('morning_elapsed_minutes',0)):
        return None
    if priority and settings['_reserve_needed'] and context and context['morning_end'] and context['morning_end']>start:
        current=[s for s in all_segments if context['morning_start']<=s['start']<context['morning_end']]
        if settings.get('confirmed_completed_minutes',0)<settings.get('morning_min_minutes',30) and not current and not reserve_checked:
            return None
    calculation_rows=rows
    rows=[r for r in rows if r['ts']<=public_finish+.001]
    all_segments=[s for r in rows for s in r['heat_segments']]
    energy=sum(r['kwh'] for r in rows)
    low=min([room]+[p['temperature'] for r in rows for p in r['_fine']])
    high=max([room]+[p['temperature'] for r in rows for p in r['_fine']])
    mix=dict(evening=0.,morning=0.,other=0.)
    for s in all_segments:
        tm=datetime.fromtimestamp(s['start'],now.tzinfo)
        kind='evening' if in_window(tm,'23:00','01:00') else 'morning' if in_window(tm,'05:00','07:00') else 'other'
        mix[kind]+=s['kwh']
    schedule_until=data.get('known_until') if data.get('valid') else None
    coverage=weather_coverage(weather,public_finish)
    result=dict(heat=bool(all_segments and all_segments[0]['start']==start),curve=rows,
                energy=round(energy,2),minimum=round(low,2),maximum=round(high,2),
                horizon_hours=24.,validated=bool(model.get('validated')),
                morning_min_minutes=settings.get('morning_min_minutes',30),
                morning_runs_minutes=[round((b-a)/60,1) for a,b in morning_runs(rows,now.tzinfo)],
                split_comfort_applied=split,session_kwh={k:round(v,3) for k,v in mix.items()},
                morning_priority_applied=priority and split,
                night_air_target=(settings['night_lower']+.05 if priority else
                                  max(settings['lower'],settings['target']-.5)),
                split_reason='Часи сеансів збережено; прогноз оновлено за фактичними вимірами',
                schedule_known_until=schedule_until,**coverage,
                conditional_heat_minutes=round(sum((s['end']-s['start'])/60 for s in all_segments if s['conditional']),1),
                schedule_complete=schedule_covered(data,start,finish),
                outage_mode=data.get('mode','scheduled' if data.get('valid') else 'unknown'),
                outage_shifted=False,outage_preheat=False,outside_minutes=0.,
                outage_status=data.get('reason','Графік не підключений'),
                comfort_shortfall=any(r['temperature']<r['lower'] for r in rows),heat_segments=all_segments,
                schedule_updated=data.get('updated'),plan_stable=True,_calculation_curve=calculation_rows)
    attach_reserve(now,room,model,weather,settings,result,context)
    result['curve']=[{k:v for k,v in row.items() if k!='_fine'} for row in rows]
    return result


def stress_scenarios(now,room,floor,outside,fire,model,weather,settings,nominal,power_kw=3.):
    """Frozen-plan sensitivity, never an outage prediction or an actuation request."""
    rows=[]
    calculation=nominal.get('_calculation_curve')
    segments=([s for r in calculation for s in r['heat_segments']] if calculation is not None else
              nominal.get('heat_segments',[]))
    context=nominal.get('morning_reserve')
    delta=max(0.,min(10.,number(settings.get('risk_cold_delta')) if number(settings.get('risk_cold_delta')) is not None else 2.))
    low_solar={**(settings.get('environment') or {}),'solar':{}}
    cold=[dict(r,temperature=r['temperature']-delta) for r in weather]
    cases=[('cold_low_solar',dict(settings,environment=low_solar),segments,outside-delta,cold)]
    data=settings.get('outage_schedule') or {}
    if data.get('mode')=='unknown' or data.get('emergency'):
        end=(context or {}).get('cycle_end',now.timestamp()+86400)
        for key,default in (('risk_blackout_short_minutes',60),('risk_blackout_long_minutes',120)):
            minutes=max(5.,min(480.,number(settings.get(key)) or default))
            start=max(now.timestamp(),end-minutes*60);trimmed=[]
            for segment in segments:
                for a,b in ((segment['start'],min(start,segment['end'])),(max(end,segment['start']),segment['end'])):
                    if b>a:trimmed.append(dict(segment,start=a,end=b))
            outage=dict(data,mode='scheduled',valid=True,outages=[*data.get('outages',[]),dict(start=start,end=end)])
            # Evaluate curtailed delivery, even when it can no longer meet the
            # nominal minimum run. This never changes the actual plan's policy.
            cases.append(('blackout_'+format(minutes,'g'),dict(settings,outage_schedule=outage,morning_min_minutes=0),trimmed,outside,weather))
    for name,scenario,requests,out,weather_rows in cases:
        result=replay(now,room,floor,out,fire,model,weather_rows,scenario,requests,
                      nominal.get('split_comfort_applied',False),power_kw,reserve_checked=True,context_override=context)
        rows.append(dict(name=name,hypothetical=True,scope='frozen_plan_without_replanning',
            assessment_until=(context or {}).get('assessment_until',now.timestamp()+86400),
            available=result is not None,energy=result.get('energy') if result else None,
            minimum=result.get('minimum') if result else None,maximum=result.get('maximum') if result else None,
            deficit_degree_hours=(result.get('morning_reserve') or {}).get('deficit_degree_hours') if result else None,
            reason=None if result else 'Scenario violates existing safety or exception constraints'))
    return rows


def stable_plan(now, room, floor, outside, fire, model, weather, settings, power_kw=9., previous=None):
    reason='Перший план'
    improved=None
    if previous:
        # Compare with the last optimisation, not the previous 5-minute sample.
        # Small changes therefore accumulate and cannot freeze the plan forever.
        small=(0<=now.timestamp()-previous['at'] and
               abs(room-expected(previous,now.timestamp(),'temperature','room'))<.25 and
               abs(floor-expected(previous,now.timestamp(),'return','floor'))<1. and
               abs(outside-previous['outside'])<1. and abs(fire-previous['fire'])<1. and
               model==previous['model'] and policy(settings)==previous['policy'])
        changed_weather=(bool(weather)!=bool(previous['weather']) or any(
            abs(x['temperature']-outdoor_at(previous['weather'],
                datetime.fromtimestamp(x['ts'],now.tzinfo),previous['outside']))>=1.
            for x in weather if x['ts']>=now.timestamp()))
        changed_power=any(abs(modulation_power(t,settings.get('modulation_profile'))-
                             modulation_power(t,previous.get('profile')))>=1.5
                          for t in (floor,floor+2,floor+4))
        changed_gain=any(abs(gain_at(now+timedelta(hours=h),weather,settings)-
                            gain_at(now+timedelta(hours=h),weather,
                                    {'environment':previous.get('environment',{})}))>=.1
                         for h in (0,3,12,18))
        if small and not changed_weather and not changed_power and not changed_gain:
            key=search_input_key(now,room,floor,outside,fire,model,weather,settings)
            checked=previous.get('search_check_key')==key
            result=replay(now,room,floor,outside,fire,model,weather,settings,
                          previous['segments'],previous['split'],power_kw,reserve_checked=checked)
            if result and result['maximum']<=max(settings['upper'],room)+.01:
                reserve=result.get('morning_reserve',{})
                improved=None
                result.update(previous.get('search',{}))
                if settings.get('split_comfort') and 'night_target' in settings and reserve.get('deficit_degree_hours',0)>.00001 and not checked:
                    alternative=optimise(now,room,floor,outside,fire,model,weather,settings,power_kw)
                    other=alternative.get('morning_reserve',{}).get('deficit_degree_hours',0)
                    if other<reserve['deficit_degree_hours']-.00001:
                        improved=alternative
                    else:
                        result.update({k:alternative.get(k) for k in (*SEARCH_FIELDS,'candidate_rejections')})
                        previous=dict(previous,search_check_key=key,
                            search={k:result.get(k) for k in (*SEARCH_FIELDS,'candidate_rejections')})
                apply_search_diagnostics(now,settings,result)
                if improved is None:
                    result.update(stability_reason=('Розширений пошук не знайшов кращого запасу; перевірку збережено'
                                      if reserve.get('deficit_degree_hours',0)>.00001 else
                                      'Невеликі зміни вимірів; часи сеансів збережено'),
                                  plan_id=previous.get('plan_id') or uuid4().hex,
                                  parent_plan_id=previous.get('parent_plan_id'))
                    # Persist the identifier even when upgrading a legacy reference.
                    previous=dict(previous,plan_id=result['plan_id'])
                    return result,previous
        reason='Нові умови, зміна запасу тепла або обмежень — план перебудовано'
    result=improved if improved is not None else optimise(now,room,floor,outside,fire,model,weather,settings,power_kw)
    result.update(plan_stable=False,stability_reason=reason,plan_id=uuid4().hex,
                  parent_plan_id=previous.get('plan_id') if previous else None)
    return result,reference(now,room,floor,outside,fire,model,weather,settings,result)



def recommend_power(now,room,floor,outside,fire,model,weather,settings,calibrations=None):
    """Three bounded searches on identical inputs, never three command writers."""
    alternatives=[]
    for kw in (3,6,9):
        mode='fixed_'+str(kw)
        calibration=power_profile(mode,calibrations,{},settings.get('equipment_version'))
        scenario=dict(settings,modulation_profile=calibration,modulation_anchor=None)
        result=optimise(now,room,floor,outside,fire,model,weather,scenario,kw)
        reserve=(result or {}).get('morning_reserve') or {}
        feasible=bool(result and (reserve.get('predicted_sufficient') if reserve else not result.get('comfort_shortfall')))
        alternatives.append(dict(mode=mode,nominal_kw=kw,found=bool(result),feasible=feasible,
            calibrated=bool(calibration.get('validated')),minimum=(result or {}).get('minimum'),
            deficit_degree_hours=reserve.get('deficit_degree_hours'),energy_kwh=(result or {}).get('energy'),
            change_before=min((s['start'] for s in (result or {}).get('heat_segments',[]) if s['start']>now.timestamp()),default=None)))
    chosen=next((a for a in alternatives if a['feasible']),None)
    bound=None
    if not chosen:
        r,f=room,floor;allowed_seconds=0.;first_shortfall=None
        context=reserve_context(now,settings,model)
        finish=(context or {}).get('assessment_until',now.timestamp()+86400)
        data=availability(settings.get('outage_schedule') or {},now.timestamp(),finish)
        outages=data.get('outages',[]) if data.get('valid') else []
        stamp=now.timestamp()
        while stamp<finish:
            end=min(stamp+300,finish);when=datetime.fromtimestamp(stamp,now.tzinfo)
            allowed=(settings.get('strategy')!='Тільки в дозволені години' or in_window(when,settings['start'],settings['end'])) and not blocked(stamp,end,outages)
            allowed_seconds+=end-stamp if allowed else 0.
            r,f=forecast_step(r,f,outdoor_at(weather,when,outside),9. if allowed else 0.,
                fire*math.exp(-(stamp-now.timestamp())/7200),model,(end-stamp)/3600,when,weather,settings)
            _,lower,_,_=comfort_at(datetime.fromtimestamp(end,now.tzinfo),settings)
            if r<lower-.001 and first_shortfall is None:first_shortfall=end
            stamp=end
        bound=dict(allowed_minutes=allowed_seconds/60,first_shortfall=first_shortfall,
            optimistic_maximum_kw=9.,upper_and_storage_limits_relaxed=True,
            assumptions='Чинна теплова модель, задана погода й вентиляція; 9 кВт у кожний доступний інтервал; невідома мережа умовно доступна')

    return dict(created_at=now.isoformat(),mode=chosen['mode'] if chosen else None,
        status='feasible_found' if chosen else 'no_available_interval' if bound and not bound['allowed_minutes'] else 'model_bound_insufficient' if bound and bound['first_shortfall'] else 'search_not_found',
        confidence='validated_scenario' if chosen and chosen['calibrated'] else 'conditional_nominal_assumption',
        reason='Знайдено план за зазначеної потужності; змініть фізичний режим вручну за потреби' if chosen else
            'Пошук не знайшов допустимого плану; вищий режим може не допомогти через межі або відключення',
        change_before=chosen['change_before'] if chosen else None,alternatives=alternatives,
        physical_impossibility_proven=False,model_bound=bound,
        model_insufficiency_proven=bool(bound and bound['first_shortfall']))
