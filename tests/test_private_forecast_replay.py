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
