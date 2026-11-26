"""Offline matched replay must not invent actual measurements or coverage."""
import unittest
from datetime import datetime, timezone
from tools.replay_forecast_period import matched_reports, pair_errors, summarize_pairs


class PrivateForecastReplayTests(unittest.TestCase):
    def test_exact_issue_report_and_identity_match_only(self):
        issue=1795000000.
        snap=dict(inputs=dict(model=dict(version=6),settings=dict(equipment_version=2,
            modulation_profile=dict(physical_mode="fixed_6"))),new_plan=dict(issued_at=issue))
        report=dict(issued_at=issue,horizon=6,actual_at=issue+21600,actual_temperature=21.,
                    actual_horizon_hours=6.,model_version=6,physical_mode="fixed_6",
                    equipment_version=2,actual_temperature_basis="sensor_report")
        rows=[report,dict(report),dict(report,issued_at=issue+1),
              dict(report,model_version=7),dict(report,equipment_version=3),
              dict(report,actual_temperature_basis="interpolated_reports"),
              dict(report,actual_at=issue+25200,actual_horizon_hours=7.),
              dict(report,actual_at=issue-1),dict(report,actual_temperature=None)]
        result=matched_reports(snap,rows)
        self.assertEqual(len(result),1)
        self.assertEqual(result[0]["actual_temperature"],21.)
        self.assertEqual(result[0]["equipment_provenance"],"matched")
        legacy=dict(report);legacy.pop("equipment_version")
        self.assertEqual(matched_reports(snap,[legacy])[0]["equipment_provenance"],"unknown")

    def test_missing_prediction_is_not_zero_and_metrics_use_same_pairs(self):
        report=dict(horizon=6,actual_at=1795021600.,actual_temperature=21.,
            model_version=6,physical_mode="fixed_6",equipment_version=2,equipment_provenance="matched")
        a=pair_errors([report],[21.5],[21.2])
        b=pair_errors([report],[21.5],[None])
        self.assertEqual(len(a),1);self.assertEqual(b,[])
        result=summarize_pairs(a,timezone.utc)
        self.assertEqual(len(result),1)
        self.assertEqual(result[0]["count"],1)
        self.assertAlmostEqual(result[0]["baseline_mae"],.5)
        self.assertAlmostEqual(result[0]["candidate_mae"],.2)
        self.assertEqual(result[0]["days"],1)
        self.assertEqual(summarize_pairs([],timezone.utc),[])

    def test_archive_read_is_lossless_for_externalized_and_compressed_records(self):
        import gzip
        import json
        from pathlib import Path
        from tempfile import TemporaryDirectory
        from heating_recovery import DecisionJournal
        from tools.replay_forecast_period import snapshots
        issue=1795000000.
        day=datetime.fromtimestamp(issue,timezone.utc).date().isoformat()
        snap=dict(inputs=dict(model=dict(version=6)),new_plan=dict(issued_at=issue))
        with TemporaryDirectory() as folder:
            journal=DecisionJournal(folder,max_bytes=1024*1024)
            record=journal.append(dict(event="full_recompute",details="x"*9000),
                                  snapshot=snap,stamp=issue+5)
            self.assertTrue(record["external_attributes"])
            path=journal.directory/(day+".jsonl")
            raw=path.read_bytes()
            compressed=path.with_suffix(".jsonl.gz")
            compressed.write_bytes(gzip.compress(raw))
            path.unlink()
            # Second file for the same UTC date: both are legitimate input
            # after interrupted rotation, duplicates must not count twice.
            second=dict(snap,new_plan=dict(issued_at=issue+3600))
            journal.append(dict(event="full_recompute"),snapshot=second,stamp=issue+3605)
            before={p.name:p.read_bytes() for p in journal.directory.iterdir()}
            found=list(snapshots(Path(folder),issue,issue+86400,{issue:[],issue+3600:[]}))
            self.assertEqual([s["new_plan"]["issued_at"] for s,e in found if s],
                             [issue,issue+3600])
            self.assertEqual({p.name:p.read_bytes() for p in journal.directory.iterdir()},before)

    def test_original_forecast_parity_and_independent_metrics(self):
        from tools.replay_forecast_period import original_parity, summarize_pairs
        rows=[dict(forecast_temperature=21.5),dict(forecast_temperature=21.25)]
        self.assertEqual(original_parity(rows,[21.5,21.25]),[True,True])
        self.assertEqual(original_parity(rows,[21.51,None]),[False,False])
        self.assertEqual(original_parity([{}],[21.5]),[False])
        a=dict(horizon=6,issued_at=1000.,actual_at=22600.,model_version=6,
               baseline_error=.5,candidate_error=.2)
        b=dict(a,issued_at=2000.,actual_at=23600.,baseline_error=1.,candidate_error=.3)
        c=dict(a,issued_at=22600.,actual_at=44200.,baseline_error=-.5,candidate_error=-.4)
        group=summarize_pairs([a,b,c],timezone.utc)[0]
        self.assertEqual(group["count"],3)
        self.assertEqual(group["independent_count"],2)
        self.assertAlmostEqual(group["baseline_independent_mae"],.5)
        self.assertAlmostEqual(group["candidate_independent_mae"],.3)
