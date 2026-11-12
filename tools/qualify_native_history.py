"""Read-only qualification of an original private V1 state against its port.

Print counts and parity only. Never print records or publish the source file.
"""
from copy import deepcopy
from datetime import datetime
from hashlib import sha256
import importlib
import json
from pathlib import Path
import sys
from tempfile import TemporaryDirectory
import zipfile
from zoneinfo import ZoneInfo

from build_marketplace import ROOT, build


def qualify(path):
    raw = Path(path).read_bytes()
    state = json.loads(raw)
    with TemporaryDirectory() as directory:
        archive = Path(directory) / "native.zip"
        build(archive)
        with zipfile.ZipFile(archive) as zipped:
            zipped.extractall(directory)
        sys.path[:0] = [directory, str(ROOT / "apps/heating_ai")]
        try:
            original = importlib.import_module("heating_stability")
            native = importlib.import_module("native_engine.heating_stability")
            reference = state["plan_reference"]
            settings = dict(reference["policy"], modulation_profile=reference.get("profile"),
                            environment=reference.get("environment", {}))
            moment = datetime.fromtimestamp(reference["at"], ZoneInfo("Europe/Kiev"))
            args = (moment, reference["room"], reference["floor"], reference["outside"],
                    reference["fire"], reference["model"], reference["weather"], settings,
                    reference["segments"], reference["split"])
            old = original.replay(*deepcopy(args), reserve_checked=True)
            new = native.replay(*deepcopy(args), reserve_checked=True)
            if old is None or new is None or old != new:
                raise AssertionError("Native V1 differs on the original saved plan reference")
            # Every persisted collection remains structurally and numerically intact.
            restored = json.loads(json.dumps(state, allow_nan=False))
            if restored != state or Path(path).read_bytes() != raw:
                raise AssertionError("Original V1 private state changed")
            history = state.get("temperature_history", {})
            report = dict(source_sha256=sha256(raw).hexdigest(), bytes=len(raw),
                model_version=state["model"]["version"], energy_bins=len(state.get("energy_bins", [])),
                cycles=len(state.get("cycle_energy", {})), history_days=len(history),
                actual_temperature_points=sum(len(day.get("actual", [])) for day in history.values()),
                issued_forecasts=sum(bool(day.get("forecast")) for day in history.values()),
                replay_points=len(new.get("curve", [])), replay_identical=True,
                original_bytes_unchanged=True,
                limitation="Saved-reference engine parity; not a complete backtest without the original telemetry and decision archive")
            print(json.dumps(report, ensure_ascii=False))
            return report
        finally:
            del sys.path[:2]
            for key in tuple(sys.modules):
                if key == "native_engine" or key.startswith("native_engine."):
                    del sys.modules[key]


if __name__ == "__main__":
    qualify(sys.argv[1])
