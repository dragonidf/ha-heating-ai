"""Keep session times stable; always simulate them with current measurements.

Pure functions: no HA writes and no override of the controller's runtime guards.
The adapter persists the returned reference beside existing plan memory.
"""
from datetime import datetime, timedelta
import math
from heating_model import (plan as optimise, step, outdoor_at, in_window,
                           modulation_power, morning_runs, morning_runs_valid,comfort_at)
from heating_outages import blocked
from heating_environment import forecast_step,gain_at,safe_upper


def policy(settings):
    keys=('target','lower','upper','start','end','strategy','split_comfort',
          'morning_min_minutes','day_price','night_price','budget_multiplier',
          'night_target','night_lower','night_upper','sleep_start','sleep_end','upper_margin')
    return {**{k:settings.get(k) for k in keys},'environment_policy_version':4}


def reference(now, room, floor, outside, fire, model, weather, settings, result):
    return dict(at=now.timestamp(),room=room,floor=floor,outside=outside,fire=fire,
                model=dict(model),policy=policy(settings),weather=list(weather),
                segments=[dict(s) for s in result['heat_segments']],
                split=result.get('split_comfort_applied',False),
                minimum=result['minimum'],maximum=result['maximum'],
                profile=settings.get('modulation_profile'),curve=list(result['curve']),
                environment=settings.get('environment',{}))

def expected(previous,stamp,key,initial):
    points=[(previous['at'],previous[initial])]+[(r['ts'],r[key]) for r in previous.get('curve',[])]
    for (a,v),(b,w) in zip(points,points[1:]):
        if a<=stamp<=b:
            return v+(w-v)*(stamp-a)/(b-a)
    return points[-1][1]


def replay(now, room, floor, outside, fire, model, weather, settings, segments, split, power_kw=9.):
    """Integrate at <=5 min, splitting at exact session and forecast boundaries."""
    start=now.timestamp(); finish=start+86400
    priority=bool(settings.get('split_comfort') and 'night_target' in settings)
    if priority and not split:
        # A safety fallback without a morning reservation must be reconsidered.
        return None
    data=settings.get('outage_schedule') or {}
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
    for day in range(3):
        for key in ('start','end','sleep_start','sleep_end'):
            value=settings.get(key)
            if value:
                boundary=(now+timedelta(days=day)).replace(hour=int(value[:2]),minute=int(value[3:5]),second=0,microsecond=0).timestamp()
                if start<boundary<finish:
                    boundaries.add(boundary)
    rows=[]; r,f=room,floor; low=high=room; energy=0.; all_segments=[]
    for block in range(48):
        a=start+1800*block; b=a+1800
        cuts=sorted({a,b,*[a+300*j for j in range(1,6)],
                     *[t for t in boundaries if a<t<b]})
        heats=[]; used=0.
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
            if r>max(room,hi)+.01 and (active or all_segments):
                return None
            if active:
                if r>hi or f>50:
                    return None
                s=dict(start=t,end=u,power_kw=round(kw,3),kwh=kw*dt)
                s['conditional']=not data.get('valid') or data.get('known_until',0)<u
                heats.append(s);used+=s['kwh']
        energy+=used;all_segments.extend(heats)
        wanted,lo,hi,zone=comfort_at(datetime.fromtimestamp(b-.001,now.tzinfo),settings)
        rows.append(dict(ts=b,temperature=round(r,2),return_=round(f,2),
                         target=wanted,lower=lo,upper=hi,comfort_zone=zone,
                         **{'return':round(f,2)},heat=bool(heats),kwh=round(used,4),
                         heat_segments=heats,outside_minutes=0.,
                         schedule_known=bool(data.get('valid') and data.get('known_until',0)>=b),
                         weather_known=bool(weather and max(x['ts'] for x in weather)+3600>=b)))
    if not morning_runs_valid(rows,now,settings.get('morning_min_minutes',30),
                              settings.get('morning_elapsed_minutes',0)):
        return None
    morning_end=now.replace(hour=7,minute=0,second=0,microsecond=0)
    if morning_end<=now:
        morning_end+=timedelta(days=1)
    minimum=settings.get('morning_min_minutes',30)
    if (priority and settings.get('morning_elapsed_minutes',0)<minimum and
            now<=morning_end-timedelta(minutes=minimum) and
            not morning_runs(rows,now.tzinfo)):
        return None
    mix=dict(evening=0.,morning=0.,other=0.)
    for s in all_segments:
        tm=datetime.fromtimestamp(s['start'],now.tzinfo)
        kind='evening' if in_window(tm,'23:00','01:00') else 'morning' if in_window(tm,'05:00','07:00') else 'other'
        mix[kind]+=s['kwh']
    schedule_until=data.get('known_until') if data.get('valid') else None
    weather_until=max((x['ts']+3600 for x in weather),default=None)
    return dict(heat=bool(all_segments and all_segments[0]['start']==start),curve=rows,
                energy=round(energy,2),minimum=round(low,2),maximum=round(high,2),
                horizon_hours=24.,validated=bool(model.get('validated')),
                morning_min_minutes=settings.get('morning_min_minutes',30),
                morning_runs_minutes=[round((b-a)/60,1) for a,b in morning_runs(rows,now.tzinfo)],
                split_comfort_applied=split,session_kwh={k:round(v,3) for k,v in mix.items()},
                morning_priority_applied=priority and split,
                night_air_target=(settings['night_lower']+.05 if priority else
                                  max(settings['lower'],settings['target']-.5)),
                split_reason='Часи сеансів збережено; прогноз оновлено за фактичними вимірами',
                schedule_known_until=schedule_until,weather_known_until=weather_until,
                conditional_heat_minutes=round(sum((s['end']-s['start'])/60 for s in all_segments if s['conditional']),1),
                schedule_complete=schedule_until is not None and schedule_until>=finish,
                weather_complete=weather_until is not None and weather_until>=finish,
                outage_shifted=False,outage_preheat=False,outside_minutes=0.,
                outage_status=data.get('reason','Графік не підключений'),
                comfort_shortfall=any(r['temperature']<r['lower'] for r in rows),heat_segments=all_segments,
                schedule_updated=data.get('updated'),plan_stable=True)


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
            common_min=(min((r['temperature'] for r in result['curve']
                            if r['ts']<=previous['at']+86400),default=result['minimum'])
                        if result else None)
            # A worsening predicted deficit or upper excursion overrides holding.
            if (result and common_min>=min(settings['lower'],previous['minimum'])-.1
                    and result['maximum']<=max(settings['upper'],room)+.01):
                result['stability_reason']='Невеликі зміни вимірів; часи сеансів збережено'
                return result,previous
        reason='Нові умови, зміна запасу тепла або обмежень — план перебудовано'
    result=optimise(now,room,floor,outside,fire,model,weather,settings,power_kw)
    result.update(plan_stable=False,stability_reason=reason)
    return result,reference(now,room,floor,outside,fire,model,weather,settings,result)

