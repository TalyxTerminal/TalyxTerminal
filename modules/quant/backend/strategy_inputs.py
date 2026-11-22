"""What a strategy backtest reads beside its bars, all through Market and fixed with the run: the benchmark's daily closes, the
13-week T-bill (^IRX) for the risk-free rate, perpetual funding history, and the stock sessions file."""
from __future__ import annotations

import ast
import asyncio
import bisect
import time

import pandas as pd
from talyx_sdk import ContractError
from talyx_sdk.i18n import t

from . import market_inputs as M
from .materials import Materials, read_selection, release

DAY = 86_400_000
FUNDING_PAGE = 1000


async def daily_closes(ctx, instrument, series, since, until, owner, *, clock="time"):
    from .research_calendar import date_windows
    windows = await date_windows(ctx, [instrument], "1d", since, until)
    captured = await Materials(ctx).capture({"market": {"selections": [{"instrument": instrument, "series": series}],
                                          "since": since, "until": until, "windows": windows, "allowEmpty": True}}, owner)
    try:
        selection = captured["manifest"]["selections"][0]
        if "problem" in selection:
            from .market_capture import failure
            raise failure(selection["problem"], instrument)

        def read():
            frame = read_selection(selection)
            return [[int(at), float(value)] for at, value in zip(frame[clock], frame["close"], strict=True) if pd.notna(value)]
        return await asyncio.to_thread(read)
    finally:
        await release(ctx, captured["references"], owner)


async def risk_free(ctx, spec, since, until, owner):
    """The risk-free rate as the report reads it: zero, a fixed percent a year, or ^IRX closes (percent a year) from Market. `spec` is {basis, pct?}."""
    basis = (spec or {}).get("basis", "zero")
    if basis == "zero":
        return {"basis": "zero"}
    if basis == "fixed":
        return {"basis": "fixed", "pct": spec.get("pct")}
    try:
        instrument = await M.resolve(ctx, "^IRX")
        resolved = {"instrument": instrument, "series": M.price_series(instrument, "1d", False)}
        rows = await daily_closes(ctx, resolved["instrument"], resolved["series"], since - 30 * DAY, until, owner + ":irx", clock="knownAt")
    except ContractError as error:
        return {"basis": "irx", "rows": [], "unavailable": error.message}
    return {"basis": "irx", "rows": rows, **({} if rows else {"unavailable": "Market has no ^IRX closes in this period"})}


async def benchmark(ctx, spec, since, until, owner):
    """The benchmark as the report reads it. `spec` is {kind, symbol?}: hold (what the strategy trades, from the run's own bars), none, equal (the
    run's members equally weighted each day, from its bars), or symbol (any Market symbol's daily closes, split- and dividend-adjusted for a stock
    or fund)."""
    kind = spec["kind"]
    if kind in ("hold", "equal", "none"):
        return {"kind": kind}
    symbol = spec["symbol"]
    from .object_data import resolve_series, resolve_text
    try:
        instrument = await resolve_text(ctx, symbol)
        instrument, series = await resolve_series(ctx, instrument, "1d")
        rows = await daily_closes(ctx, instrument, series, since - 7 * DAY, until, owner + ":benchmark")
    except ContractError as error:
        return {"kind": "series", "label": symbol, "symbol": symbol, "rows": [], "unavailable": f"{symbol}: {error.message}"}
    return {"kind": "series", "label": instrument["displaySymbol"], "symbol": symbol,
            "rows": rows, **({} if rows else {"unavailable": f"Market has no daily closes of {symbol} in this period"})}


async def funding_history(ctx, instrument, since, until):
    """Market's historical funding settlements of one perpetual, page by page: ([(ms, rate)], gaps [{from, to, reason}])."""
    records = await M.observations(ctx, instrument, "funding_rate", since, until)
    rows = await asyncio.to_thread(lambda: [(M.ms(item["time"]), float(item["rate"])) for item in records])
    gaps = []
    rows = sorted(dict(rows).items())
    if not rows:
        if not gaps:
            gaps.append({"from": since, "to": until, "reason": t("Market has no funding settlements for this period")})
        return [], gaps
    steps = sorted(b[0] - a[0] for a, b in zip(rows, rows[1:], strict=False)) or [8 * 3_600_000]
    usual = steps[len(steps) // 2]
    if rows[0][0] - since > 2 * usual:
        gaps.append({"from": since, "to": rows[0][0], "reason": t("Market's funding history starts later")})
    for (a, _), (b, _) in zip(rows, rows[1:], strict=False):
        if b - a > 3 * usual:
            gaps.append({"from": a, "to": b, "reason": t("No funding settlements reported")})
    if until - rows[-1][0] > 2 * usual and until < int(time.time() * 1000) - usual:
        gaps.append({"from": rows[-1][0], "to": until, "reason": t("Market's funding history ends earlier")})
    return rows, gaps


async def settlement_marks(ctx, instrument, rows, since, until, gaps):
    """The mark price at each funding settlement (the close of the hourly mark-price bar that ends there): what the venue charges funding on. Settlements Market has no mark price for are charged on the last trade price, and the run's notes say from when to when."""
    if not rows:
        return {}, gaps
    try:
        records = await M.observations(ctx, instrument, "ohlcv", since, until, timeframe="1h", price_basis="mark")
        closes = await asyncio.to_thread(lambda: sorted((M.ms(row["closeTime"]), float(row["close"])) for row in records))
    except ContractError:
        closes = []
    times = [at for at, _ in closes]
    marks = {}
    for at, _ in rows:
        position = bisect.bisect_right(times, at) - 1
        if position >= 0 and at - times[position] <= 3_600_000:
            marks[at] = closes[position][1]
    missing = [at for at, _ in rows if at not in marks]
    if missing:
        gaps = [*gaps, {"from": missing[0], "to": missing[-1], "reason": t("no mark price at {missing} of {rows} settlements; funding there is on the last trade price", missing=len(missing), rows=len(rows))}]
    return marks, gaps


async def funding(ctx, sources, since, until, directory):
    """Every perpetual's funding history in one file (instrument, time ms, rate), and its gaps by instrument, for the cost model."""
    perpetuals = [item for item in sources if item["marketInstrument"]["marketType"] == "swap"]
    if not perpetuals:
        return None
    gate = asyncio.Semaphore(4)

    async def one(item):
        async with gate:
            rows, gaps = await funding_history(ctx, item["marketInstrument"], since, until)
            marks, gaps = await settlement_marks(ctx, item["marketInstrument"], rows, since, until, gaps)
            return item["nativeInstrument"], ([(at, rate, marks.get(at)) for at, rate in rows], gaps)

    found = await asyncio.gather(*(one(item) for item in perpetuals))
    path = directory / "funding.parquet"
    def write():
        table = pd.DataFrame([{"instrument": name, "time": at, "rate": rate, "mark": mark} for name, (rows, _) in found for at, rate, mark in rows],
                             columns=["instrument", "time", "rate", "mark"])
        table.to_parquet(path, index=False)
    await asyncio.to_thread(write)
    gaps = {name: [[gap["from"] * 1_000_000, gap["to"] * 1_000_000, gap["reason"]] for gap in found_gaps] for name, (_, found_gaps) in found if found_gaps}
    return {"path": str(path), "gaps": gaps, "settlements": {name: len(rows) for name, (rows, _) in found}}


# --- the names the code asks Market for with q.series("name") ------------------------------------------------------------------
OPTIONAL_SERIES = {"industry"}  # a preset reads it when it can; a name Market has none for is not an error


def series_names(files):
    """The series names written as literals in `q.series("name")` across the project's files `{path: text}`."""
    names = set()
    for text in files.values():
        try:
            tree = ast.parse(text)
        except SyntaxError:
            continue
        for node in ast.walk(tree):
            if isinstance(node, ast.Call) and node.args and isinstance(node.args[0], ast.Constant) and isinstance(node.args[0].value, str):
                function = node.func
                if (isinstance(function, ast.Attribute) and function.attr == "series" and isinstance(function.value, ast.Name) and function.value.id in {"q", "talyx_quant"}) \
                        or (isinstance(function, ast.Name) and function.id == "series"):
                    names.add(node.args[0].value)
    return names


async def extras(ctx, names, sources, since, until, directory, progress=None, notes=None):
    """Each named series per name, frozen as (time ms, value) rows a strategy reads up to its simulation time. `industry` is each name's
    industry by the date it took effect, as Market knew it then where it keeps dated answers (A shares); a source with only its current
    classification gives one row from the start, said in `notes`."""
    out = {}
    for name in sorted(names):
        paths = {}
        if name == "industry":
            from .research_inputs import industries
            histories, problems = await industries(ctx, [item["marketInstrument"] for item in sources], since, until, progress=progress)
            first = next(((identity, problem) for identity, problem in problems.items() if problem["code"] not in {"no_data", "unsupported", "not_found"}), None)
            if first is not None:
                from .market_capture import failure
                raise failure(first[1], next(item["marketInstrument"] for item in sources if item["marketInstrument"]["instrumentId"] == first[0]))
        for item in sources:
            instrument = item["marketInstrument"]
            if name == "funding_rate":
                rows, _ = await funding_history(ctx, instrument, since, until)
            elif name == "industry":
                # Each row at the date it took effect (an undated one from the start of the data); a strategy sees it from then on.
                rows = [(0 if start is None else start, label) for start, _end, label in histories.get(instrument["instrumentId"], ())]
            else:
                basis = {"mark_price": "mark", "index_price": "index"}.get(name)
                records = await M.observations(ctx, instrument, "ohlcv" if basis else name, since, until,
                                               timeframe=item["series"]["timeframe"] if basis else None,
                                               price_basis=basis or "last")
                # A bar's close is known when the bar closes: stamped at its close, a strategy never reads the bar it is inside.
                rows = await asyncio.to_thread(lambda: [(M.ms(row["closeTime" if basis else "time"]), float(row["close" if basis else "value"]))
                                                        for row in records])
            target = directory / "extras" / name
            path = target / (item["nativeInstrument"] + ".parquet")
            def write():
                target.mkdir(parents=True, exist_ok=True)
                pd.DataFrame(rows, columns=["time", "value"]).to_parquet(path, index=False)
            await asyncio.to_thread(write)
            paths[item["nativeInstrument"]] = str(path)
        out[name] = paths
    return out


async def conversions(ctx, currencies, report, since, until, directory, owner):
    """Daily closes that turn each other account currency into the report currency (e.g. USDT into USD), frozen with the run; raises `no_conversion`
    for a pair Market has no price for."""
    out = []
    for currency in sorted(set(currencies) - {report}):
        from .object_data import resolve_series, resolve_text
        instrument = await resolve_text(ctx, f"{currency}/{report}")
        instrument, series = await resolve_series(ctx, instrument, "1d")
        rows = await daily_closes(ctx, instrument, series, since - 7 * DAY, until, f"{owner}:{currency}{report}", clock="knownAt")
        if not any(at <= since for at, _ in rows):
            raise ContractError("no_conversion", t("No known {currency}/{report} rate at the start ({since}).", currency=currency, report=report, since=M.timestamp(since)),
                                {"currency": currency, "report": report, "at": since})
        path = directory / f"conversion-{currency}.parquet"
        await asyncio.to_thread(lambda: pd.DataFrame(rows, columns=["time", "close"]).to_parquet(path, index=False))
        out.append({"currency": currency, "path": str(path)})
    return out
