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
                    grid_state=grid,
                    kws_fault_state=rows.get('fault', {}).get('value'),
                    kws_relay_at=rows.get('relay', {}).get('at'),
                    kws_status_at=rows.get('status', {}).get('at'),
                    kws_fault_at=rows.get('fault', {}).get('at'),
                    kws_phases_v=[rows.get(p, {}).get('value') for p in 'abc'],
                    grid_confirmed=grid == 'on', voltage_qualified=voltage_ok,
                    kws_link_fresh=linked, kws_contactor_on=rows.get('relay', {}).get('value') == 'on',
                    kws_fault=rows.get('fault', {}).get('fault_code', rows.get('fault', {}).get('value')),
                    kws_reset_pending=reason == 'kws_reset_pending',
                    grid_since=self.grid_since, stable_since=self.stable_since,
                    diagnosis_overdue=self.grid_since is not None and
                    now-self.grid_since >= max(120, reset_delay)+300 and reason != 'ready')


class KWSRestorePolicy:
    """One-shot, volatile authorization to restore a previously energized relay.

    Only a witnessed physical outage qualifies. No cached authorization is
    recovered after a process restart. Never clears an active KWS fault.
    All callers must still verify current grid, voltage and thermostat OFF.
    """
    def __init__(self):
        self.previous_grid = None
        self.previous_ready = False
        self.outage_at = None
        self.attempted = False
        self.inflight = False
        self.manual_hold = False
        self.status = 'waiting_observation'

    def cancel(self, reason):
        self.outage_at = None
        self.inflight = False
        self.previous_ready = False
        self.status = reason

    def manual_intent(self, action):
        if action in ('turn_off', 'toggle'):
            self.manual_hold = True
            self.cancel('manual_hold')
        elif action == 'turn_on':
            self.manual_hold = False
            self.cancel('manual_on')

    def observe(self, now, report, enabled):
        grid = report.get('grid_state')
        relay = report.get('kws_relay_state')
        fault = report.get('kws_fault')
        if not enabled:
            self.cancel('disabled')
        elif self.manual_hold:
            self.cancel('manual_hold')
        elif grid == 'unknown':
            self.cancel('grid_unknown')
        elif grid == 'off' and self.previous_grid != 'off':
            self.outage_at = now if self.previous_grid == 'on' and self.previous_ready else None
            self.attempted = self.inflight = False
            self.status = 'waiting_grid' if self.outage_at is not None else 'no_previous_on_proof'
        if self.outage_at is not None:
            # The only tolerated historical trip is a witnessed undervoltage
            # during the blackout. Any other trip cancels this epoch.
            if report.get('kws_fault_state') == 'on' and fault not in (2, '2'):
                self.cancel('other_protection')
            elif grid == 'on' and relay == 'on' and report.get('kws_status') == 'on':
                self.cancel('restored')
            elif grid == 'on' and not self.attempted:
                self.status = 'waiting_safe_recovery'
        self.previous_grid = grid
        self.previous_ready = bool(enabled and not self.manual_hold and grid == 'on' and report.get('ready'))

    def eligible(self, now, report, voltage, delay, *, claimed=False):
        if (self.manual_hold or self.outage_at is None or
                (self.attempted and not (claimed and self.inflight)) or
                report.get('grid_state') != 'on' or
                not report.get('voltage_qualified') or not report.get('kws_link_fresh') or
                report.get('kws_relay_state') != 'off' or report.get('kws_status') != 'off' or
                report.get('kws_fault_state') != 'off' or report.get('kws_fault') not in (0, '0', 'off', 'none')):
            return False
        if not isinstance(delay, (int, float)) or not isfinite(delay) or not 0 <= delay <= 500:
            return False
        grid_at, stable_at = report.get('grid_since'), report.get('stable_since')
        if (not isinstance(grid_at, (int, float)) or not isinstance(stable_at, (int, float)) or
                grid_at <= self.outage_at or now-grid_at < max(120, delay) or now-stable_at < 30):
            return False
        # Fresh post-return OFF and cleared-fault reports are required, not
        # stale values from before a power failure or before a trip.
        for key in ('kws_relay_at', 'kws_status_at', 'kws_fault_at'):
            at = report.get(key)
            if not isinstance(at, (int, float)) or not grid_at <= at <= now+5:
                return False
        low, high = voltage.get('recovery_low_v'), voltage.get('recovery_high_v')
        if not all(isinstance(v, (int, float)) and isfinite(v) for v in (low, high)) or low >= high:
            return False
        for phases in (voltage.get('phases_v'), report.get('kws_phases_v')):
            if (not isinstance(phases, list) or len(phases) != 3 or
                    not all(isinstance(v, (int, float)) and isfinite(v) and low <= v <= high for v in phases)):
                return False
        return True

    def claim(self):
        if self.outage_at is None or self.attempted or self.manual_hold:
            return False
        self.attempted = self.inflight = True
        self.status = 'attempt_claimed'
        return True

    def finish(self, success):
        self.inflight = False
        if self.outage_at is not None:
            self.status = 'command_sent_waiting_echo' if success else 'attempt_failed_no_retry'
