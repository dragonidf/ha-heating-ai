"""Published blackout schedule versus confirmed live power: regression tests."""
import unittest
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo
from unittest.mock import patch

from heating_outages import operational_schedule, blocked, schedule_covered
from heating_stability import policy


class OperationalOutageTests(unittest.TestCase):
    def setUp(self):
        self.tz=ZoneInfo('Europe/Kyiv')
        self.midnight=datetime(2026,11,17,0,0,tzinfo=self.tz).timestamp()
        self.off1=dict(start=self.midnight,end=self.midnight+3*3600)
        self.off2=dict(start=self.midnight+9*3600,end=self.midnight+13*3600)
        self.source=dict(valid=True,mode='scheduled',updated='2026-11-16T22:09:59+00:00',
                         outages=[self.off1,self.off2],
                         slots=[dict(start=self.midnight-3600,end=self.midnight+86400,off=False)])
    def view(self,minute,grid_since=None):
        return operational_schedule(self.source,self.midnight+minute*60,grid_since)

    def test_forced_stop_starts_exactly_10_minutes_before_publication(self):
        just_before=operational_schedule(self.source,self.midnight-601,None)
        at_limit=operational_schedule(self.source,self.midnight-600,None)
        self.assertFalse(blocked(self.midnight-601,self.midnight-600,just_before['outages']))
        self.assertTrue(blocked(self.midnight-600,self.midnight-599,at_limit['outages']))
        self.assertEqual(at_limit['outages'][0]['start'],self.midnight-600)

    def test_optimizer_rejects_short_heat_when_blackout_is_six_minutes_away(self):
        from heating_model import _plan, DEFAULT_MODEL
        now=datetime(2026,11,17,20,44,tzinfo=self.tz)
        start=now.replace(hour=21,minute=0).timestamp()
        blackout=dict(start=start,end=start+10800)
        published=dict(valid=True,mode='scheduled',outages=[blackout],slots=[])
        operational=operational_schedule(published,now.timestamp(),None)
        settings=dict(start='00:00',end='23:59',strategy='Комфорт протягом доби',
                      target=22,lower=21.5,upper=22.5,night_price=.5,day_price=1)
        result=_plan(now,20.0,21.0,-5.0,0.,DEFAULT_MODEL,[],settings,
                     outages=operational['outages'],hours_override=.5,beam_width=12)
        self.assertIsNotNone(result)
        self.assertFalse(result['heat_segments'])

    def test_refresh_timestamp_only_does_not_change_schedule_identity(self):
        from heating_outages import signature
        before=signature(self.source)
        changed=dict(self.source,updated='2026-11-17T00:00:00+00:00')
        self.assertEqual(signature(changed),before)
        changed['slots']=[dict(start=self.midnight-3600,end=self.midnight+86400,off=True)]
        self.assertNotEqual(signature(changed),before)

    def test_unchanged_grid_must_wait_until_15_minutes_into_outage(self):
        since=self.midnight-3600
        self.assertTrue(blocked(self.midnight+899,self.midnight+899.1,self.view(899/60,since)['outages']))
        eligible=self.view(15,since)
        self.assertFalse(blocked(self.midnight+900,self.midnight+901,eligible['outages']))
        self.assertEqual(eligible['runtime_supply_override']['start'],self.midnight+900)
        self.assertFalse(schedule_covered(eligible,self.midnight+900,self.midnight+1200))
        self.assertEqual(self.source['outages'],[self.off1,self.off2])

    def test_late_real_restore_restarts_independent_15_minute_clock(self):
        restored=self.midnight+70*60
        for offset in (0,10,14.9):
            case=self.view(70+offset,restored)
            self.assertIsNone(case['runtime_supply_override'])
            self.assertTrue(blocked(self.midnight+(70+offset)*60,self.midnight+(70+offset)*60+1,case['outages']))
        later=self.view(85,restored)
        self.assertFalse(blocked(self.midnight+85*60,self.midnight+85*60+1,later['outages']))
        self.assertEqual(later['runtime_supply_override']['start'],self.midnight+85*60)

    def test_interrupted_or_unknown_grid_revokes_exception_immediately(self):
        confirmed=self.view(85,self.midnight+70*60)
        self.assertIsNotNone(confirmed['runtime_supply_override'])
        no_grid=self.view(86,None)
        self.assertTrue(blocked(self.midnight+86*60,self.midnight+86*60+1,no_grid['outages']))
        short_restore=self.view(96,self.midnight+86*60)
        self.assertTrue(blocked(self.midnight+96*60,self.midnight+96*60+1,short_restore['outages']))

    def test_future_blackout_keeps_the_10_minute_pre_stop(self):
        current=self.view(20,self.midnight-3600)
        self.assertFalse(blocked(self.midnight+8*3600+49*60,
                                 self.midnight+8*3600+50*60,current['outages']))
        self.assertTrue(blocked(self.midnight+8*3600+50*60,
                                self.midnight+8*3600+51*60,current['outages']))
        self.assertTrue(blocked(self.midnight+9*3600,
                                self.midnight+9*3600+60,current['outages']))

    def test_no_runtime_override_for_invalid_or_unknown_schedule(self):
        for original in (dict(self.source,valid=False),dict(self.source,mode='unknown')):
            result=operational_schedule(original,self.midnight+1200,self.midnight-3600)
            self.assertIsNone(result['runtime_supply_override'])
            self.assertEqual(result['outages'],original['outages'])

    def test_runtime_override_in_stability_cache_policy(self):
        waiting=self.view(10,self.midnight-3600)
        allowed=self.view(20,self.midnight-3600)
        self.assertNotEqual(policy(dict(outage_schedule=waiting)),
                            policy(dict(outage_schedule=allowed)))

    def test_grid_confirmation_is_not_persisted_through_restart_or_gaps(self):
        from heating_recovery import HeatingRecovery
        app=HeatingRecovery()
        self.assertEqual(app.track_confirmed_grid('on',self.midnight),self.midnight)
        self.assertEqual(app.track_confirmed_grid('on',self.midnight+30),self.midnight)
        self.assertEqual(app.track_confirmed_grid('on',self.midnight+100),self.midnight+100)
        self.assertIsNone(app.track_confirmed_grid('unknown',self.midnight+101))
        self.assertEqual(app.track_confirmed_grid('on',self.midnight+102),self.midnight+102)
        restarted=HeatingRecovery()
        self.assertEqual(restarted.track_confirmed_grid('on',self.midnight+1000),self.midnight+1000)


if __name__=='__main__':
    unittest.main()
