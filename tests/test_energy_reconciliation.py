from copy import deepcopy
from datetime import datetime, timezone
import tempfile
from pathlib import Path
import unittest
from unittest.mock import patch
import test_adapter_modulation as adapter
from heating_fallback import reconcile_energy


class EnergyReconciliationTests(unittest.TestCase):
    def setUp(self):
        self.at=datetime(2026,11,2,9,tzinfo=timezone.utc).timestamp()
        self.start=self.at-12*3600
        self.memory=dict(model={'version':6},inlet_energy=dict(origin_kwh=446.,estimated_kwh=1.,
            months={'2026-11':dict(origin_kwh=20.,calculated_kwh=1.)}),
            energy_last={'ts':self.at-15,'total':447.},energy_bins=[],
            cycle_energy={'night':dict(start=self.start,end=self.start+8*3600,observed_kwh=0.,unknown_seconds=8*3600,complete=False,
                initial_plan={'kwh':18.},latest_plan={'kwh':22.},quality_report={'old':True})})
        self.payload=dict(id='recorder-gap-20261102',expected_total=447.,evidence_sha256='a'*64,
            method='cumulative_group_balance',bins=[dict(start=self.start,end=self.start+3600,kwh=5.9),
                dict(start=self.start+3600,end=self.start+7200,kwh=6.1)])

    def apply(self):return reconcile_energy(self.memory,self.payload,self.at,timezone.utc)

    def test_recovery_updates_total_month_24h_bins_and_cycle_separately_from_measured(self):
        restored=self.apply();counter=restored['inlet_energy'];cycle=restored['cycle_energy']['night']
        self.assertEqual(counter['recovered_kwh'],12.)
        self.assertEqual(counter['months']['2026-11']['calculated_kwh'],13.)
        self.assertEqual(sum(b['kwh'] for b in restored['energy_bins']),12.)
        self.assertEqual(restored['energy_last']['total'],459.)
        self.assertEqual(cycle['observed_kwh'],0.)
        self.assertEqual(cycle['calculated_kwh'],12.)
        self.assertEqual(cycle['initial_plan'],{'kwh':18.})
        self.assertEqual(cycle['latest_plan'],{'kwh':22.})
        self.assertFalse(cycle['complete'])
        self.assertNotIn('quality_report',cycle)
        self.assertEqual(self.memory['inlet_energy'].get('recovered_kwh',0),0)

    def test_idempotent_after_retry_and_reload(self):
        restored=self.apply()
        self.assertEqual(reconcile_energy(restored,self.payload,self.at,timezone.utc),restored)

    def test_reject_stale_baseline_overlap_future_excess_and_missing_evidence(self):
        for change in ({'expected_total':400.},{'evidence_sha256':'bad'},
                       {'bins':[dict(start=self.at,end=self.at+600,kwh=1.)]},
                       {'bins':[dict(start=self.start,end=self.start+15,kwh=1.)]}):
            with self.subTest(change=change),self.assertRaises(ValueError):
                reconcile_energy(self.memory,{**self.payload,**change},self.at,timezone.utc)
        restored=self.apply()
        self.payload.update(id='overlap',expected_total=459.)
        with self.assertRaises(ValueError):reconcile_energy(restored,self.payload,self.at,timezone.utc)

    def test_does_not_double_count_existing_positive_observations(self):
        self.memory['energy_bins']=[dict(start=self.start,end=self.start+15,kwh=.01,quality=True)]
        with self.assertRaises(ValueError):self.apply()

    def test_event_rolls_back_in_memory_and_reports_failure_when_save_fails(self):
        app=adapter.HeatingClimate();app.memory=deepcopy(self.memory);app.cfg={'power_accounting_method':'inlet'}
        app.now=lambda:datetime.fromtimestamp(self.at,timezone.utc);app.update_in_progress=lambda:False
        app.published={};app.publish=lambda entity,state,**attrs:app.published.update({entity:dict(state=state,**attrs)})
        app.save=lambda:(_ for _ in ()).throw(OSError('disk full'))
        with tempfile.TemporaryDirectory() as directory:
            app.directory=Path(directory);app.statefile=app.directory/'state.json';app.statefile.write_text('{}')
            with patch('heating_climate.time.time',return_value=self.at):app.energy_recovery_requested('heating_energy_recovery',self.payload)
        self.assertEqual(app.memory,self.memory)
        self.assertFalse(app.published['sensor.heating_energy_recovery']['success'])
        self.assertIn('disk full',app.published['sensor.heating_energy_recovery']['error'])


if __name__=='__main__':unittest.main()
