"""Pure voltage recovery latch for one-writer Heating AI V1+.

The last displayed 220V is not grid evidence. Voltage is qualified only
AFTER independent live grid confirmation, fresh per-phase readings, and
an uninterrupted stability period. Never sends commands.
"""
from __future__ import annotations

from math import isfinite
from typing import Any


class VoltageGuard:
    def __init__(self):
        self.stable_since: float | None = None
        self.last_at: float | None = None
        self.policy: tuple | None = None

    def evaluate(
        self, now: float, grid: bool | None, phases_v: list[Any],
        *, minimum_v: float | None,
        maximum_v: float | None, recovery_seconds: float = 180.,
    ) -> dict:
        if type(now) not in (int,float) or not isfinite(now) or now<=0:
            raise ValueError("Voltage clock invalid")
        settings=(minimum_v,maximum_v,recovery_seconds)
        def blocked(reason):
            self.stable_since=None
            return dict(
                status=reason,voltage_qualified=False,
                sustained_seconds=0.,physical_commands_enabled=False,
                physical_authority=False,
            )
        if self.last_at is not None and now <= self.last_at:
            self.last_at=now
            return blocked("CLOCK_RESET")
        self.last_at=now
        if settings!=self.policy:
            self.policy=settings
            self.stable_since=None
        if grid is not True:
            return blocked("GRID_OFF_OR_UNKNOWN")
        if (type(minimum_v) not in (int,float)
            or type(maximum_v) not in (int,float)
            or type(recovery_seconds) not in (int,float)
            or not all(isfinite(v) for v in settings)
            or not 140<=minimum_v<=235
            or not 220<=maximum_v<=280
            or minimum_v+5>=maximum_v
            or not 30<=recovery_seconds<=900):
            return blocked("LIMITS_UNCONFIGURED")
        if (not isinstance(phases_v,list) or len(phases_v)!=3
            or any(type(x) not in (float,int) or not isfinite(x)
                   or not 0<=x<=350 for x in phases_v)):
            return blocked("VOLTAGE_SENSOR_UNAVAILABLE")
        if not all(minimum_v<=voltage<=maximum_v for voltage in phases_v):
            return blocked("VOLTAGE_OUT_OF_RANGE")
        if self.stable_since is None:
            self.stable_since=now
        duration=max(0.,now-self.stable_since)
        return dict(
            status=("STABLE" if duration>=recovery_seconds else "RECOVERING"),
            voltage_qualified=duration>=recovery_seconds,
            sustained_seconds=round(duration,1),
            physical_commands_enabled=False,
            physical_authority=False,
        )
