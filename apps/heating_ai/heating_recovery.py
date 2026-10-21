"""Temporary native-auto recovery and verified thermostat commands."""
import time
from datetime import datetime, timezone
from pathlib import Path
from threading import RLock
from uuid import uuid4
import gzip
import hashlib
import json
import os
from heating_model import number,in_window,heating_cycle
from heating_outages import schedule,blocked

class HeatingRecovery:
    async def verify_decision_history(self,kwargs=None,**extra):
        """Read Recorder itself; successful set_state is not persistence proof.

        This bounded asynchronous request cannot delay the thermostat tick.
        No include/exclude filters or retention settings are changed.
        """
        try:
            rows=await self.get_history(entity_id='sensor.heating_decision',
                start_time=datetime.fromtimestamp(time.time()-1200,timezone.utc),
                minimal_response=False,no_attributes=False,significant_changes_only=False)
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
        self.publish('sensor.heating_diagnostics',
            'Журнал доступний' if status=='verified_same_state_attributes' else 'Перевірте історію Recorder',
            recorder_status=status,storage_degraded=bool(getattr(self,'decision_journal',None) and self.decision_journal.error),
            note='Перевіряється повна історія атрибутів; фільтри й термін зберігання не змінюються')

    def decision_event(self,event,action=None,reason=None,force=False,snapshot=None):
        journal=getattr(self,'decision_journal',None)
        if journal is None:
            return
        try:
            now=self.now();stamp=now.timestamp()
            context=getattr(self,'decision_context',{})
            settings=context.get('settings',{})
            forecast=context.get('forecast') or {}
            reserve=forecast.get('morning_reserve',{})
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
            actual=self.val('power')
            command=self.memory.get('command_diagnostic',{})
            reference=self.memory.get('plan_reference') or {}
            record=dict(event=event,timezone=str(self.climate_tz),app_version=getattr(self,'release_version',None),
                plan_id=forecast.get('plan_id') or reference.get('plan_id'),
                parent_plan_id=forecast.get('parent_plan_id'),cycle_id=reserve.get('cycle_id'),
                reason=reason or context.get('reason') or event,
                action=action or context.get('action'),planned_demand=context.get('planned_demand'),
                demand=context.get('demand'),inhibitions=context.get('inhibitions',[]),
                mode=mode,armed=armed,permitted=bool(self.cfg.get('allow_actuation')) and armed,
                grid=self.grid_state(),grid_evidence='existing inverter connection, input voltage and heartbeat guards',
                power_w=actual,energy_counter_kwh=self.val('energy'),power_source=getattr(self,'power_source','unknown'),
                power_estimated=getattr(self,'power_source','').startswith('fallback'),
                physical_heating=actual is not None and actual>=1500,
                execution='automatic' if mode=='Автоматичне керування' and armed and self.memory.get('owned') and self.memory.get('last_command')=='heat' else 'manual_or_native' if actual is not None and actual>=1500 else 'idle',
                measurements=context.get('measurements',{}),sources=sources,
                policy={k:settings.get(k) for k in ('target','lower','upper','night_target','night_lower','night_upper','sleep_start','sleep_end','start','end','strategy','morning_min_minutes')},
                reserve=reserve,coast_peak=context.get('coast_peak'),
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
            signature['source_quality']={k:(v['quality'],v['available']) for k,v in sources.items()}
            fingerprint=hashlib.sha256(json.dumps(signature,sort_keys=True,default=str).encode()).hexdigest()
            if not force and fingerprint==getattr(self,'decision_fingerprint',None):
                return
            self.decision_fingerprint=fingerprint
            compact=journal.append(record,snapshot,stamp)
            self.memory['last_decision_id']=compact['decision_id']
            self.publish('sensor.heating_decision',record['action'] or 'unknown',**compact)
            self.publish('sensor.heating_diagnostics','Помилка збереження' if journal.error else 'Журнал доступний',
                         storage_degraded=bool(journal.error),error=journal.error,retention_days=journal.retention/86400,
                         attribute_budget_bytes=8192,last_decision_id=compact['decision_id'],
                         recorder_status=self.memory.get('decision_recorder_status','pending'))
        except Exception as exc:
            # Diagnostics must not trigger native-auto fallback or stop control.
            self.log('Decision diagnostics failed: %s',exc,level='WARNING')
            try:
                self.publish('sensor.heating_diagnostics','Помилка збереження',storage_degraded=True,error=str(exc))
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

    def validate_reserve_predictions(self,now,room,power,quality,forecast=None):
        """Prospective matched-horizon validation, separate from one-hour MAE."""
        stamp=now.timestamp();pending=self.memory.setdefault('reserve_predictions',[])
        errors=self.memory.setdefault('reserve_errors',[])
        for prediction in pending:
            planned=any(s['start']<=stamp<s['end'] for s in prediction['segments'])
            actual=power is not None and power>=1500
            if (not quality or self.grid_state()!='on' or
                    self.raw('mode')!='Автоматичне керування' or self.raw('armed')!='on' or
                    prediction['model']!=self.model or planned!=actual):
                prediction['valid']=False
            if prediction['ts']<=stamp and prediction['valid'] and stamp-prediction['ts']<=45 and room is not None:
                energy=number(self.val('energy'))
                start_energy=number(prediction.get('start_energy'))
                expected=prediction.get('expected_energy',0.)
                matched=(energy is not None and start_energy is not None and
                         abs(energy-start_energy-expected)<=max(.05,expected*.05))
                if matched:
                    errors.append(dict(hours=prediction['hours'],error=prediction['temperature']-room,
                        validated=True,at=stamp,model_version=self.model.get('version')))
        pending[:]=[p for p in pending if p['ts']>stamp and p['valid']]
        errors[:]=[r for r in errors[-240:] if r.get('at',stamp)>stamp-30*86400 and r.get('model_version')==self.model.get('version')]
        if forecast and stamp-self.memory.get('reserve_validation_at',0)>=3600:
            self.memory['reserve_validation_at']=stamp
            rows=forecast.get('_calculation_curve',forecast['curve'])
            segments=[s for r in rows for s in r['heat_segments']]
            # Validation needs on/off intervals, not a duplicate of every 5-min
            # modulation sample in every pending horizon saved to state.json.
            intervals=[]
            for segment in sorted(segments,key=lambda s:s['start']):
                if intervals and segment['start']<=intervals[-1]['end']+.001:
                    intervals[-1]['end']=max(intervals[-1]['end'],segment['end'])
                else:
                    intervals.append(dict(start=segment['start'],end=segment['end']))
            for hours in (6,12,24,36):
                point=next((r for r in rows if r['ts']>=stamp+hours*3600),None)
                if point:
                    pending.append(dict(ts=point['ts'],hours=(point['ts']-stamp)/3600,
                        temperature=point['temperature'],model=dict(self.model),valid=True,segments=intervals,
                        start_energy=self.val('energy'),expected_energy=sum(s.get('kwh',0.)*
                            max(0.,min(s['end'],point['ts'])-s['start'])/(s['end']-s['start'])
                            for s in segments if s['start']<point['ts'])))

    def read_outage_schedule(self,now=None):
        source=self.cfg.get('outage_source') or self.cfg.get('outage_calendar')
        record=self.get_state(source,attribute='all') if source else None
        advisory=self.cfg.get('outage_emergency')
        emergency=self.get_state(advisory) if advisory else None
        return schedule(record,now or self.now(),self.cfg.get('outage_max_age',10800),emergency=emergency)
    def active_upper(self):
        if (self.cfg.get('night_upper') and
                in_window(self.now(),self.raw('sleep_start') or '23:00',self.raw('sleep_end') or '07:00')):
            return self.val('night_upper',self.val('upper',22.5)-.5)
        return self.val('upper',22.5)
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
        sleep_hot=self.cfg.get('night_upper') and room is not None and room>=self.active_upper()
        target=(current-2 if current is not None else number(attrs.get('min_temp')) or 5) if outside or sleep_hot else self.val('native_setpoint',26)
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
        self.apply_action('fallback',False)

    def apply_action(self,action,once):
        requested_action=action
        if getattr(self,'update_in_progress',lambda:False)():
            return
        if not self.cfg.get("allow_actuation",False):
            return
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
        elapsed=time.time()-self.memory.get("command_at",0)
        room=self.source_temperature()[0]
        outage=self.read_outage_schedule()
        scheduled_off=bool(outage.get("valid") and blocked(time.time(),time.time()+15,outage["outages"]))
        if scheduled_off and action=="heat":
            action="off"
        urgent=release or action=="fallback" or (action=="off" and (
            scheduled_off or self.grid_state()!="on" or (room is not None and room>=self.active_upper()) or
            (not once and self.raw('strategy')=='Тільки в дозволені години' and
             not in_window(self.now(),self.raw('start') or '23:00',self.raw('end') or '07:00'))))
        if action!=last and not urgent and ((last=="heat" and elapsed<600) or (last=="off" and action=="heat" and elapsed<900)):
            action=last
        current=self.val("thermostat",attribute="current_temperature")
        if action!="fallback" and (current is None or not 5<=current<=40 or
                                     self.raw('thermostat') in ('unknown','unavailable',None)):
            if self.grid_state()!="on":
                self.notify_once("Немає актуальної температури термостата; команду не виконано, auto без мережі заблокований")
                return
            action="fallback"
        attrs=self.raw("thermostat","all") or {}
        attrs=attrs.get("attributes",{})
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
                self.memory['temporary_fallback']=dict(reason='Недоступні дані або команда термостата',
                    entered_at=stamp,retry_at=self.next_compute if self.next_compute>stamp else stamp+300)
            fallback=self.memory['temporary_fallback']
            self.publish('sensor.heating_controller','Тимчасовий auto',friendly_name='Клімат — контролер',
                reason=fallback['reason'],mode=self.raw('mode'),control_method='temporary_native_auto',
                proposed_action='Штатний auto · повторна спроба керування на наступному перерахунку',
                proposed_setpoint=target,recovery_at=datetime.fromtimestamp(fallback['retry_at'],self.climate_tz).isoformat(),
                auto_confirmed=self.raw('thermostat')=='auto')
            if time.time()-fallback.get('last_attempt_at',0)<30:
                return
        command=self.memory.get('command_diagnostic',{})
        if (command.get('action'),command.get('requested_mode'),command.get('requested_setpoint'))!=(action,mode,target):
            command=dict(requested_at=time.time(),requested_action=requested_action,action=action,
                requested_mode=mode,requested_setpoint=target,status='requested')
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
            self.decision_event('command_failed',action=action,reason='Помилка виклику сервісу термостата',force=True)
            raise
        if changed:
            command.update(status='service_returned',service_returned_at=time.time())
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
        self.save()

    def verify_command(self,kwargs=None,**extra):
        args={**(kwargs or {}),**extra}
        if args.get("generation")!=self.memory.get("command_generation") or not self.memory.get('owned'):
            return
        expected=args.get("expected")
        confirmed=self.val("thermostat",attribute="temperature")
        command=self.memory.setdefault('command_diagnostic',{})
        accepted=(self.raw('thermostat')==expected and confirmed is not None and abs(confirmed-args['target'])<=.1)
        command.update(status='confirmed' if accepted else 'not_confirmed',confirmed_at=time.time(),
                       accepted_mode=self.raw('thermostat'),accepted_setpoint=confirmed)
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
        self.memory.setdefault('command_diagnostic',{}).update(boiler_confirmed=power is not None and power>=1500,
            power_checked_at=time.time(),confirmed_power_w=power)
        self.decision_event('boiler_response',force=True)
        if power is None or power<1500:
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
        temperature=number(new.get("attributes",{}).get("temperature")) if isinstance(new,dict) else None
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
    def __init__(self,directory,retention_days=14,max_bytes=256*1024*1024):
        self.directory=Path(directory)/'decision_history'
        self.retention=max(7,retention_days)*86400
        self.max_bytes=max_bytes
        self.error=None
        self.last_rotation=0
        self.used_bytes=None
        self.lock=RLock()

    def _prepare(self,stamp):
        self.directory.mkdir(parents=True,exist_ok=True)
        if self.used_bytes is None or stamp-self.last_rotation>3600:
            for p in self.directory.iterdir():
                if p.is_file() and p.stat().st_mtime<stamp-self.retention:
                    p.unlink()
            self.last_rotation=stamp
            self.used_bytes=sum(p.stat().st_size for p in self.directory.iterdir() if p.is_file())
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
                    result={k:result.get(k) for k in ('schema_version','decision_id','recorded_at','plan_id','parent_plan_id','cycle_id','action','snapshot_id')}
                    result.update(external_attributes=True,attributes_snapshot_id=digest,
                                  reason='Повний запис перевищив 8 КіБ; див. локальний знімок')
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
