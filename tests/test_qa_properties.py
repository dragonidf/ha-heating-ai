"""Reproducible generated invariants, entirely offline; no physical efficiency claim."""
import itertools
import math
import unittest
from unittest.mock import patch
from datetime import datetime
from zoneinfo import ZoneInfo
from hypothesis import given,settings,strategies as st
from heating_recovery import cycle_energy_allowance,positive_exposure
from heating_model import in_window,plan,DEFAULT_MODEL
import test_fallback_recovery as fallback


def pairwise_rows(levels):
    """Greedily cover every pair; the test checks its coverage explicitly."""
    candidates=list(itertools.product(*levels))
    def pairs(row):return {(i,row[i],j,row[j]) for i in range(len(row)) for j in range(i+1,len(row))}
    remaining=set().union(*(pairs(row) for row in candidates));rows=[]
    while remaining:
        best=max(candidates,key=lambda row:len(pairs(row)&remaining))
        rows.append(best);remaining-=pairs(best);candidates.remove(best)
    return rows


class GeneratedQATests(unittest.TestCase):
    @settings(max_examples=100,derandomize=True,deadline=None)
    @given(initial=st.floats(0,100,allow_nan=False),spent=st.floats(0,100,allow_nan=False),complete=st.booleans())
    def test_known_energy_is_never_erased_by_missing_intervals(self,initial,spent,complete):
        row=dict(start=0,end=28800,initial_plan=dict(kwh=initial),observed_kwh=spent,complete=complete)
        allowance=cycle_energy_allowance(row)
        self.assertEqual(allowance['known_spent_kwh'],spent)
        self.assertEqual(allowance['remaining_kwh'],max(0,initial-spent))
        self.assertEqual(row['initial_plan']['kwh'],initial)

    @settings(max_examples=100,derandomize=True,deadline=None)
    @given(a=st.floats(-4,4,allow_nan=False),b=st.floats(-4,4,allow_nan=False),seconds=st.integers(1,1800))
    def test_comfort_integrals_are_nonnegative_and_additive(self,a,b,seconds):
        duration,area=positive_exposure(a,b,seconds);mid=(a+b)/2
        first=positive_exposure(a,mid,seconds/2);last=positive_exposure(mid,b,seconds/2)
        self.assertGreaterEqual(duration,0);self.assertLessEqual(duration,seconds)
        self.assertGreaterEqual(area,0)
        self.assertAlmostEqual(duration,first[0]+last[0],places=8)
        self.assertAlmostEqual(area,first[1]+last[1],places=8)

    def test_pairwise_dispatch_grid_permission_sensor_and_calendar_guards(self):
        levels=[('on','off','unknown'),(True,False),('on','off'),
            ('Автоматичне керування','Спостереження'),(21.,None,24.),
            ('2026-10-25T23:10:00','2026-11-01T23:10:00','2026-12-31T23:10:00','2027-01-01T23:10:00')]
        rows=pairwise_rows(levels)
        for i in range(len(levels)):
            for j in range(i+1,len(levels)):
                self.assertEqual({(r[i],r[j]) for r in rows},set(itertools.product(levels[i],levels[j])))
        for grid,allowed,armed,mode,room,date in rows:
            with self.subTest(grid=grid,allowed=allowed,armed=armed,mode=mode,room=room,date=date):
                app=fallback.FallbackRecoveryTests().app(hour=23,grid=grid)
                app.at=datetime.fromisoformat(date).replace(tzinfo=ZoneInfo('Europe/Kyiv'))
                app.memory.update(last_command='off',command_at=app.at.timestamp()-1000)
                app.cfg['allow_actuation']=allowed;app.values.update(armed=armed,mode=mode)
                app.source_temperature=lambda:(room,'Вітальня',room,23.)
                with patch('heating_climate.time.time',return_value=app.at.timestamp()):app.apply_action('heat',False)
                if not allowed:self.assertFalse(app.calls)
                elif grid!='on' or armed!='on' or mode!='Автоматичне керування' or room is None or room>=22.5:
                    self.assertNotEqual(app.memory.get('last_command'),'heat')

    def test_weather_extremes_return_storage_and_physical_power_preserve_plan_masks(self):
        now=datetime(2026,11,1,23,tzinfo=ZoneInfo('Europe/Kyiv'))
        levels=[(-25,-15,-5,0,5,15,25),(20,25,30,35,40),(3,6,9)]
        for outside,floor,power in pairwise_rows(levels):
            with self.subTest(outside=outside,floor=floor,power=power):
                settings=dict(target=21.5,lower=21.,upper=22.,start='23:00',end='07:00',
                    strategy='Тільки в дозволені години',morning_min_minutes=30,day_price=4.32,night_price=2.16,
                    outage_schedule=dict(valid=True,mode='normal',outages=[]))
                result=plan(now,21.2,floor,outside,0.,dict(DEFAULT_MODEL,validated=True),[],settings,power)
                self.assertEqual(result['horizon_hours'],24.)
                self.assertTrue(math.isfinite(result['energy']))
                self.assertTrue(all(in_window(datetime.fromtimestamp(s['start'],now.tzinfo),'23:00','07:00')
                    for s in result['heat_segments']))
                self.assertAlmostEqual(sum(s['kwh'] for s in result['heat_segments']),result['energy'],delta=.011)


if __name__=='__main__':unittest.main()
