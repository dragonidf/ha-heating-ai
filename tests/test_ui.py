import json
from pathlib import Path
import tempfile
import unittest

from heating_ui import install_panel, project_fireplace, verify_installation


class PanelTests(unittest.TestCase):
    def test_release_manifest_detects_partial_replacement(self):
        import hashlib
        from heating_ui_assets import VERSION
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory)
            for index in range(9):
                (root/f'heating_{index}.py').write_text('pass\n')
            modules={p.name:hashlib.sha256(p.read_bytes()).hexdigest() for p in root.glob('*.py')}
            (root/'release.json').write_text(json.dumps(dict(version=VERSION,module_hashes=modules)))
            self.assertEqual(verify_installation(root),modules)
            (root/'heating_0.py').write_text('NEW_VERSION=True\n')
            with self.assertRaises(ValueError):verify_installation(root)
    def test_fireplace_projection_follows_existing_two_hour_decay(self):
        import math
        segments=project_fireplace(1000,2*math.e,True)
        self.assertAlmostEqual(segments[0]['end'],8200)
        self.assertTrue(segments[0]['estimated'])
        for delta,active in ((8,False),(None,True),(2,True),(float('nan'),True)):
            self.assertEqual(project_fireplace(1000,delta,active),[])
    def test_install_is_idempotent_and_does_not_export_secrets(self):
        with tempfile.TemporaryDirectory() as directory:
            cfg = {'ui_www_dir': directory, 'room': 'sensor.my_room', 'token': 'PRIVATE',
                   'data_dir': '/private/data', 'allow_actuation': True}
            result = install_panel(cfg)
            self.assertEqual(result['entities']['room'], 'sensor.my_room')
            self.assertNotIn('token', result['entities'])
            files = list(Path(directory).iterdir())
            self.assertTrue(any(f.suffix == '.js' for f in files))
            times = {f.name: f.stat().st_mtime_ns for f in files}
            self.assertEqual(install_panel(cfg), result)
            self.assertEqual(times, {f.name: f.stat().st_mtime_ns for f in files})
            for file in files:
                self.assertNotIn('PRIVATE', file.read_text())
                self.assertNotIn('/private/data', file.read_text())

    def test_disabled_panel_has_no_filesystem_effect(self):
        self.assertIsNone(install_panel({'ui_enabled': False, 'ui_www_dir': '/not/a/real/directory'}))

    def test_intense_speed_entity_has_a_default_and_accepts_local_mapping(self):
        with tempfile.TemporaryDirectory() as directory:
            cfg={'ui_www_dir':directory}
            self.assertEqual(install_panel(cfg)['entities']['fan_intense_speed'],
                             'input_number.heating_fan_intense_speed')
            cfg['fan_intense_speed']='input_number.other_fireplace_high'
            self.assertEqual(install_panel(cfg)['entities']['fan_intense_speed'],
                             'input_number.other_fireplace_high')

    def test_rejects_external_panel_url(self):
        with tempfile.TemporaryDirectory() as directory:
            with self.assertRaises(ValueError):
                install_panel({'ui_www_dir': directory, 'ui_url': '//example.org'})

    def test_installer_does_not_create_missing_mount(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory)/'missing_mount'/'www'/'heating-ai'
            with self.assertRaises(FileNotFoundError):
                install_panel({'ui_www_dir': str(path)})
            self.assertFalse(path.exists())


if __name__ == '__main__':
    unittest.main()
