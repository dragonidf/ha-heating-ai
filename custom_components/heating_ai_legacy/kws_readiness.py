"""Read-only qualification of the boiler breaker; never toggles its relay."""
from math import isfinite


class KWSReadiness:
    def __init__(self):
        self.grid_since = None
        self.stable_since = None
        self.reset_since = None

    def evaluate(self, now, grid, voltage_ok, rows, reset_delay=120):
        def fresh(row, age=180):
            at = row.get('at')
            return at is not None and -5 <= now-at <= age

        if grid != 'on':
            self.grid_since = self.stable_since = self.reset_since = None
            reason = 'grid_absent' if grid == 'off' else 'grid_unknown'
        else:
            if self.grid_since is None:
                self.grid_since = now
            if not voltage_ok:
                self.stable_since = self.reset_since = None
                reason = 'voltage_unqualified'
            else:
                if self.stable_since is None:
                    self.stable_since = now
                status, relay, fault = (rows.get(k, {}) for k in ('status', 'relay', 'fault'))
                voltages = [rows.get(p, {}) for p in 'abc']
                # Same-device voltage reports establish current local link
                # liveness; an unchanged post-return ON is not a dead relay.
                linked = (all(fresh(r) for r in voltages) and
                          all(r.get('at') is not None and r['at'] <= now+5 and
                              r.get('value') not in (None, 'unknown', 'unavailable')
                              for r in (status, relay, fault, *voltages)))
                code = fault.get('fault_code', status.get('fault_code'))
                if not linked:
                    reason = 'kws_link_unknown'
                elif fault['value'] != 'off' or code not in (None, 0, '0', 'none'):
                    reason = 'kws_protection'
                elif status['value'] == 'standby':
                    reason = 'kws_reset_pending'
                elif status['value'] != 'on' or relay['value'] != 'on':
                    reason = 'kws_relay_off'
                elif not all(isinstance(r['value'], (int, float)) and isfinite(r['value'])
                             and 190 <= r['value'] <= 260 for r in voltages):
                    reason = 'kws_voltage_unqualified'
                elif (self.reset_since is None or now-self.reset_since < max(120, reset_delay)
                      or now-self.grid_since < max(120, reset_delay)
                      or now-self.stable_since < 30
                      or min(status['at'], relay['at']) < self.grid_since):
                    reason = 'kws_reset_pending'
                else:
                    reason = 'ready'
        if reason in ('kws_protection', 'kws_link_unknown', 'kws_relay_off', 'kws_voltage_unqualified'):
            self.reset_since = None
        elif grid == 'on' and voltage_ok and self.reset_since is None:
            self.reset_since = now
        return dict(ready=reason == 'ready', reason=reason,
                    grid_since=self.grid_since, stable_since=self.stable_since,
                    diagnosis_overdue=self.grid_since is not None and
                    now-self.grid_since >= max(120, reset_delay)+300 and reason != 'ready')
