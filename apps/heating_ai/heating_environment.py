"""Conservative thermal observer and separately learned daytime heat gains.

The stored floor state is a return-equivalent estimate, NOT a surface reading.
Unexplained gains are not proof of sunlight. Forecast credit requires several
days of comparable observations; uncertainty is used for overheating checks.
"""
import math
import statistics
from datetime import datetime,timedelta
from zoneinfo import ZoneInfo

ENVIRONMENT_POLICY_VERSION=6

def room_reporting_limits(samples,max_age):
    reports=sorted({finite(r.get('room_reported_at')) for r in samples if finite(r.get('room_reported_at')) is not None})
    gaps=[b-a for a,b in zip(reports,reports[1:]) if 60<b-a<=max_age]
    cadence=sorted(gaps)[int(.9*(len(gaps)-1))] if len(gaps)>=12 else None
    return dict(cadence_seconds=round(cadence,1) if cadence else None,intervals=len(gaps),
        warning_seconds=min(max_age,cadence*1.5) if cadence else max_age/2,
        critical_seconds=min(max_age,cadence*3) if cadence else max_age)


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


def solar_context(latitude,longitude,when):
    """Prepare five small Astral day records outside the numerical search loop."""
    if finite(latitude) is None or finite(longitude) is None:
        return {}
    try:
        from astral import Observer
        from astral.sun import sun,elevation
        observer=Observer(float(latitude),float(longitude))
        days={}
        for offset in range(5):
            date=when.date()+timedelta(days=offset)
            times=sun(observer,date=date,tzinfo=when.tzinfo)
            # At northern solar noon: altitude = 90 - latitude + declination.
            declination=elevation(observer,times['noon'],with_refraction=False)+float(latitude)-90
            days[date.isoformat()]={**{k:times[k].timestamp() for k in ('sunrise','sunset','noon')},
                                   'declination':declination}
        return dict(latitude=float(latitude),timezone=str(when.tzinfo),days=days)
    except (ImportError,ValueError,TypeError):
        return {}  # No astronomy is not evidence of free solar heat.


def daylight(when,astronomy=None):
    if (astronomy or {}).get('timezone'):
        when=when.astimezone(ZoneInfo(astronomy['timezone']))
    day=((astronomy or {}).get('days') or {}).get(when.date().isoformat())
    latitude=finite((astronomy or {}).get('latitude'))
    if not day or latitude is None or not day['sunrise']<when.timestamp()<day['sunset']:
        return 0.
    latitude,declination=map(math.radians,(latitude,day['declination']))
    hour_angle=(when.timestamp()-day['noon'])*math.pi/43200
    return max(0.,math.sin(latitude)*math.sin(declination)+math.cos(latitude)*math.cos(declination)*math.cos(hour_angle))


def weather_factor(condition,cloud_coverage=None):
    clouds=finite(cloud_coverage)
    if clouds is not None and 0<=clouds<=100:
        return 1.-.9*clouds/100
    return {'sunny':1.,'clear':1.,'clear-night':0.,'partlycloudy':.6,
            'cloudy':.2,'rainy':.05,'pouring':0.,'snowy':.05}.get(condition,0.)


def observe_gain(previous, stamp, room, floor, outside, fire, heating,
                 model, timezone, condition=None,secondary=None,reported_at=None,
                 astronomy=None,cloud_coverage=None,quality=True,settings=None,fit_coefficients=True):
    state=dict(previous or {})
    state.setdefault('days',0);state.setdefault('coefficient',0.)
    # A temporary observation outage cannot revoke a previously qualified
    # coefficient. Do not bridge the gap or promote a provisional estimate.
    # An incompatible factor policy still invalidates the old certificate.
    state['observation_quality']=bool(quality)
    if not quality:
        state.pop('last',None);state.pop('pending_gain',None)
        state['validated']=bool(state.get('validated') is True and
                                state.get('factor_policy')==ENVIRONMENT_POLICY_VERSION)
        return state
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
            observations[-1].update(stable=True,secondary_checked=secondary is not None and other is not None,
                                    factor_policy=ENVIRONMENT_POLICY_VERSION)
        state.pop('pending_gain',None)
    if not last:
        state['last']=current
    elif fresh>last.get('reported_at',last['at']) and room!=last['room']:
        hours=(stamp-last['at'])/3600
        now=datetime.fromtimestamp(stamp,timezone)
        if (.3<=hours<=1.5 and not heating and not last['heating'] and
                fire<2 and last['fire']<2 and daylight(now,astronomy)>.1):
            predicted=model['floor_air']*(last['floor']-last['room'])-model['room_loss']*(last['room']-last['outside'])
            predicted-=ventilation_loss(last['room'],last['outside'],model['room_loss'],settings or {})
            residual=(room-last['room'])/hours-predicted
            # Room devices report quantised changes; never fit 15-second jumps.
            residual=max(0.,min(.8,residual))
            state['pending_gain']=dict(at=stamp,reported_at=fresh,day=now.date().isoformat(),gain=residual,
                factor=daylight(now,astronomy)*weather_factor(condition,cloud_coverage),room=room,secondary=secondary)
            state['observed_gain']=residual
            state['observed_at']=stamp
        if hours>=.3 or hours<=0:
            state['last']=current
    observations=[x for x in observations if stamp-x['at']<14*86400][-240:]
    state['observations']=observations
    if not fit_coefficients:
        state.setdefault('validated',False)
        return state
    eligible=[x for x in observations if x['factor']>=.15 and x.get('stable') and x.get('factor_policy')==ENVIRONMENT_POLICY_VERSION]
    days=len({x['day'] for x in eligible})
    state['days']=days
    last_day=max((x['day'] for x in eligible),default=None)
    train=[x for x in eligible if x['day']!=last_day]
    holdout=[x for x in eligible if x['day']==last_day]
    candidate=min(.6,statistics.median(x['gain']/x['factor'] for x in train)) if train else 0.
    old=state.get('coefficient',0.)
    today=datetime.fromtimestamp(stamp,timezone).date().isoformat()
    proposal=(max(old*.9,min(old*1.1,candidate)) if state.get('commissioned') else candidate)
    if state.get('fit_day')==today:proposal=old
    baseline=statistics.mean(x['gain'] for x in holdout) if holdout else None
    old_error=statistics.mean(abs(x['gain']-old*x['factor']) for x in holdout) if holdout else None
    error=statistics.mean(abs(x['gain']-proposal*x['factor']) for x in holdout) if holdout else None
    enough=days>=3 and len(eligible)>=12 and baseline is not None and baseline>.02
    accepted=enough and error<(.9*old_error if state.get('commissioned') else .9*baseline)
    if accepted:
        state.update(coefficient=proposal,commissioned=True,fit_day=today,factor_policy=ENVIRONMENT_POLICY_VERSION)
    state['holdout_error']=error if accepted else old_error
    state['validated']=bool(enough and state.get('factor_policy')==ENVIRONMENT_POLICY_VERSION and
                            state['holdout_error']<baseline*.9)
    return state


def gain_at(when, weather, settings, upper=False):
    environment=settings.get('environment') or {}
    if not environment:
        return 0.
    stamp=when.timestamp()
    row=next((row for row in reversed(weather) if 0<=stamp-row['ts']<3600),None)
    nominal,risk=_weather_gains(when,row,environment)
    return risk if upper else nominal


def _weather_gains(when, row, environment):
    if not environment:
        return 0.,0.
    solar=environment.get('solar') or {}
    factor=weather_factor(row.get('condition'),row.get('cloud_coverage')) if row is not None else 0.
    light=daylight(when,environment.get('astronomy'))
    learned=solar.get('coefficient',0.)*light*factor
    # Provisional future solar is never treated as guaranteed free heating.
    if not solar.get('validated'):
        learned=0.
    elapsed=max(0.,(when.timestamp()-solar.get('observed_at',0))/3600)
    observed=(solar.get('observed_gain',0.)*math.exp(-elapsed/2)
              if elapsed<3 and light>0 and datetime.fromtimestamp(solar.get('observed_at',0),when.tzinfo).date()==when.date() else 0.)
    residual=residual_at(when,environment.get('residual') or {})
    # An independently held-out observational candidate is only a risk
    # scenario. It never supplies nominal comfort credit. Do not add it to
    # an observed gain or another solar estimate from the same heat source.
    risk_coefficient=finite(solar.get('risk_coefficient'))
    risk_solar=risk_coefficient*light*factor if risk_coefficient is not None and 0<risk_coefficient<=.8 else 0.
    # A validated solar estimate improves a conditional temperature outlook,
    # but unguaranteed future heat must not satisfy the control lower bound.
    nominal=0. if solar.get('control_no_sun') is True else min(.8,learned)
    return nominal,min(.8,max(learned,observed,learned+residual,risk_solar))


def room_source_check(previous,stamp,readings):
    """Training-only cross-check. Never replace the regulating temperature."""
    values=[finite(v) for v,_ in readings];times=[finite(t) for _,t in readings]
    if (None in values or None in times or any(not 0<=stamp-t<=300 for t in times)
            or max(times)-min(times)>180):
        return dict(status='unconfirmed')
    suspect=abs(values[1]-values[2])<=.5 and abs(values[0]-(values[1]+values[2])/2)>1.2
    if not suspect:
        return dict(status='agree' if max(values)-min(values)<=1.2 else 'unconfirmed')
    prior=previous or {}
    if prior.get('status') not in ('pending','room_source_disagreement') or stamp-prior.get('since',stamp)>600:
        prior={}
    repeated=(prior.get('status') in ('pending','room_source_disagreement') and
              180<=stamp-prior.get('since',stamp)<=600 and
              all(t>p for t,p in zip(times,prior.get('reports',times))))
    return dict(status='room_source_disagreement' if repeated else prior.get('status','pending'),
                since=prior.get('since',stamp),reports=prior.get('reports',times))


def residual_at(when,state):
    at=finite(state.get('at'))
    if at is None or when.timestamp()<at or datetime.fromtimestamp(at,when.tzinfo).date()!=when.date():
        return 0.
    return max(-.4,min(.4,state.get('rate',0.)))*2**(-(when.timestamp()-at)/5400)


def observe_residual(previous,stamp,room,floor,outside,model,reported_at,quality,weather,settings):
    """Two independent reports, physical floor prediction, no coefficient update."""
    state=dict(previous or {})
    if not quality or any(finite(x) is None for x in (room,floor,outside,reported_at)) or not 0<=stamp-reported_at<=300:
        state.pop('last',None)
        return state
    last=state.get('last')
    if last and reported_at<=last['reported_at']:
        return state
    if last and stamp-last['at']<300:
        return state
    context={k:settings[k] for k in ('environment','ventilation_model','ventilation_expected','thermal_model_version') if k in settings}
    context['environment']={**context.get('environment',{}),'residual':{}}
    current=dict(at=stamp,reported_at=reported_at,room=room,floor=floor,outside=outside,
                 weather=weather,settings=context,model=dict(model))
    state['last']=current
    if last and last.get('model')!=model:
        state.pop('at',None);state.pop('rate',None)
        return state
    if not last or last.get('model')!=model or not 300<=stamp-last['at']<=5400:
        return state
    predicted,stored=last['room'],last['floor']
    elapsed=stamp-last['at'];cursor=last['at']
    zone=ZoneInfo((settings.get('environment') or {}).get('timezone','Europe/Kyiv'))
    # Use the previous anchored weather and floor state, never future actuals.
    while cursor<stamp:
        seconds=min(300,stamp-cursor)
        when=datetime.fromtimestamp(cursor,zone)
        rows=last['weather']
        out=last['outside']+(outside-last['outside'])*(cursor-last['at'])/elapsed
        predicted,stored=forecast_step(predicted,stored,out,0.,0.,model,seconds/3600,
                                      when,rows,last['settings'])
        cursor+=seconds
    rate=max(-.4,min(.4,(room-predicted)*3600/elapsed))
    state.update(at=stamp,rate=rate)
    return state


def forecast_step(room,floor,outside,power,fire,model,hours,when,weather,settings,upper=False):
    gain=gain_at(when,weather,settings,upper=upper)
    loss=ventilation_loss(room,outside,model['room_loss'],settings,upper=upper)
    return _integrate_forecast(room,floor,outside,power,model,hours,
                               model['fire_gain']*max(0.,fire),gain,loss)


def _integrate_forecast(room,floor,outside,power,model,hours,fire_gain,gain,loss):
    dr=model['floor_air']*(floor-room)-model['room_loss']*(room-outside)+fire_gain+gain-loss
    df=model['heat_gain']*power-model['floor_decay']*(floor-room)
    return room+dr*hours,floor+df*hours


def prepare_ventilation(base_loss,settings):
    fit=settings.get('ventilation_model') or {}
    if not fit.get('validated') or fit.get('model_version')!=settings.get('thermal_model_version'):
        return None,None,None
    expected=settings.get('ventilation_expected') or {}
    nominal=(_ventilation_adjustment(fit,base_loss,expected)
             if all(finite(expected.get(k)) is not None for k in ('recuperator','bath')) else None)
    return (nominal,_ventilation_adjustment(fit,base_loss,dict(recuperator=0.,bath=0.)),
            _ventilation_adjustment(fit,base_loss,dict(recuperator=1.,bath=1.)))


def has_upper_risk(settings):
    environment=settings.get('environment') or {}
    solar=environment.get('solar') or {}
    profile=settings.get('modulation_profile') or {}
    return bool(profile.get('power_source')=='kws_phases' and profile.get('validated') is True and
                profile.get('conservative_upper_kw') or solar.get('observed_gain') or solar.get('validated') and solar.get('coefficient') or
                solar.get('risk_coefficient') or
                (environment.get('residual') or {}).get('rate',0)>0 or (settings.get('ventilation_model') or {}).get('validated'))


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
    return _ventilation_adjustment(fit,base_loss,expected)*(room-outside)


def _ventilation_adjustment(fit,base_loss,expected):
    # Wider scenarios use maximal/minimal duty; relay ON is not measured airflow.
    duty={k:max(0.,min(1.,expected[k])) for k in ('recuperator','bath')}
    effective=fit['base_room_loss']+sum(fit[k]*duty[k] for k in duty)
    return effective-base_loss


def effective_ventilation_duty(evidence):
    """Observed powered duty, not commanded relay fraction; unknown is None.

    Mixed duty is usable for a mean ventilation estimate, but not by the
    constant-duty adaptive window fitter. Legacy ON without supply evidence
    is never promoted to measured airflow. This measures powered operation,
    not the actual volume of air exchanged.
    """
    if not isinstance(evidence,dict) or (finite(evidence.get('seconds')) or 0)<240:
        return None
    if 'effective_on_fraction' in evidence or 'effective_unknown_fraction' in evidence:
        duty=finite(evidence.get('effective_on_fraction'))
        return duty if duty is not None and 0<=duty<=1 and finite(evidence.get('effective_unknown_fraction'))==0 else None
    on=finite(evidence.get('on_fraction'))
    unknown=finite(evidence.get('unknown_fraction'))
    supply_unknown=finite(evidence.get('supply_unknown_fraction'))
    powered=finite(evidence.get('powered_relay_on_fraction'))
    if supply_unknown==0 and finite(evidence.get('supply_on_fraction'))==0 and powered==0:
        return 0.
    if unknown!=0 or on is None or not 0<=on<=1:
        return None
    if on==0:
        return 0.
    return powered if supply_unknown==0 and powered is not None and 0<=powered<=on+.001 and powered<=1 else None


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
        if any(not r.get('quality') or r.get('room_source_disagreement') or r.get('room_sources_suspect') or abs(r.get('residual_gain',0))>.1 or
               r.get('environment_version',0)>=6 and not r.get('astronomy_valid') or
               r.get('power_estimated') or r.get('power_quality')!='measured' or
               r.get('grid')!='on' or r.get('fire',0)>2 or r.get('daylight_weight',1)>.1 for r in chunk):continue
        room=finite(a.get('room'));outside=finite(a.get('outside'));floor=finite(a.get('floor_estimate'))
        if None in (room,outside,floor) or abs(room-outside)<5:continue
        u={};valid=True
        for key in ('recuperator','bath'):
            vals=[(r.get('ventilation') or {}).get(key) or {} for r in chunk[1:]]
            duties=[effective_ventilation_duty(v) for v in vals]
            if any(d is None for d in duties):valid=False;break
            u[key]=statistics.mean(duties)
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
        device=current.get(key) or {}
        entity=device.get('entity')
        known=[d for v in rows if (not entity or v.get('entity')==entity)
               and (d:=effective_ventilation_duty(v)) is not None]
        result[key]=statistics.mean(known) if len(known)>=12 else None
        support[key]=dict(intervals=len(known),unknown_fraction=1-len(known)/len(rows) if rows else 1,
            current=device.get('relay','unknown'),effective_duty=device.get('effective_duty'),
            future='empirical_powered_duty' if len(known)>=12 else 'unknown')
    return result,support
