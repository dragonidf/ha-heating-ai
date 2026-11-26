"""Independent reports and fail-closed adaptive evidence."""
from datetime import datetime, timezone
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from heating_learning_windows import (audit_transitions, build_observation_windows,
    evaluate_windows, load_evidence, prospective_gate, review_limits,
    run_learning, save_evidence, solar_context_at)
from heating_model import DEFAULT_MODEL, transitions


BASE = datetime(2026, 11, 1, 0, tzinfo=timezone.utc).timestamp()


def samples(minutes=120, report_every=60, *, day=0, power=0.):
    """Real report at each endpoint, cached values on all intervening rows."""
    rows = []
    start = BASE + day*86400
    for minute in range(0, minutes+1, 5):
        report = minute-minute%report_every
        rows.append(dict(ts=start+minute*60, room_reported_at=start+report*60,
                         report_verified=minute==report, room=21.-.002*report,
                         source="Вітальня", room_valid=True, quality=minute==report,
                         grid="on", voltage_qualified=True, power_source="kws_phases",
                         power_quality="measured", power=power, execution="automatic" if power else "idle",
                         manual_extra_active=False, outside=5., return_=23.,
                         fire=0., fire_valid=True, fire_active=False,
                         shadow_ventilation=[0., 0.], epoch="boot-1",
                         thermal_model_version=6, equipment_version=1, boiler_power_mode="fixed_6",
                         weather_issue_at=start-300, solar_factor=0.))
        rows[-1]["return"] = 23.
    return rows


class StorageLearningTest(unittest.TestCase):
    model=dict(__import__('heating_model').DEFAULT_MODEL,version=6,validated=True,
               floor_air=.055,room_loss=.022,floor_decay=.38,heat_gain=1.0368)

    def windows(self,field,days=10,shift=.01):
        from heating_model import step
        truth={**self.model,field:self.model[field]*(1+shift)}
        output=[]
        for day in range(days):
            for j,(floor,kw,seconds) in enumerate(((33.+day%8,0.,7200),
                    (22.+day%3,7.+day%3,7200),(40.+day%4,0.,1800),(21.+day%3,5.,3600))):
                start=BASE+day*86400+j*10800
                room,f=21.,floor
                steps=[]
                for i in range(seconds//300):
                    outside=-6+2*day%17
                    steps.append([300,outside,kw,0.,f])
                    room,f=step(room,f,outside,kw,0.,truth,1/12)
                output.append(dict(start=start,end=start+seconds,day=f'2026-11-{day+1:02}',
                    room_start=21.,room_end=room,floor=floor,steps=steps,
                    heat=seconds if kw else 0,idle=0 if kw else seconds,sun=0.,
                    equipment=[6,1,'fixed_6'],vent=[0.,0.]))
        return output

    def test_storage_coefficients_need_excitation_and_later_independent_holdout(self):
        from heating_learning_windows import evaluate_windows,promotion_allowed
        now=datetime(2026,11,11,tzinfo=timezone.utc)
        for field in ('floor_decay','heat_gain'):
            with self.subTest(field=field):
                result=evaluate_windows(self.windows(field),self.model)
                self.assertEqual(result['status'],'holdout_passed')
                self.assertEqual(result['coefficients'],{field:self.model[field]*1.01})
                self.assertTrue(promotion_allowed(self.model,self.model,result['coefficients'],
                    {'status':'accepted'},{},now))
                self.assertFalse(promotion_allowed(self.model,self.model,result['coefficients'],
                    {'status':'rejected'},{},now))

    def test_storage_fit_rejects_weak_support_and_conflicting_later_days(self):
        from heating_learning_windows import _storage_candidate,evaluate_windows
        self.assertIsNone(_storage_candidate(self.windows('heat_gain',days=2),self.model,self.model,None))
        self.assertIsNone(_storage_candidate([w for w in self.windows('heat_gain') if w['heat']],
                                             self.model,self.model,None))
        rows=self.windows('heat_gain')
        rows[-8:]=self.windows('heat_gain',shift=-.01)[-8:]
        result=evaluate_windows(rows,self.model)
        self.assertEqual(result['status'],'holdout_rejected')
        self.assertIsNone(result['coefficients'])


class WindowsTest(unittest.TestCase):
    def test_rolling_drift_blocks_small_changes_when_path_budget_is_used(self):
        from heating_learning_windows import promotion_allowed
        now = datetime.fromtimestamp(BASE + 8*86400, timezone.utc)
        active = dict(DEFAULT_MODEL, validated=True)
        value = active["room_loss"]
        history = [{"at":now.timestamp()-day*86400,
                    "changes":{"room_loss":[value/1.01,value]}} for day in range(1,6)]
        proposed = {"room_loss":value*1.005}
        self.assertFalse(promotion_allowed(active,active,proposed,{"status":"accepted"},
                                           {"changes_history":history},now))
        self.assertTrue(promotion_allowed(active,active,proposed,{"status":"accepted"},
                                          {"changes_history":history[:3]},now))

    def test_qualified_ventilation_does_not_change_unidentifiable_room_loss(self):
        from heating_model import step
        model=dict(DEFAULT_MODEL,version=6,validated=True)
        ventilation=dict(validated=True,model_version=6,base_room_loss=.015,
                         recuperator=.002,bath=.001)
        truth=dict(model,floor_air=model["floor_air"]*1.01,room_loss=.017)
        windows=[]
        for day in range(6):
            for j,kw in enumerate((0.,4.)):
                room,floor=21.,22.+4*j
                start=BASE+day*86400+j*7200
                steps=[]
                for i in range(12):
                    steps.append([300,3.+day,kw,0.,floor])
                    room,floor=step(room,floor,3.+day,kw,0.,truth,1/12)
                windows.append(dict(start=start,end=start+3600,day=f"2026-11-{day+1:02}",
                    room_start=21.,room_end=room,floor=steps[0][4],steps=steps,
                    heat=3600 if kw else 0,idle=0 if kw else 3600,sun=0.,
                    equipment=[6,1,"fixed_6"],vent=[1.,0.]))
        report=evaluate_windows(windows,model,ventilation)
        self.assertEqual(report["status"],"holdout_passed")
        self.assertEqual(report["coefficients"],{"floor_air":truth["floor_air"]})

    def test_verified_manual_session_is_physics_only(self):
        from heating_learning_windows import _continuous, IDENTITY
        rows = samples(60, power=4.)
        for row in rows:
            row.update(manual_extra_active=True, execution="manual_or_native",
                       manual_measurement_id=BASE-60, interval_kw=4.,
                       shadow_reasons=("manual_extra_request",))
        evidence = build_observation_windows(rows, timezone.utc)
        self.assertEqual(evidence["windows"][0]["heat"], 3600)
        self.assertEqual(evidence["eligibility_counts"]["manual_physics_windows"], 1)
        timestamps = [r["ts"] for r in rows]
        identity = tuple(rows[0].get(k) for k in IDENTITY)
        _, reasons = _continuous(rows, timestamps, timestamps[0], timestamps[-1], identity)
        self.assertIn("manual_extra_request", reasons)
        rows[5]["manual_measurement_id"] = BASE+10
        self.assertFalse(build_observation_windows(rows, timezone.utc)["windows"])

    def test_off_ventilation_without_supply_can_be_observed(self):
        from heating_learning_windows import ventilation_current_qualified, ventilation_interval_duty
        off={"recuperator":{"relay":"off","supply":"unknown"},
             "bath":{"relay":"off","supply":"unknown"}}
        self.assertTrue(ventilation_current_qualified(off))
        self.assertFalse(ventilation_current_qualified({
            **off,"recuperator":{"relay":"on","supply":"unknown"}}))
        self.assertTrue(ventilation_current_qualified({
            **off,"recuperator":{"relay":"on","supply":"on"}}))
        idle=dict(seconds=300,on_fraction=0.,unknown_fraction=0.,supply_unknown_fraction=1.,
                  powered_relay_on_fraction=None,supply_on_fraction=0.)
        self.assertEqual(ventilation_interval_duty(idle),0.)
        for changed in ({"on_fraction":1.},{"unknown_fraction":.05},
                        {"on_fraction":.5},{"seconds":100}):
            self.assertIsNone(ventilation_interval_duty({**idle,**changed}))
        active=dict(seconds=300,on_fraction=1.,unknown_fraction=0.,supply_unknown_fraction=0.,
                    powered_relay_on_fraction=1.,supply_on_fraction=1.)
        self.assertEqual(ventilation_interval_duty(active),1.)
        self.assertIsNone(ventilation_interval_duty({**active,"supply_unknown_fraction":.2}))
        self.assertIsNone(ventilation_interval_duty({**active,"powered_relay_on_fraction":.5}))

    def test_verified_zero_ventilation_supply_with_unavailable_relay(self):
        from heating_learning_windows import ventilation_current_qualified, ventilation_interval_duty
        bath={"relay":"off","supply":"unknown"}
        for relay in ("on","off","unavailable","unknown",None):
            self.assertTrue(ventilation_current_qualified({
                "recuperator":{"relay":relay,"supply":"off"}, "bath":bath}))
        for supply in ("on","unknown",None):
            self.assertFalse(ventilation_current_qualified({
                "recuperator":{"relay":"unavailable","supply":supply}, "bath":bath}))
        zero=dict(seconds=300,on_fraction=0.,unknown_fraction=1.,
                  supply_unknown_fraction=0.,supply_on_fraction=0.,
                  powered_relay_on_fraction=0.)
        self.assertEqual(ventilation_interval_duty(zero),0.)
        for changed in ({"supply_unknown_fraction":.1},{"supply_on_fraction":.1},
                        {"powered_relay_on_fraction":.1},{"powered_relay_on_fraction":None},
                        {"supply_on_fraction":None},{"seconds":100}):
            self.assertIsNone(ventilation_interval_duty({**zero,**changed}))

    def test_blackout_evaluates_history_without_promoting_model(self):
        from tools.replay_adaptive_learning import MODEL, TZ, fixture
        rows=fixture(8)
        now=datetime.fromtimestamp(rows[-1]["ts"]+300,TZ)
        with TemporaryDirectory() as folder:
            path=Path(folder)/"online_learning_v1.json"
            result=run_learning(path,rows,[],MODEL,{},{},now,False)
            self.assertEqual(result["status"],"frozen")
            self.assertEqual(result["reason"],"current_conditions_unqualified")
            self.assertNotIn("proposed",result)
            self.assertGreater(result["verified_reports"],0)
            self.assertIn("rejection_counts",result)
            self.assertIn("analysis_status",result)
            self.assertGreater(len(load_evidence(path)["windows"]),0)

    def test_real_reports_survive_cached_temperature_ticks(self):
        for cadence in (5, 10, 15, 30, 60, 120):
            with self.subTest(cadence=cadence):
                rows = samples(cadence, cadence)
                result = build_observation_windows(rows, timezone.utc)
                self.assertEqual(len(result["windows"]), 1)
                self.assertEqual(result["eligible_minutes"], cadence)
                self.assertEqual(result["unique_primary_reports"], 2)
                self.assertEqual(result["windows"][0]["room_end"], rows[-1]["room"])
                if cadence > 5:
                    self.assertFalse(transitions(rows))

    def test_republish_or_bad_intermediate_evidence_breaks_window(self):
        invalid = [
            dict(report_verified=False), dict(manual_extra_active=True), dict(power_quality="estimated"),
            dict(grid="off"), dict(voltage_qualified=False), dict(shadow_ventilation=None),
            dict(room_source_disagreement=True), dict(equipment_version=2), dict(epoch="reboot"),
            dict(fire_valid=False), dict(weather_issue_at=BASE+86400), dict(power=None),
        ]
        for fields in invalid:
            with self.subTest(fields=fields):
                rows = samples(60)
                rows[-1 if 'report_verified' in fields else 5].update(fields)
                self.assertFalse(build_observation_windows(rows, timezone.utc)["windows"])

    def test_internal_circulation_does_not_block_no_fire_windows(self):
        rows=samples(60)
        for row in rows:
            row.update(circulation_active=True,circulation_role="internal_redistribution")
        self.assertEqual(len(build_observation_windows(rows,timezone.utc)["windows"]),1)
        for field,value in (("fire_active",True),("fire_valid",False),
                            ("room_source_disagreement",True),("circulation_role","unknown")):
            altered=[dict(row) for row in rows]
            altered[5][field]=value
            self.assertFalse(build_observation_windows(altered,timezone.utc)["windows"])

    def test_daylight_idle_residual_is_solar_only_not_base_model_evidence(self):
        rows=samples(60)
        for row in rows:
            row.update(solar_factor=.5,external_gain=.2)
        rows[-1]["room"]=21.4
        result=build_observation_windows(rows,timezone.utc)
        self.assertEqual(len(result["windows"]),1)
        self.assertTrue(result["windows"][0]["solar_only"])
        self.assertEqual(result["eligibility_counts"]["automatic_physics_windows"],0)
        self.assertEqual(result["eligibility_counts"]["solar_only_windows"],1)
        self.assertEqual(evaluate_windows(result["windows"],dict(DEFAULT_MODEL,version=6))["status"],"insufficient_data")
        for change in (dict(solar_factor=0.),dict(power=6.),dict(fire_active=True),
                       dict(room_source_disagreement=True),dict(shadow_ventilation=None),
                       dict(power_quality="estimated"),dict(grid="unknown")):
            altered=[{**row,**change} for row in rows]
            self.assertFalse(build_observation_windows(altered,timezone.utc)["windows"],change)

    def test_fast_daytime_rise_requires_corroboration_and_plausible_rate(self):
        rows=samples(60)
        for row in rows:
            row.update(solar_factor=.5,external_gain=.2,room_source_status="agree")
        rows[-1]["room"]=22.
        result=build_observation_windows(rows,timezone.utc)
        self.assertTrue(result["windows"][0]["solar_only"])
        for change in (dict(room_source_status="unconfirmed"),dict(room=24.9)):
            altered=[dict(row) for row in rows]
            altered[-1].update(change)
            self.assertFalse(build_observation_windows(altered,timezone.utc)["windows"])

    def test_verified_outage_is_thermal_only_and_needs_full_continuous_proof(self):
        from heating_learning_windows import row_reasons
        rows=samples(60)
        for row in rows:
            row.update(grid="off",power=None,power_quality="unknown",power_source="unknown",
                interval_kw=None,voltage_qualified=False,thermal_inputs_ok=True,
                zero_heat_provenance="verified_mains_isolation",zero_heat_interval_verified=True,
                shadow_reasons=["unqualified_outage","unqualified_kws","equipment_not_ready",
                                "unavailable_physical_input","temporary_fallback"])
        result=build_observation_windows(rows,timezone.utc)
        self.assertEqual(len(result["windows"]),1)
        self.assertTrue(result["windows"][0]["outage_physics"])
        self.assertEqual(result["windows"][0]["idle"],3600)
        self.assertEqual(result["eligibility_counts"]["electrical_intervals"],0)
        self.assertEqual(result["eligibility_counts"]["outage_physics_minutes"],60)
        self.assertIn("unqualified_outage",row_reasons(rows[0],strict=True))
        for change in (dict(zero_heat_interval_verified=False),dict(zero_heat_provenance=None),
                       dict(grid="unknown"),dict(power=3.),dict(power_quality="contradictory"),
                       dict(thermal_inputs_ok=False),dict(shadow_ventilation=None),
                       dict(fire_active=True),dict(shadow_reasons=["tick_gap_or_restart"])):
            altered=[dict(row) for row in rows]
            altered[5].update(change)
            self.assertFalse(build_observation_windows(altered,timezone.utc)["windows"],change)

    def test_large_gap_and_shared_endpoint_are_not_counted(self):
        rows = samples(120)
        self.assertEqual(build_observation_windows(rows, timezone.utc)["eligible_minutes"], 60)
        self.assertFalse(build_observation_windows(rows[:3]+rows[7:13], timezone.utc)["windows"])

    def test_old_audit_has_exact_selection_and_multiple_labels(self):
        rows = samples(60)
        rows[1].update(manual_extra_active=True, grid="unknown", power=None)
        result = audit_transitions(rows)
        self.assertEqual(result["eligible_pairs"], len(transitions(rows)))
        self.assertIn("manual_extra_request", result["pairs"][0]["reasons"])
        self.assertIn("unknown_grid", result["pairs"][0]["reasons"])
        self.assertIn("room_report_repeated", result["pairs"][0]["reasons"])

    def test_holdout_cannot_create_candidate_from_too_few_days(self):
        evidence = build_observation_windows(samples(120, 120), timezone.utc)["windows"]
        report = evaluate_windows(evidence, dict(DEFAULT_MODEL, version=6))
        self.assertEqual(report["status"], "insufficient_data")

    def test_independent_cooling_days_can_identify_only_room_heat_loss(self):
        from heating_model import step
        from tools.replay_adaptive_learning import MODEL

        actual=dict(MODEL,room_loss=MODEL["room_loss"]*1.01)
        windows=[]
        for day in range(8):
            rows=samples(120,120,day=day)
            room,floor=21.,21.
            outside=2.+day%4
            for row in rows:
                row.update(outside=outside,room=room if row["report_verified"] else 21.)
                row["return"]=floor
                room,floor=step(room,floor,outside,0.,0.,actual,1/12)
            windows.extend(build_observation_windows(rows,timezone.utc)["windows"])
        result=evaluate_windows(windows,MODEL)
        self.assertEqual(result["status"],"holdout_passed")
        self.assertEqual(result["coefficients"],{"room_loss":actual["room_loss"]})

    def test_prospective_gate_requires_both_horizons_and_coverage(self):
        issue = BASE
        rows = [dict(start=issue, end=issue+3600, day="2026-11-01", horizon=1,
                     old_error=.4, shadow_error=.1, valid=True)]
        result = prospective_gate(rows*20, issued_at=issue, now=issue+15*86400)
        self.assertFalse(result["evidence_passed"])
        self.assertIn("prospective_3h", result["blockers"])
        self.assertFalse(result["promotion_allowed"])

    def test_versioned_storage_round_trip_and_corruption(self):
        state = dict(windows=[], forecasts=[], comparisons=[], report={"mode":"observe"})
        with TemporaryDirectory() as folder:
            path = Path(folder)/"adaptive.json"
            save_evidence(path, state)
            first = path.read_bytes()
            self.assertEqual(load_evidence(path), state)
            save_evidence(path, state)
            self.assertEqual(path.read_bytes(), first)
            path.write_bytes(first.replace(b"observe", b"altered"))
            with self.assertRaises(ValueError):
                load_evidence(path)

    def test_history_separates_heldout_days_and_refuses_promotion(self):
        from tools.replay_adaptive_learning import MODEL, TZ, fixture, frozen_fixture
        rows = fixture()
        result = build_observation_windows(rows,TZ)
        fit = evaluate_windows(result["windows"],MODEL)
        self.assertEqual(fit["status"],"holdout_passed")
        self.assertEqual(fit["holdout_days"],4)
        self.assertEqual(set(fit["coefficients"]),{"floor_air","room_loss"})
        self.assertLess(fit["shadow"]["mae"],fit["old"]["mae"])
        issue = datetime(2026,11,18,tzinfo=TZ).timestamp()
        checks = frozen_fixture(dict(coefficients=fit["coefficients"]))
        gate = prospective_gate(checks,issued_at=issue,now=issue+17*86400)
        self.assertFalse(gate["promotion_allowed"])
        self.assertIn("cold_or_constrained_1h",gate["blockers"])
        self.assertEqual(gate["metrics"]["6"]["old"]["n"],16)

    def test_midnight_and_second_dst_fold_use_absolute_time(self):
        from zoneinfo import ZoneInfo
        local = ZoneInfo("Europe/Kyiv")
        for at, expected in ((datetime(2026,10,25,3,30,tzinfo=local,fold=0),60),
                             (datetime(2026,10,24,23,30,tzinfo=local),0)):
            rows=samples(60)
            offset=at.timestamp()-BASE
            for row in rows:
                for field in ("ts","room_reported_at","weather_issue_at"):
                    row[field]+=offset
            self.assertEqual(build_observation_windows(rows,local)["eligible_minutes"],expected)

    def test_solar_fit_uses_later_independent_days_and_real_baseline(self):
        from heating_learning_windows import evaluate_solar
        from heating_model import step
        model=dict(DEFAULT_MODEL,version=6,validated=True)
        rows=[]
        for day in range(6):
            for slot in range(4):
                start=BASE+day*86400+slot*3600
                sun=.3+.1*slot
                room,floor=21.,23.
                steps=[]
                for _ in range(12):
                    steps.append([300,8.,0.,sun,floor])
                    next_room,next_floor=step(room,floor,8.,0.,0.,model,1/12)
                    room,floor=next_room+.5*sun/12,next_floor
                rows.append(dict(start=start,end=start+3600,day=f"2026-11-{day+1:02}",
                    room_start=21.,room_end=room,floor=23.,steps=steps,
                    heat=0,idle=3600,sun=sun,vent=[0.,0.],equipment=[6,1,"fixed_6"]))
        fit=evaluate_solar(rows,model,commissioning=0.)
        self.assertEqual(fit["status"],"holdout_passed")
        self.assertAlmostEqual(fit["coefficient"],.5)
        self.assertFalse(fit["comfort_credit"])
        self.assertLessEqual(fit["training_until"],fit["holdout_start"])
        self.assertEqual(evaluate_solar(rows,model,commissioning=.5)["status"],"holdout_rejected")
        self.assertEqual(evaluate_solar(rows[:12],model,commissioning=0.)["status"],"insufficient_data")

    def test_solar_missing_condition_future_issue_and_missing_geometry(self):
        from zoneinfo import ZoneInfo
        tz=ZoneInfo("Europe/Kyiv")
        at=datetime(2026,11,1,12,tzinfo=tz)
        stamp=at.timestamp()
        geometry={"latitude":50.,"days":{at.date().isoformat():{"noon":stamp,
                   "sunrise":stamp-18000,"sunset":stamp+18000,"declination":-12.}}}
        self.assertIsNone(solar_context_at(stamp,tz,[{"ts":stamp}],stamp,geometry))
        row={"ts":stamp,"condition":"sunny"}
        self.assertIsNone(solar_context_at(stamp,tz,[row],stamp+1,geometry))
        self.assertIsNone(solar_context_at(stamp,tz,[row],stamp,{"days":{}}))
        self.assertGreater(solar_context_at(stamp,tz,[row],stamp,geometry),0.)
        self.assertLess(solar_context_at(stamp,tz,[dict(row,cloud_coverage=100)],stamp,geometry),
                        solar_context_at(stamp,tz,[row],stamp,geometry))

    def test_idempotent_sidecar_and_legacy_files_untouched(self):
        from tools.replay_adaptive_learning import MODEL, TZ, fixture
        rows=fixture(6)
        now=datetime.fromtimestamp(rows[-1]["ts"]+300,TZ)
        with TemporaryDirectory() as folder:
            root=Path(folder)
            path=root/"online_learning_v1.json"
            state=root/"state.json"
            ledger=root/"energy.json"
            for file in (state,ledger): file.write_bytes(b"legacy")
            result=run_learning(path,rows,[],MODEL,{},{},now,False)
            self.assertEqual(result["status"],"frozen")
            count=len(load_evidence(path)["windows"])
            run_learning(path,rows,[],MODEL,{},{},now,False)
            self.assertEqual(len(load_evidence(path)["windows"]),count)
            self.assertGreater(count,0)
            self.assertEqual(state.read_bytes(),b"legacy")
            self.assertEqual(ledger.read_bytes(),b"legacy")
            path.write_bytes(b"broken")
            self.assertEqual(run_learning(path,rows,[],MODEL,{},{},now,True)["reason"],
                             "learning_storage_or_data_error")
            self.assertEqual(path.read_bytes(),b"broken")

    def test_future_review_is_explicit_bounded_and_never_actuates(self):
        model=DEFAULT_MODEL
        proposed=dict(floor_air=model["floor_air"]*.99)
        change=review_limits(model,proposed,model,manual_approval=True,evidence=True,degraded=False)
        self.assertEqual(change,dict(review_eligible=True,promotion_allowed=False))
        for options in ({"manual_approval":False},{"evidence":False},{"degraded":True},{"already_applied":True}):
            args=dict(manual_approval=True,evidence=True,degraded=False)
            args.update(options)
            self.assertFalse(review_limits(model,proposed,model,**args)["review_eligible"])
        self.assertFalse(review_limits(model,{"floor_air":model["floor_air"]*1.02},model,
                     manual_approval=True,evidence=True,degraded=False)["review_eligible"])


    def asynchronous_report_fixture(self, offset):
        from heating_learning_windows import freeze_forecast
        model=dict(DEFAULT_MODEL,version=6,validated=True)
        now=datetime.fromtimestamp(BASE,timezone.utc)
        settings=dict(target=22.,lower=19.,upper=24.,equipment_version=1,
                      modulation_profile={'physical_mode':'fixed_6'})
        weather=[dict(ts=BASE+i*3600,temperature=5.,condition='cloudy') for i in range(7)]
        fine=[dict(ts=BASE+i*300,temperature=21-i*.01,upper_temperature=21.1-i*.01)
              for i in range(1,73)]
        snapshot=freeze_forecast(now,21.,23.,5.,0.,weather,BASE-60,settings,
            dict(curve=fine,heat_segments=[]),model,
            {'coefficients':{'floor_air':model['floor_air']*1.001}},'boot')
        rows=[]
        for i in range(75):
            stamp=BASE+i*300
            report=next((BASE+3600*h+offset for h in (1,3)
                         if 0<=stamp-(BASE+3600*h+offset)<300),None)
            rows.append(dict(ts=stamp,room=20.8,room_valid=True,source='Вітальня',
                room_reported_at=report,report_verified=report is not None,grid='on',
                voltage_qualified=True,power_source='kws_phases',power_quality='measured',
                power=0.,interval_kw=0.,execution='idle',manual_extra_active=False,
                outside=5.,fire=0.,fire_valid=True,fire_active=False,shadow_ventilation=[0.,0.],
                epoch='boot',thermal_model_version=6,equipment_version=1,boiler_power_mode='fixed_6',
                weather_issue_at=stamp-60,solar_factor=0.,**{'return':23.}))
        return snapshot,rows

    def test_conditional_solar_projection_keeps_control_plan_and_freezes_actual_issue(self):
        from copy import deepcopy
        from heating_learning_windows import project_solar_nominal,freeze_forecast
        from heating_environment import forecast_step
        model=dict(DEFAULT_MODEL,version=6,validated=True)
        start=BASE+9*3600
        now=datetime.fromtimestamp(start,timezone.utc)
        noon=start+3*3600
        geometry=dict(latitude=50.,days={now.date().isoformat():dict(
            noon=noon,sunrise=noon-21600,sunset=noon+21600,declination=-10.)})
        settings=dict(target=22.,lower=19.,upper=25.,equipment_version=1,
            modulation_profile={"physical_mode":"fixed_6"},
            environment=dict(astronomy=geometry,solar=dict(validated=True,coefficient=.5,
                risk_coefficient=.5,control_no_sun=True)))
        weather=[dict(ts=start+i*3600,temperature=5.,condition="sunny") for i in range(7)]
        room,floor=21.,23.
        control=(room,floor);upper=(room,floor)
        blocks=[];segments=[]
        for j in range(12):
            fine=[];heat=[]
            for i in range(6):
                a=start+(j*6+i)*300;b=a+300
                when=datetime.fromtimestamp(a,timezone.utc)
                kw=6. if j==0 else 0.
                control=forecast_step(*control,5.,kw,0.,model,1/12,when,weather,settings)
                upper=forecast_step(*upper,5.,kw,0.,model,1/12,when,weather,settings,upper=True)
                fine.append(dict(ts=b,temperature=control[0],upper_temperature=upper[0]))
                if kw:
                    heat.append(dict(start=a,end=b,power_kw=kw))
            segments+=heat
            blocks.append(dict(ts=b,temperature=control[0],_fine=fine,heat_segments=heat))
        forecast=dict(curve=blocks,heat_segments=segments)
        original=deepcopy(forecast)
        self.assertIsNone(freeze_forecast(now,room,floor,5.,0.,weather,start-60,settings,
            forecast,model,None,"boot"))
        projection=project_solar_nominal(now,room,floor,5.,0.,weather,settings,forecast,model)
        self.assertEqual(forecast,original)
        self.assertEqual(len(projection["curve"]),12)
        self.assertTrue(all(p["temperature"]>=p["no_sun_temperature"]-.005 for p in projection["curve"]))
        self.assertGreater(projection["curve"][5]["temperature"],blocks[5]["temperature"])
        forecast["_solar_nominal_fine"]=projection["fine"]
        snapshot=freeze_forecast(now,room,floor,5.,0.,weather,start-60,settings,forecast,model,None,"boot")
        self.assertFalse(snapshot["settings"]["environment"]["solar"]["control_no_sun"])
        self.assertTrue(settings["environment"]["solar"]["control_no_sun"])
        self.assertEqual(snapshot["points"]["3"][1],projection["fine"][35]["temperature"])
        self.assertEqual(snapshot["segments"],[[s["start"],s["end"],s["power_kw"]] for s in segments])
        bad=deepcopy(original);bad["curve"][0].pop("_fine")
        self.assertIsNone(project_solar_nominal(now,room,floor,5.,0.,weather,settings,bad,model))

    def test_solar_prospective_requires_new_issue_and_explicit_idle_evidence(self):
        from heating_learning_windows import solar_prospective_evidence,qualify_frozen
        from heating_environment import forecast_step
        model=dict(DEFAULT_MODEL,version=6,validated=True)
        archive=[]
        certificate=dict(coefficient=.5,stored_at=BASE,identity=[6,1,"fixed_6"],
                         ventilation_model={})
        for day in range(6):
            for hour in (8,12):
                start=BASE+day*86400+hour*3600
                at=datetime.fromtimestamp(start,timezone.utc)
                date=at.date().isoformat()
                noon=BASE+day*86400+12*3600
                geometry=dict(latitude=50.,days={date:dict(noon=noon,sunrise=noon-21600,
                    sunset=noon+21600,declination=-10.)})
                settings=dict(environment=dict(astronomy=geometry,
                    solar=dict(risk_coefficient=.5,validated=False)))
                truth=dict(environment=dict(astronomy=geometry,
                    solar=dict(risk_coefficient=.5,validated=True,coefficient=.5)))
                weather=[dict(ts=start+i*3600,temperature=5.,condition="sunny") for i in range(4)]
                states=[(21.,23.)]*3
                guards=[];checks=[];points={}
                for i in range(1,37):
                    when=datetime.fromtimestamp(start+(i-1)*300,timezone.utc)
                    for j,(room,floor) in enumerate(states):
                        states[j]=forecast_step(room,floor,5.,0.,0.,model,1/12,when,weather,
                            truth if j==2 else settings,upper=j==1)
                    due=start+i*300
                    old,upper,actual=[r[0] for r in states]
                    guards.append([due,old,upper,15.,30.])
                    if i in (12,36):
                        h=i//12
                        points[str(h)]=[due,old,upper,15.,30.,"День"]
                        checks.append([h,due,old,upper,15.,30.,"День",actual,
                            ["unexplained_gain"],0.,0.,0.,0.,{"solar_idle_qualified":True}])
                snapshot=dict(start=start,model=model,identity=["boot","Вітальня",6,1,"fixed_6"],
                    room=21.,floor=23.,outside=5.,fire=0.,segments=[],weather=weather,
                    settings=settings,weather_known_until=start+14400,points=points,guards=guards)
                archive.append(dict(snapshot=snapshot,checks=checks))
        report=solar_prospective_evidence(certificate,archive,model,timezone.utc,{})
        self.assertEqual(report["status"],"passed")
        self.assertFalse(report["comfort_credit"])
        self.assertGreaterEqual(report["metrics"]["1"]["new"]["n"],12)
        # Monitor only the forecasts actually issued after activation with
        # credit, against the same plan and zero-credit counterfactual.
        from copy import deepcopy
        active={**certificate,"comfort_credit":True,"nominal_at":BASE}
        credited=deepcopy(archive)
        for item in credited:
            solar=item["snapshot"]["settings"]["environment"]["solar"]
            solar.update(validated=True,coefficient=.5)
            for check in item["checks"]:
                check[2]=check[7]  # truth was generated with this exact credit
        self.assertEqual(solar_prospective_evidence(active,credited,model,timezone.utc,{})["status"],"stable")
        for item,original in zip(credited,archive):
            for check,prior in zip(item["checks"],original["checks"]):
                check[7]=prior[2]  # actual now follows the zero-credit trajectory
        self.assertEqual(solar_prospective_evidence(active,credited,model,timezone.utc,{})["status"],
                         "rollback_required")
        self.assertEqual(solar_prospective_evidence({**active,"nominal_at":BASE+7*86400},credited,
            model,timezone.utc,{})["status"],"monitoring")
        self.assertEqual(solar_prospective_evidence({**certificate,"nominal_revoked_at":BASE},archive,
            model,timezone.utc,{})["status"],"rejected")
        self.assertEqual(solar_prospective_evidence({**certificate,"stored_at":BASE+7*86400},
            archive,model,timezone.utc,{})["status"],"insufficient_data")
        legacy=[dict(snapshot=i["snapshot"],checks=[r[:13] for r in i["checks"]]) for i in archive]
        self.assertEqual(solar_prospective_evidence(certificate,legacy,model,timezone.utc,{})["status"],
                         "insufficient_data")
        snapshot,rows=self.asynchronous_report_fixture(0)
        snapshot["settings"]["environment"]={"solar":{"risk_coefficient":.5}}
        for row in rows:
            row.update(solar_factor=.5,external_gain=.2)
        checks=qualify_frozen(snapshot,rows,timezone.utc)
        self.assertTrue(all(c[13]["solar_idle_qualified"] for c in checks))
        for row in rows:
            row.update(power=1.,interval_kw=1.)
        self.assertTrue(all(len(c)==13 or not c[13].get("solar_idle_qualified")
                            for c in qualify_frozen(snapshot,rows,timezone.utc)))

    def test_asynchronous_reports_use_actual_time_and_keep_guards(self):
        from heating_learning_windows import qualify_frozen,score_frozen
        for offset in (-240,-61,61,240,300):
            with self.subTest(offset=offset):
                snapshot,rows=self.asynchronous_report_fixture(offset)
                checks=qualify_frozen(snapshot,rows,timezone.utc)
                self.assertEqual(len(checks),2)
                for h,due,old,upper,lo,hi,zone,actual,reasons,*rest in checks:
                    self.assertEqual(due,BASE+3600*h+offset)
                    self.assertAlmostEqual(old,21-(due-BASE)/300*.01)
                    self.assertEqual(actual,20.8)
                    self.assertFalse(reasons)
                self.assertTrue(all(x['trajectory_guard_ok'] for x in
                                    score_frozen(snapshot,checks,timezone.utc)))

    def test_asynchronous_reports_do_not_extrapolate_or_change_legacy_archive(self):
        from heating_learning_windows import qualify_frozen
        snapshot,rows=self.asynchronous_report_fixture(301)
        self.assertEqual(qualify_frozen(snapshot,rows,timezone.utc),[])
        snapshot,rows=self.asynchronous_report_fixture(240)
        snapshot.pop('report_alignment_seconds')
        self.assertEqual(qualify_frozen(snapshot,rows,timezone.utc),[])
        snapshot,rows=self.asynchronous_report_fixture(240)
        snapshot['guards']=[g for g in snapshot['guards'] if not BASE+3600<=g[0]<=BASE+3900]
        self.assertFalse(any(x[0]==1 for x in qualify_frozen(snapshot,rows,timezone.utc)))

    def test_asynchronous_exact_and_early_report(self):
        from heating_learning_windows import qualify_frozen,score_frozen
        snapshot,rows=self.asynchronous_report_fixture(0)
        checks=qualify_frozen(snapshot,rows,timezone.utc)
        self.assertEqual(checks[0][1],BASE+3600)
        self.assertEqual(checks[0][2],snapshot['points']['1'][1])
        self.assertTrue(all(x['trajectory_guard_ok'] for x in
                            score_frozen(snapshot,checks,timezone.utc)))
        snapshot,rows=self.asynchronous_report_fixture(-300)
        checks=qualify_frozen(snapshot,rows[:12],timezone.utc)
        self.assertEqual(checks[0][1],BASE+3300)

    def test_frozen_weather_snapshot_rejects_lookahead_and_internal_gaps(self):
        from heating_learning_windows import freeze_forecast
        from tools.replay_adaptive_learning import MODEL, TZ
        now=datetime.fromtimestamp(BASE,TZ)
        settings=dict(target=22.,lower=20.,upper=24.)
        result=dict(curve=[dict(ts=BASE+3600,temperature=21.),
                           dict(ts=BASE+10800,temperature=22.)],heat_segments=[])
        def capture(weather,issue):
            return freeze_forecast(now,21.,23.,5.,0.,weather,issue,settings,result,MODEL,None,"boot")
        weather=[dict(ts=BASE,temperature=5.),dict(ts=BASE+7200,temperature=6.)]
        self.assertIsNone(capture(weather,BASE+1))
        snapshot=capture(weather,BASE)
        self.assertEqual(set(snapshot["points"]),{"1"})
        weather[0]["temperature"]=25.
        self.assertEqual(snapshot["weather"][0]["temperature"],5.)
