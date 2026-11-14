"""Read-only qualification of the boiler breaker; never toggles its relay."""
from math import isfinite


class KWSReadiness:
    def __init__(self):
        self.grid_since = None
        self.stable_since = None
        self.reset_since = None
        self.observed_grid_loss = False

    def evaluate(self, now, grid, voltage_ok, rows, reset_delay=120, grid_epoch=None):
        def fresh(row, age=180):
            at = row.get('at')
            return at is not None and -5 <= now-at <= age

        linked = False
        if grid != 'on':
            self.observed_grid_loss = True
            self.grid_since = self.stable_since = self.reset_since = None
            reason = 'grid_absent' if grid == 'off' else 'grid_unknown'
        else:
            if self.grid_since is None:
                # On reload, use HA's actual grid-return transition. A relay
                # confirmed after that return need not change again at boot.
                known_epoch = (not self.observed_grid_loss and isinstance(grid_epoch, (int, float))
                               and isfinite(grid_epoch) and 0 < grid_epoch <= now)
                self.grid_since = grid_epoch if known_epoch else now
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
        labels = dict(grid_absent='Електромережі немає', grid_unknown='Стан електромережі не підтверджено',
            voltage_unqualified='Напруга не підтверджена або поза безпечними межами',
            kws_link_unknown='KWS: немає актуального зв’язку', kws_protection='KWS: активний захист',
            kws_reset_pending='KWS: очікування підтвердженого відновлення',
            kws_relay_off='KWS: реле вимкнене', kws_voltage_unqualified='KWS: напруга не в нормі', ready='Обладнання готове')
        return dict(ready=reason == 'ready', reason=reason, reason_text=labels[reason],
                    grid_confirmed=grid == 'on', voltage_qualified=voltage_ok,
                    kws_link_fresh=linked, kws_contactor_on=rows.get('relay', {}).get('value') == 'on',
                    kws_fault=rows.get('fault', {}).get('fault_code', rows.get('fault', {}).get('value')),
                    kws_reset_pending=reason == 'kws_reset_pending',
                    grid_since=self.grid_since, stable_since=self.stable_since,
                    diagnosis_overdue=self.grid_since is not None and
                    now-self.grid_since >= max(120, reset_delay)+300 and reason != 'ready')
