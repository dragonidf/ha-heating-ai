"""Read-only V1+ native shadow diagnostics; never exports control actions."""
from __future__ import annotations

from homeassistant.components.sensor import SensorEntity
from homeassistant.helpers.update_coordinator import CoordinatorEntity


async def async_setup_entry(hass, entry, async_add_entities):
    """Publish one compact diagnostic sensor without a large Recorder payload."""
    async_add_entities([NativeV1ShadowStatus(entry)])


class NativeV1ShadowStatus(CoordinatorEntity, SensorEntity):
    _attr_has_entity_name = True
    _attr_name = "V1 native shadow"
    _attr_icon = "mdi:shield-eye"

    def __init__(self, entry):
        super().__init__(entry.runtime_data)
        self._attr_unique_id = f"{entry.entry_id}_v1_shadow"

    @property
    def native_value(self):
        return self.coordinator.data.get("mode", "UNAVAILABLE")

    @property
    def extra_state_attributes(self):
        # No full forecast arrays, raw YAML, credentials or private state.json
        data = self.coordinator.data
        plan = data.get("legacy_plan", {})
        result = {
            "legacy_plan_status": plan.get("status", "LEGACY_UNAVAILABLE"),
            "legacy_plan_points": len(plan.get("curve", [])),
            "legacy_plan_heat_windows": len(plan.get("segments", [])),
            "legacy_plan_execution": plan.get("source_execution_state"),
            "legacy_planned_kwh": plan.get("planned_kwh"),
            "native_plan_status": data.get("native_plan_status", "BLOCKED_INPUTS"),
            "native_plan_quality": data.get("native_plan_quality"),
            "native_plan_reason": data.get("native_plan_reason"),
            "native_plan_points": data.get("native_plan_points", 0),
            "native_planned_kwh": data.get("native_planned_kwh"),
            "source_quality": data.get("source_quality", "UNAVAILABLE"),
            "source_warnings": ",".join(data.get("source_warnings", [])[:12]),
            "manual_observed": data.get("manual_observed"),
            "grid_actual": data.get("grid_actual"),
            "voltage_guard_status": data.get("voltage_guard_status"),
            "voltage_guard_reason_code": data.get("voltage_guard_reason_code"),
            "voltage_guard_reason": data.get("voltage_guard_reason"),
            "voltage_qualified": data.get("voltage_qualified"),
            "voltage_recovery_seconds": data.get("voltage_recovery_seconds"),
            "voltage_recovery_required_seconds": data.get("voltage_recovery_required_seconds"),
            "voltage_min_v": data.get("voltage_min_v"),
            "voltage_max_v": data.get("voltage_max_v"),
            "voltage_trip_low_v": data.get("voltage_trip_low_v"),
            "voltage_trip_high_v": data.get("voltage_trip_high_v"),
            "voltage_recovery_low_v": data.get("voltage_recovery_low_v"),
            "voltage_recovery_high_v": data.get("voltage_recovery_high_v"),
            "voltage_phases_v": data.get("voltage_phases_v"),
            "voltage_affected_phases": data.get("voltage_affected_phases"),
            "voltage_source_quality": data.get("voltage_source_quality"),
            "kws_power_w": data.get("kws_power_w"),
            "kws_total_kwh": data.get("kws_total_kwh"),
            "legacy_mode": data.get("legacy_mode"),
            "thermostat_mode": data.get("thermostat_mode"),
            "journal_fenced": (data.get("durable_journal") or {}).get("fenced"),
            "storage_fault": data.get("storage_fault"),
            "decision_preview": data.get("legacy_decision_preview"),
            "decision_preview_status": data.get("legacy_decision_preview_status"),
            "decision_never_executable": True,
            "model_provenance": data.get("model_provenance"),
            "floor_provenance": data.get("floor_provenance"),
            "native_floor_c": data.get("native_floor_c"),
            "cached_model_version": data.get("cached_model_version"),
            "fan_preview_reason": (data.get("fan_preview") or {}).get("reason"),
            "fan_preview_percent": (data.get("fan_preview") or {}).get("proposed_percent"),
            "recuperator_state": (data.get("fan_preview") or {}).get("recuperator"),
            "bath_state": (data.get("fan_preview") or {}).get("bathroom"),
        }
        result.update({key: data[key] for key in (
            "physical_commands_enabled", "forecast_qualified",
            "source", "scenario", "room_c", "estimated_floor_c",
            "outside_c", "three_hour_peak_c", "three_hour_end_c",
            "source_plan_id", "computed_at",
        ) if key in data})
        return result
