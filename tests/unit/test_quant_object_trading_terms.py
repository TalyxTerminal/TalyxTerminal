"""A run's fees and size steps come from Market's trading terms; the account's own fee wins; nothing is made up."""
import asyncio
import hashlib
import time
from types import SimpleNamespace

import pandas as pd
import pytest
from talyx_sdk import ContractError

from modules.quant.backend import object_data
from tests.unit.quant_runtime_fakes import adapt
from tests.unit.test_quant_object_runs import INDEX, FakeContext
from vocabulary.instruments import MarketInstrument

PERP = MarketInstrument(venueId="binance", symbol="BTC/USDT:USDT", displaySymbol="BTC/USDT:USDT", base="BTC", quote="USDT",
                        settle="USDT", marketType="swap", assetClass="crypto", contractOrientation="linear").to_dict()
SPOT = MarketInstrument(venueId="binance", symbol="BTC/USDT", displaySymbol="BTC/USDT", base="BTC", quote="USDT",
                        marketType="spot", assetClass="crypto").to_dict()
def stock(symbol, *, mic="XNAS", currency_field="currency"):
    return MarketInstrument(identityVersion=2, instrumentId="vi2_" + hashlib.md5(symbol.encode()).hexdigest(), venueId="US", mic=mic,
                            symbol=symbol, displaySymbol=symbol, marketType="spot", assetClass="equity", securityType="common_stock",
                            adjustmentEligible=True, calendarId=mic, **{currency_field: "USD"}).to_dict()


STOCK = stock("AAPL")


def terms(instrument, *, fees=(0.0002, 0.0005), increment=0.001, unit="base", multiplier=1.0):
    return {"instrumentId": instrument["instrumentId"], "fees": {"maker": fees[0], "taker": fees[1], "basis": "notional"} if fees else None,
            "minQuantity": increment, "quantityIncrement": increment, "quantityUnit": unit,
            "contractMultiplier": multiplier, "priceIncrement": 0.1,
            "unavailable": {} if fees else {"fees": "gate lists no fee and the venue table has none for swap"},
            **({"stock": {"version": 1, "currency": "USD", "orderSize": {"minimum": 1, "increment": 1, "oddLotSellAll": False},
                          "settlement": {"sellAfterTradingDays": 0, "calendarId": instrument["calendarId"]},
                          "commission": {"rate": "0", "perShare": "0.005", "minimum": "1", "maximumRate": "0.01"},
                          "taxes": [], "limitations": [], "sessions": [], "corporateActions": []}} if instrument["assetClass"] == "equity" else {})}


def bars(volume="1.5"):
    return pd.DataFrame({"open": [100.5], "high": [101.0], "low": [99.5], "close": [100.0], "volume": [float(volume)]})


def test_large_universe_respects_the_public_terms_page_limit():
    selected = [stock(f"S{n}") for n in range(201)]
    calls = []

    async def read(args):
        assert len(args['symbols']) <= 100
        calls.append(args)
        return {'items':[{'instrumentId':id} for id in args['symbols']]}

    result = asyncio.run(object_data.trading_terms(adapt(SimpleNamespace(require=lambda name:SimpleNamespace(terms=read))),selected))
    assert set(result) == {item['instrumentId'] for item in selected}
    assert len(calls) == 3


@pytest.mark.parametrize("code", ["history_unavailable", "not_found", "source_unavailable", "network_unreachable", "invalid_input"])
@pytest.mark.parametrize("indexed", [False, True])
def test_dated_terms_fail_explicit_lists_and_disclose_failed_index_members(code, indexed):
    calls = []

    async def read(args):
        assert "symbols" not in args and len(args["series"]) == 1
        assert args["start"] == "1970-01-01T00:00:00Z" and args["end"] == "1970-01-01T00:00:00.100000Z"
        calls.append(args)
        raise ContractError(code, "source result")

    index = {"exclusions": [], "intervals": [{"since": 0, "until": 100, "members": [{"assetId": STOCK["instrumentId"], "instrument": STOCK}]}]} if indexed else None
    ctx = adapt(SimpleNamespace(require=lambda name: SimpleNamespace(terms=read)))
    request = object_data.trading_terms(ctx, [STOCK], selections=[{"instrument": STOCK, "series": {"instrumentId": STOCK["instrumentId"]}}],
        since=0, until=100, allow_missing_prices=indexed, index=index)
    if indexed:
        assert asyncio.run(request) == {}
        assert index["exclusions"][0]["sourceReason"] == "source result"
    else:
        with pytest.raises(ContractError) as caught:
            asyncio.run(request)
        assert caught.value.code == code
    assert len(calls) == 1


def native(instrument, market, frame=None):
    return object_data.instrument_terms(instrument, bars() if frame is None else frame, market)


@pytest.mark.parametrize("market_tick,expected", [("0.05", "0.0500"), (None, "0.0001")])
def test_price_precision_preserves_finer_observations_and_the_exchange_tick(market_tick, expected):
    frame = bars().assign(high=101.1234)
    item, _ = native(SPOT, {**terms(SPOT), "priceIncrement": market_tick}, frame)
    assert (item["pricePrecision"], item["priceIncrement"]) == (4, expected)


def test_index_stocks_share_one_cash_account_without_losing_listing_identity():
    second = stock("IBM", mic="XNYS")
    entries = []
    for item in [STOCK,second]:
        market = terms(item)
        value, venue = object_data.instrument_terms(item,bars('100'),market,execution_venue='SIM_USD')
        assert venue == 'SIM_USD' and item['venueId'].upper() in value['symbol']
        entries.append((value,market,item['displaySymbol']))
    venues = object_data.venue_for({'SIM_USD':entries},{'capital':1000})
    assert len(venues) == 1 and venues[0]['balances'] == ['1000 USD']
    assert len({value['id'] for value,_,_ in entries}) == 2


def test_missing_dividend_price_evidence_is_disclosed_with_the_source_reason():
    async def read(args):
        raise ContractError('history_unavailable', 'ABC: no previous source session for dividend share basis', {'missingPriceEvidence': True})
    index = {'exclusions': [], 'intervals': [{'since': 0, 'until': 100,
        'members': [{'assetId': STOCK['instrumentId'], 'instrument': STOCK}]}]}
    ctx = adapt(SimpleNamespace(require=lambda _: SimpleNamespace(terms=read)))
    result = asyncio.run(object_data.trading_terms(ctx, [STOCK], selections=[{'instrument': STOCK, 'series': {}}],
        since=0, until=100, allow_missing_prices=True, index=index))
    assert result == {}
    row = index['exclusions'][0]
    assert row['since'] == 0 and row['until'] == 100 and row['kind'] == 'missing_price_evidence'
    assert row['sourceReason'] == 'ABC: no previous source session for dividend share basis'


def test_an_index_member_whose_price_evidence_market_refuses_is_left_out_not_failing_the_run():
    async def read(args):
        raise ContractError('unavailable', 'ABC 2025-09-15: Yahoo dividend share basis does not match its adjusted price evidence')
    index = {'exclusions': [], 'intervals': [{'since': 0, 'until': 100,
        'members': [{'assetId': STOCK['instrumentId'], 'instrument': STOCK}]}]}
    ctx = adapt(SimpleNamespace(require=lambda _: SimpleNamespace(terms=read)))
    result = asyncio.run(object_data.trading_terms(ctx, [STOCK], selections=[{'instrument': STOCK, 'series': {}}],
        since=0, until=100, allow_missing_prices=True, index=index))
    assert result == {}
    row = index['exclusions'][0]
    assert row['kind'] == 'missing_price_evidence' and 'does not match' in row['sourceReason']
    # Outside an index the refusal stops the whole run.
    with pytest.raises(ContractError):
        asyncio.run(object_data.trading_terms(ctx, [STOCK], selections=[{'instrument': STOCK, 'series': {}}], since=0, until=100))


def prepared_context(tmp_path, instruments):
    """Market's task export is a long table; real Materials/capture splits it by identity."""
    market_terms = {item["instrumentId"]: terms(item) for item in instruments}
    for value in market_terms.values():
        value["stock"]["sessions"] = [{"openAt": "1970-01-01T00:00:00Z", "closeAt": "1970-01-01T00:00:00.001Z", "adjustedOHLC": ["100.5", "101", "99.5", "100"]}]
    market = Market(market_terms)
    artifacts = {}

    async def export(args):
        selections = args["series"]
        frames = []
        for selected in reversed(selections):
            instrument = next(i for i in instruments if i["instrumentId"] == selected["instrumentId"])
            frame = bars("100").assign(instrumentId=instrument["instrumentId"], market=instrument["venueId"], code=instrument["symbol"])
            frame["time"] = pd.to_datetime([0], unit="ns", utc=True)
            frame["closeTime"] = pd.to_datetime([1_000_000], unit="ns", utc=True)
            frame["knownAt"] = pd.to_datetime([1_000_000], unit="ns", utc=True)
            frames.append(frame)
        path = tmp_path / f"market-export-{len(artifacts)}.parquet"   # a handed-over file is never written again
        pd.concat(frames, ignore_index=True).to_parquet(path, index=False)
        ref = {"path": str(path), "sha256": hashlib.sha256(path.read_bytes()).hexdigest(), "bytes": path.stat().st_size}
        artifacts[str(path)] = path
        series = selections[0]["series"]
        return {"files": [{"content": f"ohlcv:{series['timeframe']}:{series['adjustment']}", "artifact": ref}], "failures": [], "gaps": []}

    ctx = FakeContext(tmp_path, export)
    ctx.require = lambda name: market
    original = ctx.platform.tasks.get

    async def progress(args):
        result = await original(args)
        if result["state"] == "completed" and result["result"].get("state") == "completed":
            result.update(progress={"done": len(instruments), "total": len(instruments),
                          "waitingFor": None, "waitingUntil": None, "message": "Fetched"})
        return result
    ctx.platform.tasks.get = progress
    return ctx


@pytest.mark.parametrize("currency_field", ["quote", "currency"])
def test_index_stock_preparation_uses_the_available_settlement_currency(tmp_path, monkeypatch, currency_field):
    from unittest.mock import AsyncMock

    from modules.quant.backend import index_universe

    instrument = stock("ABC", currency_field=currency_field)
    index = {"selection": {"instrument": INDEX}, "since": 0, "until": 86_399_999,
             "instruments": [instrument], "exclusions": [], "intervals": [], "unmappedCount": 0, "limitation": "Free source"}
    monkeypatch.setattr(index_universe, "freeze", AsyncMock(return_value=index))
    ctx = prepared_context(tmp_path, [instrument])
    settings = {"data": {"index": index["selection"], "timeframe": "1d", "since": "1970-01-01", "until": "1970-01-01"}, "account": {"capital": 1234}}
    heard = []
    result = asyncio.run(object_data.prepare(ctx, settings, "run", tmp_path, report=lambda *args, **kwargs: heard.append(args)))
    assert result["venues"][0]["name"] == "SIM_USD"
    # The data stage reports each real step after the trading terms too: the coverage check and writing the backtest inputs.
    assert [step for step in dict.fromkeys(step for step, *_ in heard)] == ["names", "prices", "terms", "coverage", "inputs"]
    assert ("prices", 1, 1, []) in heard and ("terms", 1, 1, []) in heard
    assert result["venues"][0]["balances"] == ["1234 USD"]
    # The whole membership stays with the fixed run; the run's record gets the index's summary when the run is prepared (object_runs).
    assert "index" not in result["summary"] and result["index"] is index
    assert result["instruments"][0]["currency"] == "USD"
    native_id = pd.read_parquet(result["barsPath"])["instrument"].item()
    assert native_id.startswith("ABC-US-") and native_id.endswith(".SIM_USD")
    assert result["sources"][0]["marketInstrument"]["instrumentId"] == instrument["instrumentId"]


def test_stocks_of_one_currency_on_two_exchanges_are_one_account(tmp_path):
    """Different listing exchanges still trade against the single configured USD capital."""
    nasdaq, nyse = stock("ABC"), stock("DEF", mic="XNYS")
    ctx = prepared_context(tmp_path, [nasdaq, nyse])
    settings = {"data": {"symbols": [{"instrument": nasdaq}, {"instrument": nyse}], "timeframe": "1d", "since": "1970-01-01", "until": "1970-01-01"}, "account": {"capital": 1234}}
    result = asyncio.run(object_data.prepare(ctx, settings, "run", tmp_path))
    assert [item["name"] for item in result["venues"]] == ["SIM_USD"] and result["venues"][0]["balances"] == ["1234 USD"]
    assert len({item["id"] for item in result["instruments"]}) == 2
    assert {item["marketInstrument"]["mic"] for item in result["sources"]} == {"XNAS", "XNYS"}


def test_the_size_step_is_the_markets_quantity_step_never_finer_than_the_data():
    spot, _ = native(SPOT, terms(SPOT, increment=1e-05))
    assert (spot["sizePrecision"], spot["sizeIncrement"]) == (5, "0.00001")
    coarse, _ = native(SPOT, terms(SPOT, increment=0.1))
    assert (coarse["sizePrecision"], coarse["sizeIncrement"]) == (1, "0.1")
    finer_data, _ = native(SPOT, terms(SPOT, increment=0.1), bars("0.12345678"))
    assert (finer_data["sizePrecision"], finer_data["sizeIncrement"]) == (8, "0.00000001")
    integer_step, _ = native(SPOT, terms(SPOT, increment=1.0), bars('100'))
    assert (integer_step['sizePrecision'],integer_step['sizeIncrement']) == (0,'1')
    unknown, _ = native(SPOT, {**terms(SPOT), "quantityIncrement": None})
    assert (unknown["sizePrecision"], unknown["sizeIncrement"]) == (6, "0.000001")
    equity, _ = native(STOCK, terms(STOCK, fees=(0.0, 0.0), increment=1.0, unit="shares"), pd.DataFrame({"open": [10.0], "high": [11.0], "low": [9.0], "close": [10.0], "volume": [100.0]}))
    assert (equity["sizePrecision"], equity["sizeIncrement"]) == (0, "1")


def test_a_perpetual_counted_in_contracts_is_sized_in_base_coins_by_contract_steps_and_an_unknown_unit_is_refused():
    gate, _ = native(PERP, terms(PERP, unit="contracts", multiplier=0.0001, increment=1.0))
    assert (gate["sizePrecision"], gate["sizeIncrement"]) == (4, "0.0001")
    okx, _ = native(PERP, terms(PERP, unit="contracts", multiplier=0.01, increment=0.01))
    assert (okx["sizePrecision"], okx["sizeIncrement"]) == (4, "0.0001")
    whole, _ = native(PERP, terms(PERP, unit="contracts", multiplier=10.0, increment=1.0), bars("2"))
    assert (whole["sizePrecision"], whole["sizeIncrement"]) == (0, "10")
    no_step = {**terms(PERP, unit="contracts", multiplier=0.01), "quantityIncrement": None, "unavailable": {"quantityIncrement": "the provider reports no precision"}}
    with pytest.raises(ContractError, match="no contract step"):
        native(PERP, no_step)
    unknown = {**terms(PERP), "quantityUnit": None, "unavailable": {"quantityUnit": "the provider reports no contract size"}}
    with pytest.raises(ContractError, match="unknown unit"):
        native(PERP, unknown)
    perpetual, venue = native(PERP, terms(PERP))
    assert perpetual["type"] == "perpetual" and venue == "BINANCE"


def entries(*pairs):
    return {"BINANCE": [(native(instrument, market)[0], market, instrument["displaySymbol"]) for instrument, market in pairs]}


def test_the_venue_fee_is_markets_and_the_taker_rate_reaches_every_traded_instrument():
    venues = entries((PERP, terms(PERP)))
    venue, = object_data.venue_for(venues, {})
    assert venue["fees"] == {"type": "maker_taker", "maker": 0.0002, "taker": 0.0005} and venue["accountType"] == "MARGIN"
    assert venues["BINANCE"][0][0]["takerFee"] == "0.0005"


def test_the_account_fee_wins_and_makes_a_missing_market_fee_irrelevant():
    venues = entries((SPOT, terms(SPOT, fees=None)))
    venue, = object_data.venue_for(venues, {"fee": {"maker": 0.0001, "taker": 0.0002}})
    assert venue["fees"]["taker"] == 0.0002 and venues["BINANCE"][0][0]["takerFee"] == "0.0002"


def test_no_fee_is_made_up_when_market_has_none_or_the_markets_disagree():
    with pytest.raises(ContractError, match=r"Market has no fees for BTC/USDT .*account settings") as error:
        object_data.venue_for(entries((SPOT, terms(SPOT, fees=None))), {})
    assert error.value.code == "unavailable"
    with pytest.raises(ContractError, match="different fees") as error:
        object_data.venue_for(entries((SPOT, terms(SPOT, fees=(0.001, 0.001))), (PERP, terms(PERP))), {})
    assert error.value.code == "invalid_input"


def test_stocks_keep_their_fixed_zero_commission():
    stock, _ = native(STOCK, terms(STOCK, fees=(0.0, 0.0), increment=1.0, unit="shares"), pd.DataFrame({"open": [10.0], "high": [11.0], "low": [9.0], "close": [10.0], "volume": [100.0]}))
    venue, = object_data.venue_for({"XNAS": [(stock, terms(STOCK, fees=(0.0, 0.0)), "AAPL")]}, {"fee": {"maker": 0.1, "taker": 0.1}})
    assert venue["fees"] == {"type": "instrument_conditions"} and "takerFee" not in stock


class Market:
    def __init__(self, listed):
        self.listed, self.asked = listed, []

    async def coverage(self, arguments):
        from modules.quant.backend.public_settings import timestamp
        row = {"capability":"ohlcv","timeframe":"1d","source":{"sourceId":"fixture","name":"Fixture"},"local":None,
               "available":{"periods":["1m","1d"],"earliest":None,"live":"stream"},"status":"current",
               "publishedThrough":timestamp(time.time()*1000)}
        return {"columns":[{"name":k} for k in row],"rows":[list(row.values())],"nextCursor":None}

    async def get(self, arguments):
        known = {i["instrumentId"]:i for i in [STOCK,PERP,SPOT,*arguments.get("records",[])]}
        items = []
        for value in arguments.get("records") or arguments["symbols"]:
            item = value if isinstance(value,dict) else known.get(value)
            items.append({"status":"found","symbol":item["symbol"],"instrument":item} if item else {"status":"not_found","symbol":value})
        return {"items":items,"nextCursor":None}

    async def read(self, arguments):
        return {"revision": 1, "source": {"sourceId": "fixture", "name": "Fixture"}}

    async def sessions(self, arguments):
        return {"calendarId": arguments["calendarId"], "timezone": "UTC", "mode": "exchange", "evidence": "verified",
                "coveredFrom": None, "coveredUntil": None, "sessions": []}

    async def bar_times(self, arguments):
        return {"evidence": "verified", "reason": None, "timezone": "UTC", "open": ["0"], "close": ["1000000"], "next": None}

    async def terms(self, arguments):
        ids = arguments.get("symbols") or [item["instrumentId"] for item in arguments["series"]]
        self.asked.append(ids)
        return {"items":[self.listed[id] for id in ids]}



def defaults(listed, account=None, instruments=(PERP,)):
    market = Market(listed)
    ctx = adapt(SimpleNamespace(require=lambda name: market))
    settings = {"data": {"symbols": [{"instrument": item} for item in instruments], "timeframe": "1h", "since": "2024-01-01", "until": "2024-02-01"}, "account": account or {}}
    return market, asyncio.run(object_data.defaults(ctx, settings))


def test_defaults_show_markets_fee_and_ask_with_the_shared_instrument_shape():
    market, result = defaults({PERP["instrumentId"]: terms(PERP)})
    assert result['instruments'][0]['venueLabel'] == 'binance'
    assert result["account"]["fee"] == {"maker": 0.0002, "taker": 0.0005} == result["account"]["defaults"]["fee"]
    asked, = market.asked
    assert asked == [PERP["instrumentId"]]


def test_defaults_keep_the_account_fee_and_still_show_markets_default():
    _, result = defaults({SPOT["instrumentId"]: terms(SPOT, fees=(0.001, 0.001))}, {"fee": {"maker": 0.0, "taker": 0.0}}, (SPOT,))
    assert result["account"]["fee"] == {"maker": 0.0, "taker": 0.0}
    assert result["account"]["defaults"]["fee"] == {"maker": 0.001, "taker": 0.001}


def test_defaults_for_stocks_expose_commission_material_and_unknown_crypto_fees_are_an_error():
    _, stocks = defaults({STOCK["instrumentId"]: terms(STOCK, fees=(0.0, 0.0), increment=1.0, unit="shares")}, instruments=(STOCK,))
    assert stocks['account']['fee'] is None
    assert stocks['instruments'][0]['tradingTerms']['stock']['commission']['minimum'] == '1'
    with pytest.raises(ContractError, match="Market has no fees"):
        defaults({PERP["instrumentId"]: terms(PERP, fees=None)})
    _, overridden = defaults({PERP["instrumentId"]: terms(PERP, fees=None)}, {"fee": {"maker": 0.0, "taker": 0.0001}})
    assert overridden["account"]["fee"]["taker"] == 0.0001


def test_stock_commission_overrides_are_frozen_separately_from_market_evidence():
    from modules.quant.backend.object_store import check_account
    market = terms(STOCK)
    native_terms, venue = native(STOCK, market, bars('100'))
    original = dict(market['stock']['commission'])
    override = {'rate':.001,'perShare':0,'minimum':2,'maximumRate':None}
    check_account({'stockCommission':override})
    object_data.venue_for({venue:[(native_terms,market,'AAPL')]},{'stockCommission':override})
    assert native_terms['stockCommission']['minimum'] == '2'
    assert market['stock']['commission'] == original
    for invalid in ({**override,'minimum':float('nan')},{**override,'rate':-1},{**override,'perShare':None}):
        with pytest.raises(ContractError,match='finite nonnegative'):
            check_account({'stockCommission':invalid})


def test_stock_series_explicitly_requests_split_and_dividend_adjustment():
    class Source:
        async def get(self, args):
            return {"items":[{"status":"found","symbol":"US:AAPL","instrument":STOCK}],"nextCursor":None}
    _, series = asyncio.run(object_data.resolve_series(adapt(SimpleNamespace(require=lambda name: Source())), STOCK, "1d"))
    assert series["adjustment"] == "split_dividend"
    assert "adjustmentAnchor" not in series  # the public series canonical form omits the latest anchor


def test_strategy_daily_bars_close_at_exchange_close_including_half_day():
    opened, closed = str(pd.Timestamp("2024-07-03T04:00Z").value), str(pd.Timestamp("2024-07-03T17:00Z").value)
    class Source:
        async def bar_times(self, args):
            assert args["start"] == opened and args["end"] == str(int(opened) + 999_999)
            return {"timezone": "America/New_York", "open": [opened], "close": [closed], "next": None, "evidence": "verified", "reason": None}
    times = pd.Series([int(opened) // 1_000_000])
    result = asyncio.run(object_data.close_times(adapt(SimpleNamespace(require=lambda name: Source())), STOCK, {"timeframe": "1d", "sessionScope": "regular"}, times))
    assert result.iloc[0] == int(closed)


def test_data_progress_is_kept_in_memory_and_read_while_the_data_is_fetched(tmp_path):
    # It was a file replaced while the page read it, and on Windows the replace was refused and failed the whole run.
    from modules.quant.backend.object_runs import ObjectRuns
    runs = ObjectRuns.__new__(ObjectRuns)
    runs.directory = lambda _identity: tmp_path
    runs.fetching = {}
    report = runs.data_progress('r')
    report('prices', 0, 617, [])
    report('prices', 617, 617, ['VRSK'])
    assert runs.fetching['r']['fetch'] == {'step': 'prices', 'done': 617, 'total': 617, 'leftOut': ['VRSK']}
    assert not (tmp_path / 'output').exists() and not list(tmp_path.glob('*progress*')), 'the engine creates output/ itself and refuses one that exists'
    # The run's progress reads it while the data is fetched, and the engine's own progress once that exists.
    from modules.quant.backend import strategy_views
    view = strategy_views.StrategyViews.__new__(strategy_views.StrategyViews)
    view.object_runs = SimpleNamespace(directory=lambda _identity: tmp_path, fetching=runs.fetching)
    assert view.progress({'id': 'r', 'state': 'preparing', 'stage': 'data'})['fetch']['done'] == 617
    assert view.progress({'id': 'r', 'state': 'preparing', 'stage': 'environment'}) is None


def test_a_strategy_run_says_which_names_its_data_left_out():
    from modules.quant.backend.strategy_views import left_out
    record = {'data': {'traded': 2, 'skipped': [{'symbol': 'META', 'reason': 'META 2024-06-14: Yahoo dividend share basis does not match its adjusted price evidence'}]}}
    assert left_out(record) == ['Left out 1 of 3 names, so it traded 2: META 2024-06-14: Yahoo dividend share basis does not match its adjusted price evidence']
    many = {'data': {'traded': 0, 'skipped': [{'symbol': name} for name in 'ABCDE']}}
    assert left_out(many) == ['Left out 5 of 5 names, so it traded 0: A; B; C; and 2 more'] and left_out({'data': {}}) == []


def test_a_strategy_run_says_when_its_library_signal_stops_long_before_the_period_ends():
    from modules.quant.backend.strategy_views import signal_gaps
    record = {'data': {'until': 1793750399999, 'library': [{'name': 'Reversal, LightGBM', 'version': 2, 'lastAvailableAt': 1725048000000}]}}
    assert signal_gaps(record) == ['Reversal, LightGBM v2 has values only up to Aug 30, 2024 in this period; later ones became known after it ends, '
                                   'so from then on the strategy held its last picks']
    assert signal_gaps({'data': {'until': 1793750399999, 'library': [{'name': 'X', 'version': 1, 'lastAvailableAt': 1793700000000}]}}) == []


def test_a_listed_symbol_missing_a_session_runs_through_it_and_the_data_names_the_symbol_and_day(tmp_path):
    """, the strategy side of a list of symbols goes through a session with no prices, as an index does, instead of failing the whole run;
    the data the run read names the symbol and the day."""
    abc = stock("ABC")
    ctx = prepared_context(tmp_path, [abc])
    market = ctx.require("market.instruments")
    asked = []
    terms_of = market.terms

    async def recorded(arguments):
        asked.append(arguments.get("allowMissingPrices"))
        return await terms_of(arguments)

    async def two_sessions(arguments):  # Jan 1 and Jan 2, 1970; the export holds Jan 1 only
        return {"evidence": "verified", "reason": None, "timezone": "UTC", "open": ["0", "86400000000000"], "close": ["1000000", "86401000000"], "next": None}
    market.terms, market.bar_times = recorded, two_sessions
    settings = {"data": {"symbols": [{"instrument": abc}], "timeframe": "1d", "since": "1970-01-01", "until": "1970-01-02"}, "account": {"capital": 1000}}
    result = asyncio.run(object_data.prepare(ctx, settings, "run", tmp_path))
    assert asked == [True]
    assert result["summary"]["gaps"] == [{"symbol": "ABC", "days": ["1970-01-02"]}]
    assert result["summary"]["instruments"][0]["missingBars"] == 1 and result["summary"]["traded"] == 1


def test_a_runs_notes_name_the_symbol_and_the_days_it_had_no_prices():
    from modules.quant.backend.strategy_views import price_gaps
    assert price_gaps({"data": {"gaps": [{"symbol": "ABC", "days": ["1970-01-02"]}]}}) == \
        ["ABC has no prices on 1970-01-02; the run went on through that day without a bar for it"]
    days = ["2024-03-04", "2024-03-05", "2024-03-06", "2024-03-07", "2024-03-08"]
    assert price_gaps({"data": {"gaps": [{"symbol": "DEF", "days": days}]}}) == \
        ["DEF has no prices on 2024-03-04, 2024-03-05, 2024-03-06 and 2 more; the run went on through those days without a bar for it"]
    assert price_gaps({"data": {"skipped": []}}) == []
