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


def observe_floor(previous, stamp, room, probe, power_kw, grid, model, energy_kwh=None):
    if finite(room) is None or finite(probe) is None:
        return previous or {}, probe
    state=dict(previous or {})
    estimate=finite(state.get('estimate'))
    if estimate is None:
        estimate=probe
    hours=max(0.,min(24.,(stamp-state.get('at',stamp))/3600))
    # Integrate the preceding measured power, not a command or a nominal 9 kW.
    old_power=finite(state.get('power_kw')) or 0.
    before=finite(state.get('energy_kwh'))
    if energy_kwh is not None and before is not None and hours>0:
        measured=(energy_kwh-before)/hours
        old_power=measured if 0<=measured<=10.5 and hours<=.1 else 0.
    elif hours>45/3600 and old_power>0:
        old_power=0.  # No invented heating across an unobserved restart/gap.
    if hours>0:
        decay=max(.001,model['floor_decay'])
        equilibrium=room+model['heat_gain']*old_power/decay
        estimate=equilibrium+(estimate-equilibrium)*math.exp(-decay*hours)
    heating=grid=='on' and finite(power_kw) is not None and power_kw>=1.5
    if heating:
        # Return water is a proxy, not the slab's integrated heat inventory.
        # A cool pipe must not erase energy delivered by the measured boiler.
        state.setdefault('heating_since',stamp)
        estimate=max(estimate,probe)
        state['last_heated_at']=stamp
    elif grid=='on' and stamp-state.get('last_heated_at',stamp)>6*3600:
        # Very slow correction; a cold stagnant pipe cannot erase a warm slab.
        estimate+=(probe-estimate)*min(.02,hours*.02)
    if not heating:
        state.pop('heating_since',None)
    estimate=max(room-2.,min(50.,estimate))
    state.update(at=stamp,estimate=estimate,power_kw=power_kw if heating else 0.,energy_kwh=energy_kwh,
                 energy_retained=heating and estimate>probe+.1,
                 source='measured_energy_inventory' if heating and estimate>probe+.1 else 'heating_probe' if heating else 'stored_heat_estimate',
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
    dr=model['floor_air']*(floor-room)-model['room_loss']*(room-outside)+model['fire_gain']*max(0.,fire)+gain-ventilation_loss(room,outside,model['room_loss'],settings,upper=upper)
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



def ventilation_loss(room,outside,base_loss,settings,upper=False):
    """Replace the previously averaged loss, rather than adding it twice."""
    fit=settings.get('ventilation_model') or {}
    if not fit.get('validated') or fit.get('model_version')!=settings.get('thermal_model_version'):return 0.
    expected=settings.get('ventilation_expected') or {}
    if upper:
        expected={k:0. if room>=outside else 1. for k in ('recuperator','bath')}
    elif any(finite(expected.get(k)) is None for k in ('recuperator','bath')):return 0.
    # Wider scenarios use maximal/minimal duty; relay ON is not measured airflow.
    duty={k:max(0.,min(1.,expected[k])) for k in ('recuperator','bath')}
    effective=fit['base_room_loss']+sum(fit[k]*duty[k] for k in duty)
    return (effective-base_loss)*(room-outside)


def fit_ventilation(samples,model,timezone=None):
    """Joint base/recuperator/bath effective loss, independent held-out days.

    Unknown duty, correlated relays and daylight/fire disturbances cannot
    identify independent coefficients. The old model remains the fallback.
    """
    days_present=sorted({datetime.fromtimestamp(r['ts'],timezone).date().isoformat() for r in samples if isinstance(r.get('ts'),(int,float))})
    first=next((r for r in samples if days_present and datetime.fromtimestamp(r['ts'],timezone).date().isoformat()==days_present[-1]),{})
    frozen=first.get('thermal_model')
    if frozen and frozen!=model:return dict(validated=False,model_version=model.get('version'),days=0,samples=0,reason='thermal_model_changed')
    model=frozen or model
    records=[]
    for index in range(6,len(samples),6):
        chunk=samples[index-6:index+1];a,b=chunk[0],chunk[-1]
        dt=(b['ts']-a['ts'])/3600
        if not .4<=dt<=.6:continue
        if any(not r.get('quality') or r.get('power_estimated') or r.get('power_quality')!='measured' or
               r.get('grid')!='on' or r.get('fire',0)>2 or r.get('daylight_weight',1)>.1 for r in chunk):continue
        room=finite(a.get('room'));outside=finite(a.get('outside'));floor=finite(a.get('floor_estimate'))
        if None in (room,outside,floor) or abs(room-outside)<5:continue
        u={};valid=True
        for key in ('recuperator','bath'):
            vals=[(r.get('ventilation') or {}).get(key) or {} for r in chunk[1:]]
            if any(finite(v.get('on_fraction')) is None or v.get('unknown_fraction',1)>.05 for v in vals):valid=False;break
            u[key]=statistics.mean(v['on_fraction'] for v in vals)
        if not valid:continue
        delta=room-outside;rate=(b['room']-room)/dt
        drive=model['floor_air']*(floor-room)+model['fire_gain']*a.get('fire',0)
        records.append(dict(x=[1.,u['recuperator'],u['bath']],y=(drive-rate)/delta,
            day=datetime.fromtimestamp(a['ts'],timezone).date().isoformat(),dt=dt,delta=delta,drive=drive,rate=rate))
    days=sorted(day for day in {r['day'] for r in records} if sum(r['day']==day for r in records)>=6)
    records=[r for r in records if r['day'] in days]
    result=dict(validated=False,model_version=model.get('version'),days=len(days),samples=len(records),reason='insufficient_independent_days')
    if len(days)<3:return result
    train=[r for r in records if r['day']!=days[-1]];hold=[r for r in records if r['day']==days[-1]]
    if len(train)<12 or len(hold)<6:return result
    matrix=[[sum(r['x'][i]*r['x'][j] for r in train) for j in range(3)]+[sum(r['x'][i]*r['y'] for r in train)] for i in range(3)]
    for i in range(3):
        pivot=max(range(i,3),key=lambda k:abs(matrix[k][i]));matrix[i],matrix[pivot]=matrix[pivot],matrix[i]
        if abs(matrix[i][i])<1e-7:return dict(result,reason='correlated_or_constant_relays')
        scale=matrix[i][i];matrix[i]=[v/scale for v in matrix[i]]
        for j in range(3):
            if j==i:continue
            factor=matrix[j][i];matrix[j]=[a-factor*b for a,b in zip(matrix[j],matrix[i])]
    coefficients=[matrix[i][3] for i in range(3)]
    if not (max(.001,model['room_loss']*.8)<=coefficients[0]<=min(.15,model['room_loss']*1.2) and all(0<=v<=.05 for v in coefficients[1:])):
        return dict(result,reason='coefficient_bounds')
    error=[abs((sum(v*x for v,x in zip(coefficients,r['x']))-r['y'])*r['delta']*r['dt']) for r in hold]
    baseline=[abs((model['room_loss']-r['y'])*r['delta']*r['dt']) for r in hold]
    accepted=statistics.mean(baseline)>.01 and statistics.mean(error)<statistics.mean(baseline)*.95 and max(error)<=max(baseline)+.02
    return dict(result,validated=accepted,reason='heldout_improvement' if accepted else 'no_heldout_improvement',
        base_room_loss=coefficients[0],recuperator=coefficients[1],bath=coefficients[2],
        holdout_mae=statistics.mean(error),baseline_mae=statistics.mean(baseline),holdout_day=days[-1])


def ventilation_expectation(samples,current):
    """Empirical duty scenarios, not invented future switching timestamps."""
    result={};support={}
    for key in ('recuperator','bath'):
        rows=[(r.get('ventilation') or {}).get(key) or {} for r in samples[-288:]]
        known=[v['on_fraction'] for v in rows if finite(v.get('on_fraction')) is not None and v.get('unknown_fraction',1)<=.05]
        result[key]=statistics.mean(known) if len(known)>=12 else None
        support[key]=dict(intervals=len(known),unknown_fraction=1-len(known)/len(rows) if rows else 1,
            current=(current.get(key) or {}).get('relay','unknown'),future='empirical_duty' if len(known)>=12 else 'unknown')
    return result,support
