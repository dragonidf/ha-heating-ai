"""Conservative, labelled power estimate used only during primary meter faults."""
from heating_model import number

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
        r=readings.get(entity) or {};value=number(r.get('value'));at=number(r.get('at'))
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
    demand=mode=='heat' and target-current>=1. or mode=='auto' and target>current
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
