import copy
import unittest
import heating_recovery as recovery
import test_adapter_modulation as adapter
from datetime import datetime,timedelta
from zoneinfo import ZoneInfo


class EnergyQualityTests(unittest.TestCase):
    def test_execution_report_compares_observed_start_and_duration_with_initial_sessions(self):
        at=datetime(2026,11,1,23,tzinfo=ZoneInfo('Europe/Kyiv'))
        app=recovery.HeatingRecovery();app.memory={};app.power_source='balance'
        settings=dict(start='23:00',end='07:00')
        app.observe_cycle_energy(at,settings,0.,100.,'on',False)
        entry=next(iter(app.memory['cycle_energy'].values()))
        entry['initial_sessions']=[dict(start=at.timestamp(),end=at.timestamp()+3600)]
        app.observe_cycle_energy(at+timedelta(seconds=15),settings,6000.,100.025,'on',True)
        app.observe_cycle_energy(at+timedelta(seconds=30),settings,6000.,100.05,'on',True)
        report=recovery.cycle_quality_summary(entry,at.timestamp()+30)
        self.assertEqual(report['execution']['planned_minutes'],60.)
        self.assertEqual(report['execution']['confirmed_heating_minutes'],.25)
        self.assertEqual(report['execution']['first_confirmed_timing_error_seconds'],15.)

    def test_plan_increases_keep_initial_sessions_and_an_explanation_for_review(self):
        at=datetime(2026,11,1,0,tzinfo=ZoneInfo('Europe/Kyiv'))
        app=recovery.HeatingRecovery();app.memory={};app.power_source='balance';settings=dict(start='23:00',end='07:00')
        before=at-timedelta(hours=2);app.observe_cycle_energy(before,settings,0.,100.,'on',False)
        from heating_model import heating_cycle
        c=heating_cycle(before,settings);segment=dict(start=c['cycle_start'],end=c['cycle_start']+3600)
        first=dict(plan_id='initial',heat_segments=[segment],morning_reserve=dict(c,end_cycle_planned_kwh=6.,deficit_degree_hours=.5))
        app.cycle_budget(before,first,{'weather':1})
        later=dict(plan_id='changed',heat_segments=[segment],morning_reserve=dict(c,end_cycle_planned_kwh=8.,deficit_degree_hours=.2))
        app.cycle_budget(before+timedelta(minutes=5),later,{'weather':2})
        entry=next(iter(app.memory['cycle_energy'].values()))
        self.assertEqual(entry['initial_plan']['kwh'],6.)
        self.assertEqual(entry['initial_sessions'],[segment])
        self.assertEqual(entry['significant_replans'],1)
        self.assertEqual(entry['plan_reviews'][-1]['energy_increase_kwh'],2.)
        self.assertAlmostEqual(entry['plan_reviews'][-1]['projected_deficit_reduction'],.3)
        self.assertEqual(entry['plan_reviews'][-1]['comparison_basis'],'successive_forecasts_not_causal')

    def test_upcoming_night_also_retains_its_immutable_initial_energy_reference(self):
        at=datetime(2026,11,1,18,tzinfo=ZoneInfo('Europe/Kyiv'));captured=[]
        def inject(app):
            start=at.replace(hour=23).timestamp()
            app.memory['cycle_energy']={'night':dict(start=start,end=start+8*3600,initial_plan=dict(kwh=10.),
                complete=True,observed_kwh=0.,automatic_kwh=0.,other_kwh=0.,transition_kwh=0.,unknown_seconds=0.)}
            app.observe_cycle_energy=lambda *a,**k:None
            app.sample=lambda *args:captured.append(args[-1])
        adapter.AdapterModulationTests().fast_tick(at,[],before_tick=inject)
        self.assertIn('cycle_energy_budget',captured[0])
        self.assertEqual(captured[0]['cycle_energy_budget']['remaining_kwh'],10.)

    def test_completed_report_counts_only_observed_comfort_and_breaks_sensor_gaps(self):
        at=datetime(2026,11,1,1,tzinfo=ZoneInfo('Europe/Kyiv'))
        app=recovery.HeatingRecovery();app.memory={};app.power_source='balance'
        settings=dict(start='23:00',end='07:00',target=21.,lower=20.5,upper=21.5)
        app.observe_cycle_energy(at,settings,0.,100.,'on',False,temperature=20.4,temperature_valid=True)
        app.observe_cycle_energy(at+timedelta(seconds=30),settings,0.,100.,'on',False,temperature=20.6,temperature_valid=True)
        entry=next(iter(app.memory['cycle_energy'].values()))
        self.assertAlmostEqual(entry['thermal_quality']['violation_minutes'],.25)
        self.assertEqual(entry['thermal_quality']['known_seconds'],30.)
        app.observe_cycle_energy(at+timedelta(minutes=2),settings,None,100.,'unknown',False,temperature=None,temperature_valid=False)
        self.assertEqual(entry['thermal_quality']['known_seconds'],30.)

    def entry(self):
        return dict(start=100.,end=200.,initial_plan=dict(at=50.,kwh=10.,before_start=True),
            observed_kwh=12.,automatic_kwh=11.,other_kwh=.8,transition_kwh=.2,
            complete=False,unknown_seconds=20.)

    def test_unknown_intervals_do_not_remove_the_known_spend_from_the_budget(self):
        row=self.entry();before=copy.deepcopy(row)
        budget=recovery.cycle_energy_allowance(row)
        self.assertEqual(budget['remaining_kwh'],0.)
        self.assertEqual(budget['known_spent_kwh'],12.)
        self.assertFalse(budget['fact_complete']);self.assertEqual(row,before)

    def test_unconfirmed_counter_delta_does_not_become_exact_energy(self):
        row=self.entry();row['meter_kwh']=15.
        result=recovery.cycle_quality_summary(row,250.)
        self.assertEqual(result['known_energy_kwh'],12.)
        self.assertFalse(result['energy_complete'])
        self.assertIsNone(result['independent_energy_kwh'])
        self.assertEqual(result['energy_error_kwh'],2.)
        self.assertEqual(result['attribution'],'Причина не встановлена')
        self.assertIsNone(result['comfort_violation_minutes'])

    def test_unknown_intervals_below_the_plan_do_not_claim_energy_savings(self):
        row=self.entry();row['observed_kwh']=8.
        result=recovery.cycle_quality_summary(row,250.)
        self.assertIsNone(result['energy_error_kwh'])
        self.assertEqual(result['energy_error_lower_bound_kwh'],-2.)
        self.assertIsNone(result['energy_error_percent'])

    def test_overrun_requests_one_review_without_raising_the_original_plan(self):
        at=datetime(2026,11,1,1,tzinfo=ZoneInfo('Europe/Kyiv'))
        app=recovery.HeatingRecovery();app.memory={};app.power_source='balance';app.next_compute=12345.
        settings=dict(start='23:00',end='07:00')
        app.observe_cycle_energy(at,settings,6000.,100.,'on',True)
        entry=next(iter(app.memory['cycle_energy'].values()));entry['initial_plan']=dict(at=at.timestamp()-3600,kwh=.01)
        app.observe_cycle_energy(at+timedelta(seconds=15),settings,6000.,100.025,'on',True)
        self.assertEqual(app.next_compute,0)
        self.assertEqual(entry['initial_plan']['kwh'],.01)
        self.assertIn('overrun_review',entry)
        app.next_compute=54321.
        app.observe_cycle_energy(at+timedelta(seconds=30),settings,6000.,100.05,'on',True)
        self.assertEqual(app.next_compute,54321.)


if __name__=='__main__':unittest.main()
