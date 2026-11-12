"""Isolated import/observation smoke against actual HA 2026.11 Python APIs.

No production connection, no Home Assistant Core start, no actuator call.
"""
from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone
from pathlib import Path
import sys
from tempfile import TemporaryDirectory
from types import SimpleNamespace
import zipfile

from build_marketplace import build


def row(state, attributes=None, *, age_seconds=0):
    seen = datetime.now(timezone.utc) - timedelta(seconds=age_seconds)
    return SimpleNamespace(
        state=str(state),
        attributes=attributes or {},
        last_reported=seen,
        last_updated=seen,
    )


def main() -> None:
    with TemporaryDirectory() as temp:
        folder = Path(temp)
        archive = folder / "carrier.zip"
        summary = build(archive)
        assert summary["files"] == 28
        target = folder / "isolated" / "custom_components" / "heating_ai_legacy"
        target.mkdir(parents=True)
        with zipfile.ZipFile(archive) as package:
            package.extractall(target)
        sys.path.insert(0, str(folder / "isolated"))
        try:
            from custom_components.heating_ai_legacy import async_setup
            from custom_components.heating_ai_legacy.config_flow import HeatingAIV1Flow
            from custom_components.heating_ai_legacy.native_runtime import (
                sample_v1_inputs, calculate_v1_coast,
            )

            assert issubclass(HeatingAIV1Flow, __import__(
                "homeassistant.config_entries", fromlist=["ConfigFlow"]
            ).ConfigFlow)
            assert asyncio.run(async_setup(None, {})) is True

            readings = {
                "sensor.room": row("21.3"),
                "sensor.outside": row("5.0"),
                "sensor.heating_environment": row("25.61", age_seconds=120),
                "sensor.heating_model": row("ok", {
                    "parameters": {
                        "floor_air": .0283966, "room_loss": .008192,
                        "floor_decay": .248832, "heat_gain": 1.0368,
                        "fire_gain": .025,
                    }
                }, age_seconds=150),
                "sensor.heating_forecast": row("21.03", {
                    "plan_id": "synthetic-forecast-ID",
                }),
            }
            hass = SimpleNamespace(states=SimpleNamespace(get=readings.get))
            snapshot = sample_v1_inputs(
                hass, {"room": "sensor.room", "outside": "sensor.outside"}
            )
            result = calculate_v1_coast(snapshot)
            assert result["mode"] == "COMPATIBILITY_SHADOW"
            assert result["physical_commands_enabled"] is False
            assert result["forecast_qualified"] is False
            assert result["source_plan_id"] == "synthetic-forecast-ID"
            assert result["legacy_plan"]["status"] == "LEGACY_UNAVAILABLE"
            assert isinstance(result["three_hour_end_c"], float)
            assert result["scenario"] == "three_hour_coast_without_heating_or_fire"
            from custom_components.heating_ai_legacy.plan_snapshot import mirror_legacy_plan
            timestamp = datetime.now(timezone.utc).timestamp()
            candidate = mirror_legacy_plan({
                "created_at": datetime.fromtimestamp(timestamp, timezone.utc).isoformat(),
                "execution_state": "advisory",
                "plan_id": "synthetic-plan",
                "planned_kwh": 3.5,
                "curve": [
                    {"ts": timestamp + 600, "temperature": 21.3, "target": 21.5},
                    {"ts": timestamp + 1200, "temperature": 21.4, "target": 21.5},
                ],
                "heat_segments": [
                    {"start": timestamp + 600, "end": timestamp + 1200, "power_kw": 6},
                ],
            }, timestamp)
            assert candidate["status"] == "LEGACY_MIRROR"
            assert len(candidate["curve"]) == 2 and len(candidate["segments"]) == 1
            assert candidate["execution_verified"] is False
            old = mirror_legacy_plan({
                "created_at": datetime.fromtimestamp(timestamp - 86400, timezone.utc).isoformat(),
                "curve": candidate["curve"],
            }, timestamp)
            assert old["status"] == "LEGACY_UNAVAILABLE"

            stale = dict(readings)
            stale["sensor.room"] = row("21.3", age_seconds=3600)
            try:
                sample_v1_inputs(
                    SimpleNamespace(states=SimpleNamespace(get=stale.get)),
                    {"room": "sensor.room", "outside": "sensor.outside"},
                )
            except ValueError:
                pass
            else:
                raise AssertionError("Stale room value incorrectly accepted")
            unavailable = dict(readings)
            unavailable["sensor.outside"] = row("unavailable")
            try:
                sample_v1_inputs(
                    SimpleNamespace(states=SimpleNamespace(get=unavailable.get)),
                    {"room": "sensor.room", "outside": "sensor.outside"},
                )
            except ValueError:
                pass
            else:
                raise AssertionError("Unknown outside temperature treated as zero")
            print("PASS: real HA import, original V1 coast and stale input guards; no physical calls")
        finally:
            sys.path.remove(str(folder / "isolated"))


if __name__ == "__main__":
    main()
