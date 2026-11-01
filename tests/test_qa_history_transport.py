import copy
import json
import unittest
import heating_recovery as recovery
import tempfile
import heating_ui


class HistoryTransportTests(unittest.TestCase):
    def test_old_open_panel_gets_a_bounded_refresh_explanation_instead_of_silence(self):
        app=recovery.HeatingRecovery();app.memory={};app.history_protocol=1
        app.history_comparison=dict(start=1,end=2,actual=[dict(ts=i,temperature=21.) for i in range(9000)],cycles=[])
        published=[];app.publish=lambda entity,state,**attrs:published.append(attrs)
        app.publish_plan_history();result=published[-1]['comparison']
        self.assertEqual(result['actual'],[]);self.assertEqual(result['cycles'],[])
        self.assertTrue(result['requires_panel_reload']);self.assertIn('Оновіть панель',result['error'])
        self.assertLess(len(json.dumps(published[-1]).encode()),8192)

    def test_cycle_reviews_are_paginated_separately_from_the_cycle_totals(self):
        cycle=dict(cycle_id='night',start=100,end=200,observed_kwh=12.,plan_reviews=[dict(at=i,reason='Оновлення '*100) for i in range(32)])
        items=recovery.comparison_items(dict(energy_cycles=[cycle]))
        self.assertEqual(len([p for p in items if p['kind']=='review']),32)
        self.assertNotIn('plan_reviews',next(p['value'] for p in items if p['kind']=='energy'))

    def test_public_forecast_removes_duplicate_intervals_without_changing_private_inputs(self):
        segments=[dict(start=i*300,end=(i+1)*300,power_kw=6.,kwh=.5,conditional=False) for i in range(48)]
        forecast=dict(curve=[dict(ts=(i+1)*1800,temperature=21.,lower=20.5,upper=22.,heat_segments=segments[i:i+6])
            for i in range(48)],heat_segments=segments,morning_reserve=dict(cycle_id='original',planned_minutes=240))
        before=copy.deepcopy(forecast);public=heating_ui.forecast_for_panel(forecast)
        self.assertEqual(forecast,before)
        self.assertEqual(len(public['curve']),48)
        self.assertNotIn('heat_segments',public['curve'][0])
        self.assertEqual(len(public['heat_segments']),1)
        self.assertEqual(public['heat_segments'][0]['kwh'],24.)
        self.assertEqual(public['heat_segments'][0]['power_kw'],6.)
        self.assertLess(len(json.dumps(public).encode()),8192)

    def test_archive_pressure_is_visible_before_budget_failure_without_deleting_history(self):
        with tempfile.TemporaryDirectory() as directory:
            journal=recovery.DecisionJournal(directory,max_bytes=10000)
            record=journal.append(dict(action='off'),dict(original='evidence'))
            path=journal.directory/(record['snapshot_id']+'.json.gz');before=path.read_bytes()
            journal.used_bytes=9500
            health=journal.health()
            self.assertTrue(health['pressure']);self.assertGreater(health['free_bytes'],0)
            self.assertEqual(path.read_bytes(),before)

    def test_pages_are_bounded_repeatable_and_preserve_zero_and_missing_points(self):
        result=dict(start=100,end=10000,actual=[dict(ts=i,temperature=None if i%31==0 else 0. if i%17==0 else 21.) for i in range(9000)],
            cycles=[dict(cycle_id='night',started_at=100,forecast=dict(at=-43100,lead_hours=12,
                plan_id='original',curve=[dict(ts=100+i*30,temperature=21.) for i in range(300)]))],markers=[])
        original=copy.deepcopy(result);items=recovery.comparison_items(result);offset=0;restored=[]
        while offset is not None:
            page=recovery.comparison_page(items,offset,'request','result',100,10000)
            self.assertLessEqual(len(json.dumps(page).encode()),8192)
            self.assertEqual(page,recovery.comparison_page(items,offset,'request','result',100,10000))
            restored.extend(page['items']);offset=page['next_offset']
        actual=[row['value'] for row in restored if row['kind']=='actual']
        self.assertEqual(actual,result['actual']);self.assertEqual(result,original)
        self.assertEqual(len([row for row in restored if row['kind']=='forecast']),300)

    def test_bad_cursors_do_not_read_or_mutate_other_files(self):
        for value in (-1,True,1.5,200):
            with self.subTest(value=value),self.assertRaises(ValueError):
                recovery.comparison_page([],value,'request','result',100,200)

    def test_large_item_reports_a_gap_instead_of_overflowing_recorder(self):
        item=dict(kind='energy',value=dict(reason='Деталі '*10000))
        page=recovery.comparison_page([item],0,'request','result',100,200)
        self.assertLessEqual(len(json.dumps(page).encode()),8192)
        self.assertEqual(page['items'][0]['kind'],'error')
        self.assertEqual(page['next_offset'],None)


if __name__=='__main__':unittest.main()
