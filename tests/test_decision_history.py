import gzip
import importlib.util
import json
import os
import tempfile
import time
import unittest
from datetime import datetime,timedelta
from pathlib import Path
from zoneinfo import ZoneInfo
from heating_recovery import DecisionJournal,confirmed_morning
from heating_model import DEFAULT_MODEL


class DecisionHistoryTests(unittest.TestCase):
    def test_existing_archive_recovers_interrupted_compaction_and_counts_once(self):
        with tempfile.TemporaryDirectory() as directory:
            stamp=time.time();journal=DecisionJournal(directory,max_bytes=10000);journal.directory.mkdir()
            day=datetime.fromtimestamp(stamp-86400,ZoneInfo('UTC')).strftime('%Y-%m-%d')
            path=journal.directory/(day+'.jsonl');raw=b'{"protected":true}\n'*1000;path.write_bytes(raw)
            archive=path.with_suffix('.jsonl.gz');archive.write_bytes(gzip.compress(raw))
            result=journal.append(dict(action='off'),stamp=stamp)
            self.assertFalse(result.get('storage_degraded'));self.assertFalse(path.exists())
            self.assertEqual(gzip.decompress(archive.read_bytes()),raw)
            self.assertEqual(journal.used_bytes,sum(p.stat().st_size for p in journal.directory.iterdir()))

    def test_different_existing_archive_never_replaces_original(self):
        with tempfile.TemporaryDirectory() as directory:
            stamp=time.time();journal=DecisionJournal(directory,max_bytes=10000);journal.directory.mkdir()
            day=datetime.fromtimestamp(stamp-86400,ZoneInfo('UTC')).strftime('%Y-%m-%d')
            path=journal.directory/(day+'.jsonl');raw=b'{"protected":true}\n'*1000;path.write_bytes(raw)
            path.with_suffix('.jsonl.gz').write_bytes(gzip.compress(b'different evidence'))
            result=journal.append(dict(action='off'),stamp=stamp)
            self.assertTrue(result.get('storage_degraded'));self.assertEqual(path.read_bytes(),raw)

    def test_closed_day_compression_recovers_budget_without_losing_protected_evidence(self):
        with tempfile.TemporaryDirectory() as directory:
            stamp=time.time();journal=DecisionJournal(directory,max_bytes=10000)
            journal.directory.mkdir();day=datetime.fromtimestamp(stamp-86400,ZoneInfo('UTC')).strftime('%Y-%m-%d')
            old=journal.directory/(day+'.jsonl');raw=(json.dumps(dict(reason='protected history',value=1))+'\n')*1000
            old.write_text(raw);os.utime(old,(stamp-86400,stamp-86400))
            result=journal.append(dict(action='off',reason='new evidence'),stamp=stamp)
            self.assertFalse(result.get('storage_degraded',False))
            archive=old.with_suffix('.jsonl.gz');self.assertEqual(gzip.decompress(archive.read_bytes()).decode(),raw)
            self.assertFalse(old.exists());self.assertLess(journal.used_bytes,journal.max_bytes)
            self.assertEqual(archive.stat().st_mtime,stamp-86400)

    def test_same_state_different_decisions_restart_and_replayable_snapshot(self):
        with tempfile.TemporaryDirectory() as directory:
            journal=DecisionJournal(directory)
            first=journal.append(dict(action='off',reason='Скасовано резерв',plan_id='p1'),dict(old=[1],new=[2]))
            second=journal.append(dict(action='off',reason='Збережено план',plan_id='p2'))
            restored=DecisionJournal(directory)
            self.assertEqual(restored.read_snapshot(first['snapshot_id']),dict(old=[1],new=[2]))
            records=[json.loads(line) for p in journal.directory.glob('*.jsonl') for line in p.read_text().splitlines()]
            self.assertEqual([r['decision_id'] for r in records],[first['decision_id'],second['decision_id']])
            self.assertNotEqual(first['decision_id'],second['decision_id'])

    def test_utf8_budget_externalizes_full_reason_without_truncation(self):
        with tempfile.TemporaryDirectory() as directory:
            journal=DecisionJournal(directory)
            reason='Точна причина скасування. '*2000
            record=journal.append(dict(action='off',reason=reason,plan_id='p'))
            self.assertLessEqual(len(json.dumps(record,ensure_ascii=False,separators=(',',':')).encode()),8192)
            self.assertTrue(record['external_attributes'])
            self.assertEqual(journal.read_snapshot(record['attributes_snapshot_id'])['reason'],reason)

    def test_rotation_only_new_diagnostics_and_retains_at_least_week(self):
        with tempfile.TemporaryDirectory() as directory:
            telemetry=Path(directory)/'telemetry.jsonl';telemetry.write_text('untouched')
            journal=DecisionJournal(directory,retention_days=1)
            stamp=time.time()
            a=journal.append(dict(action='off'),dict(old=True),stamp-8*86400)
            b=journal.append(dict(action='off'),dict(recent=True),stamp-6*86400)
            journal.last_rotation=0
            journal.append(dict(action='off'),stamp=stamp)
            self.assertFalse((journal.directory/(a['snapshot_id']+'.json.gz')).exists())
            self.assertTrue((journal.directory/(b['snapshot_id']+'.json.gz')).exists())
            self.assertEqual(telemetry.read_text(),'untouched')

    def test_disk_pressure_visible_without_deleting_protected_evidence(self):
        with tempfile.TemporaryDirectory() as directory:
            journal=DecisionJournal(directory,max_bytes=4096)
            record=journal.append(dict(action='off'),dict(data='evidence'))
            protected=journal.directory/(record['snapshot_id']+'.json.gz')
            journal.max_bytes=1
            failed=journal.append(dict(action='heat',reason='test'))
            self.assertTrue(failed['storage_degraded'])
            self.assertTrue(protected.exists())

    def test_staged_pack_is_additive_and_reader_accepts_exact_packed_bytes(self):
        with tempfile.TemporaryDirectory() as directory:
            journal=DecisionJournal(directory)
            values=[dict(i=i,data=list(range(2000))) for i in range(8)]
            stamp=time.time()
            ids=[journal.snapshot(v,stamp) for v in values]
            before={p.name:p.read_bytes() for p in journal.directory.glob('*.json.gz')}
            result=journal.stage_snapshot_pack(ids)
            self.assertEqual(result['snapshots'],8)
            self.assertEqual(before,{p.name:p.read_bytes() for p in journal.directory.glob('*.json.gz')})
            restored=DecisionJournal(directory)
            for key,value in zip(ids,values):
                self.assertEqual(restored.read_snapshot(key),value)
                self.assertEqual(restored._packed_snapshot(key),gzip.decompress(before[key+'.json.gz']))
            # Simulate a future authorized promotion, not done by staging.
            (journal.directory/(ids[0]+'.json.gz')).unlink()
            self.assertEqual(restored.read_snapshot(ids[0]),values[0])
            blob=journal.directory/'packed_v1'/(result['pack']+'.xz')
            blob.write_bytes(blob.read_bytes()[:-4])
            with self.assertRaises(ValueError):restored.read_snapshot(ids[0])
            # A damaged staged copy never makes existing originals unreadable.
            self.assertEqual(restored.read_snapshot(ids[1]),values[1])

    def test_pack_promotion_requires_savings_and_survives_partial_removal(self):
        from unittest.mock import patch
        with tempfile.TemporaryDirectory() as directory:
            journal=DecisionJournal(directory)
            stamp=time.time()
            values=[dict(i=i,data=list(range(10000))) for i in range(8)]
            ids=[journal.snapshot(v,stamp) for v in values]
            staged=journal.stage_snapshot_pack(ids,require_savings=True)
            self.assertEqual(staged['status'],'staged')
            self.assertLessEqual(staged['packed_bytes']*2,staged['legacy_bytes'])
            key=staged['pack']
            with self.assertRaises(PermissionError):journal.promote_snapshot_pack(key)
            original_unlink=Path.unlink
            def interrupt(path,*args,**kwargs):
                if path.name==ids[1]+'.json.gz':raise OSError('interrupted promotion')
                return original_unlink(path,*args,**kwargs)
            with patch.object(Path,'unlink',interrupt):
                with self.assertRaises(OSError):journal.promote_snapshot_pack(key,allow_remove=True)
            restored=DecisionJournal(directory)
            for digest,value in zip(ids,values):self.assertEqual(restored.read_snapshot(digest),value)
            result=restored.promote_snapshot_pack(key,allow_remove=True)
            self.assertEqual(result['removed'],7)
            self.assertEqual(restored.promote_snapshot_pack(key,allow_remove=True)['removed'],0)
            self.assertFalse(list(restored.directory.glob('*.json.gz')))
            for digest,value in zip(ids,values):self.assertEqual(restored.read_snapshot(digest),value)

    def test_pack_encoding_does_not_hold_journal_lock_and_stale_publication_is_rejected(self):
        from unittest.mock import patch
        from threading import Thread,Event
        with tempfile.TemporaryDirectory() as directory:
            journal=DecisionJournal(directory)
            stamp=time.time()
            ids=[journal.snapshot(dict(i=i,data=list(range(1000))),stamp) for i in range(2)]
            original=journal._prepare_snapshot_pack
            prepared=Event();release=Event();errors=[]
            def slow_prepare(keys):
                result=original(keys)
                prepared.set()
                if not release.wait(5):raise RuntimeError('test timeout')
                return result
            def stage():
                try:journal.stage_snapshot_pack(ids)
                except Exception as exc:errors.append(exc)
            with patch.object(journal,'_prepare_snapshot_pack',side_effect=slow_prepare):
                worker=Thread(target=stage)
                worker.start()
                try:
                    self.assertTrue(prepared.wait(5))
                    self.assertTrue(journal.lock.acquire(timeout=1))
                    try:
                        journal.snapshot(dict(i=0,data=list(range(1000))),stamp+60)
                    finally:journal.lock.release()
                finally:
                    release.set();worker.join(5)
            self.assertFalse(worker.is_alive())
            self.assertEqual(len(errors),1)
            self.assertIsInstance(errors[0],ValueError)
            self.assertFalse((journal.directory/'packed_v1').exists())
            self.assertEqual(len(list(journal.directory.glob('*.json.gz'))),2)

    def test_promotion_verification_outside_lock_rejects_concurrent_reuse(self):
        from unittest.mock import patch
        from threading import Thread,Event
        with tempfile.TemporaryDirectory() as directory:
            journal=DecisionJournal(directory)
            stamp=time.time()
            values=[dict(i=i,data=list(range(5000))) for i in range(8)]
            ids=[journal.snapshot(v,stamp) for v in values]
            key=journal.stage_snapshot_pack(ids,require_savings=True)['pack']
            original=journal._prepare_pack_promotion
            prepared=Event();release=Event();errors=[]
            def slow_prepare(pack):
                result=original(pack);prepared.set()
                if not release.wait(5):raise RuntimeError('test timeout')
                return result
            def promote():
                try:journal.promote_snapshot_pack(key,allow_remove=True)
                except Exception as exc:errors.append(exc)
            with patch.object(journal,'_prepare_pack_promotion',side_effect=slow_prepare):
                worker=Thread(target=promote);worker.start()
                try:
                    self.assertTrue(prepared.wait(5))
                    self.assertTrue(journal.lock.acquire(timeout=1))
                    try:journal.snapshot(values[0],stamp+60)
                    finally:journal.lock.release()
                finally:
                    release.set();worker.join(5)
            self.assertFalse(worker.is_alive())
            self.assertEqual(len(errors),1)
            self.assertIsInstance(errors[0],ValueError)
            self.assertEqual(len(list(journal.directory.glob('*.json.gz'))),8)
            for key,value in zip(ids,values):self.assertEqual(journal.read_snapshot(key),value)

    def test_cold_batch_compaction_preserves_hot_tiny_and_expired_sources(self):
        with tempfile.TemporaryDirectory() as directory:
            journal=DecisionJournal(directory,retention_days=14)
            stamp=time.time()
            # Generate large deterministic overlapping data and distinct hashes.
            from random import Random
            rng=Random(42)
            shared=[rng.random() for _ in range(6000)]
            cold=[dict(i=i,values=shared) for i in range(9)]
            hot=dict(i=100,values=shared)
            tiny=dict(i=101)
            old=dict(i=102,values=shared)
            cold_ids=[journal.snapshot(v,stamp-4*86400+i) for i,v in enumerate(cold)]
            hot_id=journal.snapshot(hot,stamp-3600)
            tiny_id=journal.snapshot(tiny,stamp-5*86400)
            old_id=journal.snapshot(old,stamp-15*86400)
            # No rotation is performed by selection or compaction.
            self.assertEqual(journal.cold_pack_candidates(stamp),cold_ids[:8])
            with self.assertRaises(PermissionError):journal.compact_cold_snapshot_batch(stamp)
            result=journal.compact_cold_snapshot_batch(stamp,allow_remove=True)
            self.assertEqual(result['status'],'promoted',result)
            self.assertEqual(result['removed'],8)
            for key,value in zip(cold_ids,cold):self.assertEqual(journal.read_snapshot(key),value)
            for key in (cold_ids[-1],hot_id,tiny_id,old_id):
                self.assertTrue((journal.directory/(key+'.json.gz')).exists())
            self.assertEqual(journal.compact_cold_snapshot_batch(stamp,allow_remove=True)['status'],'no_eligible_batch')

    def test_online_cold_pack_updates_accounting_and_preserves_rollback(self):
        from random import Random
        from threading import Event
        from unittest.mock import patch
        from tools.pack_decision_archive import restore_mixed_archive
        with tempfile.TemporaryDirectory() as directory:
            stamp=time.time()
            journal=DecisionJournal(directory,retention_days=14,max_bytes=32*1024*1024)
            rng=Random(42)
            shared=[rng.random() for _ in range(6000)]
            values=[dict(i=i,values=shared) for i in range(8)]
            ids=[journal.snapshot(value,stamp-4*86400+i) for i,value in enumerate(values)]
            journal._prepare(stamp)
            before=journal.used_bytes
            # No whole-directory scan under the online publication lock.
            with patch.object(Path,'iterdir',side_effect=AssertionError('online rescan')):
                result=journal.compact_cold_snapshot_batch(stamp,allow_remove=True,stop=Event())
            self.assertEqual(result['status'],'promoted',result)
            self.assertEqual(result['removed'],8)
            self.assertEqual(journal.used_bytes,before-result['removed_bytes']+result['packed_bytes'])
            self.assertEqual(journal.used_bytes,sum(p.stat().st_size for p in journal.directory.rglob('*') if p.is_file()))
            self.assertEqual(journal.snapshot_bytes,journal.used_bytes)
            self.assertEqual(journal.packed_bytes,journal.used_bytes)
            self.assertEqual(journal.snapshot_count,8)
            self.assertEqual(journal.packed_snapshot_count,8)
            self.assertEqual(journal.packed_blocks,1)
            restored=DecisionJournal(directory)
            for key,value in zip(ids,values):
                self.assertEqual(restored.read_snapshot(key),value)
            target=Path(directory)/'legacy'
            self.assertEqual(restore_mixed_archive(journal.directory,target)['restored_packed_snapshots'],8)
            for key,value in zip(ids,values):
                self.assertEqual(json.loads(gzip.decompress((target/(key+'.json.gz')).read_bytes())),value)

    def test_online_batches_reuse_hourly_scan_and_do_not_rescan_empty_queue(self):
        from random import Random
        from threading import Event
        from unittest.mock import patch
        with tempfile.TemporaryDirectory() as directory:
            journal=DecisionJournal(directory,max_bytes=32*1024*1024)
            stamp=time.time()
            rng=Random(42)
            shared=[rng.random() for _ in range(6000)]
            values=[dict(i=i,values=shared) for i in range(16)]
            ids=[journal.snapshot(value,stamp-4*86400+i) for i,value in enumerate(values)]
            journal._prepare(stamp)
            with patch.object(journal,'cold_pack_candidates',side_effect=AssertionError('redundant scan')):
                for _ in range(2):
                    result=journal.compact_cold_snapshot_batch(stamp,allow_remove=True,stop=Event())
                    self.assertEqual(result['status'],'promoted',result)
                    self.assertEqual(result['removed'],8)
                result=journal.compact_cold_snapshot_batch(stamp,allow_remove=True,stop=Event())
                self.assertEqual(result['status'],'no_eligible_batch',result)
            for key,value in zip(ids,values):
                self.assertEqual(journal.read_snapshot(key),value)
            self.assertEqual(journal.used_bytes,sum(p.stat().st_size for p in journal.directory.rglob('*') if p.is_file()))

    def test_online_stop_fences_publication_and_removal(self):
        from random import Random
        from threading import Event
        from unittest.mock import patch
        with tempfile.TemporaryDirectory() as directory:
            stamp=time.time()
            journal=DecisionJournal(directory,retention_days=14,max_bytes=32*1024*1024)
            rng=Random(42)
            shared=[rng.random() for _ in range(6000)]
            values=[dict(i=i,values=shared) for i in range(8)]
            ids=[journal.snapshot(value,stamp-4*86400+i) for i,value in enumerate(values)]
            journal._prepare(stamp)
            original=journal._prepare_snapshot_pack
            stop=Event()
            def prepare(keys):
                result=original(keys)
                stop.set()
                return result
            with patch.object(journal,'_prepare_snapshot_pack',side_effect=prepare):
                with self.assertRaisesRegex(ValueError,'stopped'):
                    journal.stage_snapshot_pack(ids,require_savings=True,cold_at=stamp,stop=stop)
            self.assertFalse(list(journal.directory.glob('*.snapshot-ref')))
            stop.clear()
            staged=journal.stage_snapshot_pack(ids,require_savings=True,cold_at=stamp,stop=stop)
            self.assertEqual(staged['status'],'staged')
            verify=journal._prepare_pack_promotion
            def prepare_promotion(key):
                result=verify(key)
                stop.set()
                return result
            with patch.object(journal,'_prepare_pack_promotion',side_effect=prepare_promotion):
                with self.assertRaisesRegex(ValueError,'stopped'):
                    journal.promote_snapshot_pack(staged['pack'],allow_remove=True,stop=stop)
            self.assertEqual(len(list(journal.directory.glob('*.json.gz'))),8)
            self.assertEqual(journal.used_bytes,sum(p.stat().st_size for p in journal.directory.rglob('*') if p.is_file()))
            for key,value in zip(ids,values):self.assertEqual(journal.read_snapshot(key),value)

    def test_cold_scan_stops_without_publishing_partial_selection(self):
        from threading import Event
        from unittest.mock import patch
        from types import SimpleNamespace
        with tempfile.TemporaryDirectory() as directory:
            journal=DecisionJournal(directory)
            stamp=time.time()
            stop=Event()
            visited=[]
            class Entries:
                def __enter__(self):return self
                def __exit__(self,*args):return False
                def __iter__(self):
                    for i in range(100000):
                        visited.append(i)
                        if i==3:stop.set()
                        yield SimpleNamespace(name=('%064x'%i)+'.json.gz',
                            is_file=lambda **kw:True,stat=lambda **kw:
                            SimpleNamespace(st_mtime=stamp-4*86400,st_size=32768))
            with patch('heating_recovery.os.scandir',return_value=Entries()), \
                 patch.object(journal,'stage_snapshot_pack') as stage:
                result=journal.compact_cold_snapshot_batch(stamp,allow_remove=True,stop=stop)
            self.assertEqual(result['status'],'stopped')
            self.assertEqual(len(visited),4)
            stage.assert_not_called()
            self.assertEqual(journal.cold_pack_candidates(stamp,stop=stop),[])

    def test_cold_publication_rejects_recently_reused_source(self):
        with tempfile.TemporaryDirectory() as directory:
            journal=DecisionJournal(directory)
            stamp=time.time()
            ids=[journal.snapshot(dict(i=i,data=list(range(10000))),stamp-4*86400) for i in range(2)]
            journal.snapshot(dict(i=0,data=list(range(10000))),stamp)
            with self.assertRaises(ValueError):
                journal.stage_snapshot_pack(ids,cold_at=stamp)
            self.assertEqual(len(list(journal.directory.glob('*.json.gz'))),2)
            self.assertFalse((journal.directory/'packed_v1').exists())

    def test_pack_savings_gate_keeps_tiny_originals(self):
        with tempfile.TemporaryDirectory() as directory:
            journal=DecisionJournal(directory)
            ids=[journal.snapshot(dict(i=i),time.time()) for i in range(2)]
            result=journal.stage_snapshot_pack(ids,require_savings=True)
            self.assertEqual(result['status'],'insufficient_savings')
            self.assertFalse((journal.directory/'packed_v1').exists())
            self.assertEqual(len(list(journal.directory.glob('*.json.gz'))),2)

    def test_packed_retention_accounts_for_shared_block_and_reuse(self):
        with tempfile.TemporaryDirectory() as directory:
            journal=DecisionJournal(directory,retention_days=7,max_bytes=8*1024*1024)
            stamp=time.time()
            values=[dict(i=i,data=list(range(500))) for i in range(2)]
            ids=[journal.snapshot(v,stamp) for v in values]
            result=journal.stage_snapshot_pack(ids)
            folder=journal.directory/'packed_v1'
            # Simulate authorized future promotion: both originals removed.
            for key in ids:(journal.directory/(key+'.json.gz')).unlink()
            journal._prepare(stamp)
            self.assertEqual(journal.snapshot_count,2)
            self.assertEqual(journal.packed_snapshot_count,2)
            self.assertEqual(journal.used_bytes,sum(p.stat().st_size for p in journal.directory.rglob('*') if p.is_file()))
            self.assertEqual(journal.snapshot_bytes,journal.used_bytes)
            refreshed=stamp+6*86400
            self.assertEqual(journal.snapshot(values[0],refreshed),ids[0])
            self.assertFalse((journal.directory/(ids[0]+'.json.gz')).exists())
            journal._prepare(stamp+8*86400)
            self.assertEqual(journal.read_snapshot(ids[0]),values[0])
            self.assertTrue((folder/(result['pack']+'.xz')).exists())
            self.assertFalse((journal.directory/(ids[1]+'.snapshot-ref')).exists())
            self.assertEqual(journal.snapshot_count,1)
            journal._prepare(stamp+14*86400)
            self.assertFalse((folder/(result['pack']+'.xz')).exists())
            self.assertEqual(journal.used_bytes,0)

    def test_packed_gc_fails_closed_on_invalid_pointer(self):
        with tempfile.TemporaryDirectory() as directory:
            journal=DecisionJournal(directory)
            stamp=time.time()
            ids=[journal.snapshot(dict(i=i),stamp) for i in range(2)]
            result=journal.stage_snapshot_pack(ids)
            pointer=journal.directory/(ids[0]+'.snapshot-ref')
            pointer.write_text('broken')
            journal._prepare(stamp+16*86400)
            self.assertTrue(journal.packed_gc_error)
            self.assertTrue((journal.directory/'packed_v1'/(result['pack']+'.xz')).exists())
            self.assertTrue(pointer.exists())

    def test_staging_failure_keeps_all_legacy_snapshots(self):
        from unittest.mock import patch
        with tempfile.TemporaryDirectory() as directory:
            journal=DecisionJournal(directory)
            ids=[journal.snapshot(dict(i=i),time.time()) for i in range(2)]
            before={p.name:p.read_bytes() for p in journal.directory.glob('*.json.gz')}
            original=journal._atomic_pack_file
            def interrupted(path,raw,mtime=None):
                if path.name.endswith('.snapshot-ref'):raise OSError('interrupted')
                return original(path,raw,mtime)
            with patch.object(journal,'_atomic_pack_file',side_effect=interrupted):
                with self.assertRaises(OSError):journal.stage_snapshot_pack(ids)
            self.assertEqual(before,{p.name:p.read_bytes() for p in journal.directory.glob('*.json.gz')})

    def test_snapshot_corruption_and_bad_identifiers_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            journal=DecisionJournal(directory)
            record=journal.append(dict(action='off'),dict(actual='evidence'))
            with self.assertRaises(ValueError):journal.read_snapshot('../state.json')
            (journal.directory/(record['snapshot_id']+'.json.gz')).write_bytes(gzip.compress(b'{}'))
            with self.assertRaises(ValueError):journal.read_snapshot(record['snapshot_id'])

    def test_journal_normal_cycle_latency(self):
        with tempfile.TemporaryDirectory() as directory:
            journal=DecisionJournal(directory)
            start=time.perf_counter()
            for i in range(30):
                journal.append(dict(action='off',reason='Тест',index=i),dict(curve=[dict(ts=j,t=21) for j in range(100)]))
            self.assertLess(time.perf_counter()-start,2.)

    def test_saved_inputs_reproduce_cancelled_morning_and_trajectory(self):
        from heating_stability import stable_plan
        spec=importlib.util.spec_from_file_location('replay_decision',Path(__file__).parents[1]/'tools/replay_decision.py')
        module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)
        now=datetime(2026,10,21,6,30,tzinfo=ZoneInfo('Europe/Kiev'))
        settings=dict(start='23:00',end='07:00',strategy='Тільки в дозволені години',
            target=21.5,lower=21,upper=22,night_target=21,night_lower=20.5,night_upper=21.5,
            sleep_start='23:00',sleep_end='06:30',split_comfort=True,morning_min_minutes=30,
            day_price=4.32,night_price=2.16)
        model=dict(DEFAULT_MODEL,validated=True)
        old,reference=stable_plan(now,21.1,22,10,0,model,[],settings,3.)
        result,_=stable_plan(now,24,21,20,15,model,[],settings,3.,reference)
        snapshot=dict(inputs=dict(now=now.isoformat(),timezone=str(now.tzinfo),room=24,floor=21,outside=20,fire=15,
            model=model,weather=[],settings=settings,power_kw=3.,previous=reference),old_plan=reference,new_plan=result)
        with tempfile.TemporaryDirectory() as directory:
            journal=DecisionJournal(directory)
            record=journal.append(dict(action='off',reason=result['morning_reserve']['cancellation_reason']),snapshot)
            actual=module.replay_snapshot(DecisionJournal(directory).read_snapshot(record['snapshot_id']))
            self.assertEqual(actual['heat_segments'],result['heat_segments'])
            self.assertEqual(actual['morning_reserve'],result['morning_reserve'])
            self.assertEqual(actual['energy'],result['energy'])


class ConfirmedMorningTests(unittest.TestCase):
    def setUp(self):
        self.at=datetime(2026,10,21,6,30,tzinfo=ZoneInfo('Europe/Kiev'))
        self.settings=dict(start='23:00',end='07:00')
        self.memory={}

    def point(self,seconds,kw=3,energy=100,auto=True,grid='on',source='balance'):
        return confirmed_morning(self.memory,self.at+timedelta(seconds=seconds),self.settings,
                                 None if kw is None else kw*1000,energy,grid,auto,source)

    def test_real_3_6_9_kw_minutes_and_counter_energy(self):
        energy=100.
        self.point(0,energy=energy)
        for seconds,kw in [(15,3),(30,6),(45,9)]:
            energy+=kw*15/3600
            result=self.point(seconds,kw,energy)
        self.assertEqual(result['confirmed_completed_minutes'],.75)
        self.assertAlmostEqual(result['confirmed_morning_kwh'],.075)

    def test_off_unknown_grid_and_duty_zeros_get_no_credit(self):
        self.point(0)
        self.point(15,0)
        self.point(30,None)
        self.point(45,3,grid='off')
        result=self.point(60,3)
        self.assertEqual(result['confirmed_completed_minutes'],0)
        self.assertEqual(result['confirmed_morning_kwh'],0)

    def test_reload_preserves_totals_never_credits_unobserved_gap(self):
        self.point(0);result=self.point(15,energy=100.0125)
        self.memory.pop('morning_observation')
        result=self.point(900,energy=101)
        self.assertEqual(result['confirmed_completed_minutes'],.25)
        self.assertEqual(result['morning_elapsed_minutes'],.25)
        self.point(915,energy=101.0125)
        self.assertEqual(self.point(930,energy=101.025)['confirmed_completed_minutes'],.75)

    def test_manual_or_armed_off_not_automatic_success(self):
        self.point(0,auto=False)
        result=self.point(15,energy=100.0125,auto=False)
        self.assertEqual(result['confirmed_completed_minutes'],0)
        self.assertEqual(result['manual_completed_minutes'],.25)
        self.assertEqual(result['morning_elapsed_minutes'],0)
        # Manual 07:40 cannot become tomorrow's automated reserve credit.
        result=self.point(4200,energy=102,auto=False)
        self.assertEqual(result['confirmed_completed_minutes'],0)

    def test_fallback_estimate_and_restart_gap_are_not_confirmed(self):
        self.point(0)
        self.point(15,source='fallback')
        self.point(30)
        result=self.point(300)
        self.assertEqual(result['confirmed_completed_minutes'],0)

class RecorderVerificationTests(unittest.TestCase):
    def test_full_history_same_state_attributes_is_required(self):
        import asyncio
        from unittest.mock import AsyncMock
        from heating_recovery import HeatingRecovery
        app=HeatingRecovery();app.memory={};states=[]
        app.publish=lambda *args,**kw:states.append((args,kw))
        required=dict(plan_id='p',cycle_id='cycle',sources={},measurements={},policy={},reserve={},command={},inputs={})
        app.get_history=AsyncMock(return_value=[[dict(state='off',attributes=dict(required,decision_id='a',schema_version=1,reason='old')),
            dict(state='off',attributes=dict(required,decision_id='b',schema_version=1,reason='new'))]])
        asyncio.run(app.verify_decision_history())
        self.assertEqual(app.memory['decision_recorder_status'],'verified_same_state_attributes')
        kwargs=app.get_history.call_args.kwargs
        self.assertFalse(kwargs['minimal_response'])
        self.assertFalse(kwargs['significant_changes_only'])
        self.assertFalse(kwargs['no_attributes'])
        app.get_history=AsyncMock(return_value=[[dict(state='off',attributes={})]])
        asyncio.run(app.verify_decision_history())
        self.assertTrue(app.memory['decision_recorder_status'].startswith('unverified'))
        app.get_history=AsyncMock(return_value=[[dict(state='off',attributes=dict(decision_id='a',schema_version=1,reason='old')),
            dict(state='off',attributes=dict(decision_id='b',schema_version=1,reason='new'))]])
        asyncio.run(app.verify_decision_history())
        self.assertTrue(app.memory['decision_recorder_status'].startswith('unverified'))

class AdapterEvidenceTests(unittest.TestCase):
    def test_source_timestamps_not_replaced_and_duplicate_fast_ticks_suppressed(self):
        from heating_recovery import HeatingRecovery
        from heating_model import plan
        app=HeatingRecovery()
        app.climate_tz=ZoneInfo('Europe/Kiev')
        at=datetime(2026,10,21,6,30,tzinfo=app.climate_tz)
        app.now=lambda:at
        app.cfg={k:'sensor.test_'+k for k in ('room','return','outside','fireplace','power_balance','thermostat')}
        app.cfg['allow_actuation']=True
        app.raw=lambda key:dict(mode='Автоматичне керування',armed='on').get(key)
        app.val=lambda key:3000
        measured=(at-timedelta(minutes=12)).isoformat()
        app.get_state=lambda *args,**kw:dict(state='21.4',last_reported=measured)
        app.grid_state=lambda:'on';app.model=dict(DEFAULT_MODEL)
        app.memory=dict(owned=True,last_command='heat')
        app.log=lambda *args,**kw:self.fail(str(args))
        app.power_source='balance';app.release_version='test'
        settings=dict(start='23:00',end='07:00',strategy='Тільки в дозволені години',target=21.5,lower=21,upper=22,
            night_target=21,night_lower=20.5,night_upper=21.5,sleep_start='23:00',sleep_end='06:30',
            day_price=4.32,night_price=2.16,split_comfort=True,morning_min_minutes=30)
        forecast=plan(at,21.4,22,10,0,app.model,[],settings,3)
        app.decision_context=dict(settings=settings,forecast=forecast,action='heat',reason='Тест',measurements=dict(room=21.4))
        published=[]
        app.publish=lambda entity,state,**attrs:published.append((entity,state,attrs))
        with tempfile.TemporaryDirectory() as directory:
            app.decision_journal=DecisionJournal(directory)
            app.power_observation=dict(at=1.,watts=0.,quality='measured',reason='primary_balance')
            app.memory['power_observation_ledger']=dict(last=dict(at=1.,watts=0.),measured_kwh=0.)
            app.decision_event('tick_change')
            app.decision_context['measurements']['room']=21.401
            app.power_observation['at']=16.
            app.memory['power_observation_ledger']['last']['at']=16.
            app.get_state=lambda *args,**kw:dict(state='21.4',last_reported=(at-timedelta(minutes=11)).isoformat())
            app.decision_event('tick_change')
            decisions=[r for r in published if r[0]=='sensor.heating_decision']
            self.assertEqual(len(decisions),1)
            attrs=decisions[0][2]
            self.assertNotIn('external_attributes',attrs)
            self.assertLessEqual(len(json.dumps(attrs,ensure_ascii=False).encode()),8192)
            self.assertEqual(attrs['energy_counter_kwh'],3000)
            self.assertEqual(attrs['sources']['room']['reported_at'],measured)
            self.assertEqual(attrs['sources']['room']['age_seconds'],720)
            self.assertNotEqual(attrs['recorded_at'],measured)
            app.decision_event('full_recompute',force=True)
            self.assertEqual(len([r for r in published if r[0]=='sensor.heating_decision']),2)

    def test_prediction_errors_require_matching_horizon_and_execution(self):
        from heating_recovery import HeatingRecovery
        app=HeatingRecovery();app.memory={};app.model=dict(DEFAULT_MODEL,version=4)
        app.raw=lambda key:'on' if key=='armed' else 'Автоматичне керування'
        app.grid_state=lambda:'on'
        app.val=lambda key:100.
        now=datetime(2026,10,21,6,30,tzinfo=ZoneInfo('Europe/Kiev'))
        point=dict(ts=now.timestamp(),hours=24,temperature=21.4,model=dict(app.model),valid=True,segments=[],start_energy=100.,expected_energy=0.)
        app.memory['reserve_predictions']=[point]
        app.validate_reserve_predictions(now,21.3,0,True)
        self.assertAlmostEqual(app.memory['reserve_errors'][0]['error'],.1)
        self.assertTrue(app.memory['reserve_errors'][0]['validated'])
        app.memory['reserve_predictions']=[dict(point,valid=True)]
        app.validate_reserve_predictions(now,21.3,3000,True) # manual/unplanned heat
        self.assertEqual(len(app.memory['reserve_errors']),1)

    def test_long_horizon_validation_stores_merged_execution_intervals(self):
        from heating_recovery import HeatingRecovery
        app=HeatingRecovery();app.memory={};app.model=dict(DEFAULT_MODEL,version=4)
        app.raw=lambda key:'on' if key=='armed' else 'Автоматичне керування'
        app.grid_state=lambda:'on';app.val=lambda key:100.
        now=datetime(2026,10,21,23,tzinfo=ZoneInfo('Europe/Kiev'))
        start=now.timestamp()
        segments=[dict(start=start+i*300,end=start+(i+1)*300,kwh=.25) for i in range(12)]
        forecast=dict(curve=[dict(ts=start+6*3600,temperature=21.4,heat_segments=segments)])
        app.validate_reserve_predictions(now,21.4,3000,True,forecast)
        pending=app.memory['reserve_predictions']
        self.assertEqual(len(pending[0]['segments']),1)
        self.assertEqual(pending[0]['segments'][0],dict(start=start,end=start+3600))
        self.assertEqual(pending[0]['expected_energy'],3.)

class RecorderFilterCompatibilityTests(unittest.TestCase):
    def test_presence_based_history_flags_are_omitted_instead_of_false(self):
        import asyncio
        from heating_recovery import HeatingRecovery
        app=HeatingRecovery();app.memory={};app.publish=lambda *a,**k:None
        required=dict(plan_id='p',cycle_id='c',sources={},measurements={},policy={},reserve={},command={},inputs={},reason='r',schema_version=1)
        async def history(**kwargs):
            # HA REST flags enable filtering by presence, even for '=False'.
            if any(kwargs.get(key) is not None for key in ('minimal_response','no_attributes','significant_changes_only')):
                return [[dict(state='off',attributes={})]]
            return [[dict(state='off',attributes=dict(required,decision_id=value)) for value in ('a','b')]]
        app.get_history=history
        asyncio.run(app.verify_decision_history())
        self.assertEqual(app.memory['decision_recorder_status'],'verified_same_state_attributes')
