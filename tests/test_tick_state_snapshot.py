"""A coherent command snapshot avoids repeated event-loop round trips."""
import unittest
from threading import local,Thread
from unittest.mock import patch
from test_adapter_modulation import HeatingClimate
import test_fallback_recovery as fallback


class NativeReads:
    def get_state(self,*args,**kwargs):return None


class SnapshotController(HeatingClimate,NativeReads):
    pass


class TickStateSnapshotTests(unittest.TestCase):
    def app(self):
        app=SnapshotController();app._tick_read_local=local()
        app._tick_read_local.states={'sensor.room':{'state':'20.5','attributes':{'unit_of_measurement':'°C'},'last_updated':'now'}}
        app._tick_read_local.hits=0
        return app

    def test_cached_scalar_attributes_and_missing_do_not_call_async_api(self):
        app=self.app()
        with patch.object(NativeReads,'get_state',create=True,side_effect=AssertionError('Unexpected API round trip')):
            for _ in range(100):self.assertEqual(app.get_state('sensor.room'),'20.5')
            self.assertEqual(app.get_state('sensor.room',attribute='unit_of_measurement'),'°C')
            self.assertEqual(app.get_state('sensor.room',attribute='last_updated'),'now')
            self.assertEqual(app.get_state('sensor.room',attribute='state'),'20.5')
            self.assertEqual(app.get_state('sensor.missing',default='unknown'),'unknown')

    def test_all_records_are_isolated_from_caller_mutation(self):
        app=self.app()
        row=app.get_state('sensor.room',attribute='all');row['attributes']['unit_of_measurement']='K'
        self.assertEqual(app.get_state('sensor.room',attribute='unit_of_measurement'),'°C')

    def test_other_namespace_and_domain_reads_keep_native_semantics(self):
        app=self.app()
        with patch.object(NativeReads,'get_state',create=True,return_value='native') as api:
            self.assertEqual(app.get_state('sensor.room',namespace='other'),'native')
            self.assertEqual(app.get_state('sensor'),'native');self.assertEqual(api.call_count,2)

    def test_worker_thread_never_sees_command_snapshot(self):
        app=self.app();read=[]
        with patch.object(NativeReads,'get_state',create=True,return_value='live'):
            worker=Thread(target=lambda:read.append(app.get_state('sensor.room')));worker.start();worker.join()
        self.assertEqual(read,['live'])

    def test_refresh_before_actuation_replaces_stale_grid(self):
        app=self.app()
        with patch.object(NativeReads,'get_state',create=True,return_value={'binary_sensor.grid':{'state':'off','attributes':{}}}):
            app.refresh_tick_snapshot()
        self.assertEqual(app.get_state('binary_sensor.grid'),'off')

    def test_publication_updates_snapshot_including_same_frame_fallback(self):
        app=self.app();calls=[];app.set_state=lambda entity,**kwargs:calls.append((entity,kwargs))
        app.publish('sensor.heating_controller','Очікування',reason='план')
        self.assertEqual(app.get_state('sensor.heating_controller'),'Очікування')
        app.publish('sensor.heating_controller','Тимчасовий auto',reason='дані')
        self.assertEqual(app.get_state('sensor.heating_controller'),'Тимчасовий auto')
        self.assertEqual(len(calls),2)

    def test_dispatch_refresh_observes_newly_disabled_permissions(self):
        app=fallback.FallbackRecoveryTests().app();app._tick_read_local=local();app._tick_read_local.states={}
        calls=[]
        def refresh():calls.append(True);app.values['armed']='off'
        app.refresh_tick_snapshot=refresh
        app.apply_action('heat',False)
        self.assertEqual(calls,[True])
        self.assertTrue(all(data.get('temperature',0)<=app.attrs['current_temperature'] for _,data in app.calls))

    def test_failed_tick_clears_snapshot_for_later_callbacks(self):
        app=self.app();app.runtime_ready=True;app.cfg={'allow_actuation':False};app.memory={}
        app.update_in_progress=lambda:False
        app.tick_inner=lambda:(_ for _ in ()).throw(RuntimeError('boom'))
        app.record_failure=lambda *a,**k:{'incident_id':'test'}
        app.run_diagnostic=lambda *a,**k:None
        with patch.object(NativeReads,'get_state',side_effect=lambda entity_id=None,**kwargs:{} if entity_id is None else 'live'):
            app.tick();self.assertIsNone(app._tick_read_local.states)
            self.assertEqual(app.get_state('sensor.room'),'live')
