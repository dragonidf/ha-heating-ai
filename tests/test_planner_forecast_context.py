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

    def test_prepared_ventilation_preserves_temperature_dependent_risk(self):
        now = datetime(2026, 11, 23, 12, tzinfo=ZoneInfo("Europe/Kyiv"))
        model = DEFAULT_MODEL
        for duty in ({}, dict(recuperator=.59, bath=.1)):
            settings = dict(thermal_model_version=model["version"], ventilation_expected=duty,
                            ventilation_model=dict(validated=True, model_version=model["version"],
                                                   base_room_loss=.012, recuperator=.026, bath=.022))
            context = ForecastContext(now, [], 19., model, settings)
            for room in (18., 19., 20.):
                for upper in (False, True):
                    expected = forecast_step(room, 22., 19., 3., 2., model, 1/12, now, [],
                                             settings, upper=upper)
                    actual = context.integrate(room, 22., 19., 3., model["fire_gain"]*2.,
                                               1/12, 0., upper=upper)
                    self.assertEqual(actual, expected)

    def test_published_ventilation_slots_match_prepared_and_scalar_forecast(self):
        now=datetime(2026,11,23,12,tzinfo=ZoneInfo("Europe/Kyiv"))
        start=now.timestamp()
        model=DEFAULT_MODEL
        settings=dict(thermal_model_version=model["version"],
            ventilation_expected=dict(recuperator=.4,bath=.2),
            ventilation_grid_only=dict(recuperator=dict(entity="switch.recup",when_powered=1.)),
            ventilation_model=dict(validated=True,model_version=model["version"],
                base_room_loss=.015,recuperator=.006,bath=.004),
            outage_schedule=dict(valid=True,mode="scheduled",
                slots=[dict(start=start,end=start+600,off=False),
                       dict(start=start+600,end=start+1200,off=True)],
                outages=[dict(start=start,end=start+1200)]))
        context=ForecastContext(now,[],5.,model,settings)
        results=[]
        for offset in (0,600,1200):
            when=datetime.fromtimestamp(start+offset,now.tzinfo)
            for room,outside in ((22.,5.),(20.,25.)):
                for upper in (False,True):
                    actual=context.integrate(room,23.,outside,0.,0.,1/12,0.,upper=upper,
                        interval=(start+offset,start+offset+300))
                    scalar=forecast_step(room,23.,outside,0.,0.,model,1/12,when,[],settings,upper=upper)
                    self.assertEqual(actual,scalar)
            results.append(context.integrate(22.,23.,5.,0.,0.,1/12,0.,
                interval=(start+offset,start+offset+300))[0])
        self.assertLess(results[0],results[2])
        self.assertLess(results[2],results[1])
        self.assertEqual(len(context.ventilation_by_interval),3)

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
