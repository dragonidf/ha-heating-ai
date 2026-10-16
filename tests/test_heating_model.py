import unittest
from datetime import datetime
from zoneinfo import ZoneInfo
from heating_model import *

class LogicTests(unittest.TestCase):
    def test_midnight_window(self):
        now=datetime(2026,10,7,23,30,tzinfo=ZoneInfo("Europe/Kiev"))
        self.assertTrue(in_window(now,"23:00","07:00"))
        self.assertFalse(in_window(now.replace(hour=12),"23:00","07:00"))
        self.assertFalse(in_window(now,"07:00","07:00"))
    def test_observation_never_actuates(self):
        self.assertEqual(decide("Автоматичне керування",True,False,20,True,"on",True,True,True,True),"observe")
        self.assertEqual(decide("Спостереження",True,True,20,True,"on",True,True,True,True),"observe")
    def test_grid_unknown_blocks(self):
        self.assertEqual(decide("Автоматичне керування",True,True,20,True,"unknown",True,True,True,True),"off")
    def test_critical_sensor_fallback(self):
        self.assertEqual(decide("Автоматичне керування",True,True,None,True,"on",True,True,True,True),"fallback")
    def test_master_off_does_not_fallback(self):
        self.assertEqual(decide("Вимкнено",True,True,None,False,"on",False,False,False,True),"release")
    def test_inertia_after_switch_off(self):
        peak,end=coast(21.8,34,5,0,DEFAULT_MODEL)
        self.assertGreater(peak,21.8)
    def test_mode_hysteresis(self):
        self.assertEqual(nominal_mode(4550,"3 кВт"),"3 кВт")
        self.assertEqual(nominal_mode(4800,"3 кВт"),"6 кВт")
        self.assertEqual(nominal_mode(0,"9 кВт"),"off")
        self.assertEqual(nominal_mode(None,"9 кВт"),"unavailable")
    def test_bad_ai_candidate_rejected(self):
        ok,reason,_=validate_candidate(DEFAULT_MODEL,{"heat_gain":999},[])
        self.assertFalse(ok)
    def test_strict_day_plan_has_no_heat(self):
        now=datetime(2026,10,7,12,tzinfo=ZoneInfo("Europe/Kiev"))
        s={"start":"23:00","end":"07:00","strategy":"Тільки в дозволені години",
           "lower":21.5,"upper":22.5,"target":22,"night_price":None,"day_price":None}
        p=plan(now,21,23,10,0,DEFAULT_MODEL,[],s)
        self.assertFalse(p["heat"])
        for row in p["curve"]:
            for seg in row["heat_segments"]:
                at=datetime.fromtimestamp(seg["start"],now.tzinfo)
                self.assertTrue(at.hour>=23 or at.hour<7)

if __name__=="__main__":
    unittest.main()
