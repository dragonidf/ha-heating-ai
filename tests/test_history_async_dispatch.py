"""History must work with AppDaemon's default fully pinned worker pool."""
import asyncio
import inspect
import threading
import unittest
from heating_recovery import HeatingRecovery


class AsyncHistoryDispatchTests(unittest.IsolatedAsyncioTestCase):
    async def test_request_is_async_and_does_not_block_the_dispatch_loop(self):
        app=HeatingRecovery();main=threading.get_ident();started=threading.Event();release=threading.Event();threads=[]
        def request(event,data,kwargs=None,**extra):
            threads.append(threading.get_ident());started.set();release.wait(3)
        app.plan_history_requested=request
        self.assertTrue(inspect.iscoroutinefunction(app.plan_history_event))
        task=asyncio.create_task(app.plan_history_event('heating_plan_history',{'request_id':'read'}))
        for _ in range(100):
            if started.is_set():break
            await asyncio.sleep(.01)
        try:
            self.assertTrue(started.is_set());self.assertFalse(task.done());self.assertNotEqual(threads,[main])
            await asyncio.sleep(0)  # The main loop remains available while I/O runs.
        finally:release.set()
        await task

    async def test_result_uses_the_same_nonblocking_dispatch(self):
        app=HeatingRecovery();main=threading.get_ident();received=[]
        app.plan_history_loaded=lambda kwargs=None,**extra:received.append((threading.get_ident(),kwargs))
        self.assertTrue(inspect.iscoroutinefunction(app.plan_history_result))
        await app.plan_history_result({'result':{'request_id':'read'}})
        self.assertNotEqual(received[0][0],main)
        self.assertEqual(received[0][1]['result']['request_id'],'read')

    async def test_worker_exception_is_observable_not_silently_lost(self):
        app=HeatingRecovery();app.plan_history_requested=lambda *a,**k:(_ for _ in ()).throw(ValueError('bad read'))
        with self.assertRaisesRegex(ValueError,'bad read'):
            await app.plan_history_event('heating_plan_history',{})

    async def test_result_publication_uses_the_immutable_snapshot(self):
        app=HeatingRecovery();snapshot={'__energy_cycles':{}};app.history_read_snapshot=snapshot
        published=[];app.publish_plan_history=lambda **kwargs:published.append(kwargs)
        await app.plan_history_result({'result':{'request_id':'reader'}})
        self.assertIs(published[0]['source'],snapshot)
        self.assertEqual(published[0]['request_id'],'reader')
