import unittest
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo
from heating_model import DEFAULT_MODEL, plan
from heating_stability import stable_plan, expected, replay


class MorningPriorityTests(unittest.TestCase):
    def setUp(self):
        self.at = datetime(2026, 10, 15, 23, tzinfo=ZoneInfo('Europe/Kiev'))
        self.model = dict(DEFAULT_MODEL, floor_air=.044238523742432284,
                          room_loss=.0128, floor_decay=.1728, heat_gain=.72,
                          fire_gain=.025, validated=True)
        self.settings = dict(target=21.5, lower=21, upper=22,
            night_target=21, night_lower=20.5, night_upper=21.5,
            sleep_start='23:00', sleep_end='06:30', start='23:00', end='07:00',
            strategy='Тільки в дозволені години', split_comfort=True,
            morning_min_minutes=30, day_price=4.32, night_price=2.16)

    def run_plan(self, room=20.9, floor=21.3, outside=7, fire=0, **changes):
        return plan(self.at, room, floor, outside, fire, self.model, [],
                    dict(self.settings, **changes), 7.5)

    def test_actual_night_initial_state_reserves_main_heat_for_morning(self):
        p = self.run_plan()
        self.assertTrue(p['morning_runs_minutes'])
        self.assertGreaterEqual(max(p['morning_runs_minutes']), 30)
        self.assertGreater(p['session_kwh']['morning'],
                           p['session_kwh']['evening'] + p['session_kwh']['other'])
        early = [r['temperature'] for r in p['curve']
                 if datetime.fromtimestamp(r['ts'], self.at.tzinfo).hour in (1,2,3,4)]
        self.assertLess(max(early), 21.25)

    def test_mild_night_does_not_heat_immediately_for_daytime_reserve(self):
        p = self.run_plan(room=21.1, floor=23, outside=10)
        self.assertFalse(p['heat'])
        self.assertTrue(p['morning_runs_minutes'])

    def test_small_updates_keep_morning(self):
        p, ref = stable_plan(self.at, 20.9, 21.3, 7, 0, self.model, [], self.settings, 7.5)
        self.assertTrue(p['morning_runs_minutes'])
        for minute in (5, 10, 15, 20):
            now = self.at + timedelta(minutes=minute)
            q, _ = stable_plan(now, expected(ref, now.timestamp(), 'temperature', 'room'),
                expected(ref, now.timestamp(), 'return', 'floor'), 7, 0,
                self.model, [], self.settings, 7.5, ref)
            self.assertTrue(q['morning_runs_minutes'])
            self.assertTrue(q['plan_stable'])

    def test_hot_fireplace_cannot_force_morning_heat(self):
        p = self.run_plan(room=23, floor=34, outside=17, fire=10)
        self.assertFalse(p['heat'])
        self.assertFalse(p['morning_runs_minutes'])

    def test_morning_outage_moves_main_heat_earlier(self):
        a = self.at + timedelta(hours=7)
        p = self.run_plan(outage_schedule=dict(valid=True, known_until=a.timestamp()+86400,
            outages=[dict(start=a.timestamp(),end=a.timestamp()+3600)],slots=[]))
        self.assertTrue(p['morning_runs_minutes'])
        for s in p['heat_segments']:
            self.assertTrue(s['end'] <= a.timestamp() or s['start'] >= a.timestamp()+3600)

    def test_colder_weather_increases_energy(self):
        self.assertGreater(self.run_plan(outside=0)['energy'], self.run_plan(outside=10)['energy'])

    def test_old_empty_morning_plan_is_not_held(self):
        self.assertIsNone(replay(self.at, 21, 23, 7, 0, self.model, [],
                                 self.settings, [], True, 7.5))

    def test_low_and_high_modulation_keep_safe_morning(self):
        for kw in (3, 9):
            settings = dict(self.settings, modulation_profile=dict(bands=[
                dict(return_c=23, power_kw=kw), dict(return_c=27, power_kw=kw)]))
            p = plan(self.at, 21, 23, 7, 0, self.model, [], settings, kw)
            self.assertTrue(p['morning_runs_minutes'], kw)
            self.assertGreaterEqual(max(p['morning_runs_minutes']), 30)
            self.assertLessEqual(p['maximum'], 22)
            self.assertAlmostEqual(sum(s['kwh'] for s in p['heat_segments']), p['energy'], places=1)

    def test_whole_morning_outage_explains_missing_run(self):
        a = self.at + timedelta(hours=6)
        p = self.run_plan(outage_schedule=dict(valid=True, known_until=a.timestamp()+86400,
            outages=[dict(start=a.timestamp(),end=a.timestamp()+7200)],slots=[]))
        self.assertFalse(p['morning_runs_minutes'])
        self.assertIn('безпечний ранковий', p['split_reason'])
