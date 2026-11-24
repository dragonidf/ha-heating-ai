"""Exercise the actual telemetry writer and restart readers with local files."""
import json
from datetime import timezone
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import Mock, patch

from test_adapter_modulation import HeatingClimate
from heating_model import DEFAULT_MODEL
from heating_recovery import DecisionJournal


class TelemetryRetentionTest(unittest.TestCase):
    def setUp(self):
        temporary = TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.directory = Path(temporary.name)
        self.app = HeatingClimate()
        self.app.telemetry = self.directory / "telemetry.jsonl"
        self.app.log = Mock()

    def record(self, stamp):
        return dict(ts=stamp, quality=True, measurement_version=3, room=21., note="Київ")

    def test_long_running_archive_has_ordered_diagnostic_tail_across_restarts(self):
        paths = [self.directory / name for name in
                 ("state.json", "online_learning_v1.json", "command-journal")]
        for path in paths:
            path.write_bytes(b"unchanged")
        self.app.telemetry.write_bytes(b"legacy oversize\n" * 1200)
        for stamp in range(1000):
            self.app.append_telemetry(self.record(stamp), maximum=8192, retain=6144)
            self.assertLessEqual(self.app.telemetry.stat().st_size, 8192)
        restored = HeatingClimate()
        restored.telemetry = self.app.telemetry
        tail = restored.load_telemetry_samples(0)
        self.assertGreater(len(tail), 50)
        self.assertEqual(tail, [self.record(i) for i in range(tail[0]["ts"], 1000)])
        journal = DecisionJournal(self.directory)
        lines = journal._recent_telemetry_lines(restored.telemetry, 980)
        self.assertEqual([json.loads(row)["ts"] for row in lines], list(range(980, 1000)))
        self.assertTrue(all(path.read_bytes() == b"unchanged" for path in paths))

    def test_partial_non_utf8_and_wrong_json_shape_do_not_hide_valid_samples(self):
        good = json.dumps(self.record(10)).encode()
        self.app.telemetry.write_bytes(good + b'\n[]\n{"ts":[]}\n\xff\n{"ts":11')
        self.app.append_telemetry(self.record(12))
        self.assertEqual(self.app.load_telemetry_samples(0), [self.record(10), self.record(12)])
        self.assertEqual(len(list(DecisionJournal(self.directory)._recent_telemetry_lines(
            self.app.telemetry, 0))), 2)

    def test_atomic_replace_failure_preserves_old_archive_and_retry_is_safe(self):
        raw = (json.dumps(self.record(1)) + "\n").encode() * 200
        self.app.telemetry.write_bytes(raw)
        with patch("heating_climate.os.replace", side_effect=OSError("disk failure")):
            with self.assertRaises(OSError):
                self.app.append_telemetry(self.record(2), maximum=8192, retain=6144)
        self.assertEqual(self.app.telemetry.read_bytes(), raw)
        self.assertEqual(list(self.directory.iterdir()), [self.app.telemetry])
        orphan = self.app.telemetry.with_suffix(".retention.tmp")
        orphan.write_text("interrupted previous rotation")
        self.app.append_telemetry(self.record(3), maximum=8192, retain=6144)
        self.assertEqual(self.app.load_telemetry_samples(0)[-1], self.record(3))
        self.assertFalse(orphan.exists())
        self.assertEqual(self.app.telemetry.stat().st_mode & 0o777, 0o600)

    def test_restart_scan_is_bounded_with_an_abnormally_long_corrupt_line(self):
        self.app.telemetry.write_bytes(b"x" * (10 * 1024 * 1024))
        self.app.append_telemetry(self.record(2))
        self.assertEqual(self.app.load_telemetry_samples(0), [self.record(2)])

    def test_diagnostic_reader_can_finish_during_atomic_rotation(self):
        for stamp in range(30):
            self.app.append_telemetry(self.record(stamp))
        reader = DecisionJournal(self.directory)._recent_telemetry_lines(self.app.telemetry, 0)
        self.assertEqual(json.loads(next(reader))["ts"], 0)
        self.app.append_telemetry(self.record(30), maximum=1600, retain=1200)
        self.assertEqual([json.loads(row)["ts"] for row in reader], list(range(1, 30)))
        self.assertEqual(self.app.load_telemetry_samples(0)[-1], self.record(30))

    def test_archive_failure_leaves_in_memory_sampling_available_until_disk_recovers(self):
        app = self.app
        app.cfg = {}
        app.climate_tz = timezone.utc
        app.memory = {}
        app.model = dict(DEFAULT_MODEL)
        app.samples = []
        app.last_sample = 0
        app.measurement_valid_since = 0
        app.val = Mock(return_value=None)
        app.grid_state = Mock(return_value="on")
        app.ventilation_sample = Mock(return_value={})
        app.run_diagnostic = Mock()
        app.save = Mock()

        def sample(stamp):
            with patch("heating_climate.time.time", return_value=stamp):
                app.sample(21., 24., 8., 0., 0., "Вітальня", 21., 22., True)

        with patch.object(app, "append_telemetry", side_effect=OSError("disk full")):
            sample(300)
            sample(600)
        self.assertEqual([row["ts"] for row in app.samples], [300, 600])
        self.assertTrue(app._telemetry_write_failed)
        self.assertEqual(app.log.call_count, 1)
        sample(900)
        self.assertFalse(app._telemetry_write_failed)
        self.assertEqual([row["ts"] for row in app.load_telemetry_samples(0)], [900])
