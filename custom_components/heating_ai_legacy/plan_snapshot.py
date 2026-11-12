"""Validated view of the *legacy* V1 plan, for read-only parity and UI.

This module never makes actuator decisions. Forecast points in the HA state
are an external data product, not an independently recomputed native plan.
"""
from __future__ import annotations

from datetime import datetime, timezone
import math
from typing import Any

MAX_POINTS = 96
MAX_SEGMENTS = 48
HORIZON = 26 * 3600


def _float(value: Any, low: float, high: float) -> float | None:
    if isinstance(value, bool):
        return None
    try:
        result = float(value)
    except (TypeError, ValueError, OverflowError):
        return None
    return result if math.isfinite(result) and low <= result <= high else None


def _timestamp(value: Any) -> float | None:
    if not isinstance(value, str):
        return None
    try:
        dt = datetime.fromisoformat(value)
    except ValueError:
        return None
    if dt.tzinfo is None:
        return None
    return dt.timestamp()


def _samples(rows: Any, now: float, *, historical: bool) -> list[dict[str, float]]:
    if not isinstance(rows, list) or len(rows) > 3000:
        return []
    first = now - (6 * 3600 if historical else 3600)
    last = now + (600 if historical else HORIZON)
    valid = []
    previous = float("-inf")
    for row in rows:
        if not isinstance(row, dict):
            continue
        at = _float(row.get("ts"), first, last)
        temp = _float(row.get("temperature"), -20, 65)
        if at is None or temp is None or at <= previous:
            continue
        previous = at
        output = {"at": at, "temperature": temp}
        if not historical:
            for key in ("target", "lower", "upper"):
                value = _float(row.get(key), 10, 35)
                if value is not None:
                    output[key] = value
        valid.append(output)
        if len(valid) >= MAX_POINTS:
            break
    return valid


def _segments(rows: Any, now: float) -> list[dict[str, float]]:
    if not isinstance(rows, list) or len(rows) > 300:
        return []
    valid = []
    for row in rows:
        if not isinstance(row, dict):
            continue
        start = _float(row.get("start"), now - 3600, now + HORIZON)
        end = _float(row.get("end"), now - 3600, now + HORIZON)
        power = _float(row.get("power_kw"), 0, 12)
        if start is None or end is None or power is None or not start < end:
            continue
        if end <= now:
            continue
        valid.append({"start": max(start, now), "end": end, "power_kw": power})
        if len(valid) >= MAX_SEGMENTS:
            break
    return sorted(valid, key=lambda r: r["start"])


def mirror_legacy_plan(attrs: Any, now: float) -> dict[str, Any]:
    """Strictly bounded, explicit legacy-mirror—not a native optimizer output."""
    empty: dict[str, Any] = {
        "status": "LEGACY_UNAVAILABLE", "source": "sensor.heating_forecast",
        "execution_verified": False, "physical_commands_enabled": False,
        "curve": [], "actual": [], "segments": [],
    }
    if not isinstance(attrs, dict):
        return empty
    created = _timestamp(attrs.get("created_at"))
    if created is None or created > now + 60 or now - created > 2 * 3600:
        return dict(empty, reason="forecast_stale_or_unverified")
    curve = _samples(attrs.get("curve"), now, historical=False)
    actual = _samples(attrs.get("actual_curve"), now, historical=True)
    segments = _segments(attrs.get("heat_segments"), now)
    if len(curve) < 2:
        return dict(empty, reason="no_valid_trajectory")
    planned = _float(attrs.get("planned_kwh"), 0, 200)
    return dict(
        status="LEGACY_MIRROR",
        source="sensor.heating_forecast",
        execution_verified=False,
        physical_commands_enabled=False,
        source_plan_id=str(attrs.get("plan_id", ""))[:80],
        source_execution_state=str(attrs.get("execution_state", "unknown"))[:40],
        created_at=created,
        curve=curve, actual=actual, segments=segments,
        planned_kwh=planned,
    )
