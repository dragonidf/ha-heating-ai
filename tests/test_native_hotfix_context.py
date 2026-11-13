"""Production method regressions without an HA installation dependency."""
import ast
from pathlib import Path
from types import SimpleNamespace
import threading
import time
import unittest

ROOT = Path(__file__).resolve().parents[1]


def method(filename, class_name, name):
    tree = ast.parse((ROOT / 'custom_components/heating_ai_legacy' / filename).read_text())
    cls = next(n for n in tree.body if isinstance(n, ast.ClassDef) and n.name == class_name)
    node = next(n for n in cls.body if isinstance(n, ast.FunctionDef) and n.name == name)
    node.decorator_list = []
    namespace = dict(threading=threading, time=time)
    exec(compile(ast.Module(body=[node], type_ignores=[]), filename, 'exec'), namespace)
    return namespace[name]


class NativeContextRegressions(unittest.TestCase):
    def test_notification_echo_does_not_query_an_absent_entity(self):
        queried = []
        def get(entity):
            if entity is None:
                raise AssertionError('HA StateMachine.get(None) crashes in lower()')
            queried.append(entity)
        app = SimpleNamespace(hass=SimpleNamespace(states=SimpleNamespace(get=get)))
        echo = method('production.py', 'NativeController', 'async_equipment_command_echo')
        result = echo(app, 'persistent_notification', 'dismiss', {'notification_id':'heating_control_fault'})
        self.assertFalse(result['matched'])
        self.assertEqual(queried, [])

    def test_recorder_coroutine_can_read_on_ha_loop(self):
        app = SimpleNamespace(_loop_thread=threading.get_ident())
        run = method('native_bridge.py', 'Hass', '_on_loop')
        self.assertEqual(run(app, lambda value: value + 1, 41), 42)


if __name__ == '__main__':
    unittest.main()
