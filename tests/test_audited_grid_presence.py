"""Input-meter evidence and local-load meter health are separate contracts."""
import unittest
from test_adapter_modulation import HeatingClimate


class AuditedGridPresenceTests(unittest.TestCase):
    def app(self):
        app=HeatingClimate();app.cfg=dict(power_measurement_policy='audited',
            inverter_heartbeat=['inverter_clock'],input_heartbeat=['a','b','c'],
            input_voltages=['a','b','c'],phase_heartbeats=[['local1'],['local2'],['local3']])
        app.values=dict(connection='on',grid='on',grid_voltage=230.,a=230.,b=230.,c=230.)
        app.raw=lambda key:app.values.get(key)
        app.val=lambda key,default=None:app.values.get(key,default)
        app.get_state=lambda entity:app.values.get(entity)
        app.stale={'local1','local2','local3'}
        app.fresh=lambda entity,seconds:entity not in app.stale
        app.heartbeat_fresh=lambda entities,seconds:any(app.fresh(e,seconds) for e in entities)
        return app

    def test_fresh_three_phase_input_is_on_despite_missing_local_load_meters(self):
        app=self.app();self.assertEqual(app.grid_state(),'on')

    def test_each_input_must_be_present_plausible_and_fresh(self):
        for phase in ('a','b','c'):
            for state in (None,0.,120.,'unavailable'):
                with self.subTest(phase=phase,state=state):
                    app=self.app();app.values[phase]=state;self.assertEqual(app.grid_state(),'unknown')
            app=self.app();app.stale={phase};self.assertEqual(app.grid_state(),'unknown')

    def test_missing_phase_binding_is_not_all_clear(self):
        app=self.app();app.stale.clear();app.cfg['input_voltages']=['a','b'];self.assertEqual(app.grid_state(),'unknown')

    def test_actual_grid_loss_and_inverter_guards_remain_authoritative(self):
        for connection,grid,voltage,stale,expected in [('on','off',0.,False,'off'),
                ('on','unknown',230.,False,'unknown'),('off','on',230.,False,'unknown'),
                ('on','on',230.,True,'unknown')]:
            app=self.app();app.values.update(connection=connection,grid=grid,grid_voltage=voltage)
            if stale:app.stale.add('inverter_clock')
            self.assertEqual(app.grid_state(),expected)

    def test_legacy_policy_preserves_its_previous_local_heartbeat_rule(self):
        app=self.app();app.cfg.pop('power_measurement_policy');self.assertEqual(app.grid_state(),'unknown')


if __name__=='__main__':unittest.main()
