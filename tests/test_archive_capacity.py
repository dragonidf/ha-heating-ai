"""Retention-aware headroom must never trade protected evidence for free space."""
import os
from pathlib import Path
import tempfile
import time
import unittest
from unittest.mock import patch
from types import SimpleNamespace
from heating_recovery import DecisionJournal


class ArchiveCapacityTests(unittest.TestCase):
    def limits(self):
        return patch.multiple(DecisionJournal,DEFAULT_MAX_BYTES=10000,HARD_MAX_BYTES=100000,
                              CAPACITY_QUANTUM=1000,MIN_FREE_BYTES=2000,create=True)

    def seed(self,directory):
        folder=Path(directory)/'decision_history';folder.mkdir(exist_ok=True)
        path=folder/'protected.json.gz';raw=os.urandom(9300);path.write_bytes(raw)
        stamp=time.time();os.utime(path,(stamp-3*86400,stamp-3*86400))
        return path,raw,stamp

    def test_early_compaction_is_single_day_and_pressure_falls_back_to_original_full_pass(self):
        import gzip
        from datetime import datetime, timezone
        with tempfile.TemporaryDirectory() as directory:
            now=time.time()
            journal=DecisionJournal(directory,max_bytes=10000)
            journal.directory.mkdir()
            contents=[b'{"x":"stable"}\n'*130,b'{"x":"stable"}\n'*450]
            paths=[]
            for days,raw in zip((2,1),contents):
                name=datetime.fromtimestamp(now-days*86400,timezone.utc).strftime('%Y-%m-%d')+'.jsonl'
                path=journal.directory/name
                path.write_bytes(raw)
                os.utime(path,(now-days*86400,now-days*86400))
                paths.append(path)
            journal._prepare(now)
            self.assertEqual(sum(p.exists() for p in paths),1)
            self.assertEqual(sum(p.with_suffix('.jsonl.gz').exists() for p in paths),1)
            journal._prepare(now+6*3600+1)
            self.assertEqual(sum(p.exists() for p in paths),0)
            for p,raw in zip(paths,contents):
                self.assertEqual(gzip.decompress(p.with_suffix('.jsonl.gz').read_bytes()),raw)

    def test_midrange_journal_compresses_closed_day_losslessly_without_new_polling(self):
        import gzip
        from datetime import datetime, timezone
        with tempfile.TemporaryDirectory() as directory:
            now=time.time()
            day=datetime.fromtimestamp(now-86400,timezone.utc).strftime('%Y-%m-%d')
            folder=Path(directory)/'decision_history'
            folder.mkdir()
            old=folder/(day+'.jsonl')
            original=(b'{"event":"unchanged","value":123}\n'*235)
            self.assertGreater(len(original),6000)
            old.write_bytes(original)
            os.utime(old,(now-86400,now-86400))
            journal=DecisionJournal(directory,max_bytes=10000)
            journal._prepare(now)
            compressed=old.with_suffix('.jsonl.gz')
            self.assertFalse(old.exists())
            self.assertEqual(gzip.decompress(compressed.read_bytes()),original)
            health=journal.health()
            self.assertEqual(health["daily_log_bytes"],compressed.stat().st_size)
            self.assertEqual(health["snapshot_bytes"],0)
            self.assertEqual(health["snapshot_count"],0)
            self.assertEqual(health["used_bytes"],compressed.stat().st_size)
            before=compressed.read_bytes()
            journal._prepare(now+60)
            self.assertEqual(compressed.read_bytes(),before)

    def test_restart_restores_disk_headroom_before_throttled_compaction(self):
        from datetime import datetime, timezone
        with tempfile.TemporaryDirectory() as directory,self.limits(),patch(
                'heating_recovery.shutil.disk_usage',
                return_value=SimpleNamespace(free=500000,total=1000000)):
            folder=Path(directory)/'decision_history'
            folder.mkdir()
            now=time.time()
            protected=folder/'longterm.json.gz'
            snapshot=os.urandom(30000)
            protected.write_bytes(snapshot)
            os.utime(protected,(now-3*86400,now-3*86400))
            paths=[]
            for days in (2,1):
                day=datetime.fromtimestamp(now-days*86400,timezone.utc).strftime('%Y-%m-%d')
                path=folder/(day+'.jsonl')
                path.write_bytes(b'{"event":"old_day"}\\n'*100)
                os.utime(path,(now-days*86400,now-days*86400))
                paths.append(path)
            journal=DecisionJournal(directory)
            journal._prepare(now)
            self.assertGreater(journal.max_bytes,journal.DEFAULT_MAX_BYTES)
            self.assertEqual(protected.read_bytes(),snapshot)
            # The temporary 10 KiB initial budget must NOT force both
            # historic days to compress synchronously on startup.
            self.assertTrue(any(path.exists() for path in paths))
            self.assertEqual(journal.snapshot_bytes,len(snapshot))
            self.assertEqual(journal.snapshot_count,1)

    def test_default_grows_for_retention_and_preserves_all_original_bytes(self):
        with tempfile.TemporaryDirectory() as directory,self.limits(),patch('heating_recovery.shutil.disk_usage',return_value=SimpleNamespace(free=500000,total=1000000)):
            path,raw,stamp=self.seed(directory);journal=DecisionJournal(directory)
            journal._prepare(stamp)
            self.assertGreater(journal.max_bytes,10000);self.assertLessEqual(journal.max_bytes,100000)
            self.assertEqual(path.read_bytes(),raw);self.assertEqual(journal.used_bytes,len(raw))
            self.assertEqual(journal.health()['capacity_policy'],'retention_headroom')

    def test_explicit_budget_is_respected(self):
        with tempfile.TemporaryDirectory() as directory,self.limits():
            path,raw,stamp=self.seed(directory);journal=DecisionJournal(directory,max_bytes=10000)
            journal._prepare(stamp);self.assertEqual(journal.max_bytes,10000)
            self.assertEqual(path.read_bytes(),raw)

    def test_restart_recomputes_capacity_without_changing_files(self):
        with tempfile.TemporaryDirectory() as directory,self.limits(),patch('heating_recovery.shutil.disk_usage',return_value=SimpleNamespace(free=500000,total=1000000)):
            path,raw,stamp=self.seed(directory);first=DecisionJournal(directory);first._prepare(stamp)
            restored=DecisionJournal(directory);restored._prepare(stamp)
            self.assertEqual(first.max_bytes,restored.max_bytes);self.assertEqual(path.read_bytes(),raw)

    def test_low_real_disk_space_stops_new_evidence_and_retains_history(self):
        with tempfile.TemporaryDirectory() as directory,self.limits(),patch('heating_recovery.shutil.disk_usage',return_value=SimpleNamespace(free=1000,total=1000000)):
            path,raw,stamp=self.seed(directory);journal=DecisionJournal(directory)
            result=journal.append(dict(action='off'),stamp=stamp)
            self.assertTrue(result.get('storage_degraded'));self.assertIn('reserve',result['storage_error'])
            self.assertEqual(path.read_bytes(),raw);self.assertEqual(journal.max_bytes,10000)

    def test_hard_cap_and_unknown_free_space_never_become_unlimited(self):
        with tempfile.TemporaryDirectory() as directory,self.limits(),patch('heating_recovery.shutil.disk_usage',side_effect=OSError('unavailable')):
            path,raw,stamp=self.seed(directory);journal=DecisionJournal(directory);journal._prepare(stamp)
            self.assertEqual(journal.max_bytes,10000);self.assertEqual(path.read_bytes(),raw)
        with tempfile.TemporaryDirectory() as directory,self.limits(),patch('heating_recovery.shutil.disk_usage',return_value=SimpleNamespace(free=500000,total=1000000)):
            path,raw,stamp=self.seed(directory);path.write_bytes(os.urandom(100001))
            journal=DecisionJournal(directory);result=journal.append(dict(action='off'),stamp=stamp)
            self.assertTrue(result.get('storage_degraded'));self.assertLessEqual(journal.max_bytes,100000)
            self.assertTrue(path.exists())


if __name__=='__main__':unittest.main()
