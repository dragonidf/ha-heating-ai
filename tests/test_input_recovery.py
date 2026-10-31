"""Retry recovered measurements without bypassing command or physical guards."""
import unittest
from unittest.mock import patch
import test_fallback_recovery as recovery_fixture
import test_adapter_modulation as adapter_fixture
from heating_fallback import aligned_power


class InputRecoveryTests(unittest.TestCase):
    app=recovery_fixture.FallbackRecoveryTests.app

    def fallback(self):
        app=self.app(23)
        app.memory['temporary_fallback']=dict(reason='Недоступні дані або команда термостата',retry_at=app.next_compute)
        return app

    def test_recovered_probe_retries_before_the_five_minute_deadline(self):
        app=self.fallback();deadline=app.next_compute
        app.track_control_inputs(['return'])
        self.assertEqual(app.next_compute,deadline)
        app.track_control_inputs([])
        self.assertEqual(app.next_compute,0)
        self.assertIn('temporary_fallback',app.memory)  # Normal full calculation still owns recovery.
        self.assertFalse(app.calls)

    def test_partial_recovery_does_not_restart_the_planner(self):
        app=self.fallback();deadline=app.next_compute
        app.track_control_inputs(['power','return']);app.track_control_inputs(['return'])
        self.assertEqual(app.next_compute,deadline)

    def test_healthy_ticks_keep_the_normal_compute_interval(self):
        app=self.app();deadline=app.next_compute
        app.track_control_inputs(['power']);app.track_control_inputs([])
        self.assertEqual(app.next_compute,deadline)
        app=self.fallback();deadline=app.next_compute;app.track_control_inputs([]);app.track_control_inputs([])
        self.assertEqual(app.next_compute,deadline)

    def test_command_failure_and_active_control_incident_keep_retry_protection(self):
        for reason,incident in [('Термостат не підтвердив режим або уставку',False),
                                ('Запит нагріву прийнято, але потужність не підтверджена',False),
                                ('Недоступні дані або команда термостата',True)]:
            with self.subTest(reason=reason,incident=incident):
                app=self.fallback();deadline=app.next_compute
                app.memory['temporary_fallback']['reason']=reason
                app.memory['control_incident']=dict(active=incident)
                app.track_control_inputs(['return']);app.track_control_inputs([])
                self.assertEqual(app.next_compute,deadline)

    def test_manual_pause_disabled_control_and_grid_loss_are_preserved(self):
        for guard in ('armed','mode','permission','grid'):
            with self.subTest(guard=guard):
                app=self.fallback();deadline=app.next_compute
                if guard=='armed':app.values['armed']='off'
                if guard=='mode':app.values['mode']='Спостереження'
                if guard=='permission':app.cfg['allow_actuation']=False
                if guard=='grid':app.grid_state=lambda:'off'
                app.track_control_inputs(['return']);app.track_control_inputs([])
                self.assertEqual(app.next_compute,deadline)
                self.assertFalse(app.calls)

    def test_input_fallback_explains_the_actual_missing_probe(self):
        app=self.app(23);app.track_control_inputs(['return'])
        with patch('heating_recovery.time.time',return_value=app.at.timestamp()):app.apply_action('fallback',False)
        fallback=app.memory['temporary_fallback']
        self.assertEqual(fallback['cause'],'inputs')
        self.assertEqual(fallback['missing_inputs'],['return'])
        self.assertIn('обратки',fallback['reason'])

    def test_async_63_second_power_reports_stay_unknown(self):
        rows={k:dict(value=0.,at=t,unit='W',kind='active') for k,t in [('a',1000.),('b',937.)]}
        assessment=aligned_power(rows,list(rows),1000.)
        self.assertFalse(assessment['valid'])
        app=self.fallback();deadline=app.next_compute
        app.track_control_inputs(['power'] if not assessment['valid'] else [])
        self.assertEqual(app.next_compute,deadline)
        self.assertFalse(app.calls)

    def test_real_tick_recomputes_when_the_missing_probe_returns(self):
        from datetime import datetime,timedelta
        from zoneinfo import ZoneInfo
        at=datetime(2026,10,31,23,7,tzinfo=ZoneInfo('Europe/Kiev'));captured=[]
        def unavailable(app):
            captured.append(app);app.original_val=app.val
            app.val=lambda key,default=None,attribute=None:None if key=='return' else app.original_val(key,default,attribute)
            app.memory['temporary_fallback']=dict(reason='Недоступні дані або команда термостата',retry_at=app.next_compute)
        actions=adapter_fixture.AdapterModulationTests().fast_tick(at,[],before_tick=unavailable)
        app=captured[0]
        self.assertEqual(actions,['fallback']);self.assertFalse(app.control_inputs_ready)
        app.val=app.original_val;app.now=lambda:at+timedelta(seconds=15);app.publish_system_settings=lambda:None
        class Recalculated(Exception):pass
        with patch('heating_climate.time.time',return_value=(at+timedelta(seconds=15)).timestamp()),\
             patch('heating_climate.stable_plan',side_effect=Recalculated) as planner:
            with self.assertRaises(Recalculated):app.tick_inner()
        self.assertEqual(planner.call_count,1)
        self.assertNotIn('temporary_fallback',app.memory)
        self.assertTrue(app.memory['recovering_from_fallback'])


if __name__=='__main__':unittest.main()
