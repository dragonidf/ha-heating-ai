"""Do not actuate equipment or initialize data while HACS replaces files."""
import unittest
from test_adapter_modulation import HeatingClimate


class UpdateGuardTests(unittest.TestCase):
    def app(self,progress):
        app=HeatingClimate();app.cfg={};app.args={}
        app.get_state=lambda *args,**kwargs:dict(attributes=dict(in_progress=progress))
        return app
    def test_busy_initialization_only_registers_one_retry(self):
        app=self.app(True);timers=[]
        app.run_in=lambda callback,delay:timers.append((callback,delay))
        app.initialize()
        self.assertEqual(len(timers),1)
        self.assertEqual(timers[0][1],5)
        self.assertFalse(hasattr(app,'memory'))
    def test_busy_cycle_and_boiler_callback_do_nothing(self):
        for progress in (True,10,50):
            app=self.app(progress)
            app.tick_inner=lambda:self.fail('Cycle ran during a file replacement')
            app.tick()
            app.apply_action('heat',False)  # No thermostat I/O, even for a delayed callback.
    def test_finished_update_resumes_cycles(self):
        app=self.app(False);calls=[];app.memory={}
        app.now=lambda:__import__('datetime').datetime(2026,10,29)
        app.publish=lambda *a,**kw:None
        app.tick_inner=lambda:calls.append(True)
        app.tick();self.assertEqual(calls,[True])
    def test_actual_boiler_marker_uses_kw_and_preserves_missing_measurements(self):
        app=self.app(False);app.stage='off';app.now=lambda:__import__('datetime').datetime(2026,10,16)
        published={};app.publish=lambda entity,state,**attrs:published.update({entity:state})
        for power,expected in ((0,'off'),(1.49,'off'),(1.5,'on'),(9,'on'),(None,None)):
            app.stage_update(power)
            self.assertEqual(published['binary_sensor.heating_boiler_active'],expected)
    def test_unavailable_fireplace_does_not_claim_confirmed_off(self):
        app=self.app(False);app.cfg={'fireplace_heartbeat':[]};app.memory={};app.samples=[]
        app.val=lambda *args:None;app.heartbeat_fresh=lambda *args:False
        published={};app.publish=lambda entity,state,**attrs:published.update({entity:state})
        self.assertEqual(app.fireplace(22),(0,False))
        self.assertIsNone(published['binary_sensor.heating_fireplace_active'])

