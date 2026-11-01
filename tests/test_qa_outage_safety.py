import unittest
from heating_outages import planning_schedule


class OutageSafetyTests(unittest.TestCase):
    def test_legacy_forecast_preference_cannot_certify_an_unhealthy_source(self):
        for reason in ('unavailable','stale','wrong_queue','malformed','contradictory'):
            with self.subTest(reason=reason):
                source=dict(mode='unknown',valid=False,emergency=False,source_available=reason!='unavailable',
                    source_fresh=reason not in ('unavailable','stale'),slots=[],outages=[],reason=reason)
                result=planning_schedule(source,'assume_available',published=False)
                self.assertEqual(result['mode'],'unknown');self.assertFalse(result['valid'])

    def test_fresh_positively_confirmed_no_restrictions_remains_normal(self):
        source=dict(mode='normal',valid=True,emergency=False,source_available=True,source_fresh=True,
            slots=[],outages=[],reason='Відключень не очікується')
        self.assertEqual(planning_schedule(source,'assume_available',published=False),source)


if __name__=='__main__':unittest.main()
