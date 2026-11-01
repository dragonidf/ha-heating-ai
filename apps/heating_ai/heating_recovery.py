"""Temporary native-auto recovery and verified thermostat commands."""
import time
from datetime import datetime, timezone, timedelta
from zoneinfo import ZoneInfo
from pathlib import Path
from threading import RLock
from uuid import uuid4
import gzip
import hashlib
import json
import os
import math
import traceback
import shutil
import copy
import zlib
from heating_model import number,in_window,heating_cycle,step,held_action,comfort_at,local_boundary
from heating_outages import schedule,blocked
from heating_fallback import estimated_power

def wire_values(value):
    if isinstance(value,dict):return {k:wire_values(v) for k,v in value.items()}
    if isinstance(value,list):return [wire_values(v) for v in value]
    if value is False:return 'false'
    return '0' if isinstance(value,(int,float)) and value==0 else value

def cycle_energy_allowance(entry):
    if not entry:return None
    initial=entry.get('initial_plan') or entry.get('baseline')
    if not initial or number(initial.get('kwh')) is None:return None
    spent=max(0.,number(entry.get('observed_kwh')) or 0.)
    if entry.get('complete') and number(entry.get('meter_kwh')) is not None:spent=entry['meter_kwh']
    return dict(start=entry['start'],end=entry['end'],remaining_kwh=max(0.,initial['kwh']-spent),
        known_spent_kwh=spent,fact_complete=bool(entry.get('complete')),initial_kwh=initial['kwh'])

def cycle_quality_summary(entry,stamp):
    initial=entry.get('initial_plan') or entry.get('baseline') or {}
    known=number(entry.get('observed_kwh'));expected=number(initial.get('kwh'))
    error=known-expected if known is not None and expected is not None else None
    reported_error=error if entry.get('complete') or error is not None and error>0 else None
    thermal=entry.get('thermal_quality') or {}
    observed=max(0.,min(stamp,entry['end'])-entry['start'])
    sessions=entry.get('initial_sessions');execution=entry.get('execution_quality')
    if execution:
        planned=sum(s['end']-s['start'] for s in sessions)/60 if isinstance(sessions,list) else None
        first=min((s['start'] for s in sessions or []),default=None)
        confirmed=number(entry.get('first_confirmed_heat_at'))
        execution=dict(execution,planned_minutes=planned,unknown_seconds=entry.get('unknown_seconds'),
            duration_difference_minutes=execution['confirmed_heating_minutes']-planned if planned is not None else None,
            first_confirmed_timing_error_seconds=confirmed-first if first is not None and confirmed is not None and entry.get('first_heat_edge_known') else None)
    return dict(start=entry['start'],end=entry['end'],completed=stamp>=entry['end'],initial_plan_kwh=expected,
        known_energy_kwh=known,energy_complete=bool(entry.get('complete')),independent_energy_kwh=None,
        energy_basis='calculated_meter_balance',counter_delta_kwh=entry.get('meter_kwh'),
        energy_error_kwh=round(reported_error,5) if reported_error is not None else None,
        energy_error_percent=round(reported_error/expected*100,2) if reported_error is not None and expected else None,
        energy_error_lower_bound_kwh=round(error,5) if error is not None and not entry.get('complete') else None,
        energy_error_is_lower_bound=not bool(entry.get('complete')),power_confidence='balance_without_independent_boiler_meter',
        unknown_seconds=entry.get('unknown_seconds'),automatic_kwh=entry.get('automatic_kwh'),
        other_kwh=entry.get('other_kwh'),transition_kwh=entry.get('transition_kwh'),
        attribution='Причина не встановлена' if error is not None and error>0 else 'Перевищення не підтверджено',
        comfort_violation_minutes=thermal.get('violation_minutes'),comfort_degree_hours=thermal.get('degree_hours'),
        overheat_minutes=thermal.get('overheat_minutes'),overheat_degree_hours=thermal.get('overheat_degree_hours'),
        sensor_coverage=round(thermal.get('known_seconds',0)/observed,4) if observed and thermal else None,
        significant_replans=entry.get('significant_replans'),execution=execution,
        review=entry.get('overrun_review'))

def positive_exposure(left,right,seconds):
    if left<=0 and right<=0:return 0.,0.
    if left>=0 and right>=0:return seconds,seconds*(left+right)/2
    positive=max(left,right);duration=seconds*positive/abs(right-left)
    return duration,duration*positive/2

def comparison_items(result):
    items=[dict(kind='actual',value=p) for p in result.get('actual',[])]
    for cycle in result.get('cycles',[]):
        meta={k:v for k,v in cycle.items() if k!='forecast'};forecast=cycle.get('forecast')
        if forecast:meta['forecast']={k:v for k,v in forecast.items() if k!='curve'}
        items.append(dict(kind='cycle',value=meta))
        items.extend(dict(kind='forecast',cycle_id=cycle['cycle_id'],value=p) for p in (forecast or {}).get('curve',[]))
    items.extend(dict(kind='marker',value=p) for p in result.get('markers',[]))
    for cycle in result.get('energy_cycles',[]):
        items.append(dict(kind='energy',value={k:v for k,v in cycle.items() if k!='plan_reviews'}))
        items.extend(dict(kind='review',value=dict(review,cycle_id=cycle['cycle_id'])) for review in cycle.get('plan_reviews',[]))
    if result.get('error') or result.get('warnings'):
        items.append(dict(kind='status',value={k:result[k] for k in ('error','warnings') if k in result}))
    return items

def comparison_page(items,offset,request_id,result_id,start,end):
    if type(offset) is not int or not 0<=offset<=len(items):raise ValueError('Invalid history cursor')
    page=dict(protocol=2,request_id=request_id,result_id=result_id,start=start,end=end,
        offset=offset,next_offset=None,total=len(items),items=[])
    cursor=offset
    while cursor<len(items) and len(page['items'])<64:
        item=items[cursor];page['items'].append(item);page['next_offset']=cursor+1
        if len(json.dumps(page).encode())>7800:
            page['items'].pop()
            if not page['items']:
                page['items'].append(dict(kind='error',value='Запис завеликий; оригінал збережено в приватному архіві.'));cursor+=1
            break
        cursor+=1
    page['next_offset']=cursor if cursor<len(items) else None
    return page

def temperature_curve(rows):
    """Public temperature evidence; a missing value is an explicit break."""
    result=[]
    for row in rows if isinstance(rows,list) else []:
        if not isinstance(row,dict) or number(row.get('ts')) is None:continue
        point={k:number(row.get(k)) for k in ('ts','temperature','lower','upper','target') if k in row or k=='temperature'}
        segments=row.get('heat_segments')
        if isinstance(segments,list):
            point['heat_segments']=[{k:s[k] for k in ('start','end','kwh','conditional') if k in s}
                for s in segments if isinstance(s,dict) and number(s.get('start')) is not None and number(s.get('end')) is not None]
        result.append(point)
    return sorted(result,key=lambda p:p['ts'])

def temperature_at(rows,stamp):
    before=None
    for point in rows:
        if point['ts']==stamp:return number(point.get('temperature'))
        if point['ts']>stamp:
            if not before or point['ts']-before['ts']>1800:return None
            a,b=number(before.get('temperature')),number(point.get('temperature'))
            return a+(b-a)*(stamp-before['ts'])/(point['ts']-before['ts']) if a is not None and b is not None else None
        before=point
    return None

def select_temperature_forecast(candidates,reference,journal=None,warnings=None):
    """Select original evidence before the cutoff; never replay a model."""
    eligible=sorted((c for c in candidates if number(c.get('at')) is not None and c['at']<=reference-43200),
                    key=lambda c:c['at'],reverse=True)
    for candidate in eligible:
        try:
            original=journal.read_snapshot(candidate['snapshot_id']) if candidate.get('snapshot_id') and journal else candidate
            plan=original.get('new_plan') or original
            rows=temperature_curve(plan.get('_calculation_curve') or plan.get('curve') or [])
            if rows and rows[0]['ts']<=reference<=rows[-1]['ts']:
                return dict(at=candidate['at'],plan_id=plan.get('plan_id'),curve=rows,
                    model_version=plan.get('model_version',(original.get('inputs',{}).get('model') or {}).get('version')),lead_hours=round((reference-candidate['at'])/3600,3),
                    source=candidate.get('source','persisted'),snapshot_id=candidate.get('snapshot_id'))
        except (OSError,ValueError,TypeError,AttributeError,EOFError,zlib.error):
            if warnings is not None:warnings.add('missing_forecast_snapshot')
    return None

def prediction_contract(value):
    """Validate persisted diagnostic inputs without granting missing validity."""
    numeric=lambda v:isinstance(v,(int,float)) and not isinstance(v,bool) and math.isfinite(v)
    if not isinstance(value,dict):return None,'invalid_prediction_type'
    p=dict(value)
    if (not all(numeric(p.get(k)) for k in ('ts','hours','temperature')) or p['hours']<=0 or
            not isinstance(p.get('valid'),bool) or not isinstance(p.get('model'),dict) or
            not isinstance(p.get('segments'),list)):
        return None,'invalid_prediction_fields'
    for key in ('checked_at','start_at','expected_energy'):
        if key in p and not numeric(p[key]):return None,'invalid_prediction_'+key
    if p.get('start_energy') is not None and not numeric(p['start_energy']):return None,'invalid_prediction_energy'
    for s in p['segments']:
        if (not isinstance(s,dict) or not numeric(s.get('start')) or not numeric(s.get('end')) or s['end']<=s['start']):
            return None,'invalid_prediction_segments'
    if p.get('reasons') is None:p['reasons']=[]
    if not isinstance(p.get('reasons',[]),list):return None,'invalid_prediction_reasons'
    observed=p.get('observed')
    if observed is None or isinstance(observed,dict) and not observed:
        p['observed']=None
    elif (not isinstance(observed,dict) or not isinstance(observed.get('valid'),bool) or
          not all(numeric(observed.get(k)) for k in ('at','room','floor','outside','fire','power_kw','request_seconds')) or
          observed['request_seconds']<0 or
          observed.get('energy') is not None and not numeric(observed['energy']) or
          not all(numeric(p['model'].get(k)) for k in ('floor_air','room_loss','floor_decay','heat_gain','fire_gain'))):
        return None,'invalid_observed_fields'
    else:p['observed']=dict(observed)
    return p,None

class HeatingRecovery:
    def reject_validation(self,reason,value,stamp):
        if not isinstance(self.memory.get('forecast_validation_rejections'),dict):self.memory['forecast_validation_rejections']={}
        rejected=self.memory['forecast_validation_rejections']
        rejected[reason]=rejected.get(reason,0)+1
        evidence=dict(at=stamp,reason=reason,record_type=type(value).__name__,
            keys=list(value)[:20] if isinstance(value,dict) else None,
            digest=hashlib.sha256(repr(value).encode()).hexdigest())
        journal=getattr(self,'decision_journal',None)
        if journal:
            try:evidence['snapshot_id']=journal.snapshot(value,stamp)
            except Exception:pass  # The original state backup remains evidence.
        if not isinstance(self.memory.get('validation_quarantine'),list):self.memory['validation_quarantine']=[]
        quarantine=self.memory['validation_quarantine']
        quarantine.append(evidence);del quarantine[:-32]

    def record_failure(self,stage,exc,critical=False):
        stamp=self.now().timestamp();key='control_incident' if critical else 'diagnostic_errors'
        if not critical and not isinstance(self.memory.get(key),dict):self.memory[key]={}
        records=self.memory if critical else self.memory[key]
        name=key if critical else stage;previous=records.get(name) or {}
        if not isinstance(previous,dict):previous={}
        signature=type(exc).__name__+': '+str(exc)
        new=not previous.get('active') or previous.get('signature')!=signature
        incident=(dict(incident_id=uuid4().hex,signature=signature,first_at=stamp,count=0,stage=stage) if new else previous)
        incident.update(active=True,last_at=stamp,count=incident['count']+1)
        if new:
            trace=traceback.extract_tb(exc.__traceback__)
            if trace:incident.update(module=Path(trace[-1].filename).name,function=trace[-1].name,line=trace[-1].lineno)
            self.log('%s incident %s at %s: %s\n%s', 'Control' if critical else 'Diagnostic',
                incident['incident_id'],stage,signature,traceback.format_exc(),level='ERROR' if critical else 'WARNING')
            incident['logged_at']=stamp
        elif stamp-incident.get('logged_at',0)>=300:
            self.log('Incident %s repeated %s times; last: %s',incident['incident_id'],incident['count'],signature,level='ERROR' if critical else 'WARNING')
            incident['logged_at']=stamp
        records[name]=incident
        return incident

    def run_diagnostic(self,stage,callback,*args,default=None,**kwargs):
        completed={k:list(self.memory[k]) if isinstance(self.memory.get(k),list) else [] for k in ('reserve_errors','forecast_validation')}
        try:
            result=callback(*args,**kwargs)
        except Exception as exc:
            self.record_failure(stage,exc)
            if stage in ('forecast_validation','peak_validation'):
                self.memory.update(completed)
            if stage=='forecast_validation':
                self.reject_validation('diagnostic_exception',self.memory.get('reserve_predictions'),self.now().timestamp())
                self.memory['reserve_predictions']=[]
            result=default
        else:
            error=(self.memory.get('diagnostic_errors') or {}).get(stage)
            if error:error.update(active=False,recovered_at=self.now().timestamp())
        return result

    def publish_diagnostic_health(self,**extra):
        errors=self.memory.get('diagnostic_errors') or {}
        active={k:{f:v.get(f) for f in ('incident_id','signature','first_at','last_at','count')} for k,v in errors.items() if v.get('active')}
        incident=self.memory.get('control_incident') or {};fallback=self.memory.get('temporary_fallback')
        quarantine=self.memory.get('validation_quarantine') or []
        recent=bool(quarantine and self.now().timestamp()-quarantine[-1]['at']<300)
        journal=getattr(self,'decision_journal',None)
        archive=journal.health() if journal and hasattr(journal,'health') else {}
        storage=self.memory.get('state_load_error') or getattr(journal,'error',None)
        control='fallback' if fallback else 'error' if incident.get('active') else 'healthy' if self.memory.get('last_successful_tick') else 'starting'
        state=('Помилка контролера' if control=='error' else 'Тимчасовий auto' if fallback else
               'Помилка збереження' if storage else 'Діагностика обмежена' if active or recent else 'Журнал доступний')
        self.publish('sensor.heating_diagnostics',state,storage_health='degraded' if storage else 'warning' if archive.get('pressure') else 'healthy',archive_health=archive,
            storage_error=storage,journal_used_bytes=getattr(getattr(self,'decision_journal',None),'used_bytes',None),
            journal_max_bytes=getattr(getattr(self,'decision_journal',None),'max_bytes',None),
            storage_degraded=bool(storage),validation_health='degraded' if active or recent else 'healthy',control_health=control,
            diagnostic_errors=active,validation_rejections=self.memory.get('forecast_validation_rejections',{}),
            last_successful_tick=self.memory.get('last_successful_tick'),incident_id=incident.get('incident_id'),
            control_error=incident.get('signature') if incident.get('active') else None,
            recorder_status=self.memory.get('decision_recorder_status','pending'),
            plan_execution=self.memory.get('plan_execution'),applied_plan=self.memory.get('applied_plan'),
            last_full_recompute=self.memory.get('last_full_recompute'),
            last_validation_rejection=(self.memory.get('validation_quarantine') or [None])[-1],**extra)

    def publish_controller_quality(self,now):
        completed=sorted((e for e in (self.memory.get('cycle_energy') or {}).values() if e['end']<=now.timestamp()),key=lambda e:e['end'])
        latest=(completed[-1].get('quality_report') or cycle_quality_summary(completed[-1],now.timestamp())) if completed else None
        groups=self.validation_summary()['groups'];mode=self.cfg.get('boiler_power_mode','unconfirmed')
        temperature_errors=[p for p in self.memory.get('temperature_errors',[]) if p.get('model_version')==self.model.get('version') and p.get('physical_mode')==mode]
        horizons=[]
        for hours in (1,3,6,12,24):
            group=next((g for g in groups if g['kind']=='frozen_plan' and g['horizon']==str(hours) and g['physical_mode']==mode),{})
            observations=[p for p in temperature_errors if p['horizon']==hours]
            leads=[p['actual_horizon_hours'] for p in observations]
            horizons.append(dict(hours=hours,count=len(observations),legacy_count=group.get('count',0),
                days=len({datetime.fromtimestamp(p['actual_at'],now.tzinfo).date() for p in observations}),
                temperature_mae=round(sum(abs(p['error']) for p in observations)/len(observations),3) if observations else None,
                actual_minimum_hours=min(leads) if leads else None,actual_maximum_hours=max(leads) if leads else None,
                energy_mae=group.get('energy_mae'),energy_count=group.get('energy_count',0),last_at=max((p['actual_at'] for p in observations),default=None)))
        journal=getattr(self,'decision_journal',None)
        self.publish('sensor.heating_controller_quality','Цикл завершено' if latest else 'Очікуємо завершення циклу',
            last_completed_cycle=latest,horizons=horizons,model_version=self.model.get('version'),
            archive_health=journal.health() if journal and hasattr(journal,'health') else {},
            prospective_only=True,independent_energy_measurement=False)

    async def verify_decision_history(self,kwargs=None,**extra):
        """Read Recorder itself; successful set_state is not persistence proof.

        This bounded asynchronous request cannot delay the thermostat tick.
        No include/exclude filters or retention settings are changed.
        """
        try:
            rows=await self.get_history(entity_id='sensor.heating_decision',
                start_time=datetime.fromtimestamp(time.time()-1200,timezone.utc),
                # None omits presence-based REST flags, including on older
                # AppDaemon transports that encode False as a query parameter.
                minimal_response=None,no_attributes=None,significant_changes_only=None)
            records=[r for group in (rows or []) for r in group if isinstance(r,dict)]
            seen={}
            verified=False
            for record in records:
                attributes=record.get('attributes',{})
                identifier=attributes.get('decision_id')
                required={'reason','plan_id','cycle_id','sources','measurements','policy','reserve','command','inputs'}
                preserved=(required<=attributes.keys() or
                    attributes.get('external_attributes') in (True,'true') and attributes.get('attributes_snapshot_id'))
                if identifier and attributes.get('schema_version') and preserved:
                    state=record.get('state')
                    if state in seen and seen[state]!=identifier:
                        verified=True
                    seen[state]=identifier
            status='verified_same_state_attributes' if verified else 'unverified: need two retained decisions with identical state'
        except Exception as exc:
            status='unverified: '+str(exc)
        self.memory['decision_recorder_status']=status
        self.publish_diagnostic_health(state_load_error=self.memory.get('state_load_error'),
            note='Перевіряється повна історія атрибутів; фільтри й термін зберігання не змінюються')

    def decision_event(self,event,action=None,reason=None,force=False,snapshot=None):
        journal=getattr(self,'decision_journal',None)
        if journal is None:
            return
        try:
            now=self.now();stamp=now.timestamp()
            context=getattr(self,'decision_context',None) or {}
            settings=context.get('settings') or {}
            forecast=context.get('forecast') or {}
            reserve=forecast.get('morning_reserve') or {}
            mode=self.raw('mode');armed=self.raw('armed')=='on'
            sources={}
            for key in ('room','return','outside','fireplace','power_balance','thermostat','energy'):
                entity=self.cfg.get(key)
                state=self.get_state(entity,attribute='all') if entity else None
                state=state if isinstance(state,dict) else {}
                reported=state.get('last_reported') or state.get('last_updated')
                try:
                    age=max(0.,stamp-datetime.fromisoformat(reported.replace('Z','+00:00')).timestamp())
                except (AttributeError,TypeError,ValueError):
                    age=None
                available=state.get('state') not in (None,'unknown','unavailable')
                max_age=self.cfg.get(key+'_max_age',300 if key=='power_balance' else 14400 if key=='room' else 1200)
                sources[key]=dict(value=number(state.get('state')),available=available,
                    reported_at=reported,age_seconds=round(age,1) if age is not None else None,
                    quality='fresh' if available and age is not None and age<=max_age else 'stale_or_unknown',
                    timestamp_kind='last_reported' if state.get('last_reported') else 'last_updated' if reported else 'unknown')
            actual=getattr(self,'current_power_w',None) if hasattr(self,'current_power_w') else self.val('power')
            command=self.memory.get('command_diagnostic') or {}
            reference=self.memory.get('plan_reference') or {}
            observation=getattr(self,'power_observation',{})
            record=dict(physical_mode=self.cfg.get('boiler_power_mode','unconfirmed'),
                equipment_version=self.memory.get('equipment_version',0),
                power_observation=observation,energy_quality=self.memory.get('power_observation_ledger'),
                ventilation=getattr(self,'ventilation_states',{}),
                execution_evidence=('physically_confirmed_by_balance' if command.get('action')=='heat' and command.get('status')=='confirmed' else 'unrequested_consumption') if actual is not None and actual>=500 and getattr(self,'power_quality','unknown')=='measured' else 'acknowledged' if command.get('status')=='confirmed' else command.get('status','calculated'),
                event=event,timezone=str(self.climate_tz),app_version=getattr(self,'release_version',None),
                plan_id=forecast.get('plan_id') or reference.get('plan_id'),
                parent_plan_id=forecast.get('parent_plan_id'),cycle_id=reserve.get('cycle_id'),
                reason=reason or context.get('reason') or event,
                action=action or context.get('action'),planned_demand=context.get('planned_demand'),
                demand=context.get('demand'),inhibitions=context.get('inhibitions',[]),
                mode=mode,armed=armed,permitted=bool(self.cfg.get('allow_actuation')) and armed,
                grid=self.grid_state(),grid_evidence='existing inverter connection, input voltage and heartbeat guards',
                power_w=actual,energy_counter_kwh=self.val('energy'),power_source=getattr(self,'power_source','unknown'),
                power_estimated=estimated_power(getattr(self,'power_source','')),
                incident_id=(self.memory.get('control_incident') or {}).get('incident_id'),
                control_state='fallback' if self.memory.get('temporary_fallback') else 'running',
                execution_state=(self.memory.get('plan_execution') or {}).get('status','unconfirmed'),
                reason_code=(self.memory.get('control_incident') or {}).get('stage') or event,
                power_quality=getattr(self,'power_quality','unknown'),
                source_timestamp=sources.get('power_balance',{}).get('reported_at'),
                physical_heating=actual is not None and actual>=1500,
                execution='automatic' if mode=='Автоматичне керування' and armed and self.memory.get('owned') and self.memory.get('last_command')=='heat' else 'manual_or_native' if actual is not None and actual>=1500 else 'idle',
                measurements=context.get('measurements',{}),sources=sources,
                policy={k:settings.get(k) for k in ('target','lower','upper','night_target','night_lower','night_upper','sleep_start','sleep_end','start','end','strategy','morning_min_minutes')},
                reserve=reserve,coast_peak=context.get('coast_peak'),cycle_energy=forecast.get('cycle_energy'),
                learning_attempt=self.memory.get('learning_attempt'),
                model_version=self.model.get('version'),command=command,
                inputs=self.memory.get('decision_input_refs',{}),
                weather_received_at=self.memory.get('weather_at'),
                weather_known_until=forecast.get('weather_known_until'),weather_complete=forecast.get('weather_complete'),
                schedule_received_at=getattr(self,'schedule_received_at',None),
                schedule_updated=(settings.get('outage_schedule') or {}).get('updated'),
                schedule_known_until=forecast.get('schedule_known_until'),
                hold_reason=forecast.get('stability_reason'),
                previous_reserve={k:(context.get('previous_reserve') or {}).get(k) for k in
                    ('cycle_id','reservation_start','reservation_end','planned_minutes','confirmed_completed_minutes','deficit_degree_hours')})
            # Suppress repeated fast ticks, but retain every full computation,
            # changes in quality/reason and every command confirmation event.
            signature=dict(record)
            signature.pop('sources')
            signature.pop('power_w')
            signature.pop('energy_counter_kwh')
            signature.pop('measurements')
            signature.pop('source_timestamp')
            signature.pop('energy_quality')
            signature['power_observation']={k:observation.get(k) for k in ('quality','reason','policy','controller_source')}
            signature['source_quality']={k:(v['quality'],v['available']) for k,v in sources.items()}
            fingerprint=hashlib.sha256(json.dumps(signature,sort_keys=True,default=str).encode()).hexdigest()
            if not force and fingerprint==getattr(self,'decision_fingerprint',None):
                return
            self.decision_fingerprint=fingerprint
            compact=journal.append(record,snapshot,stamp)
            self.memory['last_decision_id']=compact['decision_id']
            self.publish('sensor.heating_decision',record['action'] or 'unknown',**compact)
            self.publish_diagnostic_health(error=self.memory.get('state_load_error') or journal.error,retention_days=journal.retention/86400,
                         attribute_budget_bytes=8192,last_decision_id=compact['decision_id'],
                         )
        except Exception as exc:
            # Diagnostics must not trigger native-auto fallback or stop control.
            self.record_failure('decision_journal',exc)
            try:
                self.publish_diagnostic_health(error=str(exc))
            except Exception:
                pass

    def remember_plan_inputs(self,now,room,floor,outside,fire,weather,settings,forecast,previous):
        journal=getattr(self,'decision_journal',None)
        if journal is None:
            return None
        try:
            stamp=now.timestamp()
            # Only explicit planner inputs: never configuration, tokens or memory.
            inputs=dict(now=now.isoformat(),timezone=str(now.tzinfo),room=room,floor=floor,
                        outside=outside,fire=fire,model=dict(self.model),weather=weather,settings=settings,
                        power_kw=3.,previous=previous)
            refs={k:journal.snapshot(v,stamp) for k,v in dict(model=self.model,weather=weather,
                outage=settings.get('outage_schedule'),profile=settings.get('modulation_profile'),
                environment=settings.get('environment')).items()}
            self.memory['decision_input_refs']=refs
            snapshot=dict(inputs=inputs,old_plan=previous,new_plan=forecast,input_refs=refs)
            journal.snapshot(snapshot,stamp)  # durable before replacing/publishing a cancelled reservation
            return snapshot
        except Exception as exc:
            journal.error=str(exc)
            return dict(snapshot_failure=str(exc))

    def validate_reserve_predictions(self,now,room,power,quality,forecast=None,measurements=None):
        """Keep frozen-plan error separate from replay with measured inputs."""
        stamp=now.timestamp();pending=self.memory.get('reserve_predictions')
        if pending is None:pending=[]
        if not isinstance(pending,list):
            self.reject_validation('invalid_prediction_queue',pending,stamp);pending=[]
        clean=[]
        for value in pending:
            p,reason=prediction_contract(value)
            if reason:self.reject_validation(reason,value,stamp)
            else:clean.append(p)
        pending=self.memory['reserve_predictions']=clean
        for key,kind in (('reserve_errors',list),('forecast_validation',list),('forecast_validation_rejections',dict)):
            if self.memory.get(key) is None:self.memory[key]=kind()
            elif not isinstance(self.memory[key],kind):
                self.reject_validation('invalid_'+key,self.memory[key],stamp);self.memory[key]=kind()
        errors=self.memory['reserve_errors'];results=self.memory['forecast_validation'];rejected=self.memory['forecast_validation_rejections']
        grid=self.grid_state();energy=number(self.val('energy'))
        inputs=measurements or {}
        source_ok=(quality and power is not None and grid in ('on','off') and
                   not str(getattr(self,'power_source','balance') or 'unknown').startswith(('unknown','unavailable')) and
                   not estimated_power(getattr(self,'power_source','balance')) and
                   not (grid=='off' and power>0) and
                   (measurements is None or all(number(inputs.get(k)) is not None for k in ('outside','fire'))))
        for prediction in pending:
            reasons=prediction.setdefault('reasons',[])
            planned=any(s['start']<=stamp-.001<s['end'] for s in prediction['segments'])
            actual=power is not None and power>=1500
            requested=(self.memory['last_command']=='heat') if 'last_command' in self.memory else None
            interval_reason=('unknown_measurement' if not source_ok else
                'measurement_gap' if stamp-prediction.get('checked_at',stamp)>45 else None)
            reason=(interval_reason or
                ('control_mode_changed' if self.raw('mode')!='Автоматичне керування' or self.raw('armed')!='on' or self.memory.get('temporary_fallback') else
                 'model_changed' if prediction['model']!=self.model else
                 'equipment_changed' if prediction.get('equipment_version',0)!=self.memory.get('equipment_version',0) else
                 'execution_changed' if planned!=(requested if requested is not None else actual) else None))
            if reason:
                prediction['valid']=False
                if reason not in reasons:reasons.append(reason)
            prediction['checked_at']=stamp
            if interval_reason:prediction['timeline_valid']=False
            observed=prediction.get('observed')
            if observed and stamp>observed['at']:
                seconds=stamp-observed['at']
                if interval_reason or number(inputs.get('outside')) is None or number(inputs.get('fire')) is None:
                    observed['valid']=False
                if observed['valid']:
                    before=number(observed.get('energy'))
                    if energy is not None and before is not None and energy<before:
                        observed['valid']=False
                        if 'counter_reset' not in reasons:reasons.append('counter_reset')
                    else:
                        kw=((energy-before)*3600/seconds if energy is not None and before is not None else
                            (observed['power_kw']+power/1000)/2)
                        if not 0<=kw<=10.5:
                            observed['valid']=False;prediction['valid']=False;prediction['timeline_valid']=False
                            if 'counter_jump' not in reasons:reasons.append('counter_jump')
                        else:
                            observed['room'],observed['floor']=step(observed['room'],observed['floor'],
                                (observed['outside']+inputs['outside'])/2,kw,
                                (observed['fire']+inputs['fire'])/2,prediction['model'],seconds/3600)
                            observed['request_seconds']+=seconds if requested else 0.
                observed.update(at=stamp,energy=energy,power_kw=power/1000 if power is not None else None,
                                outside=inputs.get('outside'),fire=inputs.get('fire'))
            if prediction['ts']<=stamp and stamp-prediction['ts']<=45 and room is not None:
                start_energy=number(prediction.get('start_energy'))
                expected=prediction.get('expected_energy',0.)
                actual_energy=energy-start_energy if energy is not None and start_energy is not None and energy>=start_energy else None
                matched=actual_energy is not None and abs(actual_energy-expected)<=max(.05,expected*.05)
                if prediction['valid'] and matched:
                    errors.append(dict(hours=prediction['hours'],error=prediction['temperature']-room,
                        validated=True,at=stamp,model_version=self.model.get('version')))
                elif not matched and 'energy_mismatch' not in reasons:reasons.append('energy_mismatch')
                common=dict(physical_mode=prediction.get('physical_mode','unconfirmed'),equipment_version=prediction.get('equipment_version',0),hours=prediction['hours'],at=stamp,model_version=prediction['model'].get('version'),
                    horizon=prediction.get('horizon',str(prediction['hours'])),reasons=list(reasons),
                    window_start=prediction.get('start_at'),window_end=prediction['ts'])
                planned_hours=sum(max(0.,min(s['end'],prediction['ts'])-max(s['start'],prediction.get('start_at',s['start'])))
                                  for s in prediction['segments'])/3600
                actual_hours=observed['request_seconds']/3600 if observed and observed['valid'] else None
                power_error=(actual_energy/actual_hours-expected/planned_hours if actual_energy is not None and
                    actual_hours and planned_hours and 'control_mode_changed' not in reasons else None)
                results.append(dict(common,kind='frozen_plan',error=prediction['temperature']-room,
                    valid=source_ok and prediction.get('timeline_valid',True),reserve_eligible=prediction['valid'] and matched,
                    planned_kwh=expected,actual_kwh=actual_energy,
                    energy_error_kwh=actual_energy-expected if actual_energy is not None else None,
                    planned_request_minutes=planned_hours*60,actual_request_minutes=actual_hours*60 if actual_hours is not None else None,
                    power_error_kw=power_error,
                    energy_error_percent=(actual_energy/expected-1)*100 if actual_energy is not None and expected>0 else None))
                if observed and observed['valid']:
                    results.append(dict(common,kind='measured_inputs',error=observed['room']-room,valid=True,
                        actual_request_minutes=observed['request_seconds']/60,
                        note='Actual energy/weather/fire; unmeasured solar and ventilation remain disturbances'))
                for why in reasons:rejected[why]=rejected.get(why,0)+1
            elif not prediction['valid'] and not (observed and observed['valid']):
                for why in reasons:rejected[why]=rejected.get(why,0)+1
        pending[:]=[p for p in pending if p['ts']>stamp and (p['valid'] or (p.get('observed') or {}).get('valid'))]
        errors[:]=[r for r in errors[-240:] if r.get('at',stamp)>stamp-30*86400 and
                   r.get('model_version',self.model.get('version'))==self.model.get('version')]
        results[:]=[r for r in results[-720:] if r['at']>stamp-30*86400]
        if forecast is not None and not isinstance(forecast,dict):
            self.reject_validation('invalid_validation_forecast',forecast,stamp);return
        if forecast and stamp-(self.memory.get('reserve_validation_at') or 0)>=3600:
            self.memory['reserve_validation_at']=stamp
            rows=forecast.get('_calculation_curve') or forecast.get('curve') or []
            if (not isinstance(rows,list) or any(not isinstance(r,dict) or number(r.get('ts')) is None or
                    number(r.get('temperature')) is None or not isinstance(r.get('heat_segments'),list) for r in rows)):
                self.reject_validation('invalid_validation_curve',rows,stamp);return
            segments=[s for r in rows for s in r['heat_segments']]
            # Validation needs on/off intervals, not a duplicate of every 5-min
            # modulation sample in every pending horizon saved to state.json.
            intervals=[]
            for segment in sorted(segments,key=lambda s:s['start']):
                if intervals and segment['start']<=intervals[-1]['end']+.001:
                    intervals[-1]['end']=max(intervals[-1]['end'],segment['end'])
                else:
                    intervals.append(dict(start=segment['start'],end=segment['end']))
            targets=[(str(h),stamp+h*3600) for h in (1,3,6,12,24,36,48)]
            required=number((forecast.get('morning_reserve') or {}).get('assessment_until'))
            if required and required>stamp:targets.append(('required',required))
            queued=set()
            for horizon,target in targets:
                point=next((r for r in rows if r['ts']>=target),None)
                if point and point['ts'] not in queued:
                    queued.add(point['ts'])
                    observed=(dict(at=stamp,room=room,floor=inputs['floor'],outside=inputs['outside'],
                        fire=inputs['fire'],power_kw=power/1000,energy=energy,request_seconds=0.,valid=source_ok)
                        if source_ok and all(number(inputs.get(k)) is not None for k in ('floor','outside','fire')) else None)
                    pending.append(dict(physical_mode=self.cfg.get('boiler_power_mode','unconfirmed') if hasattr(self,'cfg') else 'unconfirmed',equipment_version=self.memory.get('equipment_version',0),ts=point['ts'],hours=(point['ts']-stamp)/3600,
                        horizon=horizon,start_at=stamp,checked_at=stamp,timeline_valid=source_ok,observed=observed,
                        temperature=point['temperature'],model=dict(self.model),valid=True,segments=intervals,
                        start_energy=self.val('energy'),expected_energy=sum(s.get('kwh',0.)*
                            max(0.,min(s['end'],point['ts'])-s['start'])/(s['end']-s['start'])
                            for s in segments if s['start']<point['ts'])))

    def validation_summary(self):
        groups={};stamp=self.now().timestamp()
        for r in self.memory.get('forecast_validation',[]):
            if not r.get('valid') or r.get('model_version')!=self.model.get('version'):continue
            mode=r.get('physical_mode','unconfirmed')
            key=mode+':'+r['kind']+':'+r['horizon'];group=groups.setdefault(key,dict(physical_mode=mode,kind=r['kind'],horizon=r['horizon'],
                count=0,mae=0.,minimum_hours=r['hours'],maximum_hours=r['hours'],last_at=r['at'],_days=set()))
            group['_days'].add(datetime.fromtimestamp(r['at'],self.now().tzinfo).date().isoformat())
            group['count']+=1;group['mae']+=abs(r['error']);group['last_at']=max(group['last_at'],r['at'])
            group['minimum_hours']=min(group['minimum_hours'],r['hours']);group['maximum_hours']=max(group['maximum_hours'],r['hours'])
            for field,label in (('energy_error_kwh','energy'),('power_error_kw','power')):
                value=number(r.get(field))
                if value is not None:
                    group[label+'_count']=group.get(label+'_count',0)+1
                    group[label+'_mae']=group.get(label+'_mae',0)+abs(value)
        for g in groups.values():
            g['days']=len(g.pop('_days'))
            g['mae']=round(g['mae']/g['count'],3);g['age_hours']=round((stamp-g['last_at'])/3600,1)
            for label in ('energy','power'):
                if g.get(label+'_count'):g[label+'_mae']=round(g[label+'_mae']/g[label+'_count'],3)
        return dict(groups=list(groups.values()),pending=len(self.memory.get('reserve_predictions',[])),
                    rejected=dict(self.memory.get('forecast_validation_rejections',{})))

    def observe_stop_peak(self,now,room,power,quality):
        stamp=now.timestamp();requested=self.memory.get('last_command')
        previous=self.memory.get('peak_observation')
        watch=self.memory.get('peak_watch')
        if watch:
            reason=('unknown_measurement' if not quality or power is None else
                'measurement_gap' if stamp-watch['last_at']>45 else
                'model_changed' if self.model.get('version')!=watch['model_version'] else
                'reheated_before_peak_window' if requested=='heat' or power>=1500 else None)
            if quality:watch['maximum']=max(watch['maximum'],room)
            watch['last_at']=stamp
            if reason or stamp>=watch['end']:
                self.memory.setdefault('forecast_validation',[]).append(dict(kind='post_stop_peak',horizon='3',hours=3.,
                    at=stamp,model_version=watch['model_version'],window_start=watch['start'],window_end=stamp,
                    valid=reason is None,error=watch['predicted']-watch['maximum'] if reason is None else None,
                    reasons=[reason] if reason else []))
                if reason:
                    rejected=self.memory.setdefault('forecast_validation_rejections',{})
                    rejected[reason]=rejected.get(reason,0)+1
                self.memory.pop('peak_watch',None)
        if previous and previous['request']=='heat' and requested=='off' and quality and power==0:
            peak=number(getattr(self,'decision_context',{}).get('coast_peak'))
            if peak is not None:
                self.memory['peak_watch']=dict(start=stamp,end=stamp+3*3600,last_at=stamp,maximum=room,
                    predicted=peak,model_version=self.model.get('version'))
        self.memory['peak_observation']=dict(at=stamp,request=requested)

    def observe_cycle_energy(self,now,settings,power,energy,grid,automatic,temperature=None,temperature_valid=False):
        cycle=heating_cycle(now,settings)
        if not cycle:return
        stamp=now.timestamp();ledger=self.memory.setdefault('cycle_energy',{})
        previous=self.memory.get('cycle_energy_observation')
        current=dict(at=stamp,energy=energy,cycle=cycle,automatic=automatic,grid=grid,power=power,
            temperature=temperature,temperature_valid=temperature_valid,
            known=power is not None and grid in ('on','off') and
                  not getattr(self,'power_source','unknown').startswith(('unknown','unavailable')) and
                  not estimated_power(getattr(self,'power_source','unknown')))
        for c in {(x['cycle_id'],x['cycle_start']):x for x in [cycle,*([previous['cycle']] if previous else [])]}.values():
            key=c['cycle_id']+'@'+str(c['cycle_start'])
            entry=ledger.setdefault(key,dict(start=c['cycle_start'],end=c['cycle_end'],observed_kwh=0.,
                automatic_kwh=0.,other_kwh=0.,transition_kwh=0.,unknown_seconds=0.,complete=stamp<=c['cycle_start']))
            if stamp==c['cycle_start'] and number(energy) is not None:
                entry.setdefault('counter_start',energy)
            if not previous:
                gap=max(0.,min(stamp,c['cycle_end'])-max(entry.get('observed_until',c['cycle_start']),c['cycle_start']))
                if gap:entry['unknown_seconds']+=gap;entry['complete']=False
                continue
            seconds=stamp-previous['at'];a=max(previous['at'],c['cycle_start']);b=min(stamp,c['cycle_end'])
            if b<=a:continue
            if (0<seconds<=45 and previous.get('temperature_valid') and temperature_valid and
                    number(previous.get('temperature')) is not None and number(temperature) is not None and
                    all(number(settings.get(k)) is not None for k in ('target','lower','upper'))):
                thermal=entry.setdefault('thermal_quality',dict(known_seconds=0.,violation_minutes=0.,degree_hours=0.,
                    overheat_minutes=0.,overheat_degree_hours=0.))
                cuts=[a,b]
                for day in {datetime.fromtimestamp(a,now.tzinfo).date(),datetime.fromtimestamp(b,now.tzinfo).date()}:
                    for key in ('sleep_start','sleep_end'):
                        if settings.get(key):
                            boundary=local_boundary(day,settings[key],now.tzinfo).timestamp()
                            if a<boundary<b:cuts.append(boundary)
                for left,right in zip(sorted(cuts),sorted(cuts)[1:]):
                    _,low,high,_=comfort_at(datetime.fromtimestamp((left+right)/2,now.tzinfo),settings)
                    before=previous['temperature']+(temperature-previous['temperature'])*(left-previous['at'])/seconds
                    after=previous['temperature']+(temperature-previous['temperature'])*(right-previous['at'])/seconds
                    cold,cold_area=positive_exposure(low-before,low-after,right-left)
                    hot,hot_area=positive_exposure(before-high,after-high,right-left)
                    thermal['known_seconds']+=right-left;thermal['violation_minutes']+=(cold+hot)/60
                    thermal['degree_hours']+=(cold_area+hot_area)/3600
                    thermal['overheat_minutes']+=hot/60;thermal['overheat_degree_hours']+=hot_area/3600
            if 0<seconds<=45 and previous.get('grid')==grid=='off':
                entry['outage_seconds']=entry.get('outage_seconds',0.)+b-a
            if 0<seconds<=45 and previous['automatic'] and automatic:
                entry['requested_seconds']=entry.get('requested_seconds',0.)+b-a
            if 0<seconds<=45 and previous['known'] and current['known']:
                if power>=1500 and 'first_confirmed_heat_at' not in entry:
                    entry['first_confirmed_heat_at']=stamp if previous['power']<1500 else a
                    entry['first_heat_edge_known']=previous['power']<1500
                if previous['power']>=1500 and power>=1500:entry['measured_heat_seconds']=entry.get('measured_heat_seconds',0.)+b-a
                entry['execution_quality']=dict(requested_minutes=round(entry.get('requested_seconds',0)/60,2),
                    confirmed_heating_minutes=round(entry.get('measured_heat_seconds',0)/60,2),
                    timing_resolution_seconds=45,unknown_seconds=entry['unknown_seconds'])
            before=number(previous.get('energy'));after=number(energy)
            counter_ok=(0<seconds<=45 and before is not None and after is not None and
                0<=after-before<=10.5*seconds/3600+.00002)
            if counter_ok:
                if previous['at']<=c['cycle_start']<=stamp:
                    entry.setdefault('counter_start',before+(after-before)*(c['cycle_start']-previous['at'])/seconds)
                if number(entry.get('counter_start')) is not None:
                    end_value=before+(after-before)*(b-previous['at'])/seconds
                    entry['meter_kwh']=max(0.,end_value-entry['counter_start'])
            elif before is not None and after is not None and (after<before or
                    0<seconds<=45 and after-before>10.5*seconds/3600+.00002):
                entry.pop('counter_start',None);entry.pop('meter_kwh',None);entry['counter_reset']=True
            if counter_ok and previous['known'] and current['known']:
                kwh=(after-before)*(b-a)/seconds;entry['observed_kwh']+=kwh
                kind=('automatic' if previous['automatic'] and automatic else 'other' if not previous['automatic'] and not automatic else 'transition')
                entry[kind+'_kwh']+=kwh
            else:
                entry['unknown_seconds']+=b-a;entry['complete']=False
            entry['observed_until']=b
        for entry in ledger.values():
            initial=entry.get('initial_plan') or entry.get('baseline')
            if initial and entry['observed_kwh']>initial['kwh'] and not entry.get('overrun_review'):
                entry['overrun_review']=dict(at=stamp,known_kwh=entry['observed_kwh'],initial_kwh=initial['kwh'],
                    attribution='Причина не встановлена',unknown_seconds=entry['unknown_seconds'])
                if entry['start']<=stamp<entry['end']:self.next_compute=0
            if stamp>=entry['end'] and not entry.get('quality_report'):
                entry['quality_report']=cycle_quality_summary(entry,stamp)
        self.memory['cycle_energy_observation']=current
        self.memory['cycle_energy']={k:v for k,v in ledger.items() if v['end']>stamp-16*86400}

    def cycle_budget(self,now,forecast,context):
        reserve=forecast.get('morning_reserve') or {};key=reserve.get('cycle_id')
        entry=next((v for v in self.memory.get('cycle_energy',{}).values()
                    if v['start']==reserve.get('cycle_start') and v['end']==reserve.get('cycle_end')),None)
        if entry is None:return {}
        stamp=now.timestamp();remaining=number(reserve.get('end_cycle_planned_kwh'))
        if remaining is None:return {}
        legacy_base=entry.get('baseline')
        if stamp<entry['start']:
            entry['last_prestart']=dict(at=stamp,kwh=remaining)
            if entry['start']-stamp<=3*3600:entry.setdefault('baseline',dict(at=stamp,kwh=remaining))
        if stamp<entry['end'] and entry['start']-stamp<=12*3600:
            base=legacy_base
            entry.setdefault('initial_plan',dict(base,before_start=base['at']<entry['start'],legacy=True) if base else
                dict(at=stamp,kwh=remaining,plan_id=forecast.get('plan_id'),before_start=stamp<entry['start']))
        sessions=[]
        for segment in sorted(forecast.get('heat_segments',[]),key=lambda s:s['start']):
            a=max(entry['start'],segment['start']);b=min(entry['end'],segment['end'])
            if b<=a:continue
            if sessions and a<=sessions[-1]['end']+1:sessions[-1]['end']=max(b,sessions[-1]['end'])
            else:sessions.append(dict(start=a,end=b))
        initial=entry.get('initial_plan') or {}
        if initial.get('at')==stamp and not initial.get('legacy'):
            entry.setdefault('initial_sessions',sessions)
        hashes={k:hashlib.sha256(json.dumps(v,sort_keys=True,separators=(',',':'),allow_nan=False).encode()).hexdigest()
                for k,v in context.items()}
        previous=entry.get('projection_inputs')
        changes=[k for k in hashes if previous and previous.get(k)!=hashes[k]]
        entry['projection_inputs']=hashes
        spent=entry['meter_kwh'] if entry['complete'] and number(entry.get('meter_kwh')) is not None else entry['observed_kwh']
        total=spent+remaining
        baseline=entry.get('baseline')
        initial=entry.get('initial_plan') or baseline
        prior=entry.get('latest_plan') or {}
        deficit=number(reserve.get('deficit_degree_hours'))
        if prior and prior.get('plan_id')!=forecast.get('plan_id') and (
                abs(total-(number(prior.get('kwh')) or total))>=.05 or sessions!=entry.get('current_sessions',sessions)):
            entry['significant_replans']=entry.get('significant_replans',0)+1
            previous_deficit=number(prior.get('deficit_degree_hours'))
            review=dict(at=stamp,plan_id=forecast.get('plan_id'),parent_plan_id=prior.get('plan_id'),
                energy_increase_kwh=round(total-prior['kwh'],4) if number(prior.get('kwh')) is not None else None,
                change_sources=changes,forecast_deficit_degree_hours=deficit,previous_deficit_degree_hours=previous_deficit,
                projected_deficit_reduction=previous_deficit-deficit if previous_deficit is not None and deficit is not None else None,
                comparison_basis='successive_forecasts_not_causal',
                conditional=bool(reserve.get('conditional') or not forecast.get('weather_complete') or not forecast.get('schedule_complete')),
                reason=forecast.get('stability_reason') or 'Оновлено прогноз за фактичними входами; причинний внесок не визначено')
            entry['plan_reviews']=(entry.get('plan_reviews',[])+[review])[-32:]
        entry['current_sessions']=sessions
        entry['latest_plan']=dict(at=stamp,kwh=round(total,4) if total is not None else None,
            remaining_kwh=round(remaining,4),plan_id=forecast.get('plan_id'),deficit_degree_hours=deficit)
        detail=reserve.get('comfort_deficit') or {}
        requested=entry.get('requested_seconds',0.)
        deviation=dict(extra_kwh=round(total-initial['kwh'],4) if initial and total is not None else None,
            outage_minutes=round(entry.get('outage_seconds',0.)/60,2),
            request_average_kw=round(entry['automatic_kwh']*3600/requested,3) if requested else None,
            minimum_temperature=detail.get('minimum_before_next_heat'),
            violation_at=detail.get('first_comfort_violation_at'),
            violation_temperature=detail.get('first_violation_temperature'),
            lower=detail.get('first_violation_lower'),
            attribution='measured_execution_and_current_forecast; causal shares are not independently identified')
        entry['latest_plan']['deviation']=deviation
        return dict(cycle_id=key,observed_kwh=round(spent,4),remaining_kwh=round(remaining,4),
            initial_plan=initial,latest_plan=entry['latest_plan'],
            deviation=deviation,
            fact_complete=entry['complete'],projection_complete=entry['complete'],
            initial_plan_is_prestart=bool(initial and initial['at']<=entry['start']),
            overrun_kwh=round(max(0.,spent-initial['kwh']),4) if initial else None,
            projected_total_kwh=round(total,4) if total is not None else None,
            observation_complete=entry['complete'],unknown_minutes=round(entry['unknown_seconds']/60,2),
            baseline=baseline,last_prestart=entry.get('last_prestart'),
            delta_from_baseline_kwh=round(total-baseline['kwh'],4) if baseline and total is not None else None,
            baseline_consumed=spent>=baseline['kwh'] if baseline else None,
            change_sources=changes or ['execution' if previous else 'initial_projection'],
            remaining_need=(reserve.get('no_morning_counterfactual') or {}).get('deficit_degree_hours'),
            need_certainty='conditional' if reserve.get('conditional') else 'validated_scenario',
            coast_peak=(context.get('thermal_state') or {}).get('coast_peak'),
            automatic_kwh=round(entry['automatic_kwh'],4),other_kwh=round(entry['other_kwh'],4),
            transition_kwh=round(entry['transition_kwh'],4))

    def observe_temperature_history(self,now,settings,room,power,quality,forecast=None):
        """Capture issued forecasts prospectively; never reconstruct an earlier issue."""
        stamp=now.timestamp();history=self.memory.setdefault('temperature_history',{})
        checks=self.memory.get('temperature_checks') or [];errors=self.memory.get('temperature_errors') or []
        if not isinstance(checks,list):
            self.reject_validation('invalid_temperature_check',checks,stamp);checks=[]
        errors=[p for p in errors if isinstance(p,dict) and number(p.get('actual_at')) is not None and
            all(number(p.get(k)) is not None for k in ('horizon','actual_horizon_hours','error'))] if isinstance(errors,list) else []
        self.memory['temperature_errors']=errors
        sensor=getattr(self,'temperature_quality',{});age=number(sensor.get('primary_age_seconds'))
        reported=number(sensor.get('primary_reported_at')) if 'primary_reported_at' in sensor else stamp-age if age is not None else None
        cadence=number(sensor.get('cadence_seconds')) or 3600.
        remaining=[]
        for check in checks:
            if (not isinstance(check,dict) or not isinstance(check.get('forecast'),dict) or
                    any(number(check.get(k)) is None for k in ('target_at','issued_at','horizon')) or
                    'model_version' not in check or 'physical_mode' not in check):
                self.reject_validation('invalid_temperature_check',check,stamp);continue
            target=check['target_at']
            if quality and room is not None and reported is not None and target<=reported<=target+cadence*1.5:
                source=check['forecast'];journal=getattr(self,'decision_journal',None)
                try:
                    original=journal.read_snapshot(source['snapshot_id']) if source.get('snapshot_id') and journal else source
                    expected=temperature_at(original.get('curve') or [],reported)
                except (OSError,ValueError,TypeError,AttributeError,EOFError,zlib.error):expected=None
                if expected is not None:
                    errors.append(dict(horizon=check['horizon'],issued_at=check['issued_at'],actual_at=reported,
                        model_version=check['model_version'],physical_mode=check['physical_mode'],
                        actual_horizon_hours=(reported-check['issued_at'])/3600,
                        forecast_temperature=expected,actual_temperature=room,error=expected-room))
                else:self.memory['temperature_validation_missing']=self.memory.get('temperature_validation_missing',0)+1
            elif stamp>target+cadence*1.5:
                self.memory['temperature_validation_missing']=self.memory.get('temperature_validation_missing',0)+1
            else:remaining.append(check)
        self.memory['temperature_checks']=remaining
        errors[:]=[p for p in errors[-4096:] if p['actual_at']>=stamp-30*86400]
        candidates=self.memory.setdefault('temperature_forecasts',[])
        if forecast and stamp-(candidates[-1]['at'] if candidates else 0)>=290:
            rows=forecast.get('_calculation_curve') or forecast.get('curve') or []
            curve=temperature_curve(rows)
            if curve:
                issued_at=number(forecast.get('issued_at')) or stamp
                issued=dict(at=issued_at,plan_id=forecast.get('plan_id'),curve=curve,
                    weather_complete=forecast.get('weather_complete'),model_version=self.model.get('version'))
                journal=getattr(self,'decision_journal',None)
                if journal:
                    digest=journal.snapshot(issued,issued_at)
                    journal.append(dict(event='temperature_forecast',action='observe',forecast_snapshot_id=digest,
                        plan_id=issued['plan_id'],model_version=issued['model_version']),stamp=issued_at)
                    candidates.append(dict(at=issued_at,snapshot_id=digest))
                else:candidates.append(issued)
                if issued_at-self.memory.get('temperature_validation_issued_at',0)>=3600:
                    self.memory['temperature_validation_issued_at']=issued_at
                    source=dict(at=issued_at,snapshot_id=digest) if journal else copy.deepcopy(issued)
                    for horizon in (1,3,6,12,24):
                        target=stamp+horizon*3600
                        if temperature_at(curve,target) is not None:
                            self.memory['temperature_checks'].append(dict(issued_at=issued_at,target_at=target,horizon=horizon,
                                model_version=self.model.get('version'),physical_mode=getattr(self,'cfg',{}).get('boiler_power_mode','unconfirmed'),forecast=source))
        candidates[:]=[x for x in candidates if x['at']>=stamp-36*3600]
        cycle=heating_cycle(now,settings)
        last=self.memory.get('temperature_history_power') or {}
        if cycle and cycle['cycle_start']<=stamp<cycle['cycle_end'] and cycle['cycle_id'] not in history:
            reference=cycle['cycle_start']
            frozen=select_temperature_forecast(candidates,reference,getattr(self,'decision_journal',None))
            history[cycle['cycle_id']]=dict(cycle_id=cycle['cycle_id'],cycle_start=reference,cycle_end=cycle['cycle_end'],
                started_at=reference,reference_at=reference,reference_kind='window_start',start_known=False,
                forecast=frozen,missing_reason=None if frozen else 'Завчасний прогноз не збережено',actual=[])
        if cycle and number(power) is not None and power>=1500:
            key=cycle['cycle_id'];entry=history.get(key)
            continuous=bool(last and 0<stamp-last['at']<=45 and number(last.get('power')) is not None and last['power']>=1500)
            if entry is None and not continuous:
                edge=bool(last and 0<stamp-last['at']<=45 and number(last.get('power')) is not None and last['power']<1500)
                frozen=select_temperature_forecast(candidates,stamp,getattr(self,'decision_journal',None)) if edge else None
                history[key]=dict(cycle_id=key,cycle_start=cycle['cycle_start'],cycle_end=cycle['cycle_end'],
                    started_at=stamp,start_known=edge,forecast=frozen,actual=[])
            elif entry is not None and not entry.get('physical_started_at') and not continuous:
                edge=bool(last and 0<stamp-last['at']<=45 and number(last.get('power')) is not None and last['power']<1500)
                entry.update(physical_started_at=stamp,start_known=edge)
        self.memory['temperature_history_power']=dict(at=stamp,power=power)
        for entry in history.values():
            finish=entry['started_at']+86400
            if entry.get('reference_kind')=='window_start':
                day=datetime.fromtimestamp(entry['started_at'],now.tzinfo).date()+timedelta(days=1)
                finish=local_boundary(day,settings['start'],now.tzinfo).timestamp()
            actual=entry['actual']
            if entry['started_at']<=stamp<=finish and (not actual or stamp-actual[-1]['ts']>=290 or
                    quality!=(actual[-1]['temperature'] is not None)):
                actual.append(dict(ts=stamp,temperature=room if quality else None))
        self.memory['temperature_history']={k:v for k,v in history.items() if v['started_at']>stamp-30*86400}

    def publish_plan_history(self,cycle_id=None,request_id=None):
        history=self.memory.get('temperature_history') or {}
        keys=sorted(history,key=lambda k:history[k]['started_at'],reverse=True)
        selected=history.get(cycle_id or getattr(self,'history_selected',None))
        if selected is None and keys:selected=history[keys[0]]
        if selected:self.history_selected=selected['cycle_id']
        # Send one requested curve, not a month of observations on every tick.
        def wire(value):
            if isinstance(value,dict):return {k:wire(v) for k,v in value.items()}
            if isinstance(value,list):return [wire(v) for v in value]
            if value is False:return 'false'
            return '0' if isinstance(value,(int,float)) and value==0 else value
        comparison=getattr(self,'history_comparison',None)
        summary=({k:comparison.get(k) for k in ('start','end','warnings','error')} if comparison else None)
        if summary is not None:summary.update(actual_count=len(comparison.get('actual',[])),cycle_count=len(comparison.get('cycles',[])))
        if summary is not None and getattr(self,'history_protocol',1)!=2:
            summary.update(actual=[],cycles=[],requires_panel_reload=True,
                error='Оновіть панель Heating AI після встановлення оновлення, щоб завантажити історію.')
        selected=({k:selected.get(k) for k in ('cycle_id','started_at','reference_at','reference_kind','missing_reason')} if selected else None)
        self.publish('sensor.heating_plan_history','Історія прогнозів',**wire(dict(
            protocol=2,request_id=request_id,selected=selected,comparison=summary,
            periods=[dict(cycle_id=k,started_at=history[k]['started_at'],has_forecast=bool(history[k].get('forecast'))) for k in keys],
            energy_cycles=[{**{field:v.get(field) for field in ('start','end','initial_plan','baseline','last_prestart',
                'latest_plan','observed_kwh','meter_kwh','complete','unknown_seconds','automatic_kwh','other_kwh','transition_kwh')},
                'cycle_id':k} for k,v in sorted((self.memory.get('cycle_energy') or {}).items(),
                key=lambda pair:pair[1]['start'],reverse=True)[:3]])))

    def plan_history_requested(self,event,data,kwargs=None,**extra):
        if not isinstance(data,dict) or not isinstance(data.get('request_id'),str) or not 1<=len(data['request_id'])<=128:return
        if 'start' in data or 'end' in data:
            start,end=number(data.get('start')),number(data.get('end'))
            now=self.now().timestamp()
            if start is None or end is None or not 0<end-start<=31*86400 or end>now+60:return
            if data.get('protocol')==2 and data.get('result_id'):
                if not isinstance(data['result_id'],str) or len(data['result_id'])>128:return
                cached=(getattr(self,'history_results',{}) or {}).get(data['result_id'])
                if not cached or cached['start']!=start or cached['end']!=end or now-cached['at']>600:return
                try:page=comparison_page(cached['items'],data.get('offset',0),data['request_id'],data['result_id'],start,end)
                except ValueError:return
                self.publish('sensor.heating_history_page','Історія · сторінка',**wire_values(page))
                return
            if getattr(self,'history_loading',False):
                if data['request_id']==getattr(self,'history_loading_id',None):return
                self.history_queued=dict(data);return
            self.history_loading=True
            self.history_loading_id=data['request_id'];self.history_protocol=data.get('protocol',1)
            history=copy.deepcopy(self.memory.get('temperature_history') or {})
            history['__energy_cycles']=copy.deepcopy(self.memory.get('cycle_energy') or {})
            self.submit_to_executor(self.load_temperature_comparison,start,end,history,data['request_id'],
                                    callback=self.plan_history_loaded)
            return
        key=data.get('cycle_id')
        if key not in (self.memory.get('temperature_history') or {}):return
        self.publish_plan_history(key,data['request_id'])

    def load_temperature_comparison(self,start,end,history,request_id):
        """Executor work: read private evidence without holding the controller thread."""
        try:
            energy=history.pop('__energy_cycles',{})
            result=self.decision_journal.temperature_comparison(start,end,history)
            fields=('start','end','initial_plan','baseline','last_prestart','latest_plan','observed_kwh','meter_kwh',
                'complete','unknown_seconds','automatic_kwh','other_kwh','transition_kwh','quality_report','overrun_review','plan_reviews')
            result['energy_cycles']=[dict(cycle_id=k,**{f:v[f] for f in fields if f in v}) for k,v in energy.items()
                if v['end']>start and v['start']<end]
        except Exception:
            result=dict(start=start,end=end,actual=[],cycles=[],error='Не вдалося прочитати історію. Повторіть завантаження.')
        return dict(result,request_id=request_id)

    def plan_history_loaded(self,kwargs=None,**extra):
        result=(kwargs or extra).get('result')
        self.history_loading=False
        if isinstance(result,dict):
            self.history_comparison=result
            if getattr(self,'history_protocol',1)==2 and 'start' in result and 'end' in result:
                cache=getattr(self,'history_results',{});stamp=self.now().timestamp()
                cache={k:v for k,v in cache.items() if stamp-v['at']<=600}
                while len(cache)>=4:cache.pop(next(iter(cache)))
                result_id=uuid4().hex
                cache[result_id]=dict(at=stamp,start=result['start'],end=result['end'],items=comparison_items(result))
                self.history_results=cache
                page=comparison_page(cache[result_id]['items'],0,result['request_id'],result_id,result['start'],result['end'])
                self.publish('sensor.heating_history_page','Історія · сторінка',**wire_values(page))
            self.publish_plan_history(request_id=result['request_id'])
        queued=getattr(self,'history_queued',None);self.history_queued=None
        if queued:self.plan_history_requested('heating_plan_history',queued)

    def read_outage_schedule(self,now=None):
        source=self.cfg.get('outage_source') or self.cfg.get('outage_calendar')
        record=self.get_state(source,attribute='all') if source else None
        advisory=self.cfg.get('outage_emergency')
        emergency=self.get_state(advisory) if advisory else None
        from heating_outages import planning_schedule
        attrs=record.get('attributes') if isinstance(record,dict) else None
        published=bool(isinstance(attrs,dict) and (attrs.get('today_48half') or attrs.get('tomorrow_48half')))
        return planning_schedule(schedule(record,now or self.now(),self.cfg.get('outage_max_age',10800),emergency=emergency),
                                 self.cfg.get('outage_missing_policy','unknown'),published=published)
    def active_upper(self):
        if (self.cfg.get('night_upper') and
                in_window(self.now(),self.raw('sleep_start') or '23:00',self.raw('sleep_end') or '07:00')):
            return self.val('night_upper',self.val('upper',22.5)-.5)
        return self.val('upper',22.5)
    def track_control_inputs(self,faults):
        previous=getattr(self,'control_inputs_ready',None)
        self.control_input_faults=list(faults)
        self.control_inputs_ready=not faults
        fallback=self.memory.get('temporary_fallback') or {}
        input_fallback=(fallback.get('cause')=='inputs' or
            fallback.get('reason')=='Недоступні дані або команда термостата')
        # Only recovered input failures retry early. Command/control failures
        # retain their existing deadlines and all dispatch guards still apply.
        if (previous is False and not faults and input_fallback and
                not (self.memory.get('control_incident') or {}).get('active') and
                self.cfg.get('allow_actuation') and self.raw('armed')=='on' and
                self.raw('mode')=='Автоматичне керування' and self.grid_state()=='on'):
            self.next_compute=0

    def resume_temporary_fallback(self):
        fallback=self.memory.get('temporary_fallback')
        if (fallback and
                self.raw('mode')=='Автоматичне керування' and self.raw('armed')=='on'):
            self.memory.pop('temporary_fallback',None)
            self.memory['recovering_from_fallback']=True

    def fallback_target(self,current,attrs):
        outside=(self.raw('strategy')=='Тільки в дозволені години' and
                 not in_window(self.now(),self.raw('start') or '23:00',self.raw('end') or '07:00'))
        room=self.source_temperature()[0]
        hot=room is not None and room>=self.active_upper()
        outage=self.read_outage_schedule()
        scheduled_off=bool(outage.get('valid') and blocked(time.time(),time.time()+15,outage.get('outages',[])))
        target=(current-2 if current is not None else number(attrs.get('min_temp')) or 5) if outside or hot or scheduled_off else self.val('native_setpoint',26)
        step=number(attrs.get('target_temp_step')) or .5
        target=round(target/step)*step
        return max(number(attrs.get('min_temp')) or 5,min(number(attrs.get('max_temp')) or 35,target))

    def enter_temporary_fallback(self,reason):
        stamp=time.time()
        if not self.memory.get('temporary_fallback'):
            self.memory['temporary_fallback']=dict(reason=reason,entered_at=stamp,
                retry_at=self.next_compute if self.next_compute>stamp else stamp+300)
            self.memory['recovering_from_fallback']=False
            self.journal('Тимчасовий auto: '+reason,'recovery')
        fallback=self.memory['temporary_fallback']
        if fallback['retry_at']<=stamp:
            fallback.update(last_retry_at=stamp,retry_at=stamp+300)
        self.next_compute=fallback['retry_at']
        self.apply_action('fallback',False)

    def apply_action(self,action,once):
        requested_action=action
        if getattr(self,'update_in_progress',lambda:False)():
            return
        if not self.cfg.get("allow_actuation",False):
            return
        # Inputs may change while a full plan is being computed. Recheck manual
        # permission at dispatch, not only in the earlier decide() call.
        if action in ('heat','fallback') and (self.raw('armed')!='on' or
                self.raw('mode') in ('Спостереження','Вимкнено')):
            action='observe'
        release=action in ("release","observe")
        if release:
            self.memory.pop('temporary_fallback',None)
            self.memory.pop('recovering_from_fallback',None)
        if release and not self.memory.get("owned"):
            return
        if release:
            action="off"
        last=self.memory.get("last_command")
        if self.memory.get('temporary_fallback') and not release and self.grid_state()=='on':
            action='fallback'
        if action=='fallback' and self.grid_state()!='on':
            action='off'
        room=self.source_temperature()[0]
        outage=self.read_outage_schedule()
        scheduled_off=bool(outage.get("valid") and blocked(time.time(),time.time()+15,outage["outages"]))
        if action=='heat' and (scheduled_off or self.grid_state()!='on' or
                              room is None or room>=self.active_upper()):
            action="off"
        plan_forecast=(getattr(self,'decision_context',None) or {}).get('forecast')
        if action=='heat' and not once and isinstance(plan_forecast,dict) and 'heat_segments' in plan_forecast:
            if not any(s['start']<=time.time()<s['end'] for s in plan_forecast['heat_segments']):action='off'
        urgent=release or action=="fallback" or (action=="off" and (
            scheduled_off or self.grid_state()!="on" or room is None or room>=self.active_upper() or
            getattr(self,'decision_context',{}).get('coast_overheat',False) or
            (not once and self.raw('strategy')=='Тільки в дозволені години' and
             not in_window(self.now(),self.raw('start') or '23:00',self.raw('end') or '07:00'))))
        action=held_action(last,self.memory.get('command_at',0),time.time(),action,urgent)
        current=self.val("thermostat",attribute="current_temperature")
        if action!="fallback" and (current is None or not 5<=current<=40 or
                                     self.raw('thermostat') in ('unknown','unavailable',None)):
            if release:
                self.notify_once('Ручна пауза збережена; без температури термостата auto заблокований')
                return
            if self.grid_state()!="on":
                self.notify_once("Немає актуальної температури термостата; команду не виконано, auto без мережі заблокований")
                return
            action="fallback"
        attrs=self.raw("thermostat","all") or {}
        attrs=attrs.get("attributes") or {}
        if action=="fallback":
            mode="auto"
            target=self.fallback_target(current,attrs)
        else:
            mode="heat"
            step=number(attrs.get("target_temp_step")) or .5
            target=round((current+(2 if action=="heat" else -2))/step)*step
            minimum=number(attrs.get("min_temp"))
            maximum=number(attrs.get("max_temp"))
            if (minimum is not None and target<minimum) or (maximum is not None and target>maximum):
                self.notify_once("Уставка ±2 °C виходить за межі термостата; потрібна перевірка")
                if self.grid_state()!="on":
                    return
                mode="auto"
                action="fallback"
                target=self.fallback_target(current,attrs)
        if action=='fallback' and not release:
            stamp=time.time()
            if not self.memory.get('temporary_fallback'):
                faults=list(getattr(self,'control_input_faults',[]))
                labels={'room':'температура кімнати','return':'температура обратки','outside':'температура надворі',
                    'power':'потужність котла','thermostat':'дані термостата','settings':'межі температури або часу',
                    'model':'готовність моделі','grid':'наявність мережі'}
                reason=('Неперевірені дані: '+', '.join(labels.get(k,k) for k in faults)) if faults else 'Недоступні дані або команда термостата'
                self.memory['temporary_fallback']=dict(reason=reason,cause='inputs' if faults else 'control',missing_inputs=faults,
                    entered_at=stamp,retry_at=self.next_compute if self.next_compute>stamp else stamp+300)
            fallback=self.memory['temporary_fallback']
            self.publish('sensor.heating_controller','Тимчасовий auto',friendly_name='Клімат — контролер',
                reason=fallback['reason'],mode=self.raw('mode'),control_method='temporary_native_auto',
                control_state='fallback',incident_id=(self.memory.get('control_incident') or {}).get('incident_id'),
                missing_inputs=fallback.get('missing_inputs'),
                error=(self.memory.get('control_incident') or {}).get('signature'),
                entered_at=fallback.get('entered_at'),last_attempt_at=fallback.get('last_attempt_at'),
                execution_state=(self.memory.get('command_diagnostic') or {}).get('status','unconfirmed'),
                proposed_action='Штатний auto · повторна спроба керування на наступному перерахунку',
                proposed_setpoint=target,recovery_at=datetime.fromtimestamp(fallback['retry_at'],self.climate_tz).isoformat(),
                auto_confirmed=self.raw('thermostat')=='auto')
            if time.time()-fallback.get('last_attempt_at',0)<30:
                return
        command=self.memory.get('command_diagnostic') or {}
        plan=self.memory.get('plan_execution') or {}
        new_plan=(command.get('plan_id'),command.get('plan_calculated_at'))!=(plan.get('plan_id'),plan.get('calculated_at'))
        if new_plan or (command.get('action'),command.get('requested_mode'),command.get('requested_setpoint'))!=(action,mode,target):
            command=dict(requested_at=time.time(),requested_action=requested_action,action=action,
                requested_mode=mode,requested_setpoint=target,status='requested',
                plan_id=plan.get('plan_id'),plan_calculated_at=plan.get('calculated_at'))
            self.memory['command_diagnostic']=command
        self.memory["expected_mode"]=mode
        self.memory["expected_setpoint"]=target
        transition=action!=last
        changed=self.raw("thermostat")!=mode or self.val("thermostat",attribute="temperature")!=target
        try:
            if changed:
                self.command_pending_until=time.time()+20
                if mode=='auto':
                    if self.val('thermostat',attribute='temperature')!=target:
                        self.call_service('climate/set_temperature',entity_id=self.cfg['thermostat'],temperature=target)
                    self.call_service('climate/set_hvac_mode',entity_id=self.cfg['thermostat'],hvac_mode='auto')
                    if self.memory.get('temporary_fallback'):
                        self.memory['temporary_fallback']['last_attempt_at']=time.time()
                else:
                    if self.val('thermostat',attribute='temperature')!=target:
                        self.call_service('climate/set_temperature',entity_id=self.cfg['thermostat'],temperature=target)
                    self.call_service('climate/set_hvac_mode',entity_id=self.cfg['thermostat'],hvac_mode=mode)
                generation=self.memory.get("command_generation",0)+1
                self.memory["command_generation"]=generation
                self.run_in(self.verify_command,12,expected=mode,target=target,generation=generation)
        except Exception:
            command.update(status='service_error',service_error_at=time.time())
            if plan:plan.update(status='command_failed')
            self.decision_event('command_failed',action=action,reason='Помилка виклику сервісу термостата',force=True)
            raise
        if changed:
            command.update(status='service_returned',service_returned_at=time.time())
            if plan:plan.update(status='sent',sent_at=time.time())
            self.decision_event('command_sent',action=action,force=True)
        if transition:
            self.memory["command_at"]=time.time()
            if action=='heat':
                self.memory['heat_session_start_energy']=self.val('energy')
            if action=="fallback":
                self.notify_once("Тимчасовий штатний auto: модель повторить спробу керування на наступному перерахунку")
            if once and action=="heat":
                self.memory.setdefault("once",{})["command_at"]=time.time()
            if action=="heat":
                self.run_in(self.verify_boiler_response,180)
        self.memory.update(owned=not release,last_command=action)
        if not changed and not release and command.get('status')=='requested':
            self.memory['command_generation']=self.memory.get('command_generation',0)+1
            self.verify_command(dict(expected=mode,target=target,generation=self.memory['command_generation']))
        self.save()

    def verify_command(self,kwargs=None,**extra):
        args={**(kwargs or {}),**extra}
        if args.get("generation")!=self.memory.get("command_generation") or not self.memory.get('owned'):
            return
        expected=args.get("expected")
        confirmed=self.val("thermostat",attribute="temperature")
        command=self.memory.get('command_diagnostic') or {}
        self.memory['command_diagnostic']=command
        accepted=(self.raw('thermostat')==expected and confirmed is not None and abs(confirmed-args['target'])<=.1)
        command.update(status='confirmed' if accepted else 'not_confirmed',confirmed_at=time.time(),
                       accepted_mode=self.raw('thermostat'),accepted_setpoint=confirmed)
        plan=self.memory.get('plan_execution') or {}
        if (plan and (command.get('plan_id'),command.get('plan_calculated_at'))==
                (plan.get('plan_id'),plan.get('calculated_at'))):
            plan.update(status='applied' if accepted and expected=='heat' else 'confirmed_fallback' if accepted else 'unconfirmed',
                confirmed_at=time.time())
            if accepted and expected=='heat':
                self.memory['applied_plan']=dict(plan,applied_at=time.time(),action=command.get('action'))
        self.decision_event('command_confirmation',force=True)
        self.save()
        if self.raw("thermostat")!=expected or confirmed is None or abs(confirmed-args["target"])>.1:
            self.notify_once("Термостат не підтвердив команду; тимчасовий auto і повторна спроба керування")
            if self.raw('mode')=='Автоматичне керування' and self.raw('armed')=='on':
                self.enter_temporary_fallback('Термостат не підтвердив режим або уставку')
            self.save()
        elif expected=='heat' and self.memory.pop('recovering_from_fallback',False):
            self.journal('Керування моделі відновлено після тимчасового auto','recovery')
            self.memory.pop('notification',None)
            self.call_service('persistent_notification/dismiss',notification_id='heating_control_fault')
            self.save()

    def verify_boiler_response(self,kwargs=None,**extra):
        if (not self.memory.get("owned") or self.memory.get("last_command")!="heat" or
                self.raw('mode')!='Автоматичне керування' or self.raw('armed')!='on'):
            return
        if self.grid_state()!="on":
            return
        power=self.val("power")
        self.memory.setdefault('command_diagnostic',{}).update(boiler_confirmed=power is not None and power>=(500 if self.cfg.get('power_measurement_policy')=='audited' else 1500),
            power_checked_at=time.time(),confirmed_power_w=power)
        self.decision_event('boiler_response',force=True)
        if power is None or power<(500 if self.cfg.get('power_measurement_policy')=='audited' else 1500):
            self.notify_once("Команду heat прийнято, але нагрів котла за потужністю не підтвердився")
            self.enter_temporary_fallback('Запит нагріву прийнято, але потужність не підтверджена')
        elif self.memory.get("once") and self.memory["once"].get("active_at") is None:
            self.memory["once"]["active_at"]=time.time()
            self.save()

    def climate_changed(self,entity,attribute,old,new,kwargs=None,**extra):
        if (not self.memory.get("owned") or time.time()<self.command_pending_until or
                self.memory.get('temporary_fallback')):
            return
        expected=self.memory.get("expected_mode")
        state=new.get("state") if isinstance(new,dict) else new
        temperature=number((new.get("attributes") or {}).get("temperature")) if isinstance(new,dict) else None
        manual=state not in (expected,"unknown","unavailable",None) or (temperature is not None and temperature!=self.memory.get("expected_setpoint"))
        if manual:
            self.memory["owned"]=False
            self.decision_event('manual_thermostat_change',reason='Ручна зміна термостата: керування призупинено',force=True)
            self.call_service("input_select/select_option",entity_id=self.cfg["mode"],option="Спостереження")
            self.notify_once("Виявлено ручну зміну термостата. Автоматичні команди призупинено.")
            self.save()





class DecisionJournal:
    """Bounded, private, replayable evidence. Never rotates existing telemetry.

    At least seven days are protected. If the disk budget cannot retain them,
    stop adding evidence and report degradation instead of deleting that week.
    """
    DEFAULT_MAX_BYTES=256*1024*1024
    HARD_MAX_BYTES=1024*1024*1024
    CAPACITY_QUANTUM=64*1024*1024
    MIN_FREE_BYTES=2*1024*1024*1024

    def __init__(self,directory,retention_days=14,max_bytes=None):
        self.directory=Path(directory)/'decision_history'
        self.retention=max(7,retention_days)*86400
        self.auto_capacity=max_bytes is None
        self.max_bytes=self.DEFAULT_MAX_BYTES if max_bytes is None else max_bytes
        self.error=None
        self.last_rotation=0
        self.last_compaction=0
        self.used_bytes=None
        self.lock=RLock()

    def health(self):
        stamp=time.time();cached=getattr(self,'space_health',None)
        if not cached or stamp-cached['at']>60:
            try:
                disk=shutil.disk_usage(self.directory.parent)
                cached=dict(at=stamp,free_bytes=disk.free,disk_total_bytes=disk.total)
            except OSError:cached=dict(at=stamp,free_bytes=None,disk_total_bytes=None)
            self.space_health=cached
        return dict(cached,used_bytes=self.used_bytes,max_bytes=self.max_bytes,
            pressure=self.used_bytes is not None and self.used_bytes>=self.max_bytes*.9,
            retention_days=self.retention/86400,protected_days=7,last_compaction=self.last_compaction,error=self.error,
            capacity_policy='retention_headroom' if self.auto_capacity else 'fixed',
            hard_max_bytes=self.HARD_MAX_BYTES if self.auto_capacity else self.max_bytes,
            required_bytes=getattr(self,'capacity_required_bytes',None),
            disk_reserve_bytes=getattr(self,'disk_reserve_bytes',None))

    def _capacity_headroom(self,stamp):
        if not self.auto_capacity:return
        space=self.health();free=space.get('free_bytes');total=space.get('disk_total_bytes')
        if free is None or total is None:return
        self.disk_reserve_bytes=max(self.MIN_FREE_BYTES,int(total*.1))
        if free<=self.disk_reserve_bytes:
            raise OSError('Diagnostic disk reserve reached; protected history retained')
        if self.used_bytes<self.max_bytes*.8:return
        days=max(1.,(stamp-getattr(self,'oldest_at',stamp))/86400)
        # Retain observed daily volume for the configured window, two extra days
        # and 25% headroom; never consume more than 10% of spare physical space.
        required=max(self.used_bytes*1.25,self.used_bytes/days*(self.retention/86400+2)*1.25)
        self.capacity_required_bytes=math.ceil(required)
        desired=math.ceil(required/self.CAPACITY_QUANTUM)*self.CAPACITY_QUANTUM
        ceiling=min(self.HARD_MAX_BYTES,int(self.used_bytes+(free-self.disk_reserve_bytes)*.1))
        self.max_bytes=max(self.max_bytes,min(desired,ceiling))

    def _prepare(self,stamp):
        self.directory.mkdir(parents=True,exist_ok=True)
        if self.used_bytes is None or stamp-self.last_rotation>3600:
            for p in self.directory.iterdir():
                if p.is_file() and p.stat().st_mtime<stamp-self.retention:
                    p.unlink()
            self.last_rotation=stamp
            files=[p.stat() for p in self.directory.iterdir() if p.is_file()]
            self.used_bytes=sum(p.st_size for p in files)
            self.oldest_at=min((p.st_mtime for p in files),default=stamp)
        if self.used_bytes>=self.max_bytes*.9 and stamp-self.last_compaction>60:
            self.last_compaction=stamp
            day=datetime.fromtimestamp(stamp,timezone.utc).strftime('%Y-%m-%d')
            for path in sorted(self.directory.glob('????-??-??.jsonl')):
                if path.stem>=day:continue
                info=path.stat();archive=path.with_suffix('.jsonl.gz');temporary=archive.with_suffix('.tmp');existing=archive.exists()
                try:
                    if not existing:
                        with path.open('rb') as source,gzip.open(temporary,'wb') as target:
                            shutil.copyfileobj(source,target)
                        if temporary.stat().st_size>=info.st_size:continue
                        with path.open('rb') as source,gzip.open(temporary,'rb') as restored:
                            if hashlib.file_digest(source,'sha256').digest()!=hashlib.file_digest(restored,'sha256').digest():
                                raise OSError('Diagnostic compression checksum mismatch')
                        os.utime(temporary,(info.st_atime,info.st_mtime));temporary.replace(archive)
                    else:
                        with path.open('rb') as source,gzip.open(archive,'rb') as restored:
                            if hashlib.file_digest(source,'sha256').digest()!=hashlib.file_digest(restored,'sha256').digest():
                                raise OSError('Existing diagnostic archive differs; original retained')
                    path.unlink();self.used_bytes-=info.st_size
                    if not existing:self.used_bytes+=archive.stat().st_size
                finally:
                    temporary.unlink(missing_ok=True)
        self._capacity_headroom(stamp)
        if self.used_bytes>=self.max_bytes:
            raise OSError('Diagnostic disk budget exhausted; protected history retained')

    def snapshot(self,value,stamp):
        with self.lock:
            return self._snapshot(value,stamp)

    def _snapshot(self,value,stamp):
        raw=json.dumps(value,ensure_ascii=False,sort_keys=True,separators=(',',':'),allow_nan=False).encode('utf-8')
        digest=hashlib.sha256(raw).hexdigest()
        path=self.directory/(digest+'.json.gz')
        if not path.exists():
            self._prepare(stamp)
            compressed=gzip.compress(raw,mtime=0)
            if self.used_bytes+len(compressed)>self.max_bytes:
                raise OSError('Diagnostic disk budget exhausted; snapshot not written')
            temporary=path.with_suffix('.tmp')
            temporary.write_bytes(compressed)
            temporary.replace(path)
            self.used_bytes+=len(compressed)
        os.utime(path,(stamp,stamp))
        return digest

    def read_snapshot(self,digest):
        if len(digest)!=64 or any(c not in '0123456789abcdef' for c in digest):
            raise ValueError('Invalid snapshot identifier')
        raw=gzip.decompress((self.directory/(digest+'.json.gz')).read_bytes())
        if hashlib.sha256(raw).hexdigest()!=digest:
            raise ValueError('Snapshot checksum mismatch')
        return json.loads(raw)

    def temperature_comparison(self,start,end,stored=None):
        """Read original issued snapshots and observations; never run a thermal model.

        Archive reads are on demand in an executor. Only selected forecast
        snapshots are opened; private model/configuration fields never leave here.
        """
        if not all(isinstance(v,(int,float)) and math.isfinite(v) for v in (start,end)) or not 0<end-start<=31*86400:
            raise ValueError('Invalid comparison interval')
        actual={};cycles={};issues=[];warnings=set();observations={};markers=[]
        def observe(at,value,valid):
            at=number(at);value=number(value)
            if at is not None and start-1200<=at<=end:
                actual[at]=dict(ts=at,temperature=value if valid and value is not None and 5<=value<=40 else None)
        def stamp(value):
            try:return datetime.fromisoformat(value.replace('Z','+00:00')).timestamp()
            except (ValueError,TypeError,AttributeError):return None
        curve=temperature_curve
        def frozen(value):
            if not isinstance(value,dict):return None
            rows=curve(value.get('curve') or [])
            return dict(**{k:value.get(k) for k in ('at','plan_id','lead_hours','model_version','snapshot_id')},
                curve=rows,source=value.get('source','persisted')) if rows else None
        # Telemetry persists actual control temperatures even on days with no heat.
        telemetry=self.directory.parent/'telemetry.jsonl'
        if telemetry.exists():
            with telemetry.open() as stream:
                for line in stream:
                    try:
                        row=json.loads(line)
                        observe(row.get('ts'),row.get('room'),row.get('room_valid',row.get('quality')) in (True,'true','on'))
                        ts=number(row.get('ts'))
                        if ts is not None and start-1200<=ts<=end:
                            observations[ts]=row
                            policy=row.get('comfort_policy') or {}
                            if all(number(policy.get(k)) is not None for k in ('target','lower','upper')):
                                _,lo,hi,_=comfort_at(datetime.fromtimestamp(ts,ZoneInfo('Europe/Kyiv')),policy)
                                actual[ts].update(lower=lo,upper=hi)
                    except (ValueError,AttributeError):warnings.add('malformed_telemetry')
        last=None;last_model=None
        def mark(kind,a,b,**attrs):
            if b<start or a>end:return
            if markers and markers[-1]['kind']==kind and markers[-1]['end']==a and not attrs:
                markers[-1]['end']=min(end,b)
            else:markers.append(dict(kind=kind,start=max(start,a),end=min(end,b),**attrs))
        for ts,row in sorted(observations.items()):
            version=row.get('thermal_model_version')
            if last_model is not None and version is not None and version!=last_model:
                mark('model_change',ts,ts,model_version=version)
            if version is not None:last_model=version
            if last:
                a,before=last
                if 0<ts-a<=420:
                    if before.get('grid')=='off':mark('grid_off',a,ts)
                    if (before.get('power_quality')=='measured' and before.get('power_estimated') not in (True,'true')
                            and number(before.get('power')) is not None and before['power']>=1.5):
                        mark('actual_heat',a,ts)
                    if before.get('grid') not in ('on','off') or before.get('power_quality') in ('unknown','contradictory'):
                        mark('unknown',a,ts)
                else:mark('unknown',a,ts)
            if row.get('grid')=='unknown' or row.get('power_quality') in ('unknown','contradictory'):
                mark('unknown',ts,ts)
            last=ts,row
        first=datetime.fromtimestamp(start-36*3600,timezone.utc).strftime('%Y-%m-%d')
        last=datetime.fromtimestamp(end,timezone.utc).strftime('%Y-%m-%d')
        paths={p.name[:10]:p for p in self.directory.glob('????-??-??.jsonl.gz') if first<=p.name[:10]<=last}
        paths.update({p.name[:10]:p for p in self.directory.glob('????-??-??.jsonl') if first<=p.name[:10]<=last})
        previous=None
        for day,path in sorted(paths.items()):
            try:
                with (gzip.open(path,'rt') if path.suffix=='.gz' else path.open()) as stream:
                    for line in stream:
                        try:
                            record=json.loads(line);at=stamp(record.get('recorded_at'))
                            if at is None or not start-36*3600<=at<=end:continue
                            if record.get('external_attributes') in (True,'true'):
                                record=self.read_snapshot(record['attributes_snapshot_id'])
                            if record.get('event')=='temperature_forecast' and record.get('forecast_snapshot_id'):
                                issues.append(dict(at=at,snapshot_id=record['forecast_snapshot_id'],source='archive'))
                            elif record.get('snapshot_id') and record.get('event')=='full_recompute':
                                issues.append(dict(at=at,snapshot_id=record['snapshot_id'],source='archive'))
                            reference=number((record.get('reserve') or {}).get('cycle_start'))
                            key=record.get('cycle_id')
                            if key and reference is not None and start-86400<=reference<=end and key not in cycles:
                                cycles[key]=dict(cycle_id=key,started_at=reference,reference_at=reference,
                                    reference_kind='window_start',start_known=False,forecast=None)
                            if 'power_w' not in record:continue
                            power=number(record.get('power_w'))
                            measured=record.get('power_quality')=='measured' and record.get('power_estimated') not in (True,'true')
                            continuous=bool(previous and 0<at-previous['at']<=45 and previous['measured'] and
                                            previous['power'] is not None and previous['power']>=1500)
                            if key and power is not None and power>=1500 and measured and key not in cycles and not continuous:
                                edge=bool(previous and 0<at-previous['at']<=45 and previous['measured'] and
                                          previous['power'] is not None and previous['power']<1500)
                                cycles[key]=dict(cycle_id=key,started_at=at,start_known=edge,forecast=None)
                            elif key in cycles and power is not None and power>=1500 and measured and not continuous and not cycles[key].get('physical_started_at'):
                                edge=bool(previous and 0<at-previous['at']<=45 and previous['measured'] and previous['power'] is not None and previous['power']<1500)
                                cycles[key].update(physical_started_at=at,start_known=edge)
                            previous=dict(at=at,power=power,measured=measured)
                        except (ValueError,TypeError,KeyError,OSError,AttributeError,EOFError,zlib.error):warnings.add('incomplete_journal')
            except (OSError,EOFError,zlib.error):warnings.add('incomplete_journal')
        # Select an original issue at least12h before the observed heating edge.
        # A recent replay or a newly calculated curve is never eligible.
        for entry in cycles.values():
            at=entry.get('reference_at',entry['started_at'])
            if not entry['start_known'] and entry.get('reference_kind')!='window_start':continue
            entry['forecast']=select_temperature_forecast(issues,at,self,warnings)
        for key,value in (stored or {}).items():
            if not isinstance(value,dict) or number(value.get('started_at')) is None:continue
            for point in value.get('actual') or []:
                observe(point.get('ts'),point.get('temperature'),point.get('temperature') is not None)
            if value.get('forecast'):
                cycles[key]={k:value.get(k) for k in ('cycle_id','started_at','start_known','reference_at','reference_kind','physical_started_at')}
                cycles[key]['forecast']=frozen(value['forecast'])
            elif key not in cycles:
                cycles[key]=dict(cycle_id=key,started_at=value['started_at'],start_known=value.get('start_known'),forecast=None)
        selected=sorted((c for c in cycles.values() if start-86400<=c['started_at']<=end),key=lambda c:c['started_at'])
        return dict(start=start,end=end,actual=sorted(actual.values(),key=lambda p:p['ts']),cycles=selected,
                    markers=markers,warnings=sorted(warnings))

    def append(self,record,snapshot=None,stamp=None):
        stamp=time.time() if stamp is None else stamp
        with self.lock:
            result=dict(record)
            snapshot_failure=(snapshot or {}).get('snapshot_failure') if isinstance(snapshot,dict) else None
            if snapshot_failure:
                result.update(storage_degraded=True,storage_error=snapshot_failure)
            result.setdefault('schema_version',1)
            result.setdefault('decision_id',uuid4().hex)
            result.setdefault('recorded_at',datetime.fromtimestamp(stamp,timezone.utc).isoformat())
            try:
                self._prepare(stamp)
                if snapshot is not None:
                    result['snapshot_id']=self.snapshot(snapshot,stamp)
                # Move *actual* overflow evidence, never silently truncate a reason.
                raw=json.dumps(result,ensure_ascii=False,separators=(',',':'),allow_nan=False).encode('utf-8')
                if len(raw)>8192:
                    digest=self.snapshot(result,stamp)
                    result={k:result.get(k) for k in ('schema_version','decision_id','recorded_at','plan_id','parent_plan_id','cycle_id','action','snapshot_id',
                        'incident_id','control_state','execution_state','reason_code','power_quality','source_timestamp',
                        'physical_mode','equipment_version','execution_evidence')}
                    result.update(external_attributes=True,attributes_snapshot_id=digest,
                                  reason=str(record.get('reason') or '')[:256] or 'Повний запис у локальному знімку')
                    raw=json.dumps(result,ensure_ascii=False,separators=(',',':')).encode('utf-8')
                day=datetime.fromtimestamp(stamp,timezone.utc).strftime('%Y-%m-%d')
                if self.used_bytes+len(raw)+1>self.max_bytes:
                    raise OSError('Diagnostic disk budget exhausted; record not written')
                with (self.directory/(day+'.jsonl')).open('ab') as stream:
                    stream.write(raw+b'\n')
                self.used_bytes+=len(raw)+1
                os.utime(self.directory/(day+'.jsonl'),(stamp,stamp))
                self.error=snapshot_failure
            except (OSError,ValueError,TypeError) as exc:
                self.error=str(exc)
                # On disk failure keep a bounded HA record with an explicit failure.
                result=dict(schema_version=1,decision_id=result['decision_id'],
                    recorded_at=result['recorded_at'],plan_id=record.get('plan_id'),
                    action=record.get('action'),storage_degraded=True,storage_error=self.error,
                    reason=record.get('reason') if len(str(record.get('reason','')).encode('utf-8'))<2048 else
                        'Причина не збережена: переповнення атрибутів і помилка локального журналу')
            return result


def confirmed_morning(memory,now,settings,power,energy,grid,automatic,power_source):
    """Integrate adjacent confirmed observations; never command elapsed time.

    A restart invalidates the previous observation in initialize(). Totals survive.
    Both endpoints must have real balance power and identical execution ownership.
    """
    cycle=heating_cycle(now,settings)
    if not cycle:
        return dict(confirmed_completed_minutes=0.,confirmed_morning_kwh=None,morning_elapsed_minutes=0.)
    ledger=memory.setdefault('morning_execution',{})
    entry=ledger.setdefault(cycle['cycle_id'],dict(minutes=0.,kwh=0.,manual_minutes=0.,manual_kwh=0.))
    stamp=now.timestamp();previous=memory.get('morning_observation')
    inside=bool(cycle['morning_start'] is not None and cycle['morning_start']<=stamp<=cycle['morning_end'])
    valid=inside and grid=='on' and power_source=='balance' and power is not None and power>=1500
    current=dict(ts=stamp,cycle_id=cycle['cycle_id'],valid=valid,automatic=automatic,
                 energy=energy,power=power)
    contiguous=bool(previous and previous['cycle_id']==cycle['cycle_id'] and
        previous['valid'] and valid and 0<stamp-previous['ts']<=45 and
        previous['automatic']==automatic)
    if contiguous:
        minutes=(stamp-previous['ts'])/60
        prefix='' if automatic else 'manual_'
        entry[prefix+'minutes']+=minutes
        before=number(previous.get('energy'));after=number(energy)
        if before is not None and after is not None and 0<=after-before<=10.5*minutes/60:
            entry[prefix+'kwh']+=after-before
        elif automatic:
            entry['unknown_energy']=True
    memory['morning_observation']=current
    # Cycle keys are dates; keep a bounded fortnight without touching telemetry.
    for key in sorted(ledger)[:-14]:
        del ledger[key]
    return dict(confirmed_completed_minutes=round(entry['minutes'],3),
                confirmed_morning_kwh=round(entry['kwh'],5) if number(energy) is not None and not entry.get('unknown_energy') else None,
                manual_completed_minutes=round(entry['manual_minutes'],3),
                manual_morning_kwh=round(entry['manual_kwh'],5),
                morning_elapsed_minutes=round(entry['minutes'],3) if automatic and valid else 0.)
