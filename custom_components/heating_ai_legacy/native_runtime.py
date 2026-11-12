"""Non-actuating V1 thermal-model parity observer.

It executes ONLY an unchanged pure V1 math function in the HA executor,
never calls a service, starts an AppDaemon app, or modifies any helper.
This compatibility adapter is not the final V1+ planner or command gateway.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
import asyncio
import math
import time
from typing import Any

from homeassistant.core import HomeAssistant
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed

from .const import MODEL, ENVIRONMENT, FORECAST
from .native_engine.heating_model import coast
from .plan_snapshot import mirror_legacy_plan
from .native_sources import normalize_hourly_weather, planner_snapshot
from .native_planner import provisional_plan
from .native_intent import evaluate as evaluate_legacy_intent
from .native_outages import read_calendar
from .native_safety import observe as observe_safety
from .native_persistence import observe_room
from .native_model import remember as save_learned_model
from .native_floor import advance as advance_floor, remember as save_floor
from .native_fan_sources import snapshot as fan_snapshot

REQUIRED_COEFFICIENTS = (
    "floor_air", "room_loss", "floor_decay", "heat_gain", "fire_gain"
)


def _number(value: Any, low: float, high: float) -> float:
    if isinstance(value, bool):
        raise ValueError("boolean is not a temperature or model coefficient")
    try:
        result = float(value)
    except (TypeError, ValueError, OverflowError):
        raise ValueError("missing or invalid measurement") from None
    if not math.isfinite(result) or not low <= result <= high:
        raise ValueError("measurement outside bounded observational range")
    return result


def _age(state: Any, now: datetime, limit: int) -> None:
    if state is None or state.state in ("unknown", "unavailable", ""):
        raise ValueError("required HA entity unavailable")
    last = getattr(state, "last_reported", None) or getattr(state, "last_updated", None)
    if not isinstance(last, datetime) or last.tzinfo is None:
        raise ValueError("missing independent HA measurement timestamp")
    seconds = (now - last).total_seconds()
    if seconds < -60 or seconds > limit:
        raise ValueError("required HA observation is stale")


def sample_v1_inputs(
    hass: HomeAssistant, mapping: dict[str,str],
    *, model_checkpoint: dict | None = None,
    floor_observer: dict | None = None,
) -> dict[str, Any]:
    """Require fresh *physical* sensors, accept checksummed calibration cache.

    This is SHADOW-only. An untrusted model or missing measured floor/KWS
    never becomes permission to actuate equipment.
    """
    now=datetime.now(timezone.utc)
    room_state=hass.states.get(mapping["room"])
    outside_state=hass.states.get(mapping["outside"])
    _age(room_state,now,300)
    _age(outside_state,now,300)
    room=_number(room_state.state,-20,55)
    outside=_number(outside_state.state,-55,60)
    model_row=hass.states.get(MODEL)
    observed=(model_row.attributes.get("parameters")
              if model_row is not None and
              model_row.state not in ("unknown","unavailable","")
              else None)
    source_kind="legacy_model"
    if not isinstance(observed,dict) or (
        observed.get("validated") not in (True,"true","True")
        or type(observed.get("version")) is not int
    ):
        if not isinstance(model_checkpoint,dict):
            raise ValueError("Neither legacy nor validated cached model")
        observed=model_checkpoint.get("model")
        model_at=model_checkpoint.get("observed_at")
        if not isinstance(observed,dict):
            raise ValueError("Cached thermal model is not trustworthy")
        if type(model_at) not in (int,float) or not 0<model_at<=now.timestamp()+60:
            raise ValueError("Cached model observation clock invalid")
        source_kind="private_model_checkpoint"
    else:
        model_at=getattr(model_row,"last_reported",None) or getattr(model_row,"last_updated",None)
        if (not isinstance(model_at,datetime) or model_at.tzinfo is None or
                model_at>now+timedelta(seconds=60)):
            raise ValueError("Legacy learned model has invalid provenance")
        model_at=model_at.timestamp()
    if (observed.get("validated") not in (True,"true","True")
            or type(observed.get("version")) is not int or
            observed["version"]<0):
        raise ValueError("Thermal model invalid")
    parameters={key:_number(observed.get(key),-10,10)
                for key in REQUIRED_COEFFICIENTS}
    floor_row=hass.states.get(ENVIRONMENT)
    floor_source="legacy_floor"
    try:
        _age(floor_row,now,900)
        floor=_number(floor_row.state,0,90)
    except ValueError:
        if not isinstance(floor_observer,dict):
            raise ValueError("No recent native floor inventory")
        at=floor_observer.get("at")
        if (type(at) not in (float,int) or
                not 0<=now.timestamp()-at<=1800):
            raise ValueError("Native floor inventory is stale")
        floor=_number(floor_observer.get("estimate"),0,90)
        floor_source="native_floor_inventory"
    source_plan=hass.states.get(FORECAST)
    plan_attributes=None
    plan_id=None
    if source_plan and source_plan.state not in ("unknown","unavailable"):
        fields=("created_at","curve","actual_curve","heat_segments",
                "planned_kwh","plan_id","execution_state")
        plan_attributes={key:source_plan.attributes.get(key) for key in fields}
        plan_id=source_plan.attributes.get("plan_id")
    return dict(
        at=now.isoformat(),room=room,outside=outside,floor=floor,
        parameters=parameters,plan_id=plan_id,
        model_provenance=source_kind,floor_provenance=floor_source,
        legacy_plan=mirror_legacy_plan(plan_attributes,now.timestamp()),
        learned_model_source=dict(observed),learned_model_at=model_at,
    )


def calculate_v1_coast(snapshot: dict[str, Any]) -> dict[str, Any]:
    """Pure numerical V1 model: no HA object, network or equipment access."""
    maximum, final = coast(
        snapshot["room"], snapshot["floor"], snapshot["outside"],
        0.0, dict(snapshot["parameters"]), hours=3,
    )
    return {
        "mode": "COMPATIBILITY_SHADOW",
        "physical_commands_enabled": False,
        "forecast_qualified": False,
        "source": "original_v1_thermal_model",
        "scenario": "three_hour_coast_without_heating_or_fire",
        "room_c": snapshot["room"],
        "estimated_floor_c": snapshot["floor"],
        "outside_c": snapshot["outside"],
        "three_hour_peak_c": maximum,
        "three_hour_end_c": final,
        "source_plan_id": snapshot["plan_id"],
        "legacy_plan": snapshot["legacy_plan"],
        "computed_at": snapshot["at"],
    }


class V1ShadowCoordinator(DataUpdateCoordinator[dict[str, Any]]):
    """One small bounded background calculation, at most once per minute."""

    def __init__(self, hass: HomeAssistant, mapping: dict[str, str], entry: Any) -> None:
        super().__init__(
            hass, logger=__import__("logging").getLogger(__name__),
            name="Heating AI V1 pure-model observer",
            update_interval=timedelta(seconds=60),
            config_entry=entry,
        )
        self.mapping = dict(mapping)
        self.role_map = dict(entry.data.get("entity_map", {}))
        self.role_map.update(self.mapping)
        self.source_ids = dict(entry.data.get("source_ids", {}))
        self.model_store = None
        self.model_checkpoint = None
        self._floor_state = None
        self._fan_state = None
        self._storage_directory = None
        self.durable_ledger = None
        self.ledger_summary = None
        self._last_room_archive = 0.0
        self._room_archive_job = None
        self.hourly_weather: list[dict[str, float]] | None = None
        self.weather_received_at: float | None = None
        self.native_plan: dict[str, Any] | None = None
        self._plan_job: asyncio.Task[None] | None = None
        self._last_plan_start = 0.0
        self._requested_snapshot: tuple[Any, ...] | None = None
        self._closed = False
        self._weather_unsubscribe = None
        self.outage_schedule: dict[str, Any] | None = None
        self._calendar_job: asyncio.Task[None] | None = None
        self._last_calendar_request = 0.0

    def attach_private_state(self, store, ledger, status: dict,
                             directory=None, checkpoint=None,
                             floor_observer=None) -> None:
        """Attach state only after checksum and reboot-fence verification."""
        if status.get("fenced") is not True:
            raise ValueError("Refusing unfenced native command journal")
        self.model_store = store
        self._storage_directory = directory
        self.model_checkpoint = checkpoint
        self._floor_state = floor_observer
        self.durable_ledger = ledger
        self.ledger_summary = {
            "revision": status.get("revision"),
            "owner_epoch": status.get("owner_epoch"),
            "fenced": True,
            "physical_commands_enabled": False,
        }

    async def _remember_read_only(self, at: float, room: float,
                                  model: dict, model_at: float,
                                  kws_total_kwh=None, kws_power_w=None,
                                  floor_state=None) -> None:
        try:
            # Execute the one explicitly qualified KWS transaction off-loop.
            await self.hass.async_add_executor_job(
                lambda: observe_room(
                    self.model_store, at, room,
                    counter_kwh=kws_total_kwh, boiler_power_w=kws_power_w,
                )
            )
            if self._storage_directory is not None:
                self.model_checkpoint = await self.hass.async_add_executor_job(
                    save_learned_model, self._storage_directory, model, model_at
                )
                if floor_state is not None:
                    await self.hass.async_add_executor_job(
                        save_floor, self._storage_directory, floor_state
                    )
        except Exception:
            # Archive failure must be visible; never repair or overwrite an
            # unknown/corrupted file automatically.
            self._storage_fault = "read_only_archive_failed"

    def receive_weather(self, items: list[dict[str, Any]] | None) -> None:
        """Read-only HA WeatherEntity callback: receipt is not issue time."""
        now = time.time()
        try:
            self.hourly_weather = normalize_hourly_weather(items, now)
            self.weather_received_at = now
        except (TypeError, ValueError, OverflowError):
            self.hourly_weather = None
            self.weather_received_at = None

    def _planning_key(self, snapshot: dict[str, Any]) -> tuple[Any, ...]:
        values = snapshot["settings"]
        return (
            tuple(sorted(snapshot["model"].items())),
            round(snapshot["room"] * 4) / 4,
            round(snapshot["floor"] * 2) / 2,
            round(snapshot["outside"] * 2) / 2,
            round(snapshot["fire_delta"] * 2) / 2,
            tuple((role, str(values[role])) for role in (
                "target", "lower", "upper", "night_target", "night_lower",
                "night_upper", "start", "end", "sleep_start", "sleep_end",
                "strategy",
            )),
            snapshot["weather"][-1]["ts"],
            str(values["outage_schedule"].get("outages", [])),
        )

    async def _run_plan(
        self, snapshot: dict[str, Any], signature: tuple[Any, ...]
    ) -> None:
        """A single isolated executor job; reject results for old input keys."""
        try:
            result = await self.hass.async_add_executor_job(
                provisional_plan, snapshot
            )
        except Exception:
            result = dict(status="BLOCKED_INPUTS", reason="calculation_failed",
                          physical_commands_enabled=False,
                          commands_sent=0, proposal_is_command=False)
        if not self._closed and signature == self._requested_snapshot:
            self.native_plan = result
            if isinstance(self.data, dict):
                # Only a compact summary is recorded in HA. The full curve
                # remains in RAM, until a dedicated authenticated API exists.
                old = dict(self.data)
                old["native_plan_status"] = result.get("status", "BLOCKED_INPUTS")
                old["native_plan_quality"] = result.get("quality")
                old["native_plan_reason"] = result.get("reason")
                old["native_planned_kwh"] = result.get("nominal_energy_kwh")
                old["native_plan_points"] = len(result.get("forecast", []))
                self.async_set_updated_data(old)

    async def _refresh_calendar(self) -> None:
        try:
            schedule = await read_calendar(
                self.hass, self.source_ids.get("outage_calendar")
            )
        except asyncio.CancelledError:
            raise
        except Exception:
            schedule = None
        if not self._closed:
            self.outage_schedule = schedule

    async def close(self) -> None:
        """Release native weather subscription and the one planning task."""
        self._closed = True
        if self._weather_unsubscribe is not None:
            self._weather_unsubscribe()
            self._weather_unsubscribe = None
        if self._plan_job is not None and not self._plan_job.done():
            self._plan_job.cancel()
            try:
                await self._plan_job
            except asyncio.CancelledError:
                pass
        if self._calendar_job is not None and not self._calendar_job.done():
            self._calendar_job.cancel()
            try:
                await self._calendar_job
            except asyncio.CancelledError:
                pass
        if self._room_archive_job is not None and not self._room_archive_job.done():
            # A background SQLite write may finish after task cancellation;
            # wait for its result rather than starting a second writer.
            await self._room_archive_job

    async def _async_update_data(self) -> dict[str, Any]:
        """Refresh read-only safety before slow math, never from executor."""
        # Config flow preserved V1 role bindings before AppDaemon retirement.
        # Only old SHADOW entries use live discovery as a compatibility fallback.
        role_map = dict(self.role_map)
        if "return" not in role_map:
            ui = self.hass.states.get("sensor.heating_ui")
            source_roles = ui.attributes.get("entities", {}) if ui is not None else {}
            if isinstance(source_roles, dict):
                role_map.update(source_roles)
            role_map.update(self.mapping)
        try:
            safety = observe_safety(self.hass, role_map, self.source_ids)
        except Exception:
            safety = dict(
                mode="NATIVE_SHADOW", physical_commands_enabled=False,
                physical_authority=False, source_quality="UNAVAILABLE",
                source_warnings=["source_monitor_failed"],
                grid=None, kws_power_w=None, kws_total_kwh=None,
            )
        try:
            self._fan_state, fan_result = fan_snapshot(
                self.hass, role_map, self.source_ids,
                safety, self._fan_state, time.time()
            )
        except Exception:
            self._fan_state = None
            fan_result = dict(mode="SHADOW_FAN",reason="source_unqualified",
                              physical_commands_enabled=False,commands_sent=0)
        clock = time.monotonic()
        if (self._calendar_job is None or self._calendar_job.done()) and (
            clock - self._last_calendar_request >= 600
        ):
            self._last_calendar_request = clock
            self._calendar_job = self.hass.async_create_task(
                self._refresh_calendar(),
                "heating_ai_v1_read_svitlo_calendar",
            )
        safety_status = {
            "mode": "NATIVE_SHADOW", "physical_commands_enabled": False,
            "source_quality": safety.get("source_quality"),
            "source_warnings": safety.get("source_warnings", []),
            "manual_observed": safety.get("manual_observed"),
            "grid_actual": safety.get("grid"),
            "room_c": safety.get("room_c"),
            "return_c": safety.get("return_c"),
            "armed": safety.get("armed"),
            "kws_power_w": safety.get("kws_power_w"),
            "kws_total_kwh": safety.get("kws_total_kwh"),
            "legacy_mode": safety.get("legacy_mode"),
            "thermostat_mode": safety.get("thermostat_mode"),
            "fan_state": safety.get("fan_state"),
            "fireplace_state": safety.get("fireplace_state"),
            "fan_preview": fan_result,
            "durable_journal": self.ledger_summary,
            "storage_fault": getattr(self, "_storage_fault", None),
            "cached_model_version": (
                (self.model_checkpoint or {}).get("model", {}).get("version")
            ),
        }
        try:
            snapshot = sample_v1_inputs(
                self.hass, self.mapping,
                model_checkpoint=self.model_checkpoint,
                floor_observer=self._floor_state,
            )
        except (ValueError, KeyError):
            # The UI still explains why independent sources are missing.
            # Unqualified old readings never become current measurements.
            return dict(
                safety_status,
                native_plan_status="BLOCKED_INPUTS",
                native_plan_reason="model_or_temperature_stale",
                forecast_qualified=False,
                source="original_v1_thermal_model",
            )
        # Continue the original V1 slab observer in parallel using genuine
        # KWS readings. Never invent boiler watts when KWS is unavailable.
        if (safety.get("return_c") is not None
            and safety.get("kws_power_w") is not None
            and type(safety.get("grid")) is bool):
            try:
                self._floor_state = advance_floor(
                    self._floor_state, time.time(),
                    snapshot["room"], safety["return_c"],
                    safety["kws_power_w"], safety["grid"],
                    snapshot["parameters"], safety.get("kws_total_kwh"),
                    legacy_estimate=(
                        snapshot["floor"]
                        if self._floor_state is None and
                        snapshot["floor_provenance"] == "legacy_floor"
                        else None
                    ),
                )
                if snapshot["floor_provenance"] == "native_floor_inventory":
                    snapshot["floor"] = self._floor_state["estimate"]
            except ValueError:
                self._storage_fault = "native_floor_inputs_unqualified"
        report = await self.hass.async_add_executor_job(calculate_v1_coast, snapshot)
        report.update(safety_status)
        report["model_provenance"] = snapshot["model_provenance"]
        report["floor_provenance"] = snapshot["floor_provenance"]
        report["native_floor_c"] = (
            self._floor_state.get("estimate") if self._floor_state else None
        )
        intent = evaluate_legacy_intent(
            time.time(), self.native_plan, safety_status,
            model_validated=True,  # sample_v1_inputs validated the active source
        )
        report["legacy_decision_preview"] = intent.get("proposed_action")
        report["legacy_decision_preview_status"] = intent.get("status")
        report["legacy_decision_never_executable"] = True

        if self.model_store is not None:
            stamp = time.time()
            if (stamp - self._last_room_archive >= 900 and
                    (self._room_archive_job is None or self._room_archive_job.done())):
                self._last_room_archive = stamp
                self._room_archive_job = self.hass.async_create_task(
                    self._remember_read_only(
                        stamp, snapshot["room"],
                        snapshot["learned_model_source"], snapshot["learned_model_at"],
                        safety.get("kws_total_kwh"),
                        safety.get("kws_power_w"),
                        dict(self._floor_state) if self._floor_state else None,
                    ),
                    "heating_ai_v1_record_ha_cache_only",
                )

        try:
            candidate = planner_snapshot(
                self.hass, snapshot, self.hourly_weather, self.weather_received_at,
                role_map=role_map,
                calendar_schedule=self.outage_schedule,
                fireplace_entity=self.source_ids.get("fireplace_temp"),
            )
        except (ValueError, KeyError, TypeError):
            report["native_plan_status"] = "BLOCKED_INPUTS"
            report["native_plan_reason"] = "weather_or_source_missing"
            return report
        signature = self._planning_key(candidate)
        stamp = time.monotonic()
        running = self._plan_job is not None and not self._plan_job.done()
        if not running and (
            signature != self._requested_snapshot or
            stamp - self._last_plan_start >= 600
        ):
            self._last_plan_start = stamp
            self._requested_snapshot = signature
            self._plan_job = self.hass.async_create_task(
                self._run_plan(candidate, signature),
                "heating_ai_v1_24h_non_actuating_shadow",
            )
        plan = self.native_plan or {}
        report["native_plan_status"] = (
            plan.get("status", "PLANNING")
            if signature == self._requested_snapshot else "PLANNING"
        )
        report["native_plan_quality"] = plan.get("quality")
        report["native_plan_reason"] = plan.get("reason")
        report["native_planned_kwh"] = plan.get("nominal_energy_kwh")
        report["native_plan_points"] = len(plan.get("forecast", []))
        return report
