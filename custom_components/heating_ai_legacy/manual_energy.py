"""Single-session raw KWS energy accounting; never sends equipment commands."""
from decimal import Decimal
from math import isfinite

# Measured reporting cadence is approximately 15 min; leave 5 min grace.
MAX_RUNNING_REPORT_AGE = 1200
MAX_IDLE_BASELINE_AGE = 1800
MAX_ACTIVE_BASELINE_AGE = 30
MAX_TARGET_KWH = 40


def valid_target(target):
    return type(target) is int and 1 <= target <= MAX_TARGET_KWH


def watchdog_minutes(target, configured_limit):
    """Bound manual sessions independently: 3 kW nominal + 30 min reporting grace."""
    if not valid_target(target):
        raise ValueError('invalid_target')
    if not _finite(configured_limit) or not 30 <= configured_limit <= 480:
        raise ValueError('invalid_time_limit')
    return max(configured_limit, target*20+30)


def _counter(value):
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not isfinite(value) or value < 0:
        return None
    return Decimal(str(value))


def _finite(value):
    return not isinstance(value, bool) and isinstance(value, (int, float)) and isfinite(value)


def start(target, value, at, now, limit_minutes, idle_since=None):
    """Create one request only with a qualified baseline, before any HEAT."""
    if not valid_target(target):
        raise ValueError('invalid_target')
    if _counter(value) is None or not _finite(at) or not _finite(now):
        raise ValueError('counter_invalid')
    if not _finite(limit_minutes) or limit_minutes <= 0:
        raise ValueError('invalid_time_limit')
    age = now-at
    continuous_idle = _finite(idle_since) and idle_since <= at <= now
    if not -5 <= age <= (MAX_IDLE_BASELINE_AGE if continuous_idle else MAX_ACTIVE_BASELINE_AGE):
        raise ValueError('counter_baseline_stale')
    return dict(status='active', target_kwh=target, baseline_kwh=value,
                last_kwh=value, last_report_at=at, started_at=now,
                deadline=now+limit_minutes*60, consumed_kwh=0., reason=None)


def finish(session, reason, now):
    if session.get('status') == 'active':
        session.update(status='completed' if reason == 'target_reached' else 'cancelled',
                       reason=reason, ended_at=now)
    return session


def advance(session, value, at, now):
    """Count only independently measured raw KWS deltas in one epoch."""
    if session.get('status') != 'active':
        return session
    previous = session['last_report_at']
    if not _finite(now) or not _finite(at) or _counter(value) is None or at > now+5:
        return finish(session, 'counter_invalid', now)
    if now >= session['deadline']:
        return finish(session, 'time_limit', now)
    if at < previous or at == previous and value != session['last_kwh']:
        return finish(session, 'counter_order', now)
    anchor = max(previous, session['started_at'])
    if now-max(at, session['started_at']) > MAX_RUNNING_REPORT_AGE or at-anchor > MAX_RUNNING_REPORT_AGE:
        return finish(session, 'counter_stale', now)
    if at == previous:
        return session
    delta = _counter(value)-_counter(session['last_kwh'])
    if delta < 0:
        return finish(session, 'counter_reset', now)
    if delta > Decimal('12')*Decimal(str(max(0, at-anchor)))/Decimal(3600)+Decimal('.05'):
        return finish(session, 'counter_jump', now)
    consumed = _counter(value)-_counter(session['baseline_kwh'])
    session.update(last_kwh=value, last_report_at=at, consumed_kwh=float(consumed))
    if consumed >= session['target_kwh']:
        finish(session, 'target_reached', now)
    return session


def arbitrate(planned, manual, ending):
    """Return effective demand and immediate release of manual-only HEAT."""
    demand = bool(planned or manual)
    return demand, bool(ending and not demand)
