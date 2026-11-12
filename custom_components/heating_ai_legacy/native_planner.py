"""Provisional native 24-hour V1 planning through the UNCHANGED V1 optimizer.

No HA object, AppDaemon dependency, storage, command transport or actuators.
This function must never be wired to an executable command gateway before
the parity and one-writer commissioning gates have been passed.

Unlike a mirrored sensor.heating_forecast this is a separate native solve.
Any unqualified source is explicitly reported; missing weather is not
quietly replaced with a fabricated 24h constant temperature.
"""
from __future__ import annotations

from datetime import datetime
from math import isfinite
from typing import Any

from .native_engine.heating_model import plan

COEFFICIENTS = ("floor_air", "room_loss", "floor_decay", "heat_gain", "fire_gain")
REQUIRED_SETTINGS = (
    "target", "lower", "upper", "night_target", "night_lower", "night_upper",
    "start", "end", "strategy", "sleep_start", "sleep_end",
)
MAX_FUTURE_SECONDS = 27 * 3600


def _finite(raw: Any, low: float, high: float) -> float:
    if isinstance(raw, bool) or type(raw) not in (float, int):
        raise ValueError("unqualified numerical planning input")
    result = float(raw)
    if not isfinite(result) or not low <= result <= high:
        raise ValueError("planning input outside calibrated range")
    return result


def _blocked(reason: str) -> dict[str, Any]:
    return dict(
        status="BLOCKED_INPUTS", reason=reason,
        physical_commands_enabled=False,
        proposal_is_command=False,
        commands_sent=0,
        source="native_v1_optimizer",
        forecast=[], heat_segments=[],
    )


def provisional_plan(snapshot: dict[str, Any]) -> dict[str, Any]:
    """One bounded read-only 24-hour scenario with explicit input assumptions."""
    if not isinstance(snapshot, dict):
        return _blocked("invalid_snapshot")
    try:
        raw = snapshot["at"]
        if not isinstance(raw, str):
            raise ValueError("missing timezone timestamp")
        now = datetime.fromisoformat(raw)
        if now.tzinfo is None:
            raise ValueError("timezone is mandatory")
        at = now.timestamp()
        room = _finite(snapshot["room"], -20, 55)
        floor = _finite(snapshot["floor"], 0, 90)
        outside = _finite(snapshot["outside"], -55, 60)
        fire = _finite(snapshot["fire_delta"], 0, 50)
        watts = _finite(snapshot["assumed_kw"], 0, 9)
        model = snapshot["model"]
        if not isinstance(model, dict):
            raise ValueError("model unavailable")
        coefficients = {key: _finite(model[key], -10, 10) for key in COEFFICIENTS}
        settings = snapshot["settings"]
        if not isinstance(settings, dict) or any(k not in settings for k in REQUIRED_SETTINGS):
            raise ValueError("incomplete comfort and schedule controls")
        for family in ("", "night_"):
            lo = _finite(settings[family+"lower"], 10, 35)
            target = _finite(settings[family+"target"], 10, 35)
            hi = _finite(settings[family+"upper"], 10, 35)
            if not lo < target <= hi:
                raise ValueError("invalid comfort bounds")
        for key in ("start","end","sleep_start","sleep_end"):
            raw_time = settings[key]
            if not isinstance(raw_time, str) or not 5 <= len(raw_time) <= 8:
                raise ValueError("invalid schedule time")
            hour, minute = int(raw_time[:2]), int(raw_time[3:5])
            if not (0 <= hour <= 23 and raw_time[2] == ":" and 0 <= minute <= 59):
                raise ValueError("invalid schedule clock")
        if settings["start"] == settings["end"] or settings["sleep_start"] == settings["sleep_end"]:
            raise ValueError("no permitted time window")

        weather = snapshot["weather"]
        if not isinstance(weather, list) or not 2 <= len(weather) <= 72:
            raise ValueError("no independently obtained hourly weather")
        normalized = []
        last = float("-inf")
        for item in weather:
            if not isinstance(item, dict):
                raise ValueError("malformed weather point")
            stamp = _finite(item.get("ts"), at-6*3600, at+72*3600)
            temp = _finite(item.get("temperature"), -55, 60)
            if stamp <= last:
                raise ValueError("unordered weather")
            last = stamp
            normalized.append(dict(ts=stamp, temperature=temp))
        if normalized[0]["ts"] > at+3600 or normalized[-1]["ts"] < at+23*3600:
            raise ValueError("weather does not cover requested horizon")
        if any(b["ts"] - a["ts"] > 2*3600 for a,b in zip(normalized,normalized[1:])):
            raise ValueError("large weather gap")

        # Never interpret "no calendar event" as confirmed electricity.
        # The incomplete horizon is still simulated, but cannot be actuated.
        horizon = at+24*3600
        outage = settings.get("outage_schedule")
        qualified_schedule = (
            isinstance(outage, dict) and outage.get("valid") is True and
            type(outage.get("known_until")) in (int,float) and
            outage["known_until"] >= horizon
        )
        for segment in ((outage or {}).get("outages", []) if isinstance(outage,dict) else []):
            if not isinstance(segment, dict):
                raise ValueError("invalid outage interval")
            x = _finite(segment.get("start"), at-3600, at+72*3600)
            y = _finite(segment.get("end"), at-3600, at+72*3600)
            if not x < y:
                raise ValueError("negative outage interval")

        # Guard against an unbounded or non-serializable user-provided
        # settings object. The native planner only consumes reviewed keys.
        reviewed = {k:settings[k] for k in REQUIRED_SETTINGS}
        reviewed.update(
            split_comfort=True, morning_min_minutes=30,
            outage_schedule=(outage if isinstance(outage,dict) else
                             dict(mode="unknown",valid=False,outages=[])),
            day_price=None, night_price=None,
        )
        # All variables required by the original optimizer are immutable
        # local numerical data. Ignore unverified solar/ventilation claims.
        result = plan(now,room,floor,outside,fire,coefficients,
                      normalized,reviewed,power_kw=watts)
        if not isinstance(result, dict):
            return _blocked("optimizer_no_solution")
        points = result.get("curve")
        windows = result.get("heat_segments")
        if not isinstance(points, list) or not isinstance(windows, list) or len(points) > 200 or len(windows)>150:
            return _blocked("optimizer_output_out_of_bounds")
        return {
            "status": "VIRTUAL_CONDITIONAL",
            "quality": ("PROVISIONAL_SOURCES" if not qualified_schedule
                        else "NOT_PARITY_OR_LEARNING_QUALIFIED"),
            "reason": ("schedule_coverage_unverified" if not qualified_schedule
                       else "requires_environment_and_mode_parity"),
            "physical_commands_enabled": False,
            "proposal_is_command": False,
            "commands_sent": 0,
            "source": "native_v1_optimizer",
            "forecast": points,
            "heat_segments": windows,
            "nominal_energy_kwh": result.get("energy"),
            "minimum_c": result.get("minimum"),
            "horizon_hours": result.get("horizon_hours"),
            "computed_at": at,
            "source_assumptions": [
                "nominal_thermal_model", "no_verified_solar_gain",
                "ventilation_not_reproduced", "not_executable",
            ],
        }
    except (KeyError, TypeError, ValueError, OverflowError):
        return _blocked("invalid_or_missing_input")
