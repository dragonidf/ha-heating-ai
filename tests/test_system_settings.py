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
    def test_ventilation_supply_binding_survives_same_relay_only(self):
        app=self.app()
        app.cfg.update(recuperator_entity='switch.recuperator',
            ventilation={'recuperator':{'entity':'switch.recuperator',
                         'power_entity':'binary_sensor.recuperator_supply'}})
        app.runtime_ready=True
        app.listen_state=Mock(side_effect=lambda _,entity:entity)
        app.cancel_listen_state=Mock()
        app.configure_ventilation()
        self.assertEqual(app.cfg['ventilation']['recuperator']['power_entity'],
                         'binary_sensor.recuperator_supply')
        self.assertEqual(set(app.equipment_listeners),
                         {'switch.recuperator','binary_sensor.recuperator_supply'})
        app.cfg['recuperator_entity']='switch.different'
        app.configure_ventilation()
        self.assertEqual(app.cfg['ventilation']['recuperator'],{'entity':'switch.different'})
        self.assertEqual(set(app.equipment_listeners),{'switch.different'})
        self.assertEqual(app.cancel_listen_state.call_count,2)
        app.cfg['recuperator_entity']=''
        app.configure_ventilation()
        self.assertFalse(app.equipment_listeners)

    def test_grid_only_ventilation_binding_and_interval_provenance(self):
        from heating_learning_windows import ventilation_interval_duty
        app=self.app()
        app.cfg.update(recuperator_entity='switch.recuperator',bath_entity='switch.bath',
            ventilation={'recuperator':{'entity':'switch.recuperator','supply_kind':'grid_only'},
                         'bath':{'entity':'switch.bath'}})
        app.configure_ventilation()
        states={'switch.recuperator':'unavailable','switch.bath':'unavailable'}
        app.get_state=lambda entity:states.get(entity)
        grid=['off']
        app.grid_state=lambda:grid[0]
        for stamp in range(0,300,15):
            app.ventilation_tick(stamp)
        observed=app.ventilation_sample(300)
        self.assertEqual(ventilation_interval_duty(observed['recuperator']),0.)
        self.assertIsNone(ventilation_interval_duty(observed['bath']))
        self.assertEqual(app.ventilation_states['recuperator']['supply_provenance'],'verified_grid_topology')
        self.assertEqual(app.ventilation_states['bath']['supply'],'unknown')
        grid[0]='on';states['switch.recuperator']='on'
        for stamp in range(315,600,15):
            app.ventilation_tick(stamp)
        self.assertIsNone(ventilation_interval_duty(app.ventilation_sample(600)['recuperator']))
        for stamp in range(615,900,15):
            app.ventilation_tick(stamp)
        self.assertEqual(ventilation_interval_duty(app.ventilation_sample(900)['recuperator']),1.)
        app.ventilation_tick(885)
        self.assertEqual(app.ventilation_at,900)
        app.cfg['recuperator_entity']='switch.replacement'
        app.configure_ventilation()
        self.assertNotIn('supply_kind',app.cfg['ventilation']['recuperator'])

    def test_persisted_grid_only_selection_is_exact_and_never_transferred(self):
        app=self.app()
        app.cfg.update(recuperator_entity='switch.recuperator',bath_entity='switch.bath',
                       grid_only_ventilation_entities=['switch.recuperator'])
        app.configure_ventilation()
        app.get_state=lambda entity:'unavailable'
        app.grid_state=lambda:'off'
        app.ventilation_tick(0.)
        self.assertEqual(app.ventilation_states['recuperator']['effective_duty'],0.)
        self.assertIsNone(app.ventilation_states['bath']['effective_duty'])
        app.cfg['recuperator_entity']='switch.replacement'
        app.configure_ventilation()
        app.ventilation_tick(15.)
        self.assertEqual(app.ventilation_states['recuperator']['supply_provenance'],'unknown')
        self.assertIsNone(app.ventilation_states['recuperator']['effective_duty'])

    def test_grid_binding_never_overrides_explicit_supply_sensor(self):
        app=self.app()
        app.cfg['ventilation']={'recuperator':{'entity':'switch.recuperator','supply_kind':'grid_only',
                                              'power_entity':'binary_sensor.supply'}}
        app.grid_state=lambda:'off'
        app.get_state=lambda entity:'on'
        app.ventilation_tick(0.)
        self.assertEqual(app.ventilation_states['recuperator']['supply'],'on')
        self.assertEqual(app.ventilation_states['recuperator']['effective_duty'],1.)
        self.assertEqual(app.ventilation_states['recuperator']['supply_provenance'],'supply_sensor')

    def test_saved_boiler_profile_is_published_immediately_and_old_sessions_invalidated(self):
        app=self.app();app.cfg['boiler_power_mode']='fixed_6'
        app.memory.update(equipment_version=9,planned_heat_segments=[{'start':1,'end':2}],power_recommendation={'mode':'fixed_6'})
        app.publish_equipment();self.assertEqual(app.published['sensor.heating_power_model']['state'],'fixed_6')
        status=self.submit(app,{'boiler_power_mode':'fixed_9'})
        self.assertTrue(status['last_success'])
        self.assertEqual(app.published['sensor.heating_power_model']['state'],'fixed_9')
        self.assertEqual(app.published['sensor.heating_power_model']['equipment_version'],10)
        self.assertEqual(app.memory['planned_heat_segments'],[])
        self.assertNotIn('power_recommendation',app.memory)

    def test_saved_sensor_age_limit_invalidates_the_cached_reporting_policy(self):
        app=self.app();app.room_limits_at=12345.
        self.assertTrue(self.submit(app,{'room_max_age':7200})['last_success'])
        self.assertEqual(app.room_limits_at,0.)

    def test_failed_persistence_does_not_apply_unsaved_runtime_configuration(self):
        app=self.app();before=deepcopy((app.cfg,app.model,app.memory));app.save.side_effect=OSError('disk full')
        status=self.submit(app,{'outage_max_age':7200,'boiler_power_mode':'fixed_6'})
        self.assertFalse(status['last_success'])
        self.assertEqual((app.cfg,app.model,app.memory),before)
        self.assertEqual(app.next_compute,123456)

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

    def test_ventilation_summary_cache_invalidates_on_ring_and_live_identity(self):
        from unittest.mock import patch
        app=self.app()
        app.samples=[]
        app.ventilation_states={"recuperator":{"entity":"switch.recup","relay":"off",
            "supply_provenance":"verified_grid_topology","effective_duty":0.}}
        with patch("heating_climate.ventilation_expectation",return_value=({"recuperator":.2},{})) as mean, \
             patch("heating_climate.ventilation_powered_expectation",return_value={}) as powered:
            first=app.ventilation_forecast_inputs()
            self.assertIs(first,app.ventilation_forecast_inputs())
            self.assertEqual(mean.call_count,1)
            app.ventilation_states["recuperator"]["relay"]="on"
            app.ventilation_states["recuperator"]["effective_duty"]=1.
            app.ventilation_forecast_inputs()
            self.assertEqual(mean.call_count,2)
            app.samples.append({"ts":1})
            app.ventilation_forecast_inputs()
            self.assertEqual(mean.call_count,3)
            app.samples=list(app.samples)
            app.ventilation_forecast_inputs()
            self.assertEqual(mean.call_count,4)
            self.assertEqual(powered.call_count,4)
            self.assertEqual(app.next_compute,123456)

    def test_snapshot_does_not_modify_config_model_or_memory_or_export_secrets(self):
        app=self.app();app.cfg.update(token='PRIVATE',data_dir='/private/data',outage_max_age=7200)
        before=deepcopy((app.cfg,app.model,app.memory))
        app.publish_system_settings()
        self.assertEqual((app.cfg,app.model,app.memory),before);app.save.assert_not_called()
        text=str(app.published)
        self.assertNotIn('PRIVATE',text);self.assertNotIn('/private/data',text)
        fields=heating_ui.system_fields(app.cfg,app.model)
        self.assertTrue(all(f['label'] and f['help'] and f['group'] for f in fields))
        self.assertEqual(app.published['sensor.heating_system_settings']['values']['outage_max_age'],7200)

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

    def test_acknowledgement_is_persisted_and_republished_after_restart(self):
        app=self.app();status=self.submit(app,{'outage_max_age':7200})
        saved=deepcopy(app.memory)
        self.assertEqual(saved['system_settings_receipt']['last_request_id'],'request-1')
        restarted=self.app();restarted.memory=saved;restarted.restore_system_settings();restarted.publish_system_settings()
        result=restarted.published['sensor.heating_system_settings']
        self.assertTrue(result['last_success']);self.assertEqual(result['last_request_id'],'request-1')
        self.assertEqual(result['persisted_revision'],status['revision'])

    def test_disk_error_is_an_explicit_failed_ack_not_success(self):
        app=self.app();app.save.side_effect=OSError('disk unavailable')
        status=self.submit(app,{'outage_max_age':7200})
        self.assertFalse(status['last_success']);self.assertIn('disk unavailable',status['last_error'])
        self.assertEqual(status['persisted_revision'],0)
        self.assertNotIn('system_settings_receipt',app.memory)

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

