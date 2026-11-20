"""Wall-clock recurrences; keep the rule in its own time zone across DST changes."""

from datetime import UTC, datetime
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from dateutil.rrule import rrulestr
from dateutil.tz import datetime_exists
from talyx_sdk import ContractError


def next_due(rule, zone, start, after):
    try:
        tz = ZoneInfo(zone)
        local_start = datetime.fromtimestamp(start, tz).replace(microsecond=0)
        now = datetime.fromtimestamp(after, UTC).astimezone(tz)
        recurrence = rrulestr(rule, dtstart=local_start)
        for occurrence in recurrence.xafter(now):
            if datetime_exists(occurrence) and occurrence.timestamp() > after:
                return occurrence.timestamp()
        return None
    except (ValueError, TypeError, ZoneInfoNotFoundError, OverflowError) as exc:
        raise ContractError(
            "invalid_input", f"Choose an IANA time zone and a valid recurrence: {exc}"
        ) from exc


def due_run(routine, now, returning, paused):
    """Advance from now, coalescing all occurrences missed while the workspace was closed."""
    due = routine["nextDue"]
    if due is None or due > now:
        return None, due
    next_time = next_due(routine["rrule"], routine["zone"], routine["start"], now)
    if paused:
        kind = "skipped"
    else:
        kind = "catchup" if returning else "schedule"
    return {
        "kind": kind,
        "due": due,
        "routine": routine["name"],
        "routineId": routine["id"],
    }, next_time
