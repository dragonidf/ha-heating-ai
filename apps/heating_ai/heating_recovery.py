"""Temporary native-auto recovery and verified thermostat commands."""
import time
from datetime import datetime
from heating_model import number,in_window
from heating_outages import schedule,blocked

class HeatingRecovery:
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
        self.memory["expected_mode"]=mode
        self.memory["expected_setpoint"]=target
        transition=action!=last
        changed=self.raw("thermostat")!=mode or self.val("thermostat",attribute="temperature")!=target
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
            self.call_service("input_select/select_option",entity_id=self.cfg["mode"],option="Спостереження")
            self.notify_once("Виявлено ручну зміну термостата. Автоматичні команди призупинено.")
            self.save()



