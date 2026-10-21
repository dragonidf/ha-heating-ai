"""Small deterministic pairwise matrix; fake HA I/O, no full Cartesian audit."""
import math
import unittest
import tempfile
import json
from datetime import datetime,timedelta
from pathlib import Path
from unittest.mock import patch
import test_sufficiency_search as sufficiency
from test_adapter_modulation import HeatingClimate
from heating_model import plan,in_window
from heating_outages import blocked

class ValidationMatrixTests(unittest.TestCase):
    def test_plan_energy_finiteness_order_masks_and_profile_limits(self):
        case=sufficiency.SufficiencySearchTests();case.setUp();x=case.x
        for hour,minute,power,room,floor,fire,known in [
                (21,15,9.,21.6,21.92,0.,True),(6,40,3.,21.1,22.,0.,False),
                (5,30,6.,21.4,26.,0.,True),(6,59,9.,22.1,30.,8.,False)]:
            with self.subTest(hour=hour,minute=minute,power=power):
                now=case.now.replace(hour=hour,minute=minute,second=0)
                off=now.replace(day=now.day+1 if hour==21 else now.day,hour=6,minute=0).timestamp()
                outages=[dict(start=off,end=off+5400)]
                settings=dict(case.settings,modulation_profile=None,outage_schedule=dict(
                    valid=known,mode='scheduled' if known else 'unknown',known_until=now.timestamp()+86400,
                    slots=[dict(start=now.timestamp(),end=now.timestamp()+86400,off=False)],outages=outages if known else []))
                p=plan(now,room,floor,12.3,fire,x['model'],[],settings,power)
                segments=p['heat_segments']
                self.assertEqual(p['horizon_hours'],24.)
                for a,b in zip(segments,segments[1:]):self.assertLessEqual(a['end'],b['start'])
                energy=0.
                for seg in segments:
                    self.assertGreater(seg['end'],seg['start'])
                    self.assertFalse(blocked(seg['start'],seg['end'],outages if known else []))
                    # Existing explicit outage exceptions remain separately diagnosed.
                    if not p['outside_minutes']:
                        self.assertTrue(in_window(datetime.fromtimestamp(seg['start'],now.tzinfo),'23:00','07:00'))
                    energy+=seg['power_kw']*(seg['end']-seg['start'])/3600
                self.assertAlmostEqual(energy,p['energy'],delta=.011)
                def finite(value):
                    if isinstance(value,dict):return all(finite(v) for v in value.values())
                    if isinstance(value,list):return all(finite(v) for v in value)
                    return math.isfinite(value) if isinstance(value,(int,float)) else True
                self.assertTrue(finite(p))
                if not known:self.assertTrue(p['morning_reserve']['conditional'])
    def test_weather_event_is_persisted_and_empty_response_never_refreshes_cache_age(self):
        with tempfile.TemporaryDirectory() as directory:
            app=HeatingClimate();app.memory={};app.model=dict(version=1);app.weather=[]
            app.statefile=Path(directory)/'state.json';app.next_compute=999
            at=datetime.fromisoformat('2026-10-21T23:00:00+03:00')
            rows=[dict(datetime=(at+timedelta(hours=i)).isoformat(),temperature=12-i,condition='cloudy') for i in (1,0)]
            with patch('heating_climate.time.time',return_value=at.timestamp()):
                app.weather_event('heating_weather_forecast',dict(forecast=rows))
                app.weather_event('heating_weather_forecast',dict(forecast=[]))
            saved=json.loads(app.statefile.read_text())
            self.assertEqual(saved['weather_at'],at.timestamp());self.assertEqual(len(saved['weather']),2)
            self.assertLess(saved['weather'][0]['ts'],saved['weather'][1]['ts'])
            self.assertEqual(app.next_compute,0)
