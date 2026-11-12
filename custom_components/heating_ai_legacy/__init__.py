"""Native V1+ preview built on the original V1 numerical model.

This is an observational compatibility milestone, NOT an executable heating
controller. It deliberately registers no switches, services, scripts,
automations or physical command transport.
"""
from __future__ import annotations

import asyncio
from hashlib import sha256
from pathlib import Path

from homeassistant.components import frontend, panel_custom
from homeassistant.components.http.server import StaticPathConfig
from homeassistant.components.weather.const import DATA_COMPONENT as WEATHER_ENTITIES
from homeassistant.config_entries import ConfigEntry
from homeassistant.exceptions import ConfigEntryNotReady
from homeassistant.core import HomeAssistant

from .const import DOMAIN, PLATFORMS, SOURCE_UI
from .native_runtime import V1ShadowCoordinator
from .native_api import register as register_preview_api
from .native_persistence import bootstrap
from .native_model import load as load_model_checkpoint


async def async_setup(hass: HomeAssistant, config: dict) -> bool:
    """Accept the integration without changing existing heating."""
    return True


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Register only a safe read-only native model parity sensor."""
    if entry.data.get("shadow") is not True:
        raise ValueError("V1 native physical commissioning is not implemented")
    coordinator = V1ShadowCoordinator(
        hass, dict(room=entry.data["room"], outside=entry.data["outside"]), entry
    )
    await coordinator.async_config_entry_first_refresh()
    # Only private native .storage is created. The old AppDaemon state.json,
    # KWS counters, model, automation ownership and heater are never touched.
    data_directory = hass.config.path(".storage", DOMAIN, entry.entry_id)
    try:
        native_store, ledger, journal = await hass.async_add_executor_job(
            bootstrap, data_directory
        )
        checkpoint = await hass.async_add_executor_job(
            load_model_checkpoint, data_directory
        )
    except Exception as exc:
        await coordinator.close()
        raise ConfigEntryNotReady(
            "V1+ private state or safety journal unavailable; no command authority"
        ) from exc
    coordinator.attach_private_state(
        native_store, ledger, journal, data_directory, checkpoint
    )
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
        if not state.get("_api"):
            register_preview_api(hass)
            state["_api"] = True
        state[entry.entry_id] = coordinator
        await panel_custom.async_register_panel(
            hass,
            frontend_url_path="heating-v1-preview",
            webcomponent_name=f"heating-v1-native-{bundle}",
            sidebar_title="Heating AI V1+",
            sidebar_icon="mdi:chart-line",
            module_url=f"/heating_ai_legacy/assets/panel.js?v={bundle}",
            config={"entry_id": entry.entry_id},
            require_admin=True,
        )
        panel_registered = True

        # Native, read-only hourly forecasts. No service calls, cloud tokens,
        # or legacy-AppDaemon lifecycle coupling. If provider is unavailable,
        # the independent plan remains BLOCKED_INPUTS instead of inventing it.
        ui = hass.states.get(SOURCE_UI)
        entities = ui.attributes.get("entities") if ui is not None else None
        weather_id = entities.get("weather") if isinstance(entities, dict) else None
        store = hass.data.get(WEATHER_ENTITIES)
        provider = (
            store.get_entity(weather_id)
            if store is not None and isinstance(weather_id, str) else None
        )
        if provider is not None:
            try:
                coordinator._weather_unsubscribe = provider.async_subscribe_forecast(
                    "hourly", coordinator.receive_weather,
                )
                await asyncio.wait_for(
                    provider.async_update_listeners({"hourly"}), timeout=20,
                )
            except (TypeError, ValueError, RuntimeError, asyncio.TimeoutError):
                coordinator.receive_weather(None)
    except Exception:
        state.pop(entry.entry_id, None)
        await coordinator.close()
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
    await entry.runtime_data.close()
    hass.data.get(DOMAIN, {}).pop(entry.entry_id, None)
    return True
