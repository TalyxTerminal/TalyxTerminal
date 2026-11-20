"""What the strategy page reads: one run as `{run, work, codeDiff}` (key numbers, main chart series, trades, log, checks), a section of its
Results, its trades, orders, log and rebalances page by page, the run list, and the library inputs the code reads with their pinned versions."""
from __future__ import annotations

import ast
import difflib
import json
import re
import time
from datetime import UTC, date, datetime, timedelta
from pathlib import Path

import pandas as pd
from talyx_sdk import ContractError

from ..native import figures as F
from ..native import metrics as M
from . import run_flow, strategy_settings, sweep_figures
from .object_history import parameter_diff
from .strategy_checks import StrategyChecks

STATUS = {"queued": "queued", "preparing": "running", "running": "running", "cancel_requested": "running",
          "completed": "done", "failed": "failed", "cancelled": "stopped", "interrupted": "stopped"}
ACTIVE = {"queued", "preparing", "running", "cancel_requested"}
LIQUIDATION_STOP = "Liquidation stop"
KINDS = {"run": "backtest", "case": "backtest", "sweep": "sweep", "walk": "walk", "heldout": "heldout"}
TOC = (("summary", "Summary"), ("equity", "Equity"), ("periods", "Periods"), ("rolling", "Rolling"), ("longShort", "Exposure"), ("trades", "Trades"),
       ("orders", "Orders"), ("account", "Account"), ("sectors", "Sectors"), ("instruments", "Instruments"), ("style", "Style and attribution"),
       ("costs", "Costs"), ("crisis", "Crises"), ("settings", "Settings"))
SECTIONS = tuple(name for name, _ in TOC)
# What the strategy page reads of a run's Work (`page_run`): the numbers, checks, notes and the main figure, without the series the figure is drawn from,
# the long tables it pages, or the code's own series and numbers (the chart and Results show those); the AI and other modules read `run`.
PAGE_WORK = ("kind", "currency", "capital", "annualization", "riskFree", "benchmarkLabel", "stats", "shortSample", "costs", "notes", "dataNotes", "checks",
             "usedToPick", "heldOut", "partial", "stoppedAt", "figures", "tradesTotal", "logTotal")
PAGE_MULTI = ("rebalancesTotal", "holdings", "holdingsTotal", "byInstrument", "contributions")
# A rebalance's parts written as JSON text in rebalances.parquet.
REBALANCE_PARTS = ("targets", "ranks", "values", "actual", "orders", "unfilled")
# The Results figures that draw this run against its benchmark: the run's line is called by its number and the benchmark by its short name, as the draft's keys do.
RUN_LINES = {"strategy.equity", "strategy.drawdown", "strategy.yearly", "strategy.rollingSharpe", "strategy.rollingBeta", "strategy.rollingVolatility"}


def bench_names(label):
    """A benchmark's short name in a key ("BTC hold") and the market's own name ("BTC"); the run holds "BTC/USDT:USDT hold" for holding one instrument."""
    text = label or "Benchmark"
    held = text[:-5] if text.endswith(" hold") else None
    if not held:
        return text, text
    base = held.replace(":", "/").split("/")[0].split()[0]
    return f"{base} hold", base


def name_lines(figure, number, bench):
    """Name a Results figure's lines as the draft's keys and read-outs do: the run is `#12`, the benchmark `BTC hold` (the market `BTC` beside a rolling volatility)."""
    key = ((figure.get("layout") or {}).get("meta") or {}).get("key")
    if key not in RUN_LINES:
        return figure
    short, market = bench_names(bench)
    for item in figure.get("data") or []:
        role = (item.get("meta") or {}).get("role")
        if item.get("hoverinfo") == "skip" or role not in ("main", "updown", "benchmark"):
            continue
        new = f"#{number}" if role != "benchmark" else market if key == "strategy.rollingVolatility" else short
        template = item.get("hovertemplate") or ""
        head, cut, rest = template.partition("<br>")
        if cut:
            label, colon, value = rest.partition(": ")
            item["hovertemplate"] = head + cut + (new + colon + value if colon else rest)
        item["name"] = new
        if role == "updown" and key == "strategy.yearly":
            item["meta"] = {**item["meta"], "role": "main"}  # the draft's yearly bars: the run in the text colour, beside the benchmark's
    return figure


# What a run fills in on its own and is not a setting someone changed: the currency, the venue's leverage cap, where a fee came from.
FILLED_IN = {"currency", "exchange.maxLeverage", "costs.feeSource"}


def walk_stats(walk):
    """A walk-forward's numbers in the run list: the return and deepest drawdown of its stitched out-of-sample equity, and the trades of its windows
    (no Sharpe or CAGR here: the walk view reads those off the stitched curve with the run's own annualization)."""
    points = (walk or {}).get("stitched") or []
    if len(points) < 2:
        return None
    deepest = M.equity_drawdown([value for _, value in points])
    trades = sum(row.get("trades") or 0 for row in walk.get("windows") or [])
    return {"totalReturn": round(points[-1][1] / points[0][1] - 1, 6), "maxDrawdown": round(deepest, 6), "trades": trades}


def settled_changes(used, now):
    """The run settings that differ between a run and the strategy now, leaving out what the run filled in: a cost left to Market is
    empty in the settings and holds the number the run came to (as the Work column counts "changed since")."""
    return [item for item in strategy_settings.changes(used, now)
            if item["what"] not in FILLED_IN and not (item["what"].startswith("costs.") and item["from"] is None)]


OLD = "Run #{number} was made by an earlier TALYX; run it again to see it here"
OLD_SWEEP = "Sweep #{number} was made by an earlier TALYX; run it again to see it here"


def left_out(record):
    """The names the run's data left out and why (Market refused them, or their prices failed a check): the run traded the rest, which the numbers do not say."""
    data = record.get("data") or {}
    skipped = data.get("skipped") or []
    if not skipped:
        return []
    total = len(skipped) + data["traded"]
    shown = "; ".join(str(item.get("reason") or item.get("symbol")) for item in skipped[:3]) + (f"; and {len(skipped) - 3} more" if len(skipped) > 3 else "")
    return [f"Left out {len(skipped)} of {total} names, so it traded {total - len(skipped)}: {shown}"]


def price_gaps(record):
    """Sessions a chosen symbol had no prices for (summary.gaps): the run went on through them without a bar for that name."""
    out = []
    for item in (record.get("data") or {}).get("gaps") or []:
        days = list(item.get("days") or [])
        if not days:
            continue
        shown = ", ".join(days[:3]) + (f" and {len(days) - 3} more" if len(days) > 3 else "")
        out.append(f"{item['symbol']} has no prices on {shown}; the run went on through {'that day' if len(days) == 1 else 'those days'} without a bar for it")
    return out


def signal_gaps(record):
    """A library input whose values stop well before the run ends: the strategy kept its last picks from then on, which the numbers do not show.
    Values arrive when they became known; ones a library version made later (it was not producing then, or caught up afterwards) arrive after this period."""
    data = record.get("data") or {}
    until = data.get("until")
    out = []
    for item in data.get("library") or []:
        last = item.get("lastAvailableAt")
        if until is None or last is None or until - last <= 7 * 86_400_000:
            continue
        day = datetime.fromtimestamp(last / 1000, tz=UTC)
        out.append(f"{item['name']}{' v' + str(item['version']) if item.get('version') else ''} has values only up to {day:%b} {day.day}, {day.year} in this period; "
                   "later ones became known after it ends, so from then on the strategy held its last picks")
    return out


def signal_coverage(record):
    """A library input that has values for fewer names a day than the run's data holds (a factor scored on part of an index): said with
    both numbers, since the names without a value that day have none of it to act on."""
    data = record.get("data") or {}
    names = data.get("traded") or 0
    out = []
    for item in data.get("library") or []:
        per = item.get("perDay")
        if per is None or not names or per >= names:
            continue
        out.append(f"{item['name']}{' v' + str(item['version']) if item.get('version') else ''} has values for about {per} names a day; the run's data holds "
                   f"{names} names over the period, and on a day a name has no value it has none of this input to act on")
    return out


def funding_gaps(work):
    """Settlements the funding source has no rate for (a venue that keeps only its recent history): the costs hold no funding for that time."""
    out = []
    for gap in ((work or {}).get("costs") or {}).get("incomplete") or []:
        start, end = (datetime.fromtimestamp(gap[key] / 1000, tz=UTC) for key in ("from", "to"))
        if str(gap.get("reason") or "").startswith("no mark price"):
            out.append(f"{gap['instrument']}: {gap['reason']} ({start:%b} {start.day}, {start.year} to {end:%b} {end.day}, {end.year})")
            continue
        out.append(f"{gap['instrument']} has no funding rates from {start:%b} {start.day}, {start.year} to {end:%b} {end.day}, {end.year}"
                   f"{' (' + gap['reason'] + ')' if gap.get('reason') else ''}; the costs hold no funding for that time")
    return out

STATS = ("totalReturn", "cagr", "maxDrawdown", "sharpe", "overBenchmark", "costsAYear", "trades", "sortino", "volatility", "calmar", "winRate", "profitFactor")


def pct(value, digits=0):
    return "–" if value is None else f"{value * 100:.{digits}f}%"


def day_of(milliseconds):
    return datetime.fromtimestamp(milliseconds / 1000, tz=UTC).date().isoformat()


class StrategyViews:
    def __init__(self, runs, store, library):
        self.object_runs, self.store, self.library = runs, store, library
        self.checker = StrategyChecks(runs, self)

    # --- files ------------------------------------------------------------------------------------------------------------------
    def folder(self, record):
        return self.object_runs.directory(record["id"]) / "output" / "strategy"

    @staticmethod
    def environment_of(record):
        """The Python and the Nautilus the run was made with, as its own recipe and lock say (nothing for a run that has no lock)."""
        recipe = record.get("recipe") or {}
        python = ".".join(str(recipe.get("pythonVersion") or "").split(".")[:2]) or None
        nautilus = None
        try:
            found = re.search(r"nautilus[-_]trader[^\n]*?(\d+\.\d+\.\d+[a-z0-9]*)", Path(recipe["lockPath"]).read_text(encoding="utf-8")) if recipe.get("lockPath") else None
            nautilus = found.group(1) if found else None
        except OSError:
            pass
        return {"python": python, "nautilus": nautilus} if python or nautilus else None

    def report(self, record, name):
        path = self.folder(record) / f"{name}.json"
        return json.loads(path.read_text(encoding="utf-8")) if path.is_file() else None

    def table(self, record, name):
        path = self.folder(record) / f"{name}.parquet"
        if not path.is_file():
            raise ContractError("unavailable", f"This run has no {name} table.", {"state": record["state"]})
        return pd.read_parquet(path)

    def shown(self, record):
        """The run whose report a sweep or walk shows: its selected (or best) combination."""
        if record["runKind"] in ("sweep", "walk") and record["sweep"]["selected"] is not None:
            case = next((item for item in record["sweep"]["cases"] if item["index"] == record["sweep"]["selected"]), None)
            if case is None:
                return record
            return self.object_runs.read(case["runId"])
        return record

    def progress(self, record):
        if record["state"] not in ACTIVE:
            return None
        path = self.object_runs.directory(record["id"]) / "output" / "progress.json"
        if record.get("stage") == "data" and not path.is_file():
            # While the data is fetched: how far (object_runs.data_progress), until the engine writes its own progress.
            fetching = self.object_runs.fetching.get(record["id"])
            return {**fetching, "stage": record["stage"]} if fetching else None
        if not path.is_file():
            return None
        try:
            found = {**json.loads(path.read_text(encoding="utf-8")), "stage": record["stage"]}
        except (OSError, ValueError):
            return None
        points = [point for point in found.get("equity") or [] if point[1] is not None]
        if len(points) > 1:
            # The equity so far as a return from the start, drawn like the finished one; the page puts it where the chart will be.
            figure = F.figure("strategy.live", [F.line([datetime.fromtimestamp(at / 1000, tz=UTC).strftime("%Y-%m-%d %H:%M") for at, _ in points], [round(value, 6) for _, value in points],
                                                       "main", "equity", "Equity")], yaxis={"tickformat": "+.1%"})
            F.read_out(figure)
            found["figure"] = figure
        return found

    # --- numbering and neighbours -------------------------------------------------------------------------------------------------
    def number(self, record):
        if record.get("number"):
            return record["number"]
        found = self.object_runs.records.db.execute("""SELECT count(*) FROM records WHERE kind='qrun' AND json_extract(content,'$.objectId')=? AND json_extract(content,'$.runKind')!='case'
            AND (json_extract(content,'$.createdAt'), id) <= (?,?)""", (record["objectId"], record["createdAt"], record["id"])).fetchone()
        return int(found[0])

    def before(self, record):
        row = self.object_runs.records.db.execute("""SELECT content FROM records WHERE kind='qrun' AND json_extract(content,'$.objectId')=? AND json_extract(content,'$.runKind')!='case'
            AND (json_extract(content,'$.createdAt'), id) < (?,?) ORDER BY json_extract(content,'$.createdAt') DESC, id DESC LIMIT 1""",
            (record["objectId"], record["createdAt"], record["id"])).fetchone()
        return json.loads(row[0]) if row else None

    @staticmethod
    def data_to(record):
        """The last day the run read: what it recorded (resolved when it started), else the settings' end."""
        resolved = record["data"] or {}
        return resolved.get("dataTo") or resolved.get("until")

    def data_of(self, record):
        """The data settings of a run as the page shows them: a run that reads on to the end (the held-out check) has no end of its own, so the day it read to stands in."""
        data = record["settings"]["data"]
        if data.get("until") not in (None, "latest"):
            return data
        read = self.data_to(record)
        if isinstance(read, (int, float)):
            read = datetime.fromtimestamp(read / 1000, tz=UTC).date().isoformat()
        return {**data, "until": read} if isinstance(read, str) else data

    def run_settings_of(self, record):
        """The second layer as this run used it (the settings it recorded, over the defaults for what it traded)."""
        used = record.get("runSettings") or (record["data"] or {}).get("runSettings")
        if used:
            return used
        return self.asked(record)

    @staticmethod
    def asked(record, kind=None):
        """The second layer as set when the run started, over the defaults for `kind` (what it traded; a run stopped before its data was read has none)."""
        settings = record["settings"]
        return strategy_settings.effective(kind or (record["data"] or {}).get("tradedKind") or "perpetual", settings.get("run"), settings.get("account") or {},
                                           held_out=settings["data"].get("heldOutFrom"))

    def changes(self, record, previous):
        """What changed against the run before: parameters, run settings, data settings and code, as [{what, from, to}]."""
        if previous is None:
            return []
        out = [{"what": name, "from": item["before"], "to": item["after"]} for name, item in parameter_diff(record["params"], previous["params"]).items()]
        if (record["data"] or {}).get("runSettings") and (previous["data"] or {}).get("runSettings"):
            out += strategy_settings.changes(self.run_settings_of(record), self.run_settings_of(previous))
        else:
            # One of them stopped before its data was read: compare what was set, over the same defaults, not one run's defaults with another's.
            kind = (record["data"] or {}).get("tradedKind") or (previous["data"] or {}).get("tradedKind")
            out += strategy_settings.changes(self.asked(record, kind), self.asked(previous, kind))
        # The whole data settings, the names (or index or list) among them, compared by the shared flow (the range alone was).
        out += run_flow.changes({"data": previous["settings"]["data"]}, {"data": record["settings"]["data"]})
        if record["code"] and previous["code"] and record["code"]["digest"] != previous["code"]["digest"]:
            out.append({"what": "code", "from": previous["code"]["digest"][:8], "to": record["code"]["digest"][:8]})
        return out

    def code_diff(self, record, previous):
        """The code files that differ from the run before, as unified diffs (none when either run's code is not kept)."""
        if previous is None:
            return None
        now, then = self.object_runs.directory(record["id"]) / "source", self.object_runs.directory(previous["id"]) / "source"
        if not now.is_dir() or not then.is_dir():
            return None
        names = sorted({path.relative_to(root).as_posix() for root in (now, then) for path in root.rglob("*.py")})
        out = []
        for name in names:
            a = (then / name).read_text(encoding="utf-8").splitlines() if (then / name).is_file() else None
            b = (now / name).read_text(encoding="utf-8").splitlines() if (now / name).is_file() else None
            if a == b:
                continue
            out.append({"file": name, "status": "added" if a is None else "removed" if b is None else "changed",
                        "diff": "\n".join(difflib.unified_diff(a or [], b or [], "before", "this run", lineterm=""))})
        return out

    # --- one run ----------------------------------------------------------------------------------------------------------------
    def run(self, run_id, vs=None):
        record = self.object_runs.read(run_id)
        if record["objectKind"] != "strategy":
            raise ContractError("invalid_input", "This is not a strategy run.")
        shown = self.shown(record)
        work = self.report(shown, "work")
        previous = self.before(record)
        number = self.number(record)
        notes = list(work.get("notes") or []) if work else []
        own = record["runKind"] not in ("sweep", "walk") or shown is not record  # a walk-forward, or a sweep with nothing picked, has no report of its own
        if work is None and record["state"] == "completed" and own:
            notes.append(OLD.format(number=number))
        gaps = left_out(record) + price_gaps(record) + signal_gaps(record) + signal_coverage(record) + funding_gaps(work)
        notes += gaps
        inputs = record.get("inputs")
        if inputs is None:
            inputs = [{"name": item["name"], "kind": "factor", "versionId": item["versionId"], "version": item.get("version")} for item in (record["data"] or {}).get("library", []) if item.get("versionId")] \
                + [{"name": item["name"], "kind": "model", "versionId": item["versionId"], "version": item.get("version")} for item in (record["data"] or {}).get("models", [])]
        run = {"id": record["id"], "objectId": record["objectId"], "number": number, "kind": KINDS.get(record["runKind"], "backtest"), "state": record["state"], "stage": record.get("stage"),
               "createdAt": record["createdAt"], "endedAt": record["endedAt"],
               "seconds": round((record["endedAt"] - record["createdAt"]) / 1000, 1) if record["endedAt"] else None,
               "settings": {"data": self.data_of(record), "run": self.run_settings_of(record), "params": record["params"]},
               "changes": self.changes(record, previous), "inputs": inputs, "stats": work["stats"] if work else None, "progress": self.progress(record),
               "failure": self.failure(record, work), "starred": record["starred"], "note": record["note"], "of": record.get("of"),
               "heldOut": bool(record.get("heldOut")), "codeDigest": record["code"]["digest"] if record["code"] else None,
               "dataTo": self.data_to(record), "notes": notes, "tags": record.get("tags") or [],
               "message": record["message"], "shownRunId": shown["id"], "environment": self.environment_of(record)}
        if record["runKind"] in ("sweep", "walk"):
            if record["sweep"].get("format") != 2:
                run["notes"] = [*run["notes"], OLD_SWEEP.format(number=number)]
            else:
                run["sweep"] = self.sweep_line(record)
        if record["runKind"] == "walk" and record["sweep"].get("format") == 2:
            run["walk"] = record.get("walk")
        out_work = None
        if work is not None:
            out_work = {**{key: value for key, value in work.items() if key != "checks"}, "checks": self.checks(shown, work), "usedToPick": self.used_to_pick(inputs)}
            # What the run did (no trades, a stop) stays under its numbers; what its data did not have goes in the header's data notes.
            out_work["notes"] = list(work.get("notes") or [])
            out_work["dataNotes"] = gaps
            if vs:
                first, *more = [item for item in str(vs).split(",") if item][:3]  # up to three runs beside this one; "a,b,c"
                out_work["compared"] = self.compared(shown, work, first)
                out_work["compared"]["also"] = [{key: value for key, value in self.compared(shown, work, other).items() if key in ("runId", "number", "label", "equity")} for other in more]
            out_work["figures"] = {"equity": self.equity_figure(work, out_work.get("compared"), number, (run["failure"] or {}).get("simulatedAt"))}
            if record["runKind"] == "heldout" and work.get("heldOut"):
                source = self.object_runs.read(record["of"]) if record.get("of") else None
                of_number = (source or {}).get("number")
                out_work["heldOut"] = {**work["heldOut"], "figure": sweep_figures.held_split(work, of_number, number), "rows": sweep_figures.held_rows(work["heldOut"], bench_names(work.get("benchmarkLabel"))[1]), "of": of_number}
        return {"run": run, "work": out_work, "codeDiff": self.code_diff(record, previous)}

    def page_run(self, run_id, vs=None):
        """One run as the strategy page opens it: `run`'s answer with only what the page shows of the Work (the equity's first and last day as
        `span`, the first instrument and how many); the trades, orders, log and rebalances it reads a page at a time."""
        found = self.run(run_id, vs)
        work = found["work"]
        if work is None:
            return found
        out = {key: work[key] for key in PAGE_WORK if key in work}
        out["span"] = [work["equity"][0][0], work["equity"][-1][0]] if work["equity"] else None
        out["instrument"] = work["instruments"][0] if work.get("instruments") else None
        out["instrumentCount"] = len(work.get("instruments") or [])
        if "multi" in work:
            out["multi"] = {key: work["multi"][key] for key in PAGE_MULTI}
        if "compared" in work:
            out["compared"] = {key: value for key, value in work["compared"].items() if key not in ("equity", "also")}
        return {**found, "work": out}

    @staticmethod
    def equity_figure(work, compared, number, stop=None):
        """The main picture of a run: equity against the benchmark (and the run it is compared with), their differences read as returns, and the drawdown under it."""
        from datetime import UTC, datetime

        def days(points):
            return [datetime.fromtimestamp(point[0] / 1000, tz=UTC).strftime("%Y-%m-%d") for point in points]

        def level(points):
            base = points[0][1] if points and points[0][1] else None
            return [round(point[1] / base, 6) if base and point[1] is not None else None for point in points]
        read = "<b>%{x}</b><br>NAME: %{customdata:+.1%}<extra></extra>"
        traces = []
        main = F.line(days(work["equity"]), level(work["equity"]), "main", "equity", f"#{number}", customdata=[None if value is None else round(value - 1, 6) for value in level(work["equity"])])
        main["hovertemplate"] = read.replace("NAME", f"#{number}")
        traces.append(main)
        label = work.get("benchmarkLabel") or "Benchmark"  # a sweep's combination (a light report) has no label and no drawdown
        if work.get("benchmark"):
            bench = F.line(days(work["benchmark"]), level(work["benchmark"]), "benchmark", "benchmark", label, customdata=[None if value is None else round(value - 1, 6) for value in level(work["benchmark"])])
            bench["hovertemplate"] = read.replace("NAME", label)
            traces.append(bench)
        if compared and compared["equity"]:
            other = F.line(days(compared["equity"]), level(compared["equity"]), "compare", "compare", compared["label"], customdata=[None if value is None else round(value - 1, 6) for value in level(compared["equity"])])
            other["hovertemplate"] = read.replace("NAME", compared["label"])
            traces.append(other)
            for at, extra in enumerate(compared.get("also") or [], start=1):
                more = F.line(days(extra["equity"]), level(extra["equity"]), "compare", f"compare:{at}", extra["label"], index=at, customdata=[None if value is None else round(value - 1, 6) for value in level(extra["equity"])])
                more["hovertemplate"] = read.replace("NAME", extra["label"])
                traces.append(more)
        if work.get("drawdown"):
            drawdown = F.line(days(work["drawdown"]), [None if point[1] is None else round(point[1] * 100, 3) for point in work["drawdown"]], "drawdown", "drawdown", "Drawdown", yaxis="y2")
            drawdown["hovertemplate"] = "<b>%{x}</b><br>Drawdown: %{y:.1f}%<extra></extra>"
            traces.append(drawdown)
        axis = {"anchor": "y2"}
        extra = {}
        if stop:
            # A run that stopped on an error opens 400 days before the stop and 60 after it, with a dashed line where it stopped.
            day = lambda at: datetime.fromtimestamp(at / 1000, tz=UTC).strftime("%Y-%m-%d")
            axis["range"] = [day(stop - 400 * 86_400_000), day(stop + 60 * 86_400_000)]
            extra["shapes"] = [{"type": "line", "xref": "x", "yref": "paper", "x0": day(stop), "x1": day(stop), "y0": 0, "y1": 1, "line": {"color": "#F36B21", "width": 1, "dash": "dash"}}]
        return F.figure("strategy.work.equity", traces, showlegend=False, yaxis={"domain": [0.3, 1], "tickformat": ".2f"}, yaxis2={"domain": [0, 0.22], "ticksuffix": "%"}, xaxis=axis, **extra)

    def compared(self, shown, work, other_id):
        """The run to draw beside this one: its equity, and the key numbers' differences, which only mean something between two backtests of the same period."""
        other = self.object_runs.read(other_id)
        found = self.report(self.shown(other), "work")
        number = self.number(other)
        label = f"#{number}"
        out = {"runId": other_id, "number": number, "label": label, "equity": found["equity"] if found else [], "stats": found["stats"] if found else None,
               "delta": dict.fromkeys(STATS), "whatDiffers": []}
        if found is None:
            return out
        if other["runKind"] in ("heldout", "walk") or shown["runKind"] in ("heldout", "walk"):
            out["label"] = f"{label} · " + ("held-out only" if other["runKind"] == "heldout" else "walk-forward only")
            out["whatDiffers"].append("periods differ")
            return out
        same = bool(work["equity"]) and bool(found["equity"]) and work["equity"][0][0] == found["equity"][0][0] and work["equity"][-1][0] == found["equity"][-1][0]
        for key in STATS:
            a, b = work["stats"].get(key), found["stats"].get(key)
            if isinstance(a, (int, float)) and isinstance(b, (int, float)):
                out["delta"][key] = round(a - b, 6)
        if not same:
            out["delta"]["totalReturn"] = None  # two periods: a total return is not a like for like
            out["whatDiffers"].append("periods differ")
        return out

    def failure(self, record, work):
        error = record.get("error")
        if record["state"] != "failed" or not error:
            return None
        context = record.get("failureContext") or {}
        return {"file": error.get("file"), "line": context.get("line") or error.get("line"), "error": error.get("message"), "stage": error.get("stage"),
                "simulatedAt": context.get("simulatedAt"), "instrument": context.get("instrument"), "cause": context.get("traceback") or error.get("message"),
                "tradesBefore": context.get("tradesBefore"), "equityBefore": context.get("equityBefore"), "locals": context.get("locals") or [],
                "withoutPrice": context.get("withoutPrice"), "function": context.get("function"), "sourceLine": context.get("sourceLine"), "inEngine": bool(context.get("inEngine")), "partial": bool(work and work.get("partial"))}

    def used_to_pick(self, inputs):
        """The model versions this run read the published values of, and up to which day they were picked on the data (their held-out start)."""
        out = []
        for item in inputs:
            if item.get("kind") != "model" or item.get("reads") != "values":
                continue
            try:
                held = self.library.read_version(item["versionId"]).get("heldOutFrom")
            except ContractError:
                held = None
            out.append({"name": item["name"], "to": (date.fromisoformat(held) - timedelta(days=1)).isoformat() if held else None})
        return out

    # --- the checks ---------------------------------------------------------------------------------------------------------------
    def checks(self, record, work):
        """The checks of a run: its own trades (shuffled order, best five) and what the object's other runs say (held-out, walk-forward, sweeps, costs)."""
        return self.checker.rows(record, work)

    def sweep_line(self, record):
        """A sweep as the run list and the page head show it: how far it got, how it is stopped, and the best combination described (not chosen)."""
        sweep = record["sweep"]
        done = sum(case["state"] in {"completed", "failed", "cancelled", "interrupted"} for case in sweep["cases"])
        completed = [case for case in sweep["cases"] if case["state"] == "completed" and isinstance((case["metrics"] or {}).get(sweep["metric"]), (int, float))]
        best = max(completed, key=lambda case: case["metrics"][sweep["metric"]], default=None)
        walk = record.get("walk") if record["runKind"] == "walk" else None
        windows = sum(1 for row in (walk or {}).get("windows") or [] if row.get("trades") is not None)
        return {**({"windows": windows} if walk is not None else {}),
                "axes": sweep["axes"], "metric": sweep["metric"], "selected": sweep["selected"], "done": done, "total": len(sweep["cases"]),
                "completed": sum(case["state"] == "completed" for case in sweep["cases"]), "failed": sum(case["state"] == "failed" for case in sweep["cases"]),
                "atOnce": sweep.get("atOnce"), "limit": sweep.get("limit"),
                "best": {"metric": sweep["metric"], "value": best["metrics"][sweep["metric"]], "index": best["index"], "params": best["params"]} if best else None}

    # --- the run list --------------------------------------------------------------------------------------------------------------
    def runs(self, object_id, since=None):
        self.store.read(object_id)
        clauses, values = ["kind='qrun'", "json_extract(content,'$.objectId')=?", "json_extract(content,'$.runKind')!='case'"], [object_id]
        if since is not None:
            clauses.append("json_extract(content,'$.updatedAt')>?")
            values.append(since)
        rows = self.object_runs.records.db.execute("SELECT content FROM records WHERE " + " AND ".join(clauses) +
                                            " ORDER BY json_extract(content,'$.createdAt') DESC, id DESC", values).fetchall()
        items = []
        for row in rows:
            record = json.loads(row[0])
            stats = record.get("stats")  # a held-out validation's are its held-out end's, as the run stored them
            if record["runKind"] == "walk" and not stats:
                stats = walk_stats(record.get("walk"))  # the stitched out-of-sample result, not empty columns
            items.append({"id": record["id"], "number": self.number(record), "kind": KINDS.get(record["runKind"], "backtest"), "state": record["state"], "stage": record.get("stage"),
                          "createdAt": record["createdAt"], "endedAt": record["endedAt"], "changes": self.changes(record, self.before(record)), "stats": stats,
                          "failure": ({"error": (record.get("error") or {}).get("message"), "line": (record.get("error") or {}).get("line")} if record["state"] == "failed" else None),
                          "starred": record["starred"], "note": record["note"], "tags": record.get("tags") or [], "heldOut": bool(record.get("heldOut")),
                          **({"sweep": self.sweep_line(record)} if record["runKind"] in ("sweep", "walk") and (record.get("sweep") or {}).get("format") == 2 else {})})
        return {"items": items, "now": int(time.time() * 1000), "complete": since is None}

    # --- Results ----------------------------------------------------------------------------------------------------------------
    def results(self, run_id, section=None, vs=None):
        record = self.shown(self.object_runs.read(run_id))
        report = self.report(record, "results")
        if report is None:
            raise ContractError("unavailable", "This run has no Results yet.", {"state": record["state"]})
        if section is not None and section not in SECTIONS:
            raise ContractError("invalid_input", "Choose a section: " + ", ".join(SECTIONS) + ".")
        sections = {**report["sections"], "settings": self.settings_section(record)}
        work = self.report(record, "work")
        if isinstance(sections.get("trades"), dict):
            try:
                sections["trades"] = {**sections["trades"], "split": self.trade_split(self.table(record, "trades"))}
            except ContractError:
                pass
        if work is not None and work.get("kind") == "single" and "instruments" not in sections:
            sections["instruments"] = self.instruments_section(record)
        if isinstance(sections.get("costs"), dict):
            sections["costs"] = {**sections["costs"], **self.cost_reruns(record)}
        chosen = {section: sections.get(section)} if section else sections
        toc = [{"id": name, "title": title} for name, title in TOC if name in sections]
        out = {"runId": record["id"], "sections": chosen, "missing": report["missing"], "figuresFailed": report["figuresFailed"], "toc": toc}
        others = [item for item in (vs.split(",") if isinstance(vs, str) else list(vs or [])) if item][:3]  # up to three runs beside this one
        work = self.report(record, "work") if others else None
        if others and work is not None:
            compared = [self.compared(record, work, other) for other in others]
            out["compared"] = [{key: item[key] for key in ("runId", "number", "label", "stats", "delta", "whatDiffers")} | self.summary_of(item["runId"]) for item in compared]
            figure = ((chosen.get("equity") or {}).get("figures") or {}).get("equity")
            if isinstance(figure, dict) and figure.get("data"):
                dropped = ((chosen.get("equity") or {}).get("figures") or {}).get("drawdown")
                shown_figures = {**chosen["equity"]["figures"], "equity": self.results_equity_with(figure, compared)}
                if isinstance(dropped, dict) and dropped.get("data"):
                    shown_figures["drawdown"] = self.results_drawdown_with(dropped, compared)
                out["sections"] = {**chosen, "equity": {**chosen["equity"], "figures": shown_figures}}
        bench = ((sections.get("summary") or {}).get("benchmarkLabel") if isinstance(sections.get("summary"), dict) else None) or ((sections.get("periods") or {}).get("benchmarkLabel") if isinstance(sections.get("periods"), dict) else None)
        for part in out["sections"].values():
            for item in ((part or {}).get("figures") or {}).values() if isinstance(part, dict) else []:
                if isinstance(item, dict) and "data" in item:
                    F.plain_axes(item)
                    name_lines(item, self.number(record), bench)
        return out

    @staticmethod
    def trade_stats(part):
        """The numbers of the Trades table for one group of closed trades."""
        closed = part[part["exit"].notna() & part["pnl"].notna()].sort_values("exit")
        if not len(closed):
            return {"trades": 0}
        pnl = closed["pnl"].astype(float)
        wins, losses = pnl[pnl > 0], pnl[pnl < 0]
        best, worst = closed.loc[pnl.idxmax()], closed.loc[pnl.idxmin()]
        shared = M.closed_trades(pnl.to_numpy())  # the run's own definitions, in the order the trades closed

        def mean(column):
            return round(float(closed[column].dropna().mean()), 6) if column in closed and closed[column].notna().any() else None
        rounded = lambda value, digits: round(value, digits) if value is not None else None  # noqa: E731
        return {"trades": int(len(closed)), "pnl": round(float(pnl.sum()), 2), "winRate": rounded(shared["winRate"], 4),
                "profitFactor": rounded(shared["profitFactor"], 3),
                "payoff": round(float(wins.mean() / -losses.mean()), 3) if len(wins) and len(losses) else None,
                "averageWin": rounded(shared["averageWin"], 2), "averageLoss": rounded(shared["averageLoss"], 2), "expectancy": round(float(pnl.mean()), 2),
                "largestWin": {"pnl": round(float(best["pnl"]), 2), "trade": int(best["id"])}, "largestLoss": {"pnl": round(float(worst["pnl"]), 2), "trade": int(worst["id"])},
                "mostLossesInARow": shared["longestLosing"], "barsHeld": mean("bars"), "barsHeldWinners": round(float(closed.loc[pnl > 0, "bars"].mean()), 1) if len(wins) and "bars" in closed else None,
                "barsHeldLosers": round(float(closed.loc[pnl < 0, "bars"].mean()), 1) if len(losses) and "bars" in closed else None, "averageMae": mean("mae"), "averageMfe": mean("mfe")}

    def trade_split(self, table):
        return {"all": self.trade_stats(table), "long": self.trade_stats(table[table["side"] == "Long"]), "short": self.trade_stats(table[table["side"] == "Short"])}

    def instruments_section(self, record):
        """What each contract earned and paid: trades, net P&L, win rate, profit factor, the average trade and the four costs, with its cumulative P&L."""
        table = self.table(record, "trades")
        closed = table[table["exit"].notna() & table["pnl"].notna()].sort_values("exit")
        rows, traces = [], []
        for name, part in closed.groupby("instrument", sort=False):
            shared = M.closed_trades(part["pnl"].astype(float).to_numpy())
            rows.append({"instrument": name, "trades": int(len(part)), "pnl": round(float(part["pnl"].sum()), 2), "winRate": round(shared["winRate"], 4),
                         "profitFactor": round(shared["profitFactor"], 3) if shared["profitFactor"] is not None else None, "perTrade": round(float(part["pnl"].mean()), 2),
                         **{key: round(float(part[key].fillna(0).sum()), 2) for key in ("fees", "spread", "impact", "funding")}})
            days = [datetime.fromtimestamp(at / 1000, tz=UTC).strftime("%Y-%m-%dT%H:%M:%S+00:00") for at in part["exit"]]
            trace = F.line(days, [round(float(value), 2) for value in part["pnl"].cumsum()], "main", f"instrument:{name}", str(name).split("/")[0].split("-")[0], line={"shape": "hv", "width": 2})
            traces.append(trace)
        figure = F.read_out(F.figure("strategy.instruments", traces, showlegend=False, yaxis={"tickformat": ",.0f"})) if traces else None
        return {"rows": rows, "currency": (self.report(record, "work") or {}).get("currency"), "figures": {"cumulative": figure} if figure else {}}
    def cost_reruns(self, record):
        """What the reruns of this run say, once they ran: the cost sensitivity (none, as set, doubled), the capacity at larger capital and its picture. Nothing before they did."""
        found = [item for item in self.checker.records(record["objectId"], "sweep") if item.get("of") == record["id"] and item["state"] == "completed" and (item.get("sweep") or {}).get("format") == 2]

        def sweep_of(name):
            return next((item for item in found if [axis["name"] for axis in item["sweep"]["axes"]] == [name]), None)

        def by(item, name):
            return {case["params"][name]: case.get("metrics") or {} for case in item["sweep"]["cases"] if case["state"] == "completed"} if item else {}
        out = {}
        costs = by(sweep_of("@costMultiple"), "@costMultiple")
        if costs:
            names = {0: "None", 1: "As set", 2: "Doubled"}
            out["sensitivity"] = [{"costs": names[key], **{field: costs[key].get(field) for field in ("totalReturn", "cagr", "sharpe", "maxDrawdown")}} for key in (0, 1, 2) if key in costs]
            if 1 in costs and 2 in costs:
                out["doubled"] = {"cagrNow": costs[1].get("cagr"), "cagr": costs[2].get("cagr"), "sharpeNow": costs[1].get("sharpe"), "sharpe": costs[2].get("sharpe")}
        capital = by(sweep_of("@capital"), "@capital")
        if capital:
            steps = sorted(capital)
            out["capacity"] = [{"capital": step, "cagr": capital[step].get("cagr"), "sharpe": capital[step].get("sharpe")} for step in steps]
            out["capacityFigure"] = sweep_figures.capacity(out["capacity"])
        return out

    def summary_of(self, run_id):
        """The statistics table and the against-the-benchmark numbers of a run drawn beside this one, for its columns."""
        found = self.report(self.shown(self.object_runs.read(run_id)), "results")
        summary = ((found or {}).get("sections") or {}).get("summary") or {}
        try:
            trade_stats = self.trade_stats(self.table(self.shown(self.object_runs.read(run_id)), "trades"))
        except ContractError:
            trade_stats = {}
        return {"rows": {row["name"]: row["run"] for row in summary.get("returnStatistics") or []}, "against": summary.get("againstBenchmark") or {}, "tradeStats": trade_stats}

    @staticmethod
    def results_equity_with(figure, compared):
        """The Results equity figure with each compared run drawn beside it: its daily level from the same capital, grey and dashed, read out as a signed return."""
        def daily(points):
            last = {}
            for at, value in points:
                if value is not None:
                    last[datetime.fromtimestamp(at / 1000, tz=UTC).strftime("%Y-%m-%d")] = value
            return last
        read = "<b>%{x}</b><br>NAME: %{customdata:+.1%}<extra></extra>"
        extra = []
        for at, item in enumerate(compared):
            days = daily(item["equity"])
            if not days:
                continue
            base = next(iter(days.values()))
            level = {day: value / base for day, value in days.items()} if base else {}
            trace = F.line([f"{day}T00:00:00+00:00" for day in level], [round(value, 6) for value in level.values()], "compare", "compare" if at == 0 else f"compare:{at}", item["label"],
                           customdata=[round(value - 1, 6) for value in level.values()], **({"index": at} if at else {}))
            trace["hovertemplate"] = read.replace("NAME", item["label"])
            extra.append(trace)
        return {**figure, "data": [*figure["data"], *extra]}

    @staticmethod
    def results_drawdown_with(figure, compared):
        """The Results drawdown figure with each compared run's own fall drawn beside it (grey, dashed), as the draft's underwater strip does."""
        extra = []
        for at, item in enumerate(compared):
            last = {}
            for stamp, value in item["equity"]:
                if value is not None:
                    last[datetime.fromtimestamp(stamp / 1000, tz=UTC).strftime("%Y-%m-%d")] = value
            values = list(last.values())
            fall = dict(zip(last, (M.drawdown_path(values, values[0]) * 100).round(3), strict=True)) if values else {}
            if not fall:
                continue
            trace = F.line([f"{day}T00:00:00+00:00" for day in fall], list(fall.values()), "compare", "compare" if at == 0 else f"compare:{at}", item["label"], **({"index": at} if at else {}))
            trace["hovertemplate"] = "<b>%{x}</b><br>" + item["label"] + ": %{y:.1f}%<extra></extra>"
            extra.append(trace)
        return {**figure, "data": [*figure["data"][:-1], *extra, figure["data"][-1]]}

    def settings_section(self, record):
        """This run's whole settings by layer, with what differs from the strategy's current ones (the page offers to use them again)."""
        obj = self.store.read(record["objectId"])
        used = self.run_settings_of(record)
        now = strategy_settings.effective((record["data"] or {}).get("tradedKind") or "perpetual", obj["settings"].get("run"), obj["settings"].get("account") or {},
                                          held_out=obj["settings"]["data"].get("heldOutFrom"))
        differs = {"param." + name: item for name, item in parameter_diff(record["params"], obj["settings"]["params"] | {key: value for key, value in record["params"].items()
                                                                                                                       if key not in obj["settings"]["params"]}).items()}
        data_then = dict(record["settings"]["data"])
        if data_then.pop("untilLatest", False):
            data_then["until"] = "latest"
        for key in ("indexName", "listName"):
            data_then.pop(key, None)
        if data_then.get("list"):
            data_then.pop("symbols", None)
        data_changes = run_flow.changes(data_then, obj["settings"]["data"])
        differs.update({"data." + item["what"]: {"before": item["from"], "after": item["to"]} for item in data_changes})
        changed = settled_changes(used, now)
        differs |= {"run." + item["what"]: {"before": item["from"], "after": item["to"]} for item in changed}
        layers = [{"layer": "Code", "setting": name, "then": value, "now": obj["settings"]["params"].get(name, value)} for name, value in record["params"].items()]
        layers += [{"layer": "Run", "setting": item["what"], "then": item["to"], "now": item["from"]} for item in changed] \
            or [{"layer": "Run", "setting": "capital", "then": used.get("capital"), "now": now.get("capital")}]
        layers += [{"layer": "Data", "setting": item["what"], "then": item["from"], "now": item["to"]} for item in data_changes]
        return {"params": record["params"], "run": used, "data": record["settings"]["data"], "venues": (record["data"] or {}).get("venues"), "layers": layers,
                "inputs": (record["data"] or {}).get("library", []) + (record["data"] or {}).get("models", []), "environment": record.get("environment"),
                "code": record["code"], "differs": differs}

    # --- pages of a run's tables ----------------------------------------------------------------------------------------------------
    @staticmethod
    def page(table, offset, limit):
        if limit > 500 or limit < 1:
            raise ContractError("invalid_input", "A page is 1 to 500 rows.")
        rows = json.loads(table.iloc[offset:offset + limit].to_json(orient="records"))
        return {"rows": rows, "total": len(table), "offset": offset, "nextOffset": offset + limit if offset + limit < len(table) else None}

    def trades(self, run_id, offset=0, limit=500, side=None, exit=None, losing=None, since=None, until=None, id=None):
        record = self.shown(self.object_runs.read(run_id))
        table = self.table(record, "trades")
        if id is not None:
            table = table[table["id"] == id]
        if since is not None or until is not None:
            # The trades that were open at some point in [since, until] (a drawdown's span); one still open has no exit yet.
            keep = pd.Series(True, index=table.index)
            if until is not None:
                keep &= table["entry"] <= until
            if since is not None:
                keep &= table["exit"].fillna(float("inf")) >= since
            table = table[keep]
        if side:
            table = table[table["side"] == side]
        if exit:
            table = table[table["exitBy"] == exit]
        if losing:
            table = table[table["pnl"] < 0]
        return self.page(table, offset, limit)

    def orders(self, run_id, offset=0, limit=500, status=None, since=None, until=None):
        """A page of the orders; a status is one of the Orders section's counts: TALYX's liquidation stops under their own name, the rest by state."""
        table = self.table(self.shown(self.object_runs.read(run_id)), "orders")
        if len(table) and since is not None:
            table = table[table["time"] >= since]
        if len(table) and until is not None:
            table = table[table["time"] <= until]
        if status and status != "All" and len(table):
            own = table["tags"].str.contains("exit:liquidation", regex=False)
            table = table[own] if status == LIQUIDATION_STOP else table[~own & (table["status"] == status)]
        for column in ("tags",):
            if column in table:
                table = table.assign(**{column: table[column].map(lambda value: json.loads(value) if isinstance(value, str) else value)})
        return self.page(table, offset, limit)

    def log(self, run_id, offset=0, limit=1000, kind=None, trade=None, since=None):
        if limit > 1000 or limit < 1:
            raise ContractError("invalid_input", "A log page is 1 to 1000 lines.")
        table = self.table(self.shown(self.object_runs.read(run_id)), "log")
        if table.empty:
            return {"rows": [], "total": 0, "offset": offset, "nextOffset": None}
        if since is not None:
            table = table[table["t"] >= since]  # a held-out validation's log starts where its held-out period does, as its trades do
        # The stops TALYX keeps at the liquidation price are its own bookkeeping: shown only when that kind is asked for.
        table = table[table["kind"] == kind] if kind else table[table["kind"] != LIQUIDATION_STOP]
        if trade is not None:
            table = table[table["trade"] == trade]
        rows = json.loads(table.iloc[offset:offset + limit].to_json(orient="records"))
        return {"rows": rows, "total": len(table), "offset": offset, "nextOffset": offset + limit if offset + limit < len(table) else None}

    def rebalance_table(self, record):
        """Every rebalance of a portfolio run, one a row, its parts as JSON text (`rebalance_rows` reads them)."""
        path = self.folder(record) / "rebalances.parquet"
        if not path.is_file():
            raise ContractError("unavailable", "This run has no rebalances.")
        return pd.read_parquet(path)

    @staticmethod
    def rebalance_rows(table):
        rows = json.loads(table.to_json(orient="records"))
        for row in rows:
            for key in REBALANCE_PARTS:
                row[key] = json.loads(row[key])
        return rows

    def rebalances(self, run_id, filter="changed", at=None, offset=0, limit=200, since=None, until=None):
        table = self.rebalance_table(self.shown(self.object_runs.read(run_id)))
        if limit > 200 or limit < 1:
            raise ContractError("invalid_input", "A page of rebalances is 1 to 200 rows.")
        if len(table):
            if since is not None:
                table = table[table["at"] >= since]
            if until is not None:
                table = table[table["at"] <= until]
            if at is not None:
                table = table[table["at"] == at]
            elif filter == "unfilled":
                table = table[table["unfilled"] != "[]"]
            elif filter == "changed":
                table = table[table["count"] > 0]
        return {"rows": self.rebalance_rows(table.iloc[offset:offset + limit]), "total": len(table), "offset": offset,
                "nextOffset": offset + limit if offset + limit < len(table) else None}

    # --- run settings -------------------------------------------------------------------------------------------------------------
    async def run_settings(self, object_id):
        """The run settings a strategy's next run uses: what the user set over the defaults for what it trades (fees from Market when it can say)."""
        from . import object_data
        record = self.store.read(object_id)
        if record["kind"] != "strategy":
            raise ContractError("invalid_input", "Run settings of this kind belong to a strategy.")
        data = record["settings"]["data"]
        if data.get("list"):
            data = {**data, "symbols": self.object_runs.lists.members(data["list"])}
        kind = "stock" if data.get("index") else strategy_settings.traded([item["instrument"] for item in data["symbols"]]) if data.get("symbols") else "perpetual"
        names = len(data.get("symbols") or []) or 1
        account, fee, source, note = record["settings"]["account"], None, None, None
        if all(data.get(key) for key in ("symbols", "timeframe", "since", "until")):
            try:
                found = await object_data.defaults(self.object_runs.ctx, {**record["settings"], "data": data})
                fee = found["account"]["fee"]
                source = "account settings" if account.get("fee") else "from Market (" + ", ".join(sorted({row["venue"] for row in found["instruments"]})) + ")"
            except ContractError as error:
                note = error.message
        stored = record["settings"].get("run") or {}
        effective = strategy_settings.effective(kind, stored, account, fee, source, held_out=data.get("heldOutFrom"), names=names)
        built = self.also_built(object_id)
        return {"objectId": object_id, "kind": kind, "effective": effective, "defaults": strategy_settings.defaults(kind, account, fee, source, names=names), "stored": stored,
                "applicable": {"exchange": strategy_settings.exchange_fields(kind), "margin": strategy_settings.margin_label(kind),
                               "benchmark": strategy_settings.benchmark_options(kind, names), "shorting": kind == "stock"},
                **({"feeUnavailable": note} if note else {}), **({"alsoBuilt": built} if built else {})}

    def also_built(self, object_id):
        """The periods the code built from smaller bars in the newest completed run (\"4 hours, 1 day from 1 hour\"); nothing when no run has finished or none were built."""
        row = self.object_runs.records.db.execute("""SELECT content FROM records WHERE kind='qrun' AND json_extract(content,'$.objectId')=? AND json_extract(content,'$.runKind')!='case'
            AND json_extract(content,'$.state')='completed' ORDER BY json_extract(content,'$.createdAt') DESC, id DESC LIMIT 1""", (object_id,)).fetchone()
        if not row:
            return None
        try:
            built = (self.report(self.shown(json.loads(row[0])), "work") or {}).get("builtBars") or []
        except ContractError:
            return None
        sources = sorted({item["from"] for item in built})
        return (", ".join(item["period"] for item in built) + " from " + ", ".join(sources)) if built else None

    # --- library inputs ---------------------------------------------------------------------------------------------------------
    def declared(self, obj):
        """Where the code names each library item: {name: (file, line, how)} (how: model for q.model, pick for q.pick)."""
        from pathlib import Path
        found = {}
        root = Path(obj["directory"])
        for path in sorted(root.rglob("*.py")):
            try:
                tree = ast.parse(path.read_text(encoding="utf-8"))
            except (SyntaxError, OSError):
                continue
            for node in ast.walk(tree):
                if isinstance(node, ast.Call) and node.args and isinstance(node.args[0], ast.Constant) and isinstance(node.args[0].value, str):
                    function = node.func
                    name = function.attr if isinstance(function, ast.Attribute) else function.id if isinstance(function, ast.Name) else None
                    if name == "model":
                        found[node.args[0].value] = (path.relative_to(root).as_posix(), node.lineno, "frozen")
        return found

    def used_by(self, object_id, version_id):
        row = self.object_runs.records.db.execute("""SELECT content FROM records WHERE kind='qrun' AND json_extract(content,'$.objectId')=? AND json_extract(content,'$.runKind')!='case'
            AND content LIKE ? ORDER BY json_extract(content,'$.createdAt') DESC LIMIT 1""", (object_id, f'%"versionId": "{version_id}"%')).fetchone()
        return json.loads(row[0])["id"] if row else None

    def card(self, version):
        out = {"versionId": version["versionId"], "version": version["version"], "publishedAt": version.get("publishedAt")}
        cutoff = version.get("trainingCutoff")
        if cutoff is not None:
            out["trainedTo"] = day_of(cutoff)
        return out

    def inputs(self, object_id):
        """The library items and models the code reads, each with the version it is pinned to and whether a newer one exists."""
        obj = self.store.read(object_id)
        pins = obj["settings"].get("inputs") if isinstance(obj["settings"].get("inputs"), dict) else {}  # a list: sources an earlier version kept
        switched = pins.get("switched") or {}
        declared = self.declared(obj)
        out = []
        held = obj["settings"].get("signalRun")
        if held:
            file, line, _how = declared.get(held["param"], (None, None, "values"))
            out.append({"name": held["model"], "kind": "run", "line": line, "file": file, "pinned": {"versionId": held["runId"], "version": held["number"]}, "fromRun": None,
                        "fromModelRun": {"runId": held["runId"], "number": held["number"], "model": held["model"]}})
        for group, default_kind in (("library", "factor"), ("models", "model")):
            for _key, pin in (pins.get(group) or {}).items():
                name = pin["name"]
                file, line, how = declared.get(name, (None, None, "values"))
                try:
                    version = self.library.read_version(pin["versionId"])
                    kind = version.get("kind") or default_kind
                    newest = self.library.resolve(name, kind="model") if kind == "model" else self.library.resolve_values(name)
                except ContractError as error:
                    out.append({"name": name, "kind": default_kind, "line": line, "file": file, "pinned": {"versionId": pin["versionId"]}, "unavailable": error.message,
                                "fromRun": self.used_by(object_id, pin["versionId"])})
                    continue
                card = {"name": name, "kind": kind, "line": line, "file": file, "pinned": self.card(version), "fromRun": self.used_by(object_id, pin["versionId"])}
                if how == "values" and kind == "model":
                    held = version.get("heldOutFrom")
                    card["reads"] = "values"
                    card["pickedOn"] = (date.fromisoformat(held) - timedelta(days=1)).isoformat() if held else None
                    card["pinned"].pop("trainedTo", None)
                if newest["versionId"] != pin["versionId"]:
                    card["newer"] = self.card(self.library.read_version(newest["versionId"]))
                if version.get("retired"):
                    card["retired"] = True
                if name in switched:
                    card["changedSince"] = switched[name]["runId"]
                    card["previous"] = switched[name]["previous"]
                out.append(card)
        return out

    def switch_input(self, object_id, name, version_id):
        """Pin another version of an input; the next run reads it (its results are not comparable with the runs before)."""
        obj = self.store.read(object_id)
        pins = obj["settings"].get("inputs") if isinstance(obj["settings"].get("inputs"), dict) else {}  # a list: sources an earlier version kept
        version = self.library.read_version(version_id)
        changed = None
        for group in ("library", "models"):
            for key, pin in (pins.get(group) or {}).items():
                if pin["name"] == name:
                    changed = (group, key, pin)
        if changed is None:
            raise ContractError("not_found", f"The code does not read {name}, or no run has read it yet.", {"name": name})
        if version["name"] != name or version["state"] == "delisted" or version.get("archivedAt") is not None:
            raise ContractError("invalid_input", f"Choose an available version of {name}.")
        _, _, old = changed
        last = self.used_by(object_id, old["versionId"])
        last_run = self.object_runs.read(last) if last else None
        same_as_last = bool(last_run) and version_id == old["versionId"]
        before = self.card(self.library.read_version(old["versionId"]))
        switched = dict(pins.get("switched") or {})
        incomparable = version_id != old["versionId"] and last is not None
        if incomparable:
            switched[name] = {"runId": last, "previous": before}
        elif name in switched and switched[name]["previous"]["versionId"] == version_id:
            switched.pop(name)  # back on the version the last run read
        pins = {**self.store.strategy_bindings(obj, obj["settings"], {name: version_id}), "switched": switched}
        self.store.save(obj, settings={**obj["settings"], "inputs": pins})
        return {"objectId": object_id, "items": self.inputs(object_id), "notComparable": name in switched and not same_as_last}
