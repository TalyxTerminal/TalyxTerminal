"""Market 2.0 identities, series and clocks at Quant's input boundary."""
from __future__ import annotations

import asyncio

from talyx_sdk import ContractError
from talyx_sdk.i18n import t

from vocabulary.instruments import MarketInstrument, ohlcv_series

from .materials import plain
from .public_settings import milliseconds, timestamp

MS = 1_000_000
FIELDS = ("open", "high", "low", "close", "volume")


def ns(milliseconds):
    return str(int(milliseconds) * MS)


def before_ns(milliseconds):
    """A backward cursor is exclusive; include the final millisecond."""
    return str((int(milliseconds) + 1) * MS)


def ms(nanoseconds):
    return int(nanoseconds) // MS


async def lookup(ctx, key, values):
    """The full batch, in input order. The public action pages whole instrument records."""
    items = []
    for start in range(0, len(values), 500):
        request = {key:values[start:start+500],"limit":500}
        while True:
            page = plain(await ctx.call("market.instruments.get",request))
            items.extend(page["items"])
            if page["nextCursor"] is None:
                break
            request = {**request,"cursor":page["nextCursor"]}
    return items


def require_match(item):
    if item["status"] == "ambiguous":
        raise ContractError("ambiguous", t("Qualify the symbol {symbol}.", symbol=item['symbol']), {"candidates": item["candidates"]})
    if item["status"] == "not_found":
        raise ContractError("not_found", t("Market cannot identify {symbol}.", symbol=item['symbol']))
    return item


async def symbol_items(ctx, symbols):
    return [require_match(item) for item in await lookup(ctx, "symbols", symbols)] if symbols else []


async def saved_items(ctx, records):
    """Preserve an object's original identities when its data directory or an exchange code changes."""
    if not records:
        return []
    found = await lookup(ctx, "symbols", [record["instrumentId"] for record in records])
    missing = [index for index, item in enumerate(found) if item["status"] == "not_found"]
    if missing:
        accepted = await lookup(ctx, "records", [records[index] for index in missing])
        for index, item in zip(missing, accepted, strict=True):
            found[index] = item
    return [require_match(item) for item in found]


async def resolve(ctx, text):
    return (await symbol_items(ctx, [text]))[0]["instrument"]


async def current(ctx, instrument):
    return (await saved_items(ctx, [instrument]))[0]["instrument"]


def price_series(instrument, timeframe, adjusted, *, price_basis="last"):
    adjustment = {True: "split_dividend", False: "raw", "adjusted": "split_dividend"}.get(adjusted, adjusted)
    if not instrument["adjustmentEligible"]:
        adjustment = "not_applicable"
    return ohlcv_series(MarketInstrument.from_wire(instrument), timeframe, price_basis=price_basis,
                        adjustment=adjustment, adjustment_anchor="latest" if adjustment in {"split", "split_dividend"} else None).to_dict()


def content_of(series):
    if series["capability"] == "ohlcv":
        return f"ohlcv:{series['timeframe']}:{series['adjustment']}"
    return series["capability"] + (f":{series['timeframe']}" if series["timeframe"] else "")


async def revisions(ctx, selections, until):
    """The effective read revision includes corporate actions, unlike coverage."""
    gate = asyncio.Semaphore(8)

    async def one(selection):
        async with gate:
            page = plain(await ctx.call("market.read_series", {
                "instrumentId": selection["instrument"]["instrumentId"], "series": selection["series"],
                "end": ns(until), "limit": 1, "fill": "none"}))
            return selection["series"]["seriesId"], {"revision": page["revision"], "source": page["source"]}
    return dict(await asyncio.gather(*(one(item) for item in selections)))


def stock_times(value):
    """Convert the stock material once, at the wire boundary. Decimal strings stay exact."""
    if "stock" not in value:
        return value
    stock = dict(value["stock"])
    if "acquiredAt" in stock:
        stock["acquiredAt"] = milliseconds(stock["acquiredAt"])
    stock["sessions"] = [{**row, "openAt": milliseconds(row["openAt"]), "closeAt": milliseconds(row["closeAt"])} for row in stock["sessions"]]
    if "corporateActions" in stock:
        stock["corporateActions"] = [{**row, "effectiveTime": milliseconds(row["effectiveTime"]),
                                      "publishedAt": milliseconds(row["publishedAt"]) if row["publishedAt"] is not None else None}
                                     for row in stock["corporateActions"]]
    return {**value, "stock": stock}


def decimal_scale(column):
    """Find the decimal places in Arrow's shortest float text, including exponents."""
    import pyarrow as pa
    import pyarrow.compute as pc

    text = pc.cast(column, pa.string())
    parsed = pc.extract_regex(text, r"^-?\d+(?:\.(?P<fraction>\d+))?(?:[eE](?P<exponent>[+-]?\d+))?$")
    fraction = pc.fill_null(pc.struct_field(parsed, "fraction"), "")
    exponent = pc.fill_null(pc.struct_field(parsed, "exponent"), "")
    exponent = pc.if_else(pc.equal(exponent, ""), "0", exponent)
    places = pc.subtract(pc.utf8_length(fraction), pc.cast(exponent, pa.int32()))
    return max(0, pc.max(places).as_py() or 0)


def decimal_columns(frame, fields=FIELDS):
    """Vectorized fixed-point strings, no per-cell Python Decimal conversion."""
    import pyarrow as pa
    import pyarrow.compute as pc

    arrays = {}
    for name in fields:
        column = pa.array(frame[name], type=pa.float64(), from_pandas=True)
        if column.null_count or not pc.all(pc.is_finite(column)).as_py():
            raise ContractError("invalid_input", t("Market input contains missing or nonfinite {name}.", name=name))
        scale = decimal_scale(column)
        text = pc.cast(pc.cast(column, pa.decimal128(38, scale)), pa.string())
        parts = pc.extract_regex(text, r"^(?P<sign>-?)(?P<whole>\d+)(?:\.(?P<fraction>\d+))?E(?P<exponent>-\d+)$")
        exponent = pc.struct_field(parts, "exponent")
        # Decimal's display also uses E for tiny values. Expand each exponent group in Arrow.
        for value in pc.unique(exponent).to_pylist():
            if value:
                fixed = pc.binary_join_element_wise(pc.struct_field(parts, "sign"),
                    "0." + "0" * (-int(value) - 1), pc.struct_field(parts, "whole"),
                    pc.struct_field(parts, "fraction"), "")
                text = pc.if_else(pc.fill_null(pc.equal(exponent, value), False), fixed, text)
        arrays[name] = text
    return pa.table(arrays).to_pandas()



async def observations(ctx, instrument, capability, since, until, *, timeframe=None, price_basis="last"):
    """Fetch then page backwards through one auxiliary series, retaining all rows in the window."""
    from vocabulary.instruments import market_data_series

    from .materials import run_task

    instrument = await current(ctx, instrument)
    series = (price_series(instrument, timeframe, False, price_basis=price_basis) if capability == "ohlcv" else
              market_data_series(MarketInstrument.from_wire(instrument), capability, timeframe=timeframe).to_dict())
    kind = {"kind": "bars" if capability == "ohlcv" else capability,
            **({"timeframe": series["timeframe"]} if series["timeframe"] else {})}
    if capability == "ohlcv":
        kind["priceBasis"] = price_basis
    fetched = await run_task(ctx, "market.fetch", {"symbols": [instrument["instrumentId"]], "data": [kind],
                                                   "start": timestamp(since), "end": timestamp(until)})
    if fetched["failures"]:
        problem = fetched["failures"][0]["problem"]
        raise ContractError(problem["code"], problem["message"], problem, execution="failed")
    before, rows = before_ns(until), []
    while True:
        page = plain(await ctx.call("market.read_series", {
            "instrumentId": instrument["instrumentId"], "series": series, "before": before, "limit": 10000, "fill": "none"}))
        columns = page["columns"]
        def selected_rows():
            return [{name: column[index] for name, column in columns.items()}
                    for index, stamp in enumerate(columns["time"]) if since <= ms(stamp) <= until]
        rows.extend(await asyncio.to_thread(selected_rows))
        if not page["hasMoreBefore"] or not columns["time"] or ms(columns["time"][0]) <= since:
            break
        before = page["nextBefore"]
    return await asyncio.to_thread(sorted, rows, key=lambda row: int(row["time"]))


def table_rows(page):
    names = [column["name"] for column in page["columns"]]
    return [dict(zip(names, row, strict=True)) for row in page["rows"]]


async def coverage(ctx, instruments, data):
    args = {"symbols": [item["instrumentId"] for item in instruments], "data": data, "limit": 500}
    rows = []
    while True:
        page = plain(await ctx.call("market.read_coverage", args))
        rows.extend(table_rows(page))
        if page["nextCursor"] is None:
            return rows
        args["cursor"] = page["nextCursor"]
