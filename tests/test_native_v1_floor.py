"""Native V1 floor heat inventory reuses the original observe_floor formula."""
from importlib import import_module
from pathlib import Path
import sys
from tempfile import TemporaryDirectory
from types import ModuleType
import unittest
import zipfile

from tools.build_marketplace import build


class FloorTests(unittest.TestCase):
    def test_original_observer_and_atomic_private_checkpoint(self):
        with TemporaryDirectory() as tmp:
            directory=Path(tmp)
            zip_path=directory/"review.zip"
            build(zip_path)
            with zipfile.ZipFile(zip_path) as pkg:
                pkg.extractall(directory/"code")
            module=ModuleType("isolated_v1_floor")
            module.__path__=[str(directory/"code")]
            sys.modules["isolated_v1_floor"]=module
            try:
                impl=import_module("isolated_v1_floor.native_floor")
                original=import_module(
                    "isolated_v1_floor.native_engine.heating_environment"
                )
                model=dict(
                    floor_air=.03,room_loss=.01,floor_decay=.24,
                    heat_gain=1.03,fire_gain=.025
                )
                a=impl.advance(None,100000.,21.5,27.,3000.,True,model,100.)
                self.assertGreaterEqual(a["estimate"],21.5)
                native_next=impl.advance(a,100300.,21.6,28.,3000.,True,model,100.3)
                old_next,old_estimate=original.observe_floor(
                    a,100300.,21.6,28.,3.0,"on",model,energy_kwh=100.3
                )
                self.assertEqual(native_next["estimate"],old_estimate)
                self.assertEqual(native_next["source"],old_next["source"])
                checkpoint=directory/"private"
                checkpoint.mkdir()
                impl.remember(checkpoint,native_next)
                self.assertEqual(impl.load(checkpoint),native_next)
                with self.assertRaises(ValueError):
                    impl.advance(native_next,100200.,21.5,28.,0.,True,model,100.3)
                with self.assertRaises(ValueError):
                    impl.advance(native_next,100600.,21.5,28.,None,True,model,None)
                with self.assertRaises(ValueError):
                    impl.remember(checkpoint,a)
                path=checkpoint/"v1_floor_observer.json"
                path.write_bytes(path.read_bytes()+b"tamper")
                with self.assertRaises(ValueError):
                    impl.load(checkpoint)
            finally:
                for k in tuple(sys.modules):
                    if k=="isolated_v1_floor" or k.startswith("isolated_v1_floor."):
                        sys.modules.pop(k,None)


if __name__=="__main__":
    unittest.main()
