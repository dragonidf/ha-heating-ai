"""Predictive heating: deterministic model, bounded learning and safety decisions."""
from bisect import bisect_right
from datetime import datetime, timedelta
import math
import hashlib
import json
from heating_environment import (forecast_step,safe_upper,has_upper_risk,ENVIRONMENT_POLICY_VERSION,
                                 _weather_gains,_integrate_forecast,prepare_ventilation)
import statistics
from heating_outages import blocked, covered, availability, schedule_covered

SEARCH_POLICY_VERSION = 3

DEFAULT_MODEL = {"floor_air": .035, "room_loss": .025, "floor_decay": .12,
                 "heat_gain": .50, "fire_gain": .025, "version": 1, "validated": False}
BOUNDS = {"floor_air": (.001, .20), "room_loss": (.001, .15),
          "floor_decay": (.01, 1.5), "heat_gain": (.02, 3.0), "fire_gain": (0., .15)}

def number(value):
    try:
        x = float(value)
        return x if math.isfinite(x) else None
    except (TypeError, ValueError):
        return None

def in_window(now, start, end):
    a = int(start[:2]) * 60 + int(start[3:5])
    b = int(end[:2]) * 60 + int(end[3:5])
    t = now.hour * 60 + now.minute
    # Equal endpoints mean no allowed interval, not unrestricted heating.
    return a <= t < b if a < b else (t >= a or t < b) if a > b else False

def step(room, floor, outside, power_kw, fire_delta, model, hours):
    dr = model["floor_air"] * (floor-room) - model["room_loss"] * (room-outside)
    dr += model["fire_gain"] * max(0., fire_delta)
    df = model["heat_gain"] * power_kw - model["floor_decay"] * (floor-room)
    return room + dr * hours, floor + df * hours

def comfort_at(when,settings):
    night=('night_target' in settings and
           in_window(when,settings.get('sleep_start','23:00'),settings.get('sleep_end','07:00')))
    prefix='night_' if night else ''
    return (settings[prefix+'target'],settings[prefix+'lower'],settings[prefix+'upper'],
            'Сон' if night else 'День')

def outdoor_at(forecast, when, fallback):
    stamp = when.timestamp()
    for row in reversed(forecast):
        if row["ts"] <= stamp:
            return row["temperature"]
    return fallback


class ForecastContext:
    """Time-dependent inputs shared by the searches of one numerical job."""

    def __init__(self, now, weather, outside, model, settings):
        self.start = now.timestamp()
        self.weather = weather
        self.outside = outside
        self.model = model
        self.environment = settings.get('environment') or {}
        self.weather_times = [row['ts'] for row in weather]
        self.ordered_weather = all(a<=b for a,b in zip(self.weather_times,self.weather_times[1:]))
        self.known_until = max(self.weather_times)+3600 if self.weather_times else None
        self.ventilation = prepare_ventilation(model.get('room_loss'),settings)
        self.by_time = {}

    def at(self, when):
        # Datetimes with the same timezone can compare equal across a DST fold.
        key = when, when.fold
        if key not in self.by_time:
            stamp = when.timestamp()
            if self.ordered_weather:
                index = bisect_right(self.weather_times,stamp)-1
                outdoor = self.weather[index] if index>=0 else None
                solar = outdoor if outdoor is not None and 0<=stamp-outdoor['ts']<3600 else None
            else:
                # V1 selects the last eligible row in input order, including ties.
                outdoor = next((row for row in reversed(self.weather) if row['ts']<=stamp),None)
                solar = next((row for row in reversed(self.weather) if 0<=stamp-row['ts']<3600),None)
            elapsed = (stamp-self.start)/60
            outside = outdoor['temperature'] if outdoor is not None else self.outside
            gains = _weather_gains(when,solar,self.environment)
            self.by_time[key] = stamp,elapsed,outside,gains
        return self.by_time[key]

    def integrate(self, room, floor, outside, power, fire_gain, hours, gain, upper=False):
        normal,cold,warm = self.ventilation
        coefficient = (cold if room>=outside else warm) if upper else normal
        loss = 0. if coefficient is None else coefficient*(room-outside)
        return _integrate_forecast(room,floor,outside,power,self.model,hours,fire_gain,gain,loss)


def coast(room, floor, outside, fire_delta, model, hours=3):
    values = [room]
    for i in range(int(hours * 12)):
        room, floor = step(room, floor, outside, 0., fire_delta*math.exp(-i/24), model, 1/12)
        values.append(room)
    return max(values), values[-1]

def coast_trajectory(now,room,floor,outside,fire_delta,model,settings,hours=3,upper=False):
    rows=[dict(ts=now.timestamp(),temperature=room)]
    for i in range(int(hours*12)):
        when=now+timedelta(minutes=5*i)
        weather=settings.get('environment_weather',[])
        room,floor=forecast_step(room,floor,outdoor_at(weather,when,outside),0.,fire_delta*math.exp(-i/24),model,1/12,
                                 when,weather,settings,upper=upper)
        rows.append(dict(ts=(when+timedelta(minutes=5)).timestamp(),temperature=room))
    return rows


def coast_exceeds_profile(now,room,floor,outside,fire_delta,model,settings,hours=3):
    for row in coast_trajectory(now,room,floor,outside,fire_delta,model,settings,hours,upper=True)[1:]:
        profile=comfort_at(datetime.fromtimestamp(row['ts'],now.tzinfo),settings)
        if row['temperature']>=safe_upper(profile[2],profile[3],settings):return True
    return False

def held_action(last,changed_at,stamp,wanted,urgent=False):
    if wanted==last or urgent:
        return wanted
    elapsed=stamp-(number(changed_at) or 0.)
    if (last=='heat' and elapsed<600) or (last=='off' and wanted=='heat' and elapsed<900):
        return last
    return wanted


def nominal_mode(power, previous):
    if power is None:
        return "unavailable"
    if power < 2000:
        return "off"
    # A 200 W deadband around nominal step midpoints.
    if previous == "3 кВт" and 2000 <= power < 4700:
        return previous
    if previous == "6 кВт" and 4300 <= power < 7700:
        return previous
    if previous == "9 кВт" and power >= 7300:
        return previous
    return "3 кВт" if power < 4500 else "6 кВт" if power < 7500 else "9 кВт"


def boiler_power_interval(a,b,power_source):
    if power_source=='kws_phases':
        return number(b.get('measured_interval_kw'))
    before, after = number(a.get('energy')), number(b.get('energy'))
    if before is not None and after is not None and after>=before:
        return (after-before)*3600/(b['ts']-a['ts'])
    return None


def modulation_interval(a,b,power_source):
    """Qualify one adjacent physical interval for training and evaluation."""
    dt=(b['ts']-a['ts'])/60
    if not 3<=dt<=7:
        return None
    if any((r.get('power_quality')!='measured' if power_source=='kws_phases' else not r.get('quality'))
           or r.get('measurement_version')!=3 or
           r.get('power_source')!=power_source or r.get('power_estimated') or
           r.get('recovery_id') for r in (a,b)):
        return None
    ra,rb=number(a.get('return')),number(b.get('return'))
    pa,pb=number(a.get('power')),number(b.get('power'))
    if None in (ra,rb,pa,pb) or not (0<ra<65 and 0<rb<65) or abs(rb-ra)>3:
        return None
    tracked='heating_requested' in a and 'heating_requested' in b
    if tracked:
        if not (a['heating_requested'] and b['heating_requested'] and
                a.get('grid')=='on' and b.get('grid')=='on' and
                a.get('heat_command_at')==b.get('heat_command_at') and
                number(a.get('heat_command_at')) is not None and
                a['ts']-a['heat_command_at']>=90):
            return None
    elif power_source=='kws_phases' or min(pa,pb)<2:
        return None
    kw=boiler_power_interval(a,b,power_source)
    if kw is None or not 0<=kw<=10.5 or (not tracked and kw<2):
        return None
    return dt,ra,rb,pb,kw


def modulation_observations(pairs,power_source):
    for a,b in pairs:
        if a.get('power_source')!=power_source or b.get('power_source')!=power_source:
            continue
        interval=modulation_interval(a,b,power_source)
        if interval is not None:
            yield interval


def learn_modulation(samples, *, power_source='balance', prior_kw=3., observations=None):
    """Fit effective demand power in 2°C return bands from trustworthy intervals.

    Rebuilt from the rolling telemetry, so reloads cannot double-count evidence.
    New rows include demand and command generation: zero-duty intervals count
    only during an uninterrupted, established heat request with a live grid.
    Legacy rows require confirmed heating at both ends (no inferred off duty).
    """
    bands = {}
    if observations is None:
        observations=modulation_observations(zip(samples,samples[1:]),power_source)
    for dt,ra,rb,pb,kw in observations:
        key=int(math.floor((ra+rb)/4))*2
        band=bands.setdefault(key,dict(minutes=0.,kwh=0.,intervals=0,stages={'3':0.,'6':0.,'9':0.}))
        band['minutes']+=dt
        band['kwh']+=kw*dt/60
        band['intervals']+=1
        if pb>=2:
            stage='3' if pb<4.5 else '6' if pb<7.5 else '9'
            band['stages'][stage]+=dt
    # Weighted isotonic fit: on average power should not rise with return
    # temperature. No invented 3/6/9 switching temperature is imposed.
    groups=[]
    for key,band in sorted(bands.items()):
        confidence=min(1.,band['minutes']/60)
        estimate=prior_kw*(1-confidence)+band['kwh']*60/band['minutes']*confidence
        groups.append(([key],estimate*band['minutes'],band['minutes']))
        while len(groups)>1 and groups[-2][1]/groups[-2][2]<groups[-1][1]/groups[-1][2]:
            right=groups.pop();left=groups.pop()
            groups.append((left[0]+right[0],left[1]+right[1],left[2]+right[2]))
    fitted={key:total/minutes for keys,total,minutes in groups for key in keys}
    rows=[]
    for key,band in sorted(bands.items()):
        confidence=min(1.,band['minutes']/60)
        rows.append(dict(return_c=key+1,return_min=key,return_max=key+2,
                         power_kw=round(min(9.,fitted[key]),3),
                         measured_kw=round(band['kwh']*60/band['minutes'],3),
                         minutes=round(band['minutes'],1),intervals=band['intervals'],
                         confidence=round(confidence,3),stage_minutes=band['stages']))
    return dict(version=1,bands=rows,intervals=sum(r['intervals'] for r in rows),
                minutes=round(sum(r['minutes'] for r in rows),1),
                prior_kw=prior_kw,method='return_bands_energy_isotonic',power_source=power_source,
                last_sample_at=samples[-1]['ts'] if samples else None)


def modulation_power(return_c, profile, fallback=3., anchor=None, ahead_minutes=0.):
    """Effective kW; unknown return ranges use a labelled conservative prior.

    The live anchor decays over 15 minutes and 2°C; it cannot impose today's
    stage on a distant evening or a different return temperature.
    """
    mode=(profile or {}).get('physical_mode')
    if mode in ('fixed_3','fixed_6','fixed_9'):
        fallback=float(mode[-1])
        if not profile.get('validated'):return fallback
        anchor=None  # A live reading from a different mode cannot change its calibration.
    rows=(profile or {}).get('bands',[])
    kw=fallback
    near=[r for r in rows if abs(r['return_c']-return_c)<=3]
    if near:
        left=[r for r in near if r['return_c']<=return_c]
        right=[r for r in near if r['return_c']>=return_c]
        if left and right:
            a,b=left[-1],right[0]
            fraction=(return_c-a['return_c'])/(b['return_c']-a['return_c']) if b!=a else 0
            kw=a['power_kw']+(b['power_kw']-a['power_kw'])*fraction
        else:
            kw=min(near,key=lambda r:abs(r['return_c']-return_c))['power_kw']
    if anchor and number(anchor.get('power_kw')) is not None and number(anchor.get('return_c')) is not None:
        weight=math.exp(-max(0.,ahead_minutes)/15)*max(0.,1-abs(return_c-anchor['return_c'])/2)
        kw=kw*(1-weight)+anchor['power_kw']*weight
    return max(0.,min(9.,kw))

def morning_runs(rows, tz):
    runs=[]
    for row in rows:
        for seg in row["heat_segments"]:
            at=datetime.fromtimestamp(seg["start"],tz)
            if not in_window(at,"05:00","07:00"):
                continue
            if runs and abs(seg["start"]-runs[-1][1])<=1:
                runs[-1]=(runs[-1][0],seg["end"])
            else:
                runs.append((seg["start"],seg["end"]))
    return runs


def morning_runs_valid(rows, now, minimum, elapsed=0):
    runs=morning_runs(rows,now.tzinfo)
    if 0<elapsed<minimum:
        if not runs or abs(runs[0][0]-now.timestamp())>1:
            return False
    for start,end in runs:
        credit=elapsed if abs(start-now.timestamp())<=1 else 0
        if (end-start)/60+credit<minimum-.001:
            return False
    return True


def elapsed_at(now, seconds):
    """Elapsed time, including on the 23/25-hour local DST dates."""
    return datetime.fromtimestamp(now.timestamp()+seconds,now.tzinfo)


def local_boundary(day, value, tz):
    wall=datetime.combine(day,datetime.min.time()).replace(
        hour=int(value[:2]),minute=int(value[3:5]),tzinfo=tz)
    # First occurrence of an ambiguous boundary; a skipped spring boundary moves
    # forward to its real local time. Never manufacture a nonexistent timestamp.
    return datetime.fromtimestamp(wall.timestamp(),tz)


def heating_cycle(now, settings):
    """The current allowed interval, or its nearest successor, in HA local time."""
    start,end=settings['start'],settings['end']
    if start[:5]==end[:5]:
        return None
    cycles=[]
    for offset in range(-2,5):
        day=now.date()+timedelta(days=offset)
        a=local_boundary(day,start,now.tzinfo)
        b=local_boundary(day+timedelta(days=end[:5]<start[:5]),end,now.tzinfo)
        if b.timestamp()>now.timestamp() and b.timestamp()>a.timestamp():
            cycles.append((a,b))
    if not cycles:
        return None
    a,b=min(cycles,key=lambda pair:pair[0].timestamp())
    following=min(local_boundary(now.date()+timedelta(days=i),start,now.tzinfo).timestamp()
                  for i in range(-1,6)
                  if local_boundary(now.date()+timedelta(days=i),start,now.tzinfo).timestamp()>=b.timestamp())
    morning=[]
    for day in (a.date(),b.date()):
        left=max(a.timestamp(),local_boundary(day,'05:00',now.tzinfo).timestamp())
        right=min(b.timestamp(),local_boundary(day,'07:00',now.tzinfo).timestamp())
        if right>left:
            morning.append((left,right))
    left,right=max(morning,default=(None,None),key=lambda pair:pair[1])
    return dict(cycle_id=b.isoformat()+'['+str(now.tzinfo)+']',cycle_start=a.timestamp(),
                cycle_end=b.timestamp(),morning_start=left,morning_end=right,
                next_allowed_heat_at=following)


def reserve_context(now, settings, model):
    cycle=heating_cycle(now,settings)
    if cycle is None:
        return None
    start=now.timestamp(); next_at=cycle['next_allowed_heat_at']
    data=availability(settings.get('outage_schedule') or {},start,start+96*3600)
    outages=data.get('outages',[]) if data.get('valid') else []
    # Do not jump an entire day when an outage ends midway through a window.
    predicted=None
    cursor=next_at
    while cursor<start+72*3600:
        tm=datetime.fromtimestamp(cursor,now.tzinfo)
        stop=cursor+300
        if (in_window(tm,settings['start'],settings['end']) and
                in_window(datetime.fromtimestamp(stop-.001,now.tzinfo),settings['start'],settings['end']) and
                not blocked(cursor,stop,outages)):
            predicted=cursor
            break
        cursor=min([cursor+300]+[o['end'] for o in outages if cursor<o['end']<cursor+300])
    # The half-response time follows the fitted transfer/decay rates, capped at
    # six hours to bound search cost. It is not a temperature/kWh allowance.
    rate=max(.01,(number(model.get('floor_air')) or 0)+(number(model.get('floor_decay')) or 0))
    delay=min(6.,math.log(2)/rate)*3600
    until=min(start+72*3600,(predicted if predicted is not None else start+72*3600)+delay)
    return dict(cycle,next_predicted_available_heat_at=predicted,assessment_until=until,
                response_delay_minutes=round(delay/60,1),
                next_heat_conditional=predicted is None or not schedule_covered(data,predicted,predicted+300))


def uncertainty_reserve(settings, hours):
    """Never extrapolate the existing one-hour MAE to an overnight/day reserve."""
    evidence=[abs(r['error']) for r in settings.get('reserve_errors',[])
              if number(r.get('hours')) is not None and r['hours']>=hours
              and number(r.get('error')) is not None and r.get('validated') is True]
    if len(evidence)<10:
        return None
    width=min(settings['target']-settings['lower'],settings['upper']-settings['lower'])
    return max(0.,min(width/2,sorted(evidence)[int(.8*(len(evidence)-1))]))


def trajectory_metrics(now, room, rows, settings, until, margin=0.):
    """Compare every interval with its own day/sleep limits, never a scalar low."""
    rows=[point for row in rows for point in row.get('_fine',[row])]
    previous=now.timestamp(); first=None; deficit=0.; minutes=0.; minimum=room
    maximum=room;peak_at=previous;comfort_minutes=overheat=night_deficit=0.
    day_deficit=0.;day_minutes=0.;day_depth=0.;first_value=first_lower=first_shortfall=None
    for row in rows:
        stop=min(row['ts'],until)
        if stop<=previous:
            break
        value=row['temperature']
        _,lo,hi,zone=comfort_at(datetime.fromtimestamp((previous+stop)/2,now.tzinfo),settings)
        duration=(stop-previous)/60
        if lo<=value<=hi:
            comfort_minutes+=duration
        overheat+=max(0.,value-hi)*duration/60
        if zone=='Сон':
            night_deficit+=max(0.,lo-value)*duration/60
        # The uncertainty allowance cannot move a night target up to the day band.
        threshold=(lo+min(margin,max(0.,comfort_at(datetime.fromtimestamp(previous,now.tzinfo),settings)[0]-lo)/2)
                   if margin else lo)
        short=max(0.,threshold-value)
        if short>.001:
            if first is None:
                first=previous;first_value=value;first_lower=threshold;first_shortfall=short
            deficit+=short*(stop-previous)/3600
            minutes+=(stop-previous)/60
            day_short=max(0.,lo-value)
            if zone=='День' and day_short>.001:
                day_deficit+=day_short*(stop-previous)/3600;day_minutes+=(stop-previous)/60
                day_depth=max(day_depth,day_short)
        minimum=min(minimum,value)
        if value>maximum:
            maximum=value;peak_at=stop
        previous=stop
        if stop>=until:
            break
    return dict(minimum_before_next_heat=round(minimum,3),
                first_violation_temperature=round(first_value,3) if first_value is not None else None,
                first_violation_lower=round(first_lower,3) if first_lower is not None else None,
                first_violation_shortfall=round(first_shortfall,3) if first_shortfall is not None else None,
                covered_until=previous,coverage_complete=previous>=until-.001,
                first_comfort_violation_at=first,deficit_degree_hours=round(deficit,5),
                night_deficit_degree_hours=round(night_deficit,5),
                overheat_degree_hours=round(overheat,5),comfort_minutes=round(comfort_minutes,2),
                predicted_maximum_at=peak_at,
                deficit_minutes=round(minutes,2),day_deficit_degree_hours=round(day_deficit,5),
                day_deficit_minutes=round(day_minutes,2),day_max_shortfall=round(day_depth,3),predicted_maximum=round(maximum,3))


def coast_reserve_needed(now, room, floor, outside, fire, model, weather, settings, context, power_kw=3.):
    r,f=room,floor; stamp=now.timestamp();rows=[]
    # Coast to the next opportunity, then permit a safe recovery there. Assess
    # the same response-delay horizon as the candidate: reaching the next
    # window above lower does not prove comfort throughout thermal recovery.
    end=context['assessment_until']
    next_heat=context['next_predicted_available_heat_at']
    data=availability(settings.get('outage_schedule') or {},now.timestamp(),end)
    outages=data.get('outages',[]) if data.get('valid') else []
    while stamp<end:
        stop=min(stamp+300,end)
        tm=datetime.fromtimestamp(stamp,now.tzinfo)
        _,lo,hi,zone=comfort_at(tm,settings)
        hi=safe_upper(hi,zone,settings)
        active=(next_heat is not None and stamp>=next_heat and r<hi and f<50 and
                in_window(tm,settings['start'],settings['end']) and
                in_window(datetime.fromtimestamp(stop-.001,now.tzinfo),settings['start'],settings['end']) and
                not blocked(stamp,stop,outages))
        kw=(modulation_power(f,settings.get('modulation_profile'),3.,settings.get('modulation_anchor'),
                            (stamp-now.timestamp())/60) if settings.get('modulation_profile') is not None else power_kw) if active else 0.
        args=(outdoor_at(weather,tm,outside),fire*math.exp(-(stamp-now.timestamp())/7200),
              model,(stop-stamp)/3600,tm,weather,settings)
        nr,nf=forecast_step(r,f,args[0],kw if active else 0.,*args[1:])
        if active and (nr>hi or nf>50):
            nr,nf=forecast_step(r,f,args[0],0.,*args[1:])
        r,f=nr,nf
        rows.append(dict(ts=stop,temperature=r));stamp=stop
    metrics=trajectory_metrics(now,room,rows,settings,end)
    metrics.update(assessment_until=end,scenario='coast_then_safe_next_window_recovery')
    settings['_no_morning_metrics']=metrics
    return metrics['deficit_degree_hours']>0


def morning_reservation(now, settings, context, outages, finish):
    if not context or context['morning_start'] is None:
        return None
    left=max(now.timestamp(),context['morning_start']);right=min(finish,context['morning_end'])
    elapsed=number(settings.get('morning_elapsed_minutes')) or 0
    required=max(0.,(number(settings.get('morning_min_minutes')) or 0)-elapsed)*60
    if required<=0 or right<=left:
        return None
    # Seconds matter: retain the 06:30:02–07:00 opportunity, without claiming it
    # is a completed 30 minutes. The shortfall stays explicit in diagnostics.
    if right-left<required and 'night_target' not in settings:
        return None
    duration=min(required,right-left)
    starts={left+300*i for i in range(int((right-left)/300)+1)}|{right-duration}
    if elapsed:
        starts={left}
    feasible=[a for a in starts if a>=left and a+duration<=right+.001 and
              not blocked(a,a+duration,outages)]
    if not feasible:
        # An outage can leave less than the minimum: use the largest available
        # safe portion only for the new cycle-aware (day/night) policy.
        if 'night_target' not in settings:
            return None
        pieces=[];cursor=left
        for outage in sorted(outages,key=lambda o:o['start']):
            if outage['end']<=cursor or outage['start']>=right:
                continue
            if outage['start']>cursor:pieces.append((cursor,min(right,outage['start'])))
            cursor=max(cursor,outage['end'])
        if cursor<right:pieces.append((cursor,right))
        if not pieces:return None
        a,b=max(pieces,key=lambda p:(min(required,p[1]-p[0]),p[1]))
        return (max(a,b-required),b)
    a=max(feasible)
    return a,a+duration


def attach_reserve(now, room, model, weather, settings, result, context=None):
    context=context or reserve_context(now,settings,model)
    if context is None:
        return result
    rows=result.get('_calculation_curve',result['curve'])
    hours=(context['assessment_until']-now.timestamp())/3600
    margin=uncertainty_reserve(settings,hours)
    metrics=trajectory_metrics(now,room,rows,settings,context['assessment_until'],margin or 0.)
    actual_limits=(trajectory_metrics(now,room,rows,settings,context['assessment_until'])
                   if margin else metrics)
    cycle_segments=[s for row in rows for s in row['heat_segments']
                    if context['morning_start'] is not None and
                    s['start']>=context['morning_start'] and s['end']<=context['morning_end']]
    completed=number(settings.get('confirmed_completed_minutes')) or 0.
    planned=sum((s['end']-s['start'])/60 for s in cycle_segments)
    required=(number(settings.get('morning_min_minutes')) or 0.) if settings.get('_reserve_needed') else 0.
    complete=(metrics['deficit_degree_hours']<=.00001)
    data=availability(settings.get('outage_schedule') or {},now.timestamp(),context['assessment_until'])
    heat_known=all(schedule_covered(data,s['start'],min(s['end'],context['assessment_until']))
        for row in rows for s in row['heat_segments'] if s['start']<context['assessment_until'])
    known=(heat_known and not context['next_heat_conditional'] and
           weather_coverage(weather,context['assessment_until'])['weather_complete'] and margin is not None)
    reason=(None if complete else 'Знайдений план має дефіцит; фізичну неможливість не доведено')
    cancellation=(None if planned else 'Запас достатній без ранкового нагріву' if not required else
                  'Ранковий резерв недоступний або небезпечний за поточними обмеженнями')
    result['morning_reserve']=dict(context,**metrics,morning_required_minutes=required,
        comfort_deficit=dict(actual_limits,visible_until=now.timestamp()+86400,
            outside_visible_horizon=bool(actual_limits['first_comfort_violation_at'] and
                actual_limits['first_comfort_violation_at']>=now.timestamp()+86400)),
        planned_minutes=round(planned,2),planned_kwh=round(sum(s.get('kwh',0.) for s in cycle_segments),4),
        confirmed_completed_minutes=round(completed,2),
        estimated_request_minutes=round(number(settings.get('estimated_morning_request_minutes')) or 0.,2),
        confirmed_kwh=number(settings.get('confirmed_morning_kwh')),
        no_morning_counterfactual=settings.get('_no_morning_metrics'),
        manual_completed_minutes=number(settings.get('manual_completed_minutes')) or 0.,
        minimum_unmet_minutes=round(max(0.,required-completed-planned),2),
        reservation_start=min((s['start'] for s in cycle_segments),default=None),
        reservation_end=max((s['end'] for s in cycle_segments),default=None),
        predicted_sufficient=complete,reserve_sufficient=complete if known else False if not complete else None,
        uncertainty_margin=margin,uncertainty_status='validated_horizon' if margin is not None else 'insufficient_horizon_validation',
        conditional=not known,impossibility_reason=reason,cancellation_reason=cancellation,
        calculation_horizon_hours=round(max(24.,hours),2),scenario='planned_heat_with_response_delay')
    result['morning_reserve']['comfort_24h']=trajectory_metrics(
        now,room,rows,settings,now.timestamp()+86400)
    return result


SEARCH_FIELDS=('search_policy_version','search_status','alternatives_evaluated','search_passes',
    'expanded_search_performed','early_heat_rejected_reason','terminal_reserve_checked',
    'initial_deficit_degree_hours','alternative_deficit_degree_hours','search_check_key',
    'local_search_status','local_alternatives_evaluated','local_deficit_reduction_degree_hours')


def search_input_key(now,room,floor,outside,fire,model,weather,settings):
    cycle=heating_cycle(now,settings)
    keys=('target','lower','upper','night_target','night_lower','night_upper','start','end','sleep_start',
          'sleep_end','strategy','split_comfort','morning_min_minutes','upper_margin','day_price','night_price',
          'budget_multiplier','reserve_errors','cycle_energy_budget','outage_advance_limit',
          'morning_elapsed_minutes','confirmed_completed_minutes','confirmed_morning_kwh',
          'manual_completed_minutes')
    outage=settings.get('outage_schedule') or {}
    solar=(settings.get('environment') or {}).get('solar',{})
    data=dict(version=SEARCH_POLICY_VERSION,environment_policy_version=ENVIRONMENT_POLICY_VERSION,
        cycle=cycle,room=round(room,2),floor=round(floor,1),
        outside=round(outside,1),fire=round(fire,1),model=model,weather=weather,
        settings={k:settings.get(k) for k in keys},outage={k:outage.get(k) for k in ('mode','valid','outages','slots','known_until')},
        physical_mode=(settings.get('modulation_profile') or {}).get('physical_mode'),
        equipment_version=settings.get('equipment_version'),ventilation_model=settings.get('ventilation_model'),
        ventilation_expected=settings.get('ventilation_expected'),profile=(settings.get('modulation_profile') or {}).get('bands'),anchor=settings.get('modulation_anchor'),
        actuator=settings.get('actuator'),
        environment={k:solar.get(k) for k in ('validated','coefficient','observed_gain','observed_at','risk_coefficient','control_no_sun')},
        astronomy=(settings.get('environment') or {}).get('astronomy'),
        residual=(settings.get('environment') or {}).get('residual'))
    upper=certified_upper_power(settings.get('modulation_profile'))
    if upper is not None:data['profile_upper_kw']=upper
    return hashlib.sha256(json.dumps(data,sort_keys=True,separators=(',',':')).encode()).hexdigest()


def apply_search_diagnostics(now,settings,result):
    reserve=result.get('morning_reserve')
    if not reserve:return result
    rows=result.get('_calculation_curve',result['curve'])
    cycle_end=reserve['cycle_end'];cycle_start=reserve['cycle_start']
    segments=[s for r in rows for s in r['heat_segments'] if cycle_start<=s['start']<cycle_end]
    data=availability(settings.get('outage_schedule') or {},now.timestamp(),cycle_end)
    pieces=[];cursor=max(now.timestamp(),cycle_start)
    for outage in sorted(data.get('outages',[]),key=lambda o:o['start']):
        if outage['end']<=cursor or outage['start']>=cycle_end:continue
        if outage['start']>cursor:pieces.append((cursor,min(cycle_end,outage['start'])))
        cursor=max(cursor,outage['end'])
    if cursor<cycle_end:pieces.append((cursor,cycle_end))
    last=max((b for a,b in pieces if b>a),default=None)
    constraints=[]
    for key,count in (result.get('candidate_rejections') or {}).items():
        if count:constraints.append(dict(constraint=key,evidence='candidate_rejected',count=count))
    if data.get('outages'):constraints.append(dict(constraint='outage_mask',evidence='published_intervals'))
    if settings.get('strategy')=='Тільки в дозволені години':
        constraints.append(dict(constraint='allowed_window',evidence='configured_window'))
    reserve.update({key:result.get(key) for key in SEARCH_FIELDS})
    reserve.update(end_cycle_planned_kwh=round(sum(s.get('kwh',0.) for s in segments),4),
        last_available_energy_at=last,binding_constraints=constraints,
        physical_impossibility_proven=False,
        search_limitation='Bounded beam search; candidate rejection is not proof that every feasible plan was checked')
    if reserve['deficit_degree_hours']>.00001:
        checked='виконаний' if result.get('expanded_search_performed') else 'не виконаний'
        local = {'improved':'локальний пошук скоротив дефіцит',
                 'no_improvement':'локальний пошук не знайшов кращого безпечного плану',
                 'timeout':'локальний пошук вичерпав ліміт часу',
                 'failed':'помилка локального пошуку'}.get(result.get('local_search_status'))
        reserve['impossibility_reason']=', '.join(filter(None,(
            'Знайдений план має дефіцит; розширений пошук '+checked,
            local,'фізичну неможливість не доведено')))
    return result


def _plan(now, room, floor, outside, fire_delta, model, forecast, settings, power_kw=9.0, outages=(), extensions=(), hours_override=None, split=False, energy_limit=None, forbid_morning=False, minimum_required=None, beam_width=None, forecast_context=None):
    """24h beam search in half-hour blocks; comfort dominates tariff cost."""
    hours = min(24., max(0., (forecast[-1]["ts"] - now.timestamp()) / 3600 + 1)) if forecast else 6
    if hours_override is not None:
        hours = hours_override
    count = max(1, int(hours * 2))
    width=beam_width or (48 if split else 12)
    evaluated=0;rejections=dict(upper_temperature=0,energy_limit=0)
    # The trailing two values accumulate the session buckets used by dedup.
    if forecast_context is None:
        forecast_context=ForecastContext(now,forecast,outside,model,settings)
    integrate=forecast_context.integrate
    start_stamp=now.timestamp()
    actuator=settings.get('actuator') or {};actuator_enabled=bool(actuator)
    risk_enabled=has_upper_risk(settings)
    upper_power=certified_upper_power(settings.get('modulation_profile'))
    beams = [(0., room, floor, [], [], 0., room, room, 0.,actuator.get('action'),actuator.get('changed_at',0.),0.,room,floor,0.,0.)]
    budget=settings.get('cycle_energy_budget') or {}
    def rank(value):
        excess=max(0.,value[11]-budget['remaining_kwh']) if budget else 0.
        return (round(value[8],5),round(excess,4),value[0]) if cycle_policy else (value[0],)
    night_target=max(settings["lower"], settings["target"]-.5)
    profile=settings.get('modulation_profile')
    anchor=settings.get('modulation_anchor')
    mode=(profile or {}).get('physical_mode')
    fixed_kw=(power_kw if profile is None else float(mode[-1])
              if mode in ('fixed_3','fixed_6','fixed_9') and not profile.get('validated') else None)
    reserved_exact=None
    # A new heat request must fit a complete minimum-on cycle before the
    # next hard outage/strict-window boundary. Already-running sessions are
    # instead protected by held_action(), and hard safety can always stop.
    def can_start_minimum(stamp):
        end=stamp+600
        if blocked(stamp,end,outages):
            return False
        if settings['strategy']!='Тільки в дозволені години':
            return True
        cursor=stamp
        while cursor<end-.001:
            next_at=min(end,cursor+60)
            a=datetime.fromtimestamp(cursor,now.tzinfo)
            b=datetime.fromtimestamp(next_at-.001,now.tzinfo)
            inside=in_window(a,settings['start'],settings['end']) and in_window(b,settings['start'],settings['end'])
            extra=any(cursor>=lo and next_at<=hi for lo,hi in extensions)
            if not (inside or extra):
                return False
            cursor=next_at
        return True
    minimum=number(settings.get('morning_min_minutes')) or 0
    elapsed=number(settings.get('morning_elapsed_minutes')) or 0
    cycle_policy=bool(settings.get('split_comfort') and 'night_target' in settings)
    context=settings.get('_reserve_context') or reserve_context(now,settings,model)
    needed=settings.get('_reserve_needed',True)
    if split and minimum>0 and elapsed<minimum and needed:
        exact=morning_reservation(now,settings,context,outages,now.timestamp()+count*1800)
        if not exact:
            return None
        reserved_exact=tuple(datetime.fromtimestamp(t,now.tzinfo) for t in exact)
    time_inputs={}
    deadlines={}
    minimum_can_start={}

    def inputs(tm):
        key=tm,tm.fold
        values=time_inputs.get(key)
        if values is None:
            stamp,elapsed_minutes,out,gains=forecast_context.at(tm)
            wanted,limit_low,limit_high,zone=comfort_at(tm,settings)
            limit_low+=min(settings.get('_reserve_margin',0.),max(0.,wanted-limit_low)/2)
            limit_high=safe_upper(limit_high,zone,settings)
            middle=split and in_window(tm,"01:00","05:00")
            morning=split and in_window(tm,"05:00","07:00")
            session_time=datetime.fromtimestamp(stamp,now.tzinfo) if split else tm
            evening=split and in_window(session_time,"23:00","01:00")
            morning_bucket=split and in_window(session_time,"05:00","07:00")
            desired=night_target if middle else settings["target"]
            if 'night_target' in settings:
                desired=(limit_low+.05 if zone=='Сон' else
                         min(limit_high-.1,wanted+.5*(limit_high-wanted)))
            fire_gain=model['fire_gain']*max(0.,fire_delta*math.exp(-elapsed_minutes/120))
            minutes=tm.hour*60+tm.minute
            values=(stamp,elapsed_minutes,out,gains,limit_low,limit_high,desired,middle,
                    morning,evening,morning_bucket,fire_gain,in_window(tm,settings["start"],settings["end"]),
                    (420-minutes)%1440)
            time_inputs[key]=values
        return values

    price_multiplier=settings.get("budget_multiplier",1.)
    for block in range(count):
        when = elapsed_at(now,1800*block)
        allowed = in_window(when, settings["start"], settings["end"])
        tariff_night = in_window(when, "23:00", "07:00")
        steps = [elapsed_at(when,300*j) for j in range(6)]
        substeps=[]
        for tm in steps:
            end=elapsed_at(tm,300)
            cuts={tm,end}
            for key in ('start','end','sleep_start','sleep_end'):
                value=settings.get(key)
                if value:
                    boundary=tm.replace(hour=int(value[:2]),minute=int(value[3:5]),second=0,microsecond=0)
                    if tm<boundary<end:
                        cuts.add(boundary)
            if reserved_exact:
                cuts.update(t for t in reserved_exact if tm<t<end)
            ordered=sorted(cuts)
            substeps.extend(zip(ordered,ordered[1:]))
        masks = [];hard_masks=[]
        for tm,end in substeps:
            normal = in_window(tm, settings["start"], settings["end"]) and in_window(end-timedelta(microseconds=1), settings["start"], settings["end"])
            stamp,stop=tm.timestamp(),end.timestamp()
            extension = any(stamp>=a and stop<=b for a,b in extensions)
            safe=not blocked(stamp,stop,outages) and (
                normal or extension or settings['strategy']!='Тільки в дозволені години')
            hard_masks.append(safe)
            masks.append(safe and not (forbid_morning and in_window(tm,"05:00","07:00")))
        choices = [False, True] if any(masks) else [False]
        when_stamp=when.timestamp()
        block_end=when_stamp+1800
        price = settings["night_price"] if tariff_night else settings["day_price"]
        # Unknown price: optimise relative energy, never fabricate a cost.
        price = (price if price and price > 0 else (.5 if tariff_night else 1.))*price_multiplier
        other_block=split and not (in_window(when,"23:00","01:00") or in_window(when,"05:00","07:00"))
        preference=split or allowed or settings["strategy"]=="Комфорт протягом доби"
        late_heat=split and 'night_target' in settings
        prepared=[(begin,finish,inputs(begin),finish.timestamp()) for begin,finish in substeps]
        candidates = []
        for score, r, f, decisions, curve, energy, low, high, deficit, last, changed_at, cycle_used, risk_room, risk_floor, prior_evening, prior_morning in beams:
            had_heat=any(decisions)
            for heat in choices:
                evaluated+=1
                nr, nf, penalty = r, f, 0.
                ur,uf=risk_room,risk_floor
                command,command_at=last,changed_at
                shortage=deficit; fine=[]
                used, outside_minutes, unsafe = 0., 0., False
                cycle_added=0.
                heat_segments = []
                evening_kwh,morning_kwh=prior_evening,prior_morning
                for j,(begin,finish,begin_inputs,end_stamp) in enumerate(prepared):
                    pieces=((begin,finish,begin_inputs,end_stamp),)
                    if actuator_enabled:
                        deadline_key=command_at,command=='heat'
                        deadline=deadlines.get(deadline_key)
                        if deadline is None:
                            deadline=datetime.fromtimestamp((number(command_at) or 0)+
                                                          (600 if command=='heat' else 900),now.tzinfo)
                            deadlines[deadline_key]=deadline
                        if begin<deadline<finish:
                            deadline_inputs=inputs(deadline)
                            pieces=((begin,deadline,begin_inputs,deadline_inputs[0]),
                                    (deadline,finish,deadline_inputs,end_stamp))
                    for tm,end,values,stop_stamp in pieces:
                        (stamp,elapsed_minutes,out,gains,limit_low,limit_high,desired,middle,
                         morning,evening,morning_bucket,fire_gain,inside,until_morning)=values
                        dt=(stop_stamp-stamp)/3600
                        reserved_here=reserved_exact and reserved_exact[0]<=tm and end<=reserved_exact[1]
                        active = (heat or reserved_here) and masks[j]
                        # Reject new short pulses near the hard cut; continued
                        # heating may finish normally until the safe boundary.
                        if active:
                            if actuator_enabled:
                                continuing=command=='heat'
                            else:
                                prior_end=(heat_segments[-1]['end'] if heat_segments else
                                           curve[-1]['heat_segments'][-1]['end']
                                           if curve and curve[-1]['heat_segments'] else None)
                                continuing=prior_end is not None and abs(prior_end-stamp)<.01
                            if not continuing:
                                if stamp not in minimum_can_start:
                                    minimum_can_start[stamp]=can_start_minimum(stamp)
                                active=minimum_can_start[stamp]
                        if actuator_enabled:
                            stop=(not hard_masks[j] or nr>=limit_high or
                                  actuator.get('safety_stop') and stamp<start_stamp+300)
                            requested='heat' if active and not stop else 'off'
                            if requested!=command:
                                applied=held_action(command,command_at,stamp,requested,urgent=stop)
                                if applied!=command:command,command_at=applied,stamp
                            active=command=='heat' and hard_masks[j]
                        if active and nr >= limit_high:
                            unsafe = True
                            break
                        effective_kw=(fixed_kw if fixed_kw is not None else
                                      modulation_power(nf,profile,3.,anchor,elapsed_minutes)) if active else 0.
                        delivered_kw=effective_kw
                        nr,nf=integrate(nr,nf,out,delivered_kw,fire_gain,dt,gains[0])
                        risk_kw=max(delivered_kw,upper_power) if active and upper_power is not None else delivered_kw
                        ur,uf=(integrate(ur,uf,out,risk_kw,fire_gain,dt,gains[1],upper=True)
                               if risk_enabled else (nr,nf))
                        # A session remains unsafe if its stored heat overshoots
                        # later, after the boiler has stopped (including daytime).
                        upper_risk=max(nr,ur)
                        if ((upper_risk>max(room,limit_high)+.01 and (active or had_heat or heat_segments))
                                or active and upper_power is not None and uf>50):
                            unsafe=True
                            break
                        shortage+=max(0.,limit_low-nr)*dt
                        fine.append(dict(ts=stop_stamp,temperature=nr,upper_temperature=upper_risk,**{'return':nf}))
                        penalty += (max(0., limit_low-nr)**2 +
                                    max(0., nr-limit_high)**2)*500*dt*12
                        if preference:
                            penalty += max(0.,desired-nr)**2*20*dt*12
                        if middle:
                            penalty += max(0.,nr-settings["target"])**2*30*dt*12
                        if morning:
                            # Prefer heat close to the morning, without inventing a floor-surface reading.
                            penalty += max(0.,settings["target"]-nr)**2*20*dt*12
                        if active:
                            kwh=effective_kw*dt
                            used += kwh
                            if evening:
                                evening_kwh+=kwh
                            if morning_bucket:
                                morning_kwh+=kwh
                            if budget:
                                cycle_added+=effective_kw*max(0.,min(stop_stamp,budget['end'])-
                                    max(stamp,budget['start']))/3600
                            heat_segments.append({"start": stamp, "end": stop_stamp,
                                                  'power_kw':round(effective_kw,3),'kwh':kwh})
                            if not inside:
                                outside_minutes += dt*60
                            if nr > limit_high or nf > 50:
                                unsafe = True
                                break
                            if late_heat:
                                # Store daytime heat as late as feasible in the cheap window.
                                # Earlier heat remains possible for the full day/response reserve.
                                penalty += effective_kw*dt * min(1.,until_morning/480) * 20
                    if unsafe:
                        break
                if unsafe:
                    rejections['upper_temperature']+=1
                    continue
                if energy_limit is not None and energy+used>energy_limit+.001:
                    rejections['energy_limit']+=1
                    continue
                if used and other_block:
                    penalty += used*.8

                if heat and not allowed and settings["strategy"] == "Переважно вночі":
                    penalty += used * 3
                # An out-of-window exception has a high cost; comfort can justify it.
                penalty += outside_minutes * 2
                decision_heat=bool(heat_segments)
                switching = .25 if decisions and decisions[-1] != decision_heat else 0.
                goal = max(0., settings["target"]-nr)**2 * .03
                candidates.append((score+penalty+used*price+switching+goal, nr, nf,
                                   decisions+[decision_heat], curve+[{"ts":block_end,
                                   "temperature":round(nr,2),"return":round(nf,2),"heat":bool(heat_segments),
                                   "kwh":round(used,4),"heat_segments":heat_segments,
                                   "outside_minutes":outside_minutes,"_fine":fine}],
                                   energy+used, min(low,min(t["temperature"] for t in fine)), max(high,max(t["temperature"] for t in fine)),shortage,command,command_at,cycle_used+cycle_added,ur,uf,evening_kwh,morning_kwh))
        if not candidates:
            return None
        candidates.sort(key=rank)
        # Keep diverse room/floor states instead of 12 nearly identical paths.
        seen, beams = set(), []
        for candidate in candidates:
            key=(round(candidate[1]*10), round(candidate[2]*2), candidate[3][-1],round(candidate[12]*10))
            if actuator_enabled:
                key+=(candidate[9],round(max(0.,candidate[10]+(600 if candidate[9]=='heat' else 900)-when_stamp-1800)/60))
            if split:
                key+=(candidate[14]>0,candidate[15]>0,round(candidate[5]*2))
            if key not in seen:
                seen.add(key)
                beams.append(candidate)
            if len(beams) == width:
                break
    minimum=number(settings.get("morning_min_minutes")) or 0
    elapsed=number(settings.get("morning_elapsed_minutes")) or 0
    if minimum>0 and not forbid_morning and not cycle_policy:
        eligible=[b for b in beams if morning_runs_valid(b[4],now,minimum,elapsed)]
        if not eligible:
            if split:
                return None
            # Prefer omitting an infeasible short session to promising warm floors.
            return _plan(now,room,floor,outside,fire_delta,model,forecast,settings,power_kw,
                         outages,extensions,hours_override,split,energy_limit,forbid_morning=True,
                         forecast_context=forecast_context)
        beams=eligible
    if split:
        # Require both sessions only when both complete windows lie in this forecast.
        cycle=now.replace(hour=23,minute=0,second=0,microsecond=0)
        if now>cycle:
            cycle+=timedelta(days=1)
        end=now+timedelta(hours=count/2)
        pair_complete=cycle+timedelta(hours=8)<=end
        if pair_complete and energy_limit is not None and energy_limit>=1.5 and 'night_target' not in settings:
            beams=[b for b in beams if b[14]>0 and b[15]>0]
        if not beams:
            return None
    # Apply the no-worse-comfort constraint to all surviving candidates before
    # choosing by cost. Rejecting only the cheapest split can discard a valid
    # morning run present in another beam (observed at 21:05 on 2026-10-12).
    if minimum_required is not None:
        beams=[b for b in beams if round(b[6],2)>=minimum_required]
        if not beams:
            return None
    best = min(beams, key=rank)
    return {"heat":bool(best[4][0]["heat_segments"] and best[4][0]["heat_segments"][0]["start"] == now.timestamp()),"curve":best[4],"energy":round(best[5],2),
            "minimum":round(best[6],2),"maximum":round(best[7],2),
            "horizon_hours":round(count/2,1),"validated":bool(model.get("validated")),
            "morning_priority_applied":bool(split and 'night_target' in settings),
            "search_score":best[0],"search_deficit":best[8],"alternatives_evaluated":evaluated,
            "beam_width":width,"candidate_rejections":rejections}


def weather_coverage(forecast, finish):
    """Allow one hourly interval of held temperature at the end of the horizon."""
    interval=3600
    until=max((row['ts']+interval for row in forecast),default=None)
    return dict(weather_known_until=until,
                weather_complete=until is not None and until>=finish-interval,
                weather_missing_minutes=None if until is None else round(max(0.,finish-until)/60,1))


def plan(now, room, floor, outside, fire_delta, model, forecast, settings, power_kw=9.0, *, forecast_context=None):
    """Prefer the configured window; move blocked heat earlier, never into outages.

    Only a comfort deficit caused by a planned outage can enable a bounded
    exception to the strict window. No outage data means the ordinary plan.
    """
    data = availability(settings.get("outage_schedule") or {},now.timestamp(),now.timestamp()+86400)
    outages = data.get("outages", []) if data.get("valid") else []
    # Always show the next day; do not confuse an unpublished outage schedule
    # with a confirmed uninterrupted supply. Live grid checks still gate commands.
    settings=dict(settings)
    context=reserve_context(now,settings,model)
    priority=bool(settings.get('split_comfort') and 'night_target' in settings)
    horizon=max(24.,math.ceil((context['assessment_until']-now.timestamp())/1800)/2) if priority and context else 24.
    settings['_reserve_context']=context
    settings['_reserve_margin']=uncertainty_reserve(settings,horizon) or 0.
    settings['_reserve_needed']=bool(context and coast_reserve_needed(now,room,floor,outside,fire_delta,model,forecast,settings,context,power_kw))

    schedule_until = number(data.get("known_until")) if data.get("valid") else None
    coverage = weather_coverage(forecast,now.timestamp()+86400)
    weather_until = coverage['weather_known_until']
    if forecast_context is None:
        forecast_context=ForecastContext(now,forecast,outside,model,settings)
    args = (now, room, floor, outside, fire_delta, model, forecast, settings, power_kw)
    priority=bool(settings.get('split_comfort') and 'night_target' in settings)
    def search(outages=(), extensions=()):
        candidate=_plan(*args,outages=outages,extensions=extensions,
                        hours_override=horizon,split=priority,forecast_context=forecast_context)
        if candidate is None:
            candidate=_plan(*args,outages=outages,extensions=extensions,hours_override=horizon,forecast_context=forecast_context)
        def deficit(c):
            return trajectory_metrics(now,room,c['curve'],settings,
                context['assessment_until'] if context else now.timestamp()+86400,
                settings.get('_reserve_margin',0.))['deficit_degree_hours']
        initial=selected_deficit=deficit(candidate)
        evidence=[dict(beam_width=candidate['beam_width'],deficit_degree_hours=initial,
                       alternatives_evaluated=candidate['alternatives_evaluated'])]
        counts=candidate['alternatives_evaluated']
        if priority and initial>.00001:
            # No prefix pruning: every earlier allowed interval remains available.
            # A wider pass is bounded and runs only when the first pass has deficit.
            for split in (True,False):
                alternative=_plan(*args,outages=outages,extensions=extensions,hours_override=horizon,
                                  split=split,beam_width=192,forecast_context=forecast_context)
                if alternative is None:
                    continue
                alternative_deficit=deficit(alternative)
                counts+=alternative['alternatives_evaluated']
                evidence.append(dict(beam_width=192,split=split,deficit_degree_hours=alternative_deficit,
                                     alternatives_evaluated=alternative['alternatives_evaluated']))
                if (alternative_deficit,alternative['search_score'])<(selected_deficit,candidate['search_score']):
                    candidate=alternative
                    selected_deficit=alternative_deficit
                if selected_deficit<=.00001:
                    break
        candidate.update(search_policy_version=SEARCH_POLICY_VERSION,
            search_status='feasible_found' if selected_deficit<=.00001 else 'deficit_after_expanded_search' if len(evidence)>1 else 'deficit_unexpanded',
            alternatives_evaluated=counts,search_passes=evidence,expanded_search_performed=len(evidence)>1,
            early_heat_rejected_reason='Лише перевірки безпеки; ранній нагрів має м’який пріоритет',
            terminal_reserve_checked=bool(context),initial_deficit_degree_hours=initial,
            alternative_deficit_degree_hours=min((e['deficit_degree_hours'] for e in evidence[1:]),default=None),
            search_check_key=search_input_key(now,room,floor,outside,fire_delta,model,forecast,settings))
        return candidate
    ordinary = search()
    result = search(outages=outages) if outages else ordinary
    extensions = []
    # A strict-window exception is considered only when scheduled blackouts
    # actually remove heat from the ordinary plan and worsen the minimum.
    if (outages and settings["strategy"] == "Тільки в дозволені години" and
            result["minimum"] < settings["lower"] and
            result["minimum"] < ordinary["minimum"]-.01):
        for outage in outages:
            lost = sum((s["end"]-s["start"])/60 for row in ordinary["curve"]
                       for s in row["heat_segments"] if blocked(s["start"],s["end"],[outage]))
            remaining = min(lost, settings.get("outage_advance_limit",180))
            end = outage["start"]
            # Search backwards for available 5-minute segments, up to six hours.
            cursor = end
            while remaining > 0 and cursor > max(now.timestamp(), end-6*3600):
                start = max(now.timestamp(), cursor-300)
                tm = datetime.fromtimestamp(start, now.tzinfo)
                if (cursor-start == 300 and covered(start,cursor,data.get("slots",[]))
                        and not blocked(start,cursor,outages)
                        and not in_window(tm,settings["start"],settings["end"])):
                    extensions.append((start,cursor))
                    remaining -= 5
                cursor = start
        if extensions:
            merged=[]
            for a,b in sorted(extensions):
                if merged and a <= merged[-1][1]:
                    merged[-1]=(merged[-1][0],max(b,merged[-1][1]))
                else:
                    merged.append((a,b))
            extensions=merged
            proposed = search(outages=outages, extensions=extensions)
            if proposed["minimum"] > result["minimum"]+.01:
                result = proposed
    split_applied=result.get('morning_priority_applied',False)
    profile_deficit=any(r['temperature']<comfort_at(datetime.fromtimestamp(r['ts']-.001,now.tzinfo),settings)[1]
                        for r in result['curve'])
    # The ordinary half-hour beam may omit a short run to enforce min30.
    # Still try the reserved 5-minute-grid morning slot if comfort is short.
    if not priority and settings.get("split_comfort") and (result["energy"]>=1.5 or
            'night_target' in settings and profile_deficit):
        use_extensions=extensions if any(r["outside_minutes"]>0 for r in result["curve"]) else ()
        daynight='night_target' in settings
        candidate=_plan(*args,outages=outages,extensions=use_extensions,hours_override=horizon,
                        split=True,energy_limit=None if daynight else result["energy"]+.01,
                        minimum_required=None if daynight else result["minimum"]-.05,
                        forecast_context=forecast_context)
        # Redistribute the baseline requirement; never spend extra to force a warm-floor slot.
        if (candidate is not None and (daynight or
                candidate["minimum"]>=result["minimum"]-.05 and
                candidate["maximum"]<=max(settings["upper"],result["maximum"])+.01)):
            result=candidate
            split_applied=True
    result['_calculation_curve']=result['curve']
    attach_reserve(now,room,model,forecast,settings,result,context)
    result['curve']=[r for r in result['curve'] if r['ts']<=now.timestamp()+86400+.001]
    result['energy']=round(sum(r['kwh'] for r in result['curve']),2)
    result['minimum']=round(min([room]+[p['temperature'] for r in result['curve'] for p in r.get('_fine',[r])]),2)
    result['maximum']=round(max([room]+[p['temperature'] for r in result['curve'] for p in r.get('_fine',[r])]),2)
    result['horizon_hours']=24.
    segments = [s for row in result["curve"] for s in row["heat_segments"]]
    session_kwh={"evening":0.,"morning":0.,"other":0.}
    for seg in segments:
        tm=datetime.fromtimestamp(seg["start"],now.tzinfo)
        bucket="evening" if in_window(tm,"23:00","01:00") else "morning" if in_window(tm,"05:00","07:00") else "other"
        session_kwh[bucket]+=seg.get('kwh',(seg["end"]-seg["start"])*power_kw/3600)
    result.update(morning_min_minutes=number(settings.get("morning_min_minutes")) or 0,
                  morning_runs_minutes=[round((b-a)/60,1) for a,b in morning_runs(result["curve"],now.tzinfo)],
                  split_comfort_applied=split_applied,
                  session_kwh={k:round(v,3) for k,v in session_kwh.items()},
                  night_air_target=(settings['night_lower']+.05 if priority else
                                    max(settings["lower"],settings["target"]-.5)),
                  split_reason=("Нагрів якомога ближче до останнього доступного ранку; раніше — для достатнього запасу на день"
                                if split_applied and 'night_target' in settings else
                                "Вечірній і ранковий нагрів у межах енергопотреби" if split_applied else
                                "Запас тепла не потребує примусового ранкового нагріву" if result['energy']<1.5 and not priority else
                                "Не знайдено безпечний ранковий сеанс: враховано температурні межі й доступні години"))
    shifted = bool(outages and segments != [s for r in ordinary["curve"] for s in r["heat_segments"]])
    next_outage = next((o for o in outages if o["end"] > now.timestamp()), None)
    preheat = bool(result["heat"] and next_outage and
                   0 < next_outage["start"]-now.timestamp() <= 6*3600 and shifted)
    conditional_minutes = 0.
    for row in result["curve"]:
        wanted,lo,hi,zone=comfort_at(datetime.fromtimestamp(row['ts']-.001,now.tzinfo),settings)
        row.update(target=wanted,lower=lo,upper=hi,comfort_zone=zone)
        row["schedule_known"] = schedule_covered(data,row['ts']-1800,row['ts'])
        row["weather_known"] = weather_until is not None and row["ts"] <= weather_until
        for segment in row["heat_segments"]:
            segment["conditional"] = not schedule_covered(data,segment['start'],segment['end'])
            if segment["conditional"]:
                conditional_minutes += (segment["end"]-segment["start"])/60
    # Preserve the exact exception intervals already authorised by V1's
    # blackout search. Replay must not invent or broaden these intervals.
    result['approved_window_extensions'] = [list(pair) for pair in extensions] if (
        data.get('mode') == 'scheduled' and any(r['outside_minutes'] > 0 for r in result['curve'])) else []
    result['extension_outages'] = [dict(start=o['start'],end=o['end']) for o in outages] if result['approved_window_extensions'] else []
    result.update(schedule_known_until=schedule_until, **coverage,
                  conditional_heat_minutes=round(conditional_minutes,1),
                  schedule_complete=schedule_covered(data,now.timestamp(),now.timestamp()+86400),
                  outage_mode=data.get('mode','scheduled' if data.get('valid') else 'unknown'),
                  outage_shifted=shifted, outage_preheat=preheat,
                  outside_minutes=sum(r["outside_minutes"] for r in result["curve"]),
                  outage_status=data.get("reason","Графік не підключений"),
                  comfort_shortfall=any(r['temperature']<r['lower'] for r in result['curve']),
                  heat_segments=segments, schedule_updated=data.get("updated"))
    result['_calculation_curve']=[dict(r) for r in result['_calculation_curve']]
    result['curve']=[{k:v for k,v in r.items() if k!='_fine'} for r in result['curve']]
    apply_search_diagnostics(now,settings,result)
    return result


def advisory_outlook_48(now, room, floor, outside, fire, model, weather, settings, forecast):
    """48 elapsed hours, read-only scenario on the existing model/plan.

    Keep the complete operational trace. Beyond it repeat only already
    calculated first-day heat intervals, as a conditional scenario, never
    as new commands. Known interruptions veto hypothetical heat. Unknown
    weather/supply remains marked, not silently certified.
    """
    start=now.timestamp(); finish=start+172800
    rows=forecast.get('_calculation_curve') or forecast.get('curve') or []
    if not rows or not weather:
        return dict(status='unavailable',reason='exact_plan_and_weather_required',curve=[])
    context=ForecastContext(now,weather,outside,model,settings)
    data=availability(settings.get('outage_schedule') or {},start,finish)
    outages=data.get('outages',[]) if data.get('valid') else []
    # Unlike max(timestamp), this detects holes inside hourly weather coverage.
    known=start
    for ts in sorted(set(row['ts'] for row in weather if number(row.get('temperature')) is not None)):
        if ts>known+.001:
            break
        known=max(known,ts+3600)
    curve=[];cursor=start;r,f=room,floor
    for row in rows:
        end=number(row.get('ts'))
        if end is None or end>finish:
            break
        fine=row.get('_fine') or []
        if (not fine or abs(fine[-1]['ts']-end)>.001
                or any(not 0<b-a<=301 for a,b in zip([cursor]+[p['ts'] for p in fine[:-1]],
                                                     [p['ts'] for p in fine]))):
            return dict(status='unavailable',reason='incomplete_operational_trace',curve=[])
        r,f=number(fine[-1].get('temperature')),number(fine[-1].get('return'))
        if r is None or f is None:
            return dict(status='unavailable',reason='missing_terminal_state',curve=[])
        target,lo,hi,_=comfort_at(datetime.fromtimestamp(end-.001,now.tzinfo),settings)
        curve.append(dict(ts=end,temperature=round(r,2),lower=lo,upper=hi,target=target,
                          basis='calculated_plan',weather_known=end<=known,
                          schedule_known=schedule_covered(data,cursor,end)))
        cursor=end
    planned_until=cursor
    if cursor<start+86400-.001:
        return dict(status='unavailable',reason='less_than_24h_calculated_trace',curve=[])
    pattern=[]
    # Never assume a 24-hour elapsed shift represents the same local tariff
    # window across a clock change. In that case use coast-only extension.
    clock_change=any(datetime.fromtimestamp(start+h*3600,now.tzinfo).utcoffset()!=now.utcoffset()
                     for h in range(49))
    if not clock_change:
        for row in rows:
            for seg in row.get('heat_segments',[]):
                a,b,kw=(number(seg.get(k)) for k in ('start','end','power_kw'))
                if None in (a,b,kw) or not 0<=kw<=10.5 or b<=a:
                    return dict(status='unavailable',reason='invalid_heat_pattern',curve=[])
                a=max(start,a);b=min(start+86400,b)
                if b>a:
                    a=max(cursor,a+86400);b=min(finish,b+86400)
                    if b>a:pattern.append((a,b,kw))
    pattern.sort()
    if any(a[1]>b[0]+.001 for a,b in zip(pattern,pattern[1:])):
        return dict(status='unavailable',reason='overlapping_heat_pattern',curve=[])
    # 15-minute maximum integration step, 60-minute output; use exact heat,
    # outage and weather boundaries. No beam search or new daily schedule.
    cuts={cursor,finish}
    cuts.update(start+i*900 for i in range(1,192) if cursor<start+i*900<finish)
    cuts.update(t for a,b,_ in pattern for t in (a,b) if cursor<t<finish)
    cuts.update(t for row in outages for t in (row['start'],row['end']) if cursor<t<finish)
    cuts.update(row['ts'] for row in weather if cursor<row['ts']<finish)
    cuts=sorted(cuts)
    assumed=0.;unknown_heat=0.;skipped=0.;index=0;report_start=cursor
    for a,b in zip(cuts,cuts[1:]):
        while index<len(pattern) and pattern[index][1]<=a+.001:index+=1
        kw=pattern[index][2] if index<len(pattern) and pattern[index][0]<=a+.001 and b<=pattern[index][1]+.001 else 0.
        when=datetime.fromtimestamp(a,now.tzinfo)
        _,_,hi,zone=comfort_at(when,settings)
        planned_kw=kw
        if kw and (blocked(a,b,outages) or r>=safe_upper(hi,zone,settings) or f>=50):
            kw=0.
        _,elapsed,out,gains=context.at(when)
        fire_gain=model['fire_gain']*max(0.,fire*math.exp(-elapsed/120))
        dt=(b-a)/3600
        nr,nf=context.integrate(r,f,out,kw,fire_gain,dt,gains[0])
        if kw and (nf>50 or nr>safe_upper(hi,zone,settings)):
            kw=0.;nr,nf=context.integrate(r,f,out,0.,fire_gain,dt,gains[0])
        r,f=nr,nf
        if not math.isfinite(r) or not math.isfinite(f):
            return dict(status='unavailable',reason='nonfinite_projection',curve=[])
        assumed+=kw*dt;skipped+=(planned_kw-kw)*dt
        if not schedule_covered(data,a,b):
            unknown_heat+=kw*dt
        if abs((b-start)%3600)<.001 or b==finish:
            target,lo,hi,_=comfort_at(datetime.fromtimestamp(b-.001,now.tzinfo),settings)
            curve.append(dict(ts=b,temperature=round(r,2),lower=lo,upper=hi,target=target,
                              basis='conditional_repeat' if not clock_change else 'conditional_coast',
                              weather_known=b<=known,schedule_known=schedule_covered(data,report_start,b)))
            report_start=b
    return dict(status='conditional',horizon_hours=48.,calculated_until=planned_until,
                weather_contiguous_until=known,weather_complete=known>=finish,
                schedule_complete=schedule_covered(data,start,finish),
                assumed_tail_kwh=round(assumed,3),unknown_supply_tail_kwh=round(unknown_heat,3),
                omitted_tail_kwh=round(skipped,3),clock_change=clock_change,
                assumption='repeat_first_day_heat_if_available_no_control_commands',
                curve=curve)


def solve(rows, outputs, prior):
    size=len(prior)
    a=[[sum(x[i]*x[j] for x in rows)+(1.0 if i==j else 0.) for j in range(size)] for i in range(size)]
    b=[sum(x[i]*y for x,y in zip(rows,outputs))+prior[i] for i in range(size)]
    for i in range(size):
        pivot=max(range(i,size),key=lambda j:abs(a[j][i]))
        a[i],a[pivot],b[i],b[pivot]=a[pivot],a[i],b[pivot],b[i]
        if abs(a[i][i]) < 1e-9:
            return prior
        div=a[i][i]
        a[i]=[v/div for v in a[i]]
        b[i]/=div
        for j in range(size):
            if j != i:
                factor=a[j][i]
                a[j]=[v-factor*w for v,w in zip(a[j],a[i])]
                b[j]-=factor*b[i]
    return b

def transitions(samples):
    result=[]
    last_end=0.
    probe_start=datetime.fromisoformat('2026-10-07T00:00:00+03:00').timestamp()
    for a,b in zip(samples,samples[1:]):
        start,finish=number(a.get('ts')),number(b.get('ts'))
        if start is None or finish is None or start<probe_start or start<last_end:
            continue
        dt=(finish-start)/3600
        if not (3/60 <= dt <= 7/60) or not a.get("quality") or not b.get("quality"):
            continue
        if any(number(a.get(k)) is None or number(b.get(k)) is None for k in ("room","return","outside","power")):
            continue
        if any(r.get('room_source_disagreement') or r.get('room_sources_suspect') or r.get('power_estimated') or str(r.get('power_source','balance')).startswith('fallback') or
               r.get('fire_valid') is False or number(r.get('fire',0)) is None or r.get('fire',0)>2 or
               r.get('external_gain',0)>.1 or abs(r.get('residual_gain',0))>.1 for r in (a,b)):
            continue
        if a.get('room_reported_at') is not None and b.get('room_reported_at') is not None and b['room_reported_at']<=a['room_reported_at']:
            continue
        if any(r.get('environment_version',0)>=6 and
               (not r.get('astronomy_valid') or number(r.get('room_reported_at')) is None or not 0<=r['ts']-r['room_reported_at']<=300)
               for r in (a,b)):
            continue
        # Daytime gains and stagnant pipes cannot identify floor transfer/loss.
        # Keep the records for energy, modulation and external-gain learning.
        if a.get('environment_version') and (a.get('grid')!='on' or b.get('grid')!='on' or
                a.get('daylight_weight',0.)>.1 and a['power']<1.5):
            continue
        if b.get("measurement_version")==3:
            if a.get("measurement_version")!=3 or number(b.get("interval_power")) is None:
                continue
            a = dict(a, power=b["interval_power"])
        if not 0<=a['power']<=10.5:
            continue
        # Drop sensor jumps and uncertain wood/solar contributions from thermal identification.
        if abs(b["room"]-a["room"]) > .6 or abs(b["return"]-a["return"]) > 5 or a.get("fire",0)>2:
            continue
        result.append((a,b,dt))
        last_end=finish
    return result

def error(model, pairs):
    errors=[]
    for a,b,dt in pairs:
        room,_=step(a["room"],a["return"],a["outside"],a["power"],a.get("fire",0),model,dt)
        errors.append(abs(room-b["room"]))
    return sum(errors)/len(errors) if errors else None

def learning_bounds(value,fraction=.05):
    """Cumulative local-day cap; a zero baseline stays zero."""
    return value*(1-fraction),value*(1+fraction)


def learning_change_allowed(value,baseline):
    if isinstance(value,bool) or number(value) is None or number(baseline) is None:
        return False
    if baseline==0:
        return value==0
    low,high=learning_bounds(baseline)
    # Eight ULPs of the baseline cover multiplication/subtraction roundoff;
    # for floor_air=.044 this is ~5.6e-17, not an extra learning allowance.
    tolerance=8*math.ulp(abs(baseline))
    return low-tolerance<=value<=high+tolerance


def validate_candidate(model, candidate, samples, baseline=None, diagnostics=None):
    pairs=transitions(samples)
    holdout=pairs[int(len(pairs)*.75):]
    if diagnostics is not None:
        diagnostics.update(train_count=len(pairs)-len(holdout),holdout_count=len(holdout),
                           old_holdout_error=None,new_holdout_error=None)
    if len(pairs)<96 or sum(a["power"]>1.5 for a,_,_ in pairs)<12:
        return False,"Недостатньо придатних даних нагріву та охолодження",None
    if max(a["return"] for a,_,_ in pairs)-min(a["return"] for a,_,_ in pairs)<3:
        return False,"Недостатня зміна обратки для ідентифікації",None
    for key,(lo,hi) in BOUNDS.items():
        value=number(candidate.get(key))
        if isinstance(candidate.get(key),bool) or not isinstance(candidate.get(key),(int,float)) or value is None or not lo<=value<=hi:
            return False,f"Параметр {key} поза допустимими межами",None
        old=model[key]
        if not learning_change_allowed(value,old) or not learning_change_allowed(value,(baseline or model)[key]):
            return False,f"Завелика добова зміна {key}",None
    old_error,new_error=error(model,holdout),error(candidate,holdout)
    if diagnostics is not None:
        diagnostics.update(old_holdout_error=old_error,new_holdout_error=new_error)
    if old_error is None or old_error<=0 or new_error is None or new_error>old_error*.98 or new_error>.25:
        return False,"Немає підтвердженого поліпшення на відкладених даних",new_error
    return True,"Кандидат пройшов перевірку; потрібна перевірка довгого прогнозу",new_error

def learn(model, samples, baseline=None):
    pairs=transitions(samples)
    train=pairs[:int(len(pairs)*.75)]
    if not train:
        return None
    rows=[[a["return"]-a["room"], -(a["room"]-a["outside"])] for a,_,_ in train]
    y=[(b["room"]-a["room"])/dt for a,b,dt in train]
    coefficients=solve(rows,y,[model["floor_air"],model["room_loss"]])
    candidate=dict(model)
    for k,value in zip(("floor_air","room_loss"),coefficients):
        lo,hi=BOUNDS[k]
        low,high=learning_bounds(model[k],.2);day_low,day_high=learning_bounds((baseline or model)[k])
        value=model[k]+.2*(max(lo,low,min(hi,high,value))-model[k])
        candidate[k]=max(lo,day_low,min(hi,day_high,value))
    # The return sensor is a proxy; identify its response only during actual boiler operation.
    heated=[pair for pair in train if pair[0]["power"]>1.5]
    if len(heated)>=12:
        rows=[[a["power"], -(a["return"]-a["room"])] for a,_,_ in heated]
        y=[(b["return"]-a["return"])/dt for a,b,dt in heated]
        coefficients=solve(rows,y,[model["heat_gain"],model["floor_decay"]])
        for k,value in zip(("heat_gain","floor_decay"),coefficients):
            lo,hi=BOUNDS[k]
            low,high=learning_bounds(model[k],.2);day_low,day_high=learning_bounds((baseline or model)[k])
            value=model[k]+.2*(max(lo,low,min(hi,high,value))-model[k])
            candidate[k]=max(lo,day_low,min(hi,day_high,value))
    return candidate

def decide(mode, armed, allow_actuation, room, probe_ok, grid, thermostat_ok, power_ok,
           model_ready, demand, settings_valid=True):
    if not allow_actuation or not armed or mode == "Спостереження":
        return "observe"
    if mode == "Вимкнено":
        return "release"
    if grid != "on":
        return "off"
    if not settings_valid or room is None or not probe_ok or not thermostat_ok or not power_ok:
        return "fallback"
    if mode == "Штатний auto":
        return "fallback"
    if not model_ready:
        return "fallback"
    return "heat" if demand else "off"


class HeatingPowerEstimator:
    """Time-weight signed balance before rejecting sub-2kW residuals.

    No nominal power substitution and no energy backfill. A short disturbance
    cannot establish a heating session; missing data immediately clears it.
    """
    def __init__(self, window=20., confirm=30., threshold=2000.):
        self.window, self.confirm, self.threshold = window, confirm, threshold
        self.points = []
        self.since = None
        self.average = None

    def update(self, stamp, value):
        value = number(value)
        if value is None:
            self.points = []
            self.since = None
            self.average = None
            return None
        self.points.append((stamp, value))
        start = stamp - self.window
        while len(self.points)>1 and self.points[1][0]<=start:
            self.points.pop(0)
        covered = stamp - max(start, self.points[0][0])
        area = 0.
        for i,(at,power) in enumerate(self.points[:-1]):
            end = self.points[i+1][0]
            area += max(0., end-max(start,at))*power
        self.average = area/covered if covered>0 else value
        if self.average<self.threshold or value<self.threshold:
            self.since = None
            return 0.
        if self.since is None:
            self.since = stamp
        if covered<self.window or stamp-self.since<self.confirm:
            return 0.
        # Raw sub-threshold means heating has ended: never retain residual heat power.
        if value<self.threshold:
            self.since = None
            return 0.
        return self.average






def learn_power_modes(samples,model=None):
    """Extend the existing return-band learner; split by whole command sessions.

    Legacy unlabelled intervals remain an assumption, never confirmed auto data.
    No stage is inferred from a five-minute energy average.
    """
    profiles={};candidates={}
    ended={a.get('heat_command_at') for a,b in zip(samples,samples[1:]) if a.get('heating_requested') and
           (not b.get('heating_requested') or a.get('heat_command_at')!=b.get('heat_command_at'))}
    for mode in ('fixed_3','fixed_6','fixed_9','auto'):
        rows=[r for r in samples if r.get('boiler_power_mode')==mode and r.get('boiler_mode_confirmed') and
              r.get('power_quality')=='measured' and
              (r.get('quality') or r.get('power_source')=='kws_phases')
              and not r.get('power_estimated') and
              r.get('heat_command_at') in ended]
        versions={r.get('equipment_version') for r in rows}
        latest=max((v for v in versions if isinstance(v,int)),default=None)
        rows=[r for r in rows if r.get('equipment_version')==latest]
        source=next((r.get('power_source') for r in reversed(rows)
                     if r.get('power_source') in ('balance','kws_phases')),'balance')
        rows=[r for r in rows if r.get('power_source')==source]
        sessions=sorted({r.get('heat_command_at') for r in rows if number(r.get('heat_command_at')) is not None})
        physical_pairs=None
        if len(sessions)<3:continue
        if source=='kws_phases':
            if not any(r.get('measured_interval_kw') is not None for r in rows):continue
            # Never bridge an excluded row by zipping a filtered time series.
            # Both endpoints must belong to the same qualified equipment/session.
            selected={id(r) for r in rows}
            physical_pairs=[(a,b,interval) for a,b in zip(samples,samples[1:])
                            if id(a) in selected and id(b) in selected and
                            (interval:=modulation_interval(a,b,source)) is not None]
            sessions=sorted({a['heat_command_at'] for a,b,interval in physical_pairs})
        if len(sessions)<3:continue
        split=sessions[-max(1,len(sessions)//4)] if source=='kws_phases' else sessions[-1]
        if source=='kws_phases':
            train=[r for r in rows if number(r.get('heat_command_at')) is not None and r['heat_command_at']<split]
            test=[r for r in rows if number(r.get('heat_command_at')) is not None and r['heat_command_at']>=split]
        else:
            train=[r for r in rows if r.get('heat_command_at')!=split];test=[r for r in rows if r.get('heat_command_at')==split]
        evaluation_model=(test[0].get('thermal_model') or model) if test else model
        baseline=3. if mode=='auto' else float(mode[-1])
        training_observations=None if physical_pairs is None else (interval for a,b,interval in physical_pairs if a['heat_command_at']<split)
        testing_pairs=zip(test,test[1:]) if physical_pairs is None else ((a,b) for a,b,interval in physical_pairs if a['heat_command_at']>=split)
        candidate=learn_modulation(train,power_source=source,prior_kw=baseline if source=='kws_phases' else 3.,observations=training_observations)
        errors=[];base=[];thermal=[];base_thermal=[];holdout_minutes=0.
        simulated=None;reference=None
        for a,b in testing_pairs:
            dt=(b['ts']-a['ts'])/3600
            if not 3/60<=dt<=7/60 or not a.get('heating_requested') or a.get('heat_command_at')!=b.get('heat_command_at'):continue
            actual=boiler_power_interval(a,b,source)
            if actual is None or not 0<=actual<=10.5:continue
            predicted=modulation_power(a['return'],candidate,baseline if source=='kws_phases' else 3.)
            errors.append(abs(predicted-actual));base.append(abs(baseline-actual))
            holdout_minutes+=dt*60
            # Replay the held-out complete session from one measured thermal state;
            # long-horizon validation is still reported separately.
            if source!='kws_phases' and evaluation_model and all(number(r.get(k)) is not None for r in (a,b) for k in ('room','return','outside')):
                if simulated is None:simulated=(a['room'],a.get('floor_estimate',a['return']));reference=simulated
                simulated=step(*simulated,a['outside'],predicted,a.get('fire',0.),evaluation_model,dt)
                reference=step(*reference,a['outside'],baseline,a.get('fire',0.),evaluation_model,dt)
                thermal.append(abs(simulated[0]-b['room']));base_thermal.append(abs(reference[0]-b['room']))
        days=len({datetime.fromtimestamp(r['ts']).date() for r in rows}) if physical_pairs is None else len({datetime.fromtimestamp(a['ts']).date() for a,b,interval in physical_pairs})
        electrical_ok=(days>=2 and len(errors)>=6 and candidate['minutes']>=60
                       and (source!='kws_phases' or holdout_minutes>=30-1e-9)
                       and statistics.mean(errors)<statistics.mean(base)*.95)
        thermal_ok=bool(thermal) and statistics.mean(thermal)<=statistics.mean(base_thermal)
        accepted=electrical_ok and source!='kws_phases' and thermal_ok
        stage_seconds={k:sum((r.get('instantaneous_stage_seconds') or {}).get(k,0.) for r in train if r.get('heating_requested')) for k in ('off','3','6','9','unknown')}
        known=sum(stage_seconds[k] for k in ('off','3','6','9'))
        probabilities={k:stage_seconds[k]/known for k in ('off','3','6','9')} if known else None
        candidate.update(physical_mode=mode,validated=accepted,equipment_version=latest,sessions=len(sessions),days=days,
            electrical_validated=electrical_ok,holdout_minutes=round(holdout_minutes,1),
            thermal_validation_status=('independent_temperature_holdout_required' if source=='kws_phases' else
                                       'passed' if thermal_ok else 'not_validated'),
            holdout_samples=len(errors),holdout_mae_kw=statistics.mean(errors) if errors else None,
            baseline_mae_kw=statistics.mean(base) if base else None,
            holdout_temperature_mae=statistics.mean(thermal) if thermal else None,
            baseline_temperature_mae=statistics.mean(base_thermal) if base_thermal else None,
            stage_probabilities=probabilities,stage_seconds=stage_seconds,stage_note='Probabilities from time-aligned instantaneous power bands only; not proof of physical TEN contacts',
            last_sample_at=rows[-1]['ts'] if rows else None)
        if physical_pairs is not None:
            candidate.update(training_until=max((b['ts'] for a,b,_ in physical_pairs if a['heat_command_at']<split),default=0.),
                electrical_holdout_days=len({datetime.fromtimestamp(a['ts']).date() for a,b,_ in physical_pairs if a['heat_command_at']>=split}),
                observed_upper_kw=max((interval[4] for a,b,interval in physical_pairs),default=baseline))
        candidates[mode]=candidate
        if accepted:profiles[mode]=candidate
    return dict(version=2,profiles=profiles,candidates=candidates,status='validated' if profiles else 'insufficient_independent_sessions')


def certified_upper_power(profile):
    if not profile or profile.get('power_source')!='kws_phases' or profile.get('validated') is not True:
        return None
    value=number(profile.get('conservative_upper_kw'))
    mode=profile.get('physical_mode')
    rated=9. if mode=='auto' else float(mode[-1]) if mode in ('fixed_3','fixed_6','fixed_9') else None
    observed=number(profile.get('observed_upper_kw'))
    return value if (rated is not None and observed is not None and value is not None
                     and max(rated,observed)<=value<=10.5
                     and profile.get('thermal_validation_status')=='passed') else None


def retain_kws_profile(calibrations, profile, model, equipment_version):
    result=dict(calibrations or {})
    result['profiles']={mode:p for mode,p in (result.get('profiles') or {}).items()
                        if p.get('power_source')!='kws_phases'}
    if (certified_upper_power(profile) is not None and profile.get('thermal_model_version')==model.get('version')
            and profile.get('equipment_version')==equipment_version
            and profile.get('physical_mode') in ('fixed_3','fixed_6','fixed_9','auto')):
        result['profiles'][profile['physical_mode']]=profile
    if result['profiles']:
        result['status']='validated'
    elif result.get('status')=='validated':
        result['status']='insufficient_independent_sessions'
    return result


def power_profile(mode,calibrations,legacy,equipment_version=None):
    accepted=((calibrations or {}).get('profiles') or {}).get(mode)
    if accepted and (equipment_version is None or accepted.get('equipment_version')==equipment_version):return dict(accepted,physical_mode=mode,assumed=False)
    return dict(legacy or {},physical_mode=mode,assumed=True,validated=False,
                assumption='legacy_mixed_modes' if mode in ('auto','unconfirmed') else 'nominal_fixed_with_unknown_duty')
