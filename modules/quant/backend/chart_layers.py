"""Project a frozen run onto Chart's public research layer, using its actual prices and clocks."""

from __future__ import annotations

import asyncio
import json
from decimal import ROUND_FLOOR, Decimal

import numpy as np
import pandas as pd
from talyx_sdk import ContractError

from vocabulary.identity import timeframe_nanoseconds

from .public_pages import complete_json
from .public_settings import milliseconds, timestamp
from .run_materials import run_directory

BAR_LIMIT = 200_000
DAY = 86_400_000


def read_json(path):
    return json.loads(path.read_bytes())


def window(opens, end, around):
    if not len(opens):
        raise ContractError("not_found", "This instrument has no bars in the run.")
    last_start = max(0, len(opens) - BAR_LIMIT)
    start = (
        last_start
        if around is None
        else int(
            np.clip(
                np.searchsorted(opens, milliseconds(around), side="right") - 1 - BAR_LIMIT // 2,
                0,
                last_start,
            )
        )
    )
    stop = start + BAR_LIMIT
    return int(opens[start]), int(opens[stop] - 1 if stop < len(opens) else end)


def points(pairs, limits):
    begin, end = limits
    return [
        {"time": timestamp(int(at)), "value": value} for at, value in pairs if begin <= at <= end
    ]


def series(key, name, pane, pairs, limits, *, role="data", style="line"):
    return {
        "key": key,
        "name": name,
        "pane": pane,
        "style": style,
        "role": role,
        "points": points(pairs, limits),
    }


def pane(key, title, position="below"):
    return {"key": key, "title": title, "position": position}


def selected(items, requested):
    if not items:
        raise ContractError("not_found", "This run has no instruments with values.")
    if requested is None:
        return items[0]
    for item in items:
        if requested in (item["instrumentId"], item["key"]):
            return item
    raise ContractError("invalid_input", f"{requested} is not an instrument in this run.")


def contribution_amounts(items, net):
    """Display amounts to two places, balancing the rounding to the portfolio total when possible."""
    amounts = [Decimal(str(item["pnl"])) * 100 for item in items]
    target = round(Decimal(str(net)) * 100)
    cents = [int(amount.to_integral_value(rounding=ROUND_FLOOR)) for amount in amounts]
    fractions = [amount - whole for amount, whole in zip(amounts, cents, strict=True)]
    remainder = target - sum(cents)
    ranked = sorted(
        (i for i, fraction in enumerate(fractions) if fraction),
        key=fractions.__getitem__,
        reverse=True,
    )
    if 0 <= remainder <= len(ranked):
        for i in ranked[:remainder]:
            cents[i] += 1
    else:
        # A portfolio can also gain on uninvested foreign cash; don't assign that amount to a security.
        cents = [round(amount) for amount in amounts]
    for item, whole in zip(items, cents, strict=True):
        item["pnl"] = whole / 100
    return {"net": target / 100, "remainder": (target - sum(cents)) / 100}


def strategy(services, record, requested, around, compare):
    directory = run_directory(services.runs, record)
    folder = directory / "output" / "strategy"
    work = read_json(folder / "work.json")
    sources = read_json(directory / "info.json")["sources"]
    multi = work.get("multi", {})
    contributions = {r["nativeInstrument"]: r for r in multi.get("byInstrument", [])}
    industries = read_json(directory / "invocation.json").get("industry", {})
    fills = pd.read_parquet(folder / "fills.parquet")
    fill_counts = fills.instrument.value_counts() if len(fills) else {}
    items = []
    for source in sources:
        native, instrument = source["nativeInstrument"], source["marketInstrument"]
        row = {"instrumentId": instrument["instrumentId"], "symbol": instrument["displaySymbol"], "key": native}
        if multi:
            contribution = contributions.get(native, {})
            row.update(sector=industries.get(native) or "Unclassified", pnl=contribution.get("pnl", 0),
                       trades=int(fill_counts.get(native, 0)), weight=contribution.get("weight", 0))
        items.append(row)
    attribution = None
    if multi:
        items.sort(key=lambda r: -abs(r["pnl"]))
        attribution = contribution_amounts(items, work["costs"]["net"])
    item = selected(items, requested)
    source = next(s for s in sources if s["nativeInstrument"] == item["key"])
    timeframe = source["series"]["timeframe"]
    prices = pd.read_parquet(
        directory / "prices.parquet", columns=["time", "instrument", "knownAt", "label"]
    ).sort_values("time")
    opens = prices.loc[prices.instrument == item["key"], "time"].to_numpy()
    # An inclusive window must end before the next bar opens, including on a continuous market.
    duration = timeframe_nanoseconds(timeframe) // 1_000_000
    ends = np.minimum(prices.time + duration - 1, prices.knownAt)
    end = int(ends[prices.instrument == item["key"]].max())
    limits = window(opens, end, around)
    if multi and len(opens) <= BAR_LIMIT:
        limits = (int(prices.time.iloc[0]), int(ends.max()))
    panes = [pane("equity", "Equity", "above")]
    # Work dates are the trading day's close. On an Asian daily chart its local open can be the preceding UTC day.
    if timeframe.endswith("d"):
        trading_days = prices.label.to_numpy()
    else:
        trading_days = (prices.time.to_numpy() + duration - 1) // DAY * DAY
    last_of_day = dict(zip(trading_days, prices.time, strict=True))

    def equity_pairs(rows):
        return ((last_of_day.get(t, t + DAY - 1), v) for t, v in rows)

    lines = [
        series(
            "equity", record["name"], "equity", equity_pairs(work["equity"]), limits, role="main"
        )
    ]
    if work["benchmark"]:
        lines.append(
            series(
                "benchmark",
                work["benchmarkLabel"],
                "equity",
                equity_pairs(work["benchmark"]),
                limits,
                role="benchmark",
            )
        )
    for other, shown in compare:
        other_work = services.strategy.report(shown, "work")
        if not other_work:
            continue
        lines.append(
            series(
                "compare-" + other["id"],
                f"{other['name']} #{other['number']}",
                "equity",
                equity_pairs(other_work["equity"]),
                limits,
                role="compare",
            )
        )
    # q.plot records the callback's simulation clock. Match the bar that became available at that clock.
    delivered = prices.drop_duplicates(["knownAt", "time"]).sort_values(["knownAt", "time"])
    clocks, source_opens = delivered.knownAt.to_numpy(), delivered.time.to_numpy()
    for i, plot in enumerate(work["plots"]):
        if plot["pane"] == "price" and len(items) > 1:
            continue
        key = f"plot-{i}"
        dest = key if plot["pane"] == "own" else "price"
        if dest == key:
            panes.append(pane(key, plot["name"]))
        at = np.asarray(plot["t"], dtype="int64")
        pos = np.searchsorted(clocks, at, side="right") - 1
        values = {
            int(source_opens[j]): value
            for j, value in zip(pos, plot["values"], strict=True)
            if j >= 0 and limits[0] <= source_opens[j] <= limits[1]
        }
        lines.append(series(key, plot["name"], dest, values.items(), limits, style=plot["style"]))
    if len(fills):
        fills = fills[fills.instrument == item["key"]]
    markers = [
        {"time": timestamp(int(r.time)), "side": r.side, "price": r.price, "quantity": r.quantity}
        for r in fills.itertuples()
        if limits[0] <= r.time <= limits[1]
    ]
    adjustment = source["series"]["adjustment"]
    found = result(
        record,
        item,
        items,
        timeframe,
        "raw" if adjustment == "not_applicable" else adjustment,
        limits,
        len(opens),
        panes,
        lines,
        markers,
    )
    if attribution:
        found["attribution"] = {**attribution, "currency": work["currency"]}
    return found


def rank_percentile(values, column):
    """Percentile of one column per row, excluding missing names and averaging ties."""
    chosen = values[:, column : column + 1]
    eligible = np.isfinite(values)
    n = eligible.sum(axis=1)
    below = ((values < chosen) & eligible).sum(axis=1)
    tied = (values == chosen).sum(axis=1)
    return np.where(
        np.isfinite(chosen[:, 0]),
        np.where(n == 1, 100.0, 100 * (below + (tied - 1) / 2) / np.maximum(n - 1, 1)),
        np.nan,
    )


def research(services, record, requested, around):
    folder = run_directory(services.runs, record) / "output"
    data = services.factors.data.directory(record["data"]["key"])
    info = read_json(data / "info.json")
    factor = record["objectKind"] == "factor"
    meta_path = folder / ("cross/meta.json" if factor else "pred.json")
    if not meta_path.exists():
        raise ContractError("not_found", "This run's values were not retained.")
    meta = read_json(meta_path)
    if factor:
        path = folder / "values.npy"
        values = np.load(path if path.exists() else folder / "cross/raw.npy")
        values = np.where(np.load(folder / "cross/missing.npy") == 1, np.nan, values)
    else:
        values = np.load(folder / "pred.npy", mmap_mode="r")
    valid_days = np.flatnonzero(np.isfinite(values).any(axis=1))
    if not len(valid_days):
        raise ContractError("not_found", "This run has no scored values.")
    latest = values[valid_days[-1]]
    by_column = {i["column"]: i for i in info["instruments"]}
    items = [
        {
            "instrumentId": by_column[c]["instrumentId"],
            "key": c,
            "symbol": by_column[c]["displaySymbol"],
            "latest": float(latest[j]) if np.isfinite(latest[j]) else None,
        }
        for j, c in enumerate(meta["columns"])
    ]
    items.sort(key=lambda row: (row["latest"] is None, -(row["latest"] or 0), row["symbol"]))
    item = selected(items, requested)
    j = meta["columns"].index(item["key"])
    labels = pd.Index(meta["labels"])
    bars = pd.read_parquet(
        data / "bars.parquet",
        columns=["time", "knownAt", "label"],
        filters=[("column", "==", item["key"])],
    )
    # A model scores only its test windows: the price window starts at the first scored day.
    bars = bars[bars.label.isin(labels[valid_days[0]:])].sort_values("time")
    if bars.empty:
        raise ContractError("not_found", "This instrument has no prices in the run.")
    count = len(bars)
    duration = timeframe_nanoseconds(info["timeframe"]) // 1_000_000
    # A value is known at its bar's close. On a continuous market or an intraday bar that is the next bar's open,
    # so it is drawn at the last instant of its own bar.
    bars["drawn"] = np.minimum(bars.knownAt, bars.time + duration - 1)
    limits = window(bars.time.to_numpy(), int(bars.drawn.iloc[-1]), around)
    bars = bars[bars.drawn.between(*limits)]
    span = values[labels.get_indexer(bars.label)]
    ranks = rank_percentile(span, j)
    name = "Factor value" if factor else "Prediction"

    def samples(vector):
        return zip(
            bars.drawn, (float(v) if np.isfinite(v) else None for v in vector), strict=True
        )

    lines = [
        series("value", name, "value", samples(span[:, j]), limits),
        series("rank", "Percentile", "rank", samples(ranks), limits),
    ]
    adjustment = by_column[item["key"]]["series"]["adjustment"]
    return result(
        record,
        item,
        items,
        info["timeframe"],
        "raw" if adjustment == "not_applicable" else adjustment,
        limits,
        count,
        [pane("value", name), pane("rank", "Percentile")],
        lines,
        [],
    )


def result(record, item, items, timeframe, adjustment, limits, count, panes, lines, markers):
    if len(markers) > 20_000:
        raise ContractError(
            "too_large",
            f"{len(markers):,} markers, {len(markers) - 20_000:,} over the limit of 20,000.",
        )
    interval = {"from": timestamp(limits[0]), "to": timestamp(limits[1])}
    layer = {
        "id": "quant:" + record["id"],
        "label": f"{record['name']} #{record['number']}",
        "instrumentId": item["instrumentId"],
        "timeframe": timeframe,
        "adjustment": adjustment,
        "window": interval,
        "panes": panes,
        "series": lines,
        "markers": markers,
    }
    return {
        "layer": layer,
        "window": interval,
        "truncated": count > BAR_LIMIT,
        "instrumentId": item["instrumentId"],
        "instruments": items,
    }


async def read(services, record, args):
    if record["state"] != "completed":
        raise ContractError("invalid_input", "Choose a completed run.")
    inputs = (services, record, args.get("instrumentId"), args.get("around"))
    if record["objectKind"] == "strategy":
        compare = args.get("compareTo") or []
        comparisons = []
        for identity in ([compare] if isinstance(compare, str) else compare):
            other = services.runs.read(identity)
            if other["objectKind"] != "strategy":
                raise ContractError("invalid_input", "The comparison must be a strategy run.")
            comparisons.append((other, services.strategy.shown(other)))
        found = await asyncio.to_thread(strategy, *inputs, comparisons)
    elif record["objectKind"] in {"factor", "model"}:
        if args.get("compareTo"):
            raise ContractError("invalid_input", "Comparison lines apply only to a strategy chartLayer.")
        found = await asyncio.to_thread(research, *inputs)
    else:
        raise ContractError(
            "invalid_input", "Price layers are available for strategy, factor and model runs."
        )
    payload = await complete_json(services.store.ctx, found["layer"], "research-layer.json")
    layer = payload["file"] if "file" in payload else payload
    chart = found["layer"]
    buys = sum(marker["side"] == "buy" for marker in chart["markers"])
    stats = record.get("stats") or {}
    return {
        "layer": layer,
        "label": chart["label"],
        "instrumentId": found["instrumentId"],
        "truncated": found["truncated"],
        **{key: stats[key] for key in ("trades", "openAtEnd") if key in stats},
        "timeframe": chart["timeframe"],
        "adjustment": chart["adjustment"],
        "window": chart["window"],
        "buys": buys,
        "sells": len(chart["markers"]) - buys,
        **({key: found[key] for key in ("instruments", "attribution") if key in found} if args.get("includeInstruments") else {}),
    }
