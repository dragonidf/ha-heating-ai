"""Deterministic, anonymised learning/adapter cases; no real HA commands."""
import json
import math
import tempfile
import unittest
from copy import deepcopy
from datetime import datetime,timedelta
from pathlib import Path
from unittest.mock import Mock,patch
from zoneinfo import ZoneInfo
from test_adapter_modulation import HeatingClimate
from heating_model import DEFAULT_MODEL,BOUNDS,step,validate_candidate,learn,transitions,decide

LIVE_MODEL=dict(DEFAULT_MODEL,floor_air=.044238523742432284,room_loss=.012800000000000004,
                floor_decay=.17279999999999998,heat_gain=.72,version=4,validated=True)

def samples_for(model=None,count=180):
    model=model or dict(LIVE_MODEL,floor_air=LIVE_MODEL['floor_air']*1.1)
    stamp=datetime(2026,10,20,tzinfo=ZoneInfo('Europe/Kiev')).timestamp()
    room,floor,energy=21.5,23.,100.
    rows=[]
    for i in range(count):
        power=9. if i%24<12 else 0.
        rows.append(dict(ts=stamp+i*300,room=room,**{'return':floor},outside=8.,power=power,
            quality=True,measurement_version=3,environment_version=1,grid='on',power_source='balance',
            power_estimated=False,fire=0.,fire_valid=True,daylight_weight=0.,external_gain=0.,
            interval_power=rows[-1]['power'] if rows else None,energy=energy))
        room,floor=step(room,floor,8.,power,0.,model,1/12);energy+=power/12
    return rows

class LearningLimitTests(unittest.TestCase):
    def setUp(self):self.samples=samples_for()
    def test_delta_boundary_and_true_excess_on_all_coefficient_scales(self):
        for key in BOUNDS:
            for fraction,expected in [(-.20,True),(.20,True),(.19999,True),(.20001,False)]:
                with self.subTest(key=key,fraction=fraction):
                    candidate=dict(LIVE_MODEL,**{key:LIVE_MODEL[key]*(1+fraction)})
                    with patch('heating_model.error',side_effect=[.2,.1]):
                        ok,reason,_=validate_candidate(LIVE_MODEL,candidate,self.samples)
                    self.assertEqual(ok,expected,reason)
    def test_no_absolute_point005_escape_for_small_coefficient(self):
        model=dict(LIVE_MODEL,room_loss=.002)
        with patch('heating_model.error',side_effect=[.2,.1]):
            self.assertFalse(validate_candidate(model,dict(model,room_loss=.0025),self.samples)[0])
    def test_learning_clamp_uses_the_same_cumulative_daily_limits(self):
        baseline=dict(LIVE_MODEL)
        for multiplier in (.8,1.2):
            model={**baseline,**{k:v*multiplier for k,v in baseline.items() if k in BOUNDS}}
            with patch('heating_model.solve',side_effect=[[.2,.15],[3.,1.5]]):
                candidate=learn(model,self.samples,baseline)
            for key in BOUNDS:
                self.assertLessEqual(candidate[key],baseline[key]*1.2+1e-15)
                self.assertGreaterEqual(candidate[key],baseline[key]*.8-1e-15)
    def test_zero_is_frozen_and_physical_nonfinite_and_boolean_values_are_rejected(self):
        zero=dict(LIVE_MODEL,fire_gain=0.)
        for value in (.000001,float('nan'),float('inf'),None,'bad','0.0',False):
            with self.subTest(value=value):
                with patch('heating_model.error',side_effect=[.2,.1]):
                    self.assertFalse(validate_candidate(zero,dict(zero,fire_gain=value),self.samples)[0])
        for key,(lo,hi) in BOUNDS.items():
            for base,value,valid in [(lo,lo,True),(hi,hi,True),(lo,math.nextafter(lo,-math.inf),False),
                                     (hi,math.nextafter(hi,math.inf),False)]:
                with self.subTest(key=key,value=value):
                    model=dict(LIVE_MODEL,**{key:base})
                    with patch('heating_model.error',side_effect=[.2,.1]):
                        self.assertEqual(validate_candidate(model,dict(model,**{key:value}),self.samples)[0],valid)
    def test_real_holdout_must_improve_and_training_does_not_consume_holdout(self):
        old=dict(LIVE_MODEL);candidate=dict(old,floor_air=old['floor_air']*1.1)
        self.assertTrue(validate_candidate(old,candidate,self.samples)[0])
        self.assertFalse(validate_candidate(old,dict(old,floor_air=old['floor_air']*.9),self.samples)[0])
        changed=deepcopy(self.samples)
        for row in changed[int((len(changed)-1)*.75)+1:]:row['room']+=.2
        self.assertEqual(learn(old,self.samples),learn(old,changed))
        with patch('heating_model.error',return_value=0.):
            self.assertFalse(validate_candidate(old,candidate,self.samples)[0])

class LearningFixture:
    def app(self):
        app=HeatingClimate();app.model=dict(LIVE_MODEL);app.memory={};app.samples=samples_for()
        app.climate_tz=ZoneInfo('Europe/Kiev');app.at=datetime(2026,10,21,10,tzinfo=app.climate_tz)
        app.now=lambda:app.at;app.save=Mock();app.journal=Mock();app.decision_event=Mock()
        app.next_compute=123;app.cfg={};app.publish=Mock()
        return app

class LearningAdapterTests(LearningFixture,unittest.TestCase):
    def test_accepted_candidate_keeps_validated_control_during_confirmation(self):
        app=self.app();before=dict(app.model)
        self.assertTrue(app.consider(dict(before,floor_air=before['floor_air']*1.1),'test',apply=True))
        self.assertEqual(app.model,before)
        self.assertEqual(decide('Автоматичне керування',True,True,21.,True,'on',True,True,
                                app.model['validated'],True),'heat')
        self.assertIn('pending_model',app.memory)
    def test_two_applications_share_persisted_daily_base_and_next_local_day_resets(self):
        app=self.app();base=app.model['floor_air']
        app.samples=samples_for(dict(app.model,floor_air=base*1.4))
        # Model acceptance and later prospective confirmation are separate.
        self.assertTrue(app.consider(dict(app.model,floor_air=base*1.2),'test',apply=True))
        self.assertEqual(app.memory['learning_day']['baseline']['floor_air'],base)
        app.memory['pending_model']['errors']=[dict(old=.2,new=.1)]*12
        app.confirm_learning_candidate()
        self.assertEqual(app.model['floor_air'],base*1.2)
        app.memory=json.loads(json.dumps(app.memory))
        self.assertFalse(app.consider(dict(app.model,floor_air=base*1.44),'test',apply=True))
        self.assertEqual(app.memory['learning_day']['baseline']['floor_air'],base)
        app.at+=timedelta(days=1)
        self.assertTrue(app.consider(dict(app.model,floor_air=base*1.44),'test',apply=True))
        self.assertEqual(app.memory['learning_day']['baseline']['floor_air'],base*1.2)
    def test_rejected_candidate_retains_model_ready_and_records_real_attempt(self):
        app=self.app();before=dict(app.model)
        self.assertFalse(app.consider(dict(before,floor_air=before['floor_air']*1.3),'test',apply=True))
        self.assertEqual(app.model,before)
        attempt=app.memory['learning_attempt']
        self.assertEqual(attempt['status'],'rejected');self.assertIsNone(attempt['new_holdout_error'])
        self.assertEqual(attempt['candidate']['floor_air'],before['floor_air']*1.3)
        self.assertGreater(attempt['holdout_count'],0)

class LearningLifecycleTests(LearningFixture,unittest.TestCase):
    def pending(self):
        app=self.app();app.consider(dict(app.model,floor_air=app.model['floor_air']*1.1),'test',apply=True)
        return app
    def test_prospective_confirmation_retains_ready_and_invalidates_old_long_validation(self):
        app=self.pending();before=dict(app.model);attempt=app.memory['pending_model']['attempt_id']
        app.memory['pending_model']['errors']=[dict(old=.2,new=.1)]*12
        app.memory['reserve_errors']=[dict(hours=24,error=.1,validated=True)]*12
        app.confirm_learning_candidate()
        self.assertEqual(app.model['version'],before['version']+1);self.assertTrue(app.model['validated'])
        self.assertEqual(app.memory['previous_model'],before)
        self.assertEqual(app.memory['learning_success']['attempt_id'],attempt)
        self.assertEqual(app.memory['reserve_errors'],[])
    def test_no_gain_in_prospective_hour_errors_keeps_old_model(self):
        app=self.pending();before=dict(app.model)
        app.memory['pending_model']['errors']=[dict(old=.1,new=.2)]*12
        app.confirm_learning_candidate()
        self.assertEqual(app.model,before);self.assertNotIn('pending_model',app.memory)
        self.assertNotIn('learning_success',app.memory)
    def test_repeat_event_preserves_attempt_and_progress(self):
        app=self.pending();pending=deepcopy(app.memory['pending_model'])
        app.memory['pending_model']['errors']=[dict(old=.2,new=.1)]
        app.consider(pending['candidate'],'test',apply=True)
        self.assertEqual(app.memory['pending_model']['attempt_id'],pending['attempt_id'])
        self.assertEqual(len(app.memory['pending_model']['errors']),1)
    def test_rollback_never_rebases_day_and_cancels_pending(self):
        app=self.pending();base=deepcopy(app.memory['learning_day'])
        previous=dict(app.model,floor_air=app.model['floor_air']*.9)
        app.memory['previous_model']=previous
        app.rollback(None,None,None,None)
        self.assertEqual(app.model,previous);self.assertEqual(app.memory['learning_day'],base)
        self.assertNotIn('pending_model',app.memory)
    def test_promotion_storage_failure_retains_last_verified_model(self):
        app=self.pending();old=dict(app.model)
        app.memory['pending_model']['errors']=[dict(old=.2,new=.1)]*12
        app.save=Mock(side_effect=OSError('disk full'))
        app.confirm_learning_candidate()
        self.assertEqual(app.model,old)
        self.assertEqual(app.memory['learning_attempt']['status'],'storage_error')
        self.assertIn('disk full',app.memory['learning_storage_error'])
    def test_day_boundary_uses_local_date_over_both_dst_changes(self):
        for month,day in [(3,28),(10,24)]:
            app=self.app();app.at=datetime(2026,month,day,23,59,tzinfo=app.climate_tz)
            before=app.learning_baseline();app.model=dict(app.model,floor_air=before['floor_air']*1.1)
            app.at=datetime.fromtimestamp(app.at.timestamp()+120,app.climate_tz)
            self.assertEqual(app.learning_baseline()['floor_air'],before['floor_air']*1.1)
    def test_rejected_thermal_training_does_not_reset_modulation(self):
        from heating_model import learn_modulation
        app=self.app();profile=learn_modulation(app.samples);app.memory['modulation_profile']=profile
        app.consider(dict(app.model,heat_gain=2.),'test',apply=True)
        self.assertEqual(app.memory['modulation_profile'],profile)

class DataQualityTests(unittest.TestCase):
    def test_estimates_old_probe_fire_external_gain_unknown_and_retrograde_do_not_train(self):
        rows=samples_for(count=4)
        self.assertEqual(len(transitions(rows)),3)
        for field,value in [('power_estimated',True),('power_source','fallback_phases'),('grid','unknown'),
                            ('fire',3.),('fire_valid',False),('external_gain',.2),('quality',False)]:
            bad=[dict(r,**{field:value}) for r in rows]
            with self.subTest(field=field):self.assertEqual(transitions(bad),[])
        old=[dict(r,ts=r['ts']-20*86400) for r in rows]
        self.assertEqual(transitions(old),[])
        retrograde=rows[:2]+rows[:2]+rows[2:]
        pairs=transitions(retrograde)
        self.assertTrue(all(a['ts']>=left['ts'] for (left,_,_),(a,_,_) in zip(pairs,pairs[1:])))
        self.assertEqual(len({(a['ts'],b['ts']) for a,b,_ in pairs}),len(pairs))

class StateRecoveryTests(unittest.TestCase):
    def test_corrupt_state_is_visible_read_only_and_preserved(self):
        for contents in ('{broken', '[]', json.dumps(dict(model=dict(LIVE_MODEL,heat_gain='bad')))):
            with self.subTest(contents=contents),tempfile.TemporaryDirectory() as directory:
                path=Path(directory)/'state.json';path.write_text(contents)
                app=HeatingClimate();app.args={k:k for k in ('ai_proposal','ai_report','rollback','restore_once',
                    'thermostat','target','lower','upper','start','end','strategy','mode','armed','power_balance')}
                app.args.update(data_dir=directory,allow_actuation=True)
                app.datetime=lambda:datetime(2026,10,21,12,tzinfo=ZoneInfo('Europe/Kiev'))
                app.get_state=lambda *a,**k:None
                app.apply_energy_recovery=Mock()
                for name in ('run_every','listen_event','listen_state','log','publish_system_settings'):
                    setattr(app,name,Mock())
                with patch('heating_climate.install_panel',return_value=None),patch('heating_climate.verify_installation'):
                    app.initialize()
                self.assertFalse(app.cfg['allow_actuation'])
                self.assertTrue(app.memory.get('state_load_error'))
                app.apply_energy_recovery.assert_not_called()
                app.save();self.assertEqual(path.read_text(),contents)

class ProspectiveMeasurementTests(LearningFixture,unittest.TestCase):
    def test_only_matched_fresh_primary_measurements_credit_pending_candidate(self):
        for quality,grid,estimated,energy,credited in [(True,'on',False,100.,1),(False,'on',False,100.,0),
                (True,'unknown',False,100.,0),(True,'on',True,100.,0),(True,'on',False,None,0),
                (True,'on',False,102.,0)]:
            with self.subTest(quality=quality,grid=grid,estimated=estimated,energy=energy),tempfile.TemporaryDirectory() as directory:
                app=self.app();app.consider(dict(app.model,floor_air=app.model['floor_air']*1.1),'test',apply=True)
                attempt=app.memory['pending_model']['attempt_id'];stamp=app.at.timestamp()
                app.memory['predictions']=[dict(ts=stamp,temperature=21.5,candidate_temperature=21.55,
                    learning_attempt_id=attempt,version=app.model['version'],start_energy=100.,expected_energy=0.)]
                app.last_sample=0;app.measurement_valid_since=stamp;app.telemetry=Path(directory)/'telemetry.jsonl'
                app.val=lambda *a,**k:energy;app.grid_state=lambda:grid
                app.power_source='balance';app.fallback_interval=estimated
                with patch('heating_climate.time.time',return_value=stamp):
                    app.sample(21.6,23.,10.,0.,0.,'Вітальня',21.6,22.,quality)
                self.assertEqual(len(app.memory['pending_model']['errors']),credited)
                self.assertTrue(app.model['validated'])
    def test_daily_rewrite_failure_keeps_original_telemetry_and_verified_model(self):
        app=self.app();old=dict(app.model)
        with tempfile.TemporaryDirectory() as directory:
            app.telemetry=Path(directory)/'telemetry.jsonl';app.telemetry.write_text('original history\n')
            app.log=Mock()
            with patch('heating_climate.os.replace',side_effect=OSError('disk full')):
                app.daily_learn()
            self.assertEqual(app.telemetry.read_text(),'original history\n')
            self.assertEqual(app.model,old);self.assertIn('disk full',app.memory['learning_storage_error'])
