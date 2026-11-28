"""Check the key boundary conditions for prepared numerical inputs."""
from datetime import datetime, timedelta
import unittest
from zoneinfo import ZoneInfo

from heating_environment import forecast_step, gain_at
from heating_model import DEFAULT_MODEL, ForecastContext, outdoor_at
from heating_stability import replay


class ForecastContextTest(unittest.TestCase):
    def test_weather_order_and_exact_hour_boundary(self):
        now = datetime(2026, 11, 23, 12, tzinfo=ZoneInfo("Europe/Kyiv"))
        stamp = now.timestamp()
        environment = dict(
            astronomy=dict(latitude=50., days={now.date().isoformat(): dict(
                sunrise=stamp-6*3600, noon=stamp, sunset=stamp+6*3600, declination=-17.)}),
            solar=dict(validated=True, coefficient=1.))
        settings = dict(environment=environment)
        previous = dict(ts=stamp-3600, temperature=2., condition="sunny")
        older = dict(ts=stamp-7200, temperature=1., condition="cloudy")
        first = dict(ts=stamp, temperature=3., condition="sunny")
        tied = dict(ts=stamp, temperature=4., condition="cloudy")
        for weather in ([older, previous, first, tied], [previous, first, tied, older]):
            context = ForecastContext(now, weather, -9., DEFAULT_MODEL, settings)
            for delta in (-7200.001, -.001, 0., 3599.999, 3600.):
                when = now+timedelta(seconds=delta)
                _, _, outside, gains = context.at(when)
                self.assertEqual(outside, outdoor_at(weather, when, -9.))
                self.assertEqual(gains, tuple(gain_at(when, weather, settings, upper=upper)
                                              for upper in (False, True)))
        context = ForecastContext(now, [previous], -9., DEFAULT_MODEL, settings)
        self.assertEqual(context.at(now)[-1], (0., 0.))

    def test_fall_back_distinguishes_the_two_identical_clock_times(self):
        tz = ZoneInfo("Europe/Kyiv")
        first = datetime(2026, 10, 25, 3, 30, tzinfo=tz, fold=0)
        second = first.replace(fold=1)
        self.assertEqual(first, second)
        weather = [dict(ts=first.timestamp(), temperature=1.),
                   dict(ts=second.timestamp(), temperature=2.)]
        context = ForecastContext(first, weather, 0., DEFAULT_MODEL, {})
        self.assertEqual(context.at(first)[:3], (first.timestamp(), 0., 1.))
        self.assertEqual(context.at(second)[:3], (second.timestamp(), 60., 2.))

    def test_prepared_and_scalar_forecasts_agree_with_fireplace_and_solar_risk(self):
        now=datetime(2026,11,23,12,tzinfo=ZoneInfo("Europe/Kyiv"))
        weather=[dict(ts=now.timestamp(),temperature=19.,condition="sunny")]
        settings=dict(environment=dict(solar=dict(observed_gain=.4,observed_at=now.timestamp())))
        context=ForecastContext(now,weather,19.,DEFAULT_MODEL,settings)
        _,_,outside,gains=context.at(now)
        for room in (18.,19.,20.):
            for upper,gain in enumerate(gains):
                with self.subTest(room=room,upper=upper):
                    actual=context.integrate(room,22.,outside,3.,DEFAULT_MODEL["fire_gain"]*2.,1/12,gain)
                    expected=forecast_step(room,22.,outside,3.,2.,DEFAULT_MODEL,1/12,now,weather,
                                           settings,upper=bool(upper))
                    self.assertEqual(actual,expected)

    def test_replay_keeps_all_boundaries_of_overlapping_session_requests(self):
        now = datetime(2026, 11, 23, 12, tzinfo=ZoneInfo("Europe/Kyiv"))
        stamp = now.timestamp()
        settings = dict(start="00:00", end="23:59", target=22., lower=19., upper=25.,
                        strategy="Комфорт протягом доби", morning_min_minutes=0)
        # The later start ends early; the active segment still covers later slices.
        segments = [dict(start=stamp+300, end=stamp+420), dict(start=stamp+180, end=stamp+900)]
        result = replay(now, 21., 23., 10., 0., DEFAULT_MODEL, [], settings, segments, False, 3.)
        self.assertIsNotNone(result)
        self.assertEqual([(s["start"]-stamp, s["end"]-stamp) for s in result["heat_segments"]],
                         [(180, 300), (300, 420), (420, 600), (600, 900)])


if __name__ == "__main__":
    unittest.main()
