"""Keep session times stable; always simulate them with current measurements.

Pure functions: no HA writes and no override of the controller's runtime guards.
The adapter persists the returned reference beside existing plan memory.
"""
from datetime import datetime, timedelta
import math
from uuid import uuid4
from heating_model import (plan as optimise, step, outdoor_at, in_window,
                           modulation_power, morning_runs, morning_runs_valid,comfort_at,weather_coverage,
                           reserve_context,attach_reserve,coast_reserve_needed,local_boundary)
from heating_outages import blocked, availability, schedule_covered
from heating_environment import forecast_step,gain_at,safe_upper


def policy(settings):
    keys=('target','lower','upper','start','end','strategy','split_comfort',
          'morning_min_minutes','day_price','night_price','budget_multiplier',
          'night_target','night_lower','night_upper','sleep_start','sleep_end','upper_margin')
    return {**{k:settings.get(k) for k in keys},'environment_policy_version':4,
            'outage_mode':(settings.get('outage_schedule') or {}).get('mode')}


def reference(now, room, floor, outside, fire, model, weather, settings, result):
    return dict(at=now.timestamp(),room=room,floor=floor,outside=outside,fire=fire,
                model=dict(model),policy=policy(settings),weather=list(weather),
                segments=[dict(s) for r in result.get('_calculation_curve',result['curve']) for s in r['heat_segments']],
                plan_id=result.get('plan_id'),parent_plan_id=result.get('parent_plan_id'),
                morning_reserve=result.get('morning_reserve'),
                split=result.get('split_comfort_applied',False),
                minimum=result['minimum'],maximum=result['maximum'],
                profile=settings.get('modulation_profile'),curve=list(result.get('_calculation_curve',result['curve'])),
                environment=settings.get('environment',{}))

def expected(previous,stamp,key,initial):
    points=[(previous['at'],previous[initial])]+[(r['ts'],r[key]) for r in previous.get('curve',[])]
    for (a,v),(b,w) in zip(points,points[1:]):
        if a<=stamp<=b:
            return v+(w-v)*(stamp-a)/(b-a)
    return points[-1][1]


def replay(now, room, floor, outside, fire, model, weather, settings, segments, split, power_kw=9.):
    """Integrate at <=5 min, splitting at exact session and forecast boundaries."""
    start=now.timestamp(); public_finish=start+86400
    context=reserve_context(now,settings,model)
    priority=bool(settings.get('split_comfort') and 'night_target' in settings)
    count=max(48,math.ceil((context['assessment_until']-start)/1800)) if priority and context else 48
    finish=start+count*1800
    settings=dict(settings,_reserve_context=context,_reserve_needed=bool(context and
        coast_reserve_needed(now,room,floor,outside,fire,model,weather,settings,context,power_kw)))
    priority=bool(settings.get('split_comfort') and 'night_target' in settings)
    if priority and not split:
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
    for block in range(count):
        a=start+1800*block; b=a+1800
        cuts=sorted({a,b,*[a+300*j for j in range(1,6)],
                     *[t for t in boundaries if a<t<b]})
        heats=[]; used=0.; fine=[]
        for t,u in zip(cuts,cuts[1:]):
            active=any(s['start']<=t and s['end']>=u for s in segments)
            tm=datetime.fromtimestamp(t,now.tzinfo); dt=(u-t)/3600
            kw=(modulation_power(f,settings.get('modulation_profile'),3.,
                                settings.get('modulation_anchor'),(t-start)/60)
                if settings.get('modulation_profile') is not None else power_kw)
            wanted,lo,hi,zone=comfort_at(tm,settings)
            hi=safe_upper(hi,zone,settings)
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
        if settings.get('confirmed_completed_minutes',0)<settings.get('morning_min_minutes',30) and not current:
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


def stable_plan(now, room, floor, outside, fire, model, weather, settings, power_kw=9., previous=None):
    reason='Перший план'
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
            result=replay(now,room,floor,outside,fire,model,weather,settings,
                          previous['segments'],previous['split'],power_kw)
            if result and result['maximum']<=max(settings['upper'],room)+.01:
                reserve=result.get('morning_reserve',{})
                improved=None
                # A previously optimised trajectory with unchanged inputs is
                # not new evidence of a deficit: retain its exact reservation.
                drift=(abs(room-expected(previous,now.timestamp(),'temperature','room'))>=.02 or
                       abs(floor-expected(previous,now.timestamp(),'return','floor'))>=.1 or
                       not previous.get('morning_reserve'))
                if drift and settings.get('split_comfort') and 'night_target' in settings and reserve.get('deficit_degree_hours',0)>.001:
                    alternative=optimise(now,room,floor,outside,fire,model,weather,settings,power_kw)
                    if alternative.get('morning_reserve',{}).get('deficit_degree_hours',0)<reserve['deficit_degree_hours']-.01:
                        improved=alternative
                if improved is None:
                    result.update(stability_reason='Невеликі зміни вимірів; часи сеансів збережено',
                                  plan_id=previous.get('plan_id') or uuid4().hex,
                                  parent_plan_id=previous.get('parent_plan_id'))
                    # Persist the identifier even when upgrading a legacy reference.
                    previous=dict(previous,plan_id=result['plan_id'])
                    return result,previous
        reason='Нові умови, зміна запасу тепла або обмежень — план перебудовано'
    result=optimise(now,room,floor,outside,fire,model,weather,settings,power_kw)
    result.update(plan_stable=False,stability_reason=reason,plan_id=uuid4().hex,
                  parent_plan_id=previous.get('plan_id') if previous else None)
    return result,reference(now,room,floor,outside,fire,model,weather,settings,result)

