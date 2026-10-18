"""Fireplace thresholds, continuous cooldown and restart-safe circulation demand."""
import unittest
from copy import deepcopy
from unittest.mock import patch
from test_adapter_modulation import HeatingClimate


class FireplaceCirculationTests(unittest.TestCase):
    def app(self):
        app=HeatingClimate();app.cfg={'fireplace_heartbeat':[]};app.memory={};app.samples=[]
        app.heartbeat_fresh=lambda *args:True
        app.published={}
        app.publish=lambda entity,state,**attrs:app.published.update({entity:dict(state=state,**attrs)})
        return app

    def cycle(self,app,delta,stamp=10000,slope=0,valid=True):
        app.val=lambda key:None if delta is None else 22+delta
        app.heartbeat_fresh=lambda *args:valid
        app.samples=[dict(ts=stamp-900,fire=delta-slope/4)] if delta is not None else []
        with patch('heating_climate.time.time',return_value=stamp):
            return app.fireplace(22)

    def test_initial_phases_do_not_start_circulation_below_five_degrees(self):
        cases=[(2.4,0,'Неактивний',False),(2,.8,'Неактивний',False),(2,.81,'Розпалювання',False),
               (4.9,1,'Розпалювання',False),(5,0,'Горить',True),
               (10,0,'Інтенсивно горить',True),(8,-1,'Згасає',True),(5,-.8,'Горить',True),
               (3.5,-1,'Згасає',False),(10,-1,'Згасає',True)]
        for delta,slope,phase,active in cases:
            with self.subTest(delta=delta,slope=slope):
                app=self.app()
                measured,requested=self.cycle(app,delta,slope=slope)
                self.assertAlmostEqual(measured,delta)
                self.assertEqual(requested,active)
                self.assertEqual(app.published['sensor.heating_fireplace']['state'],phase)

    def test_below_three_requires_five_continuous_minutes_then_stays_off(self):
        app=self.app();self.cycle(app,5)
        for stamp in range(10015,10315,15):
            self.assertTrue(self.cycle(app,2.9,stamp=stamp,slope=-.2)[1])
        self.assertFalse(self.cycle(app,2.9,stamp=10315,slope=-.2)[1])
        self.assertFalse(self.cycle(app,4.9,stamp=10330,slope=1)[1])
        self.assertTrue(self.cycle(app,5,stamp=10345)[1])

    def test_three_degrees_holds_and_recovery_resets_cooldown(self):
        app=self.app();self.cycle(app,5)
        self.assertTrue(self.cycle(app,2.9,stamp=10015)[1])
        self.assertTrue(self.cycle(app,3,stamp=10030)[1])
        for stamp in range(10045,10345,15):
            self.assertTrue(self.cycle(app,2.9,stamp=stamp)[1])
        self.assertFalse(self.cycle(app,2.9,stamp=10345)[1])

    def test_strong_cooling_stops_below_four_without_waiting(self):
        for delta,slope,active in [(3.9,-2,False),(4,-3,True),(3.9,-1.99,True)]:
            app=self.app();self.cycle(app,5)
            self.assertEqual(self.cycle(app,delta,stamp=10015,slope=slope)[1],active)

    def test_missing_sensor_stops_demand_and_breaks_cooldown_continuity(self):
        app=self.app();self.cycle(app,5);self.cycle(app,2.9,stamp=10015)
        self.assertFalse(self.cycle(app,None,stamp=10030)[1])
        self.assertIsNone(app.published['binary_sensor.heating_fireplace_active']['state'])
        self.assertFalse(self.cycle(app,3.5,stamp=10045)[1])

    def test_a_long_gap_does_not_count_as_continuous_low_temperature(self):
        app=self.app();self.cycle(app,5);self.cycle(app,2.9,stamp=10015)
        self.assertTrue(self.cycle(app,2.9,stamp=10500)[1])
        self.assertEqual(app.memory['fire_circulation']['below_since'],10500)

    def test_legacy_owned_fan_enters_cooldown_after_upgrade(self):
        app=self.app();app.memory.update(fire_phase='Згасає',fan_owned=True)
        self.assertTrue(self.cycle(app,2.4)[1])
        for stamp in range(10015,10300,15):self.cycle(app,2.4,stamp=stamp)
        self.assertFalse(self.cycle(app,2.4,stamp=10300)[1])

    def test_saved_cooldown_survives_a_short_reload(self):
        app=self.app();self.cycle(app,5)
        for stamp in (10015,10030,10045):self.cycle(app,2.4,stamp=stamp)
        restored=self.app();restored.memory=deepcopy(app.memory)
        for stamp in range(10060,10315,15):self.assertTrue(self.cycle(restored,2.4,stamp=stamp)[1])
        self.assertFalse(self.cycle(restored,2.4,stamp=10315)[1])

    def test_decimal_sensor_readings_at_ten_degrees_select_intense_phase(self):
        app=self.app();app.val=lambda key:32.3
        with patch('heating_climate.time.time',return_value=10000):app.fireplace(22.3)
        self.assertEqual(app.published['sensor.heating_fireplace']['state'],'Інтенсивно горить')

    def test_explicit_tuning_changes_thresholds_without_changing_defaults(self):
        app=self.app();app.cfg.update(fire_start_delta=6,fire_intense_delta=12,fire_cooldown_seconds=420)
        self.assertFalse(self.cycle(app,5)[1])
        self.assertTrue(self.cycle(app,6)[1])
        self.cycle(app,10,stamp=10015)
        self.assertEqual(app.published['sensor.heating_fireplace']['state'],'Горить')
        self.cycle(app,12,stamp=10030)
        self.assertEqual(app.published['sensor.heating_fireplace']['state'],'Інтенсивно горить')
        for stamp in range(10045,10465,15):self.assertTrue(self.cycle(app,2.9,stamp=stamp)[1])
        self.assertFalse(self.cycle(app,2.9,stamp=10465)[1])


if __name__=='__main__':unittest.main()
