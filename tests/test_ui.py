import json
from pathlib import Path
import tempfile
import unittest

from heating_ui import install_panel


class PanelTests(unittest.TestCase):
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
