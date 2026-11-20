"""Full-year Market benchmark checks the actual cohort and trading calendar outside the timed read."""

import asyncio

import pytest


def test_year_benchmark_rejects_a_file_with_only_the_first_and_last_sessions(tmp_path, monkeypatch):
    import json
    from contextlib import asynccontextmanager
    from types import SimpleNamespace

    import exchange_calendars as xcals
    import mcp
    import pyarrow as pa
    import pyarrow.parquet as pq
    from mcp.client import streamable_http

    from scripts.perf import market_read

    ids = [f"id{i}" for i in range(503)]
    dates = list(xcals.get_calendar("XNYS").sessions_in_range("2025-01-02", "2026-01-02").date)
    path = tmp_path / "bars.parquet"
    pq.write_table(pa.table({"instrumentId": [i for i in ids for _ in range(2)],
                             "time": [dates[0], dates[-1]] * len(ids)}), path)

    class Session:
        async def initialize(self):
            pass

        async def call_tool(self, name, args):
            if name == "market_read_research":
                page = {"columns": [{"name": "instrumentId"}, {"name": "code"}],
                        "rows": [[i, i] for i in ids], "nextCursor": None}
            elif name == "market_instruments_get":
                page = {"items": [{"instrument": {"instrumentId": i, "calendarId": "XNYS"}} for i in args["symbols"]],
                        "nextCursor": None}
            elif name == "market_calendar_sessions":
                page = {"evidence": "verified", "nextCursor": None, "sessions": [
                    {"date": day.isoformat(), "open": f"{day}T14:30:00Z"} for day in dates]}
            else:
                assert name == "market_read_data"
                page = {"total": 1006, "file": {"path": str(path)}}
            return SimpleNamespace(isError=False, content=[SimpleNamespace(text=json.dumps(page))])

    @asynccontextmanager
    async def connection(*args):
        yield (None, None, None)

    @asynccontextmanager
    async def session(*args):
        yield Session()

    monkeypatch.setattr(streamable_http, "streamablehttp_client", connection)
    monkeypatch.setattr(mcp, "ClientSession", session)
    with pytest.raises(RuntimeError, match=f"expected one daily bar on each of {len(dates)} trading dates"):
        asyncio.run(market_read("test", "2025-01-02", "2026-01-02", "2025-01-02"))


def test_benchmark_limits_expected_dates_to_listing_lifetime():
    from datetime import date

    import pyarrow as pa

    from scripts.perf import verify_market_year

    sessions = {"XNYS": [("2025-01-02", 1), ("2025-01-03", 2), ("2025-01-06", 3)]}
    record = {"listedFrom": "2", "delistedAt": "3", "calendarId": "XNYS"}
    table = pa.table({"instrumentId": ["X"], "time": [date(2025, 1, 3)]})
    verify_market_year(table, {"X": record}, sessions)
    with pytest.raises(RuntimeError, match="repeated"):
        verify_market_year(pa.concat_tables([table, table]), {"X": record}, sessions)
