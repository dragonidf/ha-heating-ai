"""Non-actuating V1 thermal-model parity observer.

It executes ONLY an unchanged pure V1 math function in the HA executor,
never calls a service, starts an AppDaemon app, or modifies any helper.
This compatibility adapter is not the final V1+ planner or command gateway.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
import math
from typing import Any

from homeassistant.core import HomeAssistant
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed

from .const import MODEL, ENVIRONMENT, FORECAST
from .native_engine.heating_model import coast
from .plan_snapshot import mirror_legacy_plan

REQUIRED_COEFFICIENTS = (
    "floor_air", "room_loss", "floor_decay", "heat_gain", "fire_gain"
)


def _number(value: Any, low: float, high: float) -> float:
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


def sample_v1_inputs(hass: HomeAssistant, mapping: dict[str, str]) -> dict[str, Any]:
    """Read a single bounded observation on HA's event loop; never call this in executor."""
    now = datetime.now(timezone.utc)
    entity_ids = dict(room=mapping["room"], outside=mapping["outside"],
                      floor=ENVIRONMENT, model=MODEL)
    values: dict[str, float] = {}
    rows: dict[str, Any] = {}
    for role, entity_id in entity_ids.items():
        row = hass.states.get(entity_id)
        _age(row, now, 900 if role in ("model", "floor") else 300)
        rows[role] = row
    values["room"] = _number(rows["room"].state, -20, 55)
    values["outside"] = _number(rows["outside"].state, -55, 60)
    values["floor"] = _number(rows["floor"].state, 0, 90)
    attrs = rows["model"].attributes.get("parameters")
    if not isinstance(attrs, dict):
        raise ValueError("V1 thermal coefficients not available")
    parameters = {
        key: _number(attrs.get(key), -10, 10)
        for key in REQUIRED_COEFFICIENTS
    }
    # The 3h coast is an indicative no-boiler/no-fire calculation, not
    # expected thermal execution. Preserve these limits even in SHADOW.
    source_plan = hass.states.get(FORECAST)
    plan_id = None
    plan_attributes = None
    if source_plan and source_plan.state not in ("unknown", "unavailable"):
        fields = ("created_at", "curve", "actual_curve", "heat_segments",
                  "planned_kwh", "plan_id", "execution_state")
        plan_attributes = {key: source_plan.attributes.get(key) for key in fields}
        plan_id = source_plan.attributes.get("plan_id")
    return dict(at=now.isoformat(), parameters=parameters,
                plan_id=plan_id,
                legacy_plan=mirror_legacy_plan(plan_attributes, now.timestamp()),
                **values)


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

    def __init__(self, hass: HomeAssistant, mapping: dict[str, str]) -> None:
        super().__init__(
            hass, logger=__import__("logging").getLogger(__name__),
            name="Heating AI V1 pure-model observer",
            update_interval=timedelta(seconds=60),
        )
        self.mapping = dict(mapping)

    async def _async_update_data(self) -> dict[str, Any]:
        try:
            # HA states must be read on the event-loop thread only.
            snapshot = sample_v1_inputs(self.hass, self.mapping)
        except (ValueError, KeyError) as err:
            raise UpdateFailed(str(err)) from err
        # Pure math never blocks the HA event loop.
        return await self.hass.async_add_executor_job(
            calculate_v1_coast, snapshot,
        )
