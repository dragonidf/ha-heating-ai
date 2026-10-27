"""Conservative, labelled power estimate used only during primary meter faults."""
from heating_model import number

def estimated_power(source):
    # A confirmed loss of boiler supply proves zero; thermostat inference does not.
    return str(source or '').startswith('fallback') and source!='fallback_grid_off'

def fallback_power(primary, failed, mode, target, current, phases, input_fresh,
                   manual_kw=None, grid_off=False):
    if not failed and number(primary) is not None:
        return primary, 'balance', False
    if grid_off:
        return 0., 'fallback_grid_off', False
    if mode == 'off':
        return 0., 'fallback_thermostat_off', False
    values = [number(v) for v in phases]
    ready = input_fresh and len(values) == 3 and all(v is not None for v in values)
    count = sum(v > 3000 for v in values) if ready else None
    if mode == 'auto':
        return (count * 3000., 'fallback_phases', bool(count)) if ready else (None, 'unavailable', None)
    if mode == 'heat':
        target, current = number(target), number(current)
        if target is None or current is None:
            return None, 'unavailable', None
        demand = target - current >= 1.
        if not demand:
            return 0., 'fallback_thermostat', False
        if count:
            return count * 3000., 'fallback_manual_phases', True
        # A thermostat demand alone does not identify the number of enabled TENs.
        # Retain the last independently measured stage; never invent a rating.
        kw = number(manual_kw)
        return (kw * 1000 if kw in (3, 6, 9) else None), 'fallback_manual_request', True
    return None, 'unavailable', None

def recovered_energy(intervals, start, end):
    return sum(max(0., min(end, r['end'])-max(start, r['start'])) * r['power_w']/3600000
               for r in intervals)

def recovered_coverage(intervals, start, end):
    return sum(max(0., min(end, r['end'])-max(start, r['start'])) for r in intervals)

def repair_samples(rows, recovery):
    intervals = recovery['intervals']
    changed = 0
    for i, row in enumerate(rows):
        if row.get('recovery_id') == recovery['id'] or row['ts'] < recovery['start']:
            continue
        offset = recovered_energy(intervals, recovery['start'], row['ts'])
        if number(row.get('energy')) is not None:
            row['energy'] += offset
        row['recovery_id'] = recovery['id']
        hit = next((r for r in intervals if r['start'] <= row['ts'] < r['end']), None)
        if hit:
            row['power'] = hit['power_w']/1000
            row['power_source'] = 'fallback_phases_recovered'
            row['power_estimated'] = True
            row['quality'] = (row.get('source') == 'Вітальня' and
                              all(number(row.get(k)) is not None for k in ('room','return','outside')))
            changed += 1
        if i:
            previous = rows[i-1]
            seconds = row['ts'] - previous['ts']
            coverage = recovered_coverage(intervals, previous['ts'], row['ts'])
            if seconds > 0 and coverage >= seconds-.01:
                row['interval_power'] = recovered_energy(intervals, previous['ts'], row['ts'])*3600/seconds
                row['power_estimated'] = True
            elif coverage > 0:
                row.pop('interval_power', None)
                row['quality'] = False
    return changed

