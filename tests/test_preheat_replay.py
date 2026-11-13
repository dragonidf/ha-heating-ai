"""Replay V1's certified blackout preheat without permitting arbitrary day heat."""
from datetime import datetime, timedelta
import unittest

from heating_model import DEFAULT_MODEL
from heating_stability import approved_extensions, reference, replay


class PreheatReplayTests(unittest.TestCase):
    def setUp(self):
        self.now=datetime.fromisoformat('2026-11-13T19:55:00+02:00')
        a=(self.now+timedelta(minutes=5)).timestamp()
        self.segment=dict(start=a,end=a+300,power_kw=3.,kwh=.25)
        outage=dict(start=a+7200,end=a+10800)
        self.settings=dict(start='23:00',end='07:00',strategy='Тільки в дозволені години',
            target=22.,lower=21.,upper=23.,night_price=1.32,day_price=2.64,
            split_comfort=False,morning_min_minutes=0,
            outage_schedule=dict(mode='scheduled',valid=True,outages=[outage],
                known_until=a+2*86400,slots=[dict(start=self.now.timestamp(),end=outage['start'],off=False),
                    dict(**outage,off=True),dict(start=outage['end'],end=a+2*86400,off=False)]))
        self.proposal=dict(approved_window_extensions=[[a,a+300]],extension_outages=[outage])

    def run_replay(self,settings=None,proposal=None,segments=None):
        settings=settings or self.settings
        return replay(self.now,21.5,22.,10.,0.,dict(DEFAULT_MODEL),[],settings,
            segments or [self.segment],False,3.,reserve_checked=True,
            window_extensions=approved_extensions(settings,proposal or {}))

    def test_uncertified_day_heat_is_rejected(self):
        self.assertIsNone(self.run_replay())

    def test_exact_v1_preheat_exception_survives_replay_and_reference(self):
        result=self.run_replay(proposal=self.proposal)
        self.assertIsNotNone(result)
        self.assertAlmostEqual(result['outside_minutes'],5.)
        ref=reference(self.now,21.5,22.,10.,0.,dict(DEFAULT_MODEL),[],self.settings,result)
        self.assertEqual(approved_extensions(self.settings,ref),self.proposal['approved_window_extensions'])
        self.assertIsNotNone(self.run_replay(proposal=ref))

    def test_changed_schedule_cannot_reuse_old_exception(self):
        data=dict(self.settings['outage_schedule'],outages=[dict(start=self.segment['start'],end=self.segment['end'])])
        self.assertIsNone(self.run_replay(settings=dict(self.settings,outage_schedule=data),proposal=self.proposal))

    def test_no_extra_day_heat_outside_certified_interval(self):
        extra=dict(self.segment,start=self.segment['start']+600,end=self.segment['end']+600)
        self.assertIsNone(self.run_replay(proposal=self.proposal,segments=[extra]))

    def test_live_outage_still_blocks_an_explicit_extension(self):
        data=dict(self.settings['outage_schedule'],outages=[dict(start=self.segment['start'],end=self.segment['end'])])
        proposal=dict(self.proposal,extension_outages=data['outages'])
        self.assertIsNone(self.run_replay(settings=dict(self.settings,outage_schedule=data),proposal=proposal))


if __name__=='__main__':
    unittest.main()
