"""Conservative, labelled power estimate used only during primary meter faults."""
from heating_model import number
from itertools import product
from copy import deepcopy
from datetime import datetime
import re


def validate_inlet_config(value):
    """One aggregate and its phases; each physical consumer is deducted once."""
    if not isinstance(value,dict):raise ValueError('Потрібен об’єкт схеми вводу')
    if not value:return {}
    phases=value.get('phases');loads=value.get('loads');total=value.get('total')
    entity=lambda e:isinstance(e,str) and re.fullmatch(r'sensor\.[a-z0-9_]+',e)
    if not entity(total) or not isinstance(phases,list) or len(phases)!=3 or not all(entity(e) for e in phases):
        raise ValueError('Потрібні загальна потужність і три різні потужності фаз вводу')
    if not isinstance(loads,list) or not 1<=len(loads)<=8:raise ValueError('Потрібні 1–8 неперекривних груп споживачів')
    seen=[total,*phases];physical=[];clean=[]
    groups=value.get('groups',[])
    if not isinstance(groups,list) or len(groups) not in (0,3):raise ValueError('Основний баланс потребує трьох груп лічильників')
    for load in loads+groups:
        if not isinstance(load,dict):raise ValueError('Некоректна група споживачів')
        identity=load.get('physical_id');aliases=load.get('entities');phase=load.get('phase')
        if isinstance(phase,str) and phase in ('0','1','2'):phase=int(phase)
        signed=load.get('signed',False)
        if isinstance(signed,str) and signed in ('true','false'):signed=signed=='true'
        if not isinstance(identity,str) or not 1<=len(identity)<=80 or identity in physical:raise ValueError('Фізичний прилад має бути унікальним')
        if not isinstance(aliases,list) or not 1<=len(aliases)<=3 or not all(entity(e) for e in aliases):raise ValueError('Потрібні сенсори потужності приладу')
        if phase is not None and (isinstance(phase,bool) or phase not in (0,1,2)):raise ValueError('Фаза: 0, 1, 2 або null, якщо невідома')
        if not isinstance(signed,bool):raise ValueError('signed має бути true або false')
        physical.append(identity);seen.extend(aliases)
        clean.append(dict(physical_id=identity,entities=list(aliases),phase=phase,signed=signed))
    if len(seen)!=len(set(seen)):raise ValueError('Ввід, фази та споживачі не можуть дублюватися')
    result=dict(total=total,phases=list(phases),loads=clean[:len(loads)])
    if groups:result['groups']=clean[len(loads):]
    heartbeats=value.get('heartbeats',{})
    if not isinstance(heartbeats,dict) or len(heartbeats)>40:raise ValueError('Некоректні датчики напруги для тихого споживання')
    for source,aliases in heartbeats.items():
        if source not in seen or not isinstance(aliases,list) or not 1<=len(aliases)<=3 or not all(entity(e) for e in aliases):
            raise ValueError('Потрібна напруга того самого фізичного лічильника')
    if heartbeats:result['heartbeats']={k:list(v) for k,v in heartbeats.items()}
    return result


def inlet_entities(config):
    if not config:return []
    return list(dict.fromkeys([config['total'],*config['phases'],
        *[e for load in config['loads']+config.get('groups',[]) for e in load['entities']],
        *[e for aliases in config.get('heartbeats',{}).values() for e in aliases]]))


def inlet_power(config,readings,stamp,*args,**kwargs):
    """Prefer the complete household-group balance; reserve uses appliances only.

    Both are derived estimates sharing the inlet, never independent meters.
    Quiet standby supported by fresh same-device voltage is explicitly held,
    retaining its original power timestamp and never entering model training.
    """
    try:config=validate_inlet_config(config)
    except (TypeError,ValueError):return dict(valid=False,watts=None,quality='unknown',reason='inlet_not_configured')
    readings={k:dict(v) for k,v in readings.items()};held=[]
    for entity,aliases in config.get('heartbeats',{}).items():
        row=readings.get(entity) or {};watts=number(row.get('value'));at=number(row.get('at'))
        if watts is not None and row.get('unit')=='kW':watts*=1000
        if watts is None or at is None or at>stamp or row.get('unit') not in ('W','kW') or not 0<=watts<=50:continue
        if aligned_power(readings,[config['total'],entity],stamp)['valid']:continue
        for alias in aliases:
            heartbeat=readings.get(alias) or {};voltage=number(heartbeat.get('value'));seen=number(heartbeat.get('at'))
            if voltage is not None and heartbeat.get('unit')=='V' and 50<voltage<300 and seen is not None and 0<=stamp-seen<=90:
                row['estimate_at']=seen
                held.append(dict(entity=entity,power_at=at,voltage_entity=alias,voltage_at=seen,watts=watts));break
    reserve=inlet_stage(config,readings,stamp,*args,**kwargs)
    reserve.update(method='appliance_stage',held_sources=held)
    if not config.get('groups'):return reserve
    primary=dict(valid=False,watts=None,quality='unknown',reason='group_unavailable')
    # Only GRID overlaps neither the household groups nor the boiler. Do not
    # subtract appliances again: they are already within the group readings.
    signed=[load for load in config['loads'] if load['signed']]
    if len(signed)!=1:return dict(reserve,primary=dict(primary,reason='signed_grid_required'))
    selected=[];conflict=None
    for load in config['groups']+signed:
        fresh=[]
        for entity in load['entities']:
            reading=aligned_power(readings,[config['total'],entity],stamp)
            if reading['valid']:fresh.append((entity,reading['values'][entity]))
        if not fresh:break
        entity,watts=fresh[0]
        if any(abs(watts-other)>max(150.,abs(watts)*.1) for _,other in fresh[1:]):conflict='load_alias_conflict';break
        if watts< -100:conflict='inlet_export_unverified';break
        selected.append(entity)
    if conflict:primary['reason']=conflict
    elif len(selected)==4:
        aligned=aligned_power(readings,[config['total'],*selected],stamp)
        if aligned['valid']:
            values=aligned['values'];deducted=sum(values[e] for e in selected)
            residual=values[config['total']]-deducted
            stage=min((0,3,6,9),key=lambda k:abs(residual-k*1000))
            if -300<=residual<=10500 and abs(residual-stage*1000)<=max(500.,stage*150):
                primary=dict(valid=True,watts=0. if stage==0 else residual,quality='estimated',independent=False,
                    reason='derived_group_balance',method='group_balance',stage_kw=stage,residual_w=residual,
                    deducted_w=deducted,selected_sources=selected,held_sources=[h for h in held if h['entity'] in selected],
                    control_reserve=reserve.get('control_reserve',{}))
            else:primary['reason']='group_residual_conflict'
        else:primary['reason']=aligned['reason']
    if primary['valid']:
        phases=aligned_power(readings,[config['total'],*config['phases']],stamp)
        if phases['valid']:
            values=phases['values'];capacity=sum(min(3450.,max(0.,values[e])) for e in config['phases'])
            if abs(values[config['total']]-sum(values[e] for e in config['phases']))>max(200.,abs(values[config['total']])*.05) or capacity<primary['watts']-500:
                return dict(reserve,valid=False,watts=None,quality='contradictory',reason='inlet_phase_capacity_conflict',primary=primary)
        # A stage estimate can differ slightly from true residual watts. A full
        # nominal stage of disagreement is a real conflict, not an averaging error.
        if reserve.get('valid') and abs(primary['watts']-reserve['watts'])>1500:
            return dict(reserve,valid=False,watts=None,quality='contradictory',reason='methods_conflict',primary=primary)
        return dict(primary,reserve=dict(valid=reserve['valid'],watts=reserve['watts'],reason=reserve['reason']))
    return dict(reserve,primary=primary)


def inlet_stage(config,readings,stamp,mode,thermostat,target,current,controller_request=False,
                model_kw=None,model_valid=False,window_kw=None):
    """Nominal-stage estimate, never independent metering or a learning target.

    Unknown load phases are enumerated, not inferred from device names. Even a
    unique electrical fit cannot distinguish an unmetered, coincident heater.
    """
    result=dict(valid=False,watts=None,quality='unknown',independent=False,reason='inlet_not_configured')
    try:config=validate_inlet_config(config)
    except (TypeError,ValueError):return result
    if not config:return result
    core=aligned_power(readings,[config['total']],stamp)
    if not core['valid']:return dict(result,reason=core['reason'],entity=core.get('entity'))
    total=core['values'][config['total']]
    if total< -100:return dict(result,reason='inlet_export_unverified')
    result.update(control_reserve=dict(valid=False,at=stamp,lower_w=0.,upper_w=max(0.,total),
        unknown_components=[],reason='inlet_stage_unconfirmed'),residual_w=None)
    loads=[];chosen=[];missing=[]
    for load in config['loads']:
        fresh=[]
        for entity in load['entities']:
            item=aligned_power(readings,[config['total'],entity],stamp)
            if item['valid']:fresh.append((entity,item['values'][entity]))
        if not fresh:missing.append(load['physical_id']);continue
        entity,watts=fresh[0]
        if any(abs(watts-other)>max(150.,abs(watts)*.1) for _,other in fresh[1:]):
            return dict(result,reason='load_alias_conflict',load=load['physical_id'])
        if watts<0 and not load['signed']:return dict(result,reason='negative_consumer',load=load['physical_id'])
        # The site's inlet export sign has not been verified. Absolute phase
        # readings plus negative GRID can otherwise turn 6 kW into a false 9 kW.
        if watts< -100 and load['signed']:return dict(result,reason='inlet_export_unverified',load=load['physical_id'])
        loads.append((watts,load['phase']));chosen.append(entity)
    deducted=sum(w for w,_ in loads);residual=total-deducted
    result.update(residual_w=residual,deducted_w=deducted,selected_sources=chosen)
    # Phase wattage validates nominal accounting; it is not a prerequisite for
    # a labelled control prior with fresh total / signed-GRID and live voltages.
    result['control_reserve'].update(valid=not any(load['signed'] and load['physical_id'] in missing for load in config['loads']),
        upper_w=max(0.,residual),unknown_components=missing)
    if residual< -300:
        result['control_reserve']['valid']=False
        return dict(result,reason='negative_residual')
    if missing:return dict(result,reason='consumer_unavailable')
    aligned=aligned_power(readings,[config['total'],*config['phases'],*chosen],stamp)
    if not aligned['valid']:return dict(result,reason=aligned['reason'],entity=aligned.get('entity'))
    phases=[aligned['values'][e] for e in config['phases']]
    if abs(total-sum(phases))>max(200.,abs(total)*.05):
        result['control_reserve']['valid']=False
        return dict(result,reason='inlet_sum_conflict')
    target=number(target);current=number(current)
    demand=(thermostat in ('heat','auto') and target is not None and current is not None and
            5<=current<=40 and (thermostat=='heat' and controller_request or target-current>=.5))
    candidates=[0] if abs(residual)<=300 else [k for k in (3,6,9) if abs(residual-k*1000)<=max(500.,k*120)]
    if not candidates:return dict(result,reason='stage_mismatch')
    if candidates[0] and not demand:return dict(result,reason='power_without_demand')
    if mode not in ('fixed_3','fixed_6','fixed_9','auto'):return dict(result,reason='physical_mode_unconfirmed')
    if mode.startswith('fixed_'):candidates=[k for k in candidates if k in (0,int(mode[-1]))]
    if not candidates:return dict(result,reason='physical_mode_conflict')
    fits=[];discrete=False
    # Zero loads have no phase allocation. Avoid 3**N equivalent placements.
    loads=[(w,p) for w,p in loads if abs(w)>1]
    for allocation in product(*[(p,) if p is not None else (0,1,2) for _,p in loads]):
        remainder=list(phases)
        for (watts,_),phase in zip(loads,allocation):remainder[phase]-=watts
        for mask in product((0,1),repeat=3):
            stage=3*sum(mask)
            if stage in candidates and all(abs(w-3000*on)<=max(450.,abs(3000*on)*.15) for w,on in zip(remainder,mask)):
                fits.append(stage);discrete=True
        # Meter reports may average switching over an interval. Enforce the
        # per-phase TEN ceiling without calling duty fractions physical stages.
        if all(-450<=w<=3450 for w in remainder):
            capacity=sum(min(3000.,max(0.,w)) for w in remainder)
            fits.extend(k for k in candidates if k and capacity>=k*1000-max(500.,k*120))
    if len(set(fits))!=1:return dict(result,reason='phase_stage_conflict')
    stage=fits[0]
    check='not_applicable'
    if mode=='auto':
        check='provisional' if not model_valid else 'collecting' if window_kw is None else 'compatible'
        if model_valid and number(window_kw) is not None and number(model_kw) is not None and abs(window_kw-model_kw)>3.:
            return dict(result,reason='return_model_conflict',model_check='conflict',quality='contradictory')
    return dict(result,valid=True,watts=stage*1000.,quality='estimated',reason='nominal_inlet_stage',
        stage_kw=stage,phase_check='discrete_fit' if discrete else 'bounded_average',
        model_check=check,model_kw=model_kw,window_kw=window_kw,skew_seconds=aligned['skew_seconds'])

def estimated_power(source):
    # A confirmed loss of boiler supply proves zero; thermostat inference does not.
    return str(source or '').startswith('fallback') and source!='fallback_grid_off'

def fallback_power(primary, failed, mode, target, current, phases, input_fresh,
                   manual_kw=None, grid_off=False):
    if not failed and number(primary) is not None:
        return primary, 'balance', False
    if grid_off:
        return 0., 'fallback_grid_off', False
    if mode == 'off':
        return 0., 'fallback_thermostat_off', False
    values = [number(v) for v in phases]
    ready = input_fresh and len(values) == 3 and all(v is not None for v in values)
    count = sum(v > 3000 for v in values) if ready else None
    if mode == 'auto':
        return (count * 3000., 'fallback_phases', bool(count)) if ready else (None, 'unavailable', None)
    if mode == 'heat':
        target, current = number(target), number(current)
        if target is None or current is None:
            return None, 'unavailable', None
        demand = target - current >= 1.
        if not demand:
            return 0., 'fallback_thermostat', False
        if count:
            return count * 3000., 'fallback_manual_phases', True
        # A thermostat demand alone does not identify the number of enabled TENs.
        # Retain the last independently measured stage; never invent a rating.
        kw = number(manual_kw)
        return (kw * 1000 if kw in (3, 6, 9) else None), 'fallback_manual_request', True
    return None, 'unavailable', None

def recovered_energy(intervals, start, end):
    return sum(max(0., min(end, r['end'])-max(start, r['start'])) * r['power_w']/3600000
               for r in intervals)

def recovered_coverage(intervals, start, end):
    return sum(max(0., min(end, r['end'])-max(start, r['start'])) for r in intervals)

def reconcile_energy(memory,payload,stamp,tz):
    """Idempotent Recorder reconciliation; never synthesizes training samples."""
    key=payload.get('id');evidence=payload.get('evidence_sha256')
    if not isinstance(key,str) or not re.fullmatch(r'[a-zA-Z0-9_-]{1,80}',key):raise ValueError('Некоректний recovery id')
    if not isinstance(evidence,str) or not re.fullmatch(r'[a-f0-9]{64}',evidence):raise ValueError('Потрібна SHA256 перевірених вимірювань')
    applied=memory.get('applied_recoveries',{})
    if key in applied:
        if applied[key].get('evidence_sha256')!=evidence:raise ValueError('Recovery id вже має інші вимірювання')
        return deepcopy(memory)
    counter=memory.get('inlet_energy') or {}
    total=sum(counter.get(k,0.) for k in ('origin_kwh','estimated_kwh','recovered_kwh'))
    baseline=number(payload.get('expected_total'))
    if not counter or baseline is None or abs(total-baseline)>.0001:raise ValueError('Лічильник змінився; перевірте baseline')
    bins=payload.get('bins')
    if not isinstance(bins,list) or not 1<=len(bins)<=500:raise ValueError('Потрібні 1–500 перевірених інтервалів')
    clean=[]
    for row in bins:
        if not isinstance(row,dict):raise ValueError('Некоректний інтервал')
        a,b,kwh=(number(row.get(k)) for k in ('start','end','kwh'))
        if any(v is None for v in (a,b,kwh)) or not stamp-32*86400<=a<b<=stamp or not 0<=kwh<=10.5*(b-a)/3600+.0001:
            raise ValueError('Некоректний час або енергія інтервалу')
        hour=datetime.fromtimestamp((a+b)/2,tz).hour
        clean.append(dict(start=a,end=b,kwh=kwh,estimated=True,historical=False,quality=False,
            night=hour>=23 or hour<7,cost=None,recovery_id=key))
    clean.sort(key=lambda r:r['start'])
    if any(a['end']>b['start'] for a,b in zip(clean,clean[1:])):raise ValueError('Інтервали перетинаються')
    for row in clean:
        if row['kwh']>0 and any(b.get('kwh',0)>0 and b['start']<row['end'] and b['end']>row['start'] for b in memory.get('energy_bins',[])):
            raise ValueError('Інтервал уже містить обліковану енергію')
    result=deepcopy(memory);counter=result['inlet_energy'];amount=sum(b['kwh'] for b in clean)
    counter['recovered_kwh']=counter.get('recovered_kwh',0.)+amount
    for row in clean:
        left=row['start']
        while left<row['end']:
            dt=datetime.fromtimestamp(left,tz)
            boundary=dt.replace(year=dt.year+(dt.month==12),month=dt.month%12+1,day=1,hour=0,minute=0,second=0,microsecond=0).timestamp()
            right=min(row['end'],boundary);month=counter.setdefault('months',{}).setdefault(dt.strftime('%Y-%m'),dict(origin_kwh=0.,calculated_kwh=0.))
            month['calculated_kwh']+=row['kwh']*(right-left)/(row['end']-row['start']);left=right
    result.setdefault('energy_bins',[]).extend(clean)
    result['energy_bins'].sort(key=lambda b:b['start'])
    result['energy_last']=dict(ts=stamp,total=total+amount)
    result.pop('cycle_energy_observation',None)
    for cycle in result.get('cycle_energy',{}).values():
        energy=sum(b['kwh']*max(0.,min(b['end'],cycle['end'])-max(b['start'],cycle['start']))/(b['end']-b['start']) for b in clean)
        if energy:
            cycle['calculated_kwh']=cycle.get('calculated_kwh',0.)+energy
            cycle['calculated_transition_kwh']=cycle.get('calculated_transition_kwh',0.)+energy
            cycle.setdefault('recoveries',[]).append(key);cycle['complete']=False;cycle.pop('quality_report',None)
    result.setdefault('applied_recoveries',{})[key]=dict(kwh=amount,evidence_sha256=evidence,at=stamp,
        start=clean[0]['start'],end=clean[-1]['end'],method=payload.get('method','audited_recorder_balance'),estimated=True)
    return result


def repair_samples(rows, recovery):
    intervals = recovery['intervals']
    changed = 0
    for i, row in enumerate(rows):
        if row.get('recovery_id') == recovery['id'] or row['ts'] < recovery['start']:
            continue
        offset = recovered_energy(intervals, recovery['start'], row['ts'])
        if number(row.get('energy')) is not None:
            row['energy'] += offset
        row['recovery_id'] = recovery['id']
        hit = next((r for r in intervals if r['start'] <= row['ts'] < r['end']), None)
        if hit:
            row['power'] = hit['power_w']/1000
            row['power_source'] = 'fallback_phases_recovered'
            row['power_estimated'] = True
            row['quality'] = (row.get('source') == 'Вітальня' and
                              all(number(row.get(k)) is not None for k in ('room','return','outside')))
            changed += 1
        if i:
            previous = rows[i-1]
            seconds = row['ts'] - previous['ts']
            coverage = recovered_coverage(intervals, previous['ts'], row['ts'])
            if seconds > 0 and coverage >= seconds-.01:
                row['interval_power'] = recovered_energy(intervals, previous['ts'], row['ts'])*3600/seconds
                row['power_estimated'] = True
            elif coverage > 0:
                row.pop('interval_power', None)
                row['quality'] = False
    return changed



def aligned_power(readings,entities,stamp,max_age=300,max_skew=30):
    """Validate the actual power timestamps, units and measurement kind."""
    values={};times=[]
    for entity in entities:
        r=readings.get(entity) or {};value=number(r.get('value'));at=number(r.get('estimate_at',r.get('at')))
        if value is None or at is None:return dict(valid=False,reason='missing_measurement',entity=entity)
        if not 0<=stamp-at<=max_age:return dict(valid=False,reason='stale_power',entity=entity)
        if r.get('unit') not in ('W','kW') or r.get('kind','active')!='active':return dict(valid=False,reason='power_units',entity=entity)
        values[entity]=value*(1000 if r['unit']=='kW' else 1);times.append(at)
    if not times:return dict(valid=False,reason='no_sources')
    skew=max(times)-min(times)
    if skew>max_skew:return dict(valid=False,reason='asynchronous_power',skew_seconds=skew)
    return dict(valid=True,values=values,skew_seconds=skew,oldest_at=min(times))


def phase_residual(topology,readings,stamp):
    """B is an audited residual estimate, never proof of one TEN per phase."""
    result=dict(valid=False,watts=None,quality='unknown',independent=False)
    if not isinstance(topology,dict) or topology.get('verified') is not True:return dict(result,reason='topology_unconfirmed')
    phases=topology.get('phases') or []
    if not isinstance(phases,list) or len(phases)!=3 or not all(isinstance(p,dict) for p in phases):return dict(result,reason='phase_mapping_missing')
    inputs=[];loads=[];physical=set();seen=set()
    for phase in phases:
        entity=phase.get('input')
        if not isinstance(entity,str) or entity in seen:return dict(result,reason='duplicate_phase')
        seen.add(entity);inputs.append(entity)
        if any(isinstance(phase.get(k),bool) or number(phase.get(k)) is None or phase[k]<0 for k in ('background_w','uncertainty_w')):
            return dict(result,reason='background_unconfirmed')
        if not isinstance(phase.get('loads'),list) or not all(isinstance(x,dict) for x in phase['loads']):return dict(result,reason='invalid_loads')
        for load in phase.get('loads',[]):
            identity=load.get('physical_id');entity=load.get('entity')
            if not identity or not entity or identity in physical or entity in seen:return dict(result,reason='duplicate_load')
            physical.add(identity);seen.add(entity);loads.append(entity)
    aligned=aligned_power(readings,inputs+loads,stamp)
    if not aligned['valid']:return dict(result,**{k:v for k,v in aligned.items() if k!='valid'})
    v=aligned['values'];residuals=[v[p['input']]-sum(v[x['entity']] for x in p.get('loads',[]))-p['background_w'] for p in phases]
    uncertainty=sum(p['uncertainty_w'] for p in phases)
    if min(residuals)<-max(100.,uncertainty):return dict(result,reason='negative_residual')
    watts=max(0.,sum(residuals))
    return dict(result,valid=True,watts=watts,quality='estimated',reason='audited_residual',
        phase_watts=residuals,lower_w=max(0.,watts-uncertainty),upper_w=watts+uncertainty,
        skew_seconds=aligned['skew_seconds'],shared_failures='Input meter, phase mapping and unmetered loads')


def combine_power(a,b):
    if a.get('valid'):
        if b.get('valid') and abs(a['watts']-b['watts'])>max(500.,abs(a['watts'])*.2):
            return dict(watts=None,quality='contradictory',reason='methods_conflict',independent=False)
        return dict(watts=a['watts'],quality=a.get('quality','measured'),reason=a.get('reason','primary_balance'),independent=False)
    if b.get('valid'):return dict(watts=b['watts'],quality='estimated',reason='phase_residual',independent=False)
    return dict(watts=None,quality='unknown',reason=a.get('reason') or b.get('reason') or 'no_measurement',independent=False)


def partial_balance_bounds(entities,readings,stamp):
    """Documented total / three load groups / signed inverter-GRID balance.

    An unmetered group makes boiler watts unknowable. Keep an interval instead
    of assigning that group zero or assuming its name means physical phase A/B/C.
    """
    invalid=dict(valid=False,watts=None,quality='unknown',independent=False)
    if not isinstance(entities,list) or len(entities)!=5 or len(set(entities))!=5:return dict(invalid,reason='unsupported_balance_layout')
    core=aligned_power(readings,[entities[0],entities[4]],stamp)
    if not core['valid']:return dict(invalid,reason=core['reason'])
    known=[];missing=[]
    for entity in entities[1:4]:
        (known if aligned_power(readings,[entity],stamp,max_age=90)['valid'] else missing).append(entity)
    if len(missing)!=1:return dict(invalid,reason='requires_one_unknown_group')
    # These are bounds for a degraded control reserve, never a measured balance.
    aligned=aligned_power(readings,[entities[0],*known,entities[4]],stamp,max_age=90,max_skew=90)
    if not aligned['valid']:return dict(invalid,reason=aligned['reason'])
    values=aligned['values'];remaining=values[entities[0]]-sum(values[e] for e in [*known,entities[4]])
    if values[entities[0]]<0 or any(values[e]<0 for e in known) or remaining < -100:return dict(invalid,reason='contradictory_balance')
    return dict(valid=True,watts=None,quality='unknown',independent=False,lower_w=0.,upper_w=max(0.,remaining),
                unknown_components=missing,known_components=known,reason='unattributed_residual',
                oldest_at=aligned['oldest_at'],skew_seconds=aligned['skew_seconds'],at=stamp)


def reserve_control_prior(bounds,mode,target,current,physical_mode):
    """An assumed control input, not an observation or a TEN-stage detection."""
    if not bounds.get('valid') or physical_mode not in ('fixed_3','fixed_6','fixed_9'):return None
    target=number(target);current=number(current)
    if mode not in ('off','auto','heat') or target is None or current is None or not 5<=current<=40:return None
    demand=mode=='heat' and target-current>=1. or mode=='auto' and target-current>=.5
    return min(float(physical_mode[-1])*1000,bounds['upper_w']) if demand else 0.


def integrate_observation(state,stamp,watts,quality,max_hold=45.,restart=False):
    """Causal left integration, bounded hold; persistent totals, no gap backfill."""
    previous=state.get('last')
    if previous and stamp<=previous['at']:return state
    for key in ('measured_kwh','estimated_kwh','unknown_seconds'):state.setdefault(key,0.)
    if previous:
        dt=stamp-previous['at'];known=(not restart and previous['quality'] in ('measured','estimated') and previous['watts'] is not None)
        seconds=min(dt,max_hold) if known else 0.
        if seconds:state[previous['quality']+'_kwh']+=max(0.,previous['watts'])*seconds/3600000
        state['unknown_seconds']+=dt-seconds
    state['last']=dict(at=stamp,watts=number(watts),quality=quality if number(watts) is not None else 'unknown')
    return state
