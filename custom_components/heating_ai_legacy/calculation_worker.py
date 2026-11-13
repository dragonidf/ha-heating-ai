"""Spawn-safe numerical worker. No HA state, callbacks or command authority."""
from __future__ import annotations

import time


def calculate(kind, job_id, inputs, options):
    # The release builder verifies these pure algorithms against V1 source.
    from .native_engine.heating_stability import stable_plan, recommend_power

    started = time.monotonic()
    try:
        algorithm = {"plan": stable_plan, "power_advice": recommend_power}[kind]
        result = algorithm(*inputs, **options)
        return dict(kind=kind, id=job_id, result=result,
                    seconds=time.monotonic()-started)
    except Exception as exc:
        return dict(kind=kind, id=job_id, error=str(exc),
                    seconds=time.monotonic()-started)
