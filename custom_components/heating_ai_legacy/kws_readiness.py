"""Read-only qualification of the boiler breaker; never toggles its relay."""
from math import isfinite


class KWSReadiness:
    def __init__(self):
        self.grid_since = None
        self.stable_since = None
        self.reset_since = None
        self.observed_grid_loss = False
        self.last_qualified_link_at = None
        self.link_grace_seconds = 300

    def evaluate(self, now, grid, voltage_ok, rows, reset_delay=120, grid_epoch=None):
        def fresh(row, age=180):
            at = row.get('at')
            return at is not None and -5 <= now-at <= age

        linked = False
        continue_allowed = False
        if grid != 'on':
            self.observed_grid_loss = True
            self.grid_since = self.stable_since = self.reset_since = None
            self.last_qualified_link_at = None
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
                self.last_qualified_link_at = None
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
                # An explicit negative observation has priority over missing data.
                # Losing transport must never erase a known trip or relay OFF.
                unknown = (None, 'unknown', 'unavailable')
                fault_value, status_value, relay_value = (r.get('value') for r in (fault, status, relay))
                explicit_fault = (fault_value not in ('off', *unknown) or
                                  code not in (None, 0, '0', 'none', 'unknown', 'unavailable'))
                explicit_off = relay_value not in ('on', *unknown) or status_value not in ('on', 'standby', *unknown)
                bad_voltage = any(fresh(r) and isinstance(r.get('value'), (int, float)) and
                                  (not isfinite(r['value']) or not 190 <= r['value'] <= 260)
                                  for r in voltages)
                if explicit_fault:
                    reason = 'kws_protection'
                elif status_value == 'standby':
                    reason = 'kws_reset_pending'
                    self.last_qualified_link_at = None
                    self.reset_since = None
                elif explicit_off:
                    reason = 'kws_relay_off'
                elif bad_voltage:
                    reason = 'kws_voltage_unqualified'
                elif not linked:
                    reason = 'kws_link_unknown'
                    # This only permits the caller to consider retaining an
                    # existing command; never authorizes a new HEAT or reclose.
                    continue_allowed = (self.last_qualified_link_at is not None and
                        0 <= now-self.last_qualified_link_at <= self.link_grace_seconds)
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
                    self.last_qualified_link_at = min(r['at'] for r in voltages)
        if reason in ('kws_protection', 'kws_relay_off', 'kws_voltage_unqualified') or (
                reason == 'kws_link_unknown' and not continue_allowed):
            self.reset_since = None
            self.last_qualified_link_at = None
        elif grid == 'on' and voltage_ok and self.reset_since is None:
            self.reset_since = now
        labels = dict(grid_absent='Електромережі немає', grid_unknown='Стан електромережі не підтверджено',
            voltage_unqualified='Напруга не підтверджена або поза безпечними межами',
            kws_link_unknown='KWS: немає актуального зв’язку', kws_protection='KWS: активний захист',
            kws_reset_pending='KWS: очікування підтвердженого відновлення',
            kws_relay_off='KWS: реле вимкнене', kws_voltage_unqualified='KWS: напруга не в нормі', ready='Обладнання готове')
        return dict(ready=reason == 'ready', reason=reason, reason_text=labels[reason],
                    continue_allowed=continue_allowed, last_qualified_link_at=self.last_qualified_link_at,
                    kws_relay_state=rows.get('relay', {}).get('value'),
                    kws_status=rows.get('status', {}).get('value'),
                    grid_confirmed=grid == 'on', voltage_qualified=voltage_ok,
                    kws_link_fresh=linked, kws_contactor_on=rows.get('relay', {}).get('value') == 'on',
                    kws_fault=rows.get('fault', {}).get('fault_code', rows.get('fault', {}).get('value')),
                    kws_reset_pending=reason == 'kws_reset_pending',
                    grid_since=self.grid_since, stable_since=self.stable_since,
                    diagnosis_overdue=self.grid_since is not None and
                    now-self.grid_since >= max(120, reset_delay)+300 and reason != 'ready')
