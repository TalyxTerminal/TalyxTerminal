"""Market 2.0 research data and fetch jobs: index member history, resumable downloads,
exports and SEC filings (build criteria 15-18), replaying recorded source data."""

from __future__ import annotations

import asyncio
import shutil
from pathlib import Path

import pyarrow.parquet as pq
import pytest

from modules.market.backend.app import Market
from modules.market.backend.common import MarketError, iso_of_ns, ns_of_date
from tests.fixtures.market.replay import ReplayEgress
from vocabulary.instruments import MarketInstrument, ohlcv_series

YAHOO = ("yfinance", "research")
NASDAQ = ("nasdaqtrader", "symbol-directory")
SP500 = ("sp500_history", "members")
SEC = ("sec", "aapl")
MEMBERS_2015 = ["AAPL", "BRK.B", "BF.B", "FB", "LB", "APC", "STI", "POM", "BBBY", "ANTM"]
STOCKS = ["AAPL", "MSFT", "NVDA", "AMZN", "GOOGL", "META", "BRK.B", "JPM", "V", "MA", "KO", "IBM", "XOM", "JNJ",
          "PG", "HD", "UNH", "WMT", "PEP", "CSCO"]
REVENUE = "us-gaap:RevenueFromContractWithCustomerExcludingAssessedTax"


def run(coro):
    return asyncio.run(coro)


class Upstream(ReplayEgress):
    """The replay, answering each chart request after a delay; counts requests in flight."""

    def __init__(self, *recordings, delay=0.01, spread=0.0):
        super().__init__(*recordings)
        self.delay, self.spread = delay, spread
        self.in_flight = self.peak = 0
        self.charts = 0
        self.fail = {}          # chart request number -> (status, body, headers)
        self.refuse = {}        # Yahoo symbol -> (status, body)

    async def request(self, source_id, url, **options):
        if "/v8/finance/chart/" not in url:
            return await super().request(source_id, url, **options)
        self.charts += 1
        number = self.charts
        self.in_flight += 1
        self.peak = max(self.peak, self.in_flight)
        try:
            await asyncio.sleep(self.delay + self.spread * number)
            symbol = url.rsplit("/", 1)[-1]
            if number in self.fail or symbol in self.refuse:
                status, body, headers = self.fail.get(number) or (*self.refuse[symbol], {})
                self.rules.insert(0, (lambda u, p, n=url: u == n, status, body, headers))
                try:
                    return await super().request(source_id, url, **options)
                finally:
                    self.rules.pop(0)
            return await super().request(source_id, url, **options)
        finally:
            self.in_flight -= 1


async def open_market(root: Path, egress: ReplayEgress | None = None, *, email: str | None = None) -> Market:
    market = Market(None, root, egress=egress or Upstream(YAHOO, NASDAQ, SP500, SEC))
    if email:
        market.registry.entry("sec").source.settings["contactEmail"] = email
    await market.start(load_catalogs=False)
    await market.instruments.load("nasdaqtrader", "US", "listings")
    return market


async def resolve(market, code):
    return (await market.instruments.resolve({"text": code, "market": "US"}))["instrument"]


def daily(record, adjustment=None):
    return ohlcv_series(MarketInstrument.from_wire(record), "1d", adjustment=adjustment).to_dict()


def charts(egress) -> int:
    return sum(1 for item in egress.requests if "/v8/finance/chart/" in item["url"])


def request_2024(records):
    return {"targets": [{"instrument": record} for record in records],
            "kinds": [{"kind": "bars", "timeframe": "1d"}], "start": "2024-01-01", "end": "2024-12-31"}


# ── 15. S&P 500 member history ─────────────────────────────────────────────


def test_index_members_map_by_code_period_and_never_to_a_later_holder_of_the_code(tmp_path):
    async def scenario():
        market = await open_market(tmp_path)
        try:
            for code in MEMBERS_2015:
                try:
                    record = await resolve(market, code)
                except MarketError:
                    continue      # delisted codes Yahoo no longer knows
                await market.data.read({"instrument": record, "series": daily(record), "fill": "await",
                                        "start": str(ns_of_date("2015-12-01")), "end": str(ns_of_date("2016-01-01"))})
            index = (await market.instruments.resolve({"text": "^GSPC"}))["instrument"]
            page = await market.research.read({"kind": "members", "index": index, "limit": 5000,
                                               "at": str(ns_of_date("2016-01-04"))})
            columns = page["columns"]
            assert len(columns["code"]) == 504
            mapped = {code for code, instrument in zip(columns["code"], columns["instrumentId"], strict=True)
                      if instrument is not None}
            assert mapped == {"AAPL", "BRK.B", "BF.B"}
            assert page["unmapped"] == 501
            table = await market.public.research({"data":"members","index":{"symbol":"^GSPC"},"at":"2016-01-04","limit":5})
            assert len(table["rows"]) == 5 and "code" in [c["name"] for c in table["columns"]] and table["nextCursor"]
            # FB is a member in 2016, but today's FB (a 2025 ETF) never held the code then.
            fb_now = await resolve(market, "FB")
            assert fb_now["instrumentId"] not in columns["instrumentId"]
            assert "survivorship" in page["note"] or "effective date" in page["note"]
            # A range read maps a member over its whole overlap with the range, so a range starting before
            # the first bar fetched still maps AAPL (a series begins at the first session's open).
            ranged = await market.research.read({"kind": "members", "index": index, "limit": 5000, "fill": "none",
                                                 "start": str(ns_of_date("2015-11-30")),
                                                 "end": str(ns_of_date("2016-01-05"))})
            pairs = list(zip(ranged["columns"]["code"], ranged["columns"]["instrumentId"], strict=True))
            assert {code for code, instrument in pairs if instrument is not None} == {"AAPL", "BRK.B", "BF.B"}
            assert fb_now["instrumentId"] not in ranged["columns"]["instrumentId"]
            assert ranged["unmapped"] == sum(instrument is None for _, instrument in pairs)
            # A fetch of the index takes members that map to none under today's holder of the code while that
            # listing's start is unknown; once its bars show it began later (today's FB), it is left out.
            start, end = ns_of_date("2016-01-04"), ns_of_date("2016-01-05")
            exact, _ = await market.research.members_between(index, start, end)
            assert {item["symbol"] for item in exact} == {"AAPL", "BRK.B", "BF.B"}
            wide, unmapped = await market.research.members_between(index, start, end, candidates=True)
            assert {"AAPL", "MSFT", "FB"} <= {item["symbol"] for item in wide} and len(wide) + unmapped == 504
            market.identity.observe_first_bar(fb_now["instrumentId"], ns_of_date("2025-06-26"))
            wide, _ = await market.research.members_between(index, start, end, candidates=True)
            assert "FB" not in {item["symbol"] for item in wide}
            later = await market.research.read({"kind": "members", "index": index, "limit": 5000,
                                                "at": str(ns_of_date("2020-03-03")), "fill": "none"})
            assert len(later["columns"]["code"]) == 505
            described = await market.research.describe({"kinds": ["members"]})
            assert described["indices"][0]["pointInTime"] is True
            assert described["indices"][0]["index"]["instrumentId"] == index["instrumentId"]
        finally:
            await market.close()
    run(scenario())


# ── 16. fetch jobs: rate limits, concurrency, workspaces ───────────────────


def test_fetch_waits_out_a_rate_limit_and_keeps_eight_requests_in_flight(tmp_path):
    async def scenario():
        egress = Upstream(YAHOO, NASDAQ, delay=0.02)
        egress.fail[8] = (429, "Too Many Requests", {"Retry-After": "2"})
        market = await open_market(tmp_path, egress)
        try:
            records = [await resolve(market, code) for code in STOCKS]
            before = charts(egress)
            events = []
            result = await market.jobs.fetch(request_2024(records), workspace_id="w",
                                             progress=events.append)
            assert result["completed"] == 20 and result["failureCount"] == 0
            assert result["state"] == "completed"
            assert charts(egress) - before == 21
            assert egress.peak <= 8
            waiting = [event for event in events if event["waitingFor"] == "yfinance"]
            assert waiting and waiting[0]["waitingUntil"] is not None
            stored = await market.data.read({"instrument": records[0], "series": daily(records[0]), "fill": "none",
                                             "start": str(ns_of_date("2024-01-01")),
                                             "end": str(ns_of_date("2025-01-01"))})
            assert len(stored["columns"]["time"]) == 252
        finally:
            await market.close()
    run(scenario())


def test_closing_the_workspace_interrupts_and_resuming_requests_only_what_is_left(tmp_path):
    async def scenario():
        egress = Upstream(YAHOO, NASDAQ, delay=0.01, spread=0.03)
        market = await open_market(tmp_path, egress)
        try:
            records = [await resolve(market, code) for code in STOCKS]
            value = request_2024(records)

            def progress(event):
                if event["done"] == 10 and not closing.done():
                    closing.set_result(None)
                    asyncio.ensure_future(market.jobs.workspace_closed("w"))

            closing = asyncio.get_running_loop().create_future()
            first = await market.jobs.fetch(value, workspace_id="w", progress=progress)
            assert first["state"] == "interrupted" and first["completed"] == 10
            job = market.jobs.get(first["jobId"])
            assert job.record["interruption"] == "workspace_closed"
            before = charts(egress)
            market.jobs.workspace_opened("w")
            await asyncio.wait_for(job.done.wait(), 30)
            assert job.record["state"] == "completed"
            second = market.jobs.result(job)
            assert second["completed"] == 20
            assert charts(egress) - before == 10
        finally:
            await market.close()
    run(scenario())


# ── 17. export ─────────────────────────────────────────────────────────────


def hand_over(market, folder: Path):
    """Artifacts become files in ``folder`` (the platform artifact store is not part of this test)."""
    folder.mkdir(parents=True, exist_ok=True)

    async def publish(path, _kind, name):
        shutil.move(path, folder / name)
        return {"artifactId": name, "sha256": "0" * 64}
    market.handoff.publish = publish


def test_export_writes_one_parquet_with_a_row_group_per_instrument(tmp_path):
    async def scenario():
        market = await open_market(tmp_path / "market")
        hand_over(market, tmp_path / "artifacts")
        try:
            records = [await resolve(market, code) for code in STOCKS]
            await market.jobs.fetch(request_2024(records), workspace_id=None)
            result = await market.exports.export(
                {"selections": [{"instrument": r, "series": daily(r, "split_dividend")} for r in records],
                 "start": str(ns_of_date("2024-01-01")), "end": str(ns_of_date("2025-01-01"))})
            assert result["failures"] == []
            (file,) = result["files"]
            assert file["content"] == "ohlcv:1d:split_dividend" and file["rows"] == 5040 and file["instruments"] == 20
            parquet = pq.ParquetFile(tmp_path / "artifacts" / file["artifact"]["artifactId"])
            assert parquet.metadata.num_row_groups == 20
            table = parquet.read().to_pandas()
            aapl = table[table["instrumentId"] == records[0]["instrumentId"]]
            last = aapl.iloc[-1]
            assert iso_of_ns(int(last["time"].value))[:10] == "2024-12-31"
            assert round(last["close"], 2) == 250.42
            assert (table["knownAt"] == table["closeTime"]).all()  # known at the close, though Yahoo gives it as final hours later
            assert set(table[table["instrumentId"] == records[6]["instrumentId"]]["code"]) == {"BRK.B"}
            assert list(table.columns[:6]) == ["instrumentId", "market", "code", "time", "closeTime", "knownAt"]
            written = await market.exports.export({"selections": [{"instrument": records[0], "series": daily(records[0])}],
                                             "destination": {"folder": str(tmp_path / "out")}})
            assert Path(written["files"][0]["path"]).is_file()
        finally:
            await market.close()
    run(scenario())


def test_export_completes_without_a_refused_instrument_and_reports_it(tmp_path):
    async def scenario():
        egress = Upstream(YAHOO, NASDAQ)
        egress.refuse["IBM"] = (403, "Forbidden: this symbol is not available in your region")
        market = await open_market(tmp_path / "market", egress)
        hand_over(market, tmp_path / "artifacts")
        try:
            records = [await resolve(market, code) for code in STOCKS]
            result = await market.exports.export(
                {"selections": [{"instrument": r, "series": daily(r, "split_dividend")} for r in records],
                 "start": str(ns_of_date("2024-01-01")), "end": str(ns_of_date("2025-01-01"))})
            (file,) = result["files"]
            assert file["rows"] == 4788
            (failure,) = result["failures"]
            assert failure["instrumentId"] == records[STOCKS.index("IBM")]["instrumentId"]
            assert failure["problem"]["code"] == "forbidden" and failure["problem"]["status"] == 403
            assert failure["problem"]["message"] == "yfinance refused the request (HTTP 403: access denied)"  # the source and the status, not its response
        finally:
            await market.close()
    run(scenario())


def test_fetch_refuses_a_download_larger_than_the_free_space_before_any_request(tmp_path):
    async def scenario():
        egress = Upstream(YAHOO, NASDAQ)
        market = await open_market(tmp_path, egress)
        market.jobs.free_bytes = lambda: 1_000_000
        try:
            records = [await resolve(market, code) for code in STOCKS]
            before = charts(egress)
            with pytest.raises(MarketError) as full:
                await market.jobs.fetch({**request_2024(records), "start": "2015-01-01"}, workspace_id=None)
            assert full.value.code == "insufficient_space"
            assert full.value.details["freeBytes"] == 1_000_000
            assert full.value.details["estimatedBytes"] > 1_000_000
            assert charts(egress) == before
        finally:
            await market.close()
    run(scenario())


# ── 18. SEC financials, classification, shares ─────────────────────────────


def test_sec_financials_are_read_as_they_were_known(tmp_path):
    async def scenario():
        market = await open_market(tmp_path, email="research-test@example.com")
        try:
            aapl = await resolve(market, "AAPL")
            query = {"kind": "financials", "instruments": [aapl], "metrics": [REVENUE], "periodKinds": ["FY"]}
            known = await market.research.read({**query, "asOf": str(ns_of_date("2024-11-02") + 4 * 3600 * 10**9 - 1)})
            rows = list(zip(*(known["columns"][name] for name in ("periodEnd", "value", "knownAt")), strict=True))
            fiscal_2024 = [row for row in rows if row[0] == str(ns_of_date("2024-09-28") + 4 * 3600 * 10**9)]
            assert fiscal_2024 == [(str(ns_of_date("2024-09-28") + 4 * 3600 * 10**9), "391035000000", str(ns_of_date("2024-11-02") + 4 * 3600 * 10**9 - 1))]
            before = await market.research.read({**query, "asOf": str(ns_of_date("2024-11-02")), "fill": "none"})
            latest = max(zip(before["columns"]["periodEnd"], before["columns"]["value"], strict=True),
                         key=lambda row: int(row[0]))
            assert latest == (str(ns_of_date("2023-09-30") + 4 * 3600 * 10**9), "383285000000")
        finally:
            await market.close()
    run(scenario())


def test_sec_without_a_contact_email_is_unsupported_and_sends_nothing(tmp_path):
    async def scenario():
        egress = Upstream(YAHOO, NASDAQ, SP500, SEC)
        market = await open_market(tmp_path, egress)
        try:
            aapl = await resolve(market, "AAPL")
            page = await market.research.read({"kind": "financials", "instruments": [aapl]})
            assert page["coverage"][0]["problem"]["code"] == "unsupported"
            assert not any("sec.gov" in item["url"] for item in egress.requests)
        finally:
            await market.close()
    run(scenario())


def test_classification_has_sector_and_industry_and_shares_are_raw_counts(tmp_path):
    async def scenario():
        market = await open_market(tmp_path)
        try:
            aapl = await resolve(market, "AAPL")
            page = await market.research.read({"kind": "classification", "instruments": [aapl]})
            columns = page["columns"]
            assert columns["level"] == ["sector", "industry"]
            assert columns["name"] == ["Technology", "Consumer Electronics"]
            assert columns["from"] == [None, None]
            shares = await market.research.read({"kind": "shares", "instruments": [aapl], "limit": 5000,
                                                 "start": str(ns_of_date("2016-01-01")),
                                                 "end": str(ns_of_date("2017-01-01"))})
            counts = [int(value) for value in shares["columns"]["shares"]]
            # Before the 2020 4:1 split Apple had about 5.4 billion shares; not multiplied by later splits.
            assert counts and all(5_000_000_000 < value < 6_000_000_000 for value in counts)
        finally:
            await market.close()
    run(scenario())


@pytest.mark.parametrize("status", [403, 451])
def test_public_read_reports_upstream_refusal_without_retry(tmp_path, status):
    async def scenario():
        egress = Upstream(YAHOO, NASDAQ)
        egress.refuse["AAPL"] = (status, "Forbidden")
        market = await open_market(tmp_path, egress)
        try:
            request = {"symbols":["AAPL"], "start":"2025-01-02", "end":"2025-01-02"}
            with pytest.raises(MarketError) as error:
                await asyncio.wait_for(market.public.read(request), timeout=5)
            assert error.value.code == "forbidden" and error.value.status == status
            assert egress.charts == 1
        finally:
            await market.close()
    run(scenario())


def test_sec_uses_edgar_acceptance_time_when_available():
    from modules.market.backend.sources.sec import facts_rows

    fact = {"end": "2024-03-31", "filed": "2024-04-25", "form": "10-Q", "fp": "Q1", "val": 10,
            "acceptanceDateTime": "2024-04-25T20:03:02Z"}
    row, = facts_rows({"facts": {"us-gaap": {"Revenue": {"units": {"USD": [fact]}}}}})
    assert int(row["knownAt"]) == ns_of_date("2024-04-25") + (20 * 3600 + 3 * 60 + 2) * 10**9


def test_sec_period_end_filters_and_displays_the_reported_exchange_date(tmp_path):
    async def scenario():
        market = await open_market(tmp_path, email="research-test@example.com")
        try:
            page = await market.public.research({"data": "financials", "symbols": ["AAPL"], "metrics": [REVENUE],
                                                 "start": "2024-09-28", "end": "2024-09-28"})
            fields = [c["name"] for c in page["columns"]]
            assert ("2024-09-28", "391035000000") in [
                (r[fields.index("periodEnd")], r[fields.index("value")]) for r in page["rows"]]
        finally:
            await market.close()
    run(scenario())
