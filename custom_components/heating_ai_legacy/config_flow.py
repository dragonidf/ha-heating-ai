"""One-time, read-only compatibility discovery of the running V1 controller."""
from __future__ import annotations

import voluptuous as vol
from homeassistant import config_entries

from .const import DOMAIN, SOURCE_UI, SOURCE_HINTS


class HeatingAIV1Flow(config_entries.ConfigFlow, domain=DOMAIN):
    """No side effects: installs a diagnostic native SHADOW beside V1."""

    VERSION = 1

    async def async_step_user(self, user_input=None):
        """Require explicit consent to a non-actuating compatibility probe."""
        if self._async_current_entries():
            return self.async_abort(reason="single_instance_allowed")
        source = self.hass.states.get(SOURCE_UI)
        mapping = source.attributes.get("entities") if source is not None else None
        if not isinstance(mapping, dict) or any(
            not isinstance(mapping.get(role), str)
            for role in ("room", "outside")
        ):
            return self.async_abort(reason="legacy_sources_missing")
        if user_input is None:
            return self.async_show_form(
                step_id="user",
                data_schema=vol.Schema({
                    vol.Required("acknowledge_shadow", default=False): bool
                }),
            )
        if user_input.get("acknowledge_shadow") is not True:
            return self.async_show_form(
                step_id="user",
                data_schema=vol.Schema({
                    vol.Required("acknowledge_shadow", default=False): bool
                }),
                errors={"base": "consent_required"},
            )
        await self.async_set_unique_id("native_v1_shadow")
        self._abort_if_unique_id_configured()
        return self.async_create_entry(
            title="Heating AI V1+ — SHADOW preview",
            data={
                "shadow": True,
                "room": mapping["room"],
                "outside": mapping["outside"],
                "source_ids": {
                    role: entity_id for role, entity_id in SOURCE_HINTS.items()
                    if self.hass.states.get(entity_id) is not None
                },
            },
        )
