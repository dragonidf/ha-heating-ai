"""Exercise a real spawn worker, its data-only boundary and error protocol."""
import asyncio
from concurrent.futures import ProcessPoolExecutor
import importlib
import multiprocessing
import os
from pathlib import Path
import shutil
import sys
from tempfile import TemporaryDirectory
import unittest

ROOT = Path(__file__).resolve().parents[1]


class CalculationProcessTest(unittest.TestCase):
    def test_spawn_isolates_cpu_work_and_preserves_job_protocol(self):
        with TemporaryDirectory() as directory:
            package = Path(directory) / "worker_fixture"
            engine = package / "native_engine"
            engine.mkdir(parents=True)
            (package / "__init__.py").write_text("")
            (engine / "__init__.py").write_text("")
            shutil.copy(ROOT / "custom_components/heating_ai_legacy/calculation_worker.py", package)
            (engine / "heating_stability.py").write_text(
                "import os\n"
                "def stable_plan(*inputs, **options):\n"
                "    raise ValueError('invalid numerical input')\n"
                "def recommend_power(*inputs, **options):\n"
                "    checksum = sum(i*i for i in range(1000000))\n"
                "    return dict(pid=os.getpid(), inputs=inputs, options=options, checksum=checksum)\n")
            sys.path.insert(0, directory)
            try:
                worker = importlib.import_module("worker_fixture.calculation_worker")
                async def exercise():
                    with ProcessPoolExecutor(max_workers=1, mp_context=multiprocessing.get_context("spawn")) as pool:
                        loop = asyncio.get_running_loop()
                        future = loop.run_in_executor(pool, worker.calculate, "power_advice", "job-1", [21, 25], {"prior": 3})
                        ticks = 0
                        while not future.done():
                            ticks += 1
                            await asyncio.sleep(.005)
                        result = await future
                        self.assertGreater(ticks, 1)
                        self.assertEqual(result["id"], "job-1")
                        self.assertNotEqual(result["result"]["pid"], os.getpid())
                        self.assertEqual(result["result"]["inputs"], (21, 25))
                        self.assertEqual(result["result"]["options"], {"prior": 3})
                        failed = await loop.run_in_executor(pool, worker.calculate, "plan", "job-2", [], {})
                        self.assertEqual(failed["id"], "job-2")
                        self.assertIn("invalid numerical input", failed["error"])
                        self.assertGreaterEqual(result["seconds"], 0)
                asyncio.run(exercise())
            finally:
                sys.path.remove(directory)
                for name in list(sys.modules):
                    if name == "worker_fixture" or name.startswith("worker_fixture."):
                        del sys.modules[name]


if __name__ == "__main__":
    unittest.main()
