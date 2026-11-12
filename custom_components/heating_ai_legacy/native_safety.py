"""Fail-closed independent quality and owner monitoring for native V1+.

Checks do not write HA state, execute devices, or grant a permission token.
The old AppDaemon controller remains the ONLY live writer in SHADOW.
"""
from __future__ import annotations

from datetime import datetime, timezone
from math import isfinite
from typing import Any

MAX_ROOM_AGE = 180
MAX_RETURN_AGE = 1200
MAX_GRID_AGE = 120
MAX_KWS_AGE = 180
MAX_MAINS_VOLTAGE_AGE = 120
VOLTAGE_ROLES = ("voltage_l1","voltage_l2","voltage_l3")
GRID_STATES = {"Наявна мережа": True, "Відсутня мережа": False}
V1_MANUAL_MODES = {"Штатний auto", "Спостереження", "Вимкнено"}

KWS_ROLES = ("kws_l1", "kws_l2", "kws_l3", "kws_energy")


def _read(hass: Any, entity: str | None, now: float, age_limit: int) -> dict:
    result = dict(entity_id=entity, valid=False, value=None, age_seconds=None)
    if not entity or not isinstance(entity, str):
        return dict(result, reason="not_configured")
    state = hass.states.get(entity)
    if state is None or state.state in ("unknown", "unavailable", ""):
        return dict(result, reason="not_available")
    updated = getattr(state, "last_reported", None) or getattr(state, "last_updated", None)
    if not isinstance(updated, datetime) or updated.tzinfo is None:
        return dict(result, reason="timestamp_missing")
    age = now - updated.timestamp()
    result["age_seconds"] = round(age, 1)
    if age < -30 or age > age_limit:
        return dict(result, reason="stale")
    return dict(result, valid=True, value=state.state, reason="ha_cache_fresh")


def _numerical(row: dict, low: float, high: float) -> float | None:
    if not row["valid"] or isinstance(row["value"], bool):
        return None
    try:
        value = float(row["value"])
    except (ValueError, TypeError, OverflowError):
        return None
    return value if isfinite(value) and low <= value <= high else None


def observe(hass: Any, mapping: dict[str, str], options: dict[str, str],
            now: float | None = None) -> dict[str, Any]:
    """Small trusted observational snapshot; HA cache != raw device ACK."""
    if now is None:
        now = datetime.now(timezone.utc).timestamp()
    if not isfinite(now) or now <= 0:
        raise ValueError("invalid evaluation time")
    room = _read(hass, mapping.get("room"), now, MAX_ROOM_AGE)
    probe = _read(hass, mapping.get("return"), now, MAX_RETURN_AGE)
    outside = _read(hass, mapping.get("outside"), now, 900)
    grid = _read(hass, options.get("grid"), now, MAX_GRID_AGE)
    mode = _read(hass, mapping.get("mode"), now, 86400)
    armed = _read(hass, mapping.get("armed"), now, 86400)
    thermostat = _read(hass, options.get("thermostat"), now, 240)
    legacy = _read(hass, "sensor.heating_controller", now, 240)
    fireplace = _read(hass, options.get("fireplace"), now, 900)
    fan = _read(hass, mapping.get("fan"), now, 300)
    kw = [_read(hass, options.get(k), now, MAX_KWS_AGE) for k in KWS_ROLES[:3]]
    energy = _read(hass, options.get("kws_energy"), now, 600)
    voltages = [
        _read(hass, options.get(key), now, MAX_MAINS_VOLTAGE_AGE)
        for key in VOLTAGE_ROLES
    ]
    reasons: list[str] = []
    room_c = _numerical(room, -20, 55)
    return_c = _numerical(probe, 0, 90)
    outside_c = _numerical(outside, -55, 60)
    phase_w = [_numerical(value, 0, 15000) for value in kw]
    energy_kwh = _numerical(energy, 0, 1e9)
    phases_v = [_numerical(value, 0, 350) for value in voltages]
    actual_grid = GRID_STATES.get(grid["value"]) if grid["valid"] else None
    physical_mode = str(mode["value"]) if mode["valid"] else None
    manual = physical_mode in V1_MANUAL_MODES
    if not mode["valid"] or physical_mode not in (
        "Автоматичне керування", *V1_MANUAL_MODES
    ):
        reasons.append("controller_mode_unqualified")
    if manual:
        reasons.append("explicit_manual_mode")
    if armed["value"] != "on" or not armed["valid"]:
        reasons.append("automatic_not_armed")
    if room_c is None:
        reasons.append("room_not_fresh")
    if return_c is None:
        reasons.append("return_not_fresh")
    if outside_c is None:
        reasons.append("outside_not_fresh")
    if actual_grid is not True:
        reasons.append("grid_off_or_unknown")
    if not thermostat["valid"]:
        reasons.append("thermostat_not_fresh")
    if any(value is None for value in phase_w):
        reasons.append("kws_phase_missing_or_stale")
    if energy_kwh is None:
        reasons.append("kws_counter_missing_or_stale")
    if any(value is None for value in phases_v):
        reasons.append("mains_voltage_missing_or_stale")
    if legacy["valid"]:
        reasons.append("legacy_controller_active")
    else:
        reasons.append("exclusive_old_owner_handoff_not_verified")
    reasons.append("independent_source_provenance_not_commissioned")
    reasons.append("physical_commands_hard_disabled")
    return {
        "mode": "NATIVE_SHADOW",
        "physical_commands_enabled": False,
        "physical_authority": False,
        "owner_verified": False,
        "source_quality": "HA_CACHE_ONLY",
        "manual_observed": manual,
        "legacy_mode": physical_mode,
        "grid": actual_grid,
        "armed": armed["value"] == "on" and armed["valid"],
        "room_c": room_c,
        "return_c": return_c,
        "outside_c": outside_c,
        "kws_power_w": (
            round(sum(phase_w), 2) if all(x is not None for x in phase_w) else None
        ),
        "kws_phases_w": phase_w,
        "kws_total_kwh": energy_kwh,
        "voltage_phases_v": phases_v,
        "voltage_source_quality": (
            "HA_CACHE_FRESH" if all(x is not None for x in phases_v)
            else "UNAVAILABLE_OR_STALE"
        ),
        "thermostat_mode": thermostat["value"] if thermostat["valid"] else None,
        "fireplace_state": fireplace["value"] if fireplace["valid"] else None,
        "fan_state": fan["value"] if fan["valid"] else None,
        "source_warnings": reasons,
    }
