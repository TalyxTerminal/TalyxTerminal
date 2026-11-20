"""Market's request layers (planner): a source's month files, then its day files, then its API.

A fake source keeps one-minute files cut on UTC+8 days and months and an API reaching two days back with 300 rows a
request. Missing and late files fall to the next layer; what no layer can give is recorded unavailable with when to
ask again, never as "the source has none"; a refusal (HTTP 451) is unavailable too.
"""

from __future__ import annotations

import asyncio
from contextlib import aclosing

import numpy as np
import pytest

from modules.market.backend import common
from modules.market.backend.app import Market
from modules.market.backend.common import DAY_NS, MarketError, ns_of_date
from modules.market.backend.planner import RETRY_LATE, RETRY_REFUSED
from modules.market.backend.sources.base import History, Source, archive, coverage, row
from tests.fixtures.market.replay import ReplayEgress
from vocabulary.instruments import MarketInstrument, ohlcv_series

MINUTE = 60 * 10**9
HOUR = 60 * MINUTE
UTC8 = 8 * HOUR
NOW = ns_of_date("2024-03-10") + 12 * HOUR


def bars_between(start: int, end: int) -> dict:
    times = np.arange(start, end, MINUTE, dtype=np.int64)
    ones = np.ones(len(times))
    return {"time": times, "close_time": times + MINUTE, "open": ones, "high": ones, "low": ones, "close": ones,
            "volume": ones}


class Files(Source):
    id = "files"
    descriptor = {"name": "Files", "sourceIds": ["files"], "kinds": ["bars"], "assetClasses": ["crypto"],
                  "credentials": "none", "paid": False, "supports": ["history"]}

    def __init__(self, market, *, month_delay="P3D", day_delay="PT12H"):
        super().__init__(market)
        self.calls: list[tuple] = []
        self.missing: set[tuple[str, int]] = set()
        self.failing: dict[tuple[str, int], MarketError] = {}  # files that cannot be had
        self.refuse = False
        self.month_delay, self.day_delay = month_delay, day_delay

    async def describe(self, source_id):
        files = [archive("month", "2024-01-01", self.month_delay, day_zone="+08:00"),
                 archive("day", "2024-01-01", self.day_delay, day_zone="+08:00")]
        return coverage([row({"markets": ["X"]}, "bars", [("1m", "P2D", 300, {"archives": files})])],
                        concurrency=4, rpm=None)

    async def history(self, source_id, target, kind, timeframe, price_basis, start, end, archive=None):
        self.calls.append((archive, start, end))
        if archive is not None:
            if archive in self.missing:
                return History(bars_between(0, 0), missing=True)
            if archive in self.failing:
                raise self.failing[archive]
            return History(bars_between(start, end))
        if self.refuse:
            raise MarketError("forbidden", "files refused the request (HTTP 451: not available from this region)",
                              source_id="files", status=451)
        stop = min(end, start + 300 * MINUTE)
        return History(bars_between(start, stop), stop if stop < end else None)


@pytest.fixture
def clock():
    common.set_clock(lambda: NOW)
    yield
    common.set_clock(None)


async def opened(tmp_path, **options):
    market = Market(None, tmp_path, egress=ReplayEgress(), sources=[])
    source = Files(market, **options)
    market.registry.add(source)
    await market.start(load_catalogs=False)
    record = {"instrumentId": "vi2_" + "2" * 32, "identityVersion": 2, "venueId": "X", "symbol": "XBT",
              "displaySymbol": "XBT", "marketType": "spot", "assetClass": "crypto", "calendarId": "24_7",
              "adjustmentEligible": False}
    record = (await market.public.get({"records": [record]}))["items"][0]["instrument"]
    series = ohlcv_series(MarketInstrument.from_wire(record), "1m")
    return market, source, record, series


def local(day: str) -> int:
    """Midnight of a UTC+8 date."""
    return ns_of_date(day) - UTC8


def test_months_then_days_then_the_api_on_the_files_utc8_cut(tmp_path, clock):
    async def scenario():
        market, source, record, series = await opened(tmp_path)
        try:
            start = local("2024-02-01")
            plan = market.data.plan(record, series, start)
            fill = await market.data.fill(plan, start, NOW, limit=None)
            assert fill.problem is None
            files = sorted(call[0] for call in source.calls if call[0] is not None)
            # February is out (P3D); March's month is not: its days come one file each, cut at 16:00 UTC.
            assert files[0] == ("day", local("2024-03-01")) and ("month", local("2024-02-01")) in files
            days = [moment for unit, moment in files if unit == "day"]
            assert days == [local(f"2024-03-{d:02d}") for d in range(1, 10)]  # 03-09 ended 16:00Z 03-09, out at 04:00Z
            api = [call for call in source.calls if call[0] is None]
            assert api and min(call[1] for call in api) == local("2024-03-10")
            stored = market.store.read(plan.key, start, NOW)
            assert len(stored["time"]) == (NOW // MINUTE * MINUTE - start) // MINUTE
            assert not [gap for gap in market.data.gaps(plan, start, NOW) if gap["reason"] != "not_fetched"]
        finally:
            await market.close()

    asyncio.run(scenario())


def test_a_missing_month_falls_to_its_days_and_a_day_nothing_holds_is_unavailable(tmp_path, clock):
    async def scenario():
        market, source, record, series = await opened(tmp_path)
        try:
            start, end = local("2024-02-01"), local("2024-03-01")
            source.missing = {("month", start), ("day", local("2024-02-10"))}
            plan = market.data.plan(record, series, start)
            fill = await market.data.fill(plan, start, end, limit=None)
            days = {moment for (unit, moment), _a, _b in [c for c in source.calls if c[0]] if unit == "day"}
            assert days == {local("2024-02-01") + d * DAY_NS for d in range(29)}
            assert not [call for call in source.calls if call[0] is None]  # the API reaches two days back only
            assert fill.state == "partial" and fill.problem["code"] == "no_data"
            gaps = [gap for gap in market.data.gaps(plan, start, end) if gap["reason"] != "not_fetched"]
            assert gaps == [{"start": str(local("2024-02-10")), "end": str(local("2024-02-11")),
                             "reason": "unavailable", "retryAt": str(NOW + RETRY_LATE), "problem": fill.problem}]
            assert len(market.store.read(plan.key, start, end)["time"]) == 28 * 1440
            # Not asked again before it is due; asked again (and found) after.
            source.calls.clear()
            assert (await market.data.fill(plan, start, end, limit=None)).state == "not_needed"
            assert not source.calls
            source.missing.clear()
            common.set_clock(lambda: NOW + RETRY_LATE + 1)
            assert (await market.data.fill(plan, start, end, limit=None)).state == "done"
            # The month file comes first again (it may be out by now) and answers that day.
            assert source.calls == [(("month", start), local("2024-02-10"), local("2024-02-11"))]
            assert not [gap for gap in market.data.gaps(plan, start, end) if gap["reason"] == "unavailable"]
        finally:
            await market.close()

    asyncio.run(scenario())


def test_a_late_file_is_unavailable_until_it_is_due_not_none(tmp_path, clock):
    """The day files come out two days after the day: the newest days beyond the API wait for theirs."""
    async def scenario():
        market, source, record, series = await opened(tmp_path, day_delay="P4D")
        try:
            start = local("2024-03-01")
            plan = market.data.plan(record, series, start)
            fill = await market.data.fill(plan, start, NOW, limit=None)
            assert fill.problem["code"] == "no_data"
            gaps = [gap for gap in market.data.gaps(plan, start, NOW) if gap["reason"] == "unavailable"]
            # 03-06 (UTC+8) ends 03-06T16:00Z and is out four days later: after now, so it waits for exactly that.
            first_waiting = local("2024-03-06")
            assert gaps and gaps[0]["start"] == str(first_waiting)
            assert gaps[0]["retryAt"] == str(local("2024-03-07") + 4 * DAY_NS)
            pointer = market.store.pointer(plan.key)
            assert not [span for span in pointer["spans"] if span[2] == "none"]
        finally:
            await market.close()

    asyncio.run(scenario())


def test_a_refusal_is_unavailable_for_a_while_not_none(tmp_path, clock):
    async def scenario():
        market, source, record, series = await opened(tmp_path)
        try:
            source.refuse = True
            start = NOW - DAY_NS
            plan = market.data.plan(record, series, start)
            fill = await market.data.fill(plan, start, NOW, limit=None)
            assert fill.state == "failed" and fill.problem["status"] == 451
            pointer = market.store.pointer(plan.key)
            assert not [span for span in pointer["spans"] if span[2] == "none"]
            gaps = [gap for gap in market.data.gaps(plan, start, NOW) if gap["reason"] == "unavailable"]
            assert gaps and {gap["retryAt"] for gap in gaps} == {str(NOW + RETRY_REFUSED)}
            # A read in the meantime asks nothing and says why the rows are missing.
            source.calls.clear()
            with pytest.raises(MarketError) as raised:
                await market.data.read({"instrument": record, "series": series.to_dict(), "start": str(start),
                                        "end": str(NOW)})
            assert raised.value.code == "forbidden" and not source.calls
        finally:
            await market.close()

    asyncio.run(scenario())


def test_a_file_that_cannot_be_had_falls_to_the_layers_below(tmp_path, clock):
    """A file whose host cannot be reached (or refuses) is asked from the layers below it as a missing one is: a
    month from its days, a day from the API. A part no layer below reaches is the fill's problem and asked for again
    by the next fill (unavailable for a while when refused), never left without a word."""
    async def scenario():
        market, source, record, series = await opened(tmp_path)
        try:
            down = MarketError("source_unavailable", "Cannot reach files: connection refused", source_id="files")
            refused = MarketError("forbidden", "files refused the request (HTTP 403: access denied)",
                                  source_id="files", status=403)
            start = local("2024-02-01")
            source.failing = {("month", start): down, ("day", local("2024-03-05")): down,
                              ("day", local("2024-03-06")): refused, ("day", local("2024-03-09")): down}
            plan = market.data.plan(record, series, start)
            fill = await market.data.fill(plan, start, NOW, limit=None)
            days = {moment for (unit, moment), _a, _b in [c for c in source.calls if c[0]] if unit == "day"}
            assert {local("2024-02-01") + d * DAY_NS for d in range(29)} <= days
            # 03-09 (UTC+8) is within the API's two days.
            assert min(call[1] for call in source.calls if call[0] is None) == local("2024-03-09")
            assert fill.state == "partial" and fill.problem["code"] in ("source_unavailable", "forbidden")
            stored = market.store.read(plan.key, start, NOW)
            assert len(stored["time"]) == (NOW // MINUTE * MINUTE - start) // MINUTE - 2 * 1440
            gaps = market.data.gaps(plan, start, NOW)
            assert [(gap["start"], gap["end"], gap["reason"]) for gap in gaps] == [
                (str(local("2024-03-05")), str(local("2024-03-06")), "not_fetched"),
                (str(local("2024-03-06")), str(local("2024-03-07")), "unavailable")]
            assert gaps[1]["retryAt"] == str(NOW + RETRY_REFUSED) and gaps[1]["problem"]["status"] == 403
            source.failing.clear()
            source.calls.clear()
            assert (await market.data.fill(plan, start, NOW, limit=None)).state == "done"
            assert source.calls == [(("day", local("2024-03-05")), local("2024-03-05"), local("2024-03-06"))]
        finally:
            await market.close()

    asyncio.run(scenario())


async def next_page(reader):
    while True:
        item = await anext(reader)
        if item["type"] == "replace":
            return item["page"]


def test_a_refused_newest_page_still_fills_the_window_from_the_files(tmp_path, clock):
    """The API refuses us (HTTP 451, as Binance spot from some regions) while its files are there: the
    opening replace says filling (the source is not asked before it), the refused newest page sends nothing, the
    window is filled from the files in the background and goes out as one replace with the refusal, and the span only
    the API holds is recorded unavailable."""
    async def scenario():
        market, source, record, series = await opened(tmp_path)
        source.refuse = True
        try:
            async with aclosing(market.sessions.open({"instrument": record, "series": series.to_dict(), "warmup": 10_000})) as reader, asyncio.timeout(30):
                first, whole = await next_page(reader), await next_page(reader)
            assert first["fill"] == {"state": "filling", "problem": None}
            times = [int(time) for time in whole["columns"]["time"]]
            api_only = (NOW - local("2024-03-10")) // MINUTE  # after the files' last day
            assert times == list(range(times[0], local("2024-03-10"), MINUTE)) and len(times) == 10_000 - api_only
            assert whole["fill"]["state"] == "partial" and whole["fill"]["problem"]["status"] == 451
            assert any(gap["reason"] == "unavailable" for gap in whole["gaps"])
        finally:
            await market.close()

    asyncio.run(scenario())


@pytest.mark.parametrize("join_after_newest", [False, True])
def test_consumers_opening_a_cold_window_together_share_one_fill_and_its_outcome(tmp_path, clock, monkeypatch, join_after_newest):
    from modules.market.backend import live

    async def scenario():
        market, _, record, series = await opened(tmp_path)
        fill, fills = market.data.fill, []
        newest, rest = asyncio.Event(), asyncio.Event()

        async def counted(plan, start, end, *, limit, **options):
            fills.append(limit)
            await (newest if limit is None else rest).wait()
            return await fill(plan, start, end, limit=limit, **options)

        monkeypatch.setattr(market.data, "fill", counted)
        params = {"instrument": record, "series": series.to_dict(), "warmup": 10_000}
        try:
            async with aclosing(market.sessions.open(params)) as first, aclosing(market.sessions.open(params)) as second, asyncio.timeout(30):
                if join_after_newest:
                    cold = await next_page(first)
                else:
                    cold, other = await asyncio.gather(next_page(first), next_page(second))
                    assert cold == other
                assert cold["columns"]["time"] == [] and cold["fill"]["state"] == "filling"
                newest.set()
                recent = await next_page(first)
                assert len(recent["columns"]["time"]) == 300 and recent["fill"]["state"] == "filling"
                assert await next_page(second) == recent
                rest.set()
                left, right = await asyncio.gather(next_page(first), next_page(second))
                assert left == right
                assert left["fill"] == {"state": "done", "problem": None}
                assert len(left["columns"]["time"]) == 10_000
                assert fills == [None, live.WINDOW_FILL_LIMIT]
        finally:
            await market.close()

    asyncio.run(scenario())
