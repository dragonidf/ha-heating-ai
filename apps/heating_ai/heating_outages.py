"""Validate Svitlo 3.1 half-hour data; timestamps are epoch seconds.

The arrays belong to the LOCAL date of `updated`, not the date at read time.
Missing/stale data is unknown, never an all-clear. Emergency is advisory only.
"""
from datetime import datetime, timedelta, time


def schedule(record, now, max_age=3*3600):
    empty = {"valid": False, "reason": "Графік недоступний", "slots": [],
             "outages": [], "updated": None, "known_until": None}
    if not record or record.get("state") in (None, "unknown", "unavailable"):
        return empty
    attrs = record.get("attributes") or {}
    if str(attrs.get("queue")) != "3.1" or attrs.get("region") != "kiivska-oblast":
        return dict(empty, reason="Інша черга або область")
    try:
        updated = datetime.fromisoformat(attrs["updated"].replace("Z", "+00:00"))
        if updated.tzinfo is None:
            raise ValueError("Timestamp must include timezone")
        age = now.timestamp()-updated.timestamp()
        if not -300 <= age <= max_age:
            return dict(empty, reason="Графік застарів", updated=updated.isoformat())
    except (KeyError, ValueError, TypeError, AttributeError):
        return dict(empty, reason="Не визначено дату графіка")
    anchor = updated.astimezone(now.tzinfo).date()
    slots = []
    for day, key in enumerate(("today_48half", "tomorrow_48half")):
        values = attrs.get(key)
        if not isinstance(values, list) or len(values) != 48 or any(v not in ("on", "off") for v in values):
            continue
        start = datetime.combine(anchor+timedelta(days=day), time(), now.tzinfo)
        midnight_end = datetime.combine(anchor+timedelta(days=day+1), time(), now.tzinfo)
        if midnight_end.timestamp()-start.timestamp() != 86400:
            # A 48-slot local-day format cannot identify the repeated DST hour.
            continue
        for index, value in enumerate(values):
            a = start+timedelta(minutes=30*index)
            b = start+timedelta(minutes=30*(index+1))
            slots.append({"start": a.timestamp(), "end": b.timestamp(), "off": value == "off"})
    if not slots or not any(s["end"] > now.timestamp() for s in slots):
        return dict(empty, reason="Немає придатних майбутніх інтервалів")
    outages = []
    for slot in slots:
        if not slot["off"]:
            continue
        if outages and outages[-1]["end"] == slot["start"]:
            outages[-1]["end"] = slot["end"]
        else:
            outages.append({"start": slot["start"], "end": slot["end"]})
    return {"valid": True, "reason": "Графік 3.1 враховано", "slots": slots,
            "outages": outages, "updated": updated.isoformat(),
            "known_until": max(s["end"] for s in slots)}


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

