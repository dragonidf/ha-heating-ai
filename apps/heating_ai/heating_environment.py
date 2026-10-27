"""Conservative thermal observer and separately learned daytime heat gains.

The stored floor state is a return-equivalent estimate, NOT a surface reading.
Unexplained gains are not proof of sunlight. Forecast credit requires several
days of comparable observations; uncertainty is used for overheating checks.
"""
import math
import statistics
from datetime import datetime

ENVIRONMENT_POLICY_VERSION=5


def finite(value):
    try:
        value=float(value)
        return value if math.isfinite(value) else None
    except (TypeError,ValueError):
        return None


def observe_floor(previous, stamp, room, probe, power_kw, grid, model):
    if finite(room) is None or finite(probe) is None:
        return previous or {}, probe
    state=dict(previous or {})
    estimate=finite(state.get('estimate'))
    if estimate is None:
        estimate=probe
    hours=max(0.,min(24.,(stamp-state.get('at',stamp))/3600))
    # Integrate the preceding measured power, not a command or a nominal 9 kW.
    old_power=finite(state.get('power_kw')) or 0.
    if hours>0:
        decay=max(.001,model['floor_decay'])
        equilibrium=room+model['heat_gain']*old_power/decay
        estimate=equilibrium+(estimate-equilibrium)*math.exp(-decay*hours)
    heating=grid=='on' and finite(power_kw) is not None and power_kw>=1.5
    if heating:
        # Heating proves circulation, after allowing the pipe to flush.
        # Its return reading then anchors the equivalent thermal state.
        state.setdefault('heating_since',stamp)
        estimate=probe if stamp-state['heating_since']>=180 else max(estimate,probe)
        state['last_heated_at']=stamp
    elif grid=='on' and stamp-state.get('last_heated_at',stamp)>6*3600:
        # Very slow correction; a cold stagnant pipe cannot erase a warm slab.
        estimate+=(probe-estimate)*min(.02,hours*.02)
    if not heating:
        state.pop('heating_since',None)
    estimate=max(room-2.,min(50.,estimate))
    state.update(at=stamp,estimate=estimate,power_kw=power_kw if heating else 0.,
                 source='heating_probe' if heating else 'stored_heat_estimate',
                 probe=probe,grid=grid)
    return state,estimate


def daylight(when):
    hour=when.hour+when.minute/60
    return max(0.,math.sin(math.pi*(hour-7)/11)) if 7<hour<18 else 0.


def weather_factor(condition):
    return {'sunny':1.,'clear':1.,'clear-night':0.,'partlycloudy':.6,
            'cloudy':.2,'rainy':.05,'pouring':0.,'snowy':.05}.get(condition,0.)


def observe_gain(previous, stamp, room, floor, outside, fire, heating,
                 model, timezone, condition=None,secondary=None,reported_at=None):
    state=dict(previous or {})
    observations=list(state.get('observations',[]))
    last=state.get('last')
    fresh=reported_at if reported_at is not None else stamp if not last or room!=last['room'] else last.get('reported_at',last['at'])
    current=dict(at=stamp,reported_at=fresh,room=room,floor=floor,outside=outside,
                 fire=fire,heating=heating,secondary=secondary)
    pending=state.get('pending_gain')
    reverse=last and room<last['room']-.1
    if reverse or heating or fire>=2:
        state.update(observed_gain=0.,observed_at=stamp)
        state.pop('pending_gain',None)
        if reverse and stamp-last['at']<1080:
            observations=[x for x in observations if x['at']<last['at']]
    elif pending and stamp-pending['at']>=1080 and fresh>pending.get('reported_at',pending['at']):
        other=pending.get('secondary')
        consistent=(secondary is None or other is None or secondary>=other)
        if room>=pending['room']-.1 and consistent:
            observations.append({k:v for k,v in pending.items() if k in ('at','day','gain','factor')})
            observations[-1].update(stable=True,secondary_checked=secondary is not None and other is not None)
        state.pop('pending_gain',None)
    if not last:
        state['last']=current
    elif room!=last['room']:
        hours=(stamp-last['at'])/3600
        now=datetime.fromtimestamp(stamp,timezone)
        if (.3<=hours<=1.5 and not heating and not last['heating'] and
                fire<2 and last['fire']<2 and daylight(now)>.1):
            predicted=model['floor_air']*(last['floor']-last['room'])-model['room_loss']*(last['room']-last['outside'])
            residual=(room-last['room'])/hours-predicted
            # Room devices report quantised changes; never fit 15-second jumps.
            residual=max(0.,min(.8,residual))
            state['pending_gain']=dict(at=stamp,reported_at=fresh,day=now.date().isoformat(),gain=residual,
                factor=daylight(now)*weather_factor(condition),room=room,secondary=secondary)
            state['observed_gain']=residual
            state['observed_at']=stamp
        state['last']=current
    observations=[x for x in observations if stamp-x['at']<14*86400][-240:]
    state['observations']=observations
    eligible=[x for x in observations if x['factor']>=.15 and x.get('stable')]
    days=len({x['day'] for x in eligible})
    state['days']=days
    last_day=max((x['day'] for x in eligible),default=None)
    train=[x for x in eligible if x['day']!=last_day]
    holdout=[x for x in eligible if x['day']==last_day]
    candidate=min(.6,statistics.median(x['gain']/x['factor'] for x in train)) if train else 0.
    baseline=statistics.mean(x['gain'] for x in holdout) if holdout else None
    error=statistics.mean(abs(x['gain']-candidate*x['factor']) for x in holdout) if holdout else None
    accepted=(days>=3 and len(eligible)>=12 and error is not None and baseline>.02 and error<baseline*.9)
    old=state.get('coefficient',0.)
    if state.get('commissioned'):
        # Once commissioned, move by at most 20% per local day.
        today=datetime.fromtimestamp(stamp,timezone).date().isoformat()
        if accepted and state.get('fit_day')!=today:
            delta=abs(old)*.2
            state['coefficient']=max(0.,min(.6,max(old-delta,min(old+delta,candidate))))
            state['fit_day']=today
    else:
        state['coefficient']=candidate
        if accepted:
            state['commissioned']=True
            state['fit_day']=datetime.fromtimestamp(stamp,timezone).date().isoformat()
    state['holdout_error']=error
    state['validated']=accepted
    return state


def gain_at(when, weather, settings, upper=False):
    environment=settings.get('environment') or {}
    if not environment:
        return 0.
    solar=environment.get('solar') or {}
    entries=[x for x in weather if x['ts']<=when.timestamp()]
    factor=weather_factor(entries[-1].get('condition')) if entries else 0.
    learned=solar.get('coefficient',0.)*daylight(when)*factor
    # Provisional future solar is never treated as guaranteed free heating.
    if not solar.get('validated') and not upper:
        learned=0.
    elapsed=max(0.,(when.timestamp()-solar.get('observed_at',0))/3600)
    observed=(solar.get('observed_gain',0.)*math.exp(-elapsed/2)
              if elapsed<4 and daylight(when)>0 else 0.)
    return min(.8,max(learned,observed)) if upper else min(.8,learned)


def forecast_step(room,floor,outside,power,fire,model,hours,when,weather,settings,upper=False):
    gain=gain_at(when,weather,settings,upper=upper)
    dr=model['floor_air']*(floor-room)-model['room_loss']*(room-outside)+model['fire_gain']*max(0.,fire)+gain
    df=model['heat_gain']*power-model['floor_decay']*(floor-room)
    return room+dr*hours,floor+df*hours


def safe_upper(upper, zone, settings):
    # The reserve targets daytime overshoot. Applying it again to the lower
    # sleep profile can remove every contiguous pre-07 morning interval.
    return upper-settings.get('upper_margin',0.) if zone=='День' else upper


def reliability(errors):
    values=[finite(x) for x in errors[-12:]]
    values=[x for x in values if x is not None]
    mae=statistics.mean(values) if values else None
    degraded=len(values)>=6 and mae>.3
    return dict(reliable=len(values)>=12 and not degraded,recent_mae=mae,
                degraded=degraded,upper_margin=min(.3,mae*.5) if degraded else 0.)


def anchor_weather(weather, stamp, outside):
    """Correct the near-term forecast towards the local outdoor measurement.

    This is a bounded local bias, decaying in three hours, not a rewrite of
    the provider's cached data or an assumption about tomorrow's sunshine.
    """
    past=[r for r in weather if r['ts']<=stamp]
    if not past:
        return list(weather),0.
    bias=max(-4.,min(4.,outside-past[-1]['temperature']))
    corrected=[dict(r,temperature=r['temperature']+bias*math.exp(-max(0.,r['ts']-stamp)/10800))
               for r in weather]
    return corrected,bias

