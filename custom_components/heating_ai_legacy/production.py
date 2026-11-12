"""Complete V1 runtime: original algorithms, native HA I/O and voltage stop."""
from __future__ import annotations

import asyncio
import fcntl
from math import isfinite
from hashlib import sha256
from pathlib import Path
import time
import re

from homeassistant.components import frontend, panel_custom
from homeassistant.components.http.server import StaticPathConfig
from homeassistant.components.weather.const import DATA_COMPONENT as WEATHER_ENTITIES
from homeassistant.core import callback
from homeassistant.exceptions import ConfigEntryNotReady

from .native_controller.heating_climate import HeatingClimate
from .native_voltage import VoltageGuard
from .production_migration import load_installation
from .const import DOMAIN, SOURCE_HINTS


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

    def update_in_progress(self):
        # Marketplace unload/reload revokes this bridge before replacing code.
        return False

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
        self.memory["command_generation"] = self.memory.get("command_generation", 0)+1
        self.save()
        for entity in self.args["voltage_entities"]:
            self.listen_state(self.voltage_changed, entity, attribute="all")
        self.listen_state(self.voltage_changed, self.args["grid_presence_helper"])

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
        armed = self.hass.states.get(self.args["armed"])
        mode = self.hass.states.get(self.args["mode"])
        if armed is None or armed.state != "on" or mode is None:
            return False
        if mode.state == "Автоматичне керування":
            return True
        # An explicitly selected native AUTO keeps its established fallback
        # target semantics, but an old model HEAT cannot pass this boundary.
        return (mode.state == "Штатний auto" and
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
        self._dispatch_action = action
        try:
            return super().apply_action(action, once)
        finally:
            self._dispatch_action = None

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

    async def close(self):
        for unsubscribe in self.unsubscribers:
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
    controller = NativeController(hass, cfg)
    runtime = ProductionRuntime(controller, lease)
    try:
        assets = Path(__file__).parent / "assets"
        if not hass.data.setdefault(DOMAIN, {}).get("production_assets"):
            await hass.http.async_register_static_paths([
                StaticPathConfig("/heating_ai_legacy/full-assets", str(assets), cache_headers=True)])
            hass.data[DOMAIN]["production_assets"] = True
        bundle = await hass.async_add_executor_job(lambda: sha256((assets / "full-panel.js").read_bytes()).hexdigest()[:16])
        await panel_custom.async_register_panel(hass, frontend_url_path="heating-ai-v1",
            webcomponent_name=f"heating-ai-v1-{bundle}", sidebar_title="Опалення",
            sidebar_icon="mdi:home-thermometer", require_admin=True,
            module_url=f"/heating_ai_legacy/full-assets/full-panel-loader.js?v={bundle}", config={})
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
        await runtime.close()
        raise


async def _reload(hass, entry):
    await hass.config_entries.async_reload(entry.entry_id)
