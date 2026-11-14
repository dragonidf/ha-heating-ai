"""01.11 audit: trustworthy evidence, bounded HA attributes and source provenance."""
import copy
import json
import tempfile
from pathlib import Path
import unittest
from datetime import datetime, timedelta
from unittest.mock import patch
from zoneinfo import ZoneInfo
import test_plan_accountability as accountability
import test_temperature_comparison as comparison
import test_power_provenance as provenance
from heating_fallback import combine_power
from heating_model import DEFAULT_MODEL
import heating_environment as environment
from heating_recovery import HeatingRecovery
from test_adapter_modulation import HeatingClimate


class QATruthfulnessTests(unittest.TestCase):
    def test_unchanged_room_reports_use_authoritative_ha_timestamps_and_values(self):
        app=HeatingClimate();at=datetime(2026,11,1,18,tzinfo=ZoneInfo('Europe/Kyiv'));stamp=at.timestamp()
        app.cfg=dict(room='room',thermostat='thermostat',power_measurement_policy='audited',power_balance_sources=[])
        app.memory=dict(thermostat_bias=1.);app.samples=[];app.runtime_ready=True
        app.get_state=lambda entity,**kw:dict(state='20.0' if entity=='room' else 'heat',last_reported=(at-timedelta(days=1)).isoformat())
        app.val=lambda key,default=None,attribute=None:20. if key=='room' else 22.
        app.raw=lambda *a,**k:'heat'
        app.render_template=lambda *a,**k:dict(room=dict(value='21.0',at=stamp-60,unit='°C'),
            thermostat=dict(value='heat',at=stamp-60,temperature=22.))
        with patch('heating_climate.time.time',return_value=stamp):
            result=app.source_temperature()
        self.assertEqual(result[:2],(21.,'Вітальня'))
        self.assertEqual(app.temperature_quality['primary_age_seconds'],60.)
        self.assertEqual(app.memory['thermostat_bias_at'],stamp)

    def test_telemetry_keeps_the_authoritative_report_time_used_for_control(self):
        at=datetime(2026,11,1,18,tzinfo=ZoneInfo('Europe/Kyiv'));stamp=at.timestamp()
        app=HeatingClimate();app.cfg=dict(room='room',thermostat='thermostat',power_measurement_policy='audited')
        app.memory={};app.model=dict(DEFAULT_MODEL);app.samples=[];app.last_sample=0;app.power_quality='measured'
        app.power_snapshot=dict(room=dict(at=stamp-60),thermostat=dict(at=stamp-60));app.power_snapshot_at=stamp
        app.temperature_quality=dict(primary_reported_at=stamp-120,thermostat_reported_at=stamp-120)
        app.now=lambda:at;app.val=lambda *a,**kw:None;app.raw=lambda *a,**kw:dict(last_reported=(at-timedelta(days=1)).isoformat())
        app.grid_state=lambda:'on';app.ventilation_sample=lambda stamp:{};app.run_diagnostic=lambda *a,**kw:None;app.save=lambda:None
        with tempfile.TemporaryDirectory() as directory:
            app.telemetry=Path(directory)/'telemetry.jsonl'
            with patch('heating_climate.time.time',return_value=stamp):app.sample(21.,24.,8.,0.,0.,'Вітальня',21.,22.,True)
        self.assertEqual(app.samples[0]['room_reported_at'],stamp-120)
        self.assertEqual(app.samples[0]['thermostat_reported_at'],stamp-120)

    def test_confirmed_grid_loss_is_a_defensible_zero_not_a_meter_measurement(self):
        case=provenance.PowerProvenanceTests();app=case.app();app.cfg['power_measurement_policy']='audited'
        app.stale.update(app.cfg['power_balance_sources'])
        app.get_state=lambda entity,**kw:None
        assessment=app.assess_power_sources(app.at.timestamp(),None,True)
        self.assertEqual(assessment['watts'],0.)
        self.assertEqual(assessment['quality'],'estimated')
        self.assertFalse(assessment['a']['meter_valid'])

    def test_temperature_lost_during_planning_cannot_dispatch_or_hold_a_heat_request(self):
        from test_fallback_recovery import FallbackRecoveryTests
        for last in ('off','heat'):
            app=FallbackRecoveryTests().app(hour=23)
            app.memory.update(last_command=last,command_at=app.at.timestamp()-30)
            app.source_temperature=lambda:(None,'Немає перевіреного датчика',None,23.)
            with patch('heating_climate.time.time',return_value=app.at.timestamp()):app.apply_action('heat',False)
            self.assertNotEqual(app.memory['last_command'],'heat')
            self.assertLess(app.attrs['temperature'],app.attrs['current_temperature'])

    def test_history_retains_room_measurements_when_power_is_unknown_and_marks_observed_events(self):
        case=comparison.TemperatureComparisonTests();case.setUp();self.addCleanup(case.temp.cleanup)
        at=case.start+3600
        case.actual=[dict(ts=at,room=21.2,room_valid=True,quality=False,grid='on',power=6.,
            power_quality='measured',thermal_model_version=6,comfort_policy=dict(target=21.5,lower=21.,upper=22.)),
            dict(ts=at+300,room=21.3,room_valid=True,quality=False,grid='off',power=0.,
                power_quality='measured',thermal_model_version=7),
            dict(ts=at+600,room=21.3,room_valid=True,quality=False,grid='unknown',power=None,power_quality='unknown')]
        case.write_actual();result=case.journal.temperature_comparison(case.start,case.end)
        self.assertEqual(result['actual'][0]['temperature'],21.2)
        self.assertEqual(result['actual'][0]['lower'],21.)
        self.assertEqual({r['kind'] for r in result['markers']},{'actual_heat','grid_off','unknown','model_change'})
        self.assertEqual(next(m for m in result['markers'] if m['kind']=='model_change')['model_version'],7)

    def test_identical_publications_are_skipped_but_lost_ha_state_is_rebuilt(self):
        app=HeatingClimate();states={};calls=[]
        app.get_state=lambda entity,**kw:states.get(entity)
        def set_state(entity,**value):states[entity]=value;calls.append(entity)
        app.set_state=set_state
        for _ in range(20):app.publish('sensor.heating_test','ready',value=21.)
        self.assertEqual(len(calls),1)
        states.clear();app.publish('sensor.heating_test','ready',value=21.)
        self.assertEqual(len(calls),2)
        app.publish('sensor.heating_test','ready',value=22.)
        self.assertEqual(len(calls),3)
    def test_room_thresholds_follow_observed_reporting_and_respect_owner_limit(self):
        samples=[dict(room_reported_at=i*3600.) for i in range(20)]
        limits=environment.room_reporting_limits(samples,14400)
        self.assertEqual(limits['cadence_seconds'],3600)
        self.assertEqual(limits['warning_seconds'],5400)
        self.assertEqual(limits['critical_seconds'],10800)
        self.assertLessEqual(environment.room_reporting_limits(samples,7200)['critical_seconds'],7200)
        self.assertEqual(environment.room_reporting_limits([],14400)['critical_seconds'],14400)

    def test_undated_or_stale_thermostat_bias_cannot_supply_backup_temperature(self):
        app=HeatingClimate();at=datetime(2026,11,1,14,tzinfo=ZoneInfo('Europe/Kyiv'))
        app.cfg=dict(room='room',room_max_age=14400,thermostat='thermostat');app.memory=dict(thermostat_bias=.5)
        app.val=lambda key,default=None,attribute=None:None if key=='room' else 22.5
        app.raw=lambda key,attribute=None:'heat'
        app.get_state=lambda entity,**kw:dict(state='unavailable' if entity=='room' else 'heat',last_reported=at.isoformat())
        with patch('heating_climate.time.time',return_value=at.timestamp()):
            self.assertIsNone(app.source_temperature()[0])
            app.memory['thermostat_bias_at']=(at-timedelta(days=2)).timestamp()
            self.assertIsNone(app.source_temperature()[0])

    def test_T01_four_hour_missing_room_uses_bias_until_fresh_disagreement(self):
        app=HeatingClimate();at=datetime(2026,11,14,14,tzinfo=ZoneInfo('Europe/Kyiv'));stamp=at.timestamp()
        app.cfg=dict(room='room',room_max_age=14400,thermostat='thermostat')
        app.memory=dict(thermostat_bias=.5,thermostat_bias_at=stamp-4*3600)
        primary=[None]
        app.val=lambda key,default=None,attribute=None:primary[0] if key=='room' else 22.5
        app.raw=lambda *a,**k:'heat'
        app.get_state=lambda entity,**kw:dict(state='unavailable' if entity=='room' and primary[0] is None else 'heat',last_reported=at.isoformat())
        with patch('heating_climate.time.time',return_value=stamp):
            self.assertEqual(app.source_temperature()[:2],(22.,'Термостат · резерв'))
            primary[0]=19.
            self.assertEqual(app.source_temperature()[0],19.)
            self.assertNotIn('thermostat_bias_at',app.memory)
            primary[0]=None
            self.assertIsNone(app.source_temperature()[0])

    def test_latest_eligible_issue_may_be_older_than_twelve_hours_ten_minutes(self):
        case=accountability.FrozenTemperatureTests();case.setUp()
        case.observe(case.at-timedelta(hours=12,minutes=30),forecast=case.forecast)
        case.observe(case.at-timedelta(seconds=15));case.observe(case.at,power=6000.)
        row=next(iter(case.app.memory['temperature_history'].values()))
        self.assertIsNotNone(row['forecast'])
        self.assertAlmostEqual(row['forecast']['lead_hours'],12.5)

    def test_calculation_start_cannot_masquerade_as_an_earlier_issue_time(self):
        case=accountability.FrozenTemperatureTests();case.setUp()
        late=dict(case.forecast,issued_at=(case.at-timedelta(hours=12)+timedelta(seconds=20)).timestamp())
        case.observe(case.at-timedelta(hours=12),forecast=late)
        case.observe(case.at,power=None)
        row=next(iter(case.app.memory['temperature_history'].values()))
        self.assertIsNone(row['forecast'])

    def test_forward_temperature_checks_use_the_original_curve_and_real_report_time(self):
        case=accountability.FrozenTemperatureTests();case.setUp();issued=case.at-timedelta(hours=12)
        case.app.temperature_quality=dict(primary_age_seconds=0.,cadence_seconds=3600.)
        case.observe(issued,forecast=case.forecast)
        self.assertEqual({p['horizon'] for p in case.app.memory['temperature_checks']},{1,3,6,12,24})
        case.app.memory=json.loads(json.dumps(case.app.memory))  # restart retains prospective checks
        original=copy.deepcopy(case.app.model)
        case.forecast['curve'][1]['temperature']=35.  # a later caller cannot rewrite issued evidence
        case.app.temperature_quality['primary_age_seconds']=1200.
        case.observe(issued+timedelta(hours=1,minutes=20),room=21.3)
        results=case.app.memory['temperature_errors'];self.assertEqual(len(results),1)
        self.assertEqual(results[0]['actual_at'],(issued+timedelta(hours=1)).timestamp())
        self.assertAlmostEqual(results[0]['forecast_temperature'],20.78)
        self.assertAlmostEqual(results[0]['actual_horizon_hours'],1.)
        self.assertEqual(case.app.model,original)

    def test_broken_optional_temperature_checks_cannot_block_future_checkpoints(self):
        case=accountability.FrozenTemperatureTests();case.setUp()
        case.app.memory.update(temperature_checks=[None,{},dict(target_at='broken')],temperature_errors=None)
        case.observe(case.at,power=None)
        self.assertEqual(case.app.memory['temperature_checks'],[])
        self.assertEqual(case.app.memory['temperature_errors'],[])
        self.assertEqual(case.app.memory['forecast_validation_rejections']['invalid_temperature_check'],3)
        self.assertEqual(len(case.app.memory['temperature_history']),1)

    def test_energy_forecast_validation_also_queues_one_and_three_hour_checks(self):
        import test_incident_20261029 as incident
        case=incident.PredictionContractTests();app=case.app();app.grid='on'
        at=app.at.timestamp();forecast=dict(curve=[dict(ts=at+i*1800,temperature=21.,heat_segments=[]) for i in range(1,49)])
        app.validate_reserve_predictions(app.at,21.,0.,True,forecast,dict(floor=24.,outside=8.,fire=0.))
        self.assertTrue({'1','3','6','12','24'}<={p['horizon'] for p in app.memory['reserve_predictions']})

    def test_calendar_checkpoint_does_not_require_a_physical_heat_edge(self):
        case=accountability.FrozenTemperatureTests();case.setUp()
        case.observe(case.at-timedelta(hours=12),forecast=case.forecast)
        # Unknown power after reload must not erase a previously issued curve.
        case.observe(case.at,power=None)
        rows=list(case.app.memory['temperature_history'].values())
        self.assertEqual(len(rows),1)
        self.assertEqual(rows[0]['reference_at'],case.at.timestamp())
        self.assertEqual(rows[0]['reference_kind'],'window_start')
        self.assertIsNotNone(rows[0]['forecast'])
        self.assertFalse(rows[0]['start_known'])

    def test_actual_history_continues_after_the_advance_forecast_ends(self):
        case=accountability.FrozenTemperatureTests();case.setUp()
        case.forecast['curve']=[dict(ts=case.at.timestamp()+i*1800,temperature=21.) for i in range(-23,3)]
        case.observe(case.at-timedelta(hours=12),forecast=case.forecast)
        case.observe(case.at,power=None)
        case.observe(case.at+timedelta(hours=12),power=None,room=21.7)
        row=next(iter(case.app.memory['temperature_history'].values()))
        self.assertEqual(row['actual'][-1]['temperature'],21.7)

    def test_actual_checkpoint_covers_the_extra_hour_on_the_autumn_dst_day(self):
        case=accountability.FrozenTemperatureTests();case.setUp()
        case.at=datetime(2026,10,24,23,tzinfo=ZoneInfo('Europe/Kyiv'))
        case.forecast['curve']=[dict(ts=case.at.timestamp()+i*1800,temperature=21.) for i in range(-23,25)]
        case.observe(case.at-timedelta(hours=12),forecast=case.forecast);case.observe(case.at,power=None)
        case.observe(datetime(2026,10,25,22,30,tzinfo=case.at.tzinfo),power=None,room=21.7)
        row=next(iter(case.app.memory['temperature_history'].values()))
        self.assertEqual(row['actual'][-1]['temperature'],21.7)

    def test_missing_forecast_point_is_retained_as_a_gap(self):
        case=comparison.TemperatureComparisonTests();case.setUp();self.addCleanup(case.temp.cleanup)
        at=case.start+86400
        stored={'window':dict(cycle_id='window',started_at=at,start_known=True,
            forecast=dict(at=at-43200,plan_id='original',curve=[dict(ts=at,temperature=0.),
                dict(ts=at+1800,temperature=None),dict(ts=at+3600,temperature=21.)]),actual=[])}
        result=case.journal.temperature_comparison(case.start,case.end,stored)
        self.assertEqual([p['temperature'] for p in result['cycles'][0]['forecast']['curve']],[0.,None,21.])

    def test_system_settings_publish_only_compact_values_and_keep_save_receipt(self):
        app=HeatingClimate();app.cfg={};app.model=dict(DEFAULT_MODEL)
        app.memory=dict(system_settings_revision=3,system_settings_persisted_revision=3,
            system_settings_receipt=dict(last_request_id='saved',last_success=True))
        sent=[];app.publish=lambda entity,state,**attrs:sent.append((entity,attrs))
        app.publish_system_settings();attrs=sent[-1][1]
        self.assertLess(len(json.dumps(attrs).encode()),8192)
        self.assertIn('room_max_age',attrs['values'])
        self.assertEqual(attrs['last_request_id'],'saved')

    def test_history_summary_does_not_publish_a_month_of_points(self):
        app=HeatingRecovery();app.memory={};app.history_comparison=dict(start=1,end=2,
            actual=[dict(ts=i,temperature=21.) for i in range(9000)],cycles=[])
        sent=[];app.publish=lambda entity,state,**attrs:sent.append(attrs)
        app.publish_plan_history()
        self.assertLess(len(json.dumps(sent[-1]).encode()),8192)
        self.assertEqual(sent[-1]['comparison']['actual_count'],9000)

    def test_audited_live_snapshot_owns_all_power_diagnostic_flags(self):
        case=provenance.PowerProvenanceTests();app=case.app();app.cfg['power_measurement_policy']='audited'
        app.stale.add('local2')  # stale AppDaemon cache, independently refreshed by HA snapshot
        app.assess_power_sources=lambda *a:dict(watts=3000.,quality='measured',reason='primary_balance',
            a=dict(valid=True,watts=3000.,quality='measured'),b=dict(valid=False,quality='unknown'))
        _,attrs=case.run_power(app)
        self.assertEqual(attrs['power_quality'],'measured')
        self.assertFalse(attrs['primary_failed'])
        self.assertEqual(attrs['stale_sources'],[])

    def test_conflicting_power_methods_are_explicitly_contradictory(self):
        result=combine_power(dict(valid=True,watts=3000.),dict(valid=True,watts=6000.))
        self.assertEqual(result['quality'],'contradictory')
        self.assertIsNone(result['watts'])

    def test_humidity_heartbeat_cannot_refresh_a_five_hour_old_room_value(self):
        app=HeatingClimate();at=datetime(2026,11,1,14,tzinfo=ZoneInfo('Europe/Kyiv'))
        app.cfg=dict(room='room',room_heartbeat=['room','humidity'],room_max_age=14400,thermostat='thermostat')
        app.memory={};app.val=lambda key,default=None,attribute=None:21.8 if key=='room' else 22.5
        app.raw=lambda key,attribute=None:'heat'
        app.get_state=lambda entity,**kw:dict(state='21.8',last_reported=(at-timedelta(hours=5) if entity=='room' else at).isoformat())
        with patch('heating_climate.time.time',return_value=at.timestamp()):
            room,source,_,_=app.source_temperature()
        self.assertIsNone(room)
        self.assertNotEqual(source,'Вітальня')


if __name__=='__main__':unittest.main()
