"""Home Assistant AppDaemon adapter. Deployment defaults to strict observation."""
from appdaemon.plugins.hass.hassapi import Hass
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo
from pathlib import Path
from threading import RLock
from uuid import uuid4
import json
import os
import statistics
import time
import calendar
import shutil
import hashlib
from heating_fallback import (fallback_power,repair_samples,estimated_power,aligned_power,phase_residual,combine_power,integrate_observation)
from heating_outages import blocked, signature as outage_signature
from heating_model import (DEFAULT_MODEL, BOUNDS, number, in_window, plan, coast,
                           nominal_mode, decide, learn, validate_candidate, transitions, HeatingPowerEstimator,
                           learn_modulation, modulation_power,comfort_at,coast_exceeds_profile,learn_power_modes,power_profile)
from heating_recovery import HeatingRecovery,DecisionJournal,confirmed_morning
from heating_stability import stable_plan,replay,stress_scenarios,recommend_power
from heating_environment import (observe_floor,observe_gain,reliability,daylight,anchor_weather,fit_ventilation,ventilation_expectation)
from heating_ui import (install_panel, project_fireplace, verify_installation,
                        system_fields, system_values, validate_system_settings)

class HeatingClimate(HeatingRecovery,Hass):
    def initialize(self):
        self.cfg=dict(self.args)
        if self.update_in_progress():
            self.run_in(self.initialize_after_update,5)
            return
        verify_installation()
        self.climate_tz=ZoneInfo(self.cfg.get("timezone","Europe/Kiev"))
        self.directory=Path(self.cfg.get("data_dir","/config/heating_data"))
        self.directory.mkdir(parents=True,exist_ok=True)
        self.statefile=self.directory/"state.json"
        self.telemetry=self.directory/"telemetry.jsonl"
        self.memory=self.load_state()
        self.migrate_equipment_state()
        self.model=self.memory.get("model",dict(DEFAULT_MODEL))
        self.decision_journal=DecisionJournal(self.directory)
        self.run_every(self.verify_decision_history,self.datetime()+timedelta(seconds=420),600)
        self.memory.pop('morning_observation',None)  # no integration across restart gaps
        # Unobserved downtime invalidates prospective error validation.
        pending_count=len(self.memory.get('reserve_predictions',[]))
        if pending_count:
            rejections=self.memory.setdefault('forecast_validation_rejections',{})
            rejections['restart_gap']=rejections.get('restart_gap',0)+pending_count
        self.memory['reserve_predictions']=[]
        self.memory.pop('peak_watch',None)
        self.memory.pop('peak_observation',None)
        self.memory.pop('cycle_energy_observation',None)
        self.memory['predictions']=[]  # never validate across an unobserved restart gap
        self.release_version=json.loads((Path(__file__).resolve().parent/'release.json').read_text())['version']
        self.restore_system_settings()
        self.memory.setdefault("energy_bins",list(self.cfg.get("energy_history",[])))
        self.memory.setdefault("energy_last",self.cfg.get("energy_seed"))
        self.samples=[]
        if self.telemetry.exists():
            for line in self.telemetry.read_text().splitlines()[-4032:]:
                try:
                    row=json.loads(line)
                    if row["ts"]>=time.time()-14*86400:
                        if row.get("measurement_version")!=3:
                            row["quality"]=False
                        self.samples.append(row)
                except (KeyError,ValueError):
                    continue
        self.power_estimator=HeatingPowerEstimator(window=10. if self.cfg.get('power_measurement_policy')=='audited' else 20.,confirm=10.,threshold=500. if self.cfg.get('power_measurement_policy')=='audited' else 2000.)
        self.memory['modulation_profile']=learn_modulation(self.samples)
        self.run_diagnostic('power_calibration',self.refresh_equipment_models)
        if self.memory.get('modulation_controller_version')!=1:
            self.memory['predictions']=[]
            self.memory['modulation_controller_version']=1
        self.measurement_valid_since=time.time()
        self.weather=self.memory.get("weather",[])
        if not self.memory.get('environment_version'):
            # Reconstruct only the observer; never replace historical telemetry
            # or energy. The return probe was installed on 07.10.2026.
            thermal={}
            solar={}
            for row in self.samples:
                if row['ts']<datetime(2026,10,7,tzinfo=self.climate_tz).timestamp():
                    continue
                if row.get('quality') and all(number(row.get(k)) is not None for k in ('room','return','power','outside')):
                    thermal,estimated=observe_floor(thermal,row['ts'],row['room'],row['return'],
                        row['power'],row.get('grid','unknown'),self.model)
                    solar=observe_gain(solar,row['ts'],row['room'],estimated,row['outside'],
                        row.get('fire',0),row['power']>=1.5,self.model,self.climate_tz)
            self.memory['floor_observer']=thermal
            self.memory['solar_observer']=solar
            self.memory['environment_version']=1
            self.memory['predictions']=[]
        if not self.memory.get('state_load_error'):
            self.apply_energy_recovery()
        self.power_source="unavailable"
        self.fallback_interval=False
        self.stage="off"
        self.candidate=None
        self.candidate_at=0
        self.next_compute=0
        self.last_sample=0
        self.fan_command_at=0
        self.command_pending_until=0
        # Serialize the two summary publishers without locking thermal planning.
        self.ai_summary_lock=RLock()
        self.ai_summary_attributes=None
        ventilation=self.cfg.get('ventilation',{})
        if isinstance(ventilation,dict):
            for binding in ventilation.values():
                if isinstance(binding,dict):
                    for entity in {binding.get('entity'),binding.get('power_entity')}-{None}:
                        if isinstance(entity,str):self.listen_state(self.ventilation_changed,entity)
        self.ventilation_tick()
        self.listen_event(self.weather_event,"heating_weather_forecast")
        self.listen_event(self.night_event,"heating_fan_night_request")
        self.listen_event(self.system_settings_changed,'heating_system_settings')
        self.listen_event(self.plan_history_requested,'heating_plan_history')
        self.listen_state(self.proposal_changed,self.cfg["ai_proposal"])
        self.listen_state(self.ai_report_changed,self.cfg["ai_report"])
        self.listen_state(self.rollback,self.cfg["rollback"])
        self.listen_state(self.arm_changed,self.cfg["restore_once"])
        self.listen_state(self.climate_changed,self.cfg["thermostat"],attribute="all")
        for key in ("target","lower","upper","start","end","strategy","mode","armed"):
            self.listen_state(self.settings_changed,self.cfg[key])
        for key in ('night_target','night_lower','night_upper','sleep_start','sleep_end'):
            if self.cfg.get(key):
                self.listen_state(self.settings_changed,self.cfg[key])
        self.listen_state(self.balance_changed,self.cfg["power_balance"])
        if self.cfg.get("power_fault"):
            self.listen_state(self.balance_changed,self.cfg["power_fault"])
        for key in ("outage_calendar", "outage_source", "outage_emergency"):
            if self.cfg.get(key):
                self.listen_state(self.outages_changed,self.cfg[key],attribute="all")
        self.run_every(self.tick,self.datetime()+timedelta(seconds=2),15)
        self.runtime_ready=True
        self.publish_system_settings()
        self.log("Climate controller loaded; allow_actuation=%s",self.cfg.get("allow_actuation",False))
        try:
            panel = install_panel(self.cfg)
            if panel:
                self.publish("sensor.heating_ui", panel.pop("version"),
                             friendly_name="Клімат — вебінтерфейс", icon="mdi:home-thermometer",
                             **panel)
        except (OSError, ValueError) as exc:
            self.log("Heating panel unavailable: %s", str(exc), level="WARNING")
    def initialize_after_update(self,kwargs=None,**extra):
        self.initialize()
    def update_in_progress(self):
        entity=self.cfg.get('hacs_update_entity','update.heating_ai_appdaemon_update')
        state=self.get_state(entity,attribute='all') or {}
        progress=state.get('attributes',{}).get('in_progress',False)
        return progress is True or (number(progress) or 0)>0
    def terminate(self):
        # Helpers and thermostat modes are not changed by an app reload.
        if getattr(self,'runtime_ready',False):
            self.save()
    def apply_energy_recovery(self):
        path=self.directory/"recovery-20261009.json"
        if not path.exists():
            return
        recovery=json.loads(path.read_text())
        recovery["intervals"]=[dict(start=a,end=b,power_w=p) for a,b,p in recovery["intervals"]]
        key=recovery["id"]
        if key in self.memory.get("applied_recoveries",{}):
            return
        current=self.val("energy")
        expected=recovery["baseline_energy"]+recovery["kwh"]
        if current is None or current < expected-.001:
            self.log("Recovery awaits calibrated energy counter",level="WARNING")
            return
        backup=self.directory/"deployment_backups"/key
        backup.mkdir(parents=True,exist_ok=True)
        for file in (self.statefile,self.telemetry):
            target=backup/file.name
            if file.exists() and not target.exists():
                shutil.copy2(file,target)
        rows=[json.loads(line) for line in self.telemetry.read_text().splitlines() if line.strip()]
        changed=repair_samples(rows,recovery)
        temp=self.telemetry.with_suffix(".recovery.tmp")
        temp.write_text("".join(json.dumps(row,ensure_ascii=False,allow_nan=False)+"\n" for row in rows))
        os.replace(temp,self.telemetry)
        self.samples=rows[-4032:]
        for row in self.samples:
            if row.get("measurement_version")!=3:
                row["quality"]=False
        bins=self.memory.setdefault("energy_bins",[])
        bins[:]=[b for b in bins if b.get("recovery_id")!=key]
        for row in recovery["energy_bins"]:
            bins.append(dict(row,recovery_id=key,estimated=True,historical=False,quality=True))
        bins.sort(key=lambda r:r["start"])
        self.memory["energy_last"]={"ts":time.time(),"total":current}
        self.memory["predictions"]=[]
        self.memory.setdefault("applied_recoveries",{})[key]={"kwh":recovery["kwh"],"samples":changed,"backup":str(backup)}
        self.save()
        self.publish("sensor.heating_energy_recovery",round(recovery["kwh"],5),
                     friendly_name="Котел — відновлена енергія 09.10",unit_of_measurement="kWh",
                     device_class="energy",estimated=True,recovery_id=key,recovered_samples=changed,
                     method="Резерв за потужністю вхідних фаз; 30 с підтвердження",backup=str(backup))
        self.log("Recovered %.5f kWh and %s samples",recovery["kwh"],changed)
    def now(self):
        return datetime.now(self.climate_tz)
    def load_state(self):
        try:
            data=json.loads(self.statefile.read_text())
            if not isinstance(data,dict):
                raise ValueError('State must be an object')
            model=data.get('model',DEFAULT_MODEL)
            if (not isinstance(model,dict) or any(isinstance(model.get(k),bool) or
                    not isinstance(model.get(k),(int,float)) or number(model[k]) is None or
                    not lo<=model[k]<=hi for k,(lo,hi) in BOUNDS.items()) or
                    not isinstance(model.get('version'),int) or not isinstance(model.get('validated'),bool)):
                raise ValueError('Invalid saved model')
            if any(key in data and not isinstance(data[key],list) for key in ('weather','energy_bins','journal')):
                raise ValueError('Invalid saved state collections')
            return data
        except FileNotFoundError:
            return {}
        except (OSError,ValueError,TypeError) as exc:
            # Preserve the damaged file for recovery; never replace lost model /
            # energy state with a seemingly successful empty bootstrap.
            self.cfg['allow_actuation']=False
            self.log('State could not be loaded; commands disabled: %s',exc,level='ERROR')
            return dict(state_load_error=str(exc),learning_storage_error='Не прочитано стан: '+str(exc))
    def raw(self,key,attribute=None):
        return self.get_state(self.cfg[key],attribute=attribute)
    def val(self,key,default=None,attribute=None):
        v=number(self.raw(key,attribute))
        return default if v is None else v
    def fresh(self,entity,seconds):
        state=self.get_state(entity,attribute="all")
        if not state or state.get("state") in ("unavailable","unknown",None):
            return False
        stamp=state.get("last_reported") or state.get("last_updated")
        try:
            return time.time()-datetime.fromisoformat(stamp.replace("Z","+00:00")).timestamp() <= seconds
        except (AttributeError,ValueError,TypeError):
            return False
    def heartbeat_fresh(self,entities,seconds):
        return any(self.fresh(entity,seconds) for entity in entities)
    def save(self):
        if self.memory.get('state_load_error'):
            return
        self.memory["model"]=self.model
        self.memory["weather"]=self.weather
        temp=self.statefile.with_suffix(".tmp")
        temp.write_text(json.dumps(self.memory,ensure_ascii=False,allow_nan=False))
        os.replace(temp,self.statefile)
    def publish(self,entity,state,**attributes):
        self.set_state(entity,state=str(state) if state is not None else "unavailable",attributes=attributes,replace=True)
    def journal(self,reason,kind="decision"):
        records=self.memory.setdefault("journal",[])
        if records and records[-1]["reason"]==reason and records[-1]["kind"]==kind:
            return
        records.append({"time":self.now().isoformat(),"kind":kind,"reason":reason})
        del records[:-30]
        self.save()
    def notify_once(self,reason):
        if self.memory.get("notification")!=reason:
            self.call_service("persistent_notification/create",title="Клімат — перевірка",
                              message=reason,notification_id="heating_control_fault")
            self.memory["notification"]=reason
            self.save()
    def settings_changed(self,entity,attribute,old,new,kwargs=None,**extra):
        self.next_compute=0
        if old!=new:
            self.decision_event('settings_change',reason='Змінено параметр '+entity,force=True)
    def outages_changed(self,entity,attribute,old,new,kwargs=None,**extra):
        if outage_signature(self.read_outage_schedule())!=getattr(self,'outage_signature',None):
            self.next_compute=0
    def restore_system_settings(self):
        equipment=self.memory.get('equipment_settings') or {}
        if isinstance(equipment,dict):
            for key,value in equipment.items():
                if key not in ('boiler_power_mode','power_measurement_policy','power_phase_topology','recuperator_entity','bath_entity','outage_missing_policy'):continue
                try:
                    clean,_=validate_system_settings({key:value},self.cfg,self.model);self.cfg.update(clean)
                except (ValueError,TypeError):self.log('Invalid saved equipment field: %s',key,level='WARNING')
        self.configure_ventilation()
        overrides=self.memory.get('system_settings',{})
        if overrides:
            try:
                config,_=validate_system_settings(overrides,self.cfg,self.model)
                self.cfg.update(config)
            except (ValueError,TypeError):
                self.log('Saved system settings are invalid; local configuration retained',level='WARNING')
    def publish_system_settings(self):
        self.publish('sensor.heating_system_settings','Налаштування системи',
                     friendly_name='Клімат — системні налаштування',fields=system_fields(self.cfg,self.model),
                     revision=self.memory.get('system_settings_revision',0),
                     persisted_revision=self.memory.get('system_settings_persisted_revision',self.memory.get('system_settings_revision',0)),
                     **getattr(self,'system_settings_result',self.memory.get('system_settings_receipt',{})))
    def system_settings_changed(self,event,data,kwargs=None,**extra):
        request=data.get('request_id') if isinstance(data,dict) else None
        success=False;error=None
        try:
            if not isinstance(request,str) or not 1<=len(request)<=128:
                raise ValueError('Некоректний ідентифікатор запиту')
            if self.update_in_progress():
                raise ValueError('Триває оновлення HACS; повторіть після завершення')
            if self.memory.get('state_load_error'):
                raise ValueError('Файл стану не прочитано; збереження заблоковане до відновлення')
            if data.get('revision')!=self.memory.get('system_settings_revision',0):
                raise ValueError('Налаштування вже змінилися; оновіть панель')
            config,model=validate_system_settings(data.get('values'),self.cfg,self.model)
            if getattr(self,'runtime_ready',False):
                entities=[config.get(k) for k in ('recuperator_entity','bath_entity') if config.get(k)]
                for phase in (config.get('power_phase_topology') or {}).get('phases',[]):
                    entities.append(phase['input']);entities.extend(load['entity'] for load in phase['loads'])
                if any(self.get_state(entity,attribute='all') is None for entity in entities):raise ValueError('Сутність не існує в Home Assistant')
            if model and not self.consider({**self.model,**model},'Налаштування користувача',apply=True):
                raise ValueError(self.memory.get('last_result','Модель не пройшла перевірку'))
            equipment={k:v for k,v in config.items() if k in ('boiler_power_mode','power_measurement_policy','power_phase_topology','recuperator_entity','bath_entity','outage_missing_policy')}
            for k in equipment:config.pop(k,None)
            changed={k:v for k,v in equipment.items() if self.cfg.get(k)!=v}
            if changed:
                self.decision_event('equipment_change',reason='Змінено опис фізичного обладнання',force=True,
                    snapshot=dict(previous={k:self.cfg.get(k) for k in changed},values=changed,pending_hourly=self.memory.get('predictions',[])))
                self.memory['predictions']=[]
                self.ventilation_totals={};self.ventilation_at=time.time();self.stage_totals={}
                history=self.memory.setdefault('equipment_history',[])
                history.append(dict(at=self.now().isoformat(),previous={k:self.cfg.get(k,'unconfirmed') for k in changed},values=changed))
                del history[:-32]
                self.memory['equipment_version']=self.memory.get('equipment_version',0)+1
                self.memory['equipment_settings']={**(self.memory.get('equipment_settings') or {}),**equipment}
                for k in equipment:config.pop(k,None)
                self.cfg.update(equipment);self.configure_ventilation()
                if hasattr(self,'power_estimator'):
                    self.power_estimator.window=10. if self.cfg.get('power_measurement_policy')=='audited' else 20.
                    self.power_estimator.threshold=500. if self.cfg.get('power_measurement_policy')=='audited' else 2000.
                    self.power_estimator.update(time.time(),None)
                self.memory.pop('plan_reference',None)
                self.run_diagnostic('equipment_calibration',self.refresh_equipment_models)
            self.cfg.update(config)
            self.memory['system_settings']={**self.memory.get('system_settings',{}),**config}
            self.memory['system_settings_revision']=self.memory.get('system_settings_revision',0)+1
            previous_persisted=self.memory.get('system_settings_persisted_revision',data['revision'])
            previous_receipt=self.memory.get('system_settings_receipt')
            self.memory['system_settings_persisted_revision']=self.memory['system_settings_revision']
            self.memory['system_settings_receipt']=dict(last_request_id=request,last_success=True,
                model_pending=bool(self.memory.get('pending_model')))
            try:self.save()
            except OSError:
                self.memory['system_settings_persisted_revision']=previous_persisted
                if previous_receipt is None:self.memory.pop('system_settings_receipt',None)
                else:self.memory['system_settings_receipt']=previous_receipt
                raise
            self.next_compute=0;success=True
        except (ValueError,TypeError,OSError) as exc:
            error=str(exc)
        self.system_settings_result=dict(last_request_id=request if isinstance(request,str) else None,
                                         last_success=success,last_error=error,model_pending=bool(self.memory.get('pending_model')))
        self.publish_system_settings()
    def migrate_equipment_state(self):
        if self.memory.get('equipment_schema') or self.memory.get('state_load_error'):return
        import hashlib
        backup=self.directory/'deployment_backups'/'equipment-v1'
        backup.mkdir(parents=True,exist_ok=True)
        manifest=backup/'manifest.json'
        if manifest.exists():
            try:prior=json.loads(manifest.read_text())
            except (ValueError,OSError) as exc:raise OSError('Equipment backup manifest unreadable') from exc
            for name,digest in prior.items():
                if name not in ('state.json','telemetry.jsonl','configuration.json') or hashlib.sha256((backup/name).read_bytes()).hexdigest()!=digest:
                    raise OSError('Equipment migration backup mismatch')
        hashes={}
        for source in (self.statefile,self.telemetry):
            if source.exists():
                target=backup/source.name
                if not target.exists():
                    shutil.copy2(source,target)
                    if hashlib.sha256(source.read_bytes()).digest()!=hashlib.sha256(target.read_bytes()).digest():raise OSError('Equipment migration backup mismatch')
                hashes[source.name]=hashlib.sha256(target.read_bytes()).hexdigest()
        configuration=backup/'configuration.json'
        if not configuration.exists():
            configuration.write_text(json.dumps(dict(settings=system_values(self.cfg),
                sources={k:self.cfg.get(k) for k in ('power_balance','power_balance_sources','power_fault','power_phase_topology','ventilation')}),sort_keys=True))
        hashes[configuration.name]=hashlib.sha256(configuration.read_bytes()).hexdigest()
        manifest.write_text(json.dumps(hashes,sort_keys=True))
        self.memory['equipment_schema']=1

    def configure_ventilation(self):
        raw=self.cfg.get('ventilation') or {}
        bindings=dict(raw) if isinstance(raw,dict) else {}
        for key,name in (('recuperator_entity','recuperator'),('bath_entity','bath')):
            if key in self.cfg:
                entity=self.cfg[key]
                if entity:bindings[name]=dict(entity=entity)
                else:bindings.pop(name,None)
        self.cfg['ventilation']=bindings
        if getattr(self,'runtime_ready',False):
            tracked=getattr(self,'equipment_listeners',{})
            wanted={b.get('entity') for b in bindings.values() if isinstance(b,dict)}-{None}
            for entity in list(tracked):
                if entity not in wanted:self.cancel_listen_state(tracked.pop(entity))
            for entity in wanted:
                if entity not in tracked:tracked[entity]=self.listen_state(self.ventilation_changed,entity)
            self.equipment_listeners=tracked

    def refresh_equipment_models(self):
        self.memory['power_calibrations']=learn_power_modes(self.samples,self.model)
        fit=fit_ventilation(self.samples,self.model,self.climate_tz)
        previous=self.memory.get('ventilation_model') or {}
        today=self.now().date().isoformat()
        if previous.get('validated') and previous.get('model_version')==self.model.get('version'):
            if not fit['validated'] or previous.get('fit_day')==today:return
            clipped=False
            for key in ('base_room_loss','recuperator','bath'):
                limit=abs(previous[key])*.2
                value=max(previous[key]-limit,min(previous[key]+limit,fit[key]))
                clipped=clipped or value!=fit[key];fit[key]=value
            # Clipping changes a fit: keep the candidate shadow until revalidated.
            if clipped:
                fit.update(validated=False,reason='daily_bound_requires_revalidation')
                self.memory['ventilation_candidate']=fit;return
        self.memory['ventilation_model']=dict(fit,fit_day=today)

    def read_power_snapshot(self,entities):
        template="""{% set ns=namespace(rows={}) %}{% for id in entities %}{% set s=states[id] %}{% if s is not none %}{% set ns.rows=dict(ns.rows,**{id:dict(value=s.state,at=as_timestamp(s.last_reported if s.last_reported is defined else s.last_updated,none),unit=s.attributes.get('unit_of_measurement'),kind='active')}) %}{% endif %}{% endfor %}{{ ns.rows|tojson }}"""
        result=self.render_template(template,variables=dict(entities=entities))
        if isinstance(result,str):result=json.loads(result)
        if not isinstance(result,dict) or any(not isinstance(row,dict) for row in result.values()):
            raise ValueError('Invalid live power snapshot')
        return result

    def assess_power_sources(self,stamp,primary,grid_off):
        entities=list(self.cfg.get('power_balance_sources') or [])
        topology=self.cfg.get('power_phase_topology') or {}
        phases=topology.get('phases') if isinstance(topology,dict) else None
        for phase in phases if isinstance(phases,list) else []:
            if not isinstance(phase,dict):continue
            entities.append(phase.get('input'))
            loads=phase.get('loads')
            if isinstance(loads,list):entities.extend(x.get('entity') for x in loads if isinstance(x,dict))
        live_snapshot=getattr(self,'runtime_ready',False) and callable(getattr(self,'render_template',None))
        if live_snapshot:
            # AppDaemon's state_changed cache misses reports with unchanged values.
            # Read real HA report timestamps together; never refresh stale data locally.
            wanted=sorted(set(entities+[self.cfg.get('power_balance')])-{None})
            if stamp-getattr(self,'power_snapshot_at',0)>=10 or wanted!=getattr(self,'power_snapshot_entities',None):
                self.power_snapshot=self.run_diagnostic('power_snapshot',self.read_power_snapshot,wanted,default={})
                self.power_snapshot_at=time.time();self.power_snapshot_entities=wanted
            readings=self.power_snapshot
            stamp=max(stamp,self.power_snapshot_at)
            balance=readings.get(self.cfg.get('power_balance')) or {}
            primary=number(balance.get('value'))
            if balance.get('unit')=='kW' and primary is not None:primary*=1000
            if balance.get('unit') not in ('W','kW'):primary=None
        else:
            readings={}
            for entity in set(entities)-{None}:
                record=self.get_state(entity,attribute='all')
                record=record if isinstance(record,dict) else {}
                attrs=record.get('attributes') or {}
                when=record.get('last_reported') or record.get('last_updated')
                try:at=datetime.fromisoformat(when.replace('Z','+00:00')).timestamp()
                except (AttributeError,ValueError,TypeError):at=None
                readings[entity]=dict(value=record.get('state'),at=at,unit=attrs.get('unit_of_measurement'),kind='active')
        dependencies=list(self.cfg.get('power_balance_sources') or [])
        if live_snapshot:dependencies.append(self.cfg.get('power_balance'))
        aligned=aligned_power(readings,dependencies,stamp)
        a=dict(aligned,watts=primary if aligned['valid'] else None,quality='measured' if aligned['valid'] else 'unknown')
        if primary is None or primary < -500 or primary>10500:a.update(valid=False,watts=None,reason='invalid_balance_range')
        if self.cfg.get('power_fault') and self.get_state(self.cfg['power_fault'])!='off':a.update(valid=False,watts=None,reason='meter_fault')
        if grid_off:a=dict(valid=True,watts=0.,quality='measured',reason='confirmed_grid_only_supply_off')
        b=phase_residual(topology,readings,stamp);final=combine_power(a,b)
        return dict(a={k:v for k,v in a.items() if k!='values'},b=b,**final,at=stamp)

    def observe_power_quality(self,stamp,primary,power,grid_off):
        assessment=self.assess_power_sources(stamp,primary,grid_off)
        final=assessment
        ledger=self.memory.setdefault('power_observation_ledger',dict(started_at=stamp))
        integrate_observation(ledger,stamp,final['watts'],final['quality'],restart=not getattr(self,'power_observation_started',False))
        self.power_observation_started=True
        old=getattr(self,'stage_observation',None)
        raw=final['watts'];band='unknown'
        if final['quality']=='measured' and raw is not None:
            if abs(raw)<500:band='off'
            else:
                nearest=min((3,6,9),key=lambda k:abs(raw-k*1000))
                if abs(raw-nearest*1000)<=max(500,nearest*200):band=str(nearest)
        since=old['since'] if old and old['band']==band else stamp
        if old and 0<stamp-old['at']<=45:
            key=old['band'] if old['at']-old['since']>=10 else 'unknown'
            totals=getattr(self,'stage_totals',{});totals[key]=totals.get(key,0.)+stamp-old['at'];self.stage_totals=totals
        self.stage_observation=dict(at=stamp,band=band,since=since)
        mode=self.cfg.get('boiler_power_mode','unconfirmed')
        contradiction=(mode.startswith('fixed_') and final['quality']=='measured' and raw is not None and raw>float(mode[-1])*1250)
        if contradiction:self.memory.setdefault('mode_contradiction_since',stamp)
        else:self.memory.pop('mode_contradiction_since',None)
        self.memory['mode_contradiction']=contradiction and stamp-self.memory.get('mode_contradiction_since',stamp)>=600
        journal=getattr(self,'decision_journal',None)
        if journal and stamp-getattr(self,'power_minute_at',stamp-60)>=60:
            journal.append(dict(event='power_minute',action=self.memory.get('last_command'),reason='power_quality_observation',
                physical_mode=mode,equipment_version=self.memory.get('equipment_version',0),
                power=assessment,energy=dict(ledger),instantaneous_stage_seconds=getattr(self,'stage_totals',{}),
                ventilation=getattr(self,'ventilation_states',{})),stamp=stamp)
            self.power_minute_at=stamp
        self.power_observation=dict(assessment,controller_power_w=power,controller_source=getattr(self,'power_source','unknown'),
            policy=self.cfg.get('power_measurement_policy','legacy'))

    def update_power_recommendation(self,now,room,floor,outside,fire,weather,settings,signature):
        if self.cfg.get('boiler_power_mode','unconfirmed')=='unconfirmed':
            self.memory['power_recommendation']=dict(status='insufficient_data',reason='Підтвердьте фізичний режим котла',mode=None)
            return
        old=self.memory.get('power_recommendation') or {}
        stamp=now.timestamp()
        # Cache unchanged advice for fifteen minutes; relevant forecast inputs
        # invalidate it, but a refresh timestamp alone does not.
        schedule_key=signature[:2]+signature[3:] if isinstance(signature,(tuple,list)) and len(signature)==5 else signature
        comfort={k:settings.get(k) for k in ('target','lower','upper','night_target','night_lower','night_upper','start','end','sleep_start','sleep_end','strategy','morning_min_minutes','budget_multiplier')}
        weather_key=(getattr(self,'weather',None) or weather) if weather else []
        context=(schedule_key,comfort,weather_key,self.model.get('version'))
        key=[hashlib.sha256(repr(context).encode()).hexdigest(),settings.get('equipment_version'),round(room*2),round(floor),round(outside),round(fire/5)]
        if stamp-old.get('computed_at',0)<900 and old.get('input_key')==key:return
        result=recommend_power(now,room,floor,outside,fire,self.model,weather,settings,self.memory.get('power_calibrations'))
        if old.get('mode') and result.get('mode') and result['mode']<old['mode'] and stamp-old.get('changed_at',0)<900:return
        result.update(computed_at=stamp,input_key=key,changed_at=stamp if result.get('mode')!=old.get('mode') else old.get('changed_at',stamp))
        self.memory['power_recommendation']=result

    def publish_equipment(self,ventilation_support=None):
        # AppDaemon's nested attribute transport drops numeric zero/false.
        # Keep canonical counters numeric; stringify only these wire values.
        def wire(value):
            if isinstance(value,dict):return {k:wire(v) for k,v in value.items()}
            if isinstance(value,list):return [wire(v) for v in value]
            if value is False:return 'false'
            return '0' if isinstance(value,(int,float)) and value==0 else value
        profile=power_profile(self.cfg.get('boiler_power_mode','unconfirmed'),self.memory.get('power_calibrations'),self.memory.get('modulation_profile'),self.memory.get('equipment_version',0))
        self.publish('sensor.heating_power_model',self.cfg.get('boiler_power_mode','unconfirmed'),
            **wire(dict(friendly_name='Котел — фізичний режим і прогноз',equipment_version=self.memory.get('equipment_version',0),
            history=[dict(at=h['at'],changed=list(h['values'])) for h in (self.memory.get('equipment_history') or [])[-3:]],
            profile={k:v for k,v in profile.items() if k not in ('bands','stage_minutes')},
            bands=[{k:b.get(k) for k in ('return_min','return_max','power_kw','minutes')} for b in profile.get('bands',[])[:16]],
            mode_contradiction=self.memory.get('mode_contradiction',False),
            recommendation=self.memory.get('power_recommendation'),power_observation=getattr(self,'power_observation',{}),
            energy=self.memory.get('power_observation_ledger'),
            accuracy='Перевіряється' if profile.get('assumed',True) else 'Перевірено на відкладених сеансах')))
        self.publish('sensor.heating_ventilation','Враховано' if (self.memory.get('ventilation_model') or {}).get('validated') else 'Навчання; чинна базова модель',
            **wire(dict(friendly_name='Опалення — вентиляція',devices=getattr(self,'ventilation_states',{}),support=ventilation_support or {},
            model=self.memory.get('ventilation_model') or {},candidate=self.memory.get('ventilation_candidate'),
            note='Реле не доводить витрату повітря; майбутній duty — сценарій, не точний розклад')))

    def ai_report_changed(self,entity,attribute,old,new,kwargs=None,**extra):
        if old!=new:
            self.publish_ai_summary()
    def publish_ai_summary(self,refresh=False):
        with self.ai_summary_lock:
            attributes=self.ai_summary_attributes
            if attributes is None and not refresh:
                state=self.get_state("sensor.heating_ai_summary",attribute="all") or {}
                attributes=dict(state.get("attributes",{}))
            if refresh or not attributes or not {"model","usable_samples","last_24h","last_result"}<=attributes.keys():
                current=dict(friendly_name="Клімат — добовий аналіз",model=dict(self.model),
                             usable_samples=len(transitions(self.samples)),last_24h=self.summary(),
                             last_result=self.memory.get("last_result"))
                attributes=current if refresh else {**current,**(attributes or {})}
            self.ai_summary_attributes=attributes
            # Read the current helper inside the same lock: a delayed callback must
            # not overwrite a newer report or cycle attributes with an old event.
            self.publish("sensor.heating_ai_summary",self.raw("ai_report") or "Перший аналіз очікується",
                         **attributes)
    def grid_state(self):
        alive=self.raw("connection")=="on" and self.heartbeat_fresh(self.cfg["inverter_heartbeat"],90)
        if not alive:
            return "unknown"
        grid=self.raw("grid")
        voltage=self.val("grid_voltage")
        if grid=="off" and voltage is not None and voltage<50:
            return "off"
        if grid!="on" or voltage is None or voltage<150:
            return "unknown"
        if not self.heartbeat_fresh(self.cfg["input_heartbeat"],300):
            return "unknown"
        for voltage_id,heartbeats in zip(self.cfg["input_voltages"],self.cfg["phase_heartbeats"]):
            v=number(self.get_state(voltage_id))
            if v is None or v<150 or not self.heartbeat_fresh(heartbeats,300):
                return "unknown"
        return "on"
    def source_temperature(self):
        primary=self.val("room")
        primary_ok=primary is not None and 5<primary<40 and self.heartbeat_fresh(self.cfg["room_heartbeat"],self.cfg.get('room_max_age',14400))
        thermostat=self.val("thermostat",attribute="current_temperature")
        if primary_ok:
            return primary,"Вітальня",primary,thermostat
        bias=self.memory.get("thermostat_bias")
        if thermostat is not None and bias is not None and self.raw("thermostat") not in ("unavailable","unknown",None):
            return thermostat-bias,"Термостат · резерв",primary,thermostat
        return None,"Немає перевіреного датчика",primary,thermostat
    def ventilation_changed(self,entity,attribute,old,new,kwargs=None,**extra):
        self.ventilation_tick()
    def ventilation_tick(self,stamp=None):
        stamp=time.time() if stamp is None else stamp
        states=getattr(self,'ventilation_states',{})
        elapsed=max(0.,stamp-getattr(self,'ventilation_at',stamp))
        totals=getattr(self,'ventilation_totals',{})
        for name,old in states.items():
            row=totals.setdefault(name,dict(seconds=0.,on=0.,unknown=0.,supply_on=0.,supply_unknown=0.,
                                            powered_on=0.,grid_on=0.,grid_unknown=0.))
            row['seconds']+=elapsed
            known=elapsed<=45
            row['on']+=elapsed if known and old['relay']=='on' else 0.
            row['unknown']+=elapsed if not known or old['relay'] not in ('on','off') else 0.
            row['supply_on']+=elapsed if known and old['supply']=='on' else 0.
            row['supply_unknown']+=elapsed if not known or old['supply'] not in ('on','off') else 0.
            row['powered_on']+=elapsed if known and old['relay']=='on' and old['supply']=='on' else 0.
            row['grid_on']+=elapsed if known and old['grid']=='on' else 0.
            row['grid_unknown']+=elapsed if not known or old['grid']=='unknown' else 0.
        bindings=self.cfg.get('ventilation',{})
        states={}
        if isinstance(bindings,dict):
            for name,binding in bindings.items():
                if not isinstance(binding,dict) or not isinstance(binding.get('entity'),str):continue
                states[name]=dict(relay=self.get_state(binding['entity']),
                    supply=self.get_state(binding['power_entity']) if isinstance(binding.get('power_entity'),str) else 'unknown',
                    grid=self.grid_state(),entity=binding['entity'])
        self.ventilation_states=states;self.ventilation_totals=totals;self.ventilation_at=stamp
    def ventilation_sample(self,stamp):
        self.ventilation_tick(stamp)
        result={}
        for name,row in self.ventilation_totals.items():
            seconds=row['seconds']
            result[name]=dict(entity=self.ventilation_states.get(name,{}).get('entity'),seconds=round(seconds,1),
                on_fraction=round(row['on']/seconds,4) if seconds else None,
                unknown_fraction=round(row['unknown']/seconds,4) if seconds else None,
                supply_on_fraction=round(row['supply_on']/seconds,4) if seconds else None,
                supply_unknown_fraction=round(row['supply_unknown']/seconds,4) if seconds else None,
                powered_relay_on_fraction=round(row['powered_on']/seconds,4) if seconds and row['supply_unknown']==0 else None,
                grid_on_fraction=round(row['grid_on']/seconds,4) if seconds else None,
                grid_unknown_fraction=round(row['grid_unknown']/seconds,4) if seconds else None)
        self.ventilation_totals={}
        return result
    def sample(self,room,probe,outside,power,fire,source,primary,thermostat,quality,settings=None):
        stamp=time.time()
        if stamp-self.last_sample<290:
            return
        self.last_sample=stamp
        row={"ts":stamp,"room":room,"return":probe,"outside":outside,"power":power,
             "fire":fire,"quality":quality and source=="Вітальня","source":source,
             "energy":self.val("energy"),"thermostat":thermostat}
        row["measurement_version"]=3
        row['environment_version']=1
        row['boiler_power_mode']=self.cfg.get('boiler_power_mode','unconfirmed')
        row['boiler_mode_confirmed']=row['boiler_power_mode']!='unconfirmed'
        row['equipment_version']=self.memory.get('equipment_version',0)
        row['thermal_model_version']=self.model.get('version')
        row['thermal_model']=dict(self.model)
        row['power_observation']=getattr(self,'power_observation',{})
        row['instantaneous_stage_seconds']=getattr(self,'stage_totals',{})
        row['previous_power_band']=(getattr(self,'stage_observation',None) or {}).get('band')
        row['seconds_since_command']=max(0.,stamp-self.memory.get('command_at',stamp))
        row['voltages_v']={entity:number(self.get_state(entity)) if self.fresh(entity,300) else None for entity in self.cfg.get('input_voltages',[])}
        prior=self.samples[-1] if self.samples else None
        row['return_slope_c_per_hour']=((probe-prior['return'])*3600/(stamp-prior['ts']) if prior and probe is not None and number(prior.get('return')) is not None and stamp>prior['ts'] else None)
        self.stage_totals={}
        row['floor_estimate']=(self.memory.get('floor_observer') or {}).get('estimate')
        row['daylight_weight']=daylight(self.now())
        row['external_gain']=(self.memory.get('solar_observer') or {}).get('observed_gain',0.)
        row["power_source"]=getattr(self,"power_source","balance")
        row['power_quality']=getattr(self,'power_quality','unknown')
        if row['power_quality'] in ('unknown','contradictory') or getattr(self,'power_confirmation_pending',False):row['quality']=False
        row['execution']='automatic' if self.memory.get('owned') and self.memory.get('last_command')=='heat' else 'manual_or_native' if power is not None and power>=1.5 else 'idle'
        row["power_estimated"]=getattr(self,"fallback_interval",False)
        row['heating_requested']=self.memory.get('last_command')=='heat'
        row['heat_command_at']=self.memory.get('command_at') if row['heating_requested'] else None
        row['grid']=self.grid_state()
        row['fire_valid']=self.memory.get('fire_phase')!='Датчик недоступний'
        row['ventilation']=self.ventilation_sample(stamp)
        record=self.raw('room',attribute='all') if self.cfg.get('room') else None
        if isinstance(record,dict):
            reported=record.get('last_reported') or record.get('last_updated')
            row['room_timestamp_kind']='last_reported' if record.get('last_reported') else 'last_updated'
            try:row['room_reported_at']=datetime.fromisoformat(reported.replace('Z','+00:00')).timestamp()
            except (AttributeError,ValueError,TypeError):row['room_reported_at']=None
        if settings:
            row['comfort_policy']={k:settings[k] for k in ('target','lower','upper','night_target','night_lower',
                'night_upper','sleep_start','sleep_end') if k in settings}
        self.fallback_interval=False
        previous=self.samples[-1] if self.samples else None
        if previous and previous.get("measurement_version")==3:
            seconds=stamp-previous["ts"]
            before=number(previous.get("energy"))
            after=number(row.get("energy"))
            if (seconds>0 and before is not None and after is not None and after>=before
                    and previous["ts"]>=self.measurement_valid_since):
                row["interval_power"]=(after-before)*3600/seconds
        self.samples.append(row)
        self.samples=self.samples[-4032:]
        self.memory['modulation_profile']=learn_modulation(self.samples)
        self.run_diagnostic('power_calibration',self.refresh_equipment_models)
        with self.telemetry.open("a") as file:
            file.write(json.dumps(row,ensure_ascii=False,allow_nan=False)+"\n")
        pairs=[r for r in self.samples[-288:] if r.get("source")=="Вітальня" and
               number(r.get("thermostat")) is not None and number(r.get("room")) is not None]
        if len(pairs)>=12 and pairs[-1]["ts"]-pairs[0]["ts"]>=4*3600 and max(r["room"] for r in pairs)-min(r["room"] for r in pairs)>=.5:
            self.memory["thermostat_bias"]=round(statistics.median(r["thermostat"]-r["room"] for r in pairs),2)
        pending=self.memory.setdefault("predictions",[])
        matured=[p for p in pending if p["ts"]<=stamp]
        if room is not None and source=="Вітальня" and quality and row['grid']=='on' and not row['power_estimated']:
            errors=self.memory.setdefault("hour_errors",[])
            for p in matured:
                energy=self.val("energy")
                matched=(energy is not None and p.get("start_energy") is not None and
                         abs(energy-p["start_energy"]-p.get("expected_energy",0))<=.5)
                if stamp-p["ts"]<=420 and p["version"]==self.model["version"] and matched:
                    errors.append(abs(p["temperature"]-room))
                    candidate=self.memory.get('pending_model')
                    if candidate and p.get('learning_attempt_id')==candidate['attempt_id']:
                        candidate.setdefault('errors',[]).append(dict(old=abs(p['temperature']-room),
                            new=abs(p['candidate_temperature']-room)))
            del errors[:-48]
        self.memory["predictions"]=[p for p in pending if p["ts"]>stamp]
        self.confirm_learning_candidate()
        if (not self.model.get('validated') and len(self.memory.get("hour_errors",[]))>=12 and self.hour_mae()<=.3
                and self.memory.get("previous_model") and len(transitions(self.samples))>=96):
            self.model["validated"]=True
            self.memory['learning_success']=dict(time=self.now().isoformat(),model_version=self.model['version'],
                hour_mae=self.hour_mae(),source='Початкова годинна перевірка')
            self.next_compute=0
        self.save()
    def fireplace(self,room):
        p=system_values(self.cfg)
        stamp=time.time()
        temperature=self.val("fireplace")
        valid=temperature is not None and room is not None and self.heartbeat_fresh(self.cfg["fireplace_heartbeat"],p['fireplace_max_age'])
        delta=max(0.,temperature-room) if valid else 0.
        recent=[r for r in self.samples if 0<stamp-r["ts"]<=p['fire_trend_seconds'] and number(r.get("fire")) is not None]
        slope=(delta-recent[0]["fire"])*3600/max(300,stamp-recent[0]["ts"]) if recent else 0.
        # Remove binary floating-point noise at boundaries; keep raw delta for the model.
        signal=round(delta,6);trend=round(slope,6)
        previous=self.memory.get("fire_phase","Неактивний")
        tracker=self.memory.get('fire_circulation',{})
        was_active=tracker.get('active',self.memory.get('fan_owned',False) and previous in ('Горить','Згасає','Інтенсивно горить'))
        last_valid=number(tracker.get('last_valid'))
        continuous=last_valid is not None and 0<=stamp-last_valid<=p['fire_continuity_seconds']
        below=number(tracker.get('below_since')) if continuous else None
        active=False
        if valid:
            if signal>=p['fire_start_delta']:
                active=True;below=None
            elif was_active and not (signal<p['fire_fast_stop_delta'] and trend<=p['fire_fast_stop_slope']):
                if signal<p['fire_stop_delta']:
                    below=stamp if below is None else below
                    active=stamp-below<p['fire_cooldown_seconds']
                else:
                    active=True;below=None
        if not active:
            below=None
        self.memory['fire_circulation']=dict(active=active,below_since=below,last_valid=stamp if valid else None)
        if not valid:
            phase="Датчик недоступний"
        elif trend<p['fire_fade_slope'] and signal>=p['fire_ignite_delta']:
            phase="Згасає"
        elif signal>=p['fire_intense_delta']:
            phase="Інтенсивно горить"
        elif signal>=p['fire_start_delta']:
            phase="Горить"
        elif signal>=p['fire_ignite_delta'] and trend>p['fire_ignite_slope']:
            phase="Розпалювання"
        elif active:
            phase="Згасає"
        else:
            phase="Неактивний"
        self.memory["fire_phase"]=phase
        remaining=max(0,int(p['fire_cooldown_seconds']-(stamp-below))) if below is not None else 0
        self.publish("sensor.heating_fireplace",phase,friendly_name="Камін — стан",
                     temperature=temperature,delta=round(delta,2),trend=round(slope,2),
                     circulation_requested=active,cooldown_remaining_seconds=remaining,
                     calibrated=False,icon="mdi:fireplace")
        self.publish("binary_sensor.heating_fireplace_active",None if not valid else "on" if active else "off",
                     friendly_name="Камін — корисне тепло",icon="mdi:fire")
        return delta,active
    def arm_changed(self,entity,attribute,old,new,kwargs=None,**extra):
        if new=="on" and old!="on":
            self.memory["once"]={"armed_at":time.time(),"saw_off":self.grid_state()=="off",
                                 "restore_at":None,"active_at":None}
        elif new=="off":
            self.memory.pop("once",None)
        self.save()
        self.next_compute=0
    def once_state(self,grid,room,target,permitted):
        once=self.memory.get("once")
        if self.raw("restore_once")!="on":
            self.memory.pop("once",None)
            return False,"Не замовлено"
        if once is None:
            once={"armed_at":time.time(),"saw_off":grid=="off","restore_at":None,"active_at":None}
            self.memory["once"]=once
        if grid=="off":
            once["saw_off"]=True
        if grid=="on" and once["saw_off"] and once["restore_at"] is None:
            once["restore_at"]=time.time()
        if grid!="on":
            self.memory.pop("grid_stable_since",None)
        else:
            self.memory.setdefault("grid_stable_since",time.time())
        stable=grid=="on" and time.time()-self.memory.get("grid_stable_since",time.time())>=60
        if once["restore_at"] is None:
            return False,"Очікуємо наступне повернення мережі"
        if not permitted:
            return False,"Мережа поверталася · запуск очікує дозволу керування"
        if not stable or room is None:
            return False,"Очікуємо стабільну мережу та справні датчики"
        if room>=target:
            self.call_service("input_boolean/turn_off",entity_id=self.cfg["restore_once"])
            self.journal("Одноразовий запит завершено: температура вже досягнута","restore")
            return False,"Ціль досягнута"
        active=once.get("active_at")
        if active and time.time()-active>self.val("session_limit",180)*60:
            self.call_service("input_boolean/turn_off",entity_id=self.cfg["restore_once"])
            self.notify_once("Одноразовий сеанс завершено за лімітом часу")
            return False,"Завершено за лімітом"
        return True,"Одноразовий нагрів після повернення мережі"
    def balance_changed(self,entity,attribute,old,new,kwargs=None,**extra):
        if time.time()-getattr(self,'power_update_at',0)<5:return
        self.update_heating_power()
    def update_heating_power(self):
        stamp=time.time()
        self.power_update_at=stamp
        primary=self.val("power_balance")
        dependencies=self.cfg.get('power_balance_sources',[])
        stale=[entity for entity in dependencies if not self.fresh(entity,300)]
        fault=self.get_state(self.cfg['power_fault']) if self.cfg.get('power_fault') else 'off'
        failed=primary is None or bool(stale) or fault!='off'
        phases=[number(self.get_state(e)) for e in self.cfg.get("fallback_phase_power",[])]
        phase_entities=self.cfg.get('fallback_phase_power',[])
        fresh=len(phase_entities)==3 and all(self.fresh(entity,300) for entity in phase_entities)
        alive=self.raw("connection")=="on" and self.heartbeat_fresh(self.cfg["inverter_heartbeat"],90)
        grid_off=alive and self.raw("grid")=="off" and self.val("grid_voltage",999)<50
        raw,source,active=fallback_power(primary,failed,self.raw("thermostat"),
            self.val("thermostat",attribute="temperature"),self.val("thermostat",attribute="current_temperature"),
            phases,fresh,self.memory.get("last_measured_stage_kw"),grid_off)
        if self.cfg.get('power_measurement_policy')=='audited':
            assessment=self.assess_power_sources(stamp,primary,grid_off)
            raw=assessment['watts']
            source='balance' if assessment['quality']=='measured' else 'fallback_audited_residual' if assessment['quality']=='estimated' else 'unavailable'
            active=None
        if source!=getattr(self,"power_source",None):
            self.power_estimator.update(stamp,None)
        self.power_source=source
        self.fallback_interval=getattr(self,"fallback_interval",False) or estimated_power(source)
        if raw is None:
            self.measurement_valid_since=stamp
        power=self.power_estimator.update(stamp,raw)
        pending=raw is not None and raw>=self.power_estimator.threshold and power==0.
        if getattr(self,'power_confirmation_pending',False) and not pending:self.next_compute=0
        self.power_confirmation_pending=pending
        self.current_power_w=None if pending else power
        if source=="balance" and power is not None and power>=2000:
            stage=nominal_mode(power,self.stage)
            if stage in ("3 кВт","6 кВт","9 кВт"):
                self.memory["last_measured_stage_kw"]=int(stage.split()[0])
        requested=(self.raw('mode')=='Автоматичне керування' and self.raw('armed')=='on' and
                   self.memory.get('owned',False) and self.memory.get('last_command')=='heat' and
                   not self.memory.get('temporary_fallback'))
        self.power_quality=('unknown' if self.current_power_w is None else 'estimated' if estimated_power(source) else
                            'contradictory' if grid_off and power>=1500 else 'measured')
        self.publish("sensor.heating_power_confirmed",self.current_power_w,confirmation_pending=pending,
                     friendly_name="Котел — підтверджена потужність ТЕНів",
                     unit_of_measurement="W",device_class="power",state_class="measurement",
                     measurement_version=3,threshold_w=self.power_estimator.threshold,confirmation_seconds=10,
                     averaging_seconds=self.power_estimator.window,raw_balance_w=primary,
                     averaged_balance_w=self.power_estimator.average,
                     power_source=source,estimated=estimated_power(source),primary_failed=bool(failed),
                     requested_heating=bool(requested),inferred_demand=active,
                     power_quality=self.power_quality,stale_sources=stale,power_fault=fault,
                     power_without_request=power is not None and power>=1500 and not requested,
                     uncertainty='Несвіжі складові балансу' if stale else 'Помилка або невідомий стан вимірювачів' if fault!='off' else None,
                     fallback_raw_w=raw if source.startswith("fallback") else None,
                     timestamp=self.now().isoformat())
        self.run_diagnostic('power_observation',self.observe_power_quality,stamp,primary,power,grid_off)
        return power
    def stage_update(self,power):
        self.stage=nominal_mode(None if power is None else power*1000,self.stage)
        self.candidate=None
        self.publish("sensor.heating_boiler_stage",self.stage,friendly_name="Котел — діапазон потужності",physical_stage_confirmed=False,
                     icon="mdi:water-boiler",measurement_version=3,timestamp=self.now().isoformat())
        self.publish('binary_sensor.heating_boiler_active',None if power is None or getattr(self,'power_confirmation_pending',False) else 'on' if power>=(.5 if self.cfg.get('power_measurement_policy')=='audited' else 1.5) else 'off',
                     friendly_name='Котел — фактичний нагрів',icon='mdi:water-boiler')
    def tick(self,kwargs=None,**extra):
        if self.update_in_progress():
            return
        try:
            self.tick_inner()
            self.memory['last_successful_tick']=self.now().isoformat()
            incident=self.memory.get('control_incident')
            if incident and not self.memory.get('temporary_fallback') and not self.memory.get('recovering_from_fallback'):
                incident.update(active=False,recovered_at=self.now().timestamp())
            self.run_diagnostic('health_publish',self.publish_diagnostic_health)
        except Exception as exc:
            incident=self.record_failure('control_tick',exc,critical=True)
            self.run_diagnostic("control_status",self.publish,"sensor.heating_controller","Помилка контролера",friendly_name="Клімат — контролер",
                         error=str(exc),reason='Помилка циклу: '+str(exc),incident_id=incident['incident_id'],
                         control_state='error',execution_state='unconfirmed')
            try:
                if (self.cfg.get('allow_actuation',False) and self.raw('armed')=='on' and
                        self.raw('mode')=='Автоматичне керування' and self.grid_state()=='on'):
                    self.enter_temporary_fallback('Помилка циклу: '+str(exc))
            except Exception as recovery_error:
                self.record_failure('fallback_command',recovery_error)
            self.run_diagnostic('health_publish',self.publish_diagnostic_health)
    def tick_inner(self):
        now=self.now()
        self.ventilation_tick(now.timestamp())
        if now.hour==10 and self.memory.get("learn_morning_date")!=now.date().isoformat():
            self.memory["learn_morning_date"]=now.date().isoformat()
            self.run_diagnostic('learning',self.daily_learn)
        if now.hour==20 and now.minute>=45 and self.memory.get("learn_date")!=now.date().isoformat():
            self.memory["learn_date"]=now.date().isoformat()
            self.run_diagnostic('learning',self.daily_learn)
        room,source,primary,thermostat=self.source_temperature()
        probe=self.val("return")
        probe_ok=probe is not None and 0<probe<65 and self.heartbeat_fresh(self.cfg["return_heartbeat"],self.cfg.get('return_max_age',1200))
        outside=self.val("outside")
        if outside is None:
            outside=self.val("weather",attribute="temperature")
        grid=self.grid_state()
        power=self.update_heating_power()
        self.stage_update(power/1000 if power is not None else None)
        fire,fire_active=self.fireplace(room)
        permitted=bool(self.cfg.get("allow_actuation",False)) and self.raw("armed")=="on"
        self.publish("binary_sensor.heating_actuation_permitted","on" if permitted else "off",
                     friendly_name="Клімат — дозвіл команд обладнанню")
        self.publish("sensor.heating_grid",{"on":"Мережа є","off":"Мережі немає","unknown":"Стан не підтверджено"}[grid],
                     friendly_name="Клімат — електромережа",raw=grid,icon="mdi:transmission-tower")
        target=self.val("target",22)
        lower=self.val("lower",21.5)
        upper=self.val("upper",22.5)
        settings={"target":target,"lower":lower,"upper":upper,"start":self.raw("start") or "23:00:00",
                  "end":self.raw("end") or "07:00:00","strategy":self.raw("strategy"),
                  "day_price":None,"night_price":None}
        if self.cfg.get('night_target'):
            settings.update(night_target=self.val('night_target',target-.5),
                night_lower=self.val('night_lower',lower-.5),night_upper=self.val('night_upper',upper-.5),
                sleep_start=self.raw('sleep_start'),sleep_end=self.raw('sleep_end'))
        target,lower,upper,comfort_zone=comfort_at(now,settings)
        outage_data = self.read_outage_schedule(now)
        signature=outage_signature(outage_data)
        if signature != getattr(self,"outage_signature",None):
            self.next_compute=0
            self.outage_signature=signature
            self.schedule_received_at=now.timestamp()
        settings["split_comfort"] = True
        settings["morning_min_minutes"] = self.cfg.get("morning_min_minutes",30)
        command_at=number(self.memory.get("command_at"))
        automatic=(self.raw('mode')=='Автоматичне керування' and permitted and
                   self.memory.get('owned',False) and self.memory.get('last_command')=='heat')
        settings.update(confirmed_morning(self.memory,now,settings,power,self.val('energy'),grid,
                                         automatic,getattr(self,'power_source','unknown')))
        settings["outage_schedule"] = outage_data
        settings["outage_advance_limit"] = self.cfg.get("outage_advance_limit",180)
        settings.update({k:system_values(self.cfg)[k] for k in ('risk_cold_delta','risk_blackout_short_minutes','risk_blackout_long_minutes')})
        emergency = outage_data['emergency']
        outage_now = bool(outage_data.get("valid") and blocked(now.timestamp(),now.timestamp()+15,outage_data["outages"]))
        upcoming = [o for o in outage_data.get("outages",[]) if o["end"] > now.timestamp()]
        self.publish("sensor.heating_outage_schedule",outage_data.get("reason","Графік не підключений"),
                     friendly_name="Клімат — графік відключень 3.1",valid=bool(outage_data.get("valid")),
                     updated=outage_data.get("updated"),emergency=emergency,mode=outage_data['mode'],
                     source_available=outage_data['source_available'],source_fresh=outage_data['source_fresh'],
                     assumed_available=bool(outage_data.get('assumed')),source_mode=outage_data.get('source_mode',outage_data['mode']),
                     reason=outage_data['reason'],
                     scheduled_outage_now=outage_now,next_outage=upcoming[0] if upcoming else None,
                     outages=upcoming,known_until=outage_data.get("known_until"),
                     warning=outage_data['warning'])
        budget=self.val("monthly_budget",0)
        month_used=number(self.get_state(self.cfg["monthly_energy"]))
        expected=budget*now.day/calendar.monthrange(now.year,now.month)[1]
        settings["budget_multiplier"]=max(1.,min(3.,(month_used/max(1.,expected))**2)) if budget>0 and month_used is not None else 1.
        tariff=self.val("tariff")
        if tariff and tariff>0:
            night=in_window(now,"23:00","07:00")
            settings["day_price"]=tariff*2 if night else tariff
            settings["night_price"]=tariff if night else tariff*.5
        valid=(settings['lower']<settings['target']<=settings['upper'] and lower<target<=upper and
               settings["start"]!=settings["end"] and
               ('night_target' not in settings or settings['night_lower']<settings['night_target']<=settings['night_upper']
                and settings['sleep_start']!=settings['sleep_end']))
        once,once_text=self.once_state(grid,room,target,permitted and probe_ok and power is not None and valid)
        self.publish("sensor.heating_restore_status",once_text,friendly_name="Клімат — одноразовий запуск")
        quality=room is not None and probe_ok and outside is not None and power is not None
        self.sample(room,probe,outside,None if power is None else power/1000,fire,source,primary,thermostat,quality,settings)
        self.energy_tick()
        self.run_diagnostic('cycle_energy',self.observe_cycle_energy,now,settings,power,self.val('energy'),grid,automatic)
        cycle=next((e for e in self.memory.get('cycle_energy',{}).values()
                    if e['start']<=now.timestamp()<e['end']),None)
        initial=(cycle or {}).get('initial_plan') or (cycle or {}).get('baseline')
        if initial and (cycle['complete'] or number(cycle.get('meter_kwh')) is not None):
            settings['cycle_energy_budget']=dict(start=cycle['start'],end=cycle['end'],
                remaining_kwh=max(0.,initial['kwh']-cycle.get('meter_kwh',cycle['observed_kwh'])))
        self.run_diagnostic('temperature_history',self.observe_temperature_history,now,settings,room,power,
                            source=='Вітальня' and room is not None)
        measured_fire=None if self.memory.get('fire_phase')=='Датчик недоступний' else fire
        self.run_diagnostic('forecast_validation',self.validate_reserve_predictions,now,room,power,
            quality=source=='Вітальня' and room is not None and power is not None and outside is not None,
            measurements=dict(floor=(self.memory.get('floor_observer') or {}).get('estimate',probe),outside=outside,fire=measured_fire))
        self.run_diagnostic('peak_validation',self.observe_stop_peak,now,room,power,source=='Вітальня' and room is not None)
        settings['reserve_errors']=self.memory.get('reserve_errors',[])
        profile=power_profile(self.cfg.get('boiler_power_mode','unconfirmed'),self.memory.get('power_calibrations'),
            self.memory.get('modulation_profile',dict(bands=[],intervals=0,minutes=0)),self.memory.get('equipment_version',0))
        settings['modulation_profile']=profile
        settings['equipment_version']=self.memory.get('equipment_version',0)
        settings['thermal_model_version']=self.model.get('version')
        settings['ventilation_model']=self.memory.get('ventilation_model') or {}
        settings['ventilation_expected'],ventilation_support=ventilation_expectation(self.samples,getattr(self,'ventilation_states',{}))
        floor=probe
        environment_quality=reliability(self.memory.get('hour_errors',[]))
        if quality:
            thermal,floor=observe_floor(self.memory.get('floor_observer'),now.timestamp(),
                                       room,probe,power/1000,grid,self.model,energy_kwh=self.val('energy'))
            self.memory['floor_observer']=thermal
            weather_state=self.raw('weather',attribute='all') if self.cfg.get('weather') else None
            condition=weather_state.get('state') if isinstance(weather_state,dict) else None
            room_state=self.raw('room',attribute='all') or {}
            reported=room_state.get('last_reported') or room_state.get('last_updated') if isinstance(room_state,dict) else None
            try:reported=datetime.fromisoformat(reported.replace('Z','+00:00')).timestamp()
            except (AttributeError,ValueError,TypeError):reported=None
            solar=observe_gain(self.memory.get('solar_observer'),now.timestamp(),room,floor,
                               outside,fire,power>=1500,self.model,self.climate_tz,condition,
                               secondary=thermostat,reported_at=reported)
            self.memory['solar_observer']=solar
            settings['environment']={'solar':dict(solar)}
            # Quantise the safety reserve so tiny MAE changes do not replan.
            settings['upper_margin']=round(environment_quality['upper_margin'],1)
            self.publish('sensor.heating_environment',round(floor,2),
                friendly_name='Клімат — оцінка запасу тепла підлоги',unit_of_measurement='°C',
                estimated=True,raw_return=probe,floor_source=thermal['source'],
                external_gain_c_per_hour=round(solar.get('observed_gain',0.),3),
                solar_learning_days=solar['days'],solar_validated=solar['validated'],
                solar_coefficient=round(solar['coefficient'],3),weather_condition=condition,
                recent_mae=environment_quality['recent_mae'],upper_margin=settings['upper_margin'],
                note='Еквівалент запасу тепла, не температура поверхні; зовнішній прогрів не доводить сонце')
        anchor=None
        if (grid=='on' and probe_ok and self.power_source=='balance' and
                self.memory.get('last_command')=='heat' and command_at is not None and
                now.timestamp()-command_at>=90 and power is not None):
            last=self.samples[-1] if self.samples else {}
            prior=self.samples[-2] if len(self.samples)>1 else {}
            complete=(last.get('heating_requested') and prior.get('heating_requested') and
                      last.get('heat_command_at')==prior.get('heat_command_at')==command_at and
                      prior.get('ts',0)-command_at>=90 and
                      all(r.get('quality') and r.get('power_source')=='balance' and
                          not r.get('power_estimated') and r.get('grid')=='on' for r in (prior,last)) and
                      now.timestamp()-last.get('ts',0)<360)
            avg=number(last.get('interval_power')) if complete else None
            if avg is not None and not 0<=avg<=10.5:
                avg=None
            if avg is not None or power>=2000:
                anchor=dict(return_c=probe,power_kw=min(9.,avg if avg is not None else power/1000))
        settings['modulation_anchor']=anchor
        effective=modulation_power(probe,profile,anchor=anchor) if probe_ok else None
        prior_effective=number(self.memory.get('planned_effective_kw'))
        if (effective is not None and prior_effective is not None and
                abs(effective-prior_effective)>=1.5 and
                time.time()-self.memory.get('last_plan_at',0)>=60):
            self.next_compute=0
        usable_weather=self.weather if time.time()-self.memory.get("weather_at",0)<self.cfg.get('weather_max_age',10800) else []
        usable_weather,weather_bias=anchor_weather(usable_weather,now.timestamp(),outside) if outside is not None else ([],0.)
        settings['environment_weather']=usable_weather
        peak=None;coast_overheat=False
        if quality and valid:
            peak,_=coast(room,floor,outside,fire,self.model)
            coast_overheat=coast_exceeds_profile(now,room,floor,outside,fire,self.model,settings)
        settings['actuator']=dict(action=self.memory.get('last_command'),changed_at=command_at or 0.,
            safety_stop=grid!='on' or not permitted or self.raw('mode')!='Автоматичне керування' or
                        room is not None and room>=upper or coast_overheat and not once)
        if time.time()<self.next_compute:
            segments=self.memory.get("planned_heat_segments",[])
            demand=any(s["start"] <= now.timestamp() < s["end"] for s in segments)
            if once:
                demand=True
            if outage_now or (room is not None and room>=upper) or coast_overheat and not once:
                demand=False
            action=decide(self.raw("mode"),self.raw("armed")=="on",bool(self.cfg.get("allow_actuation",False)),
                          room,probe_ok,grid,self.raw("thermostat") not in ("unknown","unavailable",None),
                          power is not None,self.model.get("validated",False),demand,valid)
            if getattr(self,'decision_context',None):
                self.decision_context.update(planned_demand=bool(any(s['start']<=now.timestamp()<s['end'] for s in segments)),
                    coast_peak=peak,coast_overheat=coast_overheat and not once,
                    demand=demand,action=action,
                    reason='Немає мережі' if grid=='off' else 'Стан мережі не підтверджено' if grid=='unknown' else
                        'Досягнуто верхню межу' if room is not None and room>=upper else
                        'Планове відключення' if outage_now else self.decision_context.get('reason'),
                    measurements=dict(room=room,raw_return=probe,estimated_floor=floor,outside=outside,fire=fire),
                    inhibitions=[key for key,value in dict(grid=grid!='on',upper=room is not None and room>=upper,
                        outage=outage_now,manual=not permitted or self.raw('mode')!='Автоматичне керування').items() if value])
            self.decision_event('tick_change',action=action)
            self.apply_action(action,False)
            self.fan_tick(grid,fire_active)
            self.publish("sensor.heating_heartbeat",now.isoformat(),friendly_name="Клімат — останній цикл",device_class="timestamp")
            return
        self.next_compute=time.time()+300
        self.resume_temporary_fallback()
        self.memory['last_plan_at']=time.time()
        self.memory['planned_effective_kw']=effective
        self.publish_system_settings()
        previous_reference=self.memory.get('plan_reference')
        forecast=None
        snapshot=None
        if quality and valid:
            forecast,plan_reference=stable_plan(now,room,floor,outside,fire,self.model,usable_weather,settings,
                          power_kw=3.0,previous=self.memory.get('plan_reference'))
            self.run_diagnostic('temperature_history',self.observe_temperature_history,now,settings,room,power,
                                source=='Вітальня' and room is not None,forecast)
            self.run_diagnostic('power_recommendation',self.update_power_recommendation,now,room,floor,outside,fire,usable_weather,settings,signature)
            forecast['cycle_energy']=self.run_diagnostic('cycle_projection',self.cycle_budget,now,forecast,dict(weather=usable_weather,
                grid={k:outage_data.get(k) for k in ('mode','valid','outages')},
                thermal_state=dict(room=room,floor=floor,fire=fire,coast_peak=peak,
                    gain={k:((settings.get('environment') or {}).get('solar') or {}).get(k) for k in ('coefficient','validated','observed_gain')}),
                ventilation=dict(model=settings.get('ventilation_model'),expected=settings.get('ventilation_expected')),
                power=dict(physical_mode=profile.get('physical_mode'),equipment_version=settings.get('equipment_version'),bands=[{k:b.get(k) for k in ('return_min','return_max','measured_kw')} for b in profile.get('bands',[])],anchor=anchor),
                manual=dict(mode=self.raw('mode'),armed=self.raw('armed')),
                fallback=bool(self.memory.get('temporary_fallback')),model=self.model),default={})
            forecast['stress_scenarios']=self.run_diagnostic('stress_scenarios',stress_scenarios,
                now,room,floor,outside,fire,self.model,usable_weather,settings,forecast,
                default=[dict(name='unavailable',available=False,hypothetical=True)])
            snapshot=self.remember_plan_inputs(now,room,floor,outside,fire,usable_weather,settings,forecast,previous_reference)
            self.memory['plan_reference']=plan_reference
            self.memory['plan_execution']=dict(plan_id=forecast.get('plan_id'),calculated_at=now.timestamp(),status='calculated')
            self.publish("sensor.heating_forecast",forecast["minimum"],friendly_name="Клімат — мінімум прогнозу",
                         unit_of_measurement="°C",device_class="temperature",curve=forecast["curve"],
                         actual_curve=[{"ts":r["ts"],"temperature":r["room"]} for r in self.samples[-72:] if r.get("room") is not None],
                         cycle_energy=forecast['cycle_energy'],
                         stress_scenarios=forecast['stress_scenarios'],
                         planned_kwh=forecast["energy"],horizon_hours=forecast["horizon_hours"],
                         validated=self.model.get("validated",False) and not environment_quality['degraded'] and not profile.get('assumed',True),
                         power_assumption=profile.get('assumption'),ventilation_validated=bool((settings.get('ventilation_model') or {}).get('validated')),
                         equipment_version=settings.get('equipment_version'),
                         structural_model_validated=self.model.get('validated',False),
                         reliability=environment_quality,estimated_floor=floor,
                         local_weather_bias=round(weather_bias,2),
                         external_heat=((settings.get('environment') or {}).get('solar') or {}).get('observed_gain',0.),
                         uncertainty='Точність знижена; обережне планування' if environment_quality['degraded'] else 'Оцінка; калібрування триває',
                         created_at=now.isoformat(),execution_state='calculated',outage_shifted=forecast["outage_shifted"],
                         plan_stable=forecast['plan_stable'],stability_reason=forecast['stability_reason'],
                         outage_preheat=forecast["outage_preheat"],outside_minutes=forecast["outside_minutes"],
                         comfort_shortfall=forecast["comfort_shortfall"],outage_status=forecast["outage_status"],
                         heat_segments=forecast["heat_segments"],schedule_updated=forecast["schedule_updated"],
                         fireplace_segments=project_fireplace(now.timestamp(),fire,fire_active),
                         schedule_known_until=forecast["schedule_known_until"],
                         weather_known_until=forecast["weather_known_until"],
                         conditional_heat_minutes=forecast["conditional_heat_minutes"],
                         schedule_complete=forecast["schedule_complete"],
                         outage_mode=forecast.get('outage_mode',outage_data['mode']),
                         weather_complete=forecast["weather_complete"],
                         weather_missing_minutes=forecast.get("weather_missing_minutes"),
                         split_comfort_applied=forecast["split_comfort_applied"],
                         session_kwh=forecast["session_kwh"],
                         night_air_target=forecast["night_air_target"],
                         split_reason=forecast["split_reason"],
                         morning_min_minutes=forecast["morning_min_minutes"],
                         morning_runs_minutes=forecast["morning_runs_minutes"],
                         planning_policy_version=4,morning_policy_version=1,
                         search_policy_version=forecast.get('search_policy_version'),search_status=forecast.get('search_status'),
                         morning_reserve=forecast.get('morning_reserve'),plan_id=forecast.get('plan_id'),
                         power_model='return_modulation_v1',effective_power_kw=round(effective,3),
                         power_learning_intervals=profile['intervals'],
                         power_learning_minutes=profile['minutes'],
                         power_prior_kw=3.,power_estimate_provisional=True,
                         actual_session_kwh=self.actual_session_energy())
            one_hour=next((r for r in forecast["curve"] if r["ts"]>=time.time()+3500),None)
            if one_hour:
                expected_energy=sum(r["kwh"] for r in forecast["curve"][:2])
                self.memory.setdefault("predictions",[]).append({**one_hour,"version":self.model["version"],
                    "start_energy":self.val("energy"),"expected_energy":expected_energy})
                candidate=self.memory.get('pending_model')
                if candidate and candidate['base_version']==self.model['version']:
                    shadow=replay(now,room,floor,outside,fire,candidate['candidate'],usable_weather,settings,
                        forecast['heat_segments'],forecast['split_comfort_applied'],3.,reserve_checked=True)
                    point=next((r for r in (shadow or {}).get('curve',[]) if r['ts']==one_hour['ts']),None)
                    if point:
                        self.memory['predictions'][-1].update(learning_attempt_id=candidate['attempt_id'],
                                                            candidate_temperature=point['temperature'])
        else:
            self.publish("sensor.heating_forecast","unavailable",friendly_name="Клімат — мінімум прогнозу",curve=[],
                         reason="Недостатньо актуальних даних або некоректні межі",planning_policy_version=4,created_at=now.isoformat())
        demand=bool(forecast and forecast["heat"])
        # The optimiser already balances comfort, stored heat and tariff.
        # Reaching the soft air target must not cancel its scheduled session.
        # Actual upper limits, coast overheating and outages still veto heat.
        planned_heat=demand
        if once:
            demand=True
        if room is not None and room>=upper:
            demand=False
        preheat=bool(forecast and forecast["outage_preheat"])
        morning_comfort=bool(demand and forecast and forecast['split_comfort_applied'] and
                             in_window(now,'05:00','07:00'))
        if (peak is not None and room is not None and room>=lower and
                (coast_overheat or peak>=target and not planned_heat and not preheat and not morning_comfort)):
            demand=False
        if outage_now:
            demand=False
        action=decide(self.raw("mode"),self.raw("armed")=="on",bool(self.cfg.get("allow_actuation",False)),
                      room,probe_ok,grid,self.raw("thermostat") not in ("unknown","unavailable",None),
                      power is not None,self.model.get("validated",False),demand,valid)
        reserve=(forecast or {}).get('morning_reserve') or {}
        reason=("Немає мережі" if grid=="off" else "Стан мережі не підтверджено" if grid=="unknown" else
                "Некоректні межі температури або часу" if not valid else
                "Недостатньо актуальних вимірювань" if not quality else
                "Планове відключення 3.1 — нагрів не планується" if outage_now else
                "Завчасний нагрів перед відключенням 3.1" if demand and preheat else
                "Ранковий підігрів підлоги" if demand and morning_comfort else
                reserve['impossibility_reason'] if not demand and reserve.get('impossibility_reason') else
                'Запас до наступного вікна умовний: недостатньо перевірених даних' if not demand and reserve.get('reserve_sufficient') is None and reserve else
                "Нагрів не потрібен / запас тепла" if peak is not None and peak>=target and room>=lower and not demand else
                "Одноразовий нагрів після повернення мережі" if once else
                "Заплановано нагрів" if demand else "Накопиченого тепла достатньо / очікування вікна")
        self.publish("sensor.heating_temperature",room if room is not None else "unavailable",
                     friendly_name="Клімат — температура керування",unit_of_measurement="°C",
                     device_class="temperature",state_class="measurement",source=source,
                     thermostat_bias=self.memory.get("thermostat_bias"))
        self.publish("sensor.heating_coast_peak",round(peak,2) if peak is not None else "unavailable",
                     friendly_name="Клімат — оцінка інерційного піку",unit_of_measurement="°C",
                     device_class="temperature",validated=self.model.get("validated",False))
        if forecast:self.memory['plan_execution'].update(status='intended',intended_action=action)
        self.publish("sensor.heating_controller","Спостереження" if action=="observe" else reason,
                     comfort_zone=comfort_zone,target_temperature=target,lower_temperature=lower,upper_temperature=upper,
                     friendly_name="Клімат — контролер",reason=reason,proposed_action="Нагрів · зсув уставки +2 °C" if demand else "Очікування · зсув уставки −2 °C",
                     control_method="heat_offset_v2",thermostat_temperature=thermostat,
                     proposed_setpoint=thermostat+(2 if demand else -2) if thermostat is not None else None,
                     source=source,quality=quality,mode=self.raw("mode"),grid=grid,return_valid=probe_ok,
                     outage_preheat=preheat,outage_shifted=bool(forecast and forecast["outage_shifted"]),
                     outside_minutes=forecast["outside_minutes"] if forecast else 0,
                     comfort_shortfall=bool(forecast and forecast["comfort_shortfall"]),
                     model_ready=self.model.get("validated",False),model_version=self.model["version"])
        self.publish("sensor.heating_model","Точність знижена" if environment_quality['degraded'] else "Перевірена" if self.model.get("validated") else "Збір даних",
                     reliability=environment_quality,environment_version=1,
                     friendly_name="Клімат — модель",parameters=self.model,samples=len(self.samples),
                     usable_pairs=len(transitions(self.samples)),hour_mae=self.hour_mae(),
                     thermostat_bias=self.memory.get("thermostat_bias"),last_analysis=self.memory.get("last_analysis"),
                     last_result=self.memory.get("last_result","Навчання ще не виконувалось"),
                     learning_attempt=self.memory.get('learning_attempt'),learning_success=self.memory.get('learning_success'),
                     learning_storage_error=self.memory.get('learning_storage_error'),
                     forecast_validation=self.run_diagnostic('validation_summary',self.validation_summary,default={}),
                     journal=self.memory.get("journal",[])[-12:],
                     power_learning=profile,
                     effective_power_kw=round(effective,3) if effective is not None else None,
                     power_learning_status='Навчання модуляції' if profile['intervals'] else 'Очікуємо підтверджений нагрів')
        self.run_diagnostic('equipment_publish',self.publish_equipment,ventilation_support)
        self.run_diagnostic('plan_history_publish',self.publish_plan_history)
        self.run_diagnostic('ai_summary',self.publish_ai_summary,refresh=True)
        self.memory["planned_heat_segments"]=forecast["heat_segments"] if forecast else []
        self.memory["demand"]=demand
        self.decision_context=dict(settings=settings,forecast=forecast,action=action,reason=reason,
            coast_overheat=coast_overheat and not once,
            planned_demand=planned_heat,demand=demand,coast_peak=peak,
            measurements=dict(room=room,raw_return=probe,estimated_floor=floor,outside=outside,fire=fire,
                              external_gain=((settings.get('environment') or {}).get('solar') or {}).get('observed_gain')),
            previous_reserve=(previous_reference or {}).get('morning_reserve'),
            inhibitions=[key for key,value in dict(grid=grid!='on',upper=room is not None and room>=upper,
                inertia=coast_overheat,outage=outage_now,quality=not quality,settings=not valid,
                manual=not permitted or self.raw('mode')!='Автоматичне керування').items() if value])
        if snapshot is None:
            snapshot=self.remember_plan_inputs(now,room,floor,outside,fire,usable_weather,settings,forecast,previous_reference)
        self.decision_event('full_recompute',force=True,snapshot=snapshot)
        if forecast:
            self.run_diagnostic('forecast_validation',self.validate_reserve_predictions,now,room,power,quality and source=='Вітальня',forecast,
                                             measurements=dict(floor=floor,outside=outside,fire=measured_fire))
        self.apply_action(action,once)
        self.fan_tick(grid,fire_active)
        self.memory['last_full_recompute']=now.isoformat()
        self.journal(reason)
        self.publish("sensor.heating_heartbeat",now.isoformat(),friendly_name="Клімат — останній цикл",device_class="timestamp")
        self.save()
    def hour_mae(self):
        values=self.memory.get("hour_errors",[])
        return round(statistics.mean(values),3) if values else None
    def actual_session_energy(self):
        if self.memory.get('last_command')!='heat':
            return None
        before=number(self.memory.get('heat_session_start_energy'))
        after=self.val('energy')
        return round(after-before,4) if before is not None and after is not None and after>=before else None
    def summary(self):
        end=time.time();start=end-86400
        rows=[r for r in self.samples if start<r['ts']<=end]
        good=[r for r in rows if r.get("quality")]
        temperatures=[r["room"] for r in good]
        energy=[r for r in rows if number(r.get("energy")) is not None]
        covered=estimated=known_power=comfort_seconds=below=above=0.
        for a,b in zip(self.samples,self.samples[1:]):
            seconds=b['ts']-a['ts']
            overlap=max(0.,min(end,b['ts'])-max(start,a['ts']))
            if not 0<seconds<=420 or not overlap:
                continue
            if a.get('quality') and b.get('quality'):
                covered+=overlap
                policy=a.get('comfort_policy')
                if policy and policy==b.get('comfort_policy') and number(a.get('room')) is not None and number(b.get('room')) is not None:
                    left=max(start,a['ts']);right=min(end,b['ts'])
                    t=left
                    while t<right:
                        stop=min(right,t+30);mid=(t+stop)/2
                        temperature=a['room']+(b['room']-a['room'])*(mid-a['ts'])/seconds
                        _,lo,hi,_=comfort_at(datetime.fromtimestamp(mid,self.climate_tz),policy)
                        duration=stop-t
                        below+=max(0.,lo-temperature)*duration/3600
                        above+=max(0.,temperature-hi)*duration/3600;comfort_seconds+=duration
                        t=stop
            if number(a.get('power')) is not None and number(b.get('power')) is not None:
                known_power+=overlap
                if any(r.get('power_estimated') and not (r.get('power_source')=='fallback_grid_off' and
                       r.get('grid')=='off' and r.get('power')==0) for r in (a,b)):
                    estimated+=overlap
        stamps=[start,*[r['ts'] for r in rows],end]
        return {"samples":len(rows),"valid_samples":len(good),
                "coverage":round(min(1.,covered/86400),3),
                "row_quality_fraction":round(len(good)/max(1,len(rows)),3),
                "expected_samples":288,"covered_minutes":round(covered/60,1),
                "max_gap_seconds":round(max(b-a for a,b in zip(stamps,stamps[1:])),1),
                "age_seconds":round(end-rows[-1]['ts'],1) if rows else None,
                "room_measurement_age_seconds":round(max(0.,end-good[-1]['room_reported_at']),1)
                    if good and number(good[-1].get('room_reported_at')) is not None else None,
                "estimated_power_fraction":round(estimated/known_power,3) if known_power else None,
                "comfort_covered_minutes":round(comfort_seconds/60,1),
                "below_degree_hours":round(below,3) if comfort_seconds else None,
                "above_degree_hours":round(above,3) if comfort_seconds else None,
                "temperature_min":min(temperatures) if temperatures else None,
                "temperature_max":max(temperatures) if temperatures else None,
                "measured_kwh":round(energy[-1]["energy"]-energy[0]["energy"],4) if len(energy)>1 else None,
                "hours_collected":round((rows[-1]["ts"]-rows[0]["ts"])/3600,2) if len(rows)>1 else 0.,
                "hour_forecast_mae":self.hour_mae(),"forecast_validation":self.validation_summary()}
    def energy_tick(self):
        total=self.val("energy")
        previous=self.memory.get("energy_last")
        bins=self.memory.setdefault("energy_bins",[])
        stamp=time.time()
        if total is not None and previous and total>=previous["total"]:
            delta=total-previous["total"]
            if delta>0:
                night=in_window(self.now(),"23:00","07:00")
                rate=self.val("tariff")
                bins.append({"start":previous["ts"],"end":stamp,"kwh":round(delta,6),
                             "night":night,"cost":round(delta*rate,5) if rate is not None else None,
                             "quality":self.val("power") is not None,"historical":False,
                             "estimated":estimated_power(getattr(self,"power_source",""))})
        if total is not None:
            self.memory["energy_last"]={"ts":stamp,"total":total}
        bins[:]=[b for b in bins if b["end"]>stamp-32*86400]
        rolling=0.
        approximate=False
        for b in bins:
            if b["end"]<=stamp-86400:
                continue
            weight=min(1.,max(0.,(b["end"]-max(b["start"],stamp-86400))/max(1,b["end"]-b["start"])))
            rolling+=b["kwh"]*weight
            approximate |= bool(b.get("historical"))
        self.publish("sensor.heating_energy_24h",round(rolling,3),friendly_name="Котел — останні 24 години",
                     unit_of_measurement="kWh",device_class="energy",estimated_hourly_history=approximate)
        month_start=self.now().replace(day=1,hour=0,minute=0,second=0,microsecond=0).timestamp()
        live=[b for b in bins if b["end"]>=month_start and not b.get("historical")]
        for suffix,filtered in (("night",[b for b in live if b["night"]]),("day",[b for b in live if not b["night"]])):
            self.publish("sensor.heating_energy_"+suffix,round(sum(b["kwh"] for b in filtered),3),
                         friendly_name="Котел — "+("нічна" if suffix=="night" else "денна")+" енергія від запуску",
                         unit_of_measurement="kWh",device_class="energy",coverage="Від запуску кліматичного контролера")
        costs=[b["cost"] for b in live if b.get("cost") is not None]
        self.publish("sensor.heating_cost",round(sum(costs),2) if costs else 0,
                     friendly_name="Котел — вартість від запуску",unit_of_measurement="UAH",device_class="monetary",
                     coverage="Від запуску контролера; поточний тариф з HA",tariff_available=self.val("tariff") is not None)
    def weather_event(self,event,data,kwargs=None,**extra):
        rows=[]
        for r in data.get("forecast",[]):
            temp=number(r.get("temperature"))
            try:
                ts=datetime.fromisoformat(r["datetime"].replace("Z","+00:00")).timestamp()
            except (KeyError,TypeError,ValueError):
                continue
            if temp is not None:
                rows.append({"ts":ts,"temperature":temp,"condition":r.get("condition")})
        if rows:
            self.weather=sorted(rows,key=lambda r:r["ts"])
            self.memory["weather_at"]=time.time()
            self.save()
            self.next_compute=0
    def night_event(self,event,data,kwargs=None,**extra):
        self.memory["night_fan_until"]=time.time()+900
        self.save()
    def fan_tick(self,grid,fire_active):
        enabled=bool(self.cfg.get("allow_actuation",False)) and self.raw("fan_control")=="on"
        mode=self.raw("fan_mode")
        auto_mode=mode in ('Авто','Автоматично')
        request=(fire_active or self.memory.get("night_fan_until",0)>time.time() or mode=="Ручний запуск") and mode!="Зупинити"
        backup_entity=self.cfg.get('fan_backup','input_boolean.heating_fan_backup_verified')
        backup_state=self.get_state(backup_entity)
        backup_verified=backup_state=='on' if backup_state is not None else self.cfg.get('fan_backup_verified',False) is True
        inverter_ok=self.raw('connection')=='on' and self.heartbeat_fresh(self.cfg.get('inverter_heartbeat',[]),90)
        power_ok=grid=='on' or backup_verified and inverter_ok
        reason="Камін" if fire_active else "Нічна циркуляція" if self.memory.get("night_fan_until",0)>time.time() else "Ручний запуск" if request else "Не потрібна"
        if mode=='Зупинити':
            reason='Зупинено вручну'
        if not power_ok and request:
            reason='Запит є; інвертор недоступний або дані застаріли' if backup_verified else 'Запит є; резервне живлення вентилятора не підтверджено'
        if not enabled:
            reason='Автоматичну циркуляцію вимкнено'
        standard=max(1,min(100,int(self.val("fan_speed",38))))
        intense=number(self.get_state(self.cfg.get('fan_intense_speed','input_number.heating_fan_intense_speed')))
        intense=max(standard,min(100,int(intense if intense is not None else 60)))
        night=min(standard,max(1,min(100,int(self.val("fan_night_limit",38)))))
        p=system_values(self.cfg)
        level=1 if in_window(self.now(),p['fan_night_start'],p['fan_night_end']) else 3 if fire_active and self.memory.get('fire_phase')=='Інтенсивно горить' else 2
        percentage={1:night,2:standard,3:intense}[level]
        state=self.raw('fan')
        if state not in ('on','off'):
            reason='Вентилятор недоступний'
        elif enabled and not auto_mode and mode!='Зупинити' and not request and state=='on' and not self.memory.get('fan_owned'):
            reason='Ручне увімкнення; автоматичний запит відсутній'
        source='fireplace' if fire_active and request else 'night' if request and self.memory.get('night_fan_until',0)>time.time() else 'manual' if request else 'none'
        status=dict(friendly_name="Клімат — циркуляція",reason=reason,requested=request,
                    proposed_percentage=percentage,control_enabled=enabled,backup_verified=backup_verified,
                    power_available=power_ok,actual_state=state,selected_level=level,
                    controller_owned=bool(self.memory.get('fan_owned')),request_source=source,
                    manual_running=state=='on' and mode=='Ручний запуск',
                    level_percentages=dict(night=night,standard=standard,intense=intense))
        self.publish("sensor.heating_fan","Спостереження" if not enabled else reason,**status)
        if not enabled or state not in ('on','off') or self.update_in_progress():
            return
        def send(service):
            key=(service,percentage if service=='fan/turn_on' else None)
            if key==getattr(self,'fan_command_key',None) and time.time()-getattr(self,'fan_command_at',0)<30:
                return False
            self.fan_command_key=key
            self.fan_command_at=time.time()
            try:
                self.call_service(service,entity_id=self.cfg['fan'],**({'percentage':percentage} if service=='fan/turn_on' else {}))
                return True
            except Exception as exc:
                # A fan error must not put an otherwise healthy boiler into native-auto recovery.
                self.log('Circulation command failed: %s',exc,level='WARNING')
                status['reason']='Вентилятор не прийняв команду; повторимо запит'
                self.publish('sensor.heating_fan',status['reason'],**status)
                return False
        if request and power_ok:
            if state!="on" or self.val("fan",attribute="percentage")!=percentage:
                if send('fan/turn_on') and state=='off':
                    self.memory["fan_owned"]=True
                    self.save()
        # Auto owns the desired state, even when a reload lost fan_owned.
        elif auto_mode or self.memory.get("fan_owned") or mode=="Зупинити":
            last=getattr(self,'fan_command_key',(None,None))[0]
            if state=='on' or last=='fan/turn_on':
                send('fan/turn_off')
            elif self.memory.get('fan_owned'):
                self.memory["fan_owned"]=False
                self.save()
    def daily_learn(self,kwargs=None,**extra):
        if self.memory.get('state_load_error'):
            return
        try:
            baseline=self.learning_baseline()
            candidate=learn(self.model,self.samples,baseline)
            self.consider(candidate,"Локальне навчання",apply=True)
            self.memory["last_analysis"]=self.now().isoformat()
            temp=self.telemetry.with_suffix('.learning.tmp')
            temp.write_text("".join(json.dumps(r,ensure_ascii=False,allow_nan=False)+"\n" for r in self.samples))
            os.replace(temp,self.telemetry)
            self.memory.pop('learning_storage_error',None)
            self.save()
        except (OSError,ValueError,TypeError) as exc:
            self.memory['learning_storage_error']=str(exc)
            self.log('Learning failed; current model retained: %s',exc,level='WARNING')
        self.next_compute=0
    def learning_baseline(self):
        date=self.now().date().isoformat()
        day=self.memory.get('learning_day')
        if not day or day.get('date')!=date:
            day=dict(date=date,baseline={key:self.model[key] for key in BOUNDS})
            self.memory['learning_day']=day
        return day['baseline']
    def consider(self,candidate,source,apply=False,rejection=None):
        baseline=self.learning_baseline()
        details={}
        accepted,reason,error=validate_candidate(self.model,candidate or {},self.samples,baseline=baseline,diagnostics=details)
        if rejection:
            accepted=False;reason=rejection
        if self.memory.get('state_load_error'):
            accepted=False;reason='Навчання призупинено: пошкоджений збережений стан'
        old=dict(self.model)
        values={k:(number((candidate or {}).get(k)) if not isinstance((candidate or {}).get(k),bool) else (candidate or {})[k]) for k in BOUNDS}
        pending=self.memory.get('pending_model')
        if accepted and apply and pending and pending['base_version']==old['version'] and all(
                pending['candidate'][k]==values[k] for k in BOUNDS):
            return True  # repeated events do not restart prospective validation
        attempt=dict(attempt_id=uuid4().hex,time=self.now().isoformat(),source=source,model_version=old['version'],
            old={k:old[k] for k in BOUNDS},candidate=values,
            delta={k:values[k]-old[k] if isinstance(values[k],(int,float)) and not isinstance(values[k],bool) else None for k in BOUNDS},
            relative_delta={k:(values[k]-old[k])/abs(old[k]) if old[k] and isinstance(values[k],(int,float)) and not isinstance(values[k],bool) else None for k in BOUNDS},
            baseline=dict(baseline),baseline_date=self.memory['learning_day']['date'],
            status='proposed' if accepted else 'rejected',reason=reason,**details)
        self.memory['learning_attempt']=attempt
        if accepted and apply:
            candidate={**old,**values,'version':old['version']+1,'validated':False}
            if old.get('validated'):
                self.memory['pending_model']=dict(attempt_id=attempt['attempt_id'],base_version=old['version'],
                    candidate=candidate,errors=[])
                reason='Кандидат очікує перевірки годинного прогнозу; чинна модель працює'
                attempt.update(status='pending',reason=reason)
                self.memory['pending_model']['attempt']=dict(attempt)
            else:
                self.memory['previous_model']=old
                self.model=candidate
                self.memory['hour_errors']=[];self.memory['predictions']=[]
                reason='Кандидат для початкового навчання; перевіряємо годинний прогноз'
                attempt.update(status='initial_validation',reason=reason)
        self.memory["last_result"]=source+": "+reason
        try:
            self.memory.pop('learning_storage_error',None)
            self.journal(self.memory["last_result"],"model")
            self.save()
        except OSError as exc:
            self.model=old;self.memory['model']=old
            self.memory.pop('pending_model',None)
            self.memory['learning_storage_error']=str(exc)
            attempt.update(status='storage_error',reason='Не вдалося зберегти спробу; чинна модель працює')
            reason=attempt['reason']
            self.memory['last_result']=source+': '+reason
            accepted=False
        self.decision_event('learning_attempt',reason=reason,force=True)
        return accepted
    def confirm_learning_candidate(self):
        pending=self.memory.get('pending_model')
        if not pending or len(pending.get('errors',[]))<12:
            return
        candidate=pending['candidate'];old=dict(self.model)
        baseline=self.learning_baseline();details={}
        accepted,reason,_=validate_candidate(old,candidate,self.samples,baseline=baseline,diagnostics=details)
        errors=pending['errors'][-12:]
        old_mae=statistics.mean(r['old'] for r in errors);new_mae=statistics.mean(r['new'] for r in errors)
        if accepted and pending['base_version']!=old['version']:
            accepted=False;reason='Чинна модель змінилася під час перевірки кандидата'
        if accepted and not (new_mae<=.3 and new_mae<old_mae*.98):
            accepted=False;reason='Кандидат не поліпшує підтверджений годинний прогноз'
        attempt=dict(pending.get('attempt',self.memory.get('learning_attempt',{})))
        attempt.update(**details,old_hour_mae=old_mae,new_hour_mae=new_mae,confirmation_time=self.now().isoformat(),
            status='applied' if accepted else 'rejected',reason='Підтверджено годинним прогнозом' if accepted else reason)
        self.memory['learning_attempt']=attempt
        self.memory.pop('pending_model',None)
        changed_keys=('previous_model','hour_errors','predictions','reserve_errors','reserve_predictions','learning_success')
        prior={key:self.memory.get(key) for key in changed_keys}
        if accepted:
            self.memory['previous_model']=old
            self.model=dict(candidate,version=old['version']+1,validated=True)
            self.memory['hour_errors']=[r['new'] for r in errors];self.memory['predictions']=[]
            self.memory['reserve_errors']=[];self.memory['reserve_predictions']=[]
            self.memory['learning_success']=dict(attempt_id=pending['attempt_id'],time=self.now().isoformat(),
                model_version=self.model['version'],old_holdout_error=details.get('old_holdout_error'),
                new_holdout_error=details.get('new_holdout_error'),hour_mae=new_mae)
            self.next_compute=0
        try:
            self.memory.pop('learning_storage_error',None)
            self.save()
        except OSError as exc:
            self.model=old;self.memory['model']=old
            for key,value in prior.items():
                if value is None:self.memory.pop(key,None)
                else:self.memory[key]=value
            self.memory['learning_storage_error']=str(exc)
            attempt.update(status='storage_error',reason='Не вдалося зберегти кандидата; чинна модель працює')
        self.decision_event('learning_confirmation',reason=attempt['reason'],force=True)
    def proposal_changed(self,entity,attribute,old,new,kwargs=None,**extra):
        if not new or new==self.memory.get("last_proposal"):
            return
        try:
            proposed=json.loads(new)
            if not isinstance(proposed,dict) or any(k not in BOUNDS for k in proposed):
                raise ValueError("Дозволені лише коефіцієнти моделі")
            candidate={**self.model,**proposed}
            self.consider(candidate,"ШІ",apply=self.raw("ai_auto_apply")=="on")
        except (TypeError,ValueError) as exc:
            self.consider({},'ШІ',rejection='Некоректна пропозиція: '+str(exc))
        self.memory["last_proposal"]=new
        self.save()
        self.next_compute=0
    def rollback(self,entity,attribute,old,new,kwargs=None,**extra):
        self.learning_baseline()  # explicit rollback never rebases the automatic daily allowance
        self.memory.pop('pending_model',None)
        previous=self.memory.get("previous_model")
        if previous:
            self.memory["previous_model"]=dict(self.model)
            self.model=previous
            self.memory["hour_errors"]=[]
            self.memory["predictions"]=[]
            self.journal("Відновлено попередню версію моделі","model")
            self.next_compute=0
        else:
            self.memory["last_result"]="Попередньої версії моделі ще немає"
            self.save()


