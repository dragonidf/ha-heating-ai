"""Svitlo availability: published schedule, confirmed no restrictions, or unknown.

Arrays belong to the LOCAL date of `updated`, never the date at read time.
No-schedule requires fresh positive evidence; actual grid guards stay authoritative.
"""
from datetime import datetime, timedelta, time

EMERGENCY_WARNING = "Аварійні відключення: графік не гарантує світло"


def availability(data, start, finish):
    """Internal rolling coverage only; never writes a synthetic HA schedule."""
    if data.get('valid') and data.get('mode') == 'normal':
        return dict(data, slots=[dict(start=start,end=finish,off=False)],
                    outages=[],known_until=finish)
    return data


def schedule_covered(data, start, end):
    if not data.get('valid'):
        return False
    if data.get('mode') in ('normal','scheduled'):
        return covered(start,end,data.get('slots',[]))
    # Old references/fixtures without a mode retain their former semantics.
    return data.get('known_until') is not None and data['known_until'] >= end


def signature(data):
    # A rolling virtual slot must not trigger optimisation every controller tick.
    slots=data.get('slots',[]) if data.get('mode') == 'scheduled' else []
    return (data.get('mode'),data.get('valid'),data.get('updated'),data.get('emergency'),
            tuple((s['start'],s['end'],s['off']) for s in slots))


def schedule(record, now, max_age=3*3600, emergency=None, horizon_seconds=86400):
    state=record.get('state') if isinstance(record,dict) else None
    emergency_on=emergency == 'on' or state == 'Emergency'
    result=dict(mode='unknown',valid=False,slots=[],outages=[],updated=None,known_until=None,
                source_available=state not in (None,'unknown','unavailable'),source_fresh=False,
                emergency=emergency_on,emergency_known=emergency in ('on','off'))
    def unknown(reason):
        return dict(result,reason=reason,warning=EMERGENCY_WARNING if emergency_on else reason)
    if not result['source_available']:
        return unknown('Графік недоступний')
    attrs=record.get('attributes')
    if not isinstance(attrs,dict):
        return unknown('Некоректні дані джерела Svitlo')
    if str(attrs.get('queue')) != '3.1' or attrs.get('region') != 'kiivska-oblast':
        return unknown('Інша черга або область')
    try:
        updated=datetime.fromisoformat(attrs['updated'].replace('Z','+00:00'))
        if updated.tzinfo is None:
            raise ValueError('Timestamp must include timezone')
        result['updated']=updated.isoformat()
        if not -300 <= now.timestamp()-updated.timestamp() <= max_age:
            return unknown('Графік застарів')
    except (KeyError,ValueError,TypeError,AttributeError):
        return unknown('Не визначено дату графіка')
    result['source_fresh']=True
    status=attrs.get('now_status')
    # Exact values from chaichuk/svitlo_live v2.9.3. No data is overloaded:
    # only the fresh explicit nosched payload can represent no restrictions.
    if (state not in ('on','off','Grid ON','Grid OFF','Emergency','No data') or
            state == 'No data' and status != 'nosched' or
            state == 'Grid ON' and status != 'on' or
            state == 'Grid OFF' and status != 'off'):
        return unknown('Стан джерела Svitlo не підтверджено')
    anchor=updated.astimezone(now.tzinfo).date()
    slots=[]
    for day,key in enumerate(('today_48half','tomorrow_48half')):
        values=attrs.get(key)
        if values is None or values == []:
            continue
        if not isinstance(values,list) or len(values) != 48 or any(v not in ('on','off') for v in values):
            return unknown('Некоректні інтервали графіка')
        start=datetime.combine(anchor+timedelta(days=day),time(),now.tzinfo)
        midnight_end=datetime.combine(anchor+timedelta(days=day+1),time(),now.tzinfo)
        if midnight_end.timestamp()-start.timestamp() != 86400:
            return unknown('Неоднозначні інтервали переходу часу')
        for index,value in enumerate(values):
            a=start+timedelta(minutes=30*index);b=a+timedelta(minutes=30)
            slots.append(dict(start=a.timestamp(),end=b.timestamp(),off=value == 'off'))
    if slots:
        if not any(s['end'] > now.timestamp() for s in slots):
            return unknown('Немає придатних майбутніх інтервалів')
        outages=[]
        for slot in slots:
            if not slot['off']:
                continue
            if outages and outages[-1]['end'] == slot['start']:
                outages[-1]['end']=slot['end']
            else:
                outages.append(dict(start=slot['start'],end=slot['end']))
        return dict(result,mode='scheduled',valid=True,reason='Графік 3.1 враховано',
                    slots=slots,outages=outages,known_until=max(s['end'] for s in slots),
                    warning=EMERGENCY_WARNING if emergency_on else None)
    if emergency_on:
        return unknown('Аварійні відключення без придатного графіка')
    # Missing optional advisory: only a real Grid ON source confirms non-emergency.
    no_emergency=emergency == 'off' or emergency is None and state == 'Grid ON'
    positive=status == 'nosched' or state == 'Grid ON' and status == 'on'
    if not no_emergency or not positive:
        return unknown('Відсутність обмежень не підтверджена')
    result.update(mode='normal',valid=True,reason='Відключень не очікується',warning=None,
                  emergency=False,emergency_known=True)
    return availability(result,now.timestamp(),now.timestamp()+horizon_seconds)


def blocked(start, end, outages):
    return any(start < o["end"] and end > o["start"] for o in outages)


def covered(start, end, slots):
    # Coverage must be complete; a gap or malformed day is not an all-clear.
    cursor = start
    for slot in slots:
        if slot["end"] <= cursor:
            continue
        if slot["start"] > cursor:
            return False
        cursor = max(cursor, slot["end"])
        if cursor >= end:
            return True
    return False

