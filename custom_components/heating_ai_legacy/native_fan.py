"""Pure V1 fireplace/circulation projection, deliberately without device transport.

Keeps the meaningful V1 ignition/stop continuity guards and the V2 principle
of a pure demand reducer. No fan, recuperator or bath writes exist here.
"""
from __future__ import annotations

from dataclasses import dataclass
from math import isfinite

FIRE_START=5.0
FIRE_INTENSE=10.0
FIRE_STOP=3.0
FIRE_FAST_STOP=4.0
FAST_STOP_SLOPE=-2.0
IGNITION_SLOPE=.8
FADE_SLOPE=-.8
FIRE_COOLDOWN=300.0
CONTINUITY=90.0


@dataclass(frozen=True)
class FanState:
    at: float
    delta: float | None
    burning: bool=False
    below_since: float | None=None


@dataclass(frozen=True)
class FanDemand:
    state: FanState
    request: bool
    percent: int | None
    source: str
    reason: str
    phase: str
    physical_commands_enabled: bool=False


def project(prev: FanState | None, at: float, delta: float | None,
            *, mode: str, enabled: bool, grid: bool | None,
            fan_readable: bool, standard: float | None,
            night_limit: float | None, intense: float | None,
            night_window: bool, backup_qualified: bool=False) -> FanDemand:
    if not (type(at) in (float,int) and isfinite(at) and at>0):
        raise ValueError("Fan clock invalid")
    if delta is not None and (type(delta) not in (float,int) or
                              not isfinite(delta) or not 0<=delta<=65):
        delta=None
    if prev is not None and at <= prev.at:
        raise ValueError("Fan clock must advance")
    span=at-prev.at if prev is not None else None
    continuous=span is not None and 0<span<=CONTINUITY
    slope=((delta-prev.delta)*3600/max(300,span)
           if continuous and delta is not None and prev.delta is not None
           else 0.0)
    burning=False
    below=None
    if delta is not None:
        if round(delta,6)>=FIRE_START:
            burning=True
        elif prev is not None and prev.burning and continuous and not (
            delta<FIRE_FAST_STOP and slope<=FAST_STOP_SLOPE
        ):
            if delta<FIRE_STOP:
                below=prev.below_since if prev.below_since is not None else at
                burning=at-below<FIRE_COOLDOWN
            else:
                burning=True
    phase=("Датчик недоступний" if delta is None
           else "Згасає" if slope<FADE_SLOPE and delta>=2
           else "Інтенсивно горить" if delta>=FIRE_INTENSE
           else "Горить" if delta>=FIRE_START
           else "Розпалювання" if delta>=2 and slope>IGNITION_SLOPE
           else "Згасає" if burning else "Неактивний")
    new=FanState(at=at,delta=delta,burning=burning,below_since=below)
    if mode not in ("Авто","Автоматично","Ручний запуск","Зупинити"):
        return FanDemand(new,False,None,"none","unknown_fan_mode",phase)
    if mode=="Зупинити" or not enabled:
        return FanDemand(new,False,None,"none","explicit_stop_or_disabled",phase)
    source=("fireplace" if burning else "manual"
            if mode=="Ручний запуск" else "none")
    request=source!="none"
    if not request:
        return FanDemand(new,False,None,"none","no_verified_demand",phase)
    if not fan_readable:
        return FanDemand(new,True,None,source,"fan_unavailable",phase)
    if grid is not True and not backup_qualified:
        return FanDemand(new,True,None,source,"fan_supply_unknown",phase)
    if any(type(v) not in (int,float) or not isfinite(v) or not 0<=v<=100
           for v in (standard,night_limit,intense)):
        return FanDemand(new,True,None,source,"fan_speed_unqualified",phase)
    usual=max(1,int(standard))
    night=min(usual,max(1,int(night_limit)))
    high=max(usual,int(intense))
    speed=(night if night_window else
           high if phase=="Інтенсивно горить" else usual)
    return FanDemand(new,True,speed,source,"conditional_demand_only",phase)
