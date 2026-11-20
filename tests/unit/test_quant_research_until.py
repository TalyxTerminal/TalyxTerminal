"""`until: "latest"` in a research object's data settings: the day of the last bar already closed on each instrument's own calendar when a run
starts, written into the run; and every place that reads a data setting's `until` goes through that."""
import ast
import asyncio
from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace
from zoneinfo import ZoneInfo

import pytest

from modules.quant.backend import object_data
from tests.unit.quant_runtime_fakes import adapt

BACKEND = Path(__file__).resolve().parents[2] / "modules" / "quant" / "backend"
HOUR = 3_600_000


def at(text):
    return int(datetime.fromisoformat(text).replace(tzinfo=UTC).timestamp() * 1000)


def calendar(zone, opens, closes):
    """Market's boundaries for a daily or hourly calendar: weekdays only for an exchange (`zone` given), every hour for 24/7."""
    def make(args):
        start, end, found = int(args["start"]) // 1_000_000, (int(args["end"]) - 1) // 1_000_000, []
        if args["calendarId"] == "24_7":
            step = HOUR
            first = (start // step) * step
            found = [{"openAt": moment, "closeAt": moment + step} for moment in range(first, end + 1, step)]
        else:
            tz = ZoneInfo(zone)
            # Exchange daily bars carry a local-midnight label and the actual session close.
            opening = (0, 0) if args["timeframe"] == "1d" else opens
            day = datetime.fromtimestamp(start / 1000, UTC).date() - timedelta(days=2)
            while datetime(day.year, day.month, day.day, tzinfo=UTC).timestamp() * 1000 <= end + 2 * 86_400_000:
                if day.weekday() < 5:
                    opened = datetime(day.year, day.month, day.day, *opening, tzinfo=tz)
                    closed = datetime(day.year, day.month, day.day, *closes, tzinfo=tz)
                    found.append({"openAt": int(opened.timestamp() * 1000), "closeAt": int(closed.timestamp() * 1000)})
                day += timedelta(days=1)
            found = [item for item in found if start <= item["openAt"] <= end]
        return {"evidence": "verified", "timezone": zone or "UTC", "open": [str(row["openAt"] * 1_000_000) for row in found],
                "close": [str(row["closeAt"] * 1_000_000) for row in found], "next": None, "reason": None}
    return make


CALENDARS = {"XSHG": calendar("Asia/Shanghai", (9, 30), (15, 0)), "XNYS": calendar("America/New_York", (9, 30), (16, 0)), "24_7": calendar(None, None, None)}


def context(now_ms, delay_ms=0, *, status="current", read=None):
    """Market as `until: latest` reads it: its calendar, and each series' publish cutoff (now minus the source's publish delay)."""
    async def boundaries(args):
        return CALENDARS[args["calendarId"]](args)

    async def coverage(args):
        from modules.quant.backend.public_settings import timestamp
        return {"columns":[{"name":"timeframe"},{"name":"publishedThrough"},{"name":"status"}],
                "rows":[["1d",timestamp(now_ms-delay_ms),status],["1h",timestamp(now_ms),"current"]],"nextCursor":None}
    return adapt(SimpleNamespace(require=lambda name: SimpleNamespace(bar_times=boundaries, coverage=coverage, read=read)))


def instrument(calendar_id, tag):
    return {"instrumentId": f"vi1_{tag * 64}", "calendarId": calendar_id, "symbol": tag, "displaySymbol": tag, "assetClass": "equity", "marketType": "spot",
            "venueId": "x", "adjustmentEligible": False}


def data(*items, timeframe="1d"):
    return {"symbols": [{"instrument": item} for item in items], "timeframe": timeframe, "since": "2016-01-01", "until": "latest"}


def latest(items, now, timeframe="1d", delay_ms=0):
    from modules.quant.backend.object_data import resolve_until
    return asyncio.run(resolve_until(context(at(now), delay_ms), data(*items, timeframe=timeframe)))


@pytest.mark.parametrize("calendar_id, tag, now, expected", [
    # A-shares close at 15:00 Beijing: at 20:00 in Beijing (12:00 UTC) the same day is in; at 08:00 it is not
    ("XSHG", "a", "2024-03-13T12:00", "2024-03-13"), ("XSHG", "a", "2024-03-13T00:00", "2024-03-12"), ("XSHG", "a", "2024-03-16T12:00", "2024-03-15"),
    # US stocks close at 16:00 New York (20:00 UTC in March after the clocks change): at UTC 01:00 the evening before is in; at 13:00 UTC the open has not come
    ("XNYS", "b", "2024-03-14T01:00", "2024-03-13"), ("XNYS", "b", "2024-03-13T22:00", "2024-03-13"), ("XNYS", "b", "2024-03-13T13:00", "2024-03-12"),
    ("XNYS", "b", "2024-03-17T01:00", "2024-03-15"),
    # 24/7: hourly bars close on the hour; just after midnight the last closed bar is yesterday's last hour
    ("24_7", "c", "2024-03-13T10:37", "2024-03-13"), ("24_7", "c", "2024-03-14T00:20", "2024-03-13"), ("24_7", "c", "2024-03-14T00:00", "2024-03-13")])
def test_latest_follows_each_calendar(calendar_id, tag, now, expected):
    timeframe = "1h" if calendar_id == "24_7" else "1d"
    resolved = latest([instrument(calendar_id, tag)], now, timeframe)
    assert resolved["until"] == expected and resolved["untilLatest"] is True
    assert resolved["since"] == "2016-01-01"


def test_latest_waits_for_the_source_to_publish_the_day():
    # New York closed at 20:00 UTC; a source that publishes daily bars four hours later has the day only from 00:00 UTC
    assert latest([instrument("XNYS", "b")], "2024-03-13T22:00", delay_ms=4 * HOUR)["until"] == "2024-03-12"
    assert latest([instrument("XNYS", "b")], "2024-03-14T00:30", delay_ms=4 * HOUR)["until"] == "2024-03-13"


@pytest.mark.parametrize("status", ["behind", "not_stored"])
def test_latest_daily_pool_waits_for_the_actual_bar_after_the_declared_delay(status):
    from tests.unit.test_quant_object_runs import SPY

    ief = {**SPY, "instrumentId": "vi2_" + "f" * 32, "symbol": "IEF", "displaySymbol": "IEF"}
    previous, newest = [str(at(f"2026-11-{day}T05:00") * 1_000_000) for day in (18, 19)]
    delivered = {SPY["instrumentId"]: [previous], ief["instrumentId"]: [previous, newest]}
    gap_reason = "not_fetched"

    async def read(args):
        assert (args["fill"], args["limit"], args["start"], args["end"]) == (
            "await", 2, previous, str(at("2026-11-19T21:00") * 1_000_000))
        times = delivered[args["instrumentId"]]
        return {"columns": {"time": times}, "fill": {"state": "done", "problem": None},
                "gaps": [] if newest in times else [{"start": newest, "end": str(int(newest) + 1), "reason": gap_reason}]}

    ctx = context(at("2026-11-20T01:13"), 4 * HOUR, status=status, read=read)
    settings = data({**SPY, "calendarId": "XNYS"}, {**ief, "calendarId": "XNYS"})
    assert asyncio.run(object_data.resolve_until(ctx, settings))["until"] == "2026-11-18"
    fixed = {**settings, "until": "2026-11-19"}
    assert asyncio.run(object_data.resolve_until(ctx, fixed)) is fixed
    # A confirmed hole still goes through strict history validation at the requested end.
    gap_reason = "source_has_none"
    assert asyncio.run(object_data.resolve_until(ctx, settings))["until"] == "2026-11-19"
    gap_reason = "not_fetched"
    delivered[SPY["instrumentId"]].append(newest)
    assert asyncio.run(object_data.resolve_until(ctx, settings))["until"] == "2026-11-19"


def test_daily_pool_on_one_calendar_observes_each_sources_cutoff():
    now = at("2026-11-20T01:13")
    slow, fast = instrument("XNYS", "a"), instrument("XNYS", "b")

    async def call(name, args):
        if name == "market.read_coverage":
            delay = 5 if args["symbols"] == [slow["instrumentId"]] else 4
            return {"columns": [{"name": "timeframe"}, {"name": "publishedThrough"}, {"name": "status"}],
                    "rows": [["1d", object_data.M.timestamp(now - delay * HOUR), "current"]], "nextCursor": None}
        assert name == "market.calendar.bar_times"
        return CALENDARS["XNYS"](args)

    assert asyncio.run(object_data.resolve_until(SimpleNamespace(call=call), data(fast, slow)))["until"] == "2026-11-18"


def test_latest_follows_each_calendar_for_hourly_bars_by_the_hour_not_the_day():
    # the day alone cannot tell 23:00 from 00:00: the last closed hourly bar at 00:20 is of the day before; at 01:20 it is of today
    assert latest([instrument("24_7", "c")], "2024-03-14T00:20", "1h")["until"] == "2024-03-13"
    assert latest([instrument("24_7", "c")], "2024-03-14T01:20", "1h")["until"] == "2024-03-14"


def test_several_instruments_record_the_latest_day():
    items = [instrument("XSHG", "a"), instrument("XNYS", "b")]
    # 2024-03-13 12:00 UTC: Shanghai closed today, New York has not opened: the settings record the later day
    resolved = latest(items, "2024-03-13T12:00")
    assert resolved["until"] == "2024-03-13"
    # New York alone, at the same instant, has only yesterday
    assert latest([items[1]], "2024-03-13T12:00")["until"] == "2024-03-12"


def test_a_fixed_last_day_is_returned_as_it_is():
    from modules.quant.backend.object_data import resolve_until
    fixed = {**data(instrument("XNYS", "b")), "until": "2024-02-01"}
    resolved = asyncio.run(resolve_until(context(at("2024-03-13T12:00")), fixed))
    assert resolved == fixed and "untilLatest" not in resolved


def test_the_settings_are_not_changed_by_resolving_them():
    from modules.quant.backend.object_data import resolve_until
    original = data(instrument("XNYS", "b"))
    copy = {**original, "symbols": list(original["symbols"])}
    asyncio.run(resolve_until(context(at("2024-03-13T22:00")), original))
    assert original == copy and original["until"] == "latest"


def test_latest_is_accepted_where_the_settings_are_checked():
    object_data.required_settings(data(instrument("XNYS", "b")))  # no error
    from modules.quant.backend.object_store import check_data
    check_data({"symbols": [{"instrument": instrument("XNYS", "b")}], "timeframe": "1d", "since": "2016-01-01", "until": "latest"}, "factor")
    with pytest.raises(Exception, match="end date"):
        check_data({"symbols": [{"instrument": instrument("XNYS", "b")}], "timeframe": "1d", "since": "2016-01-01", "until": "yesterday"}, "factor")


# ---------------------------------------------------------------------------------------------------------------- every reader


def reads_of_until():
    """Every place in the backend that reads `until` out of data settings: `x["until"]` or `x.get("until")` where x is data, settings or the like."""
    found = []
    for path in sorted(BACKEND.glob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        owners = {}
        for node in ast.walk(tree):
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                for inner in ast.walk(node):
                    owners[id(inner)] = node
        for node in ast.walk(tree):
            receiver = None
            if isinstance(node, ast.Subscript) and isinstance(node.slice, ast.Constant) and node.slice.value == "until" and isinstance(node.ctx, ast.Load):
                receiver = node.value
            elif isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute) and node.func.attr == "get" and node.args \
                    and isinstance(node.args[0], ast.Constant) and node.args[0].value == "until":
                receiver = node.func.value
            if receiver is None:
                continue
            text = ast.unparse(receiver)
            if not ("data" in text or "settings" in text):
                continue
            function = owners.get(id(node))
            found.append((path.name, node.lineno, text, function))
    return found


# Two places read the word without needing the day: object_store checks the settings (and so must accept the literal "latest"), object_history shows the
# summary of a run, whose data settings were written resolved when it started.
EXEMPT = {("object_store.py","checked_settings"):"checks that an existing held-out date remains in range",
          ("public_settings.py","settings"):"renders latest as an omitted end in the public settings",
          ("object_routes.py","plan_held_out"):"reads the selected completed run, whose dates are already resolved",
          ("object_store.py", "check_data"): "checks the settings; accepts the literal 'latest'",
          ("factor_data.py", "calendar_items"): "selects calendars for resolve_until; only inspects whether the literal is latest",
          ("object_history.py", "data_label"): "labels a run's recorded settings, already resolved",
          ("allocation_runs.py", "held_status"): "the source allocation's range was resolved before its backtest",
          ("object_runs.py", "source_settings"): "historical validations use the recorded cut-off; never resolve it against today's market",
          ("strategy_validation.py", "status"): "shows the source run's resolved held-out period and rejects an unknown end",
          ("allocation_runs.py", "estimate"): "an estimate of the periods; reads 'latest' as now itself",
          ("model_runs.py", "held_out_plan"): "a model run's settings, resolved when the run started (model_runs.start)",
          ("model_runs.py", "held_out_windows"): "the source run's recorded settings, resolved when it started",
          ("model_runs.py", "named"): "names a run's recorded settings, resolved when it started",
          ("run_materials.py", "read"): "a run's recorded data, resolved when it started",
          ("strategy_views.py", "data_of"): "shows a run's settings; 'latest' falls back to the day the run read to",
          ("strategy_views.py", "signal_gaps"): "a run's prepared data summary (record['data']), whose until is the day it read to in ms"}


def test_every_place_that_reads_a_data_settings_until_goes_through_resolve_until():
    unresolved = []
    for name, line, text, function in reads_of_until():
        body = ast.unparse(function) if function is not None else ""
        if (name, getattr(function, "name", None)) in EXEMPT:
            continue
        if "resolve_until" not in body and "frozen_data" not in body and "resolved" not in text:
            unresolved.append(f"{name}:{line} {text}['until']")
    assert not unresolved, "reads of a data setting's until that do not resolve 'latest' first:\n" + "\n".join(unresolved)


def test_the_readers_found_are_the_ones_the_design_names():
    files = {name for name, *_ in reads_of_until()}
    assert {"factor_data.py", "factor_runs.py", "object_data.py", "object_store.py"} <= files


def test_the_front_end_shows_the_resolved_day_but_the_run_records_the_backends():
    source = (BACKEND.parents[0] / "frontend" / "objects.ts").read_text(encoding="utf-8")
    assert "untilDay" in source and '"latest"' in source
