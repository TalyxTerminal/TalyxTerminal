"""What an AI caller reads of a strategy run: the same reads (`strategy_views.py`), each cut to the Host's AI response budget and keeping what
the AI acts on.

The key numbers, states, checks, settings and failures stay; chart figures go, a series becomes its span, first and last value, low and high (the
equity also its month ends), and a figure's lines the same. Names stand in for instrument objects, moments are days (with the time when it is not
midnight UTC) except where an action takes the moment back (a rebalance's `at`, a drawdown's `since`/`until`). A long list keeps its most relevant
entries (the largest trades, the orders that did not fill, the newest log lines and rebalances, the biggest holdings and contributions) with how many
there are and which read pages the rest; the paged reads (trades, orders, rebalances) give as many rows as fit with the offset to read on. The page
in the app reads the full answers. On a realistic run (70 names, five months) the reads were 30 to 770 KB and the Agent could read none of them:
"Response exceeds AI budget"."""
from __future__ import annotations

import json
from collections import Counter
from datetime import UTC, date, datetime

from .library_ai import BUDGET, CAPS, data_of, fit, head, size, text

FIGURE_KEYS = {"figure", "figures", "capacityFigure"}
READ_TRADES = "read_run_table table trades (limit, cursor, start, end)"
READ_ORDERS = "read_run_table table orders"
READ_REBALANCES = "read_run_table table rebalances"
DAY = 86_400_000


# --- values ------------------------------------------------------------------------------------------------------------------
def num(value):
    """A number to six significant digits (the engine writes ten)."""
    return float(f"{value:.6g}") if isinstance(value, float) else value


def when(moment):
    """A moment in milliseconds as a day, with the time when it is not midnight UTC."""
    if not isinstance(moment, (int, float)) or isinstance(moment, bool):
        return moment
    found = datetime.fromtimestamp(moment / 1000, tz=UTC)
    return found.strftime("%Y-%m-%d") if (found.hour, found.minute) == (0, 0) else found.strftime("%Y-%m-%d %H:%M")


def millis(day, end=False):
    """A day ("2026-08-04") as the milliseconds an action takes: its start, or its last moment."""
    try:
        start = int(datetime.combine(date.fromisoformat(str(day)[:10]), datetime.min.time(), tzinfo=UTC).timestamp() * 1000)
    except ValueError:
        return None
    return start + DAY - 1 if end else start


def is_figure(value):
    return isinstance(value, dict) and isinstance(value.get("data"), list) and "layout" in value


def lean(value, cap):
    """Any other part as the AI reads it: no chart figures, numbers to six digits, a long list as its first entries and how many, a long text cut."""
    if isinstance(value, dict):
        return {key: lean(item, cap) for key, item in value.items() if key not in FIGURE_KEYS and not is_figure(item)}
    if isinstance(value, list):
        if len(value) > cap:
            return {"first": [lean(item, cap) for item in value[:cap]], "count": len(value)}
        return [lean(item, cap) for item in value]
    if isinstance(value, str):
        return head(value, 400)
    return num(value)


def whole(value):
    """A part kept whole (a statistics table, the checks), still without figures and with numbers to six digits."""
    return lean(value, 10_000)


def compact(row, drop=()):
    """A table row without its empty fields."""
    return {key: num(value) for key, value in row.items() if key not in drop and value is not None and value != [] and value != ""}


def first(rows, cap, shape=lambda row: row):
    """A long list as its first `cap` entries and how many there are."""
    rows = list(rows)
    return [shape(row) for row in rows] if len(rows) <= cap else {"first": [shape(row) for row in rows[:cap]], "count": len(rows)}


# --- series and figures ------------------------------------------------------------------------------------------------------
def series_of(points, months=0):
    """A [time, value] series: its span, first and last value, low and high with their days, and (given `months`) its last month ends."""
    kept = [(point[0], point[1]) for point in points or [] if isinstance(point, (list, tuple)) and len(point) > 1 and isinstance(point[1], (int, float))]
    if not kept:
        return {"points": len(points or [])}
    low, high = min(kept, key=lambda point: point[1]), max(kept, key=lambda point: point[1])
    out = {"points": len(points), "from": when(kept[0][0]), "to": when(kept[-1][0]), "first": num(kept[0][1]), "last": num(kept[-1][1]),
           "low": [when(low[0]), num(low[1])], "high": [when(high[0]), num(high[1])]}
    if months:
        ends = {}
        for moment, value in kept:
            ends[str(when(moment))[:7]] = value
        out["monthEnds"] = {month: num(value) for month, value in list(ends.items())[-months:]}
    return out


def column_of(points, key):
    return [[point.get("t"), point.get(key)] for point in points or [] if isinstance(point, dict)]


def lines_of(figures):
    """A figure's lines (not its bars, markers, heatmaps or reference lines) as series: {figure: {line: span, last, low, high}}."""
    out = {}
    for name, figure in (figures or {}).items():
        if not is_figure(figure):
            if isinstance(figure, dict) and figure.get("unavailable"):
                out[name] = {"unavailable": figure["unavailable"]}
            continue
        found = {}
        for trace in figure["data"]:
            if trace.get("type") != "scatter" or trace.get("mode") != "lines" or trace.get("hoverinfo") == "skip" or not isinstance(trace.get("y"), list):
                continue
            x = [str(item)[:10] if isinstance(item, str) else item for item in trace.get("x") or []]
            found[str(trace.get("name") or len(found))] = {key: value for key, value in series_of(list(zip(x, trace["y"], strict=False))).items() if key != "points"}
        if found:
            out[name] = found
    return out


# --- rows --------------------------------------------------------------------------------------------------------------------
def trade_row(row):
    costs = sum(row.get(key) or 0 for key in ("fees", "spread", "impact", "funding"))
    out = compact({key: row.get(key) for key in ("id", "instrument", "side", "entryPrice", "exitPrice", "markPrice", "qty", "pnl", "ret", "exitBy", "mae", "mfe", "days")})
    return {**out, "entry": when(row.get("entry")), "exit": when(row.get("exit")) if row.get("exit") is not None else "open", "costs": num(float(costs))}


def order_row(row):
    out = compact(row, drop=("time", "filledAt"))
    out["time"] = when(row.get("time"))
    if row.get("filledAt") is not None:
        out["filledAt"] = when(row["filledAt"])
    return out


def weights(found, cap):
    held = sorted(((name, value) for name, value in (found or {}).items() if value), key=lambda pair: -abs(pair[1]))
    return {"largest": {name: num(value) for name, value in held[:cap]}, "names": len(held)}


def rebalance_row(row, cap=10):
    """One rebalance: when (`at` for read_strategy_rebalances), how many orders, the names that did not fill and why, the weights aimed for and held."""
    orders = row["orders"]
    out = {"at": row.get("at"), "day": when(row.get("at")), "orders": len(orders), "filled": sum(order["status"] == "Filled" for order in orders),
           "unfilled": first(row.get("unfilled") or [], cap, compact), "targets": weights(row.get("targets"), cap), "held": weights(row.get("actual"), cap)}
    if row.get("skipped"):
        out["skipped"] = head(str(row["skipped"]), 300)
    return out


def page_of(found, shape):
    """A page of rows as the AI reads it: as many rows from the offset as fit, the total, and the offset to read on."""
    rows = [shape(row) for row in found.get("rows") or []]
    offset, total = found.get("offset", 0), found.get("total", len(rows))

    def make(count):
        return {"rows": rows[:count], "total": total, "offset": offset, "nextOffset": offset + count if offset + count < total else None}
    low, high = 0, len(rows)
    if size(make(high)) <= BUDGET:
        return make(high)
    while low < high:
        middle = (low + high + 1) // 2
        if size(make(middle)) <= BUDGET:
            low = middle
        else:
            high = middle - 1
    return make(low)


# --- one run (read_strategy_run) -----------------------------------------------------------------------------------------------
def trades_of(table, cap):
    """The largest wins and losses of the whole run (its trades table), how many are open, and how to page all."""
    count = max(cap // 2, 5)
    if len(table):
        closed = table[table["pnl"].notna()]
        wins = closed[closed["pnl"] > 0].nlargest(count, "pnl").to_dict("records")
        losses = closed[closed["pnl"] < 0].nsmallest(count, "pnl").to_dict("records")
        still = int(table["exit"].isna().sum())
    else:
        wins, losses, still = [], [], 0
    total = len(table)

    def clean(rows):  # NaN (an open trade's exit) as empty
        return [trade_row({key: (None if value != value else value) for key, value in row.items()}) for row in rows]
    return {"total": total, "largestWins": clean(wins), "largestLosses": clean(losses), "openAtEnd": still, "read": READ_TRADES}


def log_of(work, cap):
    """The log's newest lines other than the trade lines (read_strategy_trades has the trades), with how many lines of each kind."""
    lines = work.get("log") or []
    shown = [line for line in lines if line.get("kind") != "Trade"]
    out = {"total": work.get("logTotal", len(lines)), "kinds": dict(Counter(str(line.get("kind")) for line in lines)),
           "latest": [{**compact(line, drop=("t",)), "t": when(line.get("t")), **({"text": head(line["text"], 300)} if isinstance(line.get("text"), str) else {})} for line in shown[-cap:]]}
    if out["total"] > len(lines):
        out["kindsCounted"] = len(lines)  # the work keeps the first lines; the kinds count those
    return out


def multi_of(multi, latest, decisions, cap):
    """A portfolio run's rebalances (`latest`: the newest), decisions, turnover, holdings and contributions, cut to the newest and the largest."""
    out = {"rebalances": {"total": multi["rebalancesTotal"], "latest": [rebalance_row(row, cap) for row in latest[-max(cap // 4, 1):]], "read": READ_REBALANCES},
           "unfilled": first([{"at": at, "day": when(at), "names": count} for at, count in (multi.get("unfilled") or [])][::-1], cap),
           "decisions": {"total": len(decisions), "states": dict(Counter(str(item.get("state")) for item in decisions)),
                         "latest": [{"day": when(item.get("t")), "state": item.get("state"), "reason": head(item.get("reason"), 300)} for item in decisions if item.get("state") != "rebalanced"][-cap:]},
           "turnover": series_of(multi.get("turnover")),
           "holdings": {"total": multi.get("holdingsTotal", len(multi.get("holdings") or [])),
                        "largest": [compact(row, drop=("nativeInstrument",)) for row in sorted(multi.get("holdings") or [], key=lambda row: -abs(row.get("weight") or 0))[:cap]]},
           "contributions": {key: [compact(row, drop=("nativeInstrument",)) for row in (multi.get("contributions") or {}).get(key) or []][:cap] for key in ("top", "bottom")},
           "names": len(multi.get("byInstrument") or [])}
    for key, value in multi.items():
        if key not in out and key not in ("rebalancesTotal", "holdingsTotal", "contributions", "byInstrument"):
            out[key] = lean(value, cap)
    return out


def tables_of(views, run_id, work):
    """The run's tables the AI's Work reads: its trades and, for a portfolio, its newest rebalances and the decisions."""
    if not isinstance(work, dict):
        return {}
    record = views.shown(views.object_runs.read(run_id))
    out = {"trades": views.table(record, "trades")}
    if "multi" in work:
        out["latest"] = views.rebalance_rows(views.rebalance_table(record).tail(max(CAPS[0] // 4, 1)))
        out["decisions"] = views.table(record, "decisions").to_dict("records")
    return out


def work_of(work, tables, cap):
    if not isinstance(work, dict):
        return work
    out = {"trades": trades_of(tables["trades"], cap)}
    for key, value in work.items():
        if key in FIGURE_KEYS or key in ("tradesTotal", "logTotal"):
            continue
        if key == "equity":
            out[key] = series_of(value, cap)
        elif key in ("benchmark", "drawdown"):
            out[key] = series_of(value)
        elif key == "dailyReturns":
            out[key] = returns_of(value)
        elif key == "exposure":
            out[key] = exposure_of(value)
        elif key == "log":
            out[key] = log_of(work, cap)
        elif key == "instruments":
            out[key] = {"names": [item.get("symbol") or item.get("id") for item in value or []][:cap], "count": len(value or [])}
        elif key == "multi" and isinstance(value, dict):
            out[key] = multi_of(value, tables["latest"], tables["decisions"], cap)
        elif key == "plots":
            out[key] = plots_of(value, cap)
        elif key == "compared" and isinstance(value, dict):
            out[key] = {**lean({name: item for name, item in value.items() if name not in ("equity", "also")}, cap), "equity": series_of(value.get("equity")),
                        "also": [{**lean({name: item for name, item in other.items() if name != "equity"}, cap), "equity": series_of(other.get("equity"))} for other in value.get("also") or []]}
        elif key in ("checks", "stats", "costs", "sharpeInputs", "riskFree", "userStats"):
            out[key] = whole(value)
        else:
            out[key] = lean(value, cap)
    return out


def returns_of(rows):
    """Daily returns ([time, run, benchmark]): how many days, the share up, the best and the worst."""
    kept = [row for row in rows or [] if isinstance(row, (list, tuple)) and len(row) > 1 and isinstance(row[1], (int, float))]
    if not kept:
        return {"days": len(rows or [])}
    best, worst = max(kept, key=lambda row: row[1]), min(kept, key=lambda row: row[1])
    return {"days": len(kept), "upShare": num(sum(row[1] > 0 for row in kept) / len(kept)), "best": [when(best[0]), num(best[1])], "worst": [when(worst[0]), num(worst[1])]}


def exposure_of(rows):
    """Exposure ([time, gross, net]): the average and largest gross, the average net and the last day."""
    kept = [row for row in rows or [] if isinstance(row, (list, tuple)) and len(row) > 2 and isinstance(row[1], (int, float)) and isinstance(row[2], (int, float))]
    if not kept:
        return {"days": len(rows or [])}
    return {"days": len(kept), "averageGross": num(sum(row[1] for row in kept) / len(kept)), "averageNet": num(sum(row[2] for row in kept) / len(kept)),
            "largestGross": [when(max(kept, key=lambda row: row[1])[0]), num(max(row[1] for row in kept))], "last": [when(kept[-1][0]), num(kept[-1][1]), num(kept[-1][2])]}


def plots_of(series, cap):
    """The code's own series (q.plot): name, pane and style with the span, last value, low and high of each."""
    def one(item):
        line = series_of(list(zip(item.get("t") or [], item.get("values") or [], strict=False)))
        return {**{key: item.get(key) for key in ("name", "pane", "style")}, **line}
    return first([item for item in series or [] if isinstance(item, dict)], cap, one)


def run_of(run, cap):
    out = {}
    for key, value in run.items():
        if key == "settings" and isinstance(value, dict):
            out[key] = {**lean({name: item for name, item in value.items() if name != "data"}, cap), "data": data_of(value.get("data"), cap)}
        elif key == "progress" and isinstance(value, dict):
            out[key] = {**lean({name: item for name, item in value.items() if name != "equity"}, cap), **({"equity": series_of(value["equity"])} if value.get("equity") else {})}
        elif key == "failure" and isinstance(value, dict):
            out[key] = {**lean({name: item for name, item in value.items() if name != "cause"}, cap), "cause": text(value.get("cause"), 2_000)}
        elif key == "stats":
            out[key] = whole(value)
        else:
            out[key] = lean(value, cap)
    return out


def diff_of(diffs, cap):
    if not isinstance(diffs, list):
        return diffs
    return first(diffs, cap, lambda item: {"file": item.get("file"), "status": item.get("status"), "diff": head(item.get("diff"), cap * 250), "chars": len(item.get("diff") or "")})


def run(views, found, args):
    tables = tables_of(views, args["runId"], found["work"])
    return fit(lambda cap: {"run": run_of(found["run"], cap), "work": work_of(found["work"], tables, cap), "codeDiff": diff_of(found.get("codeDiff"), cap)})


# --- Results (read_strategy_results) -------------------------------------------------------------------------------------------
def summary_part(part, cap):
    out = {key: whole(value) for key, value in part.items() if key not in ("engine", "engineAgainstBenchmark")}
    if part.get("engineAgainstBenchmark") not in (None, part.get("againstBenchmark")):
        out["engineAgainstBenchmark"] = whole(part["engineAgainstBenchmark"])
    engine = [row for row in part.get("engine") or [] if isinstance(row, dict)]
    if engine:
        out["engine"] = {str(row.get("name")): num(row.get("value")) for row in engine[:cap * 2]}
        if len(engine) > cap * 2:
            out["engineRows"] = len(engine)
    return out


def equity_part(part, cap):
    def one(row):  # with the since and until that read_strategy_trades takes for the trades of that drawdown (one not recovered runs to the end)
        span = {"since": millis(row.get("peak"))}
        if row.get("recovered"):
            span["until"] = millis(row["recovered"], end=True)
        return {**compact(row), "trades": span}
    out = {key: lean(value, cap) for key, value in part.items() if key not in ("drawdowns",) and key not in FIGURE_KEYS}
    out["drawdowns"] = first(part.get("drawdowns") or [], cap, one)
    return out


def orders_part(part, cap):
    """The counts, and the orders that did not fill (`notFilled`, from the orders table, which `results` puts in)."""
    return {"total": part["total"], "counts": whole(part["counts"]), "notFilled": first(part["notFilled"], cap, order_row), "read": READ_ORDERS}


def account_part(part, cap):
    points = part.get("points") or []
    return {"currency": part.get("currency"), "points": len(points), "marginEvents": first(part.get("marginEvents") or [], cap, compact),
            **{key: {name: item for name, item in series_of(column_of(points, key)).items() if name != "points"} for key in ("equity", "balance", "marginUsed", "leverage")
               if any(isinstance(point, dict) and key in point for point in points)}}


def sectors_part(part, cap):
    out = {key: [compact(row, drop=("nativeInstrument",)) for row in part.get(key) or []][:cap] for key in ("mostMade", "mostLost")}
    out["sectors"] = first(sorted(part.get("sectors") or [], key=lambda row: -abs(row.get("net") or 0)), cap, compact)
    for key, value in part.items():
        if key not in out:
            out[key] = lean(value, cap)
    return out


def instruments_part(part, cap):
    rows = sorted(part.get("rows") or [], key=lambda row: -abs(row.get("pnl") or 0))
    return {"currency": part.get("currency"), "rows": first(rows, cap, compact)}


def settings_part(part, cap):
    out = {key: lean(value, cap) for key, value in part.items() if key not in ("data", "layers", "params", "run")}
    return {**out, "params": whole(part.get("params")), "run": whole(part.get("run")), "data": data_of(part.get("data"), cap), "layers": first(part.get("layers") or [], cap * 2, compact)}


def plain_part(part, cap):
    """A section without lists to cut (statistics, legs, styles, costs, crises): kept whole, its line figures as series."""
    out = {key: whole(value) for key, value in part.items() if key not in FIGURE_KEYS}
    found = lines_of(part.get("figures"))
    return {**out, **({"lines": found} if found else {})}


def lined(shape):
    """A section's own shape, with its line figures as series."""
    def make(part, cap):
        found = lines_of(part.get("figures"))
        return {**shape(part, cap), **({"lines": found} if found else {})}
    return make


SECTIONS = {"summary": summary_part, "equity": lined(equity_part), "orders": orders_part, "account": account_part, "sectors": sectors_part,
            "instruments": instruments_part, "settings": settings_part}


def section_of(name, part, cap):
    if not isinstance(part, dict):
        return part
    return SECTIONS.get(name, plain_part)(part, cap)


def results(views, found, args):
    base = {"runId": found.get("runId"), "missing": whole(found.get("missing")), "figuresFailed": whole(found.get("figuresFailed")),
            "toc": [item.get("id") for item in found.get("toc") or []]}
    if found.get("compared"):
        base["compared"] = whole(found["compared"])
    sections = found.get("sections") or {}
    if isinstance(sections.get("orders"), dict):
        table = views.table(views.object_runs.read(found["runId"]), "orders")
        missed = json.loads(table[table["status"] != "Filled"].to_json(orient="records")) if len(table) else []
        sections = {**sections, "orders": {**sections["orders"], "notFilled": missed}}

    def make(cap, names=None):
        return {**base, "sections": {name: section_of(name, part, cap) for name, part in sections.items() if names is None or name in names}}
    out = fit(make)
    if size(out) <= BUDGET:
        return out
    # Every section at once does not fit: as many as fit in the page's order, and the rest to read one at a time.
    kept = []
    for name in sections:
        if size(make(CAPS[-1], [*kept, name])) <= BUDGET:
            kept.append(name)
    return {**make(CAPS[-1], kept), "notShown": [name for name in sections if name not in kept], "read": "read_run with part for each section"}


# --- pages ---------------------------------------------------------------------------------------------------------------------
def trades(views, found, args):
    return page_of(found, trade_row)


def orders(views, found, args):
    return page_of(found, order_row)


def rebalances(views, found, args):
    return page_of(found, rebalance_row)
