"""Opening settings is read-only; explicit edits are bounded and persisted."""
import unittest
from datetime import datetime
from zoneinfo import ZoneInfo
from unittest.mock import Mock, patch
from copy import deepcopy
from test_adapter_modulation import HeatingClimate
from heating_model import DEFAULT_MODEL
import heating_ui


class SystemSettingsTests(unittest.TestCase):
    def app(self):
        app=HeatingClimate();app.cfg={'allow_actuation':False};app.model=dict(DEFAULT_MODEL)
        app.memory={};app.next_compute=123456;app.save=Mock();app.journal=Mock();app.log=Mock()
        app.now=lambda:datetime(2026,10,21,12,tzinfo=ZoneInfo('Europe/Kiev'))
        app.update_in_progress=lambda:False
        app.published={};app.publish=lambda entity,state,**attrs:app.published.update({entity:dict(state=state,**attrs)})
        return app

    def submit(self,app,values,revision=0):
        app.system_settings_changed('heating_system_settings',dict(request_id='request-1',revision=revision,values=values))
        return app.published['sensor.heating_system_settings']

    def test_snapshot_does_not_modify_config_model_or_memory_or_export_secrets(self):
        app=self.app();app.cfg.update(token='PRIVATE',data_dir='/private/data',outage_max_age=7200)
        before=deepcopy((app.cfg,app.model,app.memory))
        app.publish_system_settings()
        self.assertEqual((app.cfg,app.model,app.memory),before);app.save.assert_not_called()
        text=str(app.published)
        self.assertNotIn('PRIVATE',text);self.assertNotIn('/private/data',text)
        fields=app.published['sensor.heating_system_settings']['fields']
        self.assertTrue(all(f['label'] and f['help'] and f['group'] for f in fields))
        self.assertEqual(next(f['value'] for f in fields if f['key']=='outage_max_age'),7200)

    def test_explicit_config_change_persists_only_requested_override(self):
        app=self.app();status=self.submit(app,{'outage_max_age':7200,'fire_cooldown_seconds':420})
        self.assertTrue(status['last_success']);self.assertEqual(app.cfg['outage_max_age'],7200)
        self.assertEqual(app.memory['system_settings'],dict(outage_max_age=7200,fire_cooldown_seconds=420))
        self.assertEqual(app.model,DEFAULT_MODEL);self.assertEqual(app.next_compute,0)
        app.save.assert_called_once()

    def test_invalid_unknown_nonfinite_or_crossed_thresholds_are_rejected_atomically(self):
        for values in ({'allow_actuation':True},{'data_dir':'/tmp'}, {'outage_max_age':float('nan')},
                       {'outage_max_age':False},{'fire_start_delta':2},{'fire_intense_delta':4},
                       {'fan_night_start':'07:00','fan_night_end':'07:00'},
                       {'outage_max_age':7200,'fire_stop_delta':10}):
            with self.subTest(values=values):
                app=self.app();before=deepcopy((app.cfg,app.model,app.memory))
                status=self.submit(app,values)
                self.assertFalse(status['last_success']);self.assertTrue(status['last_error'])
                self.assertEqual((app.cfg,app.model,app.memory),before);app.save.assert_not_called()

    def test_stale_revision_and_update_in_progress_reject_edits(self):
        for guard in ('revision','update'):
            app=self.app()
            if guard=='revision':app.memory['system_settings_revision']=2
            else:app.update_in_progress=lambda:True
            status=self.submit(app,{'outage_max_age':7200})
            self.assertFalse(status['last_success']);self.assertNotIn('outage_max_age',app.cfg)

    def test_model_edit_uses_existing_validation_and_does_not_apply_config_on_rejection(self):
        app=self.app();before=deepcopy((app.cfg,app.model))
        with patch('heating_climate.validate_candidate',return_value=(False,'Недостатньо даних',None)):
            app.samples=[]
            status=self.submit(app,{'floor_air':.04,'outage_max_age':7200})
        self.assertFalse(status['last_success']);self.assertEqual((app.cfg,app.model),before)
        self.assertNotIn('system_settings',app.memory)

    def test_saved_overrides_restore_without_replacing_local_config(self):
        app=self.app();app.memory['system_settings']={'outage_max_age':7200,'fire_cooldown_seconds':420}
        app.restore_system_settings()
        self.assertEqual(app.cfg['outage_max_age'],7200)
        self.assertFalse(app.cfg['allow_actuation']);self.assertEqual(app.model,DEFAULT_MODEL)
        app.memory['system_settings']={'fire_start_delta':1}
        app.cfg={};app.restore_system_settings()
        self.assertNotIn('fire_start_delta',app.cfg)

    def test_accepted_model_edit_keeps_existing_rollback_and_validation_semantics(self):
        app=self.app();app.samples=[];previous=dict(app.model)
        with patch('heating_climate.validate_candidate',return_value=(True,'Перевірено',.1)) as validate:
            status=self.submit(app,{'floor_air':.04})
        validate.assert_called_once()
        self.assertTrue(status['last_success']);self.assertEqual(app.model['floor_air'],.04)
        self.assertEqual(app.model['version'],previous['version']+1)
        self.assertFalse(app.model['validated']);self.assertEqual(app.memory['previous_model'],previous)

