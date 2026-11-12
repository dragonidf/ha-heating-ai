"""Authenticated read-only native V1+ planner snapshot for the dashboard.

No update operation, physical command witness, entity writer, or automation
target exists in this API. Large forecast arrays are never stored in Recorder.
"""
from __future__ import annotations

import probatio
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
        if proposal is None:
            connection.send_result(msg["id"], {
                "status": "BLOCKED_INPUTS", "reason": "native_plan_not_ready",
                "physical_commands_enabled": False,
                "commands_sent": 0, "forecast": [], "heat_segments": [],
                "entity_map": dict(coordinator.role_map),
                "outage_preview": coordinator.outage_schedule,
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
            "outage_preview": coordinator.outage_schedule,
            "decision_preview": (coordinator.data or {}).get(
                "legacy_decision_preview"
            ),
            "decision_never_executable": True,
            "voltage_guard_status": (coordinator.data or {}).get(
                "voltage_guard_status", "LIMITS_UNCONFIGURED"
            ),
            "voltage_qualified": False,
            "physical_commands_enabled": False,
            "commands_sent": 0,
            "forecast": proposal.get("forecast", [])[:200],
            "heat_segments": proposal.get("heat_segments", [])[:150],
        })

    websocket_api.async_register_command(hass, preview)
