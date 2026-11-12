"""Authenticated read-only native V1+ planner snapshot for the dashboard.

No update operation, physical command witness, entity writer, or automation
target exists in this API. Large forecast arrays are never stored in Recorder.
"""
from __future__ import annotations

import probatio
import time
from homeassistant.components import websocket_api
from homeassistant.components.websocket_api.decorators import (
    async_response, websocket_command,
)

from .const import DOMAIN


def register(hass):
    @websocket_command({
        "type": "heating_ai_legacy/native_snapshot",
        probatio.Required("entry_id"): str,
        probatio.Required("version"): [int],
    })
    @async_response
    async def preview(hass, connection, msg):
        if connection.user is None or not connection.user.is_admin:
            connection.send_error(
                msg["id"], "forbidden", "Administrator access required"
            )
            return
        if msg["version"] != [1, 0]:
            connection.send_error(
                msg["id"], "version", "Unsupported native preview contract"
            )
            return
        coordinator = hass.data.get(DOMAIN, {}).get(msg["entry_id"])
        if coordinator is None:
            connection.send_error(
                msg["id"], "unavailable", "V1 native shadow is not loaded"
            )
            return
        proposal = coordinator.native_plan
        published_at = (
            proposal.get("computed_at") if isinstance(proposal, dict) else None
        )
        if (coordinator._closed or
                coordinator._accepted_plan_key is None or
                coordinator._accepted_plan_key != coordinator._latest_input_key or
                not isinstance(published_at, (int,float)) or
                not 0 <= time.time()-published_at <= 900):
            proposal = None
        data = coordinator.data or {}
        voltage = {
            "voltage_guard_status": data.get(
                "voltage_guard_status", "LIMITS_UNCONFIGURED"
            ),
            "voltage_guard_reason_code": data.get(
                "voltage_guard_reason_code", "voltage_preview_unavailable"
            ),
            "voltage_guard_reason": data.get(
                "voltage_guard_reason", "Немає актуальних даних про напругу"
            ),
            "voltage_qualified": data.get("voltage_qualified") is True,
            "voltage_phases_v": data.get("voltage_phases_v"),
            "voltage_affected_phases": data.get("voltage_affected_phases", []),
            "voltage_min_v": data.get("voltage_min_v", 190),
            "voltage_max_v": data.get("voltage_max_v", 260),
            "voltage_trip_low_v": data.get("voltage_trip_low_v", 196),
            "voltage_trip_high_v": data.get("voltage_trip_high_v", 255),
            "voltage_recovery_low_v": data.get("voltage_recovery_low_v", 201),
            "voltage_recovery_high_v": data.get("voltage_recovery_high_v", 250),
            "voltage_recovery_seconds": data.get("voltage_recovery_seconds", 0),
            "voltage_recovery_required_seconds": data.get(
                "voltage_recovery_required_seconds", 30
            ),
        }
        if proposal is None:
            connection.send_result(msg["id"], {
                "status": "BLOCKED_INPUTS", "reason": "native_plan_not_ready",
                "physical_commands_enabled": False,
                "commands_sent": 0, "forecast": [], "heat_segments": [],
                "entity_map": dict(coordinator.role_map),
                "source_ids": dict(coordinator.source_ids),
                "outage_preview": coordinator.outage_schedule,
                **voltage,
            })
            return
        # Single immutable observational shape. No private training history,
        # state.json, user YAML, credentials or actuator commands are exposed.
        connection.send_result(msg["id"], {
            "status": proposal.get("status", "BLOCKED_INPUTS"),
            "quality": proposal.get("quality"),
            "reason": proposal.get("reason"),
            "computed_at": proposal.get("computed_at"),
            "nominal_energy_kwh": proposal.get("nominal_energy_kwh"),
            "source": "native_v1_optimizer",
            "entity_map": dict(coordinator.role_map),
            "source_ids": dict(coordinator.source_ids),
            "outage_preview": coordinator.outage_schedule,
            "decision_preview": (coordinator.data or {}).get(
                "legacy_decision_preview"
            ),
            "decision_never_executable": True,
            **voltage,
            "physical_commands_enabled": False,
            "commands_sent": 0,
            "forecast": proposal.get("forecast", [])[:200],
            "heat_segments": proposal.get("heat_segments", [])[:150],
        })

    websocket_api.async_register_command(hass, preview)
