"""Original V1 decision semantics adapted as a non-actuating comparison.

Physical authority is *never* granted by the returned intent. The legacy
V1 still owns the thermostat and fan until an independently verified
one-writer handoff. No HA connection or dispatcher is imported.
"""
from __future__ import annotations

from math import isfinite
from typing import Any

from .native_engine.heating_model import decide


def _number(value: Any) -> float | None:
    if type(value) not in (float,int) or not isfinite(value):
        return None
    return float(value)


def _planned_demand(now: float, plan: dict[str, Any] | None) -> bool | None:
    if not isinstance(plan,dict) or plan.get("status") != "VIRTUAL_CONDITIONAL":
        return None
    computed=_number(plan.get("computed_at"))
    if computed is None or not 0 <= now-computed <= 900:
        return None
    rows = plan.get("heat_segments")
    if not isinstance(rows,list) or len(rows)>150:
        return None
    demand=False
    for row in rows:
        if not isinstance(row,dict):
            return None
        start=_number(row.get("start"))
        end=_number(row.get("end"))
        kw=_number(row.get("power_kw"))
        if None in (start,end,kw) or not start < end or not 0<=kw<=12:
            return None
        if start <= now < end and kw > 0:
            demand=True
    return demand


def evaluate(now: float, plan: dict | None,
             sources: dict[str, Any],
             *, model_validated: bool) -> dict[str, Any]:
    """Bounded hypothetical status; every result is non-executable."""
    base=dict(
        stage="NON_ACTUATING_V1_DECISION",
        physical_commands_enabled=False,
        physical_authority=False,
        request_is_command=False,
        commands_sent=0,
        legacy_writer_stays_owner=True,
        proposed_action=None,
    )
    if _number(now) is None or now <= 0 or not isinstance(sources,dict):
        return dict(base,status="BLOCKED",reason="invalid_decision_inputs")
    mode=sources.get("legacy_mode")
    armed=sources.get("armed") is True
    grid=sources.get("grid_actual")
    voltage_qualified=sources.get("voltage_qualified") is True
    room=_number(sources.get("room_c"))
    return_temp=_number(sources.get("return_c"))
    kws=_number(sources.get("kws_power_w"))
    thermostat=sources.get("thermostat_mode")
    if mode not in (
        "Автоматичне керування", "Штатний auto", "Спостереження",
        "Вимкнено",
    ):
        return dict(base,status="BLOCKED",reason="mode_unqualified")
    demand=_planned_demand(now,plan)
    # Passing allow_actuation=True here is strictly a hypothetical input to
    # V1's unchanged pure decide() function. No gateway or service exists.
    decision_grid=("on" if grid is True and voltage_qualified else
                   "unknown" if grid is True else
                   "off" if grid is False else "unknown")
    original=decide(
        mode=mode,armed=armed,allow_actuation=True,
        room=room,probe_ok=return_temp is not None,
        grid=decision_grid,
        thermostat_ok=thermostat in ("auto","heat","off"),
        power_ok=kws is not None,model_ready=model_validated is True,
        demand=bool(demand),settings_valid=demand is not None,
    )
    return dict(
        base,status="SHADOW_PROPOSAL",
        decision_basis="unchanged_v1_decide_with_conditional_native_schedule",
        proposed_action=original,
        plan_available=demand is not None,
        modeled_heat_demand=demand,
        reason=("voltage_not_qualified" if grid is True and not voltage_qualified
                else "legacy_owner_not_released" if original in ("heat","off")
                else "manual_or_source_fallback"),
    )
