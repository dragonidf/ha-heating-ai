"""Offline acceptance injections: real durable journal and synthetic HA only."""
import asyncio
from datetime import datetime, timezone
import importlib.util
from pathlib import Path
from tempfile import TemporaryDirectory
import time
import unittest
from unittest.mock import patch

import test_native_v1_production as native
from test_native_v1_production import package, State

spec = importlib.util.spec_from_file_location('kws_readiness_fixture',
    Path(__file__).resolve().parents[1]/'custom_components/heating_ai_legacy/kws_readiness.py')
kws = importlib.util.module_from_spec(spec)
spec.loader.exec_module(kws)


class ReliabilityTests(unittest.IsolatedAsyncioTestCase):
    setUpClass = classmethod(native.ProductionTests.setUpClass.__func__)
    tearDownClass = classmethod(native.ProductionTests.tearDownClass.__func__)
    config = native.ProductionTests.config
    controller = native.ProductionTests.controller

    def attach_real(self, component, app, folder):
        ledger = component.CriticalLedger(folder, owner_id='owner', boot_id='boot', owner_epoch=0)
        app.attach_command_ledger(ledger, ledger.status())
        return ledger

    async def test_L01_L02_crash_prepared_and_sent_then_single_safe_stop(self):
        for sent in (False, True):
            with self.subTest(sent=sent), TemporaryDirectory() as folder, package(self.root/'package') as (component, _):
                hass, app = self.controller(component)
                ledger = self.attach_real(component, app, folder)
                result = ledger.prepare('crashed-heat', 'HEAT', {}, at=1, **app._ledger_identity())
                app.command_ledger_status['revision'] = result['revision']
                if sent:
                    ledger.record_sent('crashed-heat', at=2, **app._ledger_identity())
                ledger = component.CriticalLedger(folder)
                app.attach_command_ledger(ledger, ledger.claim_boot('new-boot', expected_owner_id='owner'))
                with self.assertRaises(component.LedgerConflict):
                    ledger.prepare('unsafe', 'HEAT', {}, **app._ledger_identity())
                with patch.object(component.time, 'time', return_value=1000):
                    await asyncio.to_thread(app._reconcile_writer_fence)
                status = ledger.status()
                self.assertFalse(status['fenced'])
                self.assertEqual(status['commands']['crashed-heat']['stage'], 'UNKNOWN')
                stops = [r for r in status['commands'].values() if r['action'] == 'STOP']
                self.assertEqual(len(stops), 1)
                self.assertEqual(stops[0]['stage'], 'HA_ECHO')
                self.assertEqual(hass.services.async_call.await_count, 1)

    async def test_L03_superseded_late_duplicate_echo_never_releases_fence(self):
        with TemporaryDirectory() as folder, package(self.root/'package') as (component, _):
            _, app = self.controller(component)
            ledger = self.attach_real(component, app, folder)
            first = app.prepare_equipment_command('climate', 'set_hvac_mode', {'entity_id':'climate.boiler','hvac_mode':'off'})
            app.mark_equipment_command_sent(first)
            second = app.prepare_equipment_command('climate', 'set_hvac_mode', {'entity_id':'climate.boiler','hvac_mode':'off'})
            app.mark_equipment_command_sent(second)
            self.assertFalse(app.record_equipment_command_echo(first, {'matched':True,'observed_at':time.time()}))
            self.assertTrue(ledger.status()['fenced'])
            self.assertTrue(app.record_equipment_command_echo(second, {'matched':True,'observed_at':time.time()}))
            self.assertTrue(app.record_equipment_command_echo(second, {'matched':True,'observed_at':time.time()}))
            self.assertFalse(ledger.status()['fenced'])
            self.assertEqual(ledger.status()['commands'][first['command_id']]['stage'], 'UNKNOWN')

    async def test_L04_transient_stop_timeout_backoff_then_recovery_without_restart(self):
        with TemporaryDirectory() as folder, package(self.root/'package') as (component, _):
            hass, app = self.controller(component)
            ledger = self.attach_real(component, app, folder)
            app.attach_command_ledger(ledger, ledger.claim_boot('recovery', expected_owner_id='owner'))
            original = hass.service
            attempts = 0
            async def flaky(*args, **kwargs):
                nonlocal attempts
                attempts += 1
                if attempts == 1:
                    raise TimeoutError('temporary local disconnect')
                return await original(*args, **kwargs)
            hass.services.async_call.side_effect = flaky
            with patch.object(component.time, 'time', return_value=1000):
                with self.assertRaises(TimeoutError):
                    await asyncio.to_thread(app._reconcile_writer_fence)
            for stamp in (1001, 1015, 1029):
                with patch.object(component.time, 'time', return_value=stamp):
                    await asyncio.to_thread(app._reconcile_writer_fence)
            self.assertEqual(attempts, 1)
            self.assertTrue(ledger.status()['fenced'])
            with patch.object(component.time, 'time', return_value=1030):
                await asyncio.to_thread(app._reconcile_writer_fence)
            self.assertEqual(attempts, 2)
            self.assertFalse(ledger.status()['fenced'])

    async def test_L04_old_off_display_cannot_clear_fence_on_service_return(self):
        with TemporaryDirectory() as folder, package(self.root/'package') as (component, _):
            hass, app = self.controller(component)
            ledger = self.attach_real(component, app, folder)
            hass.rows['climate.boiler'].state = 'off'
            hass.services.async_call.side_effect = lambda *a, **k: None
            with patch.object(component.time, 'time', return_value=2000):
                await asyncio.to_thread(app.call_service, 'climate/set_hvac_mode', entity_id='climate.boiler', hvac_mode='off')
            self.assertTrue(ledger.status()['fenced'])

    async def test_L05_manual_change_during_recovery_prepare_prevents_send(self):
        with TemporaryDirectory() as folder, package(self.root/'package') as (component, _):
            hass, app = self.controller(component)
            ledger = self.attach_real(component, app, folder)
            app.attach_command_ledger(ledger, ledger.claim_boot('recovery', expected_owner_id='owner'))
            original = app.mark_equipment_command_sent
            def mark(evidence):
                original(evidence)
                hass.rows['input_select.mode'].replace('Спостереження', context='manual')
            with patch.object(app, 'mark_equipment_command_sent', side_effect=mark):
                with self.assertRaises(PermissionError):
                    await asyncio.to_thread(app._reconcile_writer_fence)
            hass.services.async_call.assert_not_called()
            self.assertTrue(ledger.status()['fenced'])

    async def test_L06_two_boots_invalidate_old_owner_and_preserve_unknown(self):
        with TemporaryDirectory() as folder, package(self.root/'package') as (component, _):
            _, app = self.controller(component)
            ledger = self.attach_real(component, app, folder)
            stale = app._ledger_identity()
            for boot in ('second', 'third'):
                app.attach_command_ledger(ledger, ledger.claim_boot(boot, expected_owner_id='owner'))
            with self.assertRaises(component.LedgerConflict):
                ledger.prepare('old-writer', 'STOP', {}, **stale)
            self.assertTrue(ledger.status()['fenced'])

    async def test_F01_fan_failure_has_no_critical_reservation(self):
        with TemporaryDirectory() as folder, package(self.root/'package') as (component, _):
            hass, app = self.controller(component)
            ledger = self.attach_real(component, app, folder)
            snap = app.async_fan_snapshot()
            app._fan_dispatch_signature = snap['permissions']
            app._fan_dispatch_device_signature = snap['device']
            app._fan_dispatch_epoch = 0
            hass.services.async_call.side_effect = TimeoutError('cloud unavailable')
            with self.assertRaises(TimeoutError):
                await asyncio.to_thread(app.call_service, 'fan/turn_on', entity_id='fan.circulation', percentage=38)
            self.assertFalse(ledger.status()['fenced'])
            self.assertEqual(ledger.status()['commands'], {})

    def configure_kws(self, hass, app, stamp):
        app.args.update(power_accounting_method='kws', grid='binary_sensor.deye_grid',
                        connection='binary_sensor.deye_link', grid_voltage='sensor.deye_voltage',
                        room='sensor.room', return_='sensor.return')
        app.args['return'] = 'sensor.return'
        app.cfg.update(app.args)
        values = {'binary_sensor.deye_grid':'on', 'binary_sensor.deye_link':'on', 'sensor.deye_voltage':230,
                  'sensor.room':20.3, 'sensor.return':25, 'sensor.energy_meter_kotel_status':'on',
                  'switch.energy_meter_kotel':'on', 'binary_sensor.energy_meter_kotel_problema':'off',
                  **{'sensor.energy_meter_kotel_napruga_'+p:230 for p in 'abc'}}
        for entity, value in values.items():
            hass.rows[entity] = State(entity, value, at=stamp)
        for row in hass.rows.values():
            row.last_updated = row.last_reported = datetime.fromtimestamp(stamp, timezone.utc)

    async def test_K05_T02_gateway_blocks_deye_disconnect_and_invalid_probe(self):
        with package(self.root/'package') as (component, _):
            hass, app = self.controller(component)
            for stamp in range(1000, 1151, 15):
                self.configure_kws(hass, app, stamp)
                with patch.object(component.time, 'time', return_value=stamp):
                    app.async_kws_status()
            with patch.object(component.time, 'time', return_value=1150):
                self.assertTrue(app.async_kws_status()['ready'], app.equipment_status)
                snap = app._remember_manual_snapshot()
                app._dispatch_manual_signature = snap['signature']
                app._dispatch_manual_epoch = app.memory['native_manual_epoch']
                app._dispatch_action = 'heat'
                self.assertTrue(app.async_command_allowed('climate', 'set_hvac_mode', {'hvac_mode':'heat'}))
                for entity, value in [('binary_sensor.deye_link','off'), ('sensor.return','unavailable'), ('sensor.room','unknown')]:
                    old = hass.rows[entity].state
                    hass.rows[entity].state = value
                    self.assertFalse(app.async_command_allowed('climate','set_hvac_mode', {'hvac_mode':'heat'}), entity)
                    hass.rows[entity].state = old
                # AUTO is potential heat even with a currently low setpoint.
                hass.rows['climate.boiler'].attributes['temperature'] = 10
                hass.rows['switch.energy_meter_kotel'].state = 'off'
                self.assertFalse(app.async_command_allowed('climate','set_hvac_mode', {'hvac_mode':'auto'}))
            hass.services.async_call.assert_not_called()

    async def test_T03_opt_in_bias_is_bounded_and_never_used_when_fenced(self):
        with package(self.root/'package') as (component, _):
            _, app = self.controller(component)
            app.source_temperature = lambda: (20.3, 'room', 20.3, 21.5)
            app.raw = lambda key: 'Автоматичне керування' if key == 'mode' else None
            app.val = lambda key, default=None: {'target':22, 'lower':21, 'upper':22.5}.get(key, default)
            app.now = lambda: datetime(2026, 11, 14, 12, tzinfo=timezone.utc)
            app.memory.update(thermostat_bias=1.2, thermostat_bias_at=1000)
            app.temperature_quality = dict(primary_status='normal', thermostat_age_seconds=5)
            attrs = dict(min_temp=5, max_temp=35, target_temp_step=.5)
            parent = component.HeatingClimate
            with patch.object(parent, 'fallback_target', return_value=21.5), patch.object(component.time,'time',return_value=1001):
                self.assertEqual(app.fallback_target(21.5, attrs), 21.5)
                app.cfg['fallback_bias_enabled'] = True
                # Correction is allowed only for an actual native demand inside permitted hours.
                with patch.object(parent, 'fallback_target', return_value=22):
                    self.assertEqual(app.fallback_target(21.5, attrs), 23)
                    app.command_ledger_status['fenced'] = True
                    self.assertEqual(app.fallback_target(21.5, attrs), 22)
                    app.command_ledger_status['fenced'] = False
                    app.temperature_quality['primary_status'] = 'stale'
                    self.assertEqual(app.fallback_target(21.5, attrs), 22)

    async def test_U01_recovered_incident_retains_count_and_has_independent_grades(self):
        with TemporaryDirectory() as folder, package(self.root/'package') as (component, _):
            hass, app = self.controller(component)
            ledger = self.attach_real(component, app, folder)
            app.attach_command_ledger(ledger, ledger.claim_boot('recovery', expected_owner_id='owner'))
            app.memory['diagnostic_errors'] = {'ledger_recovery':dict(active=True, count=3)}
            with patch.object(component.time,'time',return_value=1000):
                await asyncio.to_thread(app._reconcile_writer_fence)
                await asyncio.to_thread(app.publish, 'sensor.heating_diagnostics', 'test')
            incident = app.memory['diagnostic_errors']['ledger_recovery']
            self.assertFalse(incident['active'])
            self.assertEqual(incident['count'], 3)
            grades = hass.rows['sensor.heating_diagnostics'].attributes['reliability']
            self.assertFalse(grades['physical_heating_confirmed'])
            self.assertFalse(grades['computation_alive'])
            self.assertEqual(grades['ledger_recovery']['state'], 'reconciliation_verified')


class BreakerTests(unittest.TestCase):
    def rows(self, at):
        return {k:dict(value=v, at=at) for k,v in
                {'status':'on','relay':'on','fault':'off','a':230,'b':230,'c':230}.items()}

    def test_K01_fault_standby_and_voltage_each_block_heat(self):
        guard = kws.KWSReadiness()
        guard.evaluate(1000, 'on', True, self.rows(1000))
        for key, value, expected in [('fault','on','kws_protection'), ('status','standby','kws_reset_pending'),
                                     ('relay','off','kws_relay_off'), ('a',177.32,'kws_voltage_unqualified')]:
            rows = self.rows(1120); rows[key]['value'] = value
            report = guard.evaluate(1120, 'on', True, rows)
            self.assertFalse(report['ready'])
            self.assertEqual(report['reason'], expected)

    def test_K02_K03_reset_requires_delay_stability_and_post_return_relay(self):
        guard = kws.KWSReadiness()
        self.assertFalse(guard.evaluate(1000, 'on', True, self.rows(1000))['ready'])
        self.assertFalse(guard.evaluate(1119, 'on', True, self.rows(1119))['ready'])
        self.assertTrue(guard.evaluate(1120, 'on', True, self.rows(1120))['ready'])
        guard.evaluate(1200, 'off', False, {})
        guard.evaluate(1300, 'on', True, self.rows(1300))
        rows = self.rows(1420); rows['relay']['at'] = 1200
        self.assertFalse(guard.evaluate(1420, 'on', True, rows)['ready'])
        rows['relay']['value'] = 'unavailable'
        self.assertTrue(guard.evaluate(1800, 'on', True, rows)['diagnosis_overdue'])
        self.assertFalse(guard.evaluate(1800, 'on', True, rows)['ready'])

    def test_K04_zero_power_never_supplies_contactor_evidence(self):
        guard = kws.KWSReadiness()
        rows = self.rows(1000)
        rows['power'] = dict(value=0, at=1000)
        rows['relay'] = dict(value='unknown', at=1000)
        self.assertFalse(guard.evaluate(1000, 'on', True, rows)['ready'])
        rows['power']['value'] = 9000
        self.assertFalse(guard.evaluate(1200, 'on', True, rows)['ready'])

    def test_P02_accelerated_72h_15second_mixed_fault_replay(self):
        guard = kws.KWSReadiness()
        ready_count = 0
        started = time.perf_counter()
        for tick in range(72*60*4):
            now = 1000+tick*15
            phase = tick % 240
            grid = 'off' if phase < 8 else 'unknown' if phase < 10 else 'on'
            rows = self.rows(now)
            if 20 <= phase < 25: rows['fault']['value'] = 'on'
            if 60 <= phase < 65: rows['relay']['value'] = 'unavailable'
            report = guard.evaluate(now, grid, phase >= 12, rows)
            if report['ready']:
                ready_count += 1
                self.assertEqual(grid, 'on')
                self.assertGreaterEqual(now-guard.grid_since, 120)
                self.assertGreaterEqual(now-guard.reset_since, 120)
                self.assertEqual(rows['fault']['value'], 'off')
                self.assertEqual(rows['relay']['value'], 'on')
        self.assertGreater(ready_count, 0)
        self.assertLess(time.perf_counter()-started, 10)
        self.assertEqual(set(vars(guard)), {'grid_since','stable_since','reset_since'})
