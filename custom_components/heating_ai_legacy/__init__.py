"""Native V1+ preview built on the original V1 numerical model.

This is an observational compatibility milestone, NOT an executable heating
controller. It deliberately registers no switches, services, scripts,
automations or physical command transport.
"""
from __future__ import annotations

from hashlib import sha256
from pathlib import Path

from homeassistant.components import frontend, panel_custom
from homeassistant.components.http.server import StaticPathConfig
from homeassistant.config_entries import ConfigEntry
from homeassistant.exceptions import ConfigEntryNotReady
from homeassistant.core import HomeAssistant

from .const import DOMAIN, PLATFORMS
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
    assets = Path(__file__).parent / "assets"
    panel = assets / "panel.js"
    if not panel.is_file():
        raise ConfigEntryNotReady("Missing native plan/fact panel")
    bundle = await hass.async_add_executor_job(
        lambda: sha256(panel.read_bytes()).hexdigest()[:12]
    )
    entry.runtime_data = coordinator
    state = hass.data.setdefault(DOMAIN, {})
    forwarded = False
    panel_registered = False
    try:
        if not state.get("static"):
            await hass.http.async_register_static_paths([
                StaticPathConfig("/heating_ai_legacy/assets", str(assets), cache_headers=False)
            ])
            state["static"] = True
        await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
        forwarded = True
        await panel_custom.async_register_panel(
            hass,
            frontend_url_path="heating-v1-preview",
            webcomponent_name=f"heating-v1-native-{bundle}",
            sidebar_title="Heating AI V1+",
            sidebar_icon="mdi:chart-line",
            module_url=f"/heating_ai_legacy/assets/panel.js?v={bundle}",
            require_admin=True,
        )
        panel_registered = True
    except Exception:
        if panel_registered:
            frontend.async_remove_panel(
                hass, "heating-v1-preview", warn_if_unknown=False
            )
        if forwarded:
            await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
        raise
    return True


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Unload only the diagnostic sensor; the legacy controller is untouched."""
    if not await hass.config_entries.async_unload_platforms(entry, PLATFORMS):
        return False
    frontend.async_remove_panel(hass, "heating-v1-preview", warn_if_unknown=False)
    return True
