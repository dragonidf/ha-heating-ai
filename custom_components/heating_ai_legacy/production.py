"""Complete V1 runtime: original algorithms, native HA I/O and voltage stop."""
from __future__ import annotations

import asyncio
from collections import deque
from copy import deepcopy
import json
import os
from tempfile import NamedTemporaryFile
from datetime import datetime, timedelta
import fcntl
from math import isfinite
from hashlib import sha256
from pathlib import Path
import time
import re
import threading
from uuid import uuid4

from homeassistant.components import frontend, panel_custom
from homeassistant.components.http.server import StaticPathConfig
from homeassistant.components.weather.const import DATA_COMPONENT as WEATHER_ENTITIES
from homeassistant.const import EVENT_HOMEASSISTANT_STOP
from homeassistant.core import callback
from homeassistant.exceptions import ConfigEntryNotReady
from homeassistant.helpers import entity_registry
from homeassistant.helpers.dispatcher import async_dispatcher_connect

from .native_controller.heating_climate import HeatingClimate
from .native_controller import heating_learning_windows as learning
from .native_controller.heating_model import comfort_at, number, retain_kws_profile
from .native_controller.heating_ui import system_fields
from .native_controller.heating_recovery import wire_values
from .native_voltage import VoltageGuard, select_phase_voltages
from .production_migration import load_installation
from .const import DOMAIN, SOURCE_HINTS
from .critical_ledger import CriticalLedger, LedgerConflict, FILENAME
from .kws_readiness import KWSReadiness, KWSRestorePolicy
from . import manual_energy
from .native_controller.heating_outages import blocked


def source_writer_active(hass, now=None):
    """Native publications are tagged; an old V1 heartbeat is a second writer."""
    state = hass.states.get("sensor.heating_heartbeat")
    if state is None or state.attributes.get("runtime") == "native_v1":
        return False
    try:
        stamp = float(state.state) if state.state.replace(".", "", 1).isdigit() else None
        if stamp is None:
            from datetime import datetime
            stamp = datetime.fromisoformat(state.state.replace("Z", "+00:00")).timestamp()
        return (now or time.time())-stamp < 45
    except (ValueError, AttributeError):
        return True


class NativeController(HeatingClimate):
    def __init__(self, hass, args):
        super().__init__(hass, args)
        self.voltage_guard = VoltageGuard()
        self.voltage_status = None
        self.command_ledger = None
        self.command_ledger_status = None
        self.kws_readiness = KWSReadiness()
        self.kws_restore = KWSRestorePolicy()
        self._command_sequence = 0
        self._pending_command_evidence = {}
        self._echo_lock = threading.Lock()
        self._echoed_command_ids = set()
        self._recovery_attempts = 0
        self._recovery_next_at = 0
        self._fence_recovery_attempted = False
        self._manual_energy_ending = False
        self.manual_energy_demand = False
        self._kws_idle_since = None
        self._archive_job = None
        self._archive_next_at = 0.
        self._archive_stop = threading.Event()
        # This lock guards only the worker/lease handoff, never disk I/O.
        self._archive_lease_lock = threading.Lock()
        self._archive_worker_active = False
        self._archive_deferred_lease = None

        self._learning_epoch = uuid4().hex
        self._learning_rows = deque(maxlen=340)
        self._learning_forecasts = deque(maxlen=6)
        self._learning_breaks = set()
        self._learning_last_tick = None
        self._learning_previous_kw = None
        self._learning_zero_now = False
        self._learning_zero_seconds = 0.
        self._learning_zero_interval = False
        self._learning_kw_seconds = 0.
        self._learning_kw_integral = 0.
        self._learning_control_mode = None
        self._learning_last_report = 0.
        self._learning_last_value = None
        self._learning_room_entity = None
        self._learning_room_report = None
        self._learning_last_issue = 0.
        self._learning_generation = 0
        self._learning_current_ok = False
        self._learning_job = None
        self._learning_status = dict(status="collecting",reason="qualified_inputs_required")

    def sample(self, room, probe, outside, power, fire, source, primary, thermostat, quality, settings=None):
        before = self.last_sample
        gap = self._capture_sample_power(power)
        result = super().sample(room, probe, outside, power, fire, source, primary, thermostat, quality, settings)
        try:
            self._learning_tick(power, fire, source, quality, before, gap)
        except Exception as exc:
            self._learning_breaks.add("capture_error")
            self._learning_current_ok = False
            self._learning_generation += 1
            self.log("Adaptive sample skipped: %s", type(exc).__name__, level="WARNING")
        return result

    def _learning_proven_zero_heat(self, power):
        """Read-only thermal proof; exact opt-in isolation, two OFF indicators.
        
        The configured breaker must be confirmed physically incapable of
        supplying the boiler on backup or partial mains under this condition.
        """
        relay=self.cfg.get("boiler_outage_isolation_relay")
        if (not isinstance(relay,str) or not relay.startswith("switch.")
                or relay!=self.cfg.get("kws_relay","switch.energy_meter_kotel")
                or self.cfg.get("power_accounting_method")!="kws"
                or getattr(self,"planning_grid_state",None)!="off"):
            return False
        helper=self.args.get("grid_presence_helper")
        if not helper or self.get_state(helper) not in ("off","Відсутня мережа"):
            return False
        phases=(self.voltage_status or {}).get("phases_v") or []
        if any((v:=number(value)) is not None and v>=50 for value in phases):
            return False
        if getattr(self,"power_quality",None)=="contradictory":
            return False
        kw=number(power)
        return power is None or kw is not None and 0<=kw<=.05

    def _capture_sample_power(self,power):
        stamp = time.time()
        previous = self._learning_last_tick
        self._learning_last_tick = stamp
        measured = (getattr(self,"power_source",None),getattr(self,"power_quality",None)) == ("kws_phases","measured")
        kw = number(power) if measured else None
        if kw is not None and not 0<=kw<=10.5:
            kw = None
        previous_kw = self._learning_previous_kw
        self._learning_previous_kw = kw
        gap = previous is None or not 0 < stamp-previous<=45
        zero=self._learning_proven_zero_heat(power)
        if not gap and zero and self._learning_zero_now:
            self._learning_zero_seconds += stamp-previous
        self._learning_zero_now=zero
        if not gap and kw is not None and previous_kw is not None:
            duration = stamp-previous
            self._learning_kw_seconds += duration
            self._learning_kw_integral += (kw+previous_kw)*duration/2
        return gap

    def measured_sample_power(self,start,end):
        if self._learning_kw_seconds>0 and self._learning_kw_seconds>=.95*(end-start):
            return self._learning_kw_integral/self._learning_kw_seconds
        return None

    def _learning_tick(self, power, fire, source, quality, before, gap):
        reasons = {"tick_gap_or_restart"} if gap else set()
        if getattr(self, "planning_grid_state", None) != "on":
            reasons.add("unqualified_outage")
        voltage = (self.voltage_status or {}).get("voltage_qualified") is True
        if not voltage:
            reasons.add("unknown_or_unqualified_voltage")
        if (getattr(self, "power_source", None), getattr(self, "power_quality", None)) != ("kws_phases", "measured"):
            reasons.add("unqualified_kws")
        if not (getattr(self, "equipment_status", None) or {}).get("ready"):
            reasons.add("equipment_not_ready")
        if not quality or source != "Вітальня":
            reasons.add("unavailable_physical_input")
        if self._learning_zero_now and not getattr(self,"_learning_thermal_inputs_ok",False):
            reasons.add("unavailable_thermal_input")
        if (self.memory.get("room_source_check") or {}).get("status") in ("pending","room_source_disagreement"):
            reasons.add("room_conflict")
        try:
            control_mode = self.raw("mode")
        except KeyError:
            control_mode = None
        if control_mode != self._learning_control_mode:
            reasons.add("control_mode_changed")
            self._learning_control_mode = control_mode
        if (power is not None and power>=1.5 and
                (control_mode != "Автоматичне керування" or not self.memory.get("owned")
                 or self.memory.get("last_command")!="heat")):
            reasons.add("unplanned_heat")
        manual = self.memory.get("manual_energy") or {}
        if manual.get("status")=="active" or (manual.get("ended_at") or 0)>before:
            reasons.add("manual_extra_request")
        if self.memory.get("temporary_fallback"):
            reasons.add("temporary_fallback")
        if ((number(fire) or 0.)>.2 or self.memory.get("fire_phase")=="Датчик недоступний"
                or (self.memory.get("fire_circulation") or {}).get("active")):
            reasons.add("fireplace")
        # Internal circulation is not external airflow or a heat source.
        # Fireplace activity/unknown temperature and room conflicts retain
        # their own independent qualification gates above.
        sensed = getattr(self, "ventilation_states", {})
        if not learning.ventilation_current_qualified(sensed):
            reasons.add("unknown_ventilation")
        self._learning_current_ok = not reasons
        if reasons:
            self._learning_generation += 1
            self._learning_breaks.update(reasons)
        if before == self.last_sample:
            return
        try:
            interval_kw = self.measured_sample_power(before,self.last_sample)
            duration=self.last_sample-before
            self._learning_zero_interval=bool(duration>=240 and self._learning_zero_now
                and self._learning_zero_seconds>=duration-1.
                and "tick_gap_or_restart" not in self._learning_breaks)
            self._learning_append(self.samples[-1], voltage, self._learning_breaks, interval_kw)
        finally:
            self._learning_zero_seconds=0.
            self._learning_zero_interval=False
            self._learning_kw_integral = self._learning_kw_seconds = 0.
            self._learning_breaks.clear()

    @callback
    def _async_listen_room_reports(self):
        entity = self.cfg.get("room")
        if not entity:
            return
        try:
            entry = entity_registry.async_get(self.hass).async_get(entity)
        except AttributeError:
            # The optional report source must not prevent the writer from
            # starting before the entity registry has finished loading.
            return
        if not entry or entry.platform != "tuya" or entry.original_device_class != "temperature":
            return
        prefix = "tuya."
        if not entry.unique_id.startswith(prefix):
            return
        # Home Assistant Tuya uses tuya.<device id><description key>.
        # Only a temperature datapoint with its own timestamp is accepted.
        datapoint = next((key for key in ("va_temperature","temp_current")
                          if entry.unique_id.endswith(key)),None)
        if not datapoint:
            return
        device = entry.unique_id[len(prefix):-len(datapoint)]
        if not device:
            return

        @callback
        def room_report(properties=None, timestamps=None):
            if entity != self.cfg.get("room") or datapoint not in (properties or ()):
                return
            observed = number((timestamps or {}).get(datapoint))
            if observed is None:
                return
            if observed > 100_000_000_000:
                observed /= 1000
            received = time.time()
            previous = self._learning_room_report
            if 0<=received-observed<=300 and (previous is None or observed>previous[1]):
                self._learning_room_report = entity,observed,received

        self._handles[uuid4().hex] = async_dispatcher_connect(
            self.hass,f"tuya_entry_update_{device}",room_report)

    def _learning_append(self, row, voltage, reasons, interval_kw):
        fields = ("ts","room","return","outside","power","fire","source","room_valid",
            "room_reported_at","power_source","power_quality","power_estimated","voltage_qualified",
            "grid","execution","manual_extra_active","thermal_model_version","equipment_version",
            "boiler_power_mode","fire_valid","fire_active","external_gain","residual_gain",
            "room_source_disagreement","room_sources_suspect","energy")
        compact = {key:row.get(key) for key in fields}
        requested=row.get("heating_requested")
        command_at=number(self.memory.get("command_at"))
        command=self.memory.get("command_diagnostic") or {}
        confirmed_at=number(command.get("confirmed_at"))
        mode="heat" if requested is True else "off"
        compact.update(heating_requested=requested,command_at=command_at,
            command_confirmed=bool(isinstance(requested,bool) and self.memory.get("owned")
                and self.memory.get("last_command")==mode and self.memory.get("expected_mode")==mode
                and command.get("status")=="confirmed" and command.get("accepted_mode")==mode
                and command_at is not None and confirmed_at is not None and command_at<=confirmed_at<=row["ts"]
                and not self._pending_command_evidence
                and not (self.command_ledger_status or {}).get("fenced",True)
                and self.raw("thermostat")==mode))
        stamp = row["ts"]
        manual = self.memory.get("manual_energy") or {}
        if (row.get("manual_extra_active") is True and manual.get("status")=="active"
                and len(self.samples)>=2 and (started:=number(manual.get("started_at"))) is not None
                and started<=self.samples[-2]["ts"] and interval_kw is not None
                and "control_mode_changed" not in reasons and "unplanned_heat" not in reasons):
            compact["manual_measurement_id"] = manual["started_at"]
        record = self.raw("room", attribute="all") or {}
        entity = self.cfg.get("room")
        try:
            observed = datetime.fromisoformat(record["last_changed"]).timestamp()
        except (KeyError,ValueError,TypeError):
            observed = None
        reported = number(row.get("room_reported_at"))
        source_report = self._learning_room_report
        direct = bool(source_report and source_report[0]==entity and reported is not None
                      and abs(reported-source_report[2])<=5 and 0<=stamp-source_report[1]<=300)
        if direct:
            observed = source_report[1]
        temperature = number(record.get("state"))
        source_changed = entity != self._learning_room_entity
        if source_changed:
            self._learning_room_entity = entity
            self._learning_epoch = uuid4().hex
            self._learning_last_report = observed or 0.
            self._learning_last_value = temperature
        verified = (temperature is not None and (direct or temperature!=self._learning_last_value)
                    and observed is not None and reported is not None and observed<=reported<=stamp
                    and observed>self._learning_last_report and 0<=stamp-observed<=300
                    and temperature==number(row.get("room"))
                    and row.get("room_valid") is True)
        if verified:
            self._learning_last_report = observed
            self._learning_last_value = temperature
            compact["room_reported_at"] = observed
        ventilation = []
        for key in ("recuperator","bath"):
            evidence = (row.get("ventilation") or {}).get(key,{})
            duty = learning.ventilation_interval_duty(evidence)
            if duty is None:
                ventilation = None
                break
            ventilation.append(duty)
        issue = number(self.memory.get("weather_at"))
        weather_state=self.raw("weather",attribute="all") if self.cfg.get("weather") else None
        solar_weather=learning.solar_weather_evidence(stamp,self.climate_tz,self.weather,issue,
                                getattr(self,"solar_geometry",{}),weather_state)
        solar=solar_weather["forecast_factor"]
        compact.update(zero_heat_provenance="verified_mains_isolation" if self._learning_zero_now else None,
            zero_heat_interval_verified=self._learning_zero_interval,
            thermal_inputs_ok=getattr(self,"_learning_thermal_inputs_ok",False) is True,
            circulation_role="internal_redistribution",
            circulation_active=self.raw("fan")=="on" if self.cfg.get("fan") else None,
            report_verified=verified, primary_fresh=reported is not None and 0<=stamp-reported<=300,
            voltage_qualified=voltage,
            shadow_ventilation=ventilation, epoch=self._learning_epoch, weather_issue_at=issue,
            solar_factor=solar, solar_weather=solar_weather, shadow_reasons=tuple(sorted(reasons)), interval_kw=interval_kw,
            room_source_status=(self.memory.get("room_source_check") or {}).get("status"))
        self._learning_rows.append(compact)
        if verified:
            if "advisory_validation" in self.memory:
                self.memory["advisory_validation"]=learning.advisory_validation_update(
                    self.memory.get("advisory_validation"),stamp,self._advisory_identity(),report=compact)
            self._monitor_learning(observed)

    def _advisory_identity(self):
        return [self.model.get("version"),self.memory.get("equipment_version",0),
                self.cfg.get("boiler_power_mode","unconfirmed")]

    def advisory_validation_view(self):
        # The ledger is replaced, never mutated, by advisory_validation_update.
        # Recompute on identity change or exact 30-day expiry, not on every tick.
        state=self.memory.get("advisory_validation")
        identity=self._advisory_identity()
        now=self.now();stamp=now.timestamp()
        cached=getattr(self,"_advisory_view_cache",None)
        if (cached is not None and cached[0] is state and cached[1]==identity
                and cached[2]==now.tzinfo and cached[3]<=stamp<=cached[4]
                and datetime.fromtimestamp(cached[3],now.tzinfo).date()==now.date()):
            return cached[5]
        result=learning.advisory_validation_summary(state,identity,now.tzinfo,stamp)
        next_expiry=min((p["reported"]+30*86400 for p in (state or {}).get("completed",[])
                         if p.get("identity")==identity and p["reported"]+30*86400>=stamp),default=float("inf"))
        self._advisory_view_cache=(state,identity,now.tzinfo,stamp,next_expiry,result)
        return result

    def remember_advisory_outlook(self, now, outlook):
        self.memory["advisory_validation"]=learning.advisory_validation_update(
            self.memory.get("advisory_validation"),now.timestamp(),self._advisory_identity(),outlook=outlook)

    def _monitor_learning(self, reported):
        checkpoint=self.memory.get("online_learning") or {}
        monitoring=checkpoint.get("monitoring")
        if (not monitoring or monitoring["version"]!=self.model.get("version")
                or not self._learning_eligible()):
            return
        candidates=[record for record in self._learning_forecasts
                    if record["model"]==self.model and record["start"]>=monitoring["at"]
                    and any(abs(reported-point[0])<=min(learning.REPORT_ALIGNMENT_SECONDS,
                            record.get("report_alignment_seconds",60)) for key,point in record["points"].items()
                            if key in ("1","3"))]
        if not candidates:
            return
        measurements=list(self._learning_rows)
        checked={(r["start"],r["horizon"]):r for r in monitoring.get("checks",())}
        for snapshot in candidates:
            for row in learning.compare_frozen(snapshot,measurements,self.climate_tz):
                if row["horizon"] in (1,3) and row["valid"]:
                    checked.setdefault((row["start"],row["horizon"]),{
                        key:row[key] for key in ("start","end","day","horizon","old_error",
                                                 "shadow_error","valid","stratum","violation")})
        monitoring["checks"]=list(checked.values())[-64:]
        reason=learning.rollback_reason(monitoring["checks"],monitoring["strata"])
        if reason:
            self._rollback_learning(reason)


    def remember_plan_inputs(self, now, room, floor, outside, fire, weather, settings, forecast, previous):
        result = super().remember_plan_inputs(now,room,floor,outside,fire,weather,settings,forecast,previous)
        if isinstance(forecast,dict) and learning.solar_nominal_settings(settings) is not None:
            try:
                nominal=learning.project_solar_nominal(now,room,floor,outside,fire,weather,settings,forecast,self.model)
                if nominal is not None:
                    forecast["_solar_nominal_fine"]=nominal["fine"]
                    forecast["solar_nominal_curve"]=nominal["curve"]
                    forecast["solar_nominal_assumption"]="conditional_solar_same_plan_no_control_credit"
                else:
                    forecast["solar_nominal_assumption"]="unavailable_exact_trace_required"
            except (ValueError,TypeError,KeyError,ArithmeticError,MemoryError) as exc:
                forecast["solar_nominal_assumption"]="unavailable_projection_failed"
                self.log("Conditional solar outlook skipped: %s",type(exc).__name__,level="WARNING")
        stamp = now.timestamp()
        if self._learning_current_ok and stamp-self._learning_last_issue>=10920:
            try:
                snapshot = learning.freeze_forecast(now,room,floor,outside,fire,weather,
                    number(self.memory.get("weather_at")),settings,forecast,self.model,None,self._learning_epoch)
                if snapshot:
                    self._learning_forecasts.append(snapshot)
                    self._learning_last_issue = stamp
            except Exception as exc:
                self.log("Learning evidence skipped: %s",type(exc).__name__,level="WARNING")
        return result

    def consider(self, candidate, source, apply=False, rejection=None):
        # Preserve proposal diagnostics. All parameter changes now pass one gate.
        return super().consider(candidate,source,apply=False,rejection=rejection)

    def _learning_eligible(self):
        manual = self.memory.get("manual_energy") or {}
        stamp=time.time()
        weather_issue=number(self.memory.get("weather_at"))
        return bool(self._writer and not self._closing and self.cfg.get("allow_actuation")
                    and not self.memory.get("state_load_error") and not self.memory.get("temporary_fallback")
                    and not getattr(self,"settings_transaction",False) and manual.get("status")!="active"
                    and self.raw("armed")=="on" and self.raw("mode")=="Автоматичне керування"
                    and not (self.command_ledger_status or {}).get("fenced",True)
                    and (self.voltage_status or {}).get("voltage_qualified") is True
                    and (getattr(self,"equipment_status",None) or {}).get("ready")
                    and self.weather and weather_issue is not None
                    and 0<=stamp-weather_issue<=learning.MAX_WEATHER_AGE
                    and self._learning_current_ok and self._learning_last_tick is not None
                    and 0<=stamp-self._learning_last_tick<=45)

    def schedule_learning(self,now):
        if now.hour<10:
            return
        today=now.date().isoformat()
        previous=self.memory.get("adaptive_schedule") or {}
        if previous.get("day")!=today:
            slot="daily"
        elif previous.get("slot")=="daily" and (
                ((now.hour,now.minute)>=(20,45) and
                 self._learning_last_report>previous.get("room_report",0.)) or
                (previous.get("eligible_at_issue") is False and self._learning_eligible())):
            # Historical analysis can run during an outage. Spend the one
            # remaining daily attempt when qualification returns, not only at night.
            slot="retry"
        else:
            return
        self.run_diagnostic("learning",self.daily_learn,slot=slot)

    def daily_learn(self, kwargs=None, *, slot=None, **extra):
        if self._learning_job is not None:
            if time.monotonic()-self._learning_job["started"]>120:
                self._learning_status.update(status="frozen",reason="learning_job_timeout")
            return
        if self.memory.get("state_load_error") or not self._learning_rows:
            return
        now=self.now()
        self.memory["last_analysis"]=now.isoformat()
        eligible=self._learning_eligible()
        issued=dict(id=uuid4().hex,model=dict(self.model),started=time.monotonic(),
                    generation=self._learning_generation,equipment=self.memory.get("equipment_version",0),
                    solar_ventilation=deepcopy(self.active_ventilation_model()),
                    solar_risk=deepcopy(self.active_solar_risk_certificate()),
                    schedule=dict(day=now.date().isoformat(),slot=slot,
                                  room_report=self._learning_last_report,eligible_at_issue=eligible))
        self._learning_job=issued
        self._learning_status.update(status="evaluating",reason="scheduled_evaluation")
        try:
            self.submit_to_executor(learning.run_learning,self.directory/"online_learning_v1.json",
                tuple(self._learning_rows),tuple(self._learning_forecasts),issued["model"],
                issued["solar_ventilation"],
                deepcopy(self.memory.get("online_learning") or {}),now,eligible,
                dict(mode=self.cfg.get("boiler_power_mode","unconfirmed"),equipment=issued["equipment"],
                     calibrations=deepcopy(self.memory.get("power_calibrations") or {}),
                     active=deepcopy(self.memory.get("kws_forecast_profile")),
                     solar_risk=issued["solar_risk"]),
                callback=self._learning_finished,job_id=issued["id"])
        except Exception as exc:
            self._learning_job=None
            self._learning_status.update(status="frozen",reason="learning_submit_failed")
            self.log("Scheduled learning unavailable: %s",type(exc).__name__,level="WARNING")

    def _learning_finished(self, data):
        issued=self._learning_job
        if not issued or data.get("job_id")!=issued["id"]:
            return
        self._learning_job=None
        result=data["result"]
        schedule=issued.get("schedule") or {}
        if schedule.get("slot"):
            self.memory["adaptive_schedule"]=dict(schedule,completed_at=self.now().isoformat())
        self._learning_status={k:v for k,v in result.items()
                               if k in ("status","reason","metrics","independent_windows","qualified_minutes",
                                        "verified_reports","rejection_counts","current_reasons","eligibility_counts",
                                        "analysis_status","analysis_blockers","parameter_support","solar","solar_prospective","solar_weather")}
        if result.get("power_learning") is not None:
            self.memory.setdefault("online_learning",{})["power_learning"]=result["power_learning"]
        self._apply_learning_result(issued,result)
        self._update_solar_credit(issued,result)
        self._store_solar_risk_candidate(issued,result)
        if self._learning_status.get("status") not in ("accepted","rolled_back"):
            checkpoint=self.memory.setdefault("online_learning",{})
            checkpoint["changes_history"]=self._learning_history(
                checkpoint,self._learning_status["status"],self._learning_status.get("reason"))

    def active_solar_risk_certificate(self):
        checkpoint=self.memory.get("online_learning") or {}
        certificate=checkpoint.get("solar_risk_candidate") or {}
        identity=[self.model.get("version"),self.memory.get("equipment_version",0),
                  self.cfg.get("boiler_power_mode","unconfirmed")]
        coefficient=number(certificate.get("coefficient"))
        if (certificate.get("identity")==identity and certificate.get("status")=="holdout_passed"
                and certificate.get("ventilation_model")== self.active_ventilation_model()
                and type(certificate.get("comfort_credit")) is bool
                and number(certificate.get("stored_at")) is not None
                and certificate["stored_at"]>=checkpoint.get("evidence_after",0)
                and coefficient is not None and 0<coefficient<=.8):
            if certificate["comfort_credit"] and (number(certificate.get("nominal_at")) is None
                    or certificate.get("nominal_revoked_at") is not None
                    or (certificate.get("nominal_validation") or {}).get("status")!="passed"):
                return {}
            return certificate
        return {}

    def _store_solar_risk_candidate(self, issued, result):
        """Persist evaluated solar evidence, not an authorization to heat less."""
        solar=result.get("solar") or {}
        if (result.get("proposed") or result.get("proposed_power") or result.get("rollback")
                or solar.get("risk_eligible") is not True or solar.get("status")!="holdout_passed"
                or issued["model"]!=self.model or issued["generation"]!=self._learning_generation
                or issued["equipment"]!=self.memory.get("equipment_version",0)
                or issued.get("solar_ventilation")!=self.active_ventilation_model()
                or time.monotonic()-issued["started"]>120 or not self._learning_eligible()):
            return
        identity=[self.model.get("version"),issued["equipment"],self.cfg.get("boiler_power_mode","unconfirmed")]
        coefficient=number(solar.get("coefficient"))
        if solar.get("identity")!=identity or coefficient is None or not 0<coefficient<=.8:
            return
        checkpoint=self.memory.setdefault("online_learning",{})
        old=checkpoint.get("solar_risk_candidate")
        if old and (old.get("holdout_end",0)>=solar.get("holdout_end",0)
                    or self.now().timestamp()<old.get("nominal_frozen_until",0)
                    or old.get("nominal_revoked_at",0)>=solar.get("holdout_end",0)):
            return
        checkpoint["solar_risk_candidate"]={**solar,"comfort_credit":False,
            "ventilation_model":deepcopy(self.active_ventilation_model()),
            "stored_at":self.now().timestamp()}
        try:
            self.save()
        except (OSError,TypeError,ValueError,MemoryError):
            if old is None:checkpoint.pop("solar_risk_candidate",None)
            else:checkpoint["solar_risk_candidate"]=old
            self._learning_status["solar"]={**solar,"status":"storage_failed"}
            return
        self.next_compute=0

    def _update_solar_credit(self, issued, result):
        """One fixed coefficient; guarded promotion or safety rollback."""
        report=result.get("solar_prospective") or {}
        active=self.active_solar_risk_certificate()
        if (not active or active!=issued.get("solar_risk")
                or issued["model"]!=self.model or issued["generation"]!=self._learning_generation
                or issued["equipment"]!=self.memory.get("equipment_version",0)
                or issued.get("solar_ventilation")!=self.active_ventilation_model()
                or time.monotonic()-issued["started"]>120
                or self.memory.get("state_load_error") or getattr(self,"settings_transaction",False)
                or report.get("identity")!=active["identity"]
                or report.get("coefficient")!=active["coefficient"]):
            return
        stamp=self.now().timestamp()
        checkpoint=self.memory["online_learning"]
        next_certificate=dict(active)
        if (report.get("status")=="passed" and not active["comfort_credit"]
                and active.get("nominal_revoked_at") is None
                and report.get("issued_after")==active["stored_at"]
                and not result.get("proposed") and not result.get("proposed_power") and not result.get("rollback")
                and stamp>=checkpoint.get("frozen_until",0)
                and self._learning_eligible()):
            next_certificate.update(comfort_credit=True,nominal_at=stamp,
                                    nominal_validation=deepcopy(report))
        elif (report.get("status")=="rollback_required" and active["comfort_credit"]
                and report.get("issued_after")==active.get("nominal_at")):
            # A conservative removal of credit needs no actuator permission,
            # but the exact issued identity and successful durable write do.
            next_certificate.update(comfort_credit=False,nominal_revoked_at=stamp,
                nominal_frozen_until=stamp+7*86400,
                nominal_rollback_reason=report.get("rollback_reason"))
        else:
            return
        checkpoint["solar_risk_candidate"]=next_certificate
        try:
            self.save()
        except (OSError,TypeError,ValueError,MemoryError):
            checkpoint["solar_risk_candidate"]=active
            self._learning_status["solar_prospective"]={**report,"status":"storage_failed"}
            return
        self.next_compute=0

    def _apply_learning_result(self, issued, result):
        if not (result.get("proposed") or result.get("proposed_power") or result.get("rollback")):
            return
        checkpoint=self.memory.get("online_learning") or {}
        now=self.now()
        if (time.monotonic()-issued["started"]>120 or issued["model"]!=self.model
                or issued["generation"]!=self._learning_generation
                or issued["equipment"]!=self.memory.get("equipment_version",0)
                or not self._learning_eligible()):
            self._learning_status.update(status="frozen",reason="learning_inputs_changed")
            return
        if result.get("rollback"):
            self._rollback_learning(result["reason"])
        elif (result.get("evidence_fingerprint")!=checkpoint.get("last_rejected_evidence")
              and (learning.power_promotion_allowed(self.model,result["proposed_power"],result["evidence"],
                        checkpoint,now,self.cfg.get("boiler_power_mode","unconfirmed"),issued["equipment"])
                   if result.get("proposed_power") else
                   learning.promotion_allowed(self.model,issued["model"],result["proposed"],
                        result["evidence"],checkpoint,now,legacy_day=self.memory.get("learning_day")))):
            old=dict(self.model)
            version=max(old["version"],checkpoint.get("generation",0))+1
            new={**old,**(result.get("proposed") or {}),"version":version}
            next_checkpoint={**checkpoint,"generation":version,"promotion_day":now.date().isoformat(),
                "daily_baseline":old,"prior_model":old,"fingerprint":result["evidence_fingerprint"],
                "restored_learning_success":deepcopy(self.memory.get("learning_success")),
                "monitoring":dict(version=version,at=now.timestamp(),strata=result["evidence"]["strata"]),
                "metrics":result["metrics"],"last_successful_learning":now.isoformat(),
                "previous_plan_id":(self.memory.get("plan_reference") or {}).get("plan_id"),
                "rollback_reason":None}
            if result.get("proposed_power"):
                next_checkpoint["pending_kws_profile"]=result["proposed_power"]
            self._commit_learning_model(new,next_checkpoint,"accepted",
                "kws_independent_thermal_holdout" if result.get("proposed_power") else "independent_holdout_passed")
        else:
            self._learning_status.update(status="rejected",reason="promotion_limits_or_prior_evidence")

    def _learning_history(self, checkpoint, status, reason, model=None):
        stamp=self.now().timestamp()
        model=self.model if model is None else model
        metrics=self._learning_status.get("metrics",{})
        row=dict(at=stamp,status=status,reason=reason,version=model.get("version"),
                 windows=self._learning_status.get("independent_windows"),
                 minutes=self._learning_status.get("qualified_minutes"),
                 changes={k:[self.model[k],model[k]] for k in learning.FIT_FIELDS
                          if k in self.model and k in model and self.model[k]!=model[k]},
                 mae={h:[metrics[h].get(side,{}).get("mae") for side in ("old","new")]
                      for h in ("1","3") if h in metrics})
        history=[event for event in checkpoint.get("changes_history",())
                 if stamp-30*86400<=event["at"]<=stamp]
        return (history+[row])[-96:]

    def _commit_learning_model(self, model, checkpoint, status, reason):
        """Commit the model and its rollback evidence at one filesystem boundary."""
        ventilation=self.memory.get("ventilation_model")
        qualified_ventilation=self.active_ventilation_model()
        active_vent=bool(qualified_ventilation.get("validated")
                         and qualified_ventilation.get("model_version")==self.model.get("version"))
        if status=="accepted" and active_vent and model.get("room_loss")!=self.model.get("room_loss"):
            self._learning_status.update(status="rejected",reason="room_loss_not_identifiable_with_ventilation")
            return False
        checkpoint={**checkpoint,"changes_history":self._learning_history(checkpoint,status,reason,model)}
        if status=="accepted":
            checkpoint["prior_ventilation_model"]=deepcopy(ventilation)
            checkpoint["prior_kws_profile"]=deepcopy(self.memory.get("kws_forecast_profile"))
        values={**self.memory,"model":model,"weather":self.weather,"online_learning":checkpoint,
                "previous_model":dict(self.model),"predictions":[],"hour_errors":[]}
        # Validation used the existing qualified ventilation coefficients. Keep
        # that exact physical context across a version-only rebind, and restore
        # its previous snapshot on rollback, at the same atomic write boundary.
        if status=="accepted" and active_vent:
            values["ventilation_model"]={**ventilation,"model_version":model["version"],
                                        "carried_from_model_version":self.model["version"]}
        elif status=="rolled_back" and "prior_ventilation_model" in checkpoint:
            values["ventilation_model"]=deepcopy(checkpoint.pop("prior_ventilation_model")) or {}
        profile=self.memory.get("kws_forecast_profile")
        if status=="accepted":
            profile=checkpoint.pop("pending_kws_profile",profile)
            if profile and profile.get("thermal_model_version")==self.model.get("version"):
                profile={**profile,"thermal_model_version":model["version"]}
        elif status=="rolled_back":
            profile=checkpoint.pop("prior_kws_profile",None)
        if profile and profile.get("equipment_version")!=self.memory.get("equipment_version",0):
            profile=None
        if profile or "kws_forecast_profile" in self.memory:
            values["kws_forecast_profile"]=profile
            values["power_calibrations"]=retain_kws_profile(values.get("power_calibrations"),profile,model,
                                                           self.memory.get("equipment_version",0))
        values.pop("pending_model",None)
        if status=="accepted":
            values["learning_success"]=dict(time=checkpoint["last_successful_learning"],
                model_version=model["version"],source="timestamp_aligned")
        else:
            values["learning_success"]=checkpoint.get("restored_learning_success")
            values.pop("previous_model",None)
        # Pending physical command and energy ledgers remain untouched.
        temporary=None
        try:
            with NamedTemporaryFile(mode="w",dir=self.statefile.parent,prefix=".learning-state-",delete=False) as stream:
                temporary=Path(stream.name)
                json.dump(values,stream,ensure_ascii=False,allow_nan=False)
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(temporary,self.statefile)
        except (OSError,TypeError,ValueError,MemoryError) as exc:
            self._learning_status.update(status="frozen",reason="model_commit_failed")
            self.log("Learning state could not be committed: %s",type(exc).__name__,level="WARNING")
            return False
        finally:
            if temporary is not None:
                try:
                    temporary.unlink(missing_ok=True)
                except OSError:
                    pass
        self.model=model
        self.last_saved_at=time.time()
        self.memory.update(values)
        self.memory.pop("pending_model",None)
        if status=="rolled_back":
            self.memory.pop("previous_model",None)
        self._learning_status.update(status=status,reason=reason)
        self.next_compute=0
        return True

    def _rollback_learning(self, reason):
        checkpoint=self.memory.get("online_learning") or {}
        previous=checkpoint.get("prior_model") or self.memory.get("previous_model")
        if not previous or previous.get("validated") is not True or self.memory.get("state_load_error"):
            self._learning_status.update(status="frozen",reason="rollback_baseline_unavailable")
            return
        updated={**checkpoint,"prior_model":None,"monitoring":None,
                 "rollback_reason":reason,"frozen_until":self.now().timestamp()+7*86400,
                 "generation":max(self.model["version"],checkpoint.get("generation",0)),
                 "last_rejected_evidence":checkpoint.get("fingerprint"),
                 "evidence_after":self.now().timestamp(),
                 "last_successful_learning":(checkpoint.get("restored_learning_success") or {}).get("time")}
        if self._commit_learning_model(dict(previous),updated,"rolled_back",reason):
            self._learning_generation+=1

    def rollback(self, entity, attribute, old, new, kwargs=None, **extra):
        self._rollback_learning("manual_rollback")

    def attach_command_ledger(self, ledger, status):
        self.command_ledger = ledger
        self.command_ledger_status = status

    def update_in_progress(self):
        # Marketplace unload/reload revokes this bridge before replacing code.
        return False

    ARCHIVE_MAINTENANCE_INTERVAL = 900
    ARCHIVE_CLOSE_TIMEOUT = 4.

    def schedule_archive_maintenance(self, stamp):
        """Use the existing tick only to dispatch a bounded cold archive job."""
        if (self._closing or not self._running or not self._writer
                or stamp < self._archive_next_at or self._archive_job is not None
                or self._kws_idle_since is None or stamp-self._kws_idle_since < 60
                or self._learning_job is not None or any(not job.get("completed")
                    for job in getattr(self,"calculation_jobs",{}).values())):
            return
        journal=getattr(self,"decision_journal",None)
        if journal is None:
            return

        def launch():
            if self._closing or not self._running or not self._writer or self._archive_job is not None:
                return
            self._archive_next_at=stamp+self.ARCHIVE_MAINTENANCE_INTERVAL
            self._archive_stop.clear()
            self._archive_job=self.hass.async_create_task(
                self._maintain_archive(journal,stamp),"Heating AI cold archive maintenance")
        self._on_loop(launch)

    def _archive_worker(self,journal,stamp):
        # Ownership is tied to the actual executor thread, not a cancellable
        # asyncio future. A worker queued behind shutdown cannot enter.
        with self._archive_lease_lock:
            if self._archive_stop.is_set() or self._closing or self._archive_worker_active:
                return dict(status="stopped",snapshots=0)
            self._archive_worker_active=True
        try:
            return journal.compact_cold_snapshot_batch(stamp,allow_remove=True,stop=self._archive_stop)
        finally:
            with self._archive_lease_lock:
                self._archive_worker_active=False
                lease,self._archive_deferred_lease=self._archive_deferred_lease,None
            if lease is not None:
                lease.close()

    def close_archive_lease(self,lease):
        # Even repeated cancellation cannot unlock a live archive writer.
        # Transfer the open file to the worker on timeout; a new writer will
        # fail the existing nonblocking flock until the old worker exits.
        self._archive_stop.set()
        with self._archive_lease_lock:
            if self._archive_worker_active:
                self._archive_deferred_lease=lease
                return
        lease.close()

    async def _maintain_archive(self,journal,stamp):
        try:
            result=await self.hass.async_add_executor_job(self._archive_worker,journal,stamp)
            journal.cold_pack_maintenance=dict(at=stamp,**result)
        except asyncio.CancelledError:
            self._archive_stop.set()
            raise
        except Exception as exc:
            journal.cold_pack_maintenance=dict(at=stamp,status="deferred",reason=type(exc).__name__)
            self.log("Cold archive maintenance deferred: %s",type(exc).__name__,level="WARNING")
        finally:
            self._archive_job=None

    async def async_close(self):
        self._closing=True
        self._writer=False
        self._archive_stop.set()
        job=self._archive_job
        if job is not None:
            done,_=await asyncio.wait({job},timeout=self.ARCHIVE_CLOSE_TIMEOUT)
            if not done:
                self.log("Cold archive shutdown budget reached; writer lease retained until executor exits",level="WARNING")
        await super().async_close()

    def tick(self, kwargs=None, **extra):
        """Recover on the existing tick; never create another polling task."""
        try:
            equipment = self._on_loop(self.async_kws_status)
            signature = (equipment["reason"], equipment.get("kws_fault"),
                         (self.voltage_status or {}).get("status"))
            if signature != getattr(self, "_equipment_signature", None):
                journal = getattr(self, "decision_journal", None)
                if journal:
                    journal.append(dict(event="equipment_transition", before=getattr(self, "_equipment_signature", None),
                        after=signature, voltage=self.voltage_status, equipment=equipment), stamp=time.time())
                self._equipment_signature = signature
            self._reconcile_writer_fence()
            if self.cfg.get("kws_auto_restore") == "enabled" and self.kws_restore.outage_at is not None:
                self._try_kws_restore()
        except Exception as exc:
            self.record_failure("ledger_recovery", exc)
        try:
            result = super().tick(kwargs, **extra)
            # Only continuous observed idle, spanning the meter report,
            # authorizes a previously reported KWS baseline.
            observed_idle = (getattr(self, "power_source", "") == "kws_phases"
                and getattr(self, "power_quality", "") == "measured"
                and getattr(self, "current_power_w", None) is not None
                and self.current_power_w < 500
                and self.memory.get("last_command") != "heat"
                and self.grid_state() == "on"
                and time.time() - (getattr(self, "power_observation", {}) or {}).get("at", 0) <= 30)
            if observed_idle:
                if self._kws_idle_since is None:
                    self._kws_idle_since = time.time()
            else:
                self._kws_idle_since = None
                if self._archive_job is not None:
                    self._archive_stop.set()
            self.schedule_archive_maintenance(time.time())
            return result
        finally:
            # An unconfirmed manual-only STOP must be retried on subsequent
            # ticks, not accidentally held by the normal minimum-on timer.
            now = time.time()
            scheduled = any(s["start"] <= now < s["end"] for s in
                            self.memory.get("planned_heat_segments", []))
            if self.memory.get("last_command") != "heat" or scheduled:
                self._manual_energy_ending = False

    def _manual_kws_snapshot(self):
        """Read raw KWS without touching energy counters."""
        def read():
            row = self.hass.states.get("sensor.energy_meter_kotel_energiia")
            power = self.hass.states.get("sensor.heating_power_confirmed")
            if row is None or row.attributes.get("unit_of_measurement") != "kWh":
                return None, None, None
            try:
                value = float(row.state)
            except (ValueError, TypeError):
                value = None
            idle_since = None
            if (power is not None and power.attributes.get("measurement_basis") == "kws_phases"
                    and power.attributes.get("power_quality") == "measured"
                    and number(power.state) is not None and number(power.state) < 500
                    and self.memory.get("last_command") != "heat"):
                idle_since = self._kws_idle_since
            return value, row.last_reported.timestamp(), idle_since
        return self._on_loop(read)

    def _manual_verified_power(self, stamp):
        """Qualified independent KWS A/B/C power; never nominal/fallback watts."""
        observation = getattr(self, "power_observation", {}) or {}
        sources = observation.get("source_quality") or {}
        fresh = all(
            sources.get("sensor.energy_meter_kotel_potuzhnist_"+phase, {}).get("quality") == "measured"
            and type(sources.get("sensor.energy_meter_kotel_potuzhnist_"+phase, {}).get("value_reported_at")) in (int, float)
            and -5 <= stamp-sources["sensor.energy_meter_kotel_potuzhnist_"+phase]["value_reported_at"] <= manual_energy.MAX_POWER_AGE
            for phase in "abc")
        if not (fresh and observation.get("policy") == "kws" and
                observation.get("quality") == "measured"):
            return None, None
        return observation.get("watts"), observation.get("at")

    def manual_energy_snapshot(self):
        return dict(self.memory.get("manual_energy") or {"status": "idle"})

    def publish_manual_energy(self):
        session = self.manual_energy_snapshot()
        self.publish("sensor.heating_manual_energy", session.get("status", "idle"),
                     friendly_name="Клімат — додатковий нагрів",
                     **{k: v for k, v in session.items() if k != "status"})

    def start_manual_energy(self, target):
        if self.memory.get("manual_energy", {}).get("status") == "active":
            raise ValueError("already_active")
        if (not self._writer or self._closing or self.cfg.get("power_accounting_method") != "kws"
                or not self.cfg.get("allow_actuation") or self.raw("armed") != "on"
                or self.raw("mode") != "Автоматичне керування"
                or self.memory.get("temporary_fallback") or self.grid_state() != "on"
                or (self.command_ledger_status or {}).get("fenced", True)
                or not self._on_loop(self.async_voltage_status)["voltage_qualified"]
                or not self._on_loop(self.async_kws_status).get("ready")
                or not self.model.get("validated")):
            raise ValueError("safety_blocked")
        room = self.source_temperature()[0]
        if room is None or room >= self.active_upper():
            raise ValueError("temperature_guard")
        outage = self.outage_execution_schedule(grid="on")
        if outage.get("valid") and blocked(time.time(), time.time()+15, outage["outages"]):
            raise ValueError("scheduled_outage")
        value, reported_at, idle_since = self._manual_kws_snapshot()
        now = time.time()
        limit = manual_energy.watchdog_minutes(target, self.val("session_limit", 180))
        try:
            session = manual_energy.start(target, value, reported_at, now, limit, idle_since)
        except ValueError as exc:
            if str(exc) not in ("counter_invalid", "counter_baseline_stale"):
                raise
            # Same independently measured phase power already used for live KWS
            # progress; never accept unknown, stale, or estimated phase data.
            watts, measured_at = self._manual_verified_power(now)
            if watts is None:
                raise
            session = manual_energy.start_power_only(target, watts, measured_at, now, limit)
        session["origin_was_idle"] = self.memory.get("last_command") != "heat"
        self.memory["manual_energy"] = session
        self._kws_idle_since = None
        # Preserve historical queues and predictions, but do not score a
        # pre-existing automatic forecast against a user-requested session.
        for pending in self.memory.get("predictions", []):
            if isinstance(pending, dict):
                pending["manual_invalid"] = True
        for pending in self.memory.get("reserve_predictions", []):
            if isinstance(pending, dict):
                pending["valid"] = False
                reasons = pending.setdefault("reasons", [])
                if "manual_extra_request" not in reasons:
                    reasons.append("manual_extra_request")
        self._manual_energy_ending = False
        self.save()
        self.publish_manual_energy()
        return self.manual_energy_snapshot()

    def stop_manual_energy(self):
        session = self.memory.get("manual_energy")
        if session and session.get("status") == "active":
            manual_energy.finish(session, "user_stop", time.time())
            self._manual_energy_ending = True
            self.save()
        self.publish_manual_energy()
        return self.manual_energy_snapshot()

    def manual_extra_demand(self, stamp, grid, outage, room, upper, coast, permitted):
        session = self.memory.get("manual_energy")
        if not session or session.get("status") != "active":
            self.manual_energy_demand = False
            return False
        if (grid != "on" or outage or not permitted or self.raw("mode") != "Автоматичне керування"
                or room is None or room >= upper or coast or self.memory.get("temporary_fallback")
                or (self.command_ledger_status or {}).get("fenced", True)
                or not self._on_loop(self.async_voltage_status)["voltage_qualified"]
                or not self._on_loop(self.async_kws_status).get("ready")):
            manual_energy.finish(session, "safety_stop", stamp)
        else:
            watts, measured_at = self._manual_verified_power(stamp)
            if session.get("power_only"):
                # Session started without a valid counter: only fresh three-phase
                # watts may advance it; lost measurements fail closed in 60 s.
                manual_energy.advance_power_only(session, watts, measured_at, stamp)
            else:
                value, at, _ = self._manual_kws_snapshot()
                manual_energy.advance(session, value, at, stamp)
                manual_energy.observe_power(session, watts, measured_at, stamp)
        self.manual_energy_demand = session.get("status") == "active"
        if not self.manual_energy_demand:
            self._manual_energy_ending = True
            self.save()
        self.publish_manual_energy()
        return self.manual_energy_demand

    def manual_energy_command(self, action, target=None):
        if action == "start":
            self.start_manual_energy(target)
        elif action == "stop":
            self.stop_manual_energy()
        else:
            raise ValueError("invalid_action")
        self.tick()
        return self.manual_energy_snapshot()

    def manual_kws_changed(self, *args):
        if self.memory.get("manual_energy", {}).get("status") == "active":
            self.tick()

    def listen_event(self, function, event_name, **kwargs):
        # Production settings are accepted only through the authenticated
        # administrator WebSocket command, never an unauthenticated HA event.
        if event_name == "heating_system_settings":
            return None
        return super().listen_event(function, event_name, **kwargs)

    def native_settings_snapshot(self):
        receipt = getattr(self, "system_settings_result",
                          self.memory.get("system_settings_receipt", {}))
        return dict(version=[1, 0],
            values={field["key"]:(wire_values(field.get("value"))
                    if field["key"] == "power_inlet_config" else
                    "0" if field.get("value") == 0 else field.get("value"))
                    for field in system_fields(self.cfg, self.model)},
            revision=self.memory.get("system_settings_revision", 0),
            persisted_revision=self.memory.get("system_settings_persisted_revision",
                                               self.memory.get("system_settings_revision", 0)),
            **receipt)

    def apply_native_settings(self, data):
        self.system_settings_changed("authenticated_native_api", data, {})
        return self.native_settings_snapshot()

    def initialize(self):
        super().initialize()
        self.memory.pop("pending_model",None)
        self._native_solar_fit=False
        checkpoint=self.memory.get("online_learning") or {}
        if checkpoint.get("rollback_reason"):
            self._learning_status=dict(status="rolled_back",reason=checkpoint["rollback_reason"])
        # Preserve all learned data and counters; old in-flight commands cannot
        # confer ownership after a process and platform migration.
        if not self.memory.get("native_runtime_initialized"):
            self.memory["owned"] = False
            for key in ("temporary_fallback", "recovering_from_fallback",
                        "expected_mode", "expected_setpoint", "once"):
                self.memory.pop(key, None)
            self.memory["native_runtime_initialized"] = True
        session = self.memory.get("manual_energy")
        if session and session.get("status") == "active":
            manual_energy.finish(session, "controller_restart", time.time())
            self.save()
        self._remember_manual_snapshot()
        self.listen_state(self.manual_kws_changed, "sensor.energy_meter_kotel_energiia", attribute="all")
        self.publish_manual_energy()
        # A fence is durable safety state, but the one-shot recovery guard is
        # runtime state. Reset only the guard on each fresh native runtime so
        # a fence created by an older process can be reconciled once after
        # restart. The ledger itself remains authoritative and the recovery
        # still requires automatic mode before issuing the safe STOP.
        self.memory.pop("fence_recovery_tick_seen", None)
        self.memory.pop("fence_recovery_attempted", None)
        self._fence_recovery_attempted = False
        self.memory["command_generation"] = self.memory.get("command_generation", 0)+1
        self.save()
        for entity in (self.args["armed"], self.args["mode"]):
            self.listen_state(self.manual_intent_changed, entity, attribute="all")
        for entity in (self.args.get("fan_control"), self.args.get("fan_mode")):
            if entity:
                self.listen_state(self.fan_permission_changed, entity, attribute="all")
        for entity in (self.args["thermostat"], self.args.get("fan")):
            if entity:
                self.listen_state(self.command_echo_changed, entity, attribute="all")
        if self.args.get("fan"):
            self._remember_fan_snapshot(baseline=True)
            self.listen_state(self.fan_state_changed, self.args["fan"], attribute="all")
        for entity in self.args["voltage_entities"]:
            self.listen_state(self.voltage_changed, entity, attribute="all")
        self.listen_state(self.voltage_changed, self.args["grid_presence_helper"])
        # Recovery is attempted from the existing periodic control tick only.
        self._on_loop(self._async_listen_kws_manual_intent)
        self._on_loop(self._async_listen_room_reports)

    @callback
    def _async_listen_kws_manual_intent(self):
        @callback
        def manual_intent(event):
            if event.context.id == self._context.id:
                return
            data = event.data
            if data.get("domain") not in ("switch", "homeassistant") or data.get("service") not in ("turn_on", "turn_off", "toggle"):
                return
            service_data = data.get("service_data") or {}
            target = service_data.get("entity_id") or (data.get("target") or {}).get("entity_id")
            relay = self.cfg.get("kws_relay", "switch.energy_meter_kotel")
            entities = [target] if isinstance(target, str) else target if isinstance(target, (tuple, list)) else []
            if relay in entities:
                self.kws_restore.manual_intent(data["service"])
            elif (not entities or "all" in entities) and self.kws_restore.outage_at is not None and data["service"] != "turn_on":
                # Ambiguous group/area commands cannot authorize an automatic reclose.
                self.kws_restore.cancel("ambiguous_manual_command")
        self._handles[uuid4().hex] = self.hass.bus.async_listen("call_service", manual_intent)

    @callback
    def async_manual_snapshot(self):
        """Return a live, memory-only token for the two manual permission inputs."""
        rows = []
        values = {}
        for name in ("armed", "mode"):
            entity = self.args[name]
            state = self.hass.states.get(entity)
            if state is None:
                values[name] = None
                rows.append([entity, None, None, None])
                continue
            values[name] = state.state
            rows.append([entity, state.state, getattr(state.context, "id", None),
                         state.last_updated.timestamp()])
        return {"signature": rows, **values}

    def _remember_manual_snapshot(self):
        snapshot = self._on_loop(self.async_manual_snapshot)
        previous = self.memory.get("native_manual_signature")
        if previous != snapshot["signature"]:
            self.memory["native_manual_epoch"] = self.memory.get("native_manual_epoch", 0)+1
            self.memory["native_manual_signature"] = snapshot["signature"]
        return snapshot

    @callback
    def async_fan_snapshot(self):
        def token(entity):
            state = self.hass.states.get(entity) if entity else None
            if state is None:
                return [entity, None, None, None]
            return [entity, state.state, getattr(state.context, "id", None),
                    state.last_updated.timestamp()]
        fan_entity = self.args.get("fan")
        fan = self.hass.states.get(fan_entity) if fan_entity else None
        control_entity, mode_entity = self.args.get("fan_control"), self.args.get("fan_mode")
        control = self.hass.states.get(control_entity) if control_entity else None
        mode = self.hass.states.get(mode_entity) if mode_entity else None
        return dict(
            permissions=[token(self.args.get("fan_control")), token(self.args.get("fan_mode"))],
            device=token(self.args.get("fan")), state=fan.state if fan else None,
            percentage=(fan.attributes.get("percentage") if fan else None),
            control=control.state if control else None,
            mode=mode.state if mode else None,
        )

    def _remember_fan_snapshot(self, *, baseline=False):
        snapshot = self._on_loop(self.async_fan_snapshot)
        previous = self.memory.get("native_fan_device_signature")
        external = (previous is not None and previous != snapshot["device"]
                    and snapshot["device"][2] != self._context.id)
        self.memory["native_fan_device_signature"] = snapshot["device"]
        if snapshot["control"] == "on":
            # Explicit fan controls own the device. Tuya telemetry/context
            # changes must not create an indefinite manual hold in AUTO.
            self.memory.pop("native_fan_manual_hold", None)
        elif external and not baseline:
            self.memory["native_fan_manual_epoch"] = self.memory.get("native_fan_manual_epoch", 0)+1
            self.memory["native_fan_manual_hold"] = dict(
                state=snapshot["state"], percentage=snapshot["percentage"], at=time.time())
            self.memory["fan_owned"] = False
        return snapshot

    def fan_state_changed(self, *args):
        self._remember_fan_snapshot()
        self.save()

    def fan_permission_changed(self, *args):
        self.memory["native_fan_manual_epoch"] = self.memory.get("native_fan_manual_epoch", 0)+1
        self.memory.pop("native_fan_manual_hold", None)
        self._remember_fan_snapshot(baseline=True)
        self.save()

    def manual_intent_changed(self, *args):
        if self.memory.get("manual_energy", {}).get("status") == "active":
            self.stop_manual_energy()
        self._remember_manual_snapshot()
        self.next_compute = 0
        self.save()
        self._reconcile_writer_fence()

    def climate_changed(self, entity, attribute, old, new, kwargs=None, **extra):
        # Only the user's mode helper may switch controller modes. A Tuya
        # echo or transient thermostat drift must never select Observation.
        if (not self.memory.get("owned") or time.time() < getattr(self, "command_pending_until", 0)
                or self.memory.get("temporary_fallback")):
            return
        expected = self.memory.get("expected_mode")
        if isinstance(new, dict):
            try:
                stamp = datetime.fromisoformat(
                    (new.get("last_updated") or new.get("last_changed")).replace("Z", "+00:00")).timestamp()
            except (TypeError, ValueError, AttributeError):
                stamp = None
            target = number(self.memory.get("expected_setpoint"))
            actual = self.val("thermostat", attribute="temperature")
            if (stamp is not None and stamp <= self.memory.get("command_at", 0)
                    and self.raw("thermostat") == expected
                    and target is not None and actual is not None
                    and abs(actual-target) <= .1):
                return
        state = new.get("state") if isinstance(new, dict) else new
        target = number((new.get("attributes") or {}).get("temperature")) if isinstance(new, dict) else None
        changed = (state not in (expected, "unknown", "unavailable", None)
                   or (target is not None and target != self.memory.get("expected_setpoint")))
        if not changed:
            return
        if self.raw("mode") == "Автоматичне керування" and self.raw("armed") == "on":
            # Cancel stale confirmation and replan under unchanged KWS,
            # voltage and command-ledger guards.
            self.memory["command_generation"] = self.memory.get("command_generation", 0)+1
            self.next_compute = 0
            self.decision_event("thermostat_external_change",
                                reason="Термостат змінився; автоматичне керування збережено",
                                force=True)
        else:
            # Explicit manual choices are authoritative and never overwritten.
            self.memory["owned"] = False
            self.decision_event("manual_thermostat_change",
                                reason="Ручний режим збережено після зміни термостата",
                                force=True)
        self.save()

    def _ledger_identity(self):
        status = self.command_ledger_status
        return dict(expected_revision=status["revision"], owner_id=status["owner_id"],
                    boot_id=status["boot_id"], owner_epoch=status["owner_epoch"])

    def _equipment_action(self, domain, service, data):
        if domain == "fan":
            return "STOP" if service == "turn_off" else "HEAT"
        dispatch = getattr(self, "_dispatch_action", None)
        if dispatch in ("off", "observe", "release"):
            return "STOP"
        if service == "set_hvac_mode":
            return "STOP" if data.get("hvac_mode") == "off" else "HEAT"
        thermostat = self.hass.states.get(self.args["thermostat"])
        try:
            current = float(thermostat.attributes["current_temperature"])
            target = float(data["temperature"])
            return "HEAT" if target > current else "STOP"
        except (ValueError, TypeError, AttributeError, KeyError):
            return "HEAT"

    def equipment_command_pending(self, domain, service, data):
        if domain != "climate" or not self._pending_command_evidence:
            return False
        # Only a real OFF may supersede unresolved equipment evidence. Normal
        # setpoint/mode stages wait for echo; a repeated OFF waits too.
        if service == "set_hvac_mode" and data.get("hvac_mode") == "off":
            return any(e["service"] == "set_hvac_mode" and
                       e["data"].get("hvac_mode") == "off"
                       for e in tuple(self._pending_command_evidence.values()))
        return True

    def prepare_equipment_command(self, domain, service, data):
        if self.command_ledger is None:
            raise PermissionError("Durable command journal is unavailable")
        self._command_sequence += 1
        command_id = f"{self.command_ledger_status['boot_id'][:16]}-{self._command_sequence}"
        action = self._equipment_action(domain, service, data)
        request = dict(sequence=self._command_sequence, domain=domain, service=service,
                       entity_id=data.get("entity_id"),
                       dispatch_action=getattr(self, "_dispatch_action", None),
                       manual_epoch=self.memory.get("native_manual_epoch"),
                       values={key:value for key,value in data.items() if key != "entity_id"})
        result = self.command_ledger.prepare(command_id, action, request,
                                             **self._ledger_identity())
        self.command_ledger_status["revision"] = result["revision"]
        if action == "STOP":
            self.command_ledger_status["fenced"] = result.get("fenced", True)
            self._pending_command_evidence.clear()
        evidence = dict(command_id=command_id, action=action, domain=domain, prepared_at=time.time(),
                        service=service, data=dict(data))
        self._pending_command_evidence[command_id] = evidence
        self.memory["native_command_evidence"] = dict(
            command_id=command_id, action=action, stage="PREPARED",
            sequence=self._command_sequence)
        return evidence

    def mark_equipment_command_sent(self, evidence):
        evidence["sent_at"] = time.time()
        revision = self.command_ledger.record_sent(
            evidence["command_id"], at=evidence["sent_at"], **self._ledger_identity())
        self.command_ledger_status["revision"] = revision
        self.memory["native_command_evidence"].update(stage="SENT", sent_at=evidence["sent_at"])

    @callback
    def async_independent_stop_seen(self, sent_at):
        """Prove a requested safe STOP from live KWS, never cached OFF alone."""
        if self.cfg.get("power_accounting_method") != "kws":
            return False
        thermostat = self.hass.states.get(self.args["thermostat"])
        if thermostat is None or thermostat.state != "off":
            return False
        if not self.async_kws_status().get("ready"):
            return False
        now = time.time()
        power_kw = 0.0
        for phase in "abc":
            volts = self.hass.states.get("sensor.energy_meter_kotel_napruga_" + phase)
            watts = self.hass.states.get("sensor.energy_meter_kotel_potuzhnist_" + phase)
            if volts is None or watts is None:
                return False
            try:
                voltage = float(volts.state)
                power = float(watts.state)
                reported = volts.last_reported.timestamp()
            except (ValueError, TypeError, AttributeError):
                return False
            if (not isfinite(voltage) or not isfinite(power)
                    or not sent_at <= reported <= now + 5
                    or now - reported > 45
                    or not 190 <= voltage <= 260
                    or not 0 <= power < 0.15):
                return False
            power_kw += power
        return power_kw < 0.15

    @callback
    def async_equipment_command_echo(self, domain, service, data, evidence=None):
        # Notifications have notification_id, not an equipment entity. They
        # cannot supply physical command evidence or enter HA StateMachine.get.
        if domain not in ("climate", "fan") or not isinstance(data.get("entity_id"), str):
            if (domain == "persistent_notification" and service == "dismiss"
                    and data.get("notification_id") == "heating_control_fault"):
                error = ((getattr(self, "memory", {}) or {}).get("diagnostic_errors") or {}).get("fallback_command")
                if error and error.get("signature") == "AttributeError: 'NoneType' object has no attribute 'lower'":
                    # This hook runs only after the blocking dismiss succeeded.
                    # Retain the incident/count; resolve its obsolete active flag.
                    error.update(active=False, recovered_at=time.time())
            return {"matched": False, "observed_at": time.time()}
        state = self.hass.states.get(data.get("entity_id"))
        matched = False
        # A completed blocking service may confirm an idempotent command even
        # when HA does not emit a new state. Later listener reconciliation must
        # be tied to this controller's context and cannot borrow a manual change.
        if state is not None and evidence is not None \
                and evidence.get("service_completed_at") is None:
            context = getattr(getattr(state, "context", None), "id", None)
            updated = getattr(state, "last_updated", None)
            if (context != self._context.id or updated is None
                    or updated.timestamp() < evidence.get("sent_at", 0)):
                return {"matched": False, "observed_at": time.time()}
        if state is not None and domain == "climate" and service == "set_hvac_mode":
            matched = state.state == data.get("hvac_mode")
            if matched and data.get("hvac_mode") == "off" and evidence is not None:
                # Service return alone cannot reconcile a pre-existing OFF display.
                sent_at = evidence.get("sent_at", float("inf"))
                matched = (state.last_reported.timestamp() >= sent_at or
                           evidence.get("service_completed_at") is not None and
                           evidence.get("independent_off_verified_at", -1) >= sent_at)
            if matched and data.get("hvac_mode") == "heat" \
                    and ((evidence or {}).get("action") == "STOP"):
                try:
                    current = float(state.attributes.get("current_temperature"))
                    target = float(state.attributes.get("temperature"))
                    matched = target <= current
                except (ValueError, TypeError):
                    matched = False
        elif state is not None and domain == "climate" and service == "set_temperature":
            try:
                matched = abs(float(state.attributes.get("temperature"))
                              - float(data["temperature"])) <= .05
            except (ValueError, TypeError, KeyError):
                pass
        elif state is not None and domain == "fan" and service == "turn_off":
            matched = state.state == "off"
        elif state is not None and domain == "fan" and service == "turn_on":
            matched = state.state == "on"
            if matched and "percentage" in data:
                try:
                    matched = int(state.attributes.get("percentage")) == int(data["percentage"])
                except (ValueError, TypeError):
                    matched = False
        return {"matched": matched, "observed_at": time.time()}

    def discard_pending_equipment_command(self, evidence):
        # Durable SENT stays unresolved for restart reconciliation; it must not
        # later be mistaken for a manual state change that happens to match.
        self._pending_command_evidence.pop(evidence["command_id"], None)
        self.command_ledger.fence(**self._ledger_identity())
        self.command_ledger_status = self.command_ledger.status()

    def record_equipment_command_echo(self, evidence, observed):
        if not observed or not observed.get("matched"):
            return False
        command_id = evidence["command_id"]
        # The blocking service path and the HA state listener can confirm the
        # same command concurrently.  A second HA_ECHO is a ledger conflict,
        # not a new equipment transition.
        with self._echo_lock:
            if command_id in self._echoed_command_ids:
                return True
            if command_id not in self._pending_command_evidence:
                return False  # Superseded/failed evidence cannot borrow a later echo.
            try:
                revision = self.command_ledger.record_ha_echo(
                    command_id,
                    at=max(observed["observed_at"], evidence["sent_at"] + 1e-6),
                    **self._ledger_identity())
            except LedgerConflict as exc:
                self._pending_command_evidence.pop(command_id, None)
                self.record_failure("ledger_echo", exc)
                return False
            if len(self._echoed_command_ids) >= 256:
                self._echoed_command_ids.clear()
            self._echoed_command_ids.add(command_id)
            self.command_ledger_status["revision"] = revision
            self.memory["native_command_evidence"].update(
                stage="HA_ECHO", ha_echo_at=observed["observed_at"])
            if (evidence["action"] == "STOP" and evidence["domain"] == "climate"
                    and evidence["service"] == "set_hvac_mode"
                    and evidence["data"].get("hvac_mode") == "off"):
                revision = self.command_ledger.complete_safe_stop(
                    command_id, **self._ledger_identity())
                self.command_ledger_status.update(revision=revision, fenced=False)
                self.memory["ledger_recovery"] = dict(state="reconciliation_verified", at=time.time())
                self.next_compute = 0
                for stage in ("ledger_recovery", "ledger_echo", "equipment_stop", "fallback_command"):
                    error = (self.memory.get("diagnostic_errors") or {}).get(stage)
                    if error and (stage != "fallback_command" or error.get("signature", "").startswith("LedgerConflict:")):
                        error.update(active=False, recovered_at=time.time())
            self._pending_command_evidence.pop(command_id, None)
        return True

    def command_echo_changed(self, *args):
        for evidence in tuple(self._pending_command_evidence.values()):
            observed = self._on_loop(self.async_equipment_command_echo,
                                     evidence["domain"], evidence["service"], evidence["data"], evidence)
            if observed["matched"]:
                self.record_equipment_command_echo(evidence, observed)

    def _reconcile_writer_fence(self):
        if self.command_ledger_status is None:
            return
        if self._pending_command_evidence:
            expired = [e for e in self._pending_command_evidence.values()
                       if time.time() - e.get("sent_at", e.get("prepared_at", time.time())) >= 30]
            if expired:
                self.command_ledger.fence(**self._ledger_identity())
                self.command_ledger_status = self.command_ledger.status()
                self._pending_command_evidence.clear()
        if not self.command_ledger_status.get("fenced"):
            self._recovery_attempts = 0
            self._recovery_next_at = 0
            return
        now = time.time()
        active = self._equipment_service_task
        if active is not None and not active.done():
            self.memory["ledger_recovery"] = dict(state="reconciliation_waiting", reason="service_in_flight")
            return
        online = self.raw("thermostat") not in (None, "unknown", "unavailable")
        if not online:
            self._recovery_was_offline = True
            self.memory["ledger_recovery"] = dict(state="reconciliation_waiting", reason="thermostat_unavailable")
            return
        if getattr(self, "_recovery_was_offline", False):
            self._recovery_attempts = 0
            self._recovery_next_at = 0
            self._recovery_was_offline = False
        if self._recovery_attempts >= 8:
            self.memory["ledger_recovery"] = dict(state="heat_fenced", reason="manual_reconciliation_required", attempts=8)
            return
        if now < self._recovery_next_at:
            return
        if (self.raw("mode") != "Автоматичне керування"
                or self.raw("armed") != "on" or not self.cfg.get("allow_actuation")):
            self.memory["ledger_recovery"] = dict(state="heat_fenced", reason="manual_authority")
            return
        manual = self._remember_manual_snapshot()
        self._dispatch_manual_signature = manual["signature"]
        self._dispatch_manual_epoch = self.memory["native_manual_epoch"]
        self._dispatch_action = "off"
        self._recovery_inflight = True
        # The legacy manual-change listener must expect our reconciliation OFF.
        self.command_pending_until = now+20
        self.memory.update(expected_mode="off", expected_setpoint=self.val("thermostat", attribute="temperature"),
                           last_command="off", command_at=now,
                           command_generation=self.memory.get("command_generation", 0)+1)
        self._recovery_attempts += 1
        self._recovery_next_at = now + min(300, 30 * 2 ** min(self._recovery_attempts-1, 4))
        self.memory["ledger_recovery"] = dict(state="safe_stop_required",
            last_attempt=now, attempts=self._recovery_attempts, next_attempt=self._recovery_next_at)
        try:
            # call_service owns PREPARED/SENT/echo. A second reservation here
            # used to supersede that very STOP and invalidate its callback.
            self.call_service("climate/set_hvac_mode",
                              entity_id=self.cfg["thermostat"], hvac_mode="off")
            if self.command_ledger_status.get("fenced"):
                self.memory["ledger_recovery"]["state"] = "reconciliation_waiting"
        finally:
            self._recovery_inflight = False
            self._dispatch_action = None
            self._dispatch_manual_signature = None
            self._dispatch_manual_epoch = None

    def grid_state(self):
        # The main three-phase input remains the physical source. Deye is
        # supplementary, not a mandatory heartbeat during a transport outage.
        legacy = super().grid_state()
        if legacy != "unknown" or self.cfg.get("power_accounting_method") != "kws":
            return legacy
        helper = self.args.get("grid_presence_helper")
        phases = self.args.get("voltage_entities") or []
        if not helper or self.get_state(helper) not in ("on", "Наявна мережа") or len(phases) != 3:
            return legacy
        # Preserve a live contradictory inverter observation. Do not treat
        # unknown or stale inverter telemetry as a physical outage.
        if self.raw("connection") == "on" and self.fresh(self.cfg["grid_voltage"], 90):
            grid, volts = self.raw("grid"), self.val("grid_voltage")
            if grid == "off" or (volts is not None and volts < 150):
                return legacy
        def current(entity):
            value = number(self.get_state(entity))
            return value if value is not None and self.fresh(entity, 90) else None
        primary = [current(entity) for entity in phases]
        backup = [current("sensor.energy_meter_kotel_napruga_"+p) for p in "abc"] if any(v is None for v in primary) else []
        selected, _ = select_phase_voltages(primary, backup, self.args.get("min_voltage_v", 190),
                                            self.args.get("max_voltage_v", 260))
        return "on" if all(v is not None and 150 <= v <= 350 for v in selected) else legacy

    @callback
    def async_voltage_status(self):
        now = time.time()
        presence = self.hass.states.get(self.args["grid_presence_helper"])
        confirmed = presence is not None and presence.state in ("on", "Наявна мережа")
        def current(entity):
            state = self.hass.states.get(entity)
            try:
                voltage = float(state.state)
                age = now-state.last_reported.timestamp()
                return voltage if isfinite(voltage) and 0 <= voltage <= 350 and -5 <= age <= 90 else None
            except (ValueError, TypeError, AttributeError):
                return None
        primary = [current(entity) for entity in self.args["voltage_entities"]]
        backup = []
        if (confirmed and self.cfg.get("power_accounting_method") == "kws"
                and any(v is None for v in primary)):
            backup = [current("sensor.energy_meter_kotel_napruga_"+p) for p in "abc"]
        phases, voltage_source = select_phase_voltages(
            primary, backup, self.args.get("min_voltage_v", 190), self.args.get("max_voltage_v", 260))
        self.voltage_status = self.voltage_guard.evaluate(
            now, confirmed, phases,
            minimum_v=self.args.get("min_voltage_v", 190),
            maximum_v=self.args.get("max_voltage_v", 260),
            recovery_seconds=self.args.get("voltage_recovery_seconds", 30),
        )
        self.voltage_status.update(physical_commands_enabled=self._writer,
                                   physical_authority=self._writer, voltage_source=voltage_source)
        return self.voltage_status

    @callback
    def async_kws_status(self):
        if self.cfg.get("power_accounting_method") != "kws":
            return dict(ready=True, reason="not_configured")
        now = time.time()
        def row(entity):
            state = self.hass.states.get(entity)
            if state is None:
                return {}
            value = state.state
            try:
                value = float(value)
            except (ValueError, TypeError):
                pass
            return dict(value=value, at=state.last_reported.timestamp(),
                        **({"fault_code": state.attributes["fault_code"]}
                           if "fault_code" in state.attributes else {}))
        grid = row(self.cfg["grid"])
        connection = row(self.cfg["connection"])
        deye_voltage = row(self.cfg["grid_voltage"])
        # Binary statuses may legitimately stay unchanged. A live voltage
        # report from this inverter proves link liveness, not their change time.
        deye_fresh = (deye_voltage.get("at") is not None
                      and -5 <= now-deye_voltage["at"] <= 90)
        physical = ("on" if deye_fresh and connection.get("value") == "on"
                    and grid.get("value") == "on" and
                    isinstance(deye_voltage.get("value"), (int, float)) and
                    deye_voltage["value"] >= 150 else
                    "off" if deye_fresh and connection.get("value") == "on" and
                    grid.get("value") == "off" and isinstance(deye_voltage.get("value"), (int, float))
                    and deye_voltage["value"] < 50 else "unknown")
        rows = {"status": row(self.cfg.get("kws_status", "sensor.energy_meter_kotel_status")),
                "relay": row(self.cfg.get("kws_relay", "switch.energy_meter_kotel")),
                "fault": row(self.cfg.get("kws_fault", "binary_sensor.energy_meter_kotel_problema"))}
        rows.update({p: row("sensor.energy_meter_kotel_napruga_"+p) for p in "abc"})
        epochs = []
        for entity in (self.cfg["grid"], self.cfg["grid_presence_helper"]):
            state = self.hass.states.get(entity)
            changed = getattr(state, "last_changed", None)
            if state is not None and state.state in ("on", "Наявна мережа") and changed is not None:
                epochs.append(changed.timestamp())
        grid_epoch = max(epochs) if len(epochs) == 2 else None
        voltage_report = self.async_voltage_status()
        if physical == "unknown" and voltage_report["voltage_qualified"]:
            # The live independent input guard already established three safe
            # phases and confirmed the main grid helper. A missing Deye
            # connection is not an additional reason to cut power.
            contradictory = (deye_fresh and connection.get("value") == "on" and
                (grid.get("value") == "off" or
                 (isinstance(deye_voltage.get("value"), (int, float)) and deye_voltage["value"] < 150)))
            if not contradictory:
                physical = "on"
        self.equipment_status = self.kws_readiness.evaluate(now, physical,
            voltage_report["voltage_qualified"], rows,
            reset_delay=self.cfg.get("kws_reset_delay", 120), grid_epoch=grid_epoch)
        self.equipment_status["kws_relay_entity"] = self.cfg.get("kws_relay", "switch.energy_meter_kotel")
        restore = getattr(self, "kws_restore", None)
        if restore is not None:
            enabled = self.cfg.get("kws_auto_restore") == "enabled"
            restore.observe(now, self.equipment_status, enabled)
            self.equipment_status["kws_auto_restore"] = enabled
            self.equipment_status["kws_restore_status"] = restore.status
            self.equipment_status["kws_restore_manual_hold"] = restore.manual_hold
        return self.equipment_status

    @callback
    def _async_kws_restore_gate(self, report, *, claimed=False):
        """Re-read physical inputs immediately before any relay energization."""
        if (not self._running or not self._writer or self._closing
                or not self.cfg.get("allow_actuation")
                or self.cfg.get("power_accounting_method") != "kws"
                or self.cfg.get("kws_auto_restore") != "enabled"
                or self.memory.get("state_load_error") or getattr(self, "settings_transaction", False)
                or (self.command_ledger_status or {}).get("fenced", True)
                or self._pending_command_evidence
                or (self._equipment_service_task is not None and not self._equipment_service_task.done())
                or self.raw("armed") != "on" or self.raw("mode") != "Автоматичне керування"
                or (self.memory.get("manual_energy") or {}).get("status") == "active"):
            return False
        now = time.time()
        thermostat = self.hass.states.get(self.args["thermostat"])
        try:
            # Closing a supply relay must never be an unobserved heat start.
            if (thermostat is None or thermostat.state != "off"
                    or not report.get("grid_since", now) <= thermostat.last_reported.timestamp() <= now+5
                    or now-thermostat.last_reported.timestamp() > 180):
                return False
            delay = self.hass.states.get(self.cfg.get("kws_reset_delay_entity", "number.energy_meter_kotel_reset_delay"))
            reset_delay = float(delay.state)
        except (AttributeError, TypeError, ValueError):
            return False
        # Relay reclosure needs both independent input and KWS voltage readings.
        if (self.voltage_status or {}).get("voltage_source") != "input":
            return False
        return self.kws_restore.eligible(now, report, self.voltage_status or {}, reset_delay, claimed=claimed)

    @callback
    def _async_claim_kws_restore(self):
        report = self.async_kws_status()
        return self._async_kws_restore_gate(report) and self.kws_restore.claim()

    def _try_kws_restore(self):
        # One attempt per observed outage; no retry after a trip, command error,
        # HA restart or manual override. The normal plan still controls heat.
        now = time.time()
        outage = self.outage_execution_schedule(grid=self.grid_state(), stamp=now)
        if outage.get("valid") and blocked(now, now+15, outage.get("outages", [])):
            return
        if not self._on_loop(self._async_claim_kws_restore):
            return
        success = False
        try:
            relay = self.cfg.get("kws_relay", "switch.energy_meter_kotel")
            self.memory["kws_restore_attempt"] = dict(at=now, entity_id=relay, status="prepared")
            self.save()
            # NativeBridge checks the same live interlock twice around dispatch.
            self.call_service("switch/turn_on", entity_id=relay)
            self.memory["kws_restore_attempt"]["status"] = "service_returned"
            success = True
        except Exception as exc:
            self.memory["kws_restore_attempt"]["status"] = "failed_no_retry"
            self.record_failure("kws_restore", exc)
        finally:
            self._on_loop(self.kws_restore.finish, success)
            self.save()

    @callback
    def async_command_allowed(self, domain, service, data):
        if domain == "switch":
            return (service == "turn_on"
                    and data.get("entity_id") == self.cfg.get("kws_relay", "switch.energy_meter_kotel")
                    and self.kws_restore.inflight
                    and self._async_kws_restore_gate(self.async_kws_status(), claimed=True))
        if domain == "fan":
            fan = self.async_fan_snapshot()
            if (getattr(self, "_fan_dispatch_signature", None) != fan["permissions"]
                    or getattr(self, "_fan_dispatch_device_signature", None) != fan["device"]
                    or getattr(self, "_fan_dispatch_epoch", None) !=
                    self.memory.get("native_fan_manual_epoch", 0)
                    or self.memory.get("native_fan_manual_hold")):
                return False
            if fan["control"] != "on":
                return False
            return service == "turn_off" or fan["mode"] != "Зупинити"
        if domain != "climate":
            return True
        if service == "set_hvac_mode" and data.get("hvac_mode") == "off":
            if getattr(self, "_recovery_inflight", False):
                manual = self.async_manual_snapshot()
                return (manual["signature"] == self._dispatch_manual_signature
                        and manual["armed"] == "on" and manual["mode"] == "Автоматичне керування")
            return True
        if not self.async_voltage_status()["voltage_qualified"]:
            return False
        # Re-read HA immediately before each dispatch, outside the worker's
        # cached tick snapshot. A manual choice between setpoint and mode must
        # invalidate the old positive demand without overwriting that choice.
        thermostat = self.hass.states.get(self.args["thermostat"])
        try:
            current = float(thermostat.attributes["current_temperature"])
            target = float(data.get("temperature", thermostat.attributes.get("temperature")))
            positive = not (isfinite(current) and isfinite(target)) or target > current
        except (ValueError, TypeError, AttributeError, KeyError):
            positive = True
        if (service == "set_hvac_mode" and data.get("hvac_mode") in ("heat", "auto")
                and getattr(self, "_dispatch_action", None) not in ("off", "observe", "release")):
            positive = True
        if not positive:
            return True  # Original V1 release lowers the target in HEAT mode.
        if (self.command_ledger_status or {}).get("fenced", True):
            return False
        if not self.async_kws_status()["ready"]:
            return False
        if self.cfg.get("power_accounting_method") == "kws":
            now = time.time()
            for role, attribute, age in (("return", None, self.cfg.get("return_max_age", 1200)), ("thermostat", "current_temperature", 5400)):
                state = self.hass.states.get(self.args.get(role))
                try:
                    value = float(state.state if attribute is None else state.attributes.get(attribute))
                    plausible = 0 < value < 65 if role == "return" else 5 < value < 40
                    if not isfinite(value) or not plausible or not -5 <= now-state.last_reported.timestamp() <= age:
                        return False
                except (ValueError, TypeError, AttributeError):
                    return False
            room = self.hass.states.get(self.args.get("room"))
            try:
                room_ok = (5 < float(room.state) < 40 and
                    -5 <= now-room.last_reported.timestamp() <= self.cfg.get("room_max_age", 14400))
            except (ValueError, TypeError, AttributeError):
                room_ok = False
            quality = getattr(self, "temperature_quality", {})
            bias_at = self.memory.get("thermostat_bias_at", 0)
            if not room_ok and not (quality.get("backup_usable") and 0 <= now-bias_at <= 86400):
                return False
        manual = self.async_manual_snapshot()
        if (getattr(self, "_dispatch_manual_signature", None) != manual["signature"]
                or getattr(self, "_dispatch_manual_epoch", None) !=
                self.memory.get("native_manual_epoch")):
            return False
        if manual["armed"] != "on" or manual["mode"] is None:
            return False
        if manual["mode"] == "Автоматичне керування":
            return True
        # An explicitly selected native AUTO keeps its established fallback
        # target semantics, but an old model HEAT cannot pass this boundary.
        return (manual["mode"] == "Штатний auto" and
                getattr(self, "_dispatch_action", None) == "fallback" and
                (service != "set_hvac_mode" or data.get("hvac_mode") == "auto"))

    def voltage_changed(self, *args):
        if getattr(self, "runtime_ready", False):
            self.apply_voltage_stop()

    def apply_voltage_stop(self):
        report = self._on_loop(self.async_voltage_status)
        now = time.time()
        signature = (report["status"], tuple(report["affected_phases"]))
        publish = (signature != getattr(self, "_voltage_signature", None)
                   or now-getattr(self, "_voltage_published_at", 0) >= 30)
        if publish:
            self.publish("sensor.heating_voltage_guard", report["status"], **report)
            self._voltage_signature, self._voltage_published_at = signature, now
        if report["voltage_qualified"]:
            if self.memory.pop("voltage_hold", None):
                self.next_compute = 0
                self.save()
            return False
        active = (self.cfg.get("allow_actuation") and self.raw("armed") == "on"
                  and self.raw("mode") in ("Автоматичне керування", "Штатний auto"))
        if not active:
            return False
        plan = self.memory.get("plan_execution")
        if isinstance(plan, dict):
            plan.update(status="blocked_voltage", blocked_reason=report["reason"])
        changed = False
        if (self.raw("thermostat") not in (None, "unavailable", "unknown", "off")
                and now-getattr(self, "_voltage_stop_at", 0) >= 5):
            self._voltage_stop_at = now
            self.command_pending_until = time.time()+20
            self.memory.update(expected_mode="off", expected_setpoint=self.val("thermostat", attribute="temperature"))
            command = dict(action="voltage_stop", requested_at=now, requested_mode="off", status="requested")
            self.memory["command_diagnostic"] = command
            try:
                self.call_service("climate/set_hvac_mode", entity_id=self.cfg["thermostat"], hvac_mode="off")
            except Exception:
                command["status"] = "service_error"
                raise
            command.update(status="service_returned", service_returned_at=time.time())
            self.memory.update(last_command="off", command_at=time.time(), owned=True)
            generation = self.memory.get("command_generation", 0)+1
            self.memory["command_generation"] = generation
            self.run_in(self.verify_voltage_stop, 12, generation=generation)
            changed = True
        first_hold = not self.memory.get("voltage_hold")
        self.memory["voltage_hold"] = report["reason"]
        if publish or changed or first_hold:
            self.publish("sensor.heating_controller", "Захист напруги", reason=report["reason"],
                         control_method="voltage_safety", proposed_action="Нагрів заблоковано",
                         voltage_guard=report)
        if first_hold or changed:
            self.save()
        return True

    def verify_voltage_stop(self, kwargs):
        if kwargs["generation"] != self.memory.get("command_generation"):
            return
        command = self.memory.get("command_diagnostic") or {}
        if command.get("action") != "voltage_stop":
            return
        accepted = self.raw("thermostat") == "off"
        command.update(status="confirmed" if accepted else "not_confirmed",
                       accepted_mode=self.raw("thermostat"), confirmed_at=time.time())
        if not accepted and self.memory.get("voltage_hold"):
            self.notify_once("Захист напруги: термостат не підтвердив вимкнення нагріву")
            self.apply_voltage_stop()
        self.save()

    def _can_hold_kws_link_gap(self, action, once):
        """Keep a previously confirmed ordinary heating demand; never dispatch
        a positive command or extend a manual-energy session on cached KWS.
        """
        if (action not in ("heat", "fallback") or once
                or not self.memory.get("owned") or self.memory.get("last_command") != "heat"
                or not self.cfg.get("allow_actuation") or self.raw("armed") != "on"
                or self.raw("mode") != "Автоматичне керування"
                or self.raw("thermostat") != "heat"
                or self.memory.get("expected_mode") != "heat"
                or self._pending_command_evidence
                or self.memory.get("temporary_fallback")
                or (self.memory.get("manual_energy") or {}).get("status") == "active"
                or getattr(self, "_manual_energy_ending", False)):
            return False
        # Only missing metering is tolerated. Other sensor, model, permission,
        # outage and comfort checks continue to run normally.
        if set(getattr(self, "control_input_faults", ["unknown"])) - {"power"}:
            return False
        context = getattr(self, "decision_context", None) or {}
        if context.get("coast_overheat") or context.get("demand") is not True:
            return False
        now = time.time()
        grid = self.grid_state()
        if grid != "on":
            return False
        outage = self.outage_execution_schedule(grid=grid, stamp=now)
        if outage.get("valid") and blocked(now, now+15, outage.get("outages", [])):
            return False
        room = self.source_temperature()[0]
        probe, current, target = self.val("return"), self.val("thermostat", attribute="current_temperature"), self.val("thermostat", attribute="temperature")
        if (room is None or room >= self.active_upper() or probe is None or not 0 < probe < self.cfg.get("return_limit", 50)
                or not self.fresh(self.args["return"], self.cfg.get("return_max_age", 1200))
                or current is None or target is None or not 5 < current < 40
                or not self.fresh(self.args["thermostat"], self.cfg.get("thermostat_temperature_max_age", 5400))
                or target <= current or target != self.memory.get("expected_setpoint")):
            return False
        return (any(seg["start"] <= now < seg["end"] for seg in self.memory.get("planned_heat_segments", []))
                or self.minimum_heat_commit(now, grid, False, room))

    def apply_action(self, action, once):
        if self.apply_voltage_stop():
            return
        equipment = self._on_loop(self.async_kws_status)
        fenced = (self.command_ledger_status or {}).get("fenced", True)
        automatic_fence = (fenced and self.raw("mode") == "Автоматичне керування" and self.raw("armed") == "on")
        if (not fenced and equipment.get("continue_allowed") and
                self._can_hold_kws_link_gap(action, once)):
            self.publish("sensor.heating_controller", "Коротка втрата зв’язку KWS",
                         reason="Утримуємо лише чинний підтверджений запит; нові команди заборонені",
                         control_state="degraded", equipment_readiness=equipment,
                         comfort_available=True, physical_heating_confirmed=False)
            return
        if automatic_fence or not equipment["ready"] or (fenced and action in ("heat", "fallback")):
            reason = "Журнал команд: очікується підтверджене вимкнення" if fenced else equipment.get("reason_text", equipment["reason"])
            self.publish("sensor.heating_controller", "Нагрів заблоковано",
                         reason=reason, control_state="blocked", equipment_readiness=equipment,
                         ledger_recovery=self.memory.get("ledger_recovery"),
                         comfort_available=False)
            if equipment.get("diagnosis_overdue") and not getattr(self, "_kws_notified", False):
                self._kws_notified = True
                self.notify_once("KWS не готовий до нагріву: "+reason)
            if not fenced and not equipment["ready"] and self.raw("thermostat") not in (None, "unknown", "unavailable", "off"):
                try:
                    self.call_service("climate/set_hvac_mode", entity_id=self.cfg["thermostat"], hvac_mode="off")
                except Exception as exc:
                    self.record_failure("equipment_stop", exc)
            self._track_comfort_lock(reason)
            return
        self._kws_notified = False
        lock = self.memory.get("comfort_lock")
        if lock and lock.get("active"):
            lock.update(active=False, recovered_at=time.time())
        manual = self._remember_manual_snapshot()
        self._dispatch_manual_signature = manual["signature"]
        self._dispatch_manual_epoch = self.memory["native_manual_epoch"]
        self._dispatch_action = action
        try:
            return super().apply_action(action, once)
        finally:
            self._dispatch_action = None
            self._dispatch_manual_signature = None
            self._dispatch_manual_epoch = None

    def _track_comfort_lock(self, reason):
        room = self.source_temperature()[0]
        if room is None or room >= self.val("lower", 21.5):
            return
        now = time.time()
        lock = self.memory.get("comfort_lock")
        if not lock or not lock.get("active"):
            lock = dict(active=True, since=now, reason=reason, notified=False)
            self.memory["comfort_lock"] = lock
        if now-lock["since"] >= 900 and not lock["notified"]:
            lock["notified"] = True
            self.notify_once("У кімнаті холодніше допустимого; безпечне резервне опалення недоступне: "+reason)

    def fallback_target(self, current, attrs):
        target = super().fallback_target(current, attrs)
        room, _, _, thermostat = self.source_temperature()
        now = time.time()
        bias = self.memory.get("thermostat_bias")
        bias_at = self.memory.get("thermostat_bias_at")
        quality = getattr(self, "temperature_quality", {})
        settings = {key: self.val(key, default) for key, default in
                    (("target", 22), ("lower", 21.5), ("upper", 22.5))}
        if self.cfg.get("night_target"):
            settings.update({key: self.val(key) for key in ("night_target", "night_lower", "night_upper")})
            settings.update(sleep_start=self.raw("sleep_start"), sleep_end=self.raw("sleep_end"))
        comfort, lower, upper, _ = comfort_at(self.now(), settings)
        valid = (self.cfg.get("fallback_bias_enabled") is True
                 and self.raw("mode") == "Автоматичне керування"
                 and quality.get("primary_status") == "normal"
                 and quality.get("thermostat_age_seconds", 1e9) <= 300
                 and all(isinstance(v, (int, float)) and isfinite(v)
                         for v in (room, thermostat, bias, bias_at, lower, upper, comfort))
                 and 0 <= now-bias_at <= 86400 and abs(bias) <= 2
                 and abs(thermostat-room-bias) <= .3
                 and room < lower-.1 and target >= (current or 0)
                 and not (self.command_ledger_status or {}).get("fenced", True))
        if valid:
            step = float(attrs.get("target_temp_step") or .5)
            ceiling = min(float(attrs.get("max_temp") or 35), upper+bias)
            corrected = min(comfort+bias, ceiling)
            target = max(float(attrs.get("min_temp") or 5),
                         min(round(corrected/step)*step, int(ceiling/step)*step))
        unavailable = (isinstance(room, (int, float)) and isinstance(thermostat, (int, float))
                       and isinstance(lower, (int, float)) and room < lower and target <= thermostat)
        self.memory["fallback_comfort"] = dict(available=not unavailable, bias_applied=bool(valid))
        if unavailable:
            self._track_comfort_lock("Резервний auto не запитує нагрів через різницю показів датчиків")
        return target

    def fan_tick(self, grid, fire_active):
        fan = self._remember_fan_snapshot()
        self._fan_dispatch_signature = fan["permissions"]
        self._fan_dispatch_device_signature = fan["device"]
        self._fan_dispatch_epoch = self.memory.get("native_fan_manual_epoch", 0)
        try:
            return super().fan_tick(grid, fire_active)
        finally:
            self._fan_dispatch_signature = None
            self._fan_dispatch_device_signature = None
            self._fan_dispatch_epoch = None

    def publish(self, entity, state, **attributes):
        if entity=="sensor.heating_model":
            report=self._learning_status
            checkpoint=self.memory.get("online_learning") or {}
            now=self.now()
            morning=now.replace(hour=10,minute=0,second=0,microsecond=0)
            evening=now.replace(hour=20,minute=45,second=0,microsecond=0)
            upcoming=morning if now<morning else evening if now<evening else morning+timedelta(days=1)
            metrics=report.get("metrics",{})
            accepted_metrics=checkpoint.get("metrics",{})
            previous=checkpoint.get("prior_model") or {}
            changes=[dict(parameter=key,before=previous[key],after=self.model[key])
                     for key in learning.FIT_FIELDS
                     if key in previous and key in self.model and previous[key]!=self.model[key]]
            attributes.update(last_accepted_changes=changes,
                last_completed_analysis=(self.memory.get("adaptive_schedule") or {}).get("completed_at"),
                last_accepted_mae_1h=accepted_metrics.get("1",{}).get("new",{}).get("mae"),
                last_accepted_mae_3h=accepted_metrics.get("3",{}).get("new",{}).get("mae"),
                learning_status=report["status"],learning_gate=report["reason"],
                last_successful_learning=(checkpoint.get("last_successful_learning") or
                                          (self.memory.get("learning_success") or {}).get("time")),
                last_adaptive_promotion=checkpoint.get("last_successful_learning"),
                independent_windows=report.get("independent_windows",0),
                qualified_minutes=report.get("qualified_minutes",0.),
                verified_reports=report.get("verified_reports",0),
                learning_eligibility=report.get("eligibility_counts",{}),
                learning_history=[r for r in checkpoint.get("changes_history",())
                                  if now.timestamp()-30*86400<=r["at"]<=now.timestamp()],
                model_drift={str(days):learning.drift_totals(checkpoint.get("changes_history",()),
                                                             now,days)
                             for days,_ in learning.DRIFT_LIMITS},
                rejection_counts=report.get("rejection_counts",{}),
                current_reasons=report.get("current_reasons",[]),
                analysis_status=report.get("analysis_status","pending"),
                analysis_blockers=report.get("analysis_blockers",[]),
                parameter_support=report.get("parameter_support",{}),
                solar_learning=report.get("solar") or {},
                solar_prospective=report.get("solar_prospective") or {},
                solar_weather=report.get("solar_weather") or {},
                solar_risk_active={k:v for k,v in self.active_solar_risk_certificate().items()
                    if k in ("coefficient","stored_at","holdout_days","holdout_windows","comfort_credit",
                             "nominal_at","nominal_revoked_at","nominal_frozen_until","nominal_rollback_reason")},
                candidate_holdout_mae_1h=metrics.get("1",{}).get("new",{}).get("mae"),
                candidate_holdout_mae_3h=metrics.get("3",{}).get("new",{}).get("mae"),
                active_version=self.model.get("version"),rollback_reason=checkpoint.get("rollback_reason"),
                next_learning=upcoming.isoformat())
        if entity == "sensor.heating_controller_quality":
            attributes["advisory_validation"]=self.advisory_validation_view()
        if entity == "sensor.heating_diagnostics":
            now = time.time()
            observation = getattr(self, "power_observation", {})
            last_tick = self.memory.get("last_successful_tick")
            try:
                last_tick = datetime.fromisoformat(last_tick).timestamp() if isinstance(last_tick, str) else float(last_tick or 0)
            except (TypeError, ValueError):
                last_tick = 0
            attributes["reliability"] = dict(
                computation_alive=0 <= now-last_tick < 45,
                equipment=getattr(self, "equipment_status", {"ready": False, "reason": "not_verified"}),
                command_authorized=bool(self._writer and self.memory.get("native_manual_signature")
                    and self.raw("armed") == "on" and self.raw("mode") == "Автоматичне керування"
                    and not (self.command_ledger_status or {}).get("fenced", True)),
                physical_heating_confirmed=bool(observation.get("quality") == "measured"
                    and 0 <= now-observation.get("at", 0) <= 180 and
                    (observation.get("watts") or 0) >= 500),
                ledger_recovery=self.memory.get("ledger_recovery"))
        if entity in ("sensor.heating_heartbeat", "sensor.heating_ui", "sensor.heating_controller"):
            attributes["runtime"] = "native_v1"
        if entity == "sensor.heating_controller" and attributes.get("control_state") == "fallback":
            comfort = self.memory.get("fallback_comfort", {})
            attributes["comfort_available"] = comfort.get("available", False)
            if comfort.get("available") is False:
                attributes["reason"] = "Резервний auto не запитує нагрів через різницю показів датчиків"
        hold = getattr(self, "memory", {}).get("voltage_hold")
        if entity == "sensor.heating_controller" and hold:
            state = "Захист напруги"
            attributes.update(reason=hold, control_method="voltage_safety",
                              proposed_action="Нагрів заблоковано", voltage_guard=self.voltage_status)
            attributes["execution_state"] = "blocked_voltage"
        return super().publish(entity, state, **attributes)


class ProductionRuntime:
    def __init__(self, controller, lease):
        self.controller = controller
        self.lease = lease
        self.unsubscribers = []
        self._close_lock = asyncio.Lock()
        self._closed = False

    async def close(self):
        async with self._close_lock:
            if self._closed:
                return
            self._closed = True
            for unsubscribe in tuple(self.unsubscribers):
                unsubscribe()
            self.unsubscribers.clear()
            try:
                await self.controller.async_close()
            finally:
                close_archive_lease=getattr(self.controller,"close_archive_lease",None)
                if close_archive_lease is None:
                    self.lease.close()
                else:
                    close_archive_lease(self.lease)


async def async_setup_production(hass, entry):
    if not re.fullmatch(r"[a-f0-9]{32}", str(entry.data.get("installation_id", ""))):
        raise ValueError("Invalid private installation identity")
    root = Path(hass.config.path("heating_ai_v1", entry.data["installation_id"]))
    cfg, report = await hass.async_add_executor_job(load_installation, root)
    if source_writer_active(hass):
        raise ConfigEntryNotReady("Previous V1 still publishes its heartbeat; stop its app before handoff")
    def acquire():
        lease = (root / "writer.lock").open("a")
        try:
            fcntl.flock(lease, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BaseException:
            lease.close()
            raise
        return lease
    lease = await hass.async_add_executor_job(acquire)
    cfg.update(data_dir=str(root / "data"), ui_www_dir=hass.config.path("www", "heating-ai"),
               ui_url="/heating-ai-v1", ui_enabled=True,
               grid_presence_helper=entry.data.get("grid_presence_helper", SOURCE_HINTS["grid"]),
               voltage_entities=entry.data.get("voltage_entities", [SOURCE_HINTS[f"voltage_l{i}"] for i in (1,2,3)]))
    cfg.update({key: entry.options.get(key, default) for key, default in (
        ("min_voltage_v", 190), ("max_voltage_v", 260), ("voltage_recovery_seconds", 30))})
    # Keep the confirmation bound to the exact entity selected in native
    # options. Empty or legacy options do not infer any supply topology.
    cfg["grid_only_ventilation_entities"] = entry.options.get("grid_only_ventilation_entities", [])
    cfg["boiler_outage_isolation_relay"] = entry.options.get("boiler_outage_isolation_relay","")
    missing = [cfg[key] for key in ("thermostat", "room", "return", "mode", "armed")
               if hass.states.get(cfg[key]) is None]
    if missing:
        lease.close()
        raise ConfigEntryNotReady("Required original V1 entities are missing")
    def open_command_ledger():
        directory = root / "command-journal"
        directory.mkdir(mode=0o700, exist_ok=True)
        boot_id = uuid4().hex
        if (directory / FILENAME).exists():
            ledger = CriticalLedger(directory)
        else:
            ledger = CriticalLedger(directory, owner_id=entry.data["installation_id"],
                                    boot_id="bootstrap-"+boot_id, owner_epoch=0)
        status = ledger.claim_boot(boot_id, expected_owner_id=entry.data["installation_id"])
        return ledger, status
    try:
        ledger, ledger_status = await hass.async_add_executor_job(open_command_ledger)
    except BaseException:
        lease.close()
        raise
    controller = NativeController(hass, cfg)
    controller.attach_command_ledger(ledger, ledger_status)
    runtime = ProductionRuntime(controller, lease)

    async def stop_runtime(_event):
        await runtime.close()

    runtime.unsubscribers.append(
        hass.bus.async_listen(EVENT_HOMEASSISTANT_STOP, stop_runtime)
    )
    try:
        assets = Path(__file__).parent / "assets"
        state = hass.data.setdefault(DOMAIN, {})
        if not state.get("_api"):
            from .native_api import register as register_native_api
            register_native_api(hass)
            state["_api"] = True
        if not state.get("production_assets"):
            await hass.http.async_register_static_paths([
                StaticPathConfig("/heating_ai_legacy/full-assets", str(assets), cache_headers=True)])
            state["production_assets"] = True
        bundle = await hass.async_add_executor_job(lambda: sha256((assets / "full-panel.js").read_bytes()).hexdigest()[:16])
        await panel_custom.async_register_panel(hass, frontend_url_path="heating-ai-v1",
            webcomponent_name=f"heating-ai-v1-{bundle}", sidebar_title="Опалення",
            sidebar_icon="mdi:home-thermometer", require_admin=True,
            module_url=f"/heating_ai_legacy/full-assets/full-panel-loader.js?v={bundle}",
            config={"entry_id": entry.entry_id})
        runtime.unsubscribers.append(lambda: frontend.async_remove_panel(hass, "heating-ai-v1", warn_if_unknown=False))
        @callback
        def conflict(event):
            if (event.data.get("entity_id") == "sensor.heating_heartbeat"
                    and event.data.get("new_state") is not None
                    and event.data["new_state"].context.id != controller._context.id):
                controller._writer = False
                controller._running = False
                hass.states.async_set("sensor.heating_native_owner", "Конфлікт контролерів", {
                    "reason": "Попередній контролер знову активний; нові команди заблоковано"})
                hass.async_create_task(runtime.close(), "Stop conflicting Heating AI writer")
        runtime.unsubscribers.append(hass.bus.async_listen("state_changed", conflict))
        await controller.async_start(writer=True)
        state[entry.entry_id] = runtime
        weather = hass.data.get(WEATHER_ENTITIES)
        provider = weather.get_entity(cfg.get("weather")) if weather is not None else None
        if provider is not None:
            @callback
            def forecast(data):
                if isinstance(data, dict) and isinstance(data.get("forecast"), list):
                    controller._launch(controller._dispatch(controller.weather_event, "heating_weather_forecast", data, {}))
            runtime.unsubscribers.append(provider.async_subscribe_forecast("hourly", forecast))
            try:
                await asyncio.wait_for(provider.async_update_listeners({"hourly"}), 20)
            except (TimeoutError, ValueError, RuntimeError):
                controller.log("Hourly weather refresh unavailable; preserved V1 cache remains subject to its freshness limits", level="WARNING")
        entry.runtime_data = runtime
        entry.async_on_unload(entry.add_update_listener(_reload))
        return True
    except BaseException:
        hass.data.get(DOMAIN, {}).pop(entry.entry_id, None)
        await runtime.close()
        raise


async def _reload(hass, entry):
    await hass.config_entries.async_reload(entry.entry_id)
