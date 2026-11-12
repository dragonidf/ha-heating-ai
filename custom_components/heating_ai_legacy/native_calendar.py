"""Pure parser for Svitlo 3.1 calendar events.

A calendar listing cannot prove no outages outside returned events.
All results are conditional and non-actuating.
"""
from __future__ import annotations

from datetime import datetime
import math
from typing import Any

LIMIT=72
HORIZON=48*3600


def _time(value: Any) -> float:
    if isinstance(value,datetime):
        date=value
    elif isinstance(value,str):
        try:
            date=datetime.fromisoformat(value.replace("Z","+00:00"))
        except ValueError as exc:
            raise ValueError("Calendar datetime invalid") from exc
    else:
        raise ValueError("Expected a timed calendar outage")
    if date.tzinfo is None:
        raise ValueError("Calendar outage timezone unknown")
    stamp=date.timestamp()
    if not math.isfinite(stamp):
        raise ValueError("Calendar timestamp not finite")
    return stamp


def parse_events(items: Any, now: float) -> dict:
    """Do not turn a missing or empty Svitlo feed into confirmed grid."""
    empty=dict(
        mode="calendar_unknown",valid=False,outages=[],
        known_until=None,coverage_proven=False,
        source="calendar_kiivska_oblast_3_1",
        physical_commands_enabled=False,
        observed_at=now,
    )
    if type(now) not in (int,float) or not math.isfinite(now) or now<=0:
        raise ValueError("Invalid calendar observation time")
    if not isinstance(items,list) or len(items)>LIMIT:
        return dict(empty,reason="calendar_response_unavailable_or_oversized")
    if not items:
        return dict(empty,reason="empty_calendar_is_not_coverage")
    records=[]
    for item in items:
        start=item.get("start") if isinstance(item,dict) else getattr(item,"start",None)
        end=item.get("end") if isinstance(item,dict) else getattr(item,"end",None)
        try:
            a,b=_time(start),_time(end)
        except ValueError:
            return dict(empty,reason="unqualified_calendar_interval")
        if not (a<b and now-2*3600<=b and a<=now+HORIZON+3600):
            continue
        records.append(dict(start=max(a,now),end=min(b,now+HORIZON)))
    if not records:
        return dict(empty,reason="no_qualified_outage_intervals")
    records.sort(key=lambda r:r["start"])
    merged=[]
    for item in records:
        if merged and item["start"]<=merged[-1]["end"]:
            merged[-1]["end"]=max(merged[-1]["end"],item["end"])
        else:
            merged.append(item)
    return dict(
        mode="calendar_events_partial",
        valid=True,
        outages=merged,
        # This marks the latest known event, NOT complete schedule coverage.
        known_until=max(item["end"] for item in merged),
        coverage_proven=False,
        source="calendar_kiivska_oblast_3_1",
        physical_commands_enabled=False,
        observed_at=now,
        reason="known_outages_only_unpublished_intervals_unknown",
    )
