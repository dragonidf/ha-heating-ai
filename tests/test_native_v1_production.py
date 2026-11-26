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
        "homeassistant.const": module("homeassistant.const",
            EVENT_HOMEASSISTANT_STOP="homeassistant_stop"),
        "homeassistant.components": module("homeassistant.components",
            frontend=SimpleNamespace(async_remove_panel=Mock()),
            panel_custom=SimpleNamespace(async_register_panel=AsyncMock())),
        "homeassistant.components.http.server": module("homeassistant.components.http.server", StaticPathConfig=Mock()),
        "homeassistant.components.weather.const": module("homeassistant.components.weather.const", DATA_COMPONENT="weather"),
        "homeassistant.exceptions": module("homeassistant.exceptions", ConfigEntryNotReady=RuntimeError),
        "homeassistant.helpers": module("homeassistant.helpers", entity_registry=SimpleNamespace(
            async_get=lambda hass: SimpleNamespace(async_get=lambda entity: None))),
        "homeassistant.helpers.dispatcher": module("homeassistant.helpers.dispatcher",
            async_dispatcher_connect=Mock()),
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
        self.bus = SimpleNamespace(async_listen=self.listen)
        self.states = SimpleNamespace(get=self.rows.get, async_all=lambda: list(self.rows.values()),
                                      async_set=self.set_state)
        self.services = SimpleNamespace(async_call=AsyncMock(side_effect=self.service))

    def set_state(self, entity, value, attributes=None, context=None):
        row = State(entity, value, attributes)
        row.context = context or row.context
        self.rows[entity] = row

    async def service(self, domain, service, data, **kwargs):
        if domain == "climate" and service == "set_hvac_mode":
            import time
            row = self.rows[data["entity_id"]]
            row.state = data["hvac_mode"]
            row.context = kwargs.get("context", row.context)
            row.last_updated = row.last_reported = datetime.fromtimestamp(time.time(), timezone.utc)

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

    def fence(self, **identity):
        self.identity = dict(revision=identity['expected_revision']+1,
            owner_id=identity['owner_id'], boot_id=identity['boot_id'],
            owner_epoch=identity['owner_epoch']+1, fenced=True)

    def status(self):
        return self.identity

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

    async def test_outage_zero_heat_needs_exact_topology_and_no_conflicting_evidence(self):
        with package(self.root / "package") as (component, _):
            hass,app=self.controller(component)
            app.cfg.update(power_accounting_method="kws",kws_relay="switch.kws")
            app.planning_grid_state="off"
            hass.rows["sensor.grid"].state="Відсутня мережа"
            app.voltage_status={"phases_v":[None,None,None]}
            app.power_quality="unknown"
            self.assertFalse(app._learning_proven_zero_heat(None))
            app.cfg["boiler_outage_isolation_relay"]="switch.kws"
            self.assertTrue(app._learning_proven_zero_heat(None))
            self.assertFalse(app._learning_proven_zero_heat(3.))
            app.voltage_status={"phases_v":[230.,None,None]}
            self.assertFalse(app._learning_proven_zero_heat(None))
            app.voltage_status={"phases_v":[None,None,None]}
            hass.rows["sensor.grid"].state="Наявна мережа"
            self.assertFalse(app._learning_proven_zero_heat(None))
            hass.rows["sensor.grid"].state="Відсутня мережа"
            app.planning_grid_state="unknown"
            self.assertFalse(app._learning_proven_zero_heat(None))
            app.planning_grid_state="off"
            app.cfg["kws_relay"]="switch.other"
            self.assertFalse(app._learning_proven_zero_heat(None))
            app.cfg["kws_relay"]="switch.kws"
            with patch.object(component.time,"time",return_value=1000.):
                self.assertTrue(app._capture_sample_power(None))
            with patch.object(component.time,"time",return_value=1015.):
                self.assertFalse(app._capture_sample_power(None))
            self.assertEqual(app._learning_zero_seconds,15.)
            self.assertEqual(app._learning_kw_seconds,0.)
            self.assertIsNone(app.measured_sample_power(1000.,1015.))
            with patch.object(component.time,"time",return_value=1070.):
                self.assertTrue(app._capture_sample_power(None))
            self.assertEqual(app._learning_zero_seconds,15.)
            hass.services.async_call.assert_not_called()

    async def test_solar_risk_candidate_is_bound_to_current_context_and_persisted(self):
        import time
        with package(self.root / "package") as (component, _):
            hass,app=self.controller(component)
            app.cfg["boiler_power_mode"]="fixed_6"
            app.memory["equipment_version"]=1
            app.climate_tz=timezone.utc
            app._learning_eligible=Mock(return_value=True)
            issued=dict(model=dict(app.model),equipment=1,generation=app._learning_generation,
                        solar_ventilation={},started=time.monotonic())
            outcome={"solar":dict(status="holdout_passed",risk_eligible=True,coefficient=.5,
                identity=[6,1,"fixed_6"],holdout_end=1000.,holdout_days=2,holdout_windows=8)}
            app.memory["ventilation_model"]={"validated":False,"reason":"changed"}
            app._store_solar_risk_candidate(issued,outcome)
            self.assertEqual(app.active_solar_risk_certificate(),{})
            app.memory["ventilation_model"]={}
            with patch.object(app,"save",side_effect=OSError):
                app._store_solar_risk_candidate(issued,outcome)
            self.assertEqual(app.active_solar_risk_certificate(),{})
            app._store_solar_risk_candidate(issued,outcome)
            active=app.active_solar_risk_certificate()
            self.assertEqual(active["coefficient"],.5)
            self.assertFalse(active["comfort_credit"])
            self.assertEqual(json.loads(app.statefile.read_text())["online_learning"]["solar_risk_candidate"],active)
            app.memory["equipment_version"]=2
            self.assertEqual(app.active_solar_risk_certificate(),{})
            app.memory["equipment_version"]=1
            app.cfg["boiler_power_mode"]="fixed_9"
            self.assertEqual(app.active_solar_risk_certificate(),{})
            app.cfg["boiler_power_mode"]="fixed_6"
            app.model={"version":7}
            self.assertEqual(app.active_solar_risk_certificate(),{})
            self.assertEqual(app.command_ledger.calls,[])
            hass.services.async_call.assert_not_called()

    async def test_solar_credit_is_promoted_once_and_rolled_back_atomically(self):
        import time
        from copy import deepcopy
        with package(self.root / "package") as (component, _):
            hass,app=self.controller(component)
            app.climate_tz=timezone.utc
            app.cfg["boiler_power_mode"]="fixed_6"
            app.memory["equipment_version"]=1
            app._learning_eligible=Mock(return_value=True)
            risk=dict(identity=[6,1,"fixed_6"],status="holdout_passed",coefficient=.5,
                      comfort_credit=False,stored_at=1000.,holdout_end=900.,ventilation_model={})
            app.memory["online_learning"]={"solar_risk_candidate":deepcopy(risk)}
            issued=dict(model=dict(app.model),equipment=1,generation=app._learning_generation,
                        solar_ventilation={},solar_risk=deepcopy(risk),started=time.monotonic())
            report=dict(status="passed",identity=risk["identity"],coefficient=.5,issued_after=1000.)
            app._update_solar_credit(issued,{"solar_prospective":{**report,"issued_after":999.}})
            self.assertFalse(app.active_solar_risk_certificate()["comfort_credit"])
            with patch.object(app,"save",side_effect=OSError):
                app._update_solar_credit(issued,{"solar_prospective":report})
            self.assertFalse(app.active_solar_risk_certificate()["comfort_credit"])
            app._update_solar_credit(issued,{"solar_prospective":report})
            active=app.active_solar_risk_certificate()
            self.assertTrue(active["comfort_credit"])
            self.assertEqual(active,json.loads(app.statefile.read_text())["online_learning"]["solar_risk_candidate"])
            issued["solar_risk"]=deepcopy(active)
            rollback=dict(status="rollback_required",identity=risk["identity"],coefficient=.5,
                          issued_after=active["nominal_at"],rollback_reason="forecast_quality_regression")
            app._learning_eligible=Mock(return_value=False)
            app._update_solar_credit(issued,{"solar_prospective":rollback})
            revoked=app.active_solar_risk_certificate()
            self.assertFalse(revoked["comfort_credit"])
            self.assertGreater(revoked["nominal_frozen_until"],revoked["nominal_revoked_at"])
            issued["solar_risk"]=deepcopy(revoked)
            app._learning_eligible=Mock(return_value=True)
            app._update_solar_credit(issued,{"solar_prospective":report})
            self.assertFalse(app.active_solar_risk_certificate()["comfort_credit"])
            app.memory["online_learning"]["evidence_after"]=1001.
            self.assertEqual(app.active_solar_risk_certificate(),{})
            self.assertEqual(app.command_ledger.calls,[])
            hass.services.async_call.assert_not_called()

    async def test_single_scheduled_learning_job_keeps_v6_without_evidence(self):
        with package(self.root / "package") as (component, _):
            _, app = self.controller(component)
            app.directory = self.root
            app.now = lambda: datetime(2026,11,1,tzinfo=timezone.utc)
            app.submit_to_executor = Mock()
            app._learning_rows.append({"ts":1.})
            with patch.object(component.HeatingClimate,"daily_learn") as legacy:
                app.daily_learn()
                app.daily_learn()
                legacy.assert_not_called()
            app.submit_to_executor.assert_called_once()
            function,path,*_ = app.submit_to_executor.call_args.args
            self.assertEqual(function.__name__,"run_learning")
            self.assertEqual(path.name,"online_learning_v1.json")
            app._learning_finished({"job_id":app._learning_job["id"],
                                    "result":{"status":"insufficient_data"}})
            self.assertEqual(app.model,{"version":6})
            self.assertEqual(app.command_ledger.calls,[])

    async def test_native_schedule_catches_up_and_retries_only_after_new_reports(self):
        with package(self.root / "package") as (component, _):
            _, app=self.controller(component)
            date=datetime(2026,11,1,14,tzinfo=timezone.utc)
            with patch.object(app,"daily_learn") as learn:
                app.schedule_learning(date)
                learn.assert_called_once()
                app.memory["adaptive_schedule"]={"day":date.date().isoformat(),"slot":"daily",
                                                 "room_report":1000.}
                learn.reset_mock()
                app._learning_last_report=1000.
                app.schedule_learning(date.replace(hour=21))
                learn.assert_not_called()
                app._learning_last_report=2000.
                app.schedule_learning(date.replace(hour=21))
                learn.assert_called_once()
            self.assertEqual(app.command_ledger.calls,[])

    async def test_daily_retry_waits_for_recovered_qualification(self):
        with package(self.root / "package") as (component, _):
            _, app=self.controller(component)
            date=datetime(2026,11,1,14,tzinfo=timezone.utc)
            app.memory["adaptive_schedule"]={"day":date.date().isoformat(),"slot":"daily",
                                             "room_report":1000.,"eligible_at_issue":False}
            app._learning_last_report=1000.
            with patch.object(app,"daily_learn") as learn, patch.object(app,"_learning_eligible") as ready:
                ready.return_value=False
                app.schedule_learning(date)
                learn.assert_not_called()
                ready.return_value=True
                app.schedule_learning(date)
                learn.assert_called_once_with(slot="retry")
                learn.reset_mock()
                app.memory["adaptive_schedule"]["slot"]="retry"
                app.schedule_learning(date)
                learn.assert_not_called()

    async def test_learning_commit_and_rollback_are_atomic_and_leave_control_ledgers(self):
        with package(self.root / "package") as (component, _):
            _, app = self.controller(component)
            app.directory = self.root
            app.now = lambda: datetime(2026,11,1,tzinfo=timezone.utc)
            app.model = {"version":6,"validated":True,"floor_air":.035}
            source=dict(app.model)
            ventilation=dict(validated=True,model_version=6,base_room_loss=.015,
                             recuperator=.002,bath=.001)
            app.memory.update(model=source,manual_energy={"status":"completed"},energy_cursor=3.,
                              ventilation_model=dict(ventilation))
            app._learning_rows.append({"ts":1.})
            app.submit_to_executor=Mock()
            app._learning_eligible=Mock(return_value=True)
            app.daily_learn()
            outcome={"status":"accepted","proposed":{"floor_air":.0351},
                "evidence":{"status":"accepted","strata":{"1:night_idle":{"mae":.3,"violation":False}}},
                "metrics":{},"evidence_fingerprint":"fixture"}
            job=app._learning_job["id"]
            original=app.statefile.read_bytes() if app.statefile.exists() else None
            with patch.object(component.os,"replace",side_effect=OSError("disk")):
                app._learning_finished({"job_id":job,"result":outcome})
            self.assertEqual(app.model,source)
            self.assertEqual(app.memory["ventilation_model"],ventilation)
            self.assertEqual(app.statefile.read_bytes() if app.statefile.exists() else None,original)
            app.daily_learn()
            self.assertIsNotNone(app._learning_job)
            app._learning_finished({"job_id":app._learning_job["id"],"result":outcome})
            self.assertEqual(app.model["version"],7)
            self.assertEqual(app.memory["ventilation_model"],{**ventilation,"model_version":7,
                             "carried_from_model_version":6})
            self.assertEqual(json.loads(app.statefile.read_text())["ventilation_model"],
                             app.memory["ventilation_model"])
            self.assertEqual(json.loads(app.statefile.read_text())["model"],app.model)
            self.assertEqual(app.memory["energy_cursor"],3.)
            from test_unified_learning import pairs
            issued=app.now().timestamp()
            app._learning_forecasts.append({"model":dict(app.model),"start":issued,
                                            "points":{"1":[issued+3600]}})
            app.climate_tz=timezone.utc
            with patch.object(component.learning,"compare_frozen",return_value=pairs(old_error=.5)):
                app._monitor_learning(issued+3600)
            self.assertEqual(app.model,source)
            self.assertEqual(json.loads(app.statefile.read_text())["model"],source)
            self.assertEqual(app.memory["ventilation_model"],ventilation)
            self.assertEqual(json.loads(app.statefile.read_text())["ventilation_model"],ventilation)
            self.assertGreater(app.memory["online_learning"]["frozen_until"],app.now().timestamp())
            self.assertEqual(app.memory["manual_energy"],{"status":"completed"})
            self.assertEqual(app.command_ledger.calls,[])

    async def test_kws_profile_promotion_and_rollback_are_atomic(self):
        from test_kws_forecast_learning import PROFILE
        with package(self.root / "package") as (component, _):
            _, app = self.controller(component)
            app.directory = self.root
            app.now = lambda: datetime(2026,11,1,tzinfo=timezone.utc)
            app.model = {"version":6,"validated":True,"floor_air":.035}
            source=dict(app.model)
            app.cfg["boiler_power_mode"]="fixed_6"
            app.memory.update(model=source,equipment_version=1,
                manual_energy={"status":"completed"},energy_cursor=3.)
            app._learning_rows.append({"ts":1.})
            app.submit_to_executor=Mock()
            app._learning_eligible=Mock(return_value=True)
            outcome={"status":"accepted","proposed_power":dict(PROFILE),
                "evidence":{"status":"accepted","strata":{"1:night_idle":{"mae":.3,"violation":False}}},
                "metrics":{},"evidence_fingerprint":"fixture-power"}
            app.daily_learn()
            original=app.statefile.read_bytes() if app.statefile.exists() else None
            with patch.object(component.os,"replace",side_effect=OSError("disk")):
                app._learning_finished({"job_id":app._learning_job["id"],"result":outcome})
            self.assertEqual(app.model,source)
            self.assertIsNone(app.memory.get("kws_forecast_profile"))
            self.assertEqual(app.statefile.read_bytes() if app.statefile.exists() else None,original)
            app.daily_learn()
            app._learning_finished({"job_id":app._learning_job["id"],"result":outcome})
            self.assertEqual(app.model,{**source,"version":7})
            applied={**PROFILE,"thermal_model_version":7}
            self.assertEqual(app.memory["kws_forecast_profile"],applied)
            self.assertEqual(app.memory["power_calibrations"]["profiles"]["fixed_6"],applied)
            self.assertEqual(json.loads(app.statefile.read_text())["kws_forecast_profile"],applied)
            app._rollback_learning("fixture")
            self.assertEqual(app.model,source)
            self.assertIsNone(app.memory.get("kws_forecast_profile"))
            self.assertFalse(app.memory["power_calibrations"]["profiles"])
            self.assertIsNone(json.loads(app.statefile.read_text())["kws_forecast_profile"])
            self.assertGreater(app.memory["online_learning"]["frozen_until"],app.now().timestamp())
            self.assertEqual(app.memory["energy_cursor"],3.)
            self.assertEqual(app.memory["manual_energy"],{"status":"completed"})
            self.assertEqual(app.command_ledger.calls,[])

    async def test_adaptive_capture_requires_numeric_change_at_primary(self):
        with package(self.root / "package") as (component, _):
            _, app = self.controller(component)
            app.climate_tz = timezone.utc
            stamp = 3000.
            source = {"state":"21.0","last_changed":datetime.fromtimestamp(stamp,timezone.utc).isoformat()}
            app.raw = lambda *args,**kwargs: source
            app._learning_append(dict(ts=stamp,room=21.,room_valid=True,room_reported_at=stamp),True,(),0.)
            self.assertTrue(app._learning_rows[-1]["report_verified"])
            for value,expected in (("21.00",False),("20.9",True)):
                stamp += 300
                source.update(state=value,last_changed=datetime.fromtimestamp(stamp,timezone.utc).isoformat())
                app._learning_append(dict(ts=stamp,room=float(value),room_valid=True,room_reported_at=stamp),True,(),0.)
                self.assertIs(app._learning_rows[-1]["report_verified"],expected)
            self.assertEqual(app.memory,{})
            self.assertEqual(app.command_ledger.calls,[])

    async def test_direct_primary_reports_with_unchanged_temperature_are_independent(self):
        with package(self.root / "package") as (component, _):
            hass, app = self.controller(component)
            app.climate_tz = timezone.utc
            app.cfg["room"] = "sensor.primary"
            hass.rows["sensor.primary"] = State("sensor.primary", 21., at=3000.)
            primary = hass.rows["sensor.primary"]
            entry=SimpleNamespace(platform="tuya",original_device_class="temperature",
                                  unique_id="tuya.test123va_temperature")
            with patch.object(component.entity_registry,"async_get",
                              return_value=SimpleNamespace(async_get=lambda key: entry)):
                app._async_listen_room_reports()
            _, signal, dispatch = component.async_dispatcher_connect.call_args.args
            self.assertEqual(signal,"tuya_entry_update_test123")
            for stamp, report, datapoint, observed, expected in (
                (3000., 3000., None, None, False),  # cached at startup
                (3300., 3000., None, None, False),  # same cached report
                (6600., 6599., "va_temperature", 6598., True),
                (6790., 6789., "va_temperature", 6598., False),  # same hardware packet
                (6900., 6899., None, None, False),  # availability republish
                (7200., 7199., "va_humidity", 7198., False),
            ):
                primary.last_reported = datetime.fromtimestamp(report, timezone.utc)
                with patch.object(component.time,"time",return_value=report):
                    dispatch([datapoint] if datapoint else None,
                             {datapoint:observed} if datapoint else None)
                app._learning_append(dict(ts=stamp,room=21.,room_valid=True,
                    room_reported_at=report),True,(),0.)
                self.assertIs(app._learning_rows[-1]["report_verified"],expected)
            self.assertEqual(app.command_ledger.calls,[])

    async def test_room_report_subscription_is_optional_until_registry_loads(self):
        with package(self.root / "package") as (component, _):
            _,app=self.controller(component)
            app.cfg["room"]="sensor.primary"
            registry=Mock()
            registry.async_get.side_effect=AttributeError("Entity registry is not loaded")
            with patch.object(component.entity_registry,"async_get",return_value=registry):
                app._async_listen_room_reports()
            component.async_dispatcher_connect.assert_not_called()
            self.assertEqual(app.command_ledger.calls,[])

    async def test_manual_request_uses_native_serialized_control_only(self):
        with package(self.root / "package") as (component, _):
            hass, app = self.controller(component)
            app.model["validated"] = True
            app.cfg.update(power_accounting_method="kws", session_limit="180")
            with patch.object(app, "async_kws_status", return_value={"ready": True}), \
                 patch.object(app, "async_voltage_status", return_value={"voltage_qualified": True}), \
                 patch.object(app, "grid_state", return_value="on"), \
                 patch.object(app, "source_temperature", return_value=(21, "Вітальня", 21, 21)), \
                 patch.object(app, "val", return_value=180), \
                 patch.object(app, "outage_execution_schedule", return_value={"valid": False}), \
                 patch.object(app, "active_upper", return_value=22), \
                 patch.object(app, "save"), \
                 patch.object(app, "publish"):
                app._manual_kws_snapshot = lambda: (100.0, 10000., 10000.)
                with patch("time.time", return_value=10000.):
                    app.start_manual_energy(40)
                self.assertEqual(app.memory["manual_energy"]["status"], "active")
                self.assertEqual(app.memory["manual_energy"]["target_kwh"], 40)
                self.assertEqual(app.memory["manual_energy"]["deadline"], 10000+830*60)
                with self.assertRaisesRegex(ValueError, "already_active"):
                    app.start_manual_energy(1)
                with patch("time.time", return_value=10001.):
                    app.stop_manual_energy()
                self.assertEqual(app.memory["manual_energy"]["reason"], "user_stop")

    async def test_manual_energy_native_completion_and_grid_cancellation(self):
        with package(self.root / "package") as (component, _):
            hass, app = self.controller(component)
            with patch.object(app, "async_kws_status", return_value={"ready": True}), \
                 patch.object(app, "async_voltage_status", return_value={"voltage_qualified": True}), \
                 patch.object(app, "save"), patch.object(app, "publish"):
                for grid,expected in [('on','completed'),('off','cancelled')]:
                    with self.subTest(grid=grid):
                        stamp=10000.
                        session=component.manual_energy.start(40,100.,stamp,stamp,830)
                        app.memory["manual_energy"]=session
                        app._manual_energy_ending=False
                        for n in range(1,20):
                            at=stamp+n*900
                            app._manual_kws_snapshot=lambda: (100.+2*n, at, None)
                            self.assertTrue(app.manual_extra_demand(at,'on',False,21.,22.5,False,True))
                        at=stamp+20*900
                        app._manual_kws_snapshot=lambda:(140.,at,None)
                        self.assertFalse(app.manual_extra_demand(at,grid,False,21.,22.5,False,True))
                        self.assertEqual(session["status"],expected)
                        self.assertTrue(app._manual_energy_ending)
                        self.assertFalse(app.manual_energy_demand)

    async def test_native_manual_progress_uses_measured_phase_power_with_unchanged_counter(self):
        with package(self.root / "package") as (component, _):
            hass, app = self.controller(component)
            stamp=10000.
            session=component.manual_energy.start(1,100.,stamp,stamp,180)
            app.memory["manual_energy"]=session
            with patch.object(app, "async_kws_status", return_value={"ready": True}), \
                 patch.object(app, "async_voltage_status", return_value={"voltage_qualified": True}), \
                 patch.object(app, "save"), patch.object(app, "publish"):
                for n in range(41):
                    at=stamp+n*15
                    app._manual_kws_snapshot=lambda:(100.,at,None)
                    app.power_observation=dict(at=at,watts=6000,quality='measured',policy='kws',
                        source_quality={'sensor.energy_meter_kotel_potuzhnist_'+phase:
                            dict(quality='measured',value_reported_at=at) for phase in 'abc'})
                    self.assertEqual(app.manual_extra_demand(at,'on',False,21.,22.5,False,True),n<40)
                self.assertEqual(session['status'],'completed')
                self.assertEqual(session['completion_basis'],'kws_power')
                self.assertAlmostEqual(session['progress_kwh'],1)
                self.assertEqual(session['consumed_kwh'],0)
                self.assertTrue(app._manual_energy_ending)

    async def test_immediate_manual_request_cannot_bypass_native_voltage_or_kws(self):
        with package(self.root / "package") as (component, _):
            for fault in ('voltage','kws'):
                with self.subTest(fault=fault):
                    hass,app=self.controller(component)
                    app.memory.update(last_command='off',command_at=970,owned=True,
                                      manual_energy=dict(status='active',target_kwh=1))
                    app.manual_energy_demand=True
                    if fault=='voltage':
                        hass.rows['sensor.l2'].state='196'
                        with patch.object(component.time,'time',return_value=1000):
                            await asyncio.to_thread(app.apply_action,'heat',False)
                    else:
                        with patch.object(app,'apply_voltage_stop',return_value=False), \
                             patch.object(app,'async_kws_status',return_value={'ready':False,'reason':'test','reason_text':'KWS blocked'}), \
                             patch.object(app,'source_temperature',return_value=(None,'',None,None)):
                            await asyncio.to_thread(app.apply_action,'heat',False)
                    self.assertEqual(hass.rows['climate.boiler'].state,'off')
                    self.assertFalse(any(c.kwargs.get('service_data',{}).get('hvac_mode')=='heat'
                                         for c in hass.services.async_call.await_args_list))

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

    async def test_duplicate_equipment_echo_is_idempotent(self):
        with package(self.root / "package") as (component, _):
            hass, app = self.controller(component)
            evidence = app.prepare_equipment_command(
                "climate", "set_hvac_mode",
                {"entity_id": "climate.boiler", "hvac_mode": "off"})
            app.mark_equipment_command_sent(evidence)
            evidence["service_completed_at"] = 1001
            hass.rows["climate.boiler"].state = "off"
            hass.rows["climate.boiler"].last_reported = datetime.fromtimestamp(evidence["sent_at"]+1, timezone.utc)
            observed = app.async_equipment_command_echo(
                "climate", "set_hvac_mode", evidence["data"], evidence)
            self.assertTrue(app.record_equipment_command_echo(evidence, observed))
            self.assertTrue(app.record_equipment_command_echo(evidence, observed))
            self.assertEqual(
                [call[0] for call in app.command_ledger.calls],
                ["PREPARED", "SENT", "HA_ECHO", "UNFENCED"])

    async def test_persisted_fence_recovery_guard_is_reset_on_native_restart(self):
        with package(self.root / "package") as (component, _):
            hass, app = self.controller(component)
            app.memory.update(
                fence_recovery_tick_seen=True,
                fence_recovery_attempted=True,
                native_runtime_initialized=True,
            )
            app._fence_recovery_attempted = True
            with patch.object(component.HeatingClimate, "initialize"):
                await asyncio.to_thread(app.initialize)
            self.assertNotIn("fence_recovery_tick_seen", app.memory)
            self.assertNotIn("fence_recovery_attempted", app.memory)
            self.assertFalse(app._fence_recovery_attempted)

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

    async def test_auto_fan_tuya_echo_does_not_steal_control(self):
        with package(self.root / "package") as (component, _):
            hass, app = self.controller(component)
            baseline = app.async_fan_snapshot()
            app.memory.update(fan_owned=True, native_fan_manual_epoch=3,
                              native_fan_device_signature=baseline["device"],
                              native_fan_manual_hold=dict(state="on", at=1))
            hass.rows["fan.circulation"].replace("on", context="tuya-echo")
            await asyncio.to_thread(app.fan_state_changed)
            self.assertNotIn("native_fan_manual_hold", app.memory)
            self.assertTrue(app.memory["fan_owned"])
            self.assertEqual(app.memory["native_fan_manual_epoch"], 3)
            fan = app.async_fan_snapshot()
            app._fan_dispatch_signature = fan["permissions"]
            app._fan_dispatch_device_signature = fan["device"]
            app._fan_dispatch_epoch = 3
            await asyncio.to_thread(app.call_service, "fan/turn_off",
                                    entity_id="fan.circulation")
            self.assertEqual(hass.services.async_call.await_count, 1)

    async def test_persisted_external_fan_hold_clears_on_explicit_control_enable(self):
        with package(self.root / "package") as (component, _):
            hass, app = self.controller(component)
            hass.rows["input_boolean.fan_control"].state = "off"
            app.memory["native_fan_device_signature"] = app.async_fan_snapshot()["device"]
            hass.rows["fan.circulation"].replace("on", context="external-manual")
            await asyncio.to_thread(app.fan_state_changed)
            self.assertTrue(app.memory["native_fan_manual_hold"])
            hass.rows["input_boolean.fan_control"].state = "on"
            await asyncio.to_thread(app.fan_permission_changed)
            self.assertNotIn("native_fan_manual_hold", app.memory)
            fan = app.async_fan_snapshot()
            app._fan_dispatch_signature = fan["permissions"]
            app._fan_dispatch_device_signature = fan["device"]
            app._fan_dispatch_epoch = app.memory["native_fan_manual_epoch"]
            await asyncio.to_thread(app.call_service, "fan/turn_off",
                                    entity_id="fan.circulation")
            self.assertEqual(hass.services.async_call.await_count, 1)

    async def test_explicit_stop_blocks_fan_start_but_allows_off(self):
        with package(self.root / "package") as (component, _):
            hass, app = self.controller(component)
            hass.rows["input_select.fan_mode"].state = "Зупинити"
            fan = app.async_fan_snapshot()
            app._fan_dispatch_signature = fan["permissions"]
            app._fan_dispatch_device_signature = fan["device"]
            app._fan_dispatch_epoch = 0
            with self.assertRaisesRegex(PermissionError, "safety interlock"):
                await asyncio.to_thread(app.call_service, "fan/turn_on",
                                        entity_id="fan.circulation", percentage=38)
            hass.services.async_call.assert_not_called()
            await asyncio.to_thread(app.call_service, "fan/turn_off",
                                    entity_id="fan.circulation")
            self.assertEqual(hass.services.async_call.await_count, 1)

    async def test_source_heartbeat_is_not_mistaken_for_native_ownership(self):
        with package(self.root / "package") as (component, _):
            hass = FakeHA()
            hass.rows["sensor.heating_heartbeat"] = State("sensor.heating_heartbeat", "1000")
            self.assertTrue(component.source_writer_active(hass, 1020))
            self.assertFalse(component.source_writer_active(hass, 1050))
            hass.rows["sensor.heating_heartbeat"].attributes["runtime"] = "native_v1"
            self.assertFalse(component.source_writer_active(hass, 1020))


    async def test_archive_job_is_bounded_and_lease_waits_for_executor_exit(self):
        import threading
        with package(self.root / "package") as (component, _):
            hass,app=self.controller(component)
            started=threading.Event()
            release=threading.Event()
            calls=[]
            def compact(stamp, *, allow_remove, stop):
                calls.append((stamp,allow_remove,stop))
                started.set()
                if not release.wait(5):
                    raise RuntimeError("fixture timed out")
                return dict(status="deferred",snapshots=0)
            app.decision_journal=SimpleNamespace(compact_cold_snapshot_batch=compact)
            app._kws_idle_since=1000.
            app.calculation_jobs={}
            app.terminate=Mock()
            app._archive_next_at=0.
            lease=Mock()
            runtime=component.ProductionRuntime(app,lease)
            try:
                app.schedule_archive_maintenance(1061.)
                app.schedule_archive_maintenance(1062.)
                self.assertTrue(await asyncio.to_thread(started.wait,2))
                self.assertEqual(len(calls),1)
                self.assertTrue(calls[0][1])
                close=asyncio.create_task(runtime.close())
                await asyncio.sleep(.03)
                self.assertTrue(calls[0][2].is_set())
                lease.close.assert_not_called()
                app.schedule_archive_maintenance(99999.)
                self.assertEqual(len(calls),1)
                release.set()
                await asyncio.wait_for(close,3)
                lease.close.assert_called_once()
                self.assertTrue(app._closed)
            finally:
                release.set()
                await runtime.close()

    async def test_stalled_archive_does_not_block_shutdown_and_keeps_lease_until_worker_exits(self):
        import threading
        import time
        with package(self.root / "package") as (component, _):
            _,app=self.controller(component)
            started=threading.Event(); release=threading.Event(); finished=threading.Event()
            lease=Mock()
            def compact(stamp, *, allow_remove, stop):
                started.set()
                if not release.wait(10):
                    raise RuntimeError("fixture timed out")
                self.assertTrue(stop.is_set())
                finished.set()
                return dict(status="stopped",snapshots=0)
            app.decision_journal=SimpleNamespace(compact_cold_snapshot_batch=compact)
            app._kws_idle_since=1000.
            app.calculation_jobs={}
            app.terminate=Mock()
            runtime=component.ProductionRuntime(app,lease)
            app.ARCHIVE_CLOSE_TIMEOUT=.05
            try:
                app.schedule_archive_maintenance(1061.)
                self.assertTrue(await asyncio.to_thread(started.wait,2))
                start=time.monotonic()
                await asyncio.wait_for(runtime.close(),1.)
                self.assertLess(time.monotonic()-start,.9)
                self.assertTrue(app._archive_stop.is_set())
                self.assertTrue(app._closed)
                lease.close.assert_not_called()
                self.assertFalse(finished.is_set())
                release.set()
                await asyncio.wait_for(app._archive_job,2.)
                self.assertTrue(finished.is_set())
                lease.close.assert_called_once()
            finally:
                release.set()
                await runtime.close()

    async def test_archive_worker_cannot_start_writes_after_lease_release(self):
        with package(self.root / "package") as (component, _):
            _,app=self.controller(component)
            journal=SimpleNamespace(compact_cold_snapshot_batch=Mock())
            lease=Mock()
            app.terminate=Mock()
            runtime=component.ProductionRuntime(app,lease)
            await runtime.close()
            result=await asyncio.to_thread(app._archive_worker,journal,1061.)
            self.assertEqual(result["status"],"stopped")
            journal.compact_cold_snapshot_batch.assert_not_called()
            lease.close.assert_called_once()

    async def test_archive_schedule_defers_heat_and_pending_calculation(self):
        with package(self.root / "package") as (component, _):
            _,app=self.controller(component)
            app.decision_journal=SimpleNamespace(compact_cold_snapshot_batch=Mock())
            app.calculation_jobs={}
            app._archive_next_at=0.
            app._kws_idle_since=None
            app.schedule_archive_maintenance(1000.)
            self.assertIsNone(app._archive_job)
            app._kws_idle_since=800.
            app.calculation_jobs={"plan":{"completed":False}}
            app.schedule_archive_maintenance(1000.)
            self.assertIsNone(app._archive_job)
            app.calculation_jobs={}
            app._learning_job={"started":0.}
            app.schedule_archive_maintenance(1000.)
            self.assertIsNone(app._archive_job)
            app._learning_job=None
            app._archive_next_at=1100.
            app.schedule_archive_maintenance(1000.)
            self.assertIsNone(app._archive_job)
            app.decision_journal.compact_cold_snapshot_batch.assert_not_called()

    async def test_runtime_close_is_idempotent_and_releases_lease_once(self):
        with package(self.root / "package") as (component, _):
            controller = SimpleNamespace(async_close=AsyncMock())
            lease = Mock()
            runtime = component.ProductionRuntime(controller, lease)
            await runtime.close()
            await runtime.close()
            controller.async_close.assert_awaited_once()
            lease.close.assert_called_once()
            self.assertTrue(runtime._closed)

    async def test_ha_stop_listener_is_unsubscribed_once_after_event(self):
        """A consumed one-shot listener must not be unsubscribed again at shutdown."""
        with package(self.root / "package") as (component, _):
            install_id = "a" * 32
            root = self.root / "stop-hook-fixture"
            (root / "heating_ai_v1" / install_id).mkdir(parents=True, exist_ok=True)
            hass = FakeHA()
            hass.config = SimpleNamespace(path=lambda *parts: str(root.joinpath(*parts)))
            hass.data = {component.DOMAIN: {"_api": True, "production_assets": True}}
            hass.http = SimpleNamespace(async_register_static_paths=AsyncMock())

            class EventBus:
                def __init__(self):
                    self.listeners = {}

                def async_listen(self, name, handler):
                    handlers = self.listeners.setdefault(name, [])
                    handlers.append(handler)
                    return lambda: handlers.remove(handler)

                def async_listen_once(self, name, handler):
                    async def once(event):
                        unsubscribe()  # Real HA consumes a one-shot before invoking it.
                        await handler(event)
                    unsubscribe = self.async_listen(name, once)
                    return unsubscribe

                async def fire(self, name):
                    for handler in tuple(self.listeners.get(name, ())):
                        result = handler(SimpleNamespace(data={}))
                        if asyncio.iscoroutine(result):
                            await result

            hass.bus = EventBus()
            cfg = self.config()
            cfg.update(room="sensor.room", **{"return": "sensor.return"})
            for entity in ("climate.boiler", "sensor.room", "sensor.return",
                           "input_boolean.armed", "input_select.mode"):
                hass.rows[entity] = State(entity, "on")
            controller = SimpleNamespace(
                async_start=AsyncMock(), async_close=AsyncMock(),
                attach_command_ledger=Mock(),
                _context=SimpleNamespace(id="fixture-controller"))
            ledger = Mock()
            ledger.claim_boot.return_value = {
                "revision": 1, "owner_id": install_id,
                "boot_id": "fixture-boot", "owner_epoch": 1, "fenced": False}
            entry = SimpleNamespace(
                data={"installation_id": install_id}, options={}, entry_id="fixture",
                add_update_listener=Mock(return_value=lambda: None),
                async_on_unload=Mock())
            with patch.object(component, "load_installation", return_value=(cfg, {})), \
                 patch.object(component, "NativeController", return_value=controller), \
                 patch.object(component, "CriticalLedger", return_value=ledger):
                self.assertTrue(await component.async_setup_production(hass, entry))
                await hass.bus.fire("homeassistant_stop")
            controller.async_close.assert_awaited_once()
            self.assertEqual(hass.bus.listeners["homeassistant_stop"], [])

    async def test_close_cancels_launched_jobs_and_rejects_late_launch(self):
        with package(self.root / "package") as (component, _):
            hass, app = self.controller(component)
            started = asyncio.Event()
            finished = asyncio.Event()

            async def job():
                started.set()
                try:
                    await asyncio.sleep(60)
                finally:
                    finished.set()

            task = app._launch(job())
            await started.wait()
            await app.async_close()
            self.assertTrue(task.done())
            self.assertTrue(finished.is_set())
            late = app._launch(asyncio.sleep(0))
            self.assertIsNone(late)


    async def test_manual_power_only_start_recovers_from_missing_counter_without_bypassing_safety(self):
        with package(self.root / "package") as (component, _):
            hass, app = self.controller(component)
            app.model["validated"] = True
            app.cfg.update(power_accounting_method="kws", session_limit="180")
            with patch.object(app,"async_kws_status",return_value={"ready":True}), \
                 patch.object(app,"async_voltage_status",return_value={"voltage_qualified":True}), \
                 patch.object(app,"grid_state",return_value="on"), \
                 patch.object(app,"source_temperature",return_value=(21,"Вітальня",21,21)), \
                 patch.object(app,"val",return_value=180), \
                 patch.object(app,"outage_execution_schedule",return_value={"valid":False}), \
                 patch.object(app,"active_upper",return_value=22), \
                 patch.object(app,"save"),patch.object(app,"publish"):
                app._manual_kws_snapshot=lambda:(None,None,None)
                app.power_observation=dict(at=10000.,watts=6000,quality="measured",policy="kws",
                    source_quality={"sensor.energy_meter_kotel_potuzhnist_"+phase:
                        dict(quality="measured",value_reported_at=10000.) for phase in "abc"})
                with patch.object(component.time,"time",return_value=10000.):
                    app.start_manual_energy(1)
                session=app.memory["manual_energy"]
                self.assertTrue(session["power_only"])
                for n in range(41):
                    at=10000.+n*15
                    app.power_observation["at"]=at
                    for source in app.power_observation["source_quality"].values():
                        source["value_reported_at"]=at
                    self.assertEqual(app.manual_extra_demand(at,"on",False,21.,22.5,False,True),n<40)
                self.assertEqual(session["reason"],"target_reached")
                self.assertAlmostEqual(session["progress_kwh"],1)
                self.assertEqual(session["consumed_kwh"],0)

    async def test_power_only_is_denied_without_independent_healthy_three_phase_power(self):
        with package(self.root / "package") as (component, _):
            hass, app = self.controller(component)
            app.model["validated"] = True
            app.cfg.update(power_accounting_method="kws", session_limit="180")
            with patch.object(app,"async_kws_status",return_value={"ready":True}), \
                 patch.object(app,"async_voltage_status",return_value={"voltage_qualified":True}), \
                 patch.object(app,"grid_state",return_value="on"), \
                 patch.object(app,"source_temperature",return_value=(21,"Вітальня",21,21)), \
                 patch.object(app,"val",return_value=180), \
                 patch.object(app,"outage_execution_schedule",return_value={"valid":False}), \
                 patch.object(app,"active_upper",return_value=22), \
                 patch.object(app,"save"),patch.object(app,"publish"):
                app._manual_kws_snapshot=lambda:(None,None,None)
                app.power_observation=dict(at=10000.,watts=6000,quality="measured",policy="kws",
                    source_quality={"sensor.energy_meter_kotel_potuzhnist_"+phase:
                        dict(quality="measured",value_reported_at=9900.) for phase in "abc"})
                with patch.object(component.time,"time",return_value=10000.):
                    with self.assertRaisesRegex(ValueError,"counter_invalid"):
                        app.start_manual_energy(1)
                self.assertNotEqual(app.memory.get("manual_energy",{}).get("status"),"active")


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

