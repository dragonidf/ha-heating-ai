"""Calendar comparisons use issued evidence, never a retrospective model run."""
import copy
import gzip
import json
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from heating_recovery import DecisionJournal, HeatingRecovery


class TemperatureComparisonTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.journal = DecisionJournal(self.temp.name)
        self.end = datetime(2026, 10, 31, 12, tzinfo=timezone.utc).timestamp()
        self.start = self.end - 7 * 86400
        self.actual = []

    def record(self, at, **values):
        return self.journal.append(dict(recorded_at=datetime.fromtimestamp(at, timezone.utc).isoformat(), **values), stamp=at)

    def night(self, at, key):
        issued = at - 12 * 3600
        forecast = dict(plan_id='original-' + key, curve=[dict(ts=at + i * 1800, temperature=21 + i * .01) for i in range(25)])
        self.journal.append(dict(event='full_recompute', recorded_at=datetime.fromtimestamp(issued, timezone.utc).isoformat()),
            dict(inputs=dict(now=datetime.fromtimestamp(issued, timezone.utc).isoformat()), new_plan=forecast), issued)
        self.record(at - 15, power_w=0., power_quality='measured', power_source='balance', cycle_id=key)
        self.record(at, power_w=6000., power_quality='measured', power_source='balance', cycle_id=key)
        self.actual.extend(dict(ts=at + i * 300, room=21.1 + i * .01, quality=True) for i in range(20))
        return forecast

    def write_actual(self):
        (Path(self.temp.name) / 'telemetry.jsonl').write_text(''.join(json.dumps(r) + '\n' for r in self.actual))

    def test_week_combines_original_forecasts_and_actual_without_recomputing(self):
        for day in range(7):
            at=self.start + day * 86400 + 3600
            original=self.night(at, str(day))
            self.journal.append(dict(event='full_recompute', recorded_at=datetime.fromtimestamp(at, timezone.utc).isoformat()),
                dict(inputs=dict(now=datetime.fromtimestamp(at, timezone.utc).isoformat()), new_plan=dict(original, curve=[dict(ts=at, temperature=35.)])), at)
        self.write_actual()
        result=self.journal.temperature_comparison(self.start, self.end)
        self.assertEqual(len(result['cycles']), 7)
        self.assertEqual(len(result['actual']), 140)
        self.assertTrue(all(c['forecast']['curve'][0]['temperature']==21. for c in result['cycles']))
        self.assertTrue(all(c['started_at']-c['forecast']['at']>=12*3600 for c in result['cycles']))
        self.assertEqual(self.journal.temperature_comparison(self.start,self.end),result)

    def test_actual_exists_without_any_advance_forecast_or_heat_cycle(self):
        self.actual=[dict(ts=self.start+300,room=22.4,quality=True),dict(ts=self.start+600,room=22.5,quality=False)]
        self.write_actual()
        result=self.journal.temperature_comparison(self.start,self.end)
        self.assertEqual(result['cycles'],[])
        self.assertEqual([p['temperature'] for p in result['actual']],[22.4,None])

    def test_start_gap_and_late_forecasts_do_not_become_twelve_hour_evidence(self):
        at=self.start+86400;self.night(at,'gap')
        path=next(self.journal.directory.glob('*.jsonl'))
        # The low observation is more than45s before the high observation.
        for p in self.journal.directory.glob('*.jsonl'):
            lines=[json.loads(s) for s in p.read_text().splitlines()]
            for r in lines:
                if r.get('power_w')==0:r['recorded_at']=datetime.fromtimestamp(at-300,timezone.utc).isoformat()
            p.write_text(''.join(json.dumps(r)+'\n' for r in lines))
        result=self.journal.temperature_comparison(self.start,self.end)
        self.assertFalse(result['cycles'][0]['start_known'])
        self.assertIsNone(result['cycles'][0]['forecast'])

    def test_compressed_journal_and_missing_snapshot_preserve_missing_evidence(self):
        self.night(self.start+86400,'compressed')
        for p in self.journal.directory.glob('*.jsonl'):
            p.with_suffix('.jsonl.gz').write_bytes(gzip.compress(p.read_bytes()));p.unlink()
        self.assertIsNotNone(self.journal.temperature_comparison(self.start,self.end)['cycles'][0]['forecast'])
        for p in self.journal.directory.glob('*.json.gz'):p.write_bytes(b'corrupt')
        result=self.journal.temperature_comparison(self.start,self.end)
        self.assertIsNone(result['cycles'][0]['forecast'])
        self.assertTrue(result['warnings'])

    def test_persisted_frozen_forecast_survives_restart_and_is_not_modified(self):
        at=self.start+86400;original=self.night(at,'persisted')
        stored={'persisted':dict(cycle_id='persisted',started_at=at,start_known=True,
            forecast=dict(at=at-43200,plan_id='already-frozen',curve=original['curve']),actual=[])}
        previous=copy.deepcopy(stored)
        result=self.journal.temperature_comparison(self.start,self.end,stored)
        self.assertEqual(result['cycles'][0]['forecast']['plan_id'],'already-frozen')
        self.assertEqual(stored,previous)

    def test_cycle_boundary_during_continuous_heat_is_not_a_new_physical_start(self):
        at=self.start+86400;self.night(at,'night')
        self.record(at+8*3600-15,power_w=6000.,power_quality='measured',power_source='balance',cycle_id='night')
        self.record(at+8*3600,power_w=6000.,power_quality='measured',power_source='balance',cycle_id='next-window')
        result=self.journal.temperature_comparison(self.start,self.end)
        self.assertEqual(len(result['cycles']),1)

    def test_range_event_uses_background_reader_without_touching_planning(self):
        app=HeatingRecovery();app.memory={};app.decision_journal=self.journal
        app.now=lambda:datetime.fromtimestamp(self.end,timezone.utc);app.next_compute=123
        jobs=[];app.submit_to_executor=lambda *args,**kwargs:jobs.append((args,kwargs))
        app.plan_history_requested('heating_plan_history',dict(request_id='week',start=self.start,end=self.end))
        self.assertEqual(len(jobs),1)
        self.assertEqual(app.next_compute,123)
        app.plan_history_requested('heating_plan_history',dict(request_id='bad',start=self.end-32*86400,end=self.end))
        self.assertEqual(len(jobs),1)

    def test_late_issue_is_never_relabelled_as_twelve_hours_early(self):
        at=self.start+86400;self.night(at,'late')
        for p in self.journal.directory.glob('*.jsonl'):
            rows=[json.loads(s) for s in p.read_text().splitlines()]
            for r in rows:
                if r.get('event')=='full_recompute':r['recorded_at']=datetime.fromtimestamp(at-43199,timezone.utc).isoformat()
            p.write_text(''.join(json.dumps(r)+'\n' for r in rows))
        self.assertIsNone(self.journal.temperature_comparison(self.start,self.end)['cycles'][0]['forecast'])

    def test_history_callback_publishes_readonly_result_and_runs_latest_queued_request(self):
        app=HeatingRecovery();app.memory={};app.history_loading=True
        app.history_queued=dict(request_id='next',start=self.start,end=self.end)
        published=[];queued=[]
        app.publish_plan_history=lambda **kw:published.append(kw)
        app.plan_history_requested=lambda *args:queued.append(args)
        app.plan_history_loaded(dict(result=dict(request_id='first',actual=[])))
        self.assertFalse(app.history_loading)
        self.assertEqual(published,[dict(request_id='first')])
        self.assertEqual(queued[0][1]['request_id'],'next')
        self.assertIsNone(app.history_queued)


if __name__=='__main__':unittest.main()
