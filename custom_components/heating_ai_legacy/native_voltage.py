"""Three-phase voltage guard for one-writer Heating AI V1+ shadow mode.

Voltage limits mirror the KWS protection settings. The software trips early,
then requires a wider recovery band and uninterrupted fresh readings. It never
sends commands to the boiler.
"""
from __future__ import annotations

from math import isfinite
from typing import Any


LOW_MARGIN_V = 6
HIGH_MARGIN_V = 5
RECOVERY_HYSTERESIS_V = 5
MAX_SAMPLE_GAP_S = 90


def valid_policy(minimum_v: Any, maximum_v: Any,
                 recovery_seconds: Any = 30.) -> bool:
    """Validate KWS limits and ensure the software recovery band is usable."""
    settings = (minimum_v, maximum_v, recovery_seconds)
    return (
        all(type(value) in (int, float) and isfinite(value) for value in settings)
        and 140 <= minimum_v <= 235
        and 220 <= maximum_v <= 280
        and minimum_v + LOW_MARGIN_V + RECOVERY_HYSTERESIS_V
            < maximum_v - HIGH_MARGIN_V - RECOVERY_HYSTERESIS_V
        and 30 <= recovery_seconds <= 900
    )


class VoltageGuard:
    def __init__(self):
        self.stable_since: float | None = None
        self.last_at: float | None = None
        self.policy: tuple | None = None
        self.trip: str | None = None

    def evaluate(
        self, now: float, grid: bool | None, phases_v: list[Any],
        *, minimum_v: float | None = 190.,
        maximum_v: float | None = 260., recovery_seconds: float = 30.,
    ) -> dict:
        if type(now) not in (int, float) or not isfinite(now) or now <= 0:
            raise ValueError("Voltage clock invalid")

        def blocked(status, reason_code, **details):
            self.stable_since = None
            return dict(
                status=status, reason_code=reason_code,
                reason=details.pop("reason", reason_code),
                voltage_qualified=False, sustained_seconds=0.,
                physical_commands_enabled=False, physical_authority=False,
                minimum_v=minimum_v, maximum_v=maximum_v,
                trip_low_v=(minimum_v + LOW_MARGIN_V
                            if type(minimum_v) in (int, float) else None),
                trip_high_v=(maximum_v - HIGH_MARGIN_V
                             if type(maximum_v) in (int, float) else None),
                recovery_low_v=(minimum_v + LOW_MARGIN_V + RECOVERY_HYSTERESIS_V
                                if type(minimum_v) in (int, float) else None),
                recovery_high_v=(maximum_v - HIGH_MARGIN_V - RECOVERY_HYSTERESIS_V
                                 if type(maximum_v) in (int, float) else None),
                phases_v=phases_v if isinstance(phases_v, list) else None,
                affected_phases=details.pop("affected_phases", []),
                **details,
            )

        settings = (minimum_v, maximum_v, recovery_seconds)
        if self.last_at is not None and now <= self.last_at:
            self.last_at = now
            self.trip = None
            return blocked("CLOCK_RESET", "voltage_clock_reset")
        if self.last_at is not None and now - self.last_at > MAX_SAMPLE_GAP_S:
            self.stable_since = None
        self.last_at = now
        if settings != self.policy:
            self.policy = settings
            self.stable_since = None
            self.trip = None
        if grid is not True:
            self.trip = None
            return blocked("GRID_OFF_OR_UNKNOWN", "grid_not_confirmed")
        if not valid_policy(minimum_v, maximum_v, recovery_seconds):
            self.trip = None
            return blocked("LIMITS_UNCONFIGURED", "voltage_limits_invalid")
        if (not isinstance(phases_v, list) or len(phases_v) != 3
            or any(type(x) not in (float, int) or not isfinite(x)
                   or not 0 <= x <= 350 for x in phases_v)):
            self.trip = None
            return blocked("VOLTAGE_SENSOR_UNAVAILABLE", "voltage_phase_missing_or_stale")

        low_trip = minimum_v + LOW_MARGIN_V
        high_trip = maximum_v - HIGH_MARGIN_V
        recovery_low = low_trip + RECOVERY_HYSTERESIS_V
        recovery_high = high_trip - RECOVERY_HYSTERESIS_V
        low = [i for i, value in enumerate(phases_v, 1) if value <= low_trip]
        high = [i for i, value in enumerate(phases_v, 1) if value >= high_trip]
        if low or high:
            self.trip = "LOW_VOLTAGE" if low else "HIGH_VOLTAGE"
            names = [f"L{i}" for i in (low or high)]
            detail = (
                f"Низька напруга {', '.join(names)}: "
                f"{min(phases_v[i - 1] for i in low):g} В; "
                f"межа KWS {minimum_v:g} В, раннє вимкнення ≤{low_trip:g} В"
                if low else
                f"Висока напруга {', '.join(names)}: "
                f"{max(phases_v[i - 1] for i in high):g} В; "
                f"межа KWS {maximum_v:g} В, раннє вимкнення ≥{high_trip:g} В"
            )
            return blocked(
                self.trip, "phase_below_early_cutoff" if low else "phase_above_early_cutoff",
                reason=detail, affected_phases=names,
            )

        outside_recovery = [
            i for i, value in enumerate(phases_v, 1)
            if not recovery_low <= value <= recovery_high
        ]
        if outside_recovery:
            self.stable_since = None
            names = [f"L{i}" for i in outside_recovery]
            return blocked(
                "RECOVERY_HYSTERESIS", "voltage_not_normalized",
                reason=(f"Очікування нормалізації {', '.join(names)}: "
                        f"потрібно {recovery_low:g}–{recovery_high:g} В"),
                affected_phases=names,
            )

        if self.stable_since is None:
            self.stable_since = now
        duration = max(0., now - self.stable_since)
        qualified = duration >= recovery_seconds
        if qualified:
            self.trip = None
        return dict(
            status="NORMAL" if qualified else "RECOVERING",
            reason_code="voltage_normal" if qualified else "voltage_stabilizing",
            reason=("Напруга нормалізована; дозвіл відновлено"
                    if qualified else
                    f"Напруга в нормі; стабілізація {duration:.0f}/{recovery_seconds:g} с"),
            voltage_qualified=qualified,
            sustained_seconds=round(duration, 1),
            physical_commands_enabled=False,
            physical_authority=False,
            minimum_v=minimum_v, maximum_v=maximum_v,
            trip_low_v=low_trip, trip_high_v=high_trip,
            recovery_low_v=recovery_low, recovery_high_v=recovery_high,
            phases_v=phases_v, affected_phases=[],
        )
