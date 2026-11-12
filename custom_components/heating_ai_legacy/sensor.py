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
        return {key: data[key] for key in (
            "physical_commands_enabled", "forecast_qualified",
            "source", "scenario", "room_c", "estimated_floor_c",
            "outside_c", "three_hour_peak_c", "three_hour_end_c",
            "source_plan_id", "computed_at",
        ) if key in data}
