"""Read-only native HA CalendarEntity integration for Svitlo group 3.1.

Use the 2026.11 public CalendarEntity.async_get_events() API.
Never use a service call or command, and never invent coverage.
"""
from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone
from typing import Any

from homeassistant.components.calendar import DATA_COMPONENT
from homeassistant.util import dt as dt_util

from .native_calendar import parse_events


async def read_calendar(hass: Any, calendar_entity: str | None) -> dict:
    now=datetime.now(timezone.utc)
    if not isinstance(calendar_entity,str) or not calendar_entity.startswith("calendar."):
        return parse_events(None,now.timestamp())
    component=hass.data.get(DATA_COMPONENT)
    entity=(component.get_entity(calendar_entity)
            if component is not None else None)
    if entity is None:
        return parse_events(None,now.timestamp())
    try:
        records=await asyncio.wait_for(
            entity.async_get_events(
                hass,dt_util.as_local(now),
                dt_util.as_local(now+timedelta(hours=48)),
            ),timeout=15,
        )
    except (Exception, asyncio.CancelledError) as err:
        if isinstance(err,asyncio.CancelledError):
            raise
        return parse_events(None,now.timestamp())
    return parse_events(records,now.timestamp())
