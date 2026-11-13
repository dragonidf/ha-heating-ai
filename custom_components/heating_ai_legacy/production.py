"""Complete V1 runtime: original algorithms, native HA I/O and voltage stop."""
from __future__ import annotations

import asyncio
import fcntl
from math import isfinite
from hashlib import sha256
from pathlib import Path
import time
import re
from uuid import uuid4

from homeassistant.components import frontend, panel_custom
from homeassistant.components.http.server import StaticPathConfig
from homeassistant.components.weather.const import DATA_COMPONENT as WEATHER_ENTITIES
from homeassistant.const import EVENT_HOMEASSISTANT_STOP
from homeassistant.core import callback
from homeassistant.exceptions import ConfigEntryNotReady

from .native_controller.heating_climate import HeatingClimate
from .native_controller.heating_ui import system_fields
from .native_controller.heating_recovery import wire_values
from .native_voltage import VoltageGuard
from .production_migration import load_installation
from .const import DOMAIN, SOURCE_HINTS
from .critical_ledger import CriticalLedger, FILENAME


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
        self._command_sequence = 0
        self._pending_command_evidence = {}\n        self._fence_recovery_attempted = False

    def attach_command_ledger(self, ledger, status):
        self.command_ledger = ledger
        self.command_ledger_status = status

    def update_in_progress(self):
        # Marketplace unload/reload revokes this bridge before replacing code.
        return False

    def tick(self, kwargs=None, **extra):
        """Retry one fenced STOP from the existing 15-second control tick.

        This is deliberately a one-shot per continuous fence: it adds no
        polling loop and cannot emit repeated STOP commands while a service or
        echo is unavailable.
        """
        try:
            self._reconcile_writer_fence()
        except Exception as exc:
            self.log("Ledger fence recovery deferred: %s", str(exc), level="WARNING")
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
        self._reconcile_writer_fence()

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

    def record_equipment_command_echo(self, evidence, observed):
        if not observed or not observed.get("matched"):
            return False
        revision = self.command_ledger.record_ha_echo(
            evidence["command_id"],
            at=max(observed["observed_at"], evidence["sent_at"] + 1e-6),
            **self._ledger_identity())
        self.command_ledger_status["revision"] = revision
        self.memory["native_command_evidence"].update(
            stage="HA_ECHO", ha_echo_at=observed["observed_at"])
        if evidence["action"] == "STOP" and evidence["domain"] == "climate":
            revision = self.command_ledger.complete_safe_stop(
                evidence["command_id"], **self._ledger_identity())
            self.command_ledger_status.update(revision=revision, fenced=False)
        self._pending_command_evidence.pop(evidence["command_id"], None)
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
        if not self.command_ledger_status.get("fenced"):
            self._fence_recovery_attempted = False
            return
        if (self.raw("mode") != "Автоматичне керування"
                or self._fence_recovery_attempted):
            return
        self._fence_recovery_attempted = True
        manual = self._remember_manual_snapshot()
        self._dispatch_manual_signature = manual["signature"]
        self._dispatch_manual_epoch = self.memory["native_manual_epoch"]
        self._dispatch_action = "off"
        try:
            self.call_service("climate/set_hvac_mode",
                              entity_id=self.cfg["thermostat"], hvac_mode="off")
        finally:
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
        if domain != "climate" or (service == "set_hvac_mode" and data.get("hvac_mode") == "off"):
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
        if (service == "set_hvac_mode" and data.get("hvac_mode") == "heat"
                and getattr(self, "_dispatch_action", None) not in ("off", "observe", "release")):
            positive = True
        if not positive:
            return True  # Original V1 release lowers the target in HEAT mode.
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
        if entity in ("sensor.heating_heartbeat", "sensor.heating_ui", "sensor.heating_controller"):
            attributes["runtime"] = "native_v1"
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
