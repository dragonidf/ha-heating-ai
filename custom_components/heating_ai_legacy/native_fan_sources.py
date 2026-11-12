"""Build V1 fireplace/fan/ventilation SHADOW input without any device calls."""
from __future__ import annotations

from datetime import datetime
import math
from zoneinfo import ZoneInfo
from typing import Any

from .native_fan import FanState, project

KYIV = ZoneInfo("Europe/Kyiv")


def _row(hass: Any, entity_id: str | None):
    return hass.states.get(entity_id) if isinstance(entity_id, str) else None


def _value(hass: Any, entity_id: str | None):
    row=_row(hass,entity_id)
    return row.state if row is not None and row.state not in (
        "unknown","unavailable",""
    ) else None


def _float(raw: Any, minimum: float, maximum: float) -> float | None:
    if isinstance(raw,bool):
        return None
    try:
        value=float(raw)
    except (TypeError,ValueError,OverflowError):
        return None
    return value if math.isfinite(value) and minimum <= value <= maximum else None


def _fresh(hass: Any, entity_id: str | None, now: float, max_age: float):
    row=_row(hass,entity_id)
    if row is None or row.state in ("unknown","unavailable",""):
        return None
    received=getattr(row,"last_reported",None) or getattr(row,"last_updated",None)
    if not isinstance(received,datetime) or received.tzinfo is None:
        return None
    return row if -30 <= now-received.timestamp() <= max_age else None


def snapshot(hass: Any, roles: dict, sources: dict,
             safety: dict, previous: FanState | None,
             now: float) -> tuple[FanState, dict]:
    fireplace = _fresh(hass,sources.get("fireplace_temp"),now,3600)
    room = safety.get("room_c")
    temperature = _float(fireplace.state,-20,65) if fireplace is not None else None
    delta = max(0,temperature-room) if temperature is not None and room is not None else None
    mode = _value(hass,roles.get("fan_mode")) or "UNKNOWN"
    enabled = _value(hass,roles.get("fan_control")) == "on"
    fan=_fresh(hass,roles.get("fan"),now,300)
    normal=_float(_value(hass,roles.get("fan_speed")),1,100)
    night=_float(_value(hass,roles.get("fan_night_limit")),1,100)
    intense=_float(_value(hass,roles.get("fan_intense_speed")),1,100)
    local=datetime.fromtimestamp(now,KYIV)
    night_window=local.hour>=23 or local.hour<7
    result=project(
        previous,now,delta,mode=mode,enabled=enabled,
        grid=safety.get("grid"),fan_readable=fan is not None and fan.state in ("on","off"),
        standard=normal,night_limit=night,intense=intense,
        night_window=night_window,backup_qualified=False,
    )
    ventilation={}
    for role in ("recuperator","bath"):
        source=sources.get(role)
        raw=_fresh(hass,source,now,600)
        ventilation[role] = (
            raw.state if raw is not None and raw.state in ("on","off")
            else "unknown"
        )
    return result.state, dict(
        mode="SHADOW_FAN",
        state=result.phase,
        request=result.request,
        proposed_percent=result.percent,
        request_source=result.source,
        reason=result.reason,
        actual_state=fan.state if fan is not None else "unknown",
        manual_mode=mode,
        enabled=enabled,
        supply_qualified=safety.get("grid") is True,
        recuperator=ventilation["recuperator"],
        bathroom=ventilation["bath"],
        physical_commands_enabled=False,
        commands_sent=0,
    )
