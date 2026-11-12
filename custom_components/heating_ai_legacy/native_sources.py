"""Read-only HA source adapter for an independently solved V1 24h SHADOW.

The original V1 UI publishes entity mappings. Read them as discovered facts,
not hard-coded installation policy. No HA service calls or automation writes.
"""
from __future__ import annotations

from datetime import datetime, timezone
from math import isfinite
from typing import Any

UI = "sensor.heating_ui"
OUTAGES = "sensor.heating_outage_schedule"
FIRE = "sensor.heating_fireplace"
MAX_FORECAST_AGE = 3600

HELPERS = {
    "target": "target", "lower": "lower", "upper": "upper",
    "night_target": "night_target", "night_lower": "night_lower",
    "night_upper": "night_upper", "start": "start", "end": "end",
    "sleep_start": "sleep_start", "sleep_end": "sleep_end",
    "strategy": "strategy",
}
NUMERIC_HELPERS = set(HELPERS) - {
    "start", "end", "sleep_start", "sleep_end", "strategy",
}


def _number(raw: Any, low: float, high: float) -> float:
    if isinstance(raw, bool):
        raise ValueError("source is not numeric")
    try:
        value = float(raw)
    except (TypeError, ValueError, OverflowError):
        raise ValueError("missing numeric source") from None
    if not isfinite(value) or not low <= value <= high:
        raise ValueError("numeric source outside physical range")
    return value


def _state(hass: Any, entity_id: str) -> Any:
    if not isinstance(entity_id, str) or "." not in entity_id:
        raise ValueError("source entity_id missing")
    row = hass.states.get(entity_id)
    if row is None or row.state in ("unknown", "unavailable", ""):
        raise ValueError("source entity unavailable")
    return row


def _fresh(row: Any, limit: int, now: datetime) -> None:
    stamp = getattr(row, "last_reported", None) or getattr(row, "last_updated", None)
    if not isinstance(stamp, datetime) or stamp.tzinfo is None:
        raise ValueError("source timestamp missing")
    seconds = (now - stamp).total_seconds()
    if seconds < -60 or seconds > limit:
        raise ValueError("source timestamp not fresh")


def _bool(raw: Any) -> bool:
    return raw is True or isinstance(raw, str) and raw.lower() in ("true", "on")


def _outage_snapshot(hass: Any, now: datetime) -> dict[str, Any]:
    row = _state(hass, OUTAGES)
    _fresh(row, 1800, now)
    attrs = row.attributes
    if not isinstance(attrs, dict) or not isinstance(attrs.get("outages"), list):
        raise ValueError("outage source missing")
    intervals = attrs["outages"]
    if len(intervals) > 48:
        raise ValueError("outage intervals too numerous")
    upcoming = []
    for event in intervals:
        if not isinstance(event, dict):
            raise ValueError("malformed outage")
        a = _number(event.get("start"), now.timestamp()-3600, now.timestamp()+72*3600)
        b = _number(event.get("end"), now.timestamp()-3600, now.timestamp()+72*3600)
        if a >= b:
            raise ValueError("invalid outage bounds")
        upcoming.append(dict(start=a, end=b))
    valid = _bool(attrs.get("valid")) and _bool(attrs.get("source_fresh"))
    return {
        "valid": valid,
        "mode": attrs.get("mode") if isinstance(attrs.get("mode"), str) else "unknown",
        "outages": upcoming if valid else [],
        "known_until": _number(
            attrs.get("known_until"), 0, now.timestamp()+72*3600
        ) if attrs.get("known_until") is not None else None,
    }


def normalize_hourly_weather(items: Any, received_at: float) -> list[dict[str, float]]:
    """Native WeatherEntity hourly rows, received != original forecast issue time."""
    if not isinstance(items, list) or not 2 <= len(items) <= 72:
        raise ValueError("hourly provider missing or oversized")
    records = []
    previous = float("-inf")
    for row in items:
        if not isinstance(row, dict):
            raise ValueError("malformed provider record")
        date = row.get("datetime")
        if isinstance(date, datetime):
            instant = date
        elif isinstance(date, str):
            try:
                instant = datetime.fromisoformat(date.replace("Z","+00:00"))
            except ValueError as exc:
                raise ValueError("invalid weather datetime") from exc
        else:
            raise ValueError("missing provider timestamp")
        if instant.tzinfo is None:
            raise ValueError("weather forecast has no timezone")
        at = _number(instant.timestamp(),received_at-6*3600,received_at+72*3600)
        c = _number(row.get("temperature"),-55,60)
        if at <= previous:
            raise ValueError("hourly weather not strictly chronological")
        previous = at
        records.append(dict(ts=at, temperature=c))
    if records[0]["ts"] > received_at+3600 or records[-1]["ts"] < received_at+23*3600:
        raise ValueError("hourly forecast insufficient")
    return records


def planner_snapshot(hass: Any, baseline: dict[str, Any],
                     hourly: list[dict[str, float]] | None,
                     received_at: float | None,
                     *, role_map: dict[str, str] | None = None) -> dict[str, Any]:
    """One bounded source snapshot; fail closed before calling the optimizer."""
    now = datetime.fromisoformat(baseline["at"])
    if now.tzinfo is None:
        raise ValueError("timezone required")
    if hourly is None or received_at is None or now.timestamp()-received_at > MAX_FORECAST_AGE:
        raise ValueError("fresh native hourly weather required")
    mapping = role_map
    if not isinstance(mapping, dict):
        user = _state(hass, UI)
        mapping = user.attributes.get("entities")
    if not isinstance(mapping, dict):
        raise ValueError("neither native nor legacy entity map available")
    settings: dict[str, Any] = {}
    for key,role in HELPERS.items():
        row = _state(hass, mapping.get(role))
        settings[key] = (
            _number(row.state,10,35) if key in NUMERIC_HELPERS
            else str(row.state)
        )
    settings["outage_schedule"] = _outage_snapshot(hass, now)
    # The fireplace is a live observed source, not a programmable command.
    fireplace = _state(hass, FIRE)
    _fresh(fireplace, 1800, now)
    if fireplace.state == "Неактивний":
        fire_delta = 0.0
    else:
        c = _number(fireplace.attributes.get("temperature"),-20,65)
        fire_delta = max(0.0,c-baseline["room"])
    return {
        "at": baseline["at"],
        "room": baseline["room"],
        "floor": baseline["floor"],
        "outside": baseline["outside"],
        "fire_delta": fire_delta,
        "model": dict(baseline["parameters"]),
        "assumed_kw": 3.0,
        "weather": [dict(row) for row in hourly],
        "settings": settings,
        "source_quality": {
            "weather_issue_time_known": False,
            "floor_is_derived_heat_storage": True,
            "fireplace_is_provisional": True,
            "solar_and_ventilation_not_reproduced": True,
            "electricity_next_24h_qualified": (
                settings["outage_schedule"]["valid"] and
                (settings["outage_schedule"]["known_until"] or 0)
                >= now.timestamp()+24*3600
            ),
        },
    }
