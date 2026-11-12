"""Native V1+ preview built on the original V1 numerical model.

This is an observational compatibility milestone, NOT an executable heating
controller. It deliberately registers no switches, services, scripts,
automations or physical command transport.
"""
from __future__ import annotations

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant

from .const import PLATFORMS
from .native_runtime import V1ShadowCoordinator


async def async_setup(hass: HomeAssistant, config: dict) -> bool:
    """Accept the integration without changing existing heating."""
    return True


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Register only a safe read-only native model parity sensor."""
    if entry.data.get("shadow") is not True:
        raise ValueError("V1 native physical commissioning is not implemented")
    coordinator = V1ShadowCoordinator(
        hass, dict(room=entry.data["room"], outside=entry.data["outside"])
    )
    await coordinator.async_config_entry_first_refresh()
    entry.runtime_data = coordinator
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    return True


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Unload only the diagnostic sensor; the legacy controller is untouched."""
    return await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
