import unittest
from datetime import datetime
from zoneinfo import ZoneInfo
from heating_model import plan, DEFAULT_MODEL

class Forecast24Tests(unittest.TestCase):
    def settings(self):
        return dict(start="23:00",end="07:00",strategy="Тільки в дозволені години",
                    lower=21.5,upper=22.5,target=22,night_price=None,day_price=None)
    def run_plan(self,now,settings,weather=()):
        return plan(now,21,25,5,0,DEFAULT_MODEL,list(weather),settings)
    def test_short_schedule_does_not_truncate(self):
        now=datetime(2026,10,12,17,tzinfo=ZoneInfo("Europe/Kiev"))
        s=self.settings(); end=now.timestamp()+7*3600
        s['outage_schedule']=dict(valid=True,known_until=end,outages=[],slots=[],updated='test')
        p=self.run_plan(now,s)
        self.assertEqual(p['horizon_hours'],24)
        self.assertEqual(len(p['curve']),48)
        self.assertEqual(p['curve'][-1]['ts'],now.timestamp()+86400)
        self.assertFalse(p['schedule_complete'])
        self.assertTrue(any(not row['schedule_known'] for row in p['curve']))
        self.assertTrue(all(seg['conditional']==(seg['end']>end) for seg in p['heat_segments']))
    def test_no_schedule_or_weather_is_explicitly_uncertain(self):
        now=datetime(2026,10,12,23,tzinfo=ZoneInfo("Europe/Kiev"))
        p=self.run_plan(now,self.settings())
        self.assertEqual(p['horizon_hours'],24)
        self.assertIsNone(p['schedule_known_until'])
        self.assertIsNone(p['weather_known_until'])
        self.assertTrue(all(seg['conditional'] for seg in p['heat_segments']))
    def test_known_outage_remains_blocked(self):
        now=datetime(2026,10,12,23,tzinfo=ZoneInfo("Europe/Kiev")); start=now.timestamp()
        s=self.settings();s['outage_schedule']=dict(valid=True,known_until=start+86400,
            outages=[dict(start=start,end=start+3600)],slots=[],updated='test')
        p=self.run_plan(now,s,[dict(ts=start+i*3600,temperature=5) for i in range(24)])
        self.assertTrue(p['schedule_complete']); self.assertTrue(p['weather_complete'])
        self.assertFalse(p['heat'])
        self.assertTrue(all(seg['start']>=start+3600 for seg in p['heat_segments']))
        self.assertEqual(p['conditional_heat_minutes'],0)


class Advisory48Tests(unittest.TestCase):
    def fixture(self):
        from heating_model import forecast_step
        now=datetime(2026,11,26,0,tzinfo=ZoneInfo("Europe/Kyiv"));start=now.timestamp()
        settings=dict(start="23:00",end="07:00",strategy="Тільки в дозволені години",
                      lower=20.,upper=40.,target=22.)
        weather=[dict(ts=start+i*3600,temperature=5.,condition="cloudy") for i in range(48)]
        room,floor=21.,23.;r,f=room,floor;rows=[]
        for j in range(48):
            fine=[];segments=[]
            for i in range(6):
                a=start+(j*6+i)*300;b=a+300
                kw=3. if j<2 else 0.
                r,f=forecast_step(r,f,5.,kw,0.,DEFAULT_MODEL,1/12,
                                  datetime.fromtimestamp(a,now.tzinfo),weather,settings)
                fine.append(dict(ts=b,temperature=r,**{"return":f}))
                if kw:segments.append(dict(start=a,end=b,power_kw=kw,kwh=kw/12))
            rows.append(dict(ts=b,temperature=r,**{"return":f},_fine=fine,heat_segments=segments))
        return now,room,floor,weather,settings,dict(curve=rows,_calculation_curve=rows)

    def test_advisory_48_does_not_mutate_plan_and_marks_unknown(self):
        from heating_model import advisory_outlook_48
        from copy import deepcopy
        now,r,f,weather,settings,forecast=self.fixture();start=now.timestamp()
        before=deepcopy(forecast)
        result=advisory_outlook_48(now,r,f,5.,0.,DEFAULT_MODEL,weather,settings,forecast)
        self.assertEqual(result["status"],"conditional")
        self.assertEqual(result["curve"][-1]["ts"],start+172800)
        self.assertEqual(result["calculated_until"],start+86400)
        self.assertEqual(result["assumed_tail_kwh"],3.)
        self.assertEqual(result["unknown_supply_tail_kwh"],3.)
        self.assertTrue(result["weather_complete"])
        self.assertFalse(result["schedule_complete"])
        self.assertEqual(forecast,before)
        self.assertTrue(all(p["basis"]=="calculated_plan" for p in result["curve"][:48]))
        self.assertTrue(all(p["basis"]=="conditional_repeat" for p in result["curve"][48:]))

    def test_advisory_respects_published_off_and_internal_weather_gap(self):
        from heating_model import advisory_outlook_48
        now,r,f,weather,settings,forecast=self.fixture();start=now.timestamp()
        settings["outage_schedule"]=dict(valid=True,mode="scheduled",
            outages=[dict(start=start+86400,end=start+90000)],
            slots=[dict(start=start,end=start+86400,off=False),
                   dict(start=start+86400,end=start+90000,off=True),
                   dict(start=start+90000,end=start+172800,off=False)])
        result=advisory_outlook_48(now,r,f,5.,0.,DEFAULT_MODEL,
            [row for i,row in enumerate(weather) if i!=30],settings,forecast)
        self.assertEqual(result["assumed_tail_kwh"],0.)
        self.assertEqual(result["omitted_tail_kwh"],3.)
        self.assertEqual(result["weather_contiguous_until"],start+30*3600)
        self.assertFalse(result["weather_complete"])
        self.assertFalse(result["curve"][-1]["weather_known"])
        self.assertEqual(advisory_outlook_48(now,r,f,5.,0.,DEFAULT_MODEL,[],settings,forecast)["status"],"unavailable")


    def test_advisory_ignores_legacy_airflow_inputs_during_scheduled_outage(self):
        from heating_model import advisory_outlook_48
        now,r,f,weather,settings,forecast=self.fixture()
        start=now.timestamp()
        settings["outage_schedule"]=dict(valid=True,mode="scheduled",outages=[
            dict(start=start+85800,end=start+90000)],
            slots=[dict(start=start,end=start+86400,off=False),
                   dict(start=start+86400,end=start+90000,off=True)])
        baseline=advisory_outlook_48(now,r,f,5.,0.,DEFAULT_MODEL,weather,settings,forecast)
        legacy=dict(settings,thermal_model_version=DEFAULT_MODEL["version"],
            ventilation_model=dict(validated=True,model_version=DEFAULT_MODEL["version"],
                base_room_loss=.015,recuperator=.006,bath=.004),
            ventilation_expected=dict(recuperator=1.,bath=.2),
            ventilation_grid_only=dict(recuperator=dict(entity="switch.recup",when_powered=1.)))
        self.assertEqual(advisory_outlook_48(now,r,f,5.,0.,DEFAULT_MODEL,weather,legacy,forecast),baseline)

    def test_advisory_coarse_step_against_five_minute_reference(self):
        from heating_model import advisory_outlook_48
        now,r,f,weather,settings,forecast=self.fixture()
        coarse=advisory_outlook_48(now,r,f,5.,0.,DEFAULT_MODEL,weather,settings,forecast)
        fine=advisory_outlook_48(now,r,f,5.,0.,DEFAULT_MODEL,weather,settings,forecast,step_seconds=300)
        self.assertEqual(coarse["assumed_tail_kwh"],fine["assumed_tail_kwh"])
        self.assertEqual(coarse["curve"][:48],fine["curve"][:48])
        self.assertLess(max(abs(a["temperature"]-b["temperature"]) for a,b in zip(coarse["curve"],fine["curve"])),.1)
