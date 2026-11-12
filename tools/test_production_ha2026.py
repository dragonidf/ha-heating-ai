"""Boot the complete native V1 against real HA APIs and synthetic equipment."""
from __future__ import annotations

import asyncio
import json
from pathlib import Path
import sys
from tempfile import TemporaryDirectory
import time
import zipfile

from build_marketplace import build, ROOT


async def exercise(directory):
    from homeassistant.core import HomeAssistant
    from custom_components.heating_ai_legacy.critical_ledger import CriticalLedger
    from custom_components.heating_ai_legacy.production import NativeController
    from custom_components.heating_ai_legacy.native_controller.heating_model import DEFAULT_MODEL
    from custom_components.heating_ai_legacy.config_flow import HeatingAIV1OptionsFlow
    from types import SimpleNamespace

    hass = HomeAssistant(str(directory))
    cfg = json.loads((ROOT / "examples/heating_climate.yaml.example").read_text())["heating_climate_controller"]
    cfg.update(allow_actuation=True, data_dir=str(directory / "private"),
        ui_www_dir=str(directory / "www/heating-ai"), ui_url="/heating-ai-v1",
        grid_presence_helper="input_select.grid_presence",
        voltage_entities=["sensor.input_l1", "sensor.input_l2", "sensor.input_l3"])
    Path(cfg["data_dir"]).mkdir()
    state = dict(model=dict(DEFAULT_MODEL, version=6, validated=True), weather=[], energy_bins=[])
    Path(cfg["data_dir"], "state.json").write_text(json.dumps(state))
    for value in cfg.values():
        if isinstance(value, str) and "." in value and "/" not in value and " " not in value:
            hass.states.async_set(value, "0")
    for role, value in dict(mode="Спостереження", armed="off", room=21.8,
        outside=5, return_=25, fan_control="off", fan_mode="Вимкнено",
        start="23:00:00", end="07:00:00", strategy="Тільки в дозволені години",
        sleep_start="23:00:00", sleep_end="07:00:00",
        night_target=21.5, night_lower=21, night_upper=22,
        target=22, lower=21.5, upper=22.5, grid="on", connection="on",
        grid_voltage=230, energy=125, fan="off").items():
        hass.states.async_set(cfg["return" if role == "return_" else role], value)
    hass.states.async_set(cfg["thermostat"], "heat", {
        "current_temperature":22, "temperature":24, "min_temp":5,
        "max_temp":35, "target_temp_step":.5})
    hass.states.async_set(cfg["grid_presence_helper"], "Наявна мережа")
    for entity in cfg["voltage_entities"]:
        hass.states.async_set(entity,230)
    commands = []
    async def hvac(call):
        commands.append(dict(call.data))
        current = hass.states.get(cfg["thermostat"])
        hass.states.async_set(cfg["thermostat"], call.data["hvac_mode"], dict(current.attributes))
    hass.services.async_register("climate", "set_hvac_mode", hvac)
    hass.services.async_register("persistent_notification", "create", lambda call: None)
    hass.services.async_register("persistent_notification", "dismiss", lambda call: None)
    controller = NativeController(hass,cfg)
    # Production never receives command authority without the same durable,
    # boot-fenced journal used by the config-entry runtime.  Keep the smoke
    # test on that real safety path instead of injecting a permissive fake.
    journal_dir = directory / "command-journal"
    journal_dir.mkdir()
    owner_id = "synthetic-ha-2026-smoke"
    ledger = CriticalLedger(
        journal_dir,
        owner_id=owner_id,
        boot_id="bootstrap-synthetic-ha-2026-smoke",
        owner_epoch=0,
    )
    controller.attach_command_ledger(
        ledger,
        ledger.claim_boot(
            "synthetic-ha-2026-smoke-boot", expected_owner_id=owner_id
        ),
    )
    try:
        # Real OptionsFlow APIs reject assigning the platform's read-only
        # config_entry property; the options constructor must remain portable.
        options = HeatingAIV1OptionsFlow(SimpleNamespace(options={}))
        assert (await options.async_step_init())["type"] == "form"
        await controller.async_start(writer=True)
        assert controller.runtime_ready
        assert controller.model["version"] == 6
        assert hass.states.get("sensor.heating_ui").attributes["release_verified"] is True
        assert hass.states.get("sensor.heating_ui").attributes["runtime"] == "native_v1"
        await controller._dispatch(controller.tick,{})
        assert controller.memory.get("last_successful_tick"), controller.memory.get("control_incident")
        assert not commands, "Manual observation must not actuate equipment at startup"
        hass.states.async_set(cfg["mode"], "Автоматичне керування")
        hass.states.async_set(cfg["armed"], "on")
        hass.states.async_set(cfg["voltage_entities"][1],196)
        await controller._dispatch(controller.apply_action,"heat",False)
        assert commands[-1]["hvac_mode"] == "off"
        assert "L2" in hass.states.get("sensor.heating_controller").attributes["reason"]
        try:
            await asyncio.to_thread(controller.call_service,"climate/set_hvac_mode",
                                    entity_id=cfg["thermostat"],hvac_mode="auto")
        except PermissionError:
            pass
        else:
            raise AssertionError("Voltage interlock allowed AUTO")
        hass.states.async_set(cfg["voltage_entities"][1],230)
        controller.async_voltage_status()
        controller.voltage_guard.stable_since = time.time()-31
        assert controller.async_voltage_status()["voltage_qualified"]
        await asyncio.to_thread(controller.call_service,"climate/set_hvac_mode",
                                entity_id=cfg["thermostat"],hvac_mode="heat")
        assert commands[-1]["hvac_mode"] == "heat"
    finally:
        await controller.async_close()
        assert not controller._handles
        assert not controller._writer
        await hass.async_stop()
    print("PASS: complete V1 startup on real HA, verified native bundle, preserved model, manual pause, physical-service boundary and voltage recovery (synthetic services only)")


def main():
    with TemporaryDirectory() as temp:
        root = Path(temp)
        build(root / "controller.zip")
        target = root / "isolated/custom_components/heating_ai_legacy"
        target.mkdir(parents=True)
        with zipfile.ZipFile(root / "controller.zip") as archive:
            archive.extractall(target)
        sys.path.insert(0, str(root / "isolated"))
        (root / "ha").mkdir()
        asyncio.run(exercise(root / "ha"))


if __name__ == "__main__":
    main()
