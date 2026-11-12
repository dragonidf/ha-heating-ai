"""One-time, read-only compatibility discovery of the running V1 controller."""
from __future__ import annotations

import voluptuous as vol
from pathlib import Path
from uuid import uuid4
from homeassistant import config_entries
from homeassistant.core import callback
from homeassistant.helpers import selector

from .const import DOMAIN, SOURCE_UI, SOURCE_HINTS
from .native_voltage import valid_policy


class HeatingAIV1Flow(config_entries.ConfigFlow, domain=DOMAIN):
    """No side effects: installs a diagnostic native SHADOW beside V1."""

    VERSION = 1

    @staticmethod
    @callback
    def async_get_options_flow(config_entry):
        return HeatingAIV1OptionsFlow(config_entry)

    async def async_step_user(self, user_input=None):
        return self.async_show_menu(step_id="user", menu_options=["prepared", "migrate", "preview"])

    async def async_step_prepared(self, user_input=None):
        """Select a private snapshot prepared on the shared HA mount."""
        from .production_migration import load_installation
        if self._async_current_entries():
            return self.async_abort(reason="single_instance_allowed")
        import re
        base = Path(self.hass.config.path("heating_ai_v1"))
        def discover():
            found = {}
            if base.is_dir():
                for root in base.iterdir():
                    if not re.fullmatch(r"[a-f0-9]{32}", root.name):
                        continue
                    try:
                        _, report = load_installation(root)
                    except (OSError, ValueError, KeyError):
                        continue
                    found[root.name] = report
            return found
        prepared = await self.hass.async_add_executor_job(discover)
        if not prepared:
            return self.async_abort(reason="prepared_snapshot_missing")
        if user_input is not None and user_input.get("installation_id") in prepared:
            self._installation_id = user_input["installation_id"]
            self._import_report = prepared[self._installation_id]
            return await self.async_step_activate()
        return self.async_show_form(step_id="prepared", data_schema=vol.Schema({
            vol.Required("installation_id"): selector.SelectSelector(selector.SelectSelectorConfig(
                options=[{"value":identity,"label":f"Модель {report['model_version']} · {report['files']} файлів"}
                         for identity, report in prepared.items()]))}))

    async def async_step_migrate(self, user_input=None):
        """Import the complete private V1 data bundle through HA's upload API."""
        if self._async_current_entries():
            return self.async_abort(reason="single_instance_allowed")
        errors = {}
        if user_input is not None:
            from homeassistant.components.file_upload import process_uploaded_file
            from .production_migration import install_bundle
            self._installation_id = uuid4().hex
            target = Path(self.hass.config.path("heating_ai_v1", self._installation_id))
            def migrate():
                with process_uploaded_file(self.hass, user_input["bundle"]) as path:
                    return install_bundle(path.read_bytes(), target)
            try:
                self._import_report = await self.hass.async_add_executor_job(migrate)
            except (OSError, ValueError, KeyError):
                errors["base"] = "invalid_migration_bundle"
            else:
                return await self.async_step_activate()
        return self.async_show_form(step_id="migrate", errors=errors,
            data_schema=vol.Schema({vol.Required("bundle"): selector.FileSelector(
                selector.FileSelectorConfig(accept=".zip,application/zip"))}))

    async def async_step_activate(self, user_input=None):
        from .production import source_writer_active
        errors = {}
        if user_input is not None:
            if user_input.get("legacy_stopped") is not True:
                errors["base"] = "legacy_still_active"
            elif source_writer_active(self.hass):
                errors["base"] = "legacy_still_active"
            else:
                await self.async_set_unique_id("native_v1_controller")
                self._abort_if_unique_id_configured()
                return self.async_create_entry(title="Heating AI · Опалення", data={
                    "runtime": "native_v1", "installation_id": self._installation_id,
                    "grid_presence_helper": SOURCE_HINTS["grid"],
                    "voltage_entities": [SOURCE_HINTS[f"voltage_l{i}"] for i in (1,2,3)],
                })
        return self.async_show_form(step_id="activate", errors=errors,
            description_placeholders={"model": str(self._import_report["model_version"]),
                                      "files": str(self._import_report["files"])},
            data_schema=vol.Schema({vol.Required("legacy_stopped", default=False): bool}))

    async def async_step_preview(self, user_input=None):
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
        known_roles = (
            "room","outside","return","weather","fan","target","lower",
            "upper","night_target","night_lower","night_upper",
            "start","end","sleep_start","sleep_end","strategy","mode",
            "armed","fan_control","fan_mode","fan_speed",
            "fan_night_limit","fan_intense_speed",
        )
        # Persist entity discovery ONCE; HA continues knowing its native
        # sensors even when the old AppDaemon sensor.heating_ui is removed.
        entity_map = {
            role: value for role in known_roles
            if type(value := mapping.get(role)) is str
            and len(value) <= 128 and value.count(".") == 1
            and " " not in value
        }
        declared = self.hass.states.get("sensor.heating_power_model")
        declared_mode = (
            declared.state
            if declared is not None and declared.state in (
                "fixed_3", "fixed_6", "fixed_9"
            ) else "unconfirmed"
        )
        await self.async_set_unique_id("native_v1_shadow")
        self._abort_if_unique_id_configured()
        return self.async_create_entry(
            title="Heating AI V1+ — SHADOW preview",
            data={
                "shadow": True,
                "room": mapping["room"],
                "outside": mapping["outside"],
                "entity_map": entity_map,
                "boiler_power_mode": declared_mode,
                "source_ids": {
                    role: entity_id for role, entity_id in SOURCE_HINTS.items()
                    if self.hass.states.get(entity_id) is not None
                },
            },
        )


class HeatingAIV1OptionsFlow(config_entries.OptionsFlow):
    """Configure only the read-only voltage qualification band."""

    def __init__(self, config_entry):
        self._entry = config_entry

    async def async_step_init(self, user_input=None):
        if user_input is not None:
            minimum = user_input.get("min_voltage_v")
            maximum = user_input.get("max_voltage_v")
            recovery = user_input.get("voltage_recovery_seconds", 30)
            if not valid_policy(minimum, maximum, recovery):
                return self.async_show_form(
                    step_id="init",
                    data_schema=self._schema(user_input),
                    errors={"base": "invalid_voltage_band"},
                )
            return self.async_create_entry(title="", data={
                "min_voltage_v": minimum,
                "max_voltage_v": maximum,
                "voltage_recovery_seconds": recovery,
            })
        return self.async_show_form(
            step_id="init",
            data_schema=self._schema(self._entry.options),
        )

    @staticmethod
    def _schema(current):
        return vol.Schema({
            vol.Required("min_voltage_v", default=current.get("min_voltage_v", 190)):
                selector.NumberSelector(selector.NumberSelectorConfig(
                    min=140, max=235, step=1,
                    mode=selector.NumberSelectorMode.BOX,
                    unit_of_measurement="V",
                )),
            vol.Required("max_voltage_v", default=current.get("max_voltage_v", 260)):
                selector.NumberSelector(selector.NumberSelectorConfig(
                    min=220, max=280, step=1,
                    mode=selector.NumberSelectorMode.BOX,
                    unit_of_measurement="V",
                )),
            vol.Required("voltage_recovery_seconds",
                         default=current.get("voltage_recovery_seconds", 30)):
                selector.NumberSelector(selector.NumberSelectorConfig(
                    min=30, max=900, step=30,
                    mode=selector.NumberSelectorMode.BOX,
                    unit_of_measurement="s",
                )),
        })
