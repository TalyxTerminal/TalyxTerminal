"""A strategy's data settings become fixed Market bars, instrument terms and a venue, before the run starts."""
from __future__ import annotations

import asyncio
import hashlib
import json
import re
import time
from contextlib import ExitStack
from datetime import UTC, date, datetime
from decimal import Decimal
from pathlib import Path
from weakref import WeakValueDictionary

import numpy as np
import pandas as pd
import pyarrow as pa
import pyarrow.parquet as pq
from talyx_sdk import ContractError
from talyx_sdk.i18n import t

from vocabulary.instruments import MarketInstrument

from . import market_inputs as M
from .materials import ExportFiles, Materials, plain, read_selection, release
from .teardown import cleanup

PERIOD_MS = {"m": 60_000, "h": 3_600_000, "d": 86_400_000}
CAPITAL = 100_000
RAW = {"raw", "not_applicable"}
TERMS_BATCH = 2  # Each dated stock reply carries its full execution history.


def frozen_lists(data):
    """What a strategy run froze beyond its record (`data` is the record's): each name's source (`sources`) and bars (`instruments`), and for an
    index its summary with the exclusions. They are kept in info.json beside the bars; the record keeps counts, so reading the runs stays cheap."""
    return json.loads(Path(data["infoPath"]).read_text(encoding="utf-8"))


def public_summary(value):
    """A run's data for one run's page and its readers: the record's part, with the names' bars and sources from info.json (each stock's
    engine evidence left out); the index's exclusions stay in that file (data.infoPath)."""
    if value is None:
        return None
    result = dict(value)
    if 'infoPath' in value:
        lists = frozen_lists(value)
        result['instruments'] = lists['instruments']
        result['sources'] = [{key: item for key, item in source.items() if key != 'stock'} for source in lists['sources']]
    if isinstance(value.get('funding'), dict) and value['funding'].get('gaps'):
        # Runs recorded before the gaps were kept in milliseconds hold nanoseconds, which exact JSON cannot carry.
        result['funding'] = {**value['funding'], 'gaps': {name: [[start // 1_000_000 if start > 10 ** 16 else start, end // 1_000_000 if end > 10 ** 16 else end, *rest]
                                                                  for start, end, *rest in gaps] for name, gaps in value['funding']['gaps'].items()}}
    return result


def day_ms(value, end):
    moment = datetime.combine(date.fromisoformat(value), datetime.min.time(), UTC)
    return int(moment.timestamp() * 1000) + (86_400_000 - 1 if end else 0)


def yesterday(now_ms=None):
    """The last whole UTC day: the answer when nothing says which calendar the names follow."""
    now = int(time.time() * 1000) if now_ms is None else now_ms
    return datetime.fromtimestamp((now - 86_400_000) / 1000, UTC).date().isoformat()


FIRST_DAY = "1970-01-01"  # Market keeps nothing earlier: its times are non-negative nanoseconds


def check_range(data):
    """The one date range data settings may name, when they are saved and when a run reads them: from 1970-01-01 on."""
    for key, label in (("since", "The start date"), ("until", "The end date"), ("heldOutFrom", "The held-out start")):
        value = data.get(key)
        if isinstance(value, str) and value != "latest" and value < FIRST_DAY:
            raise ContractError("invalid_input", t("{label} {value} is before {FIRST_DAY}; Market keeps no data from before 1970. Choose {FIRST_DAY} or later.", label=label, value=value, FIRST_DAY=FIRST_DAY),
                                {"field": key, "earliest": FIRST_DAY})


async def bar_coverage(ctx, instrument, timeframe):
    """Market's source status and publication cutoff for the requested bar size."""
    items = await M.coverage(ctx, [instrument], "bars")
    daily = timeframe.endswith(("d", "w", "M"))  # a source declares one delay for its daily periods and one for its intraday ones
    base = next((item for item in items if item["timeframe"] == timeframe), None) \
        or next(item for item in items if item["timeframe"] and item["timeframe"].endswith(("d", "w", "M")) == daily)
    return base


async def pending_daily_close(ctx, instrument, previous, latest):
    """Market leaves a late final session unfetched when the preceding bar exists. Confirm that tail at the source."""
    opened, closed = latest
    page = plain(await ctx.call("market.read_series", {
        "instrumentId": instrument["instrumentId"], "series": M.price_series(instrument, "1d", False),
        "start": M.ns(previous), "end": M.ns(closed), "limit": 2, "fill": "await"}))
    problem = page["fill"]["problem"]
    if problem:
        raise ContractError(problem["code"], problem["message"], problem)
    times = page["columns"]["time"]
    return M.ns(previous) in times and M.ns(opened) not in times and any(
        gap["reason"] == "not_fetched" and int(gap["start"]) <= opened * M.MS < int(gap["end"]) for gap in page["gaps"])


async def resolve_until(ctx, data, *, instruments=None):
    """Freeze Latest close on the selected calendars at Market's publication cutoffs.
    An explicit daily pool also waits for any late final bar. Fixed dates keep their requested range."""
    check_range(data)
    if data.get("until") != "latest":
        return data
    from zoneinfo import ZoneInfo

    from .research_calendar import boundaries
    items = instruments if instruments else [symbol["instrument"] for symbol in data.get("symbols") or []]
    timeframe = data.get("timeframe") or "1d"
    coverage = await asyncio.gather(*(bar_coverage(ctx, item, timeframe) for item in items))
    days, pending = [], []
    # Historical index members have individual membership windows; they need not all trade on the last day.
    daily_pool = timeframe == "1d" and not data.get("index")
    for calendar_id in sorted({item["calendarId"] for item in items}):
        group = [(item, row) for item, row in zip(items, coverage, strict=True) if item["calendarId"] == calendar_id]
        cutoff = (min if daily_pool else max)(M.milliseconds(row["publishedThrough"]) for _, row in group)
        scope = "continuous" if calendar_id == "24_7" else "regular"
        zone, found = await boundaries(ctx, calendar_id, timeframe, scope, cutoff - 20 * 86_400_000, cutoff)
        closed = [(opened, closes) for opened, closes in found if closes <= cutoff]
        if closed:
            local_zone = ZoneInfo(zone)
            days.append(datetime.fromtimestamp(closed[-1][0] / 1000, local_zone).date().isoformat())
            if daily_pool and len(closed) >= 2:
                previous = closed[-2][0]
                for item, row in group:
                    if row["status"] in {"not_stored", "behind", "updating"} and await pending_daily_close(ctx, item, previous, closed[-1]):
                        pending.append(datetime.fromtimestamp(previous / 1000, local_zone).date().isoformat())
                        break
    until = max(days) if days else yesterday()
    return {**data, "until": min([until, *pending]), "untilLatest": True}


async def frozen_data(ctx, data, *, progress=None):
    """The data settings as a run takes them: the last day resolved, and for an index its members frozen and written into `symbols`. Returns (data, index or None)."""
    if not data.get("index"):
        return await resolve_until(ctx, data), None
    from .index_universe import freeze
    from .research_calendar import date_window
    first = yesterday() if data["until"] == "latest" else data["until"]
    start, end = await date_window(ctx, data["index"]["instrument"]["calendarId"], data["timeframe"], day_ms(data["since"], False), day_ms(first, True))
    index = await freeze(ctx, data["index"], start, end, progress=progress, timeframe=data["timeframe"])
    data = await resolve_until(ctx, data, instruments=index["instruments"])
    if data["until"] != first:
        start, end = await date_window(ctx, data["index"]["instrument"]["calendarId"], data["timeframe"], day_ms(data["since"], False), day_ms(data["until"], True))
        index = await freeze(ctx, data["index"], start, end, progress=progress, timeframe=data["timeframe"])
    return {**data, "symbols": [{"instrument": item} for item in index["instruments"]]}, index


def day_label(milliseconds):
    return datetime.fromtimestamp(milliseconds / 1000, UTC).strftime("%Y-%m-%d %H:%M")


def required_settings(data):
    for key, message in (("symbols", "Choose at least one symbol in the data settings."), ("timeframe", "Choose a bar period in the data settings."),
                         ("since", "Choose a start date in the data settings."), ("until", "Choose an end date in the data settings.")):
        if not data.get(key) and not (key == 'symbols' and data.get('index')):
            raise ContractError("invalid_input", message, {"field": key})


async def resolve_text(ctx, text):
    """Resolve Market's standard listing or venue-contract notation."""
    return await M.resolve(ctx, text)


async def resolve_series(ctx, instrument, timeframe, *, adjustment=None, environment="production"):
    instrument = await M.current(ctx, instrument)
    return instrument, M.price_series(instrument, timeframe, adjustment if adjustment is not None else stock_like(instrument))


ORDER = ("1m", "5m", "15m", "30m", "1h", "4h", "1d")


async def verified_routes(ctx, instrument):
    return routes(await M.coverage(ctx, [instrument], "bars"))


def routes(rows):
    """The bar sizes Market can build from the periods its source declares."""
    periods = {period for row in rows for period in row["available"]["periods"]}
    def supported(wanted):
        def minutes(value):
            match = re.fullmatch(r"(\d+)(m|h|d)", value)
            return int(match[1]) * {"m": 1, "h": 60, "d": 1440}[match[2]] if match else None
        target = minutes(wanted)
        return any(minutes(base) and (base.endswith("d") == wanted.endswith("d")) and target % minutes(base) == 0 for base in periods)
    return [period for period in ORDER if supported(period)]


def route_name(instrument, timeframe):
    kind = {"swap": "perpetual", "spot": "spot", "future": "future"}.get(instrument["marketType"], instrument["marketType"])
    return f"{instrument['venueId']} {instrument['displaySymbol']} {kind} {timeframe}"


async def check_routes(ctx, instruments, *timeframes):
    for instrument in instruments:
        available = await verified_routes(ctx, instrument)
        for timeframe in timeframes:
            if timeframe not in available:
                raise ContractError("unsupported", t("{instrument} history is not available in Market.", instrument=route_name(instrument, timeframe)),
                                    {"instrumentId": instrument["instrumentId"], "timeframe": timeframe})


async def preflight(ctx, instrument, series, *, since=None, environment="production", realtime=False):
    rows = await M.coverage(ctx, [instrument], "bars" if series["capability"] == "ohlcv" else series["capability"])
    candidates = [row for row in rows if row["capability"] == series["capability"]]
    if not candidates or series["timeframe"] not in routes(rows):
        raise ContractError("unsupported", t("{displaySymbol}: Market has no {timeframe} {capability} history.", displaySymbol=instrument['displaySymbol'], timeframe=series['timeframe'], capability=series['capability']))
    row = next((item for item in candidates if item["timeframe"] == series["timeframe"]), candidates[0])
    earliest = row["available"]["earliest"]
    earliest_date = None
    lower_bound = M.milliseconds(earliest) if earliest is not None else None
    if since is not None and earliest is not None and series["timeframe"].endswith("d"):
        from zoneinfo import ZoneInfo
        moment = datetime.fromtimestamp(M.milliseconds(earliest) / 1000, UTC)
        page = plain(await ctx.call("market.calendar.sessions", {
            "calendarId": instrument["calendarId"], "startDate": moment.date().isoformat(),
            "endDate": moment.date().isoformat()}))
        if page["timezone"] is not None:
            earliest_date = moment.astimezone(ZoneInfo(page["timezone"])).date().isoformat()
            lower_bound = day_ms(earliest_date, False)
    if since is not None and lower_bound is not None and since < lower_bound:
        reason = f"{instrument['displaySymbol']}: Market history starts on {day_label(M.milliseconds(earliest))} UTC."
        detail = {"earliestAt": M.milliseconds(earliest), "reason": reason, "instrumentId": instrument["instrumentId"]}
        if earliest_date is not None:
            detail["earliestDate"] = earliest_date
        raise ContractError("history_unavailable", reason, detail)
    if realtime and row["available"]["live"] == "none":
        raise ContractError("unsupported", t("{displaySymbol}: Market has no live {capability} data.", displaySymbol=instrument['displaySymbol'], capability=series['capability']))
    return row


def shared_instrument(record):
    return MarketInstrument.from_wire(record).wire_dict()


def decimals(value):
    return max(0, -Decimal(str(value)).normalize().as_tuple().exponent)


async def close_times(ctx, instrument, series, times):
    """Exact exchange closes; only continuous bars have a fixed elapsed period."""
    if instrument['calendarId'] == '24_7':
        return (times + int(series['timeframe'][:-1]) * PERIOD_MS[series['timeframe'][-1]]) * 1_000_000
    from .research_calendar import boundaries
    _zone, found = await boundaries(ctx, instrument['calendarId'], series['timeframe'], series['sessionScope'], int(times.min()), int(times.max()))
    closed = dict(found)
    missing = [int(value) for value in times if int(value) not in closed]
    if missing:
        raise ContractError('unavailable', t("{displaySymbol}: Market calendar has no close for bar {missing}", displaySymbol=instrument['displaySymbol'], missing=missing[0]))
    return times.map(closed) * 1_000_000


async def trading_terms(ctx, instruments, *, selections=None, since=None, until=None, allow_missing_prices=False, index=None, skipped=None, progress=None, windows=None):
    size = 1 if selections is not None else 100
    by_id = {item["instrument"]["instrumentId"]: item["series"] for item in selections} if selections is not None else None
    gate = asyncio.Semaphore(4)
    done = 0
    async def one(batch):
        nonlocal done
        async with gate:
            try:
                args = {"symbols": [item["instrumentId"] for item in batch], "limit": 500}
                if by_id is not None:
                    begin, stop = windows[batch[0]["instrumentId"]] if windows is not None else (since, until)
                    args = {"series": [{"instrumentId": item["instrumentId"], "series": by_id[item["instrumentId"]]} for item in batch],
                            "start": M.timestamp(begin), "end": M.timestamp(stop), "allowMissingPrices": allow_missing_prices}
                result = plain(await ctx.call("market.instruments.terms", args))
                return await asyncio.to_thread(lambda: [M.stock_times(item) for item in result["items"]])
            except ContractError as error:
                if index is None and skipped is None:
                    raise
                from .index_universe import exclude
                for instrument in batch:
                    if index is not None:
                        exclude(index, instrument["instrumentId"], since, until, "missing_price_evidence", source_reason=error.message)
                    if skipped is not None:
                        skipped.append({"symbol": instrument["displaySymbol"], "reason": error.message, "code": error.code})
                return []
            finally:
                done += len(batch)
                if progress is not None:
                    progress(done)
    tasks = [asyncio.create_task(one(instruments[i:i+size])) for i in range(0, len(instruments), size)]
    try:
        return {item["instrumentId"]: item for batch in await asyncio.gather(*tasks) for item in batch}
    finally:
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)


def fees_for(venue, named_terms, account):
    """Each name's maker/taker rate, [fee] in the order of `named_terms` (display symbol, Market trading terms): the account's own when set, else
    Market's for that name (the venue's, or the venue's standard tier: basis market_fee_table). Names on one venue may be charged differently;
    no fee is made up."""
    if account.get("fee"):
        return [account["fee"] for _ in named_terms]
    missing = [f"{name} ({terms['unavailable']['fees']})" for name, terms in named_terms if terms["fees"] is None]
    if missing:
        raise ContractError("unavailable", t("Market has no fees for {missing}. Set the fees in the account settings.", missing=', '.join(missing)), {"venue": venue})
    return [{"maker": terms["fees"]["maker"], "taker": terms["fees"]["taker"]} for _, terms in named_terms]


def one_fee(fees):
    """The fee when every name pays the same, else None (the settings then show no single rate)."""
    return fees[0] if fees and all(fee == fees[0] for fee in fees) else None


def assumed_fees(terms_by_venue, account):
    """The venues whose fee comes from the venue's standard tier, not the venue's own market list ("assumed" in the settings)."""
    if account.get("fee"):
        return []
    return sorted({name for name, entries in terms_by_venue.items() for native, market, _ in entries
                   if native["type"] != "equity" and (market["fees"] or {}).get("basis") == "market_fee_table"})


def sized(precision_floor, increment, volume_precision):
    """Size precision and increment: the market's own quantity step when it gives one, never finer data than the data carries."""
    if increment is None:
        precision = max(precision_floor, volume_precision)
        return precision, format(Decimal(1).scaleb(-precision), "f")
    precision = max(decimals(increment), volume_precision)
    return precision, format(Decimal(str(increment)).normalize(), "f") if precision == decimals(increment) else format(Decimal(1).scaleb(-precision), "f")


def instrument_terms(instrument, frame, market, *, execution_venue=None):
    """Native prices use Market's trading step when known and retain the precision of the observed prices. Sizes follow the market's
    quantity step in the base asset (1e-6 when the market gives none); a perpetual quoted in contracts is sized in base asset steps of
    contracts times Market's contract multiplier. Equities trade whole shares."""
    market_type, asset = instrument["marketType"], instrument["assetClass"]
    venue = re.sub(r"[^A-Za-z0-9_-]", "_", instrument["venueId"]).upper()
    raw = re.sub(r"[^A-Za-z0-9_-]", "_", instrument["symbol"])
    price_precision = max(M.decimal_scale(pa.array(frame[column])) for column in ("open", "high", "low", "close"))
    if market["priceIncrement"] is None:
        price_increment = Decimal(1).scaleb(-price_precision)
    else:
        price_increment = Decimal(str(market["priceIncrement"]))
        price_precision = max(price_precision, decimals(price_increment))
    volume_precision = M.decimal_scale(pa.array(frame["volume"]))
    quote = instrument.get("quote") or instrument.get("currency")
    if market_type == "swap" and instrument["contractOrientation"] == "linear":
        increment = market["quantityIncrement"]
        if market["quantityUnit"] == "contracts":
            # The venue counts contracts; the simulator sizes in the base asset, in steps of whole contract steps (a step is `quantityIncrement` contracts of `contractMultiplier` base each).
            if increment is None:
                raise ContractError("invalid_input", t("{displaySymbol} is quoted in contracts of {contractMultiplier} {base}, but Market gives no contract step ({quantityIncrement}).", displaySymbol=instrument['displaySymbol'], contractMultiplier=market['contractMultiplier'], base=instrument['base'], quantityIncrement=market['unavailable']['quantityIncrement']))
            increment = (Decimal(str(increment)) * Decimal(str(market["contractMultiplier"]))).normalize()
        elif market["quantityUnit"] != "base":
            raise ContractError("invalid_input", (t("{displaySymbol} is quoted in {unit} ({quantityUnit}); the simulator sizes perpetuals in the base asset.",
                        displaySymbol=instrument['displaySymbol'], unit=market['quantityUnit'] or 'an unknown unit', quantityUnit=market['unavailable']['quantityUnit'])
                    if market['quantityUnit'] is None else t("{displaySymbol} is quoted in {unit}; the simulator sizes perpetuals in the base asset.",
                        displaySymbol=instrument['displaySymbol'], unit=market['quantityUnit'] or 'an unknown unit')))
        kind, symbol, (size_precision, size_increment) = "perpetual", f"{instrument['base']}{quote}-PERP", sized(6, increment, volume_precision)
    elif market_type == "spot" and asset == "crypto":
        kind, symbol, (size_precision, size_increment) = "spot", f"{instrument['base']}{quote}", sized(6, market["quantityIncrement"], volume_precision)
    elif market_type == "spot" and asset in {"equity", "fund"}:
        kind, symbol, size_precision, size_increment = "equity", raw, 0, "1"
        if volume_precision:
            raise ContractError("invalid_input", t("{displaySymbol} has fractional share volume; the equity simulator needs whole shares.", displaySymbol=instrument['displaySymbol']))
    else:
        raise ContractError("invalid_input", t("{displaySymbol} is a {market_type} {asset}; strategy backtests cover spot, linear perpetual and equity data.", displaySymbol=instrument['displaySymbol'], market_type=market_type, asset=asset))
    if not quote:
        raise ContractError("invalid_input", t("Market gives {displaySymbol} no quote currency.", displaySymbol=instrument['displaySymbol']))
    terms = {"id": f"{symbol}.{venue}", "type": kind, "symbol": symbol, "currency": quote,
             "pricePrecision": price_precision, "sizePrecision": size_precision,
             "priceIncrement": f"{price_increment:.{price_precision}f}", "sizeIncrement": size_increment}
    if execution_venue is not None:
        terms['symbol'] = f'{symbol}-{venue}-' + hashlib.sha256(instrument['instrumentId'].encode()).hexdigest()[:8]
        venue = execution_venue
        terms['id'] = f"{terms['symbol']}.{venue}"
    if kind != "equity":
        terms["baseCurrency"] = instrument["base"]
    if kind == "perpetual":
        terms["settlementCurrency"] = instrument.get("settle") or quote
    if kind == 'equity':
        if 'stock' not in market:
            raise ContractError('unavailable', t("{displaySymbol}: Market has no verified equity execution conditions", displaySymbol=instrument['displaySymbol']))
        # the backtest reads no corporate actions (the prices are adjusted); a ten-year history of them was most of the invocation
        terms['stock'] = {key: value for key, value in market['stock'].items() if key != 'corporateActions'}
    return terms, venue


def stock_commission(stock, account):
    if account.get('stockCommission') is None:
        return stock['commission']
    override = {key: None if value is None else str(value) for key, value in account['stockCommission'].items()}
    return {**stock['commission'], **override, 'assumption': 'User account commission override', 'evidence': 'Quant account settings'}


def venue_for(terms_by_venue, account):
    """One simulated venue per source venue; each entry is (native terms, Market trading terms, display symbol). The account fee overrides Market's."""
    venues = []
    for name, entries in terms_by_venue.items():
        terms = [native for native, _, _ in entries]
        for item in terms:
            if item['type'] == 'equity':
                item['stockCommission'] = stock_commission(item['stock'], account)
        margin = any(item["type"] == "perpetual" for item in terms)
        currency = terms[0]["currency"]
        traded = [(native, (label, market)) for native, market, label in entries if native["type"] != "equity"]
        for (item, _), fee in zip(traded, fees_for(name, [named for _, named in traded], account), strict=True):
            # Each name its own rate; the risk check estimates a buy's commission from the taker rate.
            item["makerFee"], item["takerFee"] = format(Decimal(str(fee["maker"])), "f"), format(Decimal(str(fee["taker"])), "f")
        venue = {"name": name, "accountType": "MARGIN" if margin else "CASH", "omsType": "NETTING",
                 "balances": [f"{account.get('capital', CAPITAL)} {currency}"], "baseCurrency": currency if margin else None,
                 "fees": {"type": "per_instrument"} if traded else {"type": "instrument_conditions"},
                 "leverage": account.get("leverage", 1)}
        if not margin:
            venue.pop("baseCurrency")
        venues.append(venue)
    return venues


def bar_type(terms_id, timeframe):
    step, unit = int(timeframe[:-1]), timeframe[-1]
    return f"{terms_id}-{step}-" + {"m": "MINUTE", "h": "HOUR", "d": "DAY"}[unit] + "-LAST-EXTERNAL"


def composite_bar_type(signal, fine):
    """The signal bar built inside the engine from the fine bars, e.g. BTCUSDT-PERP.OKX-1-HOUR-LAST-INTERNAL@5-MINUTE-EXTERNAL."""
    step, unit = int(fine[:-1]), fine[-1]
    return signal.removesuffix("-EXTERNAL") + "-INTERNAL@" + f"{step}-" + {"m": "MINUTE", "h": "HOUR", "d": "DAY"}[unit] + "-EXTERNAL"


async def daily_annualization(ctx, selections, since, until):
    """Days per year of the selected instruments' trading calendar, counted by Market (the longest when they differ): the basis equity statistics are annualized on."""
    from .research_calendar import annualization, date_boundaries
    counted = []
    for calendar, scope in sorted({(item["instrument"]["calendarId"], item["series"]["sessionScope"]) for item in selections}):
        _zone, found = await date_boundaries(ctx, calendar, "1d", scope, since, until)
        counted.append(annualization(found, since, until))
    return max(counted)


PRICES_BATCH = 50  # names to one Market capture, so a long index reports its progress as it goes


def left_out(index, skipped):
    """The names left out so far, as the run's progress lists them: Market refused them or has no prices for them."""
    names = [row["symbol"] for row in skipped] + [row["symbol"] for row in (index or {}).get("exclusions") or []]
    return list(dict.fromkeys(name for name in names if name))


def none_left(skipped, index):
    """The run's failure when no name is left: each name with Market's reason (the first five)."""
    named = without_prices(skipped)
    return ContractError("no_data", t("No name has prices for this run: {named}", named=named) if named else t("No name has prices for this run."), {"index": index})


def without_prices(skipped, shown=5):
    """The names a run left out with Market's reason, the first `shown` of them: "AAA: reason; BBB 2024-06-14: reason; and 3 more" (a reason
    that names its symbol is not prefixed with it again)."""
    def one(row):
        symbol = row.get("symbol") or row.get("ticker")
        return row["reason"] if symbol in row["reason"] else t("{symbol}: {reason}", symbol=symbol, reason=row["reason"])
    named = "; ".join(one(row) for row in skipped[:shown])
    return t("{named}; and {count} more", named=named, count=len(skipped) - shown) if len(skipped) > shown else named


def left_out_text(skipped, total):
    """The run's data note for the names it left out: "3 of 300 names have no prices: …"."""
    return t("{count} of {total} names have no prices: {details}", count=len(skipped), total=total, details=without_prices(skipped))


def stock_like(instrument):
    return instrument["assetClass"] in {"equity", "fund"}


def engine_bars(selection, exports):
    """A selection's bars for the engine. Market adjusts share volume by float split ratios (a 3-for-2 split, or a
    spin-off the source records as a split, leaves fractions); the equity simulator counts whole shares, as Market
    itself rounds when it undoes a source's split adjustment."""
    found = read_selection(selection, exports)
    if stock_like(selection["instrument"]):
        found["volume"] = found["volume"].round()
    return found


def record_missing_bars(index, item, frame, now, schedule):
    expected = np.asarray([opened for opened, closed in schedule if closed <= now], dtype=np.int64)
    absent = expected[~np.isin(expected, frame["time"].to_numpy())]
    item["coverage"]["missingBars"] = int(len(absent))
    if index is None:
        # Sessions after the name's last bar are not gaps: its prices end there (delisted, or the source has none after), and the backtest
        # sells what it holds at that bar's close.
        last = int(frame["time"].max())
        if len(expected) and last < int(expected[-1]):
            item["coverage"]["endedAt"] = int(frame["closeAt"].max())  # its last close
        item["coverage"]["missingDays"] = [time.strftime("%Y-%m-%d", time.gmtime(int(opened) / 1000)) for opened in absent if opened < last]
    else:
        from .index_universe import check_prices
        check_prices(index, item, frame, now, schedule)


# A stock's sessions as the backtest reads them: one row a session, only what the simulated venue uses (`native/stock_execution.conditions`).
# Kept as columns, 559 names' ten years take a few hundred MB in the backtest; one JSON text a stock, parsed whole, took 2.8 GiB.
SESSION_SCHEMA = pa.schema([("instrument", pa.string()), ("sessionDate", pa.string()), ("openAt", pa.int64()), ("closeAt", pa.int64()),
                            ("lower", pa.string()), ("upper", pa.string()), ("priceIncrement", pa.string()),
                            ("priceScale", pa.string()), ("priceOffset", pa.string()), ("priceMaxError", pa.string())])


def session_table(native_id, sessions):
    column = lambda key: [row[key] for row in sessions]
    price_map = lambda key: [row["priceMap"][key] for row in sessions]
    return pa.table({"instrument": [native_id] * len(sessions), "sessionDate": column("sessionDate"), "openAt": column("openAt"),
                     "closeAt": column("closeAt"), "lower": column("lower"), "upper": column("upper"), "priceIncrement": column("priceIncrement"),
                     "priceScale": price_map("scale"), "priceOffset": price_map("offset"), "priceMaxError": price_map("maxError")}, schema=SESSION_SCHEMA)


def write_strategy_input(selection, by_series, fine_ids, reference_ids, market, exports, write, index, now, schedule):
    instrument, series = selection["instrument"], selection["series"]
    raw = engine_bars(selection, exports)
    record_missing_bars(index, selection, raw, now, schedule)
    currency = instrument.get("quote") or instrument.get("currency")
    execution_venue = "SIM_" + currency if stock_like(instrument) else None
    terms, venue = instrument_terms(instrument, raw, market, execution_venue=execution_venue)
    if stock_like(instrument):
        sessions = terms["stock"].pop("sessions")
        by_close = pd.DataFrame(sessions, columns=["closeAt", "adjustedOHLC"]).set_index("closeAt").reindex(raw["closeAt"].to_numpy())
        expected = np.asarray(by_close["adjustedOHLC"].to_list(), dtype=float)
        actual = raw[["open", "high", "low", "close"]].to_numpy()
        if expected.shape != actual.shape or not np.allclose(actual, expected, rtol=0, atol=1e-8, equal_nan=False):
            raise ContractError("conflict", t("{displaySymbol}: adjusted history changed during capture.", displaySymbol=instrument['displaySymbol']))
        write("stock-sessions", session_table(terms["id"], sessions))
    kind = bar_type(terms["id"], series["timeframe"])
    engine_selection = by_series[fine_ids[instrument["instrumentId"]]] if instrument["instrumentId"] in fine_ids else selection
    engine = raw if engine_selection is selection else engine_bars(engine_selection, exports)
    engine_kind = bar_type(terms["id"], engine_selection["series"]["timeframe"])
    out = M.decimal_columns(engine)
    out["instrument"], out["barType"] = terms["id"], engine_kind
    out["ts_event"], out["ts_init"] = engine["closeNs"].astype("int64").to_numpy(), engine["knownNs"].astype("int64").to_numpy()
    for basis, reference_id in reference_ids.get(instrument["instrumentId"], {}).items():
        reference = read_selection(by_series[reference_id], exports).set_index("closeNs")
        needed = engine["closeNs"]
        if not needed.isin(reference.index).all():
            raise ContractError("history_gap", t("{displaySymbol}: missing {upper} reference candles.", displaySymbol=instrument['displaySymbol'], upper=basis.upper()))
        matched = reference.loc[needed].reset_index(drop=True)
        values = M.decimal_columns(matched, fields=M.FIELDS[:4])
        for field in ("open", "high", "low", "close"):
            out[basis + "_" + field] = values[field].to_numpy()
    table = pa.Table.from_pandas(out, preserve_index=False)
    write("bars", table)
    from .research_data import labels_of
    zone = (market.get("stock") or {}).get("timezone") or instrument.get("timezone") or "UTC"
    price = raw.assign(instrument=terms["id"], label=labels_of(raw["time"], series["timeframe"], zone))[
        ["instrument", "time", "label", "knownAt", *M.FIELDS]]
    table = pa.Table.from_pandas(price, preserve_index=False)
    write("prices", table)
    from .research_calendar import label_of
    tested = {"start": M.timestamp(label_of(int(raw["time"].min()), series["timeframe"], zone))[:10],
              "end": M.timestamp(label_of(int(raw["time"].max()), series["timeframe"], zone))[:10],
              "bars": len(raw), "timeframe":series["timeframe"]}
    source = {"tested":tested, "instrument": instrument, "marketInstrument": instrument, "series": series, "nativeInstrument": terms["id"],
              "barType": kind, "version": selection["version"], "source": selection["source"],
              "coverage": selection["coverage"], "units": {"time": "unix_ms", "volume": "shares" if stock_like(instrument) else "base"},
              "timeSemantics": {"eventTime": "closeTime", "availability": "knownAt"}, "rows": len(raw)}
    if stock_like(instrument):
        source["stock"] = terms["stock"]
    return terms, venue, source, len(out)


async def prepare(ctx, settings, params_run_id, directory, *, report=None):
    """Freeze selections through Market, then prepare native input off the Host event loop."""
    report = report or (lambda *_args, **_kwargs: None)
    def member_progress(value):
        report("names", value["done"], value["total"], [],
               waitingFor=value["waitingFor"], waitingUntil=value["waitingUntil"],
               # Market's bare task state ("running") is not a step; the step's own words stand then ("Checking names: 0 of 585")
               message=value["message"] if value.get("message") not in (None, "", "queued", "running", "pending") else None)
    data, index = await frozen_data(ctx, settings["data"], progress=member_progress)
    required_settings(data)
    account, timeframe, fine = settings["account"], data["timeframe"], data.get("fillBars")
    since, until = day_ms(data["since"], False), day_ms(data["until"], True)
    selected, wanted, fine_ids, reference_ids, skipped = [], {}, {}, {}, []
    for position, symbol in enumerate(data["symbols"]):
        report("names", position, len(data["symbols"]), left_out(index, skipped))
        instrument = symbol["instrument"]
        try:
            instrument, series = await resolve_series(ctx, instrument, timeframe, adjustment=data.get("adjustment"))
            await preflight(ctx, instrument, series)
            item = {"instrument": instrument, "series": series}
            selected.append(item)
            wanted[series["seriesId"]] = item
            execution_tf = fine or timeframe
            if fine:
                fine_series = M.price_series(instrument, fine, data.get("adjustment", stock_like(instrument)))
                await preflight(ctx, instrument, fine_series)
                wanted[fine_series["seriesId"]] = {"instrument": instrument, "series": fine_series}
                fine_ids[instrument["instrumentId"]] = fine_series["seriesId"]
            for basis in data.get("referencePrices", []):
                reference = M.price_series(instrument, execution_tf, False, price_basis=basis)
                wanted[reference["seriesId"]] = {"instrument": instrument, "series": reference}
                reference_ids.setdefault(instrument["instrumentId"], {})[basis] = reference["seriesId"]
        except ContractError as error:
            # A name Market cannot price is left out of the run and named in its data notes, for a list as for an index; a run fails only
            # when no name is left (as JoinQuant, RiceQuant and QuantConnect run a universe whose members lack data).
            from .index_universe import exclude
            selected = [item for item in selected if item["instrument"]["instrumentId"] != instrument["instrumentId"]]
            wanted = {key: item for key, item in wanted.items() if item["instrument"]["instrumentId"] != instrument["instrumentId"]}
            fine_ids.pop(instrument["instrumentId"], None)
            reference_ids.pop(instrument["instrumentId"], None)
            if index is not None:
                exclude(index, symbol["instrument"]["instrumentId"], since, until, "no_price_series", source_reason=error.message)
            skipped.append({"symbol": symbol["instrument"]["displaySymbol"], "reason": error.message, "code": error.code})
    if not selected:
        raise none_left(skipped, index)
    def prices_progress(value):
        # Market fetches names (one item a name), then exports series (adjusted and raw prices: two a stock): two steps, two counts.
        report(value["step"], value["done"], value["total"], left_out(index, skipped),
               waitingFor=value["waitingFor"], waitingUntil=value["waitingUntil"])
    from .research_calendar import date_windows
    windows = await date_windows(ctx, [item["instrument"] for item in selected], timeframe, since, until)
    captured = await Materials(ctx).capture({"market": {"selections": list(wanted.values()), "since": since, "until": until, "windows": windows,
                    "allowEmpty": True, "progress": prices_progress}}, params_run_id)
    try:
        manifest = captured["manifest"]
        by_id = {item["series"]["seriesId"]: item for item in manifest["selections"]}
        usable = []
        for choice in selected:
            item = by_id[choice["series"]["seriesId"]]
            instrument = item["instrument"]
            needed = [item["series"]["seriesId"], *reference_ids.get(instrument["instrumentId"], {}).values()]
            if instrument["instrumentId"] in fine_ids:
                needed.append(fine_ids[instrument["instrumentId"]])
            missing = next((by_id[key] for key in needed if not by_id[key]["coverage"]["exportedRows"]), None)
            if missing is not None:
                problem = missing.get("problem")
                from .index_universe import exclude
                reason = problem["message"] if problem else t("Market has no prices in this period.")
                if index is not None:
                    exclude(index, instrument["instrumentId"], since, until, "no_prices", source_reason=reason)
                skipped.append({"symbol": instrument["displaySymbol"], "reason": reason, "code": problem["code"] if problem else "no_data"})
                continue
            usable.append(item)
        if not usable:
            raise none_left(skipped, index)
        schedules = {}
        now = int(time.time() * 1000)
        directory.mkdir(parents=True, exist_ok=True)
        sources, terms, by_venue, writers = [], [], {}, {}
        count = 0
        with ExportFiles() as exports, ExitStack() as files:
            def write(name, table):
                if name not in writers:
                    writers[name] = files.enter_context(pq.ParquetWriter(directory / f"{name}.parquet", table.schema))
                writers[name].write_table(table)

            for offset in range(0, len(usable), TERMS_BATCH):
                batch = usable[offset:offset + TERMS_BATCH]
                report("terms", offset, len(usable), left_out(index, skipped))
                market_terms = await trading_terms(ctx, [item["instrument"] for item in batch], selections=batch,
                    since=since, until=until, allow_missing_prices=True, index=index, windows=windows, skipped=skipped,
                    progress=lambda done: report("terms", offset + done, len(usable), left_out(index, skipped)))
                for position, item in enumerate(batch, offset):
                    instrument, series = item["instrument"], item["series"]
                    instrument_id = instrument["instrumentId"]
                    if instrument_id not in market_terms:
                        continue
                    report("coverage", position, len(usable), left_out(index, skipped))
                    key = (instrument["calendarId"], series["timeframe"], series["sessionScope"])
                    if key not in schedules:
                        from .research_calendar import boundaries
                        schedules[key] = (await boundaries(ctx, *key, *windows[instrument_id]))[1]
                    market = market_terms.pop(instrument_id)
                    result, = await cleanup(asyncio.to_thread(write_strategy_input, item, by_id, fine_ids, reference_ids,
                                                              market, exports, write, index, now, schedules[key]))
                    native, venue, source, rows = result
                    terms.append(native)
                    sources.append(source)
                    by_venue.setdefault(venue, []).append((native, market, instrument["displaySymbol"]))
                    count += rows
                    report("inputs", position + 1, len(usable), left_out(index, skipped))
        if not sources:
            raise none_left(skipped, index)
        if index is not None:
            index["nativeInstruments"] = {item["nativeInstrument"]: item["marketInstrument"]["instrumentId"] for item in sources}
        venues = venue_for(by_venue, account)
        assumed = assumed_fees(by_venue, account)
        annualization = await daily_annualization(ctx, sources, since, until)
        bar_types = [composite_bar_type(item["barType"], fine) if fine else item["barType"] for item in sources]
        summary = {"timeframe": timeframe, "since": since, "until": until, "adjustment": next((item["series"]["adjustment"] for item in usable if stock_like(item["instrument"])), "raw"),
                   "rows": count, "skipped": skipped, "feeAssumed": assumed, "requested": len(data["symbols"]), "traded": len(sources),
                   "instruments": [{"id": item["nativeInstrument"], "symbol": item["instrument"]["displaySymbol"],
                       "venue": item["instrument"]["venueId"], "marketType": item["instrument"]["marketType"],
                       "rows": item["rows"], "version": item["version"], "range": item["coverage"]["exportedRange"],
                       "missingBars": item["coverage"]["missingBars"]} for item in sources],
                   # Sessions of a chosen symbol Market has no prices for: the run went through them without a bar for that name.
                   "gaps": [{"symbol": item["instrument"]["displaySymbol"], "days": item["coverage"]["missingDays"]} for item in sources if item["coverage"].get("missingDays")],
                   # Names whose prices stop before the run ends: what they held was sold at their last close.
                   "ended": [{"symbol": item["instrument"]["displaySymbol"], "at": item["coverage"]["endedAt"]} for item in sources if item["coverage"].get("endedAt")]}
        ranges = [source["tested"] for source in sources]
        summary["tested"] = {"start": min(r["start"] for r in ranges), "end": max(r["end"] for r in ranges),
                             "bars":sum(r["bars"] for r in ranges), "timeframe":timeframe}
        if fine:
            summary["fillBars"] = fine
        return {"barsPath": str(directory / "bars.parquet"), "pricesPath": str(directory / "prices.parquet"),
                "stockSessions": str(directory / "stock-sessions.parquet") if "stock-sessions" in writers else None,
                "instruments": terms, "venues": venues, "barTypes": bar_types, "annualization": annualization,
                "summary": summary, "sources": sources, "index": index,
                # what the freeze is of: every series at the revision it was exported at (prepare_reusing checks these)
                "frozen": {"until": until, "selections": list(wanted.values()), "revisions": {item["series"]["seriesId"]: item["version"] for item in manifest["selections"]}}}
    finally:
        await release(ctx, captured["references"], params_run_id)


_freeze_locks = WeakValueDictionary()


async def prepare_reusing(ctx, settings, params_run_id, directory, kept, *, report=None):
    async with _freeze_locks.setdefault(kept, asyncio.Lock()):
        return await _prepare_reusing(ctx, settings, params_run_id, directory, kept, report=report)


async def _prepare_reusing(ctx, settings, params_run_id, directory, kept, *, report=None):
    """`prepare`, unless the same settings were prepared before (`kept`, a folder of the data root named by them) and Market still has
    every series at the revision frozen then: that freeze is copied into the run's folder, and nothing is fetched, exported or arranged.
    The freeze of these settings is kept for the next run."""
    import pickle
    import shutil
    files = ("bars.parquet", "prices.parquet", "stock-sessions.parquet")
    # Earlier freezes inferred the trading tick from observed prices (v1), or wrote times as text and sessions as JSON (v2): prepared again.
    stored = kept / "prepared-v3.pickle"
    found = await asyncio.to_thread(lambda: pickle.loads(stored.read_bytes())) if stored.is_file() else None
    if found is not None:
        frozen = found["frozen"]
        current = await M.revisions(ctx, frozen["selections"], frozen["until"])
        if all(current[key]["revision"] == version for key, version in frozen["revisions"].items()):
            directory.mkdir(parents=True, exist_ok=True)
            for name in files:
                if (kept / name).is_file():
                    await cleanup(asyncio.to_thread(shutil.copyfile, kept / name, directory / name))
            return {**found, "barsPath": str(directory / "bars.parquet"), "pricesPath": str(directory / "prices.parquet"),
                    "stockSessions": str(directory / "stock-sessions.parquet") if found["stockSessions"] else None, "reused": True}
    prepared = await prepare(ctx, settings, params_run_id, directory, report=report)

    def keep():
        staging = kept.with_name(kept.name + ".new")
        shutil.rmtree(staging, ignore_errors=True)
        staging.mkdir(parents=True)
        for name in files:
            if (directory / name).is_file():
                shutil.copyfile(directory / name, staging / name)
        (staging / stored.name).write_bytes(pickle.dumps(prepared))
        shutil.rmtree(kept, ignore_errors=True)
        staging.rename(kept)
    await cleanup(asyncio.to_thread(keep))
    return prepared



async def defaults(ctx, settings):
    """What the run would use for each selected symbol's venue, fees and account before the settings override them."""
    data, account = settings["data"], settings["account"]
    required_settings(data)
    data, index = await frozen_data(ctx, data)
    required_settings(data)
    rows, resolved, currency = [], [], None
    for symbol in data["symbols"]:
        try:
            instrument, series = await resolve_series(ctx, symbol["instrument"], data["timeframe"], adjustment=data.get("adjustment"))
            availability = await preflight(ctx, instrument, series, since=None if index is not None else day_ms(data['since'], False))
        except ContractError as error:
            from .index_universe import MISSING_SERIES, exclude
            if index is None or error.code not in MISSING_SERIES:
                raise
            exclude(index, symbol['instrument']['instrumentId'], index['since'], index['until'], 'no_price_series')
            continue
        if data.get('referencePrices'):
            from .reference_prices import configure
            await configure(ctx, [{'marketInstrument': instrument, 'series': series}], data['referencePrices'], since=day_ms(data['since'], False))
        resolved.append(instrument)
        currency = currency or instrument.get("quote") or instrument.get("currency")
        rows.append({"instrumentId": instrument["instrumentId"], "symbol": instrument["displaySymbol"], "venue": instrument["venueId"], 'venueLabel': instrument['venueId'],
                     "marketType": instrument["marketType"], "assetClass": instrument["assetClass"], "quote": instrument.get("quote") or instrument.get("currency"),
                     "calendarId": instrument["calendarId"], "seriesId": series["seriesId"], "timeframe": series["timeframe"], "adjustment": series["adjustment"] if stock_like(instrument) else "raw", 'history': availability})
    if not resolved:
        raise ContractError('unavailable', t("No index members have price series in the selected range."), {'index': index})
    market_terms = await trading_terms(ctx, resolved)
    listed = [(item["displaySymbol"], market_terms[item["instrumentId"]]) for item in resolved]
    traded = [entry for entry, item in zip(listed, resolved, strict=True) if item["assetClass"] not in {"equity", "fund"}]
    venues = ", ".join(sorted({item["venueId"] for item in resolved}))
    fee = one_fee(fees_for(venues, traded, account)) if traded else account.get('fee')
    market_fee = one_fee([terms["fees"] and {"maker": terms["fees"]["maker"], "taker": terms["fees"]["taker"]} for _, terms in traded])  # what Market alone gives
    for row in rows:
        term = market_terms[row['instrumentId']]
        row['tradingTerms'] = term
        if 'stock' in term:
            row['stockCommission'] = stock_commission(term['stock'], account)
    # Spot and stocks trade on cash accounts, which cannot go short; perpetuals trade on margin accounts.
    blocked = [row["symbol"] for row in rows if row["marketType"] != "swap"]
    from .index_universe import summary
    return {"instruments": rows, **({'index': summary(index)} if index is not None else {}), "canShort": {"allowed": not blocked, "blockedBy": blocked}, "account": {"currency": currency, "capital": account.get("capital", CAPITAL), "leverage": account.get("leverage", 1),
                                              "fee": fee, "defaults": {"capital": CAPITAL, "leverage": 1, "fee": market_fee or fee}}}
