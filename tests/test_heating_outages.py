import unittest
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo
from heating_model import plan, DEFAULT_MODEL, in_window
from heating_outages import schedule, blocked


class OutageTests(unittest.TestCase):
    def setUp(self):
        self.now=datetime(2026,10,9,4,30,tzinfo=ZoneInfo("Europe/Kiev"))
        self.settings={"start":"06:00","end":"07:00","strategy":"Тільки в дозволені години",
                       "target":22,"lower":21.5,"upper":22.5,"night_price":.5,"day_price":1}

    def data(self, now=None, ranges=((12,14),), tomorrow=None):
        now=now or self.now
        today=["on"]*48
        for a,b in ranges:
            today[a:b]=["off"]*(b-a)
        attrs={"updated":now.isoformat(),"queue":"3.1","region":"kiivska-oblast","today_48half":today}
        if tomorrow is not None:
            attrs["tomorrow_48half"]=tomorrow
        return {"state":"off","attributes":attrs}

    def result(self, start="06:00", room=21.8, floor=24, record=None):
        s=dict(self.settings,start=start,outage_schedule=schedule(record or self.data(),self.now))
        return plan(self.now,room,floor,10,0,DEFAULT_MODEL,[],s)

    def test_6_to_7_moved_to_5_to_6(self):
        p=self.result()
        segments=p["heat_segments"]
        self.assertEqual(len(segments),12)
        self.assertEqual(datetime.fromtimestamp(segments[0]["start"],self.now.tzinfo).hour,5)
        self.assertEqual(datetime.fromtimestamp(segments[-1]["end"],self.now.tzinfo).hour,6)
        self.assertEqual(p["outside_minutes"],60)
        self.assertTrue(p["outage_shifted"])

    def test_prefers_existing_night_window(self):
        p=self.result(start="23:00")
        self.assertEqual(p["outside_minutes"],0)
        self.assertTrue(p["heat_segments"])
        self.assertTrue(all(in_window(datetime.fromtimestamp(s["start"],self.now.tzinfo),"23:00","07:00") for s in p["heat_segments"]))

    def test_no_heat_overlap_even_when_blocks_are_unaligned(self):
        self.now=self.now.replace(minute=47,second=13)
        p=self.result()
        outages=schedule(self.data(),self.now)["outages"]
        self.assertTrue(all(not blocked(s["start"],s["end"],outages) for s in p["heat_segments"]))

    def test_no_unneeded_preheat_for_warm_house(self):
        s=dict(self.settings,lower=15,outage_schedule=schedule(self.data(),self.now))
        p=plan(self.now,24,30,22,0,DEFAULT_MODEL,[],s)
        self.assertEqual(p["outside_minutes"],0)

    def test_energy_matches_actual_heating_segments(self):
        p=self.result()
        self.assertAlmostEqual(p["energy"],sum(s["end"]-s["start"] for s in p["heat_segments"])*9/3600,places=2)

    def test_stale_schedule_reverts_to_normal_window(self):
        record=self.data(self.now-timedelta(hours=4))
        p=self.result(record=record)
        self.assertEqual(p["outside_minutes"],0)
        self.assertFalse(p["outage_shifted"])
        self.assertFalse(schedule(record,self.now)["valid"])

    def test_arrays_keep_date_at_midnight(self):
        updated=self.now.replace(day=8,hour=23,minute=50)
        now=updated+timedelta(minutes=20)
        tomorrow=["on"]*48;tomorrow[1:3]=["off"]*2
        data=schedule(self.data(updated,ranges=(),tomorrow=tomorrow),now)
        start=datetime.fromtimestamp(data["outages"][0]["start"],now.tzinfo)
        self.assertEqual((start.day,start.hour,start.minute),(9,0,30))

    def test_unknown_and_wrong_queue_are_not_clear(self):
        self.assertFalse(schedule({"state":"unavailable"},self.now)["valid"])
        record=self.data();record["attributes"]["queue"]="3.2"
        self.assertFalse(schedule(record,self.now)["valid"])
        record=self.data();record["attributes"]["today_48half"][10]="maybe"
        self.assertFalse(schedule(record,self.now)["valid"])

    def test_dst_48slots_rejected_as_ambiguous(self):
        now=self.now.replace(day=25,hour=0)
        self.assertFalse(schedule(self.data(now),now)["valid"])

    def test_schedule_change_replans(self):
        before=self.result()
        after=self.result(record=self.data(ranges=()))
        self.assertTrue(before["outage_shifted"])
        self.assertFalse(after["outage_shifted"])
        self.assertEqual(after["outside_minutes"],0)

    def test_exception_bounded_by_removed_heat(self):
        p=self.result()
        self.assertLessEqual(p["outside_minutes"],60)
        self.assertTrue(p["comfort_shortfall"])

if __name__=="__main__":
    unittest.main()
