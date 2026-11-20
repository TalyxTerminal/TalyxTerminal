"""Market's calendar service methods, callable without a running host."""

from datetime import date

from modules.market.backend import calendars


def connect_calendar():
    async def sessions(value):
        return calendars.sessions(value["calendarId"], date.fromisoformat(value["startDate"]),
                                  date.fromisoformat(value["endDate"]))

    async def bar_times(value):
        return calendars.bar_times(value["calendarId"], value["timeframe"], value["sessionScope"],
                                   int(value["start"]), int(value["end"]),
                                   int(value["after"]) if value.get("after") else None, value.get("limit", 5000))

    return {"sessions": sessions, "bar_times": bar_times}
