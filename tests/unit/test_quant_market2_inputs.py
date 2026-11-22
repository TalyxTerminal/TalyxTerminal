"""Direct Market 2.0 boundary rules: adjustment, export identity, clocks and exact native values."""
import asyncio
from pathlib import Path
from types import SimpleNamespace

import pandas as pd
import pyarrow as pa
import pyarrow.parquet as pq
import pytest
from talyx_sdk import ContractError

from modules.quant.backend import market_capture as C
from modules.quant.backend import market_inputs as M
from modules.quant.backend import object_data as O
from modules.quant.backend import research_data as R
from modules.quant.backend.materials import ExportFiles, read_selection, run_task
from tests.fixtures.quant_index import frozen_index
from tests.unit.quant_runtime_fakes import adapt
from vocabulary.instruments import MarketInstrument


def stock(symbol="AAPL", tag="a"):
    return MarketInstrument(identityVersion=2, instrumentId="vi2_" + tag * 32, venueId="US", symbol=symbol,
        displaySymbol=symbol, marketType="spot", assetClass="equity", securityType="common_stock",
        calendarId="XNYS", currency="USD", adjustmentEligible=True).to_dict()


def long_file(path, instruments, closes, *, times=None, closed=None, known=None):
    count = len(instruments)
    times = times or [1704171600000000000] * count  # local midnight Jan 2, 2024
    closed = closed or [1704229200000000000] * count  # 21:00 UTC
    known = known or [value + 900_000_000_000 for value in closed]
    values = {"instrumentId": [item["instrumentId"] for item in instruments],
              "time": pa.array(times, type=pa.timestamp("ns", tz="UTC")),
              "closeTime": pa.array(closed, type=pa.timestamp("ns", tz="UTC")),
              "knownAt": pa.array(known, type=pa.timestamp("ns", tz="UTC")),
              **{field: pa.array(closes, type=pa.float64()) for field in M.FIELDS[:4]},
              "volume": pa.array([100.] * count)}
    pq.write_table(pa.table(values), path)
    return path


def handed(path):
    """The FileRef Market's export hands over for ``path``."""
    import hashlib
    data = Path(path).read_bytes()
    return {"path": str(path), "sha256": hashlib.sha256(data).hexdigest(), "bytes": len(data)}


def kept(ctx):
    """The handed-over files Quant still keeps a copy of."""
    return sorted(item.name for item in ctx.files.iterdir() if "." not in item.name)


def selection(instrument, path, *, adjusted=True):
    return {"instrument": instrument, "series": M.price_series(instrument, "1d", adjusted),
            "files": [{"path": str(path)}], "version": 7, "source": {"sourceId": "fixture", "name": "Fixture"},
            "coverage": {"exportedRows": 1, "exportedRange": {"start": 1704171600000, "end": 1704171600000}, "missingBars": 0}}


@pytest.mark.parametrize("raw,adjusted,change", [([100., 98.], [98., 98.], -.02), ([100., 50.], [50., 50.], -.5)])
def test_explicit_adjustment_and_research_freeze_keep_raw_economics(tmp_path, raw, adjusted, change):
    instrument = stock()
    day = 86_400_000_000_000
    times = [1704171600000000000, 1704171600000000000 + day]
    closes = [1704229200000000000, 1704229200000000000 + day]
    raw_path, adjusted_path = tmp_path / "raw.parquet", tmp_path / "adjusted.parquet"
    long_file(raw_path, [instrument] * 2, raw, times=times, closed=closes)
    long_file(adjusted_path, [instrument] * 2, adjusted, times=times, closed=closes)
    original = selection(instrument, raw_path, adjusted=False)
    selected = selection(instrument, adjusted_path)
    assert original["series"]["adjustment"] == "raw"
    assert selected["series"]["adjustment"] == "split_dividend"
    assert M.price_series(instrument, "1d", O.stock_like(instrument))["adjustment"] == "split_dividend"
    R.write_bars([selected], {instrument["instrumentId"]: original}, ["AAPL"], tmp_path / "frozen.parquet", "1d", "America/New_York", None)
    frozen = pd.read_parquet(tmp_path / "frozen.parquet")
    assert frozen.close.tolist() == adjusted and frozen.rawClose.tolist() == raw
    assert frozen.close.pct_change().iloc[1] == 0
    assert frozen.rawClose.pct_change().iloc[1] == pytest.approx(change)


def test_names_read_from_one_export_file_open_it_once_and_each_gets_its_own_rows(tmp_path):
    """Market writes one instrument per row group; many names read through one ExportFiles get exactly their rows."""
    a, b, c = stock(), stock("MSFT", "b"), stock("NVDA", "c")
    path = tmp_path / "long.parquet"
    for name, instruments, closes in (("a", [a, a], [10., 11.]), ("b", [b], [20.]), ("mixed", [c, a], [30., 12.])):
        long_file(tmp_path / f"{name}.parquet", instruments, closes)
    tables = [pq.read_table(tmp_path / f"{name}.parquet") for name in ("a", "b", "mixed")]
    with pq.ParquetWriter(path, tables[0].schema) as writer:
        for table in tables:
            writer.write_table(table, row_group_size=table.num_rows)
    with ExportFiles() as files:
        assert read_selection(selection(a, path), files).close.tolist() == [10., 11., 12.]
        assert read_selection(selection(b, path), files).close.tolist() == [20.]
        assert read_selection(selection(c, path), files).close.tolist() == [30.]
        assert len(files.opened) == 1
        assert read_selection(selection(stock("AMD", "d"), path), files).empty
    assert files.opened == {}


def test_long_export_is_split_by_identity_not_file_or_request_order(tmp_path):
    a, b = stock(), stock("MSFT", "b")
    path = tmp_path / "long.parquet"
    long_file(path, [b, a], [20., 10.])
    assert read_selection(selection(a, path)).close.tolist() == [10.]
    assert read_selection(selection(b, path)).close.tolist() == [20.]
    inventory = C.file_inventory([{"path": str(path), "content": "ohlcv:1d:split_dividend"}])
    assert inventory[("ohlcv:1d:split_dividend", a["instrumentId"])]["start"] == 1704171600000


def test_ns_boundary_and_native_event_availability_are_distinct(tmp_path):
    instrument, path = stock(), tmp_path / "export.parquet"
    long_file(path, [instrument], [98.])
    chosen = selection(instrument, path)
    close = 1704229200000
    terms = {"instrumentId": instrument["instrumentId"], "priceIncrement": "0.01", "stock": {
        "acquiredAt": "2024-01-01T00:00:00Z", "commission": {},
        "sessions": [{"sessionDate": "2024-01-02", "lower": None, "upper": None, "priceIncrement": "0.01", "priceMap": {"scale": "1", "offset": "0", "maxError": "0"}, "openAt":"2024-01-02T14:30:00Z","closeAt":M.timestamp(close), "adjustedOHLC": ["98"] * 4}],
        "corporateActions": [{"effectiveTime":"2024-01-01","publishedAt":"2024-01-01T00:00:00Z"}]}}
    converted = M.stock_times(terms)
    assert converted["stock"]["acquiredAt"] == 1704067200000
    assert converted["stock"]["sessions"][0]["closeAt"] == close
    assert converted["stock"]["corporateActions"][0]["effectiveTime"] == 1704067200000
    assert converted["stock"]["corporateActions"][0]["publishedAt"] == 1704067200000
    assert terms["stock"]["acquiredAt"] == "2024-01-01T00:00:00Z"
    def write(name, table):
        pq.write_table(table, tmp_path / f"{name}.parquet")

    with ExportFiles() as exports:
        O.write_strategy_input(chosen, {}, {}, {}, converted, exports, write, None, close, [(1704171600000, close)])
    sessions = pd.read_parquet(tmp_path / "stock-sessions.parquet")
    assert sessions.closeAt.tolist() == [close]
    bars = pd.read_parquet(tmp_path / "bars.parquet")
    assert bars.ts_event.tolist() == [1704229200000000000]
    assert bars.ts_init.tolist() == [1704230100000000000]
    assert R.labels_of(pd.Series([1704171600000]), "1d", "America/New_York").tolist() == [1704153600000]
    local = pd.Timestamp("2026-11-09T16:00:00Z")
    assert R.labels_of(pd.Series([local.value // M.MS]), "1d", "Asia/Shanghai").tolist() == [pd.Timestamp("2026-11-10T00:00:00Z").value // M.MS]


def test_native_decimal_text_is_fixed():
    frame = pd.DataFrame({"open": [1e-8, -1e-8, 1.23, 0.]})
    strings = M.decimal_columns(frame, ("open",)).open.tolist()
    assert strings == ["0.00000001", "-0.00000001", "1.23000000", "0.00000000"]


def test_reused_symbol_is_two_research_columns_and_native_identities():
    a, b = stock("XYZ", "a"), stock("XYZ", "b")
    columns = R.column_names([a, b])
    assert len(set(columns)) == 2
    frame = pd.DataFrame({field: [100.] for field in M.FIELDS})
    terms = {"priceIncrement": "0.01", "stock": {"commission": {}}}
    first = O.instrument_terms(a, frame, terms, execution_venue="SIM_USD")[0]
    second = O.instrument_terms(b, frame, terms, execution_venue="SIM_USD")[0]
    assert first["id"] != second["id"]


def test_one_funding_settlement_is_charged_once_at_market_time(monkeypatch):
    import sys

    from modules.quant.native import metrics
    monkeypatch.setitem(sys.modules, "native.metrics", metrics)
    from modules.quant.native import strategy_costs
    class Money:
        def __init__(self, amount, currency):
            self.amount, self.currency = amount, currency
    monkeypatch.setitem(sys.modules, "nautilus_trader.backtest", SimpleNamespace(SimulationModule=object))
    monkeypatch.setitem(sys.modules, "nautilus_trader.model", SimpleNamespace(Money=Money))
    at = 1704096000000  # 2024-01-01 08:00 UTC
    rows = [{"instrument": "BTC", "time": M.ms(str(at * M.MS)), "rate": .0001}]
    rates = strategy_costs.funding_rates(rows)
    assert rates["BTC"][0] == [at * M.MS]
    ledger = SimpleNamespace(entries=[])
    module = strategy_costs.simulation_module({"funding": True}, SimpleNamespace(close=lambda *args: 100_000), ledger, rates, {"BTC": "perpetual"})
    ctx = SimpleNamespace(positions=[SimpleNamespace(instrument_id="BTC", signed_qty=1, settlement_currency="USDT")])
    assert module.process(at * M.MS - 1, ctx) is None
    result = module.process(at * M.MS, ctx)
    assert result[0].amount == -10
    assert module.process(at * M.MS, ctx) is None
    assert module.process(at * M.MS + 1, ctx) is None
    assert ledger.entries == [{"t": at * M.MS, "instrument": "BTC", "kind": "funding", "amount": 10., "currency": "USDT", "rate": .0001}]


def test_task_progress_and_failure_preserve_market_words():
    heard, cancelled = [], []
    async def call(*args):
        return {"taskId": "t"}
    async def get(args):
        return {"state": "failed", "progress": {
            "done": 0, "total": 1, "waitingFor": "Yahoo", "waitingUntil": "1704067200000000000", "message": "rate limit"},
            "result": None, "error": {"code": "source_denied", "message": "original source text", "details": {"status": 451}}}
    async def cancel(args):
        cancelled.append(args)
    ctx = adapt(SimpleNamespace(actions=SimpleNamespace(call=call), platform=SimpleNamespace(tasks=SimpleNamespace(get=get, cancel=cancel))))
    with pytest.raises(ContractError) as error:
        asyncio.run(run_task(ctx, "market.fetch", {}, heard.append))
    assert (error.value.code, error.value.message, error.value.details) == ("source_denied", "original source text", {"status": 451})
    assert heard[0]["waitingFor"] == "Yahoo" and heard[0]["waitingUntil"] == "1704067200000000000"


def test_task_cancellation_reaches_market_even_during_submission():
    cancelled = []
    async def run():
        entered = asyncio.Event()
        async def call(*args):
            entered.set()
            await asyncio.sleep(.01)
            return {"taskId": "t"}
        async def cancel(args):
            cancelled.append(args)
        ctx = adapt(SimpleNamespace(actions=SimpleNamespace(call=call), platform=SimpleNamespace(tasks=SimpleNamespace(cancel=cancel))))
        task = asyncio.create_task(run_task(ctx, "market.fetch", {}))
        await entered.wait()
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
    asyncio.run(run())
    assert cancelled == [{"taskId": "t"}]


@pytest.mark.parametrize("allow_empty", [False, True])
def test_export_failure_is_fatal_for_a_list_and_retained_for_index_exclusion(tmp_path, monkeypatch, allow_empty):
    a, b = stock(), stock("MSFT", "b")
    path = tmp_path / "export.parquet"
    long_file(path, [a], [10.])
    problem = {"code": "source_denied", "message": "source says unavailable", "retryable": False}
    requests = []
    reference = handed(path)
    async def fetch(*args):
        return []
    async def revisions(ctx, selections, until):
        return {item["series"]["seriesId"]: {"revision": 3, "source": {"sourceId": "fixture"}} for item in selections}
    async def task(ctx, name, args, progress):
        requests.append(args)
        return {"files": [{"content": "ohlcv:1d:split_dividend", "artifact": reference}],
                "gaps": [], "failures": [{"instrumentId": b["instrumentId"], "problem": problem}]}
    ctx = adapt(SimpleNamespace(), tmp_path / "quant")
    monkeypatch.setattr(C, "fetch", fetch)
    monkeypatch.setattr(M, "revisions", revisions)
    monkeypatch.setattr(C, "run_task", task)
    request = {"selections": [{"instrument": item, "series": M.price_series(item, "1d", True)} for item in (a, b)],
               "since": 0, "until": 1705000000000, "allowEmpty": allow_empty}
    if allow_empty:
        result = asyncio.run(C.capture(ctx, request, "run"))
        failed = result["manifest"]["selections"][1]
        assert failed["problem"] == problem and failed["coverage"]["exportedRows"] == 0
        assert read_selection(result["manifest"]["selections"][0]).close.tolist() == [10.]
        assert kept(ctx) == [reference["sha256"]]     # Quant's own copy while the run holds it
        asyncio.run(C.release(ctx, result["references"], "run"))
    else:
        with pytest.raises(ContractError) as error:
            asyncio.run(C.capture(ctx, request, "run"))
        assert error.value.code == problem["code"] and error.value.message == "MSFT: source says unavailable"
    assert kept(ctx) == []                             # let go once the run is done or failed
    assert requests[0]["fill"] == "none"


def test_revision_change_reexports_and_releases_the_superseded_artifact(tmp_path, monkeypatch):
    instrument, path = stock(), tmp_path / "export.parquet"
    long_file(path, [instrument], [50.])
    reads, exports = [], []
    references = [handed(path), handed(long_file(tmp_path / "second.parquet", [instrument], [51.]))]
    async def revisions(ctx, selections, until):
        reads.append(None)
        return {item["series"]["seriesId"]: {"revision": 1 if len(reads) == 1 else 2, "source": {"sourceId": "fixture"}} for item in selections}
    async def task(ctx, name, args, progress):
        exports.append(None)
        return {"files": [{"content": "ohlcv:1d:split_dividend", "artifact": references[len(exports) - 1]}],
                "gaps": [], "failures": []}
    monkeypatch.setattr(M, "revisions", revisions)
    monkeypatch.setattr(C, "run_task", task)
    ctx = adapt(SimpleNamespace(), tmp_path / "quant")
    result = asyncio.run(C.capture(ctx, {"selections": [{"instrument": instrument, "series": M.price_series(instrument, "1d", True)}],
                          "since": 0, "until": 1705000000000, "fill": "none"}, "run"))
    assert len(exports) == 2 and kept(ctx) == [references[1]["sha256"]]   # the superseded export was let go
    assert result["manifest"]["selections"][0]["version"] == 2
    assert result["references"][0]["sha256"] == references[1]["sha256"]
    asyncio.run(C.release(ctx, result["references"], "run"))
    assert kept(ctx) == []


@pytest.mark.parametrize("stage", ["export", "terms"])
@pytest.mark.parametrize("indexed", [False, True])
def test_failed_member_is_five_requested_four_traded_for_a_list_as_for_an_index(tmp_path, monkeypatch, stage, indexed):
    from unittest.mock import AsyncMock

    from test_quant_object_trading_terms import INDEX, prepared_context
    from test_quant_object_trading_terms import stock as named_stock

    from modules.quant.backend import index_universe
    from modules.quant.backend.strategy_views import left_out

    instruments = [named_stock("S" + str(index)) for index in range(5)]
    bad = instruments[-1]
    problem = {"code": "source_denied", "message": "S4 unavailable from fixture"}
    ctx = prepared_context(tmp_path, instruments)
    if stage == "export":
        original = ctx.export
        async def export(args):
            result = await original({**args, "series": [item for item in args["series"] if item["instrumentId"] != bad["instrumentId"]]})
            result["failures"] = [{"instrumentId": bad["instrumentId"], "problem": problem}]
            return result
        ctx.export = export
    else:
        market = ctx.require("market.instruments")
        original = market.terms
        async def terms(args):
            if bad["instrumentId"] in (args.get("symbols") or [s["instrumentId"] for s in args["series"]]):
                raise ContractError(problem["code"], problem["message"])
            return await original(args)
        market.terms = terms
    data = {"symbols": [{"instrument": item} for item in instruments], "timeframe": "1d", "since": "1970-01-01", "until": "1970-01-01"}
    if indexed:
        data = {**data, "index": {"instrument": INDEX}}
        index = {"selection": data["index"], "since": 0, "until": 86_399_999, "instruments": instruments, "exclusions": [],
                 "unmappedCount": 0, "intervals": [{"since": 0, "until": 86_399_999,
                    "members": [{"assetId": item["instrumentId"], "instrumentId": item["instrumentId"], "symbol": item["displaySymbol"]} for item in instruments]}]}
        index = frozen_index(index)
        monkeypatch.setattr(index_universe, "freeze", AsyncMock(return_value=index))
    result = asyncio.run(O.prepare(ctx, {"data": data, "account": {"capital": 1000}}, "run", tmp_path))
    summary = result["summary"]
    assert (summary["requested"], summary["traded"]) == (5, 4)
    assert summary["skipped"] == [{"symbol": "S4", "reason": problem["message"], "code": problem["code"]}]
    assert left_out({"data": summary}) == ["1 of 5 names have no prices: S4 unavailable from fixture"]


def test_cancellation_during_keeping_a_file_still_lets_it_go(tmp_path):
    from modules.quant.backend.materials import acquire, release
    reference = handed(long_file(tmp_path / "export.parquet", [stock()], [10.]))
    held = []
    async def run():
        ctx = adapt(SimpleNamespace(), tmp_path / "quant")
        entered = asyncio.Event()
        keep = ctx.keep
        async def slow_keep(value, owner):
            kept_now = await keep(value, owner)
            entered.set()
            await asyncio.sleep(.01)
            return kept_now
        ctx.keep = slow_keep
        async def capture():
            try:
                await acquire(ctx, reference, "run", held)
            finally:
                await release(ctx, held, "run")
        task = asyncio.create_task(capture())
        await entered.wait()
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        return ctx
    ctx = asyncio.run(run())
    assert kept(ctx) == []


@pytest.mark.parametrize("delay_minutes", [0, 15])
def test_a_bar_is_found_at_the_moment_the_engine_has_it(monkeypatch, delay_minutes):
    """the engine's clock runs on when a bar is known (its close plus the source's publication delay); the fill model looked bars up by
    their close, so with any delay every market order found "no bar" and was refused as halted. Hourly bars of A and B, B missing its fourth:
    an order sent when a bar is known fills on the next bar known, and B has none at that moment only where it is halted."""
    import sys

    from modules.quant.native import metrics
    monkeypatch.setitem(sys.modules, "native.metrics", metrics)
    from modules.quant.native import strategy_costs
    hour, delay = 3_600_000_000_000, delay_minutes * 60_000_000_000
    closes = [1_704_070_800_000_000_000 + hour * k for k in range(6)]
    rows = [{"instrument": name, "barType": name + ".OKX-1-HOUR-LAST-EXTERNAL", "ts_event": str(close), "ts_init": str(close + delay), "open": 1, "high": 1, "low": 1, "close": 1, "volume": 1}
            for name in ("A", "B") for k, close in enumerate(closes) if not (name == "B" and k == 3)]
    prices = strategy_costs.Prices(pd.DataFrame(rows), 365)
    known = [close + delay for close in closes]
    for k in range(5):
        assert prices.next_moment("A", known[k]) == known[k + 1]
        assert prices.at("A", known[k + 1]) == k + 1
    assert prices.next_moment("B", known[2]) == known[3] and prices.at("B", known[3]) is None  # halted for that hour
    assert prices.at("B", known[4]) == 3
    assert prices.close("A", closes[2]) == 1.0 and prices.last("A", closes[2] - 1) == 1  # what a bar closed at is still read by its close


def test_a_fill_is_timed_by_the_open_of_the_bar_it_filled_in(monkeypatch):
    """a fill was stamped with the close of its bar (when the engine had it) while its price was the open: Work said 23:00 for an order
    filled at the 22:00 open. Each bar knows its open moment: its close less its length, or a stock's session open."""
    import sys

    from modules.quant.native import metrics
    monkeypatch.setitem(sys.modules, "native.metrics", metrics)
    from modules.quant.native import strategy_costs
    hour = 3_600_000_000_000
    closes = [1_731_884_400_000_000_000 + hour * k for k in range(3)]  # 2024-11-17 23:00 UTC and the next two hours
    rows = [{"instrument": "BTC", "barType": "BTCUSDT-PERP.OKX-1-HOUR-LAST-EXTERNAL", "ts_event": str(at), "ts_init": str(at), "open": 1, "high": 1, "low": 1,
             "close": 1, "volume": 1} for at in closes]
    session = [{"instrument": "AAPL", "barType": "AAPL.XNAS-1-DAY-LAST-EXTERNAL", "ts_event": "1731963600000000000", "ts_init": "1731963600000000000",
                "open": 1, "high": 1, "low": 1, "close": 1, "volume": 1}]  # 2024-11-18 21:00 UTC, the session opened 14:30 UTC
    prices = strategy_costs.Prices(pd.DataFrame(rows + session), 365, {"AAPL": {1731963600000000000: 1731940200000000000}})
    assert list(prices.by["BTC"]["opened"]) == [at - hour for at in closes]
    assert list(prices.by["AAPL"]["opened"]) == [1731940200000000000]
    assert strategy_costs.bar_length("X-5-MINUTE-LAST-EXTERNAL") == 300_000_000_000 and strategy_costs.bar_length("X-1-DAY-LAST-EXTERNAL") == 86_400_000_000_000
