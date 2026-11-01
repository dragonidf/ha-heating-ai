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
