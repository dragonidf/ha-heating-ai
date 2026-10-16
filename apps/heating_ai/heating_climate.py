"""Home Assistant AppDaemon adapter. Deployment defaults to strict observation."""
from appdaemon.plugins.hass.hassapi import Hass
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo
from pathlib import Path
from threading import RLock
import json
import os
import statistics
import time
import calendar
import shutil
from heating_fallback import fallback_power, repair_samples
from heating_outages import schedule, blocked
from heating_model import (DEFAULT_MODEL, BOUNDS, number, in_window, plan, coast,
                           nominal_mode, decide, learn, validate_candidate, transitions, HeatingPowerEstimator,
                           learn_modulation, modulation_power,comfort_at,coast_exceeds_profile)
from heating_recovery import HeatingRecovery
from heating_stability import stable_plan
from heating_environment import observe_floor,observe_gain,reliability,daylight,anchor_weather
from heating_ui import install_panel, project_fireplace, verify_installation

class HeatingClimate(HeatingRecovery,Hass):
    def initialize(self):
        self.cfg=self.args
        if self.update_in_progress():
            self.run_in(self.initialize_after_update,5)
            return
        verify_installation()
        self.climate_tz=ZoneInfo(self.cfg.get("timezone","Europe/Kiev"))
        self.directory=Path(self.cfg.get("data_dir","/config/heating_data"))
        self.directory.mkdir(parents=True,exist_ok=True)
        self.statefile=self.directory/"state.json"
        self.telemetry=self.directory/"telemetry.jsonl"
        try:
            self.memory=json.loads(self.statefile.read_text())
        except (OSError,ValueError):
            self.memory={}
        self.model=self.memory.get("model",dict(DEFAULT_MODEL))
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
        self.power_estimator=HeatingPowerEstimator(confirm=10.)
        self.memory['modulation_profile']=learn_modulation(self.samples)
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
        self.listen_event(self.weather_event,"heating_weather_forecast")
        self.listen_event(self.night_event,"heating_fan_night_request")
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
        for entity in self.cfg.get("fallback_phase_power",[]):
            self.listen_state(self.balance_changed,entity)
        if self.cfg.get("power_fault"):
            self.listen_state(self.balance_changed,self.cfg["power_fault"])
        for key in ("outage_calendar", "outage_emergency"):
            if self.cfg.get(key):
                self.listen_state(self.settings_changed,self.cfg[key],attribute="all")
        self.run_every(self.tick,self.datetime()+timedelta(seconds=2),15)
        self.runtime_ready=True
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
        except (ValueError,TypeError):
            return False
    def heartbeat_fresh(self,entities,seconds):
        return any(self.fresh(entity,seconds) for entity in entities)
    def save(self):
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
        primary_ok=primary is not None and 5<primary<40 and self.heartbeat_fresh(self.cfg["room_heartbeat"],4*3600)
        thermostat=self.val("thermostat",attribute="current_temperature")
        if primary_ok:
            return primary,"Вітальня",primary,thermostat
        bias=self.memory.get("thermostat_bias")
        if thermostat is not None and bias is not None and self.raw("thermostat") not in ("unavailable","unknown",None):
            return thermostat-bias,"Термостат · резерв",primary,thermostat
        return None,"Немає перевіреного датчика",primary,thermostat
    def sample(self,room,probe,outside,power,fire,source,primary,thermostat,quality):
        stamp=time.time()
        if stamp-self.last_sample<290:
            return
        self.last_sample=stamp
        row={"ts":stamp,"room":room,"return":probe,"outside":outside,"power":power,
             "fire":fire,"quality":quality and source=="Вітальня","source":source,
             "energy":self.val("energy"),"thermostat":thermostat}
        row["measurement_version"]=3
        row['environment_version']=1
        row['floor_estimate']=self.memory.get('floor_observer',{}).get('estimate')
        row['daylight_weight']=daylight(self.now())
        row['external_gain']=self.memory.get('solar_observer',{}).get('observed_gain',0.)
        row["power_source"]=getattr(self,"power_source","balance")
        row["power_estimated"]=getattr(self,"fallback_interval",False)
        row['heating_requested']=self.memory.get('last_command')=='heat'
        row['heat_command_at']=self.memory.get('command_at') if row['heating_requested'] else None
        row['grid']=self.grid_state()
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
        with self.telemetry.open("a") as file:
            file.write(json.dumps(row,ensure_ascii=False,allow_nan=False)+"\n")
        pairs=[r for r in self.samples[-288:] if r.get("source")=="Вітальня" and
               number(r.get("thermostat")) is not None and number(r.get("room")) is not None]
        if len(pairs)>=12 and pairs[-1]["ts"]-pairs[0]["ts"]>=4*3600 and max(r["room"] for r in pairs)-min(r["room"] for r in pairs)>=.5:
            self.memory["thermostat_bias"]=round(statistics.median(r["thermostat"]-r["room"] for r in pairs),2)
        pending=self.memory.setdefault("predictions",[])
        matured=[p for p in pending if p["ts"]<=stamp]
        if room is not None and source=="Вітальня":
            errors=self.memory.setdefault("hour_errors",[])
            for p in matured:
                energy=self.val("energy")
                matched=(energy is not None and p.get("start_energy") is not None and
                         abs(energy-p["start_energy"]-p.get("expected_energy",0))<=.5)
                if stamp-p["ts"]<=420 and p["version"]==self.model["version"] and matched:
                    errors.append(abs(p["temperature"]-room))
            del errors[:-48]
        self.memory["predictions"]=[p for p in pending if p["ts"]>stamp]
        if (len(self.memory.get("hour_errors",[]))>=12 and self.hour_mae()<=.3
                and self.memory.get("previous_model") and len(transitions(self.samples))>=96):
            self.model["validated"]=True
        self.save()
    def fireplace(self,room):
        stamp=time.time()
        temperature=self.val("fireplace")
        valid=temperature is not None and room is not None and self.heartbeat_fresh(self.cfg["fireplace_heartbeat"],4*3600)
        delta=max(0.,temperature-room) if valid else 0.
        recent=[r for r in self.samples if 0<stamp-r["ts"]<=1800 and number(r.get("fire")) is not None]
        slope=(delta-recent[0]["fire"])*3600/max(300,stamp-recent[0]["ts"]) if recent else 0.
        # Remove binary floating-point noise at boundaries; keep raw delta for the model.
        signal=round(delta,6);trend=round(slope,6)
        previous=self.memory.get("fire_phase","Неактивний")
        tracker=self.memory.get('fire_circulation',{})
        was_active=tracker.get('active',self.memory.get('fan_owned',False) and previous in ('Горить','Згасає','Інтенсивно горить'))
        last_valid=number(tracker.get('last_valid'))
        continuous=last_valid is not None and 0<=stamp-last_valid<=90
        below=number(tracker.get('below_since')) if continuous else None
        active=False
        if valid:
            if signal>=5:
                active=True;below=None
            elif was_active and not (signal<4 and trend<=-2):
                if signal<3:
                    below=stamp if below is None else below
                    active=stamp-below<300
                else:
                    active=True;below=None
        if not active:
            below=None
        self.memory['fire_circulation']=dict(active=active,below_since=below,last_valid=stamp if valid else None)
        if not valid:
            phase="Датчик недоступний"
        elif trend<-.8 and signal>=2:
            phase="Згасає"
        elif signal>=10:
            phase="Інтенсивно горить"
        elif signal>=5:
            phase="Горить"
        elif signal>=2 and trend>.8:
            phase="Розпалювання"
        elif active:
            phase="Згасає"
        else:
            phase="Неактивний"
        self.memory["fire_phase"]=phase
        remaining=max(0,int(300-(stamp-below))) if below is not None else 0
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
        self.update_heating_power()
    def update_heating_power(self):
        stamp=time.time()
        primary=self.val("power_balance")
        failed=primary is None or (self.cfg.get("power_fault") and self.get_state(self.cfg["power_fault"])=="on")
        phases=[number(self.get_state(e)) for e in self.cfg.get("fallback_phase_power",[])]
        fresh=self.heartbeat_fresh(self.cfg.get("fallback_phase_power",[])+self.cfg.get("input_heartbeat",[]),300)
        alive=self.raw("connection")=="on" and self.heartbeat_fresh(self.cfg["inverter_heartbeat"],90)
        grid_off=alive and self.raw("grid")=="off" and self.val("grid_voltage",999)<50
        raw,source,active=fallback_power(primary,failed,self.raw("thermostat"),
            self.val("thermostat",attribute="temperature"),self.val("thermostat",attribute="current_temperature"),
            phases,fresh,self.memory.get("last_measured_stage_kw"),grid_off)
        if source!=getattr(self,"power_source",None):
            self.power_estimator.update(stamp,None)
        self.power_source=source
        self.fallback_interval=getattr(self,"fallback_interval",False) or source.startswith("fallback")
        if raw is None:
            self.measurement_valid_since=stamp
        power=self.power_estimator.update(stamp,raw)
        if source=="balance" and power is not None and power>=2000:
            stage=nominal_mode(power,self.stage)
            if stage in ("3 кВт","6 кВт","9 кВт"):
                self.memory["last_measured_stage_kw"]=int(stage.split()[0])
        self.publish("sensor.heating_power_confirmed",power,
                     friendly_name="Котел — підтверджена потужність ТЕНів",
                     unit_of_measurement="W",device_class="power",state_class="measurement",
                     measurement_version=3,threshold_w=2000,confirmation_seconds=10,
                     averaging_seconds=20,raw_balance_w=primary,
                     averaged_balance_w=self.power_estimator.average,
                     power_source=source,estimated=source.startswith("fallback"),primary_failed=bool(failed),
                     requested_heating=active,fallback_raw_w=raw if source.startswith("fallback") else None,
                     timestamp=self.now().isoformat())
        return power
    def stage_update(self,power):
        self.stage=nominal_mode(None if power is None else power*1000,self.stage)
        self.candidate=None
        self.publish("sensor.heating_boiler_stage",self.stage,friendly_name="Котел — підтверджений ступінь",
                     icon="mdi:water-boiler",measurement_version=3,timestamp=self.now().isoformat())
        self.publish('binary_sensor.heating_boiler_active',None if power is None else 'on' if power>=1.5 else 'off',
                     friendly_name='Котел — фактичний нагрів',icon='mdi:water-boiler')
    def tick(self,kwargs=None,**extra):
        if self.update_in_progress():
            return
        try:
            self.tick_inner()
        except Exception as exc:
            self.log("Climate cycle error: %s",exc,level="ERROR")
            self.publish("sensor.heating_controller","Помилка контролера",friendly_name="Клімат — контролер",error=str(exc))
            try:
                if (self.cfg.get('allow_actuation',False) and self.raw('armed')=='on' and
                        self.raw('mode')=='Автоматичне керування' and self.grid_state()=='on'):
                    self.enter_temporary_fallback('Помилка циклу: '+str(exc))
            except Exception as recovery_error:
                self.log('Temporary auto also failed: %s',recovery_error,level='ERROR')
    def tick_inner(self):
        now=self.now()
        if now.hour==10 and self.memory.get("learn_morning_date")!=now.date().isoformat():
            self.memory["learn_morning_date"]=now.date().isoformat()
            self.daily_learn()
        if now.hour==20 and now.minute>=45 and self.memory.get("learn_date")!=now.date().isoformat():
            self.memory["learn_date"]=now.date().isoformat()
            self.daily_learn()
        room,source,primary,thermostat=self.source_temperature()
        probe=self.val("return")
        probe_ok=probe is not None and 0<probe<65 and self.heartbeat_fresh(self.cfg["return_heartbeat"],1200)
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
        outage_data = schedule(self.get_state(self.cfg["outage_calendar"],attribute="all"),now,
                               self.cfg.get("outage_max_age",10800)) if self.cfg.get("outage_calendar") else {}
        signature=(outage_data.get("valid"),outage_data.get("updated"))
        if signature != getattr(self,"outage_signature",None):
            self.next_compute=0
            self.outage_signature=signature
        settings["split_comfort"] = True
        settings["morning_min_minutes"] = self.cfg.get("morning_min_minutes",30)
        settings["morning_elapsed_minutes"] = 0.
        command_at=number(self.memory.get("command_at"))
        if (in_window(now,"05:00","07:00") and power is not None and power>=1500
                and self.memory.get("last_command")=="heat" and command_at is not None
                and in_window(datetime.fromtimestamp(command_at,self.climate_tz),"05:00","07:00")
                and datetime.fromtimestamp(command_at,self.climate_tz).date()==now.date()):
            settings["morning_elapsed_minutes"] = min(30.,max(0.,(now.timestamp()-command_at)/60))
        settings["outage_schedule"] = outage_data
        settings["outage_advance_limit"] = self.cfg.get("outage_advance_limit",180)
        emergency = self.get_state(self.cfg.get("outage_emergency")) == "on" if self.cfg.get("outage_emergency") else False
        outage_now = bool(outage_data.get("valid") and blocked(now.timestamp(),now.timestamp()+15,outage_data["outages"]))
        upcoming = [o for o in outage_data.get("outages",[]) if o["end"] > now.timestamp()]
        self.publish("sensor.heating_outage_schedule",outage_data.get("reason","Графік не підключений"),
                     friendly_name="Клімат — графік відключень 3.1",valid=bool(outage_data.get("valid")),
                     updated=outage_data.get("updated"),emergency=emergency,
                     scheduled_outage_now=outage_now,next_outage=upcoming[0] if upcoming else None,
                     outages=upcoming,known_until=outage_data.get("known_until"),
                     warning="Аварійні відключення: графік не гарантує світло" if emergency else None)
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
        self.sample(room,probe,outside,None if power is None else power/1000,fire,source,primary,thermostat,quality)
        self.energy_tick()
        profile=self.memory.get('modulation_profile',dict(bands=[]))
        settings['modulation_profile']=profile
        floor=probe
        environment_quality=reliability(self.memory.get('hour_errors',[]))
        if quality:
            thermal,floor=observe_floor(self.memory.get('floor_observer'),now.timestamp(),
                                       room,probe,power/1000,grid,self.model)
            self.memory['floor_observer']=thermal
            weather_state=self.raw('weather',attribute='all') if self.cfg.get('weather') else None
            condition=weather_state.get('state') if isinstance(weather_state,dict) else None
            solar=observe_gain(self.memory.get('solar_observer'),now.timestamp(),room,floor,
                               outside,fire,power>=1500,self.model,self.climate_tz,condition)
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
        if time.time()<self.next_compute:
            demand=bool(self.memory.get("demand",False))
            segments=self.memory.get("planned_heat_segments",[])
            demand = demand and any(s["start"] <= now.timestamp() < s["end"] for s in segments)
            if once:
                demand=True
            if outage_now or (room is not None and room>=upper):
                demand=False
            action=decide(self.raw("mode"),self.raw("armed")=="on",bool(self.cfg.get("allow_actuation",False)),
                          room,probe_ok,grid,self.raw("thermostat") not in ("unknown","unavailable",None),
                          power is not None,self.model.get("validated",False),demand,valid)
            self.apply_action(action,False)
            self.fan_tick(grid,fire_active)
            self.publish("sensor.heating_heartbeat",now.isoformat(),friendly_name="Клімат — останній цикл",device_class="timestamp")
            return
        self.next_compute=time.time()+300
        self.resume_temporary_fallback()
        self.memory['last_plan_at']=time.time()
        self.memory['planned_effective_kw']=effective
        usable_weather=self.weather if time.time()-self.memory.get("weather_at",0)<3*3600 else []
        usable_weather,weather_bias=anchor_weather(usable_weather,now.timestamp(),outside) if outside is not None else ([],0.)
        settings['environment_weather']=usable_weather
        forecast=None
        peak=None
        coast_overheat=False
        if quality and valid:
            forecast,plan_reference=stable_plan(now,room,floor,outside,fire,self.model,usable_weather,settings,
                          power_kw=3.0,previous=self.memory.get('plan_reference'))
            self.memory['plan_reference']=plan_reference
            peak,_=coast(room,floor,outside,fire,self.model)
            coast_overheat=coast_exceeds_profile(now,room,floor,outside,fire,self.model,settings)
            self.publish("sensor.heating_forecast",forecast["minimum"],friendly_name="Клімат — мінімум прогнозу",
                         unit_of_measurement="°C",device_class="temperature",curve=forecast["curve"],
                         actual_curve=[{"ts":r["ts"],"temperature":r["room"]} for r in self.samples[-72:] if r.get("room") is not None],
                         planned_kwh=forecast["energy"],horizon_hours=forecast["horizon_hours"],
                         validated=self.model.get("validated",False) and not environment_quality['degraded'],
                         structural_model_validated=self.model.get('validated',False),
                         reliability=environment_quality,estimated_floor=floor,
                         local_weather_bias=round(weather_bias,2),
                         external_heat=settings.get('environment',{}).get('solar',{}).get('observed_gain',0.),
                         uncertainty='Точність знижена; обережне планування' if environment_quality['degraded'] else 'Оцінка; калібрування триває',
                         created_at=now.isoformat(),outage_shifted=forecast["outage_shifted"],
                         plan_stable=forecast['plan_stable'],stability_reason=forecast['stability_reason'],
                         outage_preheat=forecast["outage_preheat"],outside_minutes=forecast["outside_minutes"],
                         comfort_shortfall=forecast["comfort_shortfall"],outage_status=forecast["outage_status"],
                         heat_segments=forecast["heat_segments"],schedule_updated=forecast["schedule_updated"],
                         fireplace_segments=project_fireplace(now.timestamp(),fire,fire_active),
                         schedule_known_until=forecast["schedule_known_until"],
                         weather_known_until=forecast["weather_known_until"],
                         conditional_heat_minutes=forecast["conditional_heat_minutes"],
                         schedule_complete=forecast["schedule_complete"],
                         weather_complete=forecast["weather_complete"],
                         split_comfort_applied=forecast["split_comfort_applied"],
                         session_kwh=forecast["session_kwh"],
                         night_air_target=forecast["night_air_target"],
                         split_reason=forecast["split_reason"],
                         morning_min_minutes=forecast["morning_min_minutes"],
                         morning_runs_minutes=forecast["morning_runs_minutes"],
                         planning_policy_version=4,
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
        else:
            self.publish("sensor.heating_forecast","unavailable",friendly_name="Клімат — мінімум прогнозу",curve=[],
                         reason="Недостатньо актуальних даних або некоректні межі")
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
        reason=("Немає мережі" if grid=="off" else "Стан мережі не підтверджено" if grid=="unknown" else
                "Некоректні межі температури або часу" if not valid else
                "Недостатньо актуальних вимірювань" if not quality else
                "Планове відключення 3.1 — нагрів не планується" if outage_now else
                "Завчасний нагрів перед відключенням 3.1" if demand and preheat else
                "Ранковий підігрів підлоги" if demand and morning_comfort else
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
        self.publish("sensor.heating_controller","Спостереження" if action=="observe" else reason,
                     comfort_zone=comfort_zone,target_temperature=target,lower_temperature=lower,upper_temperature=upper,
                     friendly_name="Клімат — контролер",reason=reason,proposed_action="Нагрів · термостат +2 °C" if demand else "Очікування · термостат −2 °C",
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
                     journal=self.memory.get("journal",[])[-12:],
                     power_learning=profile,
                     effective_power_kw=round(effective,3) if effective is not None else None,
                     power_learning_status='Навчання модуляції' if profile['intervals'] else 'Очікуємо підтверджений нагрів')
        self.publish_ai_summary(refresh=True)
        self.memory["planned_heat_segments"]=forecast["heat_segments"] if forecast else []
        self.memory["demand"]=demand
        self.apply_action(action,once)
        self.fan_tick(grid,fire_active)
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
        rows=[r for r in self.samples if r["ts"]>time.time()-86400]
        good=[r for r in rows if r.get("quality")]
        temperatures=[r["room"] for r in good]
        energy=[r for r in rows if number(r.get("energy")) is not None]
        return {"samples":len(rows),"valid_samples":len(good),
                "coverage":round(len(good)/max(1,len(rows)),3),
                "temperature_min":min(temperatures) if temperatures else None,
                "temperature_max":max(temperatures) if temperatures else None,
                "measured_kwh":round(energy[-1]["energy"]-energy[0]["energy"],4) if len(energy)>1 else None,
                "hours_collected":round((rows[-1]["ts"]-rows[0]["ts"])/3600,2) if len(rows)>1 else 0.,
                "hour_forecast_mae":self.hour_mae()}
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
                             "estimated":getattr(self,"power_source","").startswith("fallback")})
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
        level=1 if in_window(self.now(),"23:00","07:00") else 3 if fire_active and self.memory.get('fire_phase')=='Інтенсивно горить' else 2
        percentage={1:night,2:standard,3:intense}[level]
        state=self.raw('fan')
        if state not in ('on','off'):
            reason='Вентилятор недоступний'
        elif enabled and mode!='Зупинити' and not request and state=='on' and not self.memory.get('fan_owned'):
            reason='Ручне увімкнення; автоматичний запит відсутній'
        source='fireplace' if fire_active and request else 'night' if request and self.memory.get('night_fan_until',0)>time.time() else 'manual' if request else 'none'
        status=dict(friendly_name="Клімат — циркуляція",reason=reason,requested=request,
                    proposed_percentage=percentage,control_enabled=enabled,backup_verified=backup_verified,
                    power_available=power_ok,actual_state=state,selected_level=level,
                    controller_owned=bool(self.memory.get('fan_owned')),request_source=source,
                    manual_running=state=='on' and not request and not self.memory.get('fan_owned') and mode!='Зупинити',
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
        elif self.memory.get("fan_owned") or mode=="Зупинити":
            last=getattr(self,'fan_command_key',(None,None))[0]
            if state=='on' or last=='fan/turn_on':
                send('fan/turn_off')
            elif self.memory.get('fan_owned'):
                self.memory["fan_owned"]=False
                self.save()
    def daily_learn(self,kwargs=None,**extra):
        candidate=learn(self.model,self.samples)
        if candidate is None:
            self.memory["last_result"]="Недостатньо даних для навчання"
        else:
            self.consider(candidate,"Локальне навчання",apply=True)
        self.memory["last_analysis"]=self.now().isoformat()
        self.telemetry.write_text("".join(json.dumps(r,ensure_ascii=False,allow_nan=False)+"\n" for r in self.samples))
        self.save()
        self.next_compute=0
    def consider(self,candidate,source,apply=False):
        accepted,reason,error=validate_candidate(self.model,candidate,self.samples)
        if accepted and apply:
            self.memory["previous_model"]=dict(self.model)
            candidate["version"]=self.model["version"]+1
            candidate["validated"]=False
            self.model=candidate
            self.memory["hour_errors"]=[]
            self.memory["predictions"]=[]
            reason="Застосовано кандидат; перевіряємо годинний прогноз у спостереженні"
        self.memory["last_result"]=source+": "+reason
        self.journal(self.memory["last_result"],"model")
        self.save()
        return accepted
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
            self.memory["last_result"]="Пропозицію ШІ відхилено: "+str(exc)
            self.journal(self.memory["last_result"],"model")
        self.memory["last_proposal"]=new
        self.save()
        self.next_compute=0
    def rollback(self,entity,attribute,old,new,kwargs=None,**extra):
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


