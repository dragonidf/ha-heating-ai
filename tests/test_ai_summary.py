"""AI report changes only republish the summary, independently of planning."""
from copy import deepcopy
from datetime import datetime
from tempfile import TemporaryDirectory
from threading import Event, Thread
from unittest.mock import Mock, patch
import unittest

from test_adapter_modulation import HeatingClimate


class AISummaryTests(unittest.TestCase):
    def setUp(self):
        directory = TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        app = self.app = HeatingClimate()
        keys = ('ai_proposal', 'ai_report', 'rollback', 'restore_once', 'thermostat',
                'target', 'lower', 'upper', 'start', 'end', 'strategy', 'mode',
                'armed', 'power_balance')
        app.args = {key: 'input_text.' + key for key in keys}
        app.args.update(ai_report='input_text.heating_ai_report', data_dir=directory.name,
                        ui_enabled=False)
        self.attributes = dict(friendly_name='Клімат — добовий аналіз',
                               model=dict(version=4), usable_samples=123,
                               last_24h=dict(measured_kwh=17.5, coverage=.98),
                               last_result='Коефіцієнти збережено')
        self.states = {
            'input_text.heating_ai_report': dict(state='Старий звіт', attributes={}),
            'sensor.heating_ai_summary': dict(state='Старий звіт', attributes=self.attributes),
        }
        def get_state(entity, attribute=None):
            row = self.states.get(entity)
            if attribute == 'all':
                return deepcopy(row)
            return row.get('state') if row else None
        app.get_state = get_state
        self.writes = []
        def set_state(entity, **row):
            row.pop('replace', None)
            self.writes.append((entity, deepcopy(row)))
            self.states[entity] = deepcopy(row)
        app.set_state = set_state
        self.listeners = []
        app.listen_state = lambda callback, entity, **kwargs: self.listeners.append((callback, entity))
        app.listen_event = Mock()
        app.run_every = Mock()
        app.datetime = datetime.now
        app.log = Mock()
        with patch('heating_climate.verify_installation'), patch('heating_climate.install_panel', return_value=None):
            app.initialize()
        self.writes.clear()
        app.next_compute = 987654321
        app.memory['plan_reference'] = dict(heat_segments=[dict(start=1, end=2)])

    def report(self, text, old='Старий звіт', event_text=None):
        self.states['input_text.heating_ai_report']['state'] = text
        self.app.ai_report_changed('input_text.heating_ai_report', 'state', old,
                                   text if event_text is None else event_text)

    def test_listener_uses_configured_input_and_not_the_published_sensor(self):
        matches = [(callback.__name__, entity) for callback, entity in self.listeners
                   if callback.__name__ == 'ai_report_changed']
        self.assertEqual(matches, [('ai_report_changed', 'input_text.heating_ai_report')])
        self.assertNotIn('sensor.heating_ai_summary', [entity for _, entity in self.listeners])

    def test_report_publishes_immediately_preserving_attributes_without_replanning(self):
        before = deepcopy(self.app.memory)
        with patch('heating_climate.stable_plan') as thermal_plan:
            self.report('Новий звіт')
        thermal_plan.assert_not_called()
        self.assertEqual(self.writes, [('sensor.heating_ai_summary',
                                      dict(state='Новий звіт', attributes=self.attributes))])
        self.assertEqual(self.app.next_compute, 987654321)
        self.assertEqual(self.app.memory, before)
        self.assertEqual(self.states['input_text.heating_ai_report']['state'], 'Новий звіт')

    def test_delayed_event_uses_latest_report_and_unchanged_text_does_not_republish(self):
        self.report('Найновіший звіт', event_text='Проміжний звіт')
        self.assertEqual(self.writes[-1][1]['state'], 'Найновіший звіт')
        self.writes.clear()
        self.report('Найновіший звіт', old='Найновіший звіт')
        self.assertEqual(self.writes, [])

    def test_full_publish_refreshes_attributes_then_report_reuses_that_snapshot(self):
        self.app.model = dict(version=7)
        self.app.memory['last_result'] = 'Новий результат'
        self.app.summary = Mock(return_value=dict(measured_kwh=18.2))
        self.app.publish_ai_summary(refresh=True)
        attributes = deepcopy(self.writes[-1][1]['attributes'])
        self.assertEqual(attributes['model'], dict(version=7))
        self.assertEqual(attributes['last_24h'], dict(measured_kwh=18.2))
        self.assertEqual(attributes['last_result'], 'Новий результат')
        self.app.summary.reset_mock()
        # An HA cache that has not caught up must not roll back cycle attributes.
        self.states['sensor.heating_ai_summary']['attributes'] = self.attributes
        self.report('Звіт після циклу')
        self.assertEqual(self.writes[-1][1]['attributes'], attributes)
        self.app.summary.assert_not_called()

    def test_report_before_first_cycle_keeps_all_attributes_and_empty_placeholder(self):
        del self.states['sensor.heating_ai_summary']
        self.report('')
        self.assertEqual(self.writes[-1][1]['state'], 'Перший аналіз очікується')
        self.assertTrue({'model', 'usable_samples', 'last_24h', 'last_result'} <=
                        self.writes[-1][1]['attributes'].keys())

    def test_cycle_and_report_publish_are_serialized_and_latest_report_wins(self):
        entered, release, callback_started = Event(), Event(), Event()
        original_publish = self.app.publish
        failures = []
        def publish(entity, state, **attrs):
            if not entered.is_set():
                entered.set()
                if not release.wait(3):
                    raise TimeoutError('test publish was not released')
            original_publish(entity, state, **attrs)
        self.app.publish = publish
        def run(callback):
            try:
                callback()
            except Exception as exc:
                failures.append(exc)
        cycle = Thread(target=lambda: run(lambda: self.app.publish_ai_summary(refresh=True)))
        cycle.start()
        try:
            self.assertTrue(entered.wait(3))
            def change():
                callback_started.set()
                self.report('Звіт під час циклу')
            callback = Thread(target=lambda: run(change))
            callback.start()
            self.assertTrue(callback_started.wait(3))
        finally:
            release.set()
            cycle.join(3)
        callback.join(3)
        self.assertFalse(cycle.is_alive() or callback.is_alive())
        self.assertEqual(failures, [])
        self.assertEqual([row['state'] for _, row in self.writes],
                         ['Старий звіт', 'Звіт під час циклу'])
        self.assertEqual(self.app.next_compute, 987654321)


if __name__ == '__main__':
    unittest.main()
