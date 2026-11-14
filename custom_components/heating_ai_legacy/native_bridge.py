"""Native HA I/O for the existing synchronous V1 controller.

Controller callbacks run serially in an executor. Numerical jobs retain V1's
separate immutable-input worker; HA's event loop never runs the optimiser.
"""
from __future__ import annotations

import asyncio
from concurrent.futures import ProcessPoolExecutor
from copy import deepcopy
from datetime import datetime, timezone
import inspect
import logging
import multiprocessing
from pathlib import Path
import site
import threading
import time
from uuid import uuid4

from homeassistant.core import Context, callback
from homeassistant.helpers.event import async_track_state_change_event
from homeassistant.helpers.template import Template
from homeassistant.util.async_ import run_callback_threadsafe

from .calculation_worker import calculate

_LOGGER = logging.getLogger(__name__)

_POOL_CLOSE_TIMEOUT = 15
_SERIAL_CLOSE_TIMEOUT = 5


def _terminate_process_pool(pool):
    if hasattr(pool, "terminate_workers"):
        pool.terminate_workers()
    else:
        pool.shutdown(wait=False, cancel_futures=True)


async def _close_process_pool(hass, pool):
    try:
        await asyncio.wait_for(
            hass.async_add_executor_job(_terminate_process_pool, pool),
            _POOL_CLOSE_TIMEOUT,
        )
    except asyncio.TimeoutError:
        killer = getattr(pool, "kill_workers", None)
        if killer is None:
            _LOGGER.error("Heating AI calculation pool did not stop within timeout")
            return
        try:
            await asyncio.wait_for(
                hass.async_add_executor_job(killer), _SERIAL_CLOSE_TIMEOUT
            )
        except (asyncio.TimeoutError, RuntimeError) as exc:
            _LOGGER.error("Heating AI calculation pool could not be killed: %s", exc)



class Hass:
    """The small I/O surface used by V1; no AppDaemon dependency."""

    def __init__(self, hass, args):
        self.hass = hass
        self.args = deepcopy(args)
        self._running = False
        self._writer = False
        self._serial = asyncio.Lock()
        self._handles = {}
        self._jobs = set()
        self._published = set()
        self._calculation_pool = None
        self._pool_lock = asyncio.Lock()
        self._close_lock = asyncio.Lock()
        self._closing = False
        self._closed = False
        self._context = Context()
        self._equipment_service_task = None
        self._loop_thread = threading.get_ident()

    def _on_loop(self, function, *args):
        if threading.get_ident() == self._loop_thread:
            # Async Recorder callbacks already run here. Execute only the
            # supplied short HA I/O closure directly; never reschedule and wait
            # on the same loop. Normal synchronous V1 callbacks stay off-loop.
            return function(*args)
        return run_callback_threadsafe(self.hass.loop, function, *args).result(30)

    async def _dispatch(self, function, *args):
        async with self._serial:
            if not self._running:
                return
            if inspect.iscoroutinefunction(function):
                await function(*args)
            else:
                await self.hass.async_add_executor_job(function, *args)

    def _launch(self, coro):
        if self._closing or not self._running:
            coro.close()
            return None
        task = self.hass.async_create_task(coro, "Heating AI V1 callback")
        self._jobs.add(task)
        task.add_done_callback(self._callback_finished)
        return task

    @callback
    def _callback_finished(self, task):
        self._jobs.discard(task)
        if task.cancelled():
            return
        error = task.exception()
        if error is None:
            return
        now = time.time()
        signature = (type(error).__name__+": "+str(error))[:300]
        memory = getattr(self, "memory", {})
        errors = memory.setdefault("diagnostic_errors", {})
        previous = errors.get("native_callback", {})
        incident = (previous if previous.get("signature") == signature else
                    dict(signature=signature, first_at=now, count=0))
        incident.update(active=True, last_at=now, count=incident["count"]+1)
        if now-incident.get("logged_at", 0) >= 300:
            _LOGGER.error("Heating AI callback failed (%s): %s", incident["count"], signature)
            incident["logged_at"] = now
        errors["native_callback"] = incident

    async def async_start(self, *, writer=False):
        self._running = True
        self._writer = writer
        try:
            await self._dispatch(self.initialize)
        except BaseException:
            await self.async_close()
            raise

    async def async_close(self):
        async with self._close_lock:
            if self._closed:
                return
            self._closing = True
            self._writer = False
            self._running = False

            for cancel in tuple(self._handles.values()):
                cancel()
            self._handles.clear()

            current = asyncio.current_task()
            service = self._equipment_service_task
            if service is not None and service is not current and not service.done():
                service.cancel()
                done, _ = await asyncio.wait({service}, timeout=_SERIAL_CLOSE_TIMEOUT)
                if not done:
                    _LOGGER.error("Heating AI thermostat service did not cancel during shutdown")
            jobs = tuple(job for job in self._jobs if job is not current)
            for job in jobs:
                job.cancel()
            if jobs:
                await asyncio.gather(*jobs, return_exceptions=True)

            async with self._pool_lock:
                pool, self._calculation_pool = self._calculation_pool, None
            if pool is not None:
                await _close_process_pool(self.hass, pool)

            acquired = False
            try:
                await asyncio.wait_for(self._serial.acquire(), _SERIAL_CLOSE_TIMEOUT)
                acquired = True
                await self.hass.async_add_executor_job(self.terminate)
            except asyncio.TimeoutError:
                _LOGGER.warning("Heating AI serial shutdown wait timed out")
                await self.hass.async_add_executor_job(self.terminate)
            finally:
                if acquired:
                    self._serial.release()
            self._closed = True

    def datetime(self):
        return datetime.now(timezone.utc)

    def log(self, message, *args, level="INFO", **kwargs):
        _LOGGER.log(getattr(logging, level, logging.INFO), message, *args)

    def get_state(self, entity_id=None, attribute=None, default=None,
                  namespace=None, copy=True, **kwargs):
        @callback
        def read():
            def record(state):
                return dict(state.as_dict(), last_reported=state.last_reported.isoformat())
            if entity_id is None:
                return {s.entity_id: record(s) for s in self.hass.states.async_all()}
            row = self.hass.states.get(entity_id)
            if row is None:
                return default
            if attribute == "all":
                return record(row)
            return row.state if attribute is None else row.attributes.get(attribute, default)
        result = self._on_loop(read)
        return deepcopy(result) if copy else result

    def set_state(self, entity_id, *, state, attributes=None, **kwargs):
        if not (entity_id.startswith(("sensor.heating_", "binary_sensor.heating_"))
                or entity_id in ("sensor.kotel_spozhita_energiia",
                                 "sensor.kotel_potochna_potuzhnist", "sensor.kotel_rezhim")):
            raise ValueError("Unexpected controller output entity")
        @callback
        def write():
            if not self._running:
                return
            self._published.add(entity_id)
            self.hass.states.async_set(entity_id, state, deepcopy(attributes or {}),
                                       context=self._context)
        return self._on_loop(write)

    def render_template(self, template, variables=None, **kwargs):
        @callback
        def render():
            return Template(template, self.hass).async_render(variables, parse_result=False)
        return self._on_loop(render)

    def call_service(self, service, **data):
        domain, action = service.split("/", 1)
        allowed = {
            "climate": ("set_temperature", "set_hvac_mode"),
            "fan": ("turn_on", "turn_off"),
            "input_boolean": ("turn_off",),
            "input_select": ("select_option",),
            "persistent_notification": ("create", "dismiss"),
        }
        if action not in allowed.get(domain, ()):
            raise PermissionError("Service outside controller scope")
        targets = {value for value in self.args.values() if isinstance(value, str)}
        if domain != "persistent_notification" and data.get("entity_id") not in targets:
            raise PermissionError("Entity outside configured controller scope")

        async def permitted():
            if not self._running or not self._writer:
                raise PermissionError("Controller writer is closed")
            active = self._equipment_service_task
            if domain == "climate" and active is not None and not active.done() and active is not asyncio.current_task():
                raise PermissionError("Previous thermostat service is still in flight")
            guard = getattr(self, "async_command_allowed", None)
            if guard is not None and not guard(domain, action, data):
                # A voltage change between planning and dispatch must also stop heat.
                voltage = getattr(self, "voltage_status", None)
                if (domain == "climate" and voltage is not None
                        and voltage.get("voltage_qualified") is False):
                    await self.hass.services.async_call("climate", "set_hvac_mode", {
                        "entity_id": self.args["thermostat"], "hvac_mode": "off",
                    }, blocking=True, context=self._context)
                raise PermissionError("Equipment command blocked by safety interlock")

        # The first pass is memory-only and happens before disk evidence. The
        # second pass closes changes between PREPARED and the HA service call.
        asyncio.run_coroutine_threadsafe(permitted(), self.hass.loop).result(30)
        evidence = None
        prepare = getattr(self, "prepare_equipment_command", None)
        if domain == "climate" and prepare is not None:
            evidence = prepare(domain, action, data)
            getattr(self, "mark_equipment_command_sent")(evidence)

        async def send():
            await permitted()
            if domain == "climate":
                self._equipment_service_task = asyncio.current_task()
            try:
                await self.hass.services.async_call(domain, action, data, blocking=True,
                                                   context=self._context)
            finally:
                if domain == "climate" and self._equipment_service_task is asyncio.current_task():
                    self._equipment_service_task = None
            if evidence is not None:
                evidence["service_completed_at"] = datetime.now(timezone.utc).timestamp()
            observe = getattr(self, "async_equipment_command_echo", None)
            return observe(domain, action, data, evidence) if observe is not None else None
        sent = asyncio.run_coroutine_threadsafe(send(), self.hass.loop)
        try:
            observed = sent.result(30)
        except BaseException:
            sent.cancel()
            if evidence is not None:
                getattr(self, "discard_pending_equipment_command", lambda *_: None)(evidence)
            raise
        if evidence is not None:
            getattr(self, "record_equipment_command_echo")(evidence, observed)
        return None

    def listen_state(self, function, entity_id, attribute=None, **kwargs):
        token = uuid4().hex
        @callback
        def register():
            @callback
            def changed(event):
                old, new = event.data.get("old_state"), event.data.get("new_state")
                def value(s):
                    if s is None:
                        return None
                    if attribute == "all":
                        return dict(s.as_dict(), last_reported=s.last_reported.isoformat())
                    return s.state if attribute is None else s.attributes.get(attribute)
                before, after = value(old), value(new)
                if before != after:
                    self._launch(self._dispatch(function, entity_id, attribute or "state",
                                                before, after, kwargs))
            self._handles[token] = async_track_state_change_event(self.hass, [entity_id], changed)
        self._on_loop(register)
        return token

    def cancel_listen_state(self, token):
        @callback
        def cancel():
            handle = self._handles.pop(token, None)
            if handle:
                handle()
        self._on_loop(cancel)

    def listen_event(self, function, event_name, **kwargs):
        token = uuid4().hex
        @callback
        def register():
            @callback
            def event_received(event):
                self._launch(self._dispatch(function, event.event_type, dict(event.data), kwargs))
            self._handles[token] = self.hass.bus.async_listen(event_name, event_received)
        self._on_loop(register)
        return token

    def _timer(self, function, delay, interval, kwargs):
        token = uuid4().hex
        @callback
        def register():
            pending = None
            @callback
            def fire():
                nonlocal pending
                self._handles.pop(token, None)
                if self._running:
                    if interval:
                        self._handles[token] = self.hass.loop.call_later(interval, fire).cancel
                    if pending is None or pending.done():
                        pending = self._launch(self._dispatch(function, kwargs))
            self._handles[token] = self.hass.loop.call_later(max(0, delay), fire).cancel
        self._on_loop(register)
        return token

    def run_in(self, function, delay, **kwargs):
        return self._timer(function, delay, None, kwargs)

    def run_every(self, function, start, interval, **kwargs):
        return self._timer(function, start.timestamp()-self.datetime().timestamp(), interval, kwargs)

    def submit_to_executor(self, function, *args, callback=None, **kwargs):
        async def run():
            if not self._running:
                return
            if function == getattr(self, "calculate_snapshot", None) and args and args[0] in ("plan", "power_advice"):
                kind, job_id, _algorithm, inputs, options = args
                try:
                    async with self._pool_lock:
                        if self._closing or not self._running:
                            return
                        if self._calculation_pool is None:
                            # CPU-bound Python threads share HA's GIL. Use spawn,
                            # never fork a running HA process, and pass only pure data.
                            self._calculation_pool = ProcessPoolExecutor(
                                max_workers=2, mp_context=multiprocessing.get_context("spawn"),
                                # HA loads custom_components via its package loader;
                                # /config need not be on a spawned interpreter's path.
                                initializer=site.addsitedir,
                                initargs=(str(Path(__file__).resolve().parents[2]),))
                        pool = self._calculation_pool
                        future = await self.hass.async_add_executor_job(
                            pool.submit, calculate, kind, job_id, inputs, options)
                    result = await asyncio.wrap_future(future)
                except Exception as exc:
                    if not self._running:
                        return
                    result = dict(kind=kind, id=job_id, error=str(exc), seconds=0.)
            else:
                result = await self.hass.async_add_executor_job(function, *args)
            if self._running and callback is not None:
                await self._dispatch(callback, dict(kwargs, result=result))
        self.hass.loop.call_soon_threadsafe(lambda: self._launch(run()))

    async def get_history(self, *, entity_id, start_time, **kwargs):
        from functools import partial
        from homeassistant.components.recorder import get_instance, history
        query = partial(history.get_significant_states, self.hass, start_time,
                        entity_ids=[entity_id], significant_changes_only=False,
                        minimal_response=False, no_attributes=False)
        rows = await get_instance(self.hass).async_add_executor_job(query)
        return [[s.as_dict() if hasattr(s, "as_dict") else s for s in group]
                for group in rows.values()]
