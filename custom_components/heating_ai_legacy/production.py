"""Complete V1 runtime: original algorithms, native HA I/O and voltage stop."""
from __future__ import annotations

import asyncio
from datetime import datetime
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

from .native_controller.heating_climate import HeatingClimate
from .native_controller.heating_model import comfort_at
from .native_controller.heating_ui import system_fields
from .native_controller.heating_recovery import wire_values
from .native_voltage import VoltageGuard
from .production_migration import load_installation
from .const import DOMAIN, SOURCE_HINTS
from .critical_ledger import CriticalLedger, LedgerConflict, FILENAME
from .kws_readiness import KWSReadiness


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
        self._command_sequence = 0
        self._pending_command_evidence = {}
        self._echo_lock = threading.Lock()
        self._echoed_command_ids = set()
        self._recovery_attempts = 0
        self._recovery_next_at = 0
        self._fence_recovery_attempted = False

    def attach_command_ledger(self, ledger, status):
        self.command_ledger = ledger
        self.command_ledger_status = status

    def update_in_progress(self):
        # Marketplace unload/reload revokes this bridge before replacing code.
        return False

    def tick(self, kwargs=None, **extra):
        """Recover on the existing tick; never create another polling task."""
        try:
            equipment = self._on_loop(self.async_kws_status)
            signature = equipment["reason"]
            if signature != getattr(self, "_equipment_signature", None):
                journal = getattr(self, "decision_journal", None)
                if journal:
                    journal.append(dict(event="equipment_transition", before=getattr(self, "_equipment_signature", None),
                        after=signature, voltage=self.voltage_status, equipment=equipment), stamp=time.time())
                self._equipment_signature = signature
            self._reconcile_writer_fence()
        except Exception as exc:
            self.record_failure("ledger_recovery", exc)
        return super().tick(kwargs, **extra)

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
        # Preserve all learned data and counters; old in-flight commands cannot
        # confer ownership after a process and platform migration.
        if not self.memory.get("native_runtime_initialized"):
            self.memory["owned"] = False
            for key in ("temporary_fallback", "recovering_from_fallback",
                        "expected_mode", "expected_setpoint", "once"):
                self.memory.pop(key, None)
            self.memory["native_runtime_initialized"] = True
        self._remember_manual_snapshot()
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
        if external and not baseline:
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
        self._remember_manual_snapshot()
        self.next_compute = 0
        self.save()
        self._reconcile_writer_fence()

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
        evidence = dict(command_id=command_id, action=action, domain=domain,
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
                matched = state.last_reported.timestamp() >= evidence.get("sent_at", float("inf"))
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
                for stage in ("ledger_recovery", "ledger_echo", "equipment_stop"):
                    error = (self.memory.get("diagnostic_errors") or {}).get(stage)
                    if error:
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
                       if time.time() - e.get("sent_at", time.time()) >= 30]
            if expired:
                self.command_ledger.fence(**self._ledger_identity())
                self.command_ledger_status = self.command_ledger.status()
                self._pending_command_evidence.clear()
        if not self.command_ledger_status.get("fenced"):
            self._recovery_attempts = 0
            self._recovery_next_at = 0
            return
        now = time.time()
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

    @callback
    def async_voltage_status(self):
        now = time.time()
        presence = self.hass.states.get(self.args["grid_presence_helper"])
        confirmed = presence is not None and presence.state in ("on", "Наявна мережа")
        phases = []
        for entity in self.args["voltage_entities"]:
            state = self.hass.states.get(entity)
            try:
                voltage = float(state.state)
                age = now-state.last_reported.timestamp()
                phases.append(voltage if -5 <= age <= 90 else None)
            except (ValueError, TypeError, AttributeError):
                phases.append(None)
        self.voltage_status = self.voltage_guard.evaluate(
            now, confirmed, phases,
            minimum_v=self.args.get("min_voltage_v", 190),
            maximum_v=self.args.get("max_voltage_v", 260),
            recovery_seconds=self.args.get("voltage_recovery_seconds", 30),
        )
        self.voltage_status.update(physical_commands_enabled=self._writer,
                                   physical_authority=self._writer)
        return self.voltage_status

    @callback
    def async_kws_status(self):
        if self.args.get("power_accounting_method") != "kws":
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
        grid = row(self.args["grid"])
        connection = row(self.args["connection"])
        deye_voltage = row(self.args["grid_voltage"])
        # Binary statuses may legitimately stay unchanged. A live voltage
        # report from this inverter proves link liveness, not their change time.
        deye_fresh = (deye_voltage.get("at") is not None
                      and -5 <= now-deye_voltage["at"] <= 90)
        physical = ("on" if deye_fresh and connection.get("value") == "on"
                    and grid.get("value") == "on" and
                    isinstance(deye_voltage.get("value"), (int, float)) and
                    deye_voltage["value"] >= 150 else
                    "off" if deye_fresh and connection.get("value") == "on" and
                    grid.get("value") == "off" else "unknown")
        rows = {"status": row(self.args.get("kws_status", "sensor.energy_meter_kotel_status")),
                "relay": row(self.args.get("kws_relay", "switch.energy_meter_kotel")),
                "fault": row(self.args.get("kws_fault", "binary_sensor.energy_meter_kotel_problema"))}
        rows.update({p: row("sensor.energy_meter_kotel_napruga_"+p) for p in "abc"})
        self.equipment_status = self.kws_readiness.evaluate(now, physical,
            self.async_voltage_status()["voltage_qualified"], rows,
            reset_delay=self.args.get("kws_reset_delay", 120))
        return self.equipment_status

    @callback
    def async_command_allowed(self, domain, service, data):
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
        if self.args.get("power_accounting_method") == "kws":
            now = time.time()
            for role, attribute, age in (("return", None, self.args.get("return_max_age", 1200)), ("thermostat", "current_temperature", 5400)):
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
                    -5 <= now-room.last_reported.timestamp() <= self.args.get("room_max_age", 14400))
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

    def apply_action(self, action, once):
        if self.apply_voltage_stop():
            return
        equipment = self._on_loop(self.async_kws_status)
        fenced = (self.command_ledger_status or {}).get("fenced", True)
        if action in ("heat", "fallback") and (fenced or not equipment["ready"]):
            reason = "Журнал команд: очікується підтверджене вимкнення" if fenced else equipment["reason"]
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
                self.lease.close()


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
        hass.bus.async_listen_once(EVENT_HOMEASSISTANT_STOP, stop_runtime)
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
