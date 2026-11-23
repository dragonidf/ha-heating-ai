"""Independent reports and fail-closed adaptive evidence."""
from datetime import datetime, timezone
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from heating_learning_windows import (audit_transitions, build_observation_windows,
    evaluate_windows, load_evidence, mode_for, prospective_gate, review_limits,
    run_shadow, save_evidence, solar_context_at)
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


class WindowsTest(unittest.TestCase):
    def test_60_and_120_minute_reports_survive_cached_temperature_ticks(self):
        for cadence in (60, 120):
            with self.subTest(cadence=cadence):
                rows = samples(cadence, cadence)
                result = build_observation_windows(rows, timezone.utc)
                self.assertEqual(len(result["windows"]), 1)
                self.assertEqual(result["eligible_minutes"], cadence)
                self.assertEqual(result["unique_primary_reports"], 2)
                self.assertEqual(result["windows"][0]["room_end"], rows[-1]["room"])
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

    def test_invalid_mode_fails_closed(self):
        self.assertEqual(mode_for("active"), ("disabled", "invalid_adaptive_learning_mode"))
        self.assertEqual(mode_for("candidate"), ("candidate", None))


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
            path=root/"learning_windows_v1.json"
            state=root/"state.json"
            ledger=root/"energy.json"
            for file in (state,ledger): file.write_bytes(b"legacy")
            result=run_shadow(path,rows,[],rows,MODEL,{},"observe",now)
            self.assertIsNone(result["warning"])
            count=len(load_evidence(path)["windows"])
            run_shadow(path,rows,[],rows,MODEL,{},"observe",now)
            self.assertEqual(len(load_evidence(path)["windows"]),count)
            self.assertGreater(count,0)
            self.assertEqual(state.read_bytes(),b"legacy")
            self.assertEqual(ledger.read_bytes(),b"legacy")
            path.write_bytes(b"broken")
            self.assertEqual(run_shadow(path,rows,[],rows,MODEL,{},"candidate",now)["warning"],
                             "adaptive_evidence_corrupt")
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
