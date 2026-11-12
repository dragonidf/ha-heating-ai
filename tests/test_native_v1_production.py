"""Native V1 handoff, byte-preserving import and real command interlocks."""
import asyncio
from concurrent.futures import Future
from contextlib import contextmanager
from datetime import datetime, timezone
from hashlib import sha256
import importlib
from io import BytesIO
import json
import runpy
from pathlib import Path
import sys
from tempfile import TemporaryDirectory
from types import ModuleType, SimpleNamespace
import unittest
from unittest.mock import AsyncMock, Mock, patch
import zipfile

from tools.build_marketplace import build


def module(name, **values):
    result = ModuleType(name)
    result.__dict__.update(values)
    return result


def run_callback(loop, function, *args):
    future = Future()
    def run():
        try:
            future.set_result(function(*args))
        except BaseException as exc:
            future.set_exception(exc)
    loop.call_soon_threadsafe(run)
    return future


@contextmanager
def package(directory):
    name = "v1_production_fixture"
    pkg = module(name)
    pkg.__path__ = [str(directory)]
    stubs = {
        name: pkg,
        "homeassistant": module("homeassistant"),
        "homeassistant.core": module("homeassistant.core", callback=lambda fn: fn,
            Context=lambda: SimpleNamespace(id="native-fixture-context")),
        "homeassistant.components": module("homeassistant.components",
            frontend=SimpleNamespace(async_remove_panel=Mock()),
            panel_custom=SimpleNamespace(async_register_panel=AsyncMock())),
        "homeassistant.components.http.server": module("homeassistant.components.http.server", StaticPathConfig=Mock()),
        "homeassistant.components.weather.const": module("homeassistant.components.weather.const", DATA_COMPONENT="weather"),
        "homeassistant.exceptions": module("homeassistant.exceptions", ConfigEntryNotReady=RuntimeError),
        "homeassistant.helpers.event": module("homeassistant.helpers.event",
            async_track_state_change_event=lambda hass, ids, cb: hass.listen(ids, cb)),
        "homeassistant.helpers.template": module("homeassistant.helpers.template", Template=Mock()),
        "homeassistant.util.async_": module("homeassistant.util.async_", run_callback_threadsafe=run_callback),
    }
    with patch.dict(sys.modules, stubs):
        try:
            yield importlib.import_module(name+".production"), importlib.import_module(name+".production_migration")
        finally:
            for key in tuple(sys.modules):
                if key == name or key.startswith(name+"."):
                    sys.modules.pop(key, None)


class State:
    def __init__(self, entity_id, state, attributes=None, at=1000):
        self.entity_id, self.state = entity_id, str(state)
        self.attributes = attributes or {}
        self.last_reported = self.last_updated = datetime.fromtimestamp(at, timezone.utc)
        self.context = SimpleNamespace(id="old-writer")

    def as_dict(self):
        return dict(entity_id=self.entity_id, state=self.state, attributes=self.attributes,
                    last_updated=self.last_updated.isoformat(), last_changed=self.last_updated.isoformat())

    def replace(self, value, *, context):
        self.state = str(value)
        self.context = SimpleNamespace(id=context)
        self.last_updated = self.last_reported = self.last_updated.replace(microsecond=self.last_updated.microsecond+1)


class FakeHA:
    def __init__(self):
        self.loop = asyncio.get_running_loop()
        self.rows, self.listeners = {}, []
        self.states = SimpleNamespace(get=self.rows.get, async_all=lambda: list(self.rows.values()),
                                      async_set=self.set_state)
        self.services = SimpleNamespace(async_call=AsyncMock(side_effect=self.service))

    def set_state(self, entity, value, attributes=None, context=None):
        row = State(entity, value, attributes)
        row.context = context or row.context
        self.rows[entity] = row

    async def service(self, domain, service, data, **kwargs):
        if domain == "climate" and service == "set_hvac_mode":
            self.rows[data["entity_id"]].state = data["hvac_mode"]

    def listen(self, ids, callback):
        pair = (ids, callback)
        self.listeners.append(pair)
        return lambda: self.listeners.remove(pair)

    def async_create_task(self, coro, name=None):
        return asyncio.create_task(coro, name=name)

    async def async_add_executor_job(self, function, *args):
        return await asyncio.to_thread(function, *args)


class FakeLedger:
    def __init__(self):
        self.calls=[]

    def prepare(self, command_id, action, request, **identity):
        self.calls.append(("PREPARED",command_id,action,request))
        return {"created":True,"revision":identity["expected_revision"]+1}

    def record_sent(self, command_id, **identity):
        self.calls.append(("SENT",command_id))
        return identity["expected_revision"]+1

    def record_ha_echo(self, command_id, **identity):
        self.calls.append(("HA_ECHO",command_id))
        return identity["expected_revision"]+1

    def complete_safe_stop(self, command_id, **identity):
        self.calls.append(("UNFENCED",command_id))
        return identity["expected_revision"]+1


class ProductionTests(unittest.IsolatedAsyncioTestCase):
    @classmethod
    def setUpClass(cls):
        cls.temp = TemporaryDirectory()
        cls.root = Path(cls.temp.name)
        build(cls.root / "package.zip")
        with zipfile.ZipFile(cls.root / "package.zip") as archive:
            archive.extractall(cls.root / "package")

    @classmethod
    def tearDownClass(cls):
        cls.temp.cleanup()

    def config(self):
        return dict(thermostat="climate.boiler", armed="input_boolean.armed",
                    mode="input_select.mode", allow_actuation=True,
                    fan="fan.circulation", fan_control="input_boolean.fan_control",
                    fan_mode="input_select.fan_mode",
                    grid_presence_helper="sensor.grid", voltage_entities=["sensor.l1", "sensor.l2", "sensor.l3"])

    def controller(self, component):
        hass = FakeHA()
        cfg = self.config()
        for key, value in (("climate.boiler", "heat"), ("input_boolean.armed", "on"),
                           ("input_select.mode", "Автоматичне керування"),
                           ("fan.circulation", "off"), ("input_boolean.fan_control", "on"),
                           ("input_select.fan_mode", "Авто"),
                           ("sensor.grid", "Наявна мережа"), ("sensor.l1", 230),
                           ("sensor.l2", 230), ("sensor.l3", 230)):
            hass.rows[key] = State(key, value, {"current_temperature": 22, "temperature": 24})
        app = component.NativeController(hass, cfg)
        app.cfg, app.memory, app.weather = cfg, {}, []
        app.model = {"version": 6}
        app.statefile = self.root / "working-state.json"
        app._running = app._writer = True
        app.next_compute = 0
        app.attach_command_ledger(FakeLedger(),dict(revision=0,owner_id="fixture-owner",
            boot_id="fixture-boot",owner_epoch=1,fenced=False))
        return hass, app

    async def test_voltage_trip_bypasses_minimum_on_and_blocks_auto_dispatch(self):
        with package(self.root / "package") as (component, _):
            for voltage in (196, 255):
                hass, app = self.controller(component)
                hass.rows["sensor.l2"].state = str(voltage)
                app.memory.update(last_command="heat", command_at=995, owned=True)
                app.memory["plan_execution"] = {"plan_id":"scheduled-heat","status":"intended"}
                with patch.object(component.time, "time", return_value=1000):
                    await asyncio.to_thread(app.apply_action, "heat", False)
                    self.assertEqual(hass.rows["climate.boiler"].state, "off")
                    self.assertIn("L2", hass.rows["sensor.heating_controller"].attributes["reason"])
                    self.assertEqual(app.memory["last_command"], "off")
                    self.assertEqual(app.memory["plan_execution"]["status"], "blocked_voltage")
                    with self.assertRaises(PermissionError):
                        await asyncio.to_thread(app.call_service, "climate/set_hvac_mode",
                                                entity_id="climate.boiler", hvac_mode="auto")
                    self.assertEqual(hass.rows["climate.boiler"].state, "off")

    async def test_voltage_events_do_not_rewrite_full_history_on_every_phase_report(self):
        with package(self.root / "package") as (component, _):
            hass, app = self.controller(component)
            hass.rows["sensor.l2"].state = "196"
            with patch.object(app, "save", wraps=app.save) as saved:
                with patch.object(component.time, "time", return_value=1000):
                    await asyncio.to_thread(app.apply_voltage_stop)
                self.assertEqual(saved.call_count,1)
                for stamp,voltage in ((1005,"195"),(1010,"194"),(1015,"195.5")):
                    hass.rows["sensor.l2"].state=voltage
                    with patch.object(component.time, "time", return_value=stamp):
                        await asyncio.to_thread(app.apply_voltage_stop)
                self.assertEqual(saved.call_count,1)
                self.assertEqual(hass.services.async_call.await_count,1)

    async def test_stabilized_voltage_delegates_unchanged_v1_and_keeps_manual_pause(self):
        with package(self.root / "package") as (component, _):
            hass, app = self.controller(component)
            parent = component.HeatingClimate
            with patch.object(parent, "apply_action") as original:
                with patch.object(component.time, "time", return_value=1000):
                    await asyncio.to_thread(app.apply_action, "heat", False)
                original.assert_not_called()
                with patch.object(component.time, "time", return_value=1030):
                    await asyncio.to_thread(app.apply_action, "heat", False)
                original.assert_called_once_with("heat", False)
                self.assertNotIn("voltage_hold", app.memory)
                hass.rows["input_boolean.armed"].state = "off"
                hass.rows["sensor.l2"].state = "190"
                hass.services.async_call.reset_mock()
                with patch.object(component.time, "time", return_value=1040):
                    await asyncio.to_thread(app.apply_action, "observe", False)
                original.assert_called_with("observe", False)
                hass.services.async_call.assert_not_called()

    async def test_closed_writer_rejects_commands_and_cancels_delayed_callbacks(self):
        with package(self.root / "package") as (component, _):
            hass, app = self.controller(component)
            delayed = Mock()
            await asyncio.to_thread(app.run_in, delayed, .02)
            await app.async_close()
            await asyncio.sleep(.04)
            delayed.assert_not_called()
            with self.assertRaises(PermissionError):
                await asyncio.to_thread(app.call_service, "fan/turn_off", entity_id="climate.boiler")
            hass.services.async_call.assert_not_called()
            self.assertFalse(app._handles)

    async def test_manual_auto_between_setpoint_and_heat_rejects_old_heat_without_overriding_auto(self):
        with package(self.root / "package") as (component, _):
            hass, app = self.controller(component)
            manual = app.async_manual_snapshot()
            app.memory.update(native_manual_epoch=1,
                              native_manual_signature=manual["signature"])
            app._dispatch_manual_epoch = 1
            app._dispatch_manual_signature = manual["signature"]
            app._dispatch_action = "heat"
            with patch.object(component.time, "time", return_value=1000):
                app.async_voltage_status()
            with patch.object(component.time, "time", return_value=1030):
                self.assertTrue(app.async_voltage_status()["voltage_qualified"])
            with patch.object(component.time, "time", return_value=1031):
                await asyncio.to_thread(app.call_service, "climate/set_temperature",
                                        entity_id="climate.boiler", temperature=24)
                # User selects native AUTO while the legacy worker still has HEAT.
                hass.rows["input_select.mode"].state = "Штатний auto"
                hass.rows["climate.boiler"].state = "auto"
            with patch.object(component.time, "time", return_value=1032):
                with self.assertRaises(PermissionError):
                    await asyncio.to_thread(app.call_service, "climate/set_hvac_mode",
                                            entity_id="climate.boiler", hvac_mode="heat")
                self.assertEqual(hass.rows["climate.boiler"].state, "auto")
                self.assertEqual(hass.services.async_call.await_count, 1)

    async def test_disarmed_dispatch_rejects_positive_setpoint_but_allows_lowering_target(self):
        with package(self.root / "package") as (component, _):
            hass, app = self.controller(component)
            manual = app.async_manual_snapshot()
            app.memory.update(native_manual_epoch=1,
                              native_manual_signature=manual["signature"])
            app._dispatch_manual_epoch = 1
            app._dispatch_manual_signature = manual["signature"]
            app._dispatch_action = "heat"
            with patch.object(component.time, "time", return_value=1000):
                app.async_voltage_status()
            hass.rows["input_boolean.armed"].state = "off"
            with patch.object(component.time, "time", return_value=1030):
                with self.assertRaises(PermissionError):
                    await asyncio.to_thread(app.call_service, "climate/set_temperature",
                                            entity_id="climate.boiler", temperature=24)
            with patch.object(component.time, "time", return_value=1031):
                await asyncio.to_thread(app.call_service, "climate/set_temperature",
                                        entity_id="climate.boiler", temperature=20)
                self.assertEqual(hass.services.async_call.await_count, 1)

    async def test_change_back_to_automatic_still_invalidates_stale_heat_dispatch(self):
        with package(self.root / "package") as (component, _):
            hass, app = self.controller(component)
            app.memory["native_manual_epoch"] = 7
            app.memory["native_manual_signature"] = app.async_manual_snapshot()["signature"]
            app._dispatch_manual_epoch = 7
            app._dispatch_manual_signature = app.async_manual_snapshot()["signature"]
            app._dispatch_action = "heat"
            with patch.object(component.time, "time", return_value=1000):
                app.async_voltage_status()
            hass.rows["input_select.mode"].replace("Штатний auto", context="manual-auto")
            hass.rows["input_select.mode"].replace("Автоматичне керування", context="manual-back")
            with patch.object(component.time, "time", return_value=1030):
                with self.assertRaises(PermissionError):
                    await asyncio.to_thread(app.call_service, "climate/set_hvac_mode",
                                            entity_id="climate.boiler", hvac_mode="heat")
            hass.services.async_call.assert_not_called()

    async def test_blocked_planner_cannot_send_after_manual_round_trip(self):
        with package(self.root / "package") as (component, _):
            hass, app = self.controller(component)
            parent = component.HeatingClimate
            def stale_plan(action, once):
                hass.rows["input_select.mode"].replace("Штатний auto", context="manual-auto")
                hass.rows["input_select.mode"].replace("Автоматичне керування", context="manual-back")
                app.call_service("climate/set_hvac_mode", entity_id="climate.boiler", hvac_mode="heat")
            with patch.object(component.time, "time", return_value=1000):
                app.async_voltage_status()
            with patch.object(component.time, "time", return_value=1030), \
                    patch.object(parent, "apply_action", side_effect=stale_plan):
                with self.assertRaises(PermissionError):
                    await asyncio.to_thread(app.apply_action, "heat", False)
            hass.services.async_call.assert_not_called()
            self.assertGreaterEqual(app.memory["native_manual_epoch"], 1)

    async def test_callbacks_are_serial_and_errors_from_services_are_observable(self):
        with package(self.root / "package") as (component, _):
            hass, app = self.controller(component)
            events = []
            async def work(value):
                events.append((value, "start"))
                await asyncio.sleep(.01)
                events.append((value, "end"))
            await asyncio.gather(app._dispatch(work, 1), app._dispatch(work, 2))
            self.assertEqual(events, [(1,"start"),(1,"end"),(2,"start"),(2,"end")])
            hass.services.async_call.side_effect = RuntimeError("HA refused service")
            with self.assertRaisesRegex(RuntimeError, "HA refused"):
                await asyncio.to_thread(app.call_service, "persistent_notification/create", message="test")

    async def test_failed_service_cannot_later_borrow_a_manual_state_echo(self):
        with package(self.root / "package") as (component, _):
            hass, app = self.controller(component)
            hass.services.async_call.side_effect = RuntimeError("HA refused service")
            with self.assertRaisesRegex(RuntimeError, "HA refused"):
                await asyncio.to_thread(app.call_service, "climate/set_hvac_mode",
                                        entity_id="climate.boiler", hvac_mode="off")
            self.assertEqual(app.memory["native_command_evidence"]["stage"], "SENT")
            self.assertFalse(app._pending_command_evidence)
            hass.rows["climate.boiler"].replace("off", context="manual-user")
            app.command_echo_changed()
            self.assertNotIn("HA_ECHO", [call[0] for call in app.command_ledger.calls])

    async def test_equipment_gateway_records_prepared_sent_echo_and_safe_stop(self):
        with package(self.root / "package") as (component, _):
            hass, app = self.controller(component)
            await asyncio.to_thread(app.call_service, "climate/set_hvac_mode",
                                    entity_id="climate.boiler", hvac_mode="off")
            stages=[call[0] for call in app.command_ledger.calls]
            self.assertEqual(stages,["PREPARED","SENT","HA_ECHO","UNFENCED"])
            self.assertEqual(app.command_ledger.calls[0][2],"STOP")
            self.assertFalse(app.command_ledger_status["fenced"])

    async def test_raw_settings_event_is_not_registered_in_native_production(self):
        with package(self.root / "package") as (component, _):
            hass, app = self.controller(component)
            token = await asyncio.to_thread(app.listen_event, app.system_settings_changed,
                                            "heating_system_settings")
            self.assertIsNone(token)
            self.assertFalse(hass.listeners)

    async def test_external_fan_change_between_plan_and_dispatch_is_not_overwritten(self):
        with package(self.root / "package") as (component, _):
            hass, app = self.controller(component)
            fan = app.async_fan_snapshot()
            app._fan_dispatch_signature = fan["permissions"]
            app._fan_dispatch_device_signature = fan["device"]
            app._fan_dispatch_epoch = 0
            hass.rows["fan.circulation"].replace("on", context="external-manual")
            hass.rows["fan.circulation"].attributes["percentage"] = 72
            with self.assertRaises(PermissionError):
                await asyncio.to_thread(app.call_service, "fan/turn_off",
                                        entity_id="fan.circulation")
            hass.services.async_call.assert_not_called()

    async def test_persisted_external_fan_hold_clears_only_on_explicit_fan_choice(self):
        with package(self.root / "package") as (component, _):
            hass, app = self.controller(component)
            app.memory["native_fan_device_signature"] = app.async_fan_snapshot()["device"]
            hass.rows["fan.circulation"].replace("on", context="external-manual")
            await asyncio.to_thread(app.fan_state_changed)
            self.assertTrue(app.memory["native_fan_manual_hold"])
            await asyncio.to_thread(app.fan_permission_changed)
            self.assertNotIn("native_fan_manual_hold",app.memory)
            fan = app.async_fan_snapshot()
            app._fan_dispatch_signature = fan["permissions"]
            app._fan_dispatch_device_signature = fan["device"]
            app._fan_dispatch_epoch = app.memory["native_fan_manual_epoch"]
            await asyncio.to_thread(app.call_service, "fan/turn_off",
                                    entity_id="fan.circulation")
            self.assertEqual(hass.services.async_call.await_count,1)

    async def test_source_heartbeat_is_not_mistaken_for_native_ownership(self):
        with package(self.root / "package") as (component, _):
            hass = FakeHA()
            hass.rows["sensor.heating_heartbeat"] = State("sensor.heating_heartbeat", "1000")
            self.assertTrue(component.source_writer_active(hass, 1020))
            self.assertFalse(component.source_writer_active(hass, 1050))
            hass.rows["sensor.heating_heartbeat"].attributes["runtime"] = "native_v1"
            self.assertFalse(component.source_writer_active(hass, 1020))


class BundleTests(unittest.TestCase):
    def test_shared_mount_export_requires_stopped_app_and_preserves_all_history(self):
        with TemporaryDirectory() as directory:
            root=Path(directory)
            stubs={name:module(name) for name in ("appdaemon","appdaemon.plugins","appdaemon.plugins.hass")}
            stubs["appdaemon.plugins.hass.hassapi"]=module("appdaemon.plugins.hass.hassapi",Hass=object)
            with patch.dict(sys.modules,stubs):
                exporter=runpy.run_path(str(Path(__file__).resolve().parents[1]/"tools/native_migration_exporter.py"))
            source=root/"source";source.mkdir();(source/"decision_history").mkdir()
            (source/"state.json").write_text('{"model":{"version":6,"validated":true},"kws_energy":{"measured_kwh":123.4}}')
            (source/"telemetry.jsonl").write_text('{"ts":1,"power":3}\n')
            (source/"decision_history/day.jsonl").write_text('{"reason":"unchanged"}\n')
            (source/"decision_history/forecast.json.gz").write_bytes(b"opaque-original-forecast")
            config=root/"config.json";config.write_text('{"v1":{"disable":false}}')
            before={str(p.relative_to(source)):p.read_bytes() for p in source.rglob('*') if p.is_file()}
            call=exporter["export_snapshot"]
            with self.assertRaises(ValueError):
                call(config,"v1",source,root/"native",watchdog_disabled=True)
            config.write_text('{"v1":{"disable":true}}')
            with self.assertRaises(ValueError):
                call(config,"v1",source,root/"native",watchdog_disabled=False)
            report=call(config,"v1",source,root/"native",watchdog_disabled=True)
            self.assertEqual(report["files"],4)
            self.assertTrue(report["complete"])
            self.assertEqual({str(p.relative_to(root/"native/data")):p.read_bytes()
                for p in (root/"native/data").rglob('*') if p.is_file()},before)
            self.assertEqual({str(p.relative_to(source)):p.read_bytes()
                for p in source.rglob('*') if p.is_file()},before)
            with self.assertRaises(FileExistsError):
                call(config,"v1",source,root/"native",watchdog_disabled=True)

    def test_migration_preserves_model_counters_history_and_original_bytes(self):
        with TemporaryDirectory() as directory:
            path = Path(directory)
            build(path / "package.zip")
            with zipfile.ZipFile(path / "package.zip") as archive:
                archive.extractall(path / "package")
            with package(path / "package") as (_, migration):
                state = {"model": dict(floor_air=.03, room_loss=.01, floor_decay=.12,
                    heat_gain=.5, fire_gain=.025, version=6, validated=True),
                    "energy_last":{"ts":1000,"total":123.45}, "energy_bins":[{"kwh":12.3}],
                    "temperature_history":{"day":{"actual":[[1,22]]}}, "kws_energy":{"origin_kwh":100,"measured_kwh":23.45}}
                raw = json.dumps(state, indent=3).encode()
                cfg = json.dumps({role:"sensor."+role for role in migration.REQUIRED_ROLES}).encode()
                handoff = json.dumps(dict(legacy_disabled=True, watchdog_disabled=True,
                    state_sha256=sha256(raw).hexdigest(), config_sha256=sha256(cfg).hexdigest())).encode()
                def bundle(extra=None):
                    out=BytesIO()
                    with zipfile.ZipFile(out,"w") as z:
                        for name,data in {"config.json":cfg,"handoff.json":handoff,
                            "data/state.json":raw,"data/telemetry.jsonl":b'{"ts":1000}\n',**(extra or {})}.items():
                            z.writestr(name,data)
                    return out.getvalue()
                report=migration.install_bundle(bundle(),path/"private")
                self.assertTrue(report["complete"])
                self.assertEqual((path/"private/data/state.json").read_bytes(),raw)
                self.assertEqual((path/"private/original-state.json").read_bytes(),raw)
                self.assertEqual(json.loads((path/"private/data/state.json").read_bytes()),state)
                self.assertEqual(migration.load_installation(path/"private")[1],report)
                with self.assertRaises(FileExistsError):
                    migration.install_bundle(bundle(),path/"private")
                for bad in ("../escape.json","data/../escape.json","data/executable.py"):
                    with self.assertRaises(ValueError):
                        migration.inspect_bundle(bundle({bad:b"untrusted"}))
                (path/"private/original-state.json").write_text("{}")
                with self.assertRaises(ValueError):
                    migration.load_installation(path/"private")
