import importlib.util
from pathlib import Path
import tempfile
import unittest

spec=importlib.util.spec_from_file_location('migrate_hacs',Path(__file__).parents[1]/'tools/migrate_hacs.py')
m=importlib.util.module_from_spec(spec)
spec.loader.exec_module(m)


class MigrationTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        root=Path(self.temp.name)
        self.app,self.hacs,self.data,self.backup=(root/name for name in ('apps','hacs','data','backup'))
        for directory in (self.app,self.hacs,self.data):
            directory.mkdir()
        for name in m.EXPECTED:
            (self.hacs/name).write_text('new '+name)
        for name in m.LEGACY:
            (self.app/name).write_text('old '+name)
        (self.app/'local.yaml').write_text('private settings')
        (self.data/'state.json').write_text('learned state')
        (self.app/'unrelated.py').write_text('other app')

    def plan(self):
        return m.plan(self.app,self.hacs,self.data,self.backup)

    def test_plan_is_read_only_and_apply_requires_stop(self):
        plan=self.plan()
        self.assertFalse(self.backup.exists())
        with self.assertRaises(ValueError):m.apply(plan)
        self.assertTrue((self.app/'heating_climate.py').exists())

    def test_migration_and_rollback_preserve_settings_data_and_other_apps(self):
        manifest=m.apply(self.plan(),appdaemon_stopped=True)
        self.assertTrue((self.app/'heating_ai_hacs').is_symlink())
        self.assertFalse((self.app/'heating_climate.py').exists())
        self.assertEqual((self.app/'local.yaml').read_text(),'private settings')
        self.assertEqual((self.data/'state.json').read_text(),'learned state')
        self.assertEqual((self.app/'unrelated.py').read_text(),'other app')
        m.rollback(manifest,appdaemon_stopped=True)
        self.assertEqual((self.app/'heating_climate.py').read_text(),'old heating_climate.py')
        self.assertFalse((self.app/'heating_ai_hacs').exists())

    def test_duplicate_module_is_rejected(self):
        (self.app/'nested').mkdir()
        (self.app/'nested/heating_model.py').write_text('duplicate')
        with self.assertRaises(ValueError):self.plan()

    def test_data_inside_hacs_is_rejected(self):
        with self.assertRaises(ValueError):m.plan(self.app,self.hacs,self.hacs,self.backup)

    def test_partial_hacs_download_is_rejected(self):
        (self.hacs/'heating_ui_assets.py').unlink()
        with self.assertRaises(ValueError):self.plan()

    def test_changed_backup_is_not_used_for_rollback(self):
        manifest=m.apply(self.plan(),appdaemon_stopped=True)
        (manifest.parent/'heating_model.py').write_text('corrupt')
        with self.assertRaises(ValueError):m.rollback(manifest,appdaemon_stopped=True)
        self.assertTrue((self.app/'heating_ai_hacs').is_symlink())
