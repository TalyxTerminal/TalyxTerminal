"""A run's public summary, sections and tables are shared by reads and activity completion."""

from __future__ import annotations

import asyncio
import json
from pathlib import Path

from talyx_sdk import ContractError

from . import ai_views, chart_layers, market_inputs, public_settings, strategy_ai
from .public_pages import complete_json, offset, table
from .quant_home import run_summary
from .run_materials import run_directory

PARTS = {
    "strategy": "statistics equity periods rolling longShort tradeStats orderStats account chartLayer sectors instruments style costs crisis settings code",
    "model": "chartLayer returns information turnover inputs robustness settings code",
    "factor": "chartLayer returns ic industry style turnover costs event settings code",
    "allocation": "weights risk returns style robust settings",
}
TABLES = {
    "strategy": "trades orders rebalances daily",
    "model": "windows values daily",
    "factor": "crossSection daily",
    "allocation": "periods daily",
}
RUN_KINDS = {"backtest": "run", "train": "run", "heldout": "heldOut", "walk": "walkForward"}
FILTERS = {
    "trades": {"side", "exit", "losing"},
    "orders": {"status"},
    "rebalances": {"only"},
    "log": {"stream"},
    "cells": {"state"},
}


def run_kind(record):
    if record.get("batch") or record.get("allocation") and record.get("sweep"):
        return "sweep"
    return RUN_KINDS.get(record["runKind"], record["runKind"])


def numbers(record):
    values = {
        **(record.get("stats") or {}),
        **(record.get("metrics") or {}),
        **(record.get("numbers") or {}),
    }
    return {key: value for key, value in values.items() if type(value) in (float, int)}


def stamp(value):
    return public_settings.timestamp(value) if type(value) in (float, int) else value


def tested(data, timeframe):
    if data.get("tested"):
        return {"timeframe": timeframe, **data["tested"]}
    begin, end = data.get("dataFrom"), data.get("dataTo")
    if begin is None or end is None:
        return None
    return {
        "start": stamp(begin)[:10],
        "end": stamp(end)[:10],
        "bars": data.get("bars") or data.get("rows", 0),
        "timeframe": timeframe,
    }


def parts_and_tables(record):
    kind = record["objectKind"]
    extra = {"factor": ["fetch"], "model": ["valuesProgress"], "allocation": ["code"], "strategy": ["inputVersions"]}[kind]
    if run_kind(record) in {"publish", "heldOut"}:
        return ["summary", *extra], ["log"]
    if run_kind(record) in {"sweep", "walkForward"}:
        return ["summary", "settings", *extra], ["cells", "log"]
    return ["summary", *PARTS[kind].split(), *extra], [
        *TABLES[kind].split(),
        *(["cells"] if record.get("sweep") or record.get("batch") else []),
        "log",
    ]


def receipt(record):
    return {"runId": record["id"], "state": record.get("state") or record["status"]}


async def summary(services, record):
    kind = record["objectKind"]
    settings = await run_settings(services, record)
    previous = services.history.before(record)
    changes = []
    if previous and (previous.get("settings"), previous.get("params")) != (
        record.get("settings"),
        record.get("params"),
    ):
        changes = public_settings.changed(await run_settings(services, previous), settings)
    data = record.get("data") or {}
    span = settings["data"]
    drops = data.get("skipped") or []
    used = data.get("traded", data.get("names"))
    if not isinstance(used, int):
        used = len(data.get("instruments") or [])
    total = data.get("requested")
    if type(total) is not int:
        total = len(span.get("symbols") or []) or used + len(drops)
    progress = services.strategy.progress(record) if kind == "strategy" else record.get("progress")
    if isinstance(progress, dict):
        progress = {k: v for k, v in progress.items() if k not in {"figures", "equity", "figure"}}
    parts, tables = parts_and_tables(record)
    measured = tested(data, span.get("timeframe", "1d"))
    if (
        record["runKind"] == "heldout"
        and kind == "strategy"
        and measured
        and record["state"] == "completed"
    ):
        path = services.strategy.folder(record) / "work.json"
        if path.is_file():
            report = json.loads(await asyncio.to_thread(path.read_text, encoding="utf-8"))
            start = (
                public_settings.milliseconds(span_start)
                if (span_start := settings["run"].get("heldOutFrom"))
                else 0
            )
            scored = [r[0] for r in report.get("dailyReturns", []) if r[0] >= start]
            if scored:
                measured = {
                    **measured,
                    "start": stamp(scored[0])[:10],
                    "end": stamp(scored[-1])[:10],
                    "bars": len(scored),
                }
    result = {
        **receipt(record),
        "objectId": record["objectId"],
        "objectName": record["name"],
        "number": record.get("number") or 0,
        "kind": kind,
        "runKind": run_kind(record),
        "summary": run_summary(record),
        "progress": progress,
        "tested": measured,
        "requested": {
            "start": span.get("start"),
            "end": span.get("end"),
            "untilLatest": bool(data.get("untilLatest") or span.get("end") is None),
        },
        "heldOutFrom": settings["run"].get("heldOutFrom"),
        "numbers": numbers(record),
        "symbols": {
            "set": total,
            "used": used,
            "droppedTotal": len(drops),
            "dropped": [{"symbol": d["symbol"], "reason": d["reason"]} for d in drops[:20]],
        },
        "checks": record.get("checks") or [],
        "settings": settings,
        "changed": changes,
        "parts": parts,
        "tables": tables,
        "failure": record.get("failure") or record.get("error"),
        "createdAt": stamp(record["createdAt"]),
        "endedAt": stamp(record.get("endedAt")),
    }

    if run_kind(record) in {"sweep", "walkForward"}:
        cells = await sweep_cells(services, record)
        sweep = record.get("sweep") or {}
        terminal = {"completed", "cancelled", "failed", "interrupted"}
        ended = sum(cell["state"] in terminal for cell in cells)
        result["batch"] = {
            "total": sweep.get("total", len(cells)),
            "ended": ended,
            "failed": sum(cell["state"] == "failed" for cell in cells),
            "best": sweep.get("bestIndex", sweep.get("best")),
            "table": "cells",
        }
        batch = result["batch"]
        metric = (
            sweep.get("metric")
            or {
                "strategy": "sharpe",
                "factor": "longShortNet",
                "model": "ric",
                "allocation": "sharpe",
            }[kind]
        )
        ranked = sorted(
            cells,
            key=lambda cell: (
                cell["numbers"].get(metric)
                if isinstance(cell["numbers"].get(metric), (float, int))
                else float("-inf")
            ),
            reverse=True,
        )
        batch["metric"] = metric
        axes = {}
        for cell in cells:
            for name, value in (cell.get("values") or {}).items():
                values = axes.setdefault(name, [])
                if value not in values:
                    values.append(value)
        batch["axes"] = sweep.get("axes") or [
            {"name": name, "values": values} for name, values in axes.items()
        ]
        if batch["best"] is None and ranked and ranked[0]["numbers"].get(metric) is not None:
            batch["best"] = ranked[0]["cell"]
        batch["cells"] = ai_views.fit(
            ranked[:20], lambda kept: {**result, "batch": {**batch, "cells": kept}}
        )
        if walk := record.get("walk"):
            windows = walk.get("windows") or []
            batch.update(
                efficiency=walk.get("efficiency"),
                changed=walk.get("changed"),
                windowsTotal=len(windows),
            )
            batch["windows"] = ai_views.fit(
                windows[:20], lambda kept: {**result, "batch": {**batch, "windows": kept}}
            )
        if record.get("batch") or kind == "allocation":
            if ended != len(cells):
                result["state"] = "running"
            elif any(cell["state"] == "failed" for cell in cells):
                result["state"] = "failed"
    return result


async def sweep_cells(services, record):
    sweep = record.get("sweep") or {}
    if record["objectKind"] == "strategy":
        return [
            {
                "cell": c["index"],
                "runId": c.get("runId"),
                "state": c["state"],
                "values": c.get("params"),
                "numbers": c.get("metrics") or {},
            }
            for c in sweep.get("cases", [])
        ]
    if record["objectKind"] == "factor":
        if record["state"] != "completed":
            return []
        report = await asyncio.to_thread(services.factors.sweep, record["id"])
        return [
            {"cell": i, "state": "completed", "values": c.get("combo"), "numbers": numbers(c)}
            for i, c in enumerate(report["cells"])
        ]
    group = record.get("batch") or sweep["id"]
    rows = [
        r
        for r in services.records.list("qrun")
        if (r.get("batch") or (r.get("sweep") or {}).get("id")) == group
    ]
    return [
        {
            "cell": i,
            "runId": r["id"],
            "state": r["state"],
            "values": r.get("values") or (r.get("sweep") or {}).get("cell"),
            "numbers": numbers(r),
        }
        for i, r in enumerate(sorted(rows, key=lambda item: item["number"]))
    ]


async def run_settings(services, record):
    kind = record["objectKind"]
    saved = record.get("settings") or {}
    obj = {
        "kind": kind,
        "settings": {**saved, "params": saved.get("params", record.get("params", {}))},
    }
    settings, _ = await public_settings.settings(services.store.ctx, obj)
    return settings


async def compare(services, record, other_id):
    other = services.runs.read(other_id)
    if record["objectKind"] != other["objectKind"]:
        raise ContractError(
            "invalid_input", "compareTo must reference another run of the same kind."
        )
    left, right = await summary(services, record), await summary(services, other)
    a, b = left["numbers"], right["numbers"]
    return {
        "settingsChanged": public_settings.changed(right["settings"], left["settings"]),
        "codeChanged": (record.get("code") or record.get("codeSnapshot"))
        != (other.get("code") or other.get("codeSnapshot")),
        "dataChanged": left["settings"]["data"] != right["settings"]["data"],
        "sameSpan": left["tested"] == right["tested"],
        "numbers": {
            k: {
                "this": a.get(k),
                "other": b.get(k),
                "diff": a[k] - b[k] if k in a and k in b else None,
            }
            for k in sorted(a.keys() | b.keys())
        },
    }


async def read(services, args):
    identity = args["runId"]
    record = services.runs.read(identity)
    kind, part = record["objectKind"], args.get("part", "summary")
    parts, _ = parts_and_tables(record)
    if part not in parts or args.get("cursor") and part != "code":
        raise ContractError(
            "invalid_input", f"Available parts: {', '.join(parts)}. cursor applies to code."
        )
    if part == "chartLayer":
        return {"runId": identity, "part": part, "data": await chart_layers.read(services, record, args)}
    if isinstance(args.get("compareTo"), list):
        raise ContractError("invalid_input", "Multiple comparison runs apply only to chartLayer.")
    if args.get("instrumentId") or args.get("around") or args.get("includeInstruments"):
        raise ContractError("invalid_input", "instrumentId, around and includeInstruments apply to chartLayer.")
    if part == "summary":
        result = await summary(services, record)
    else:
        if part == "settings":
            block = (await summary(services, record))["settings"]
        elif part == "fetch":
            block = await complete_json(services.store.ctx, services.factors.fetch(identity), "fetch.json")
        elif part == "valuesProgress":
            block = services.models.values_progress(identity)
        elif part == "inputVersions":
            data = record.get("data") or {}
            block = {group: [{k: item[k] for k in ("name", "versionId", "version") if k in item}
                             for item in data.get(group) or []] for group in ("library", "models")}
        elif part == "code":
            if kind == "allocation":
                files = services.allocation.code(identity)["files"]
            elif kind == "model":
                files = services.models.code(identity)["files"]
            elif record.get("code"):
                source = record["code"]
                files = [
                    {
                        "file": source["entry"],
                        "text": (Path(source["snapshotPath"]) / source["entry"]).read_text(
                            encoding="utf-8"
                        ),
                    }
                ]
            else:
                files = [{"file": "formula", "text": record.get("formula") or ""}]
            block = ai_views.chunk_files(files, offset(args))
        elif kind == "strategy":
            section = {"statistics": "summary", "tradeStats": "trades", "orderStats": "orders"}.get(
                part, part
            )
            data = await asyncio.to_thread(services.strategy.results, identity, section)
            block = strategy_ai.results(services.strategy, data, {"section": section})
        elif kind == "factor":
            block = await services.factors.results(identity, part, (), None, figures=False)
            block = ai_views.bound(
                ai_views.lean(
                    block.get("results") or {"state": record.get("resultsStatus", "building")}
                )
            )
        elif kind == "model":
            data = await asyncio.to_thread(services.models.results, identity, None)
            block = ai_views.model_results(
                {**data, "results": {part: data["results"].get(part)}}, part
            )["results"]
        else:
            from .allocation_routes import for_ai

            block = for_ai(await asyncio.to_thread(services.allocation.results, identity, part))
        result = {"runId": identity, "part": part, "data": block}
        if part == "code":
            next_offset = block.pop("nextOffset")
            result["nextCursor"] = str(next_offset) if next_offset is not None else None
    if args.get("compareTo"):
        result["compare"] = await compare(services, record, args["compareTo"])
    return result


def daily_records(services, record):
    import pandas as pd

    if record["objectKind"] == "strategy":
        work = json.loads(
            (services.strategy.folder(services.strategy.shown(record)) / "work.json").read_text(
                encoding="utf-8"
            )
        )
        points = {
            name: dict((point[0], point[1]) for point in (work.get(key) or []))
            for name, key in (
                ("equity", "equity"),
                ("benchmark", "benchmark"),
                ("drawdown", "drawdown"),
                ("return", "dailyReturns"),
                ("riskFree", "dailyRiskFree"),
            )
        }
        return [
            {"date": date, **{name: values.get(date) for name, values in points.items()}}
            for date in points["return"]
        ]
    directory = run_directory(services.runs, record) / "output"
    if record["objectKind"] == "factor":
        import numpy as np

        with np.load(directory / "daily.npz") as data:
            return [
                {
                    "date": int(date),
                    **{
                        k: (float(data[k][i]) if np.isfinite(data[k][i]) else None)
                        for k in data
                        if k != "labels"
                    },
                }
                for i, date in enumerate(data["labels"])
            ]
    if record["objectKind"] == "allocation":
        frame = pd.read_parquet(directory / "equity.parquet").rename_axis("date").reset_index()
        frame["date"] = pd.to_datetime(frame["date"], utc=True).map(
            lambda moment: moment.isoformat()
        )
        return frame.to_dict("records")
    import numpy as np

    meta = json.loads((directory / "pred.json").read_text(encoding="utf-8"))
    pred = np.load(directory / "pred.npy", mmap_mode="r")
    labels = np.load(directory / "label.npy", mmap_mode="r")
    from ..native import metrics

    ranks = metrics.rank_correlation(pred, labels)
    counts = np.isfinite(pred).sum(axis=1)
    return [
        {
            "date": date,
            "names": int(counts[i]),
            "rankIc": metrics.finite(ranks[i]),
        }
        for i, date in enumerate(meta["labels"])
    ]


async def row_symbols(services, ctx, record):
    """Map the labels frozen by the engine to presentable stable identities."""
    data = record.get("data") or {}
    entries = []
    if data.get("infoPath"):
        facts = json.loads(
            await asyncio.to_thread(Path(data["infoPath"]).read_text, encoding="utf-8")
        )
        for source in facts["sources"]:
            entries.append((source["nativeInstrument"], source["marketInstrument"]))
    elif data.get("key"):
        path = services.factors.data.directory(data["key"]) / "info.json"
        if path.is_file():
            facts = json.loads(await asyncio.to_thread(path.read_text, encoding="utf-8"))
            entries = [
                (item["column"], item["instrument"])
                for item in facts["instruments"]
                if item.get("instrument")
            ]
    if not entries:
        entries = [
            (s["instrument"]["displaySymbol"], s["instrument"])
            for s in record.get("settings", {}).get("data", {}).get("symbols", [])
        ]
    result = {}
    for (key, source), match in zip(
        entries, await market_inputs.saved_items(ctx, [i for _, i in entries]), strict=True
    ):
        identity = match["instrument"]["instrumentId"]
        for label in (key, identity, source["symbol"], source["displaySymbol"]):
            result[label] = (match["symbol"], identity)
    return result


async def rows(services, ctx, args):
    identity, name = args["runId"], args["table"]
    record = services.runs.read(identity)
    _, tables = parts_and_tables(record)
    filters = args.get("filter") or {}
    if name not in tables or filters.keys() - FILTERS.get(name, set()):
        raise ContractError(
            "invalid_input",
            f"Tables: {', '.join(tables)}. Filters for {name}: {', '.join(sorted(FILTERS.get(name, ())))}.",
        )
    start = public_settings.milliseconds(args["start"]) if args.get("start") else None
    end = public_settings.milliseconds(args["end"], end=True) if args.get("end") else None
    at = public_settings.milliseconds(args["date"]) if args.get("date") else None
    if at is not None and name not in {"crossSection", "values", "rebalances"}:
        raise ContractError("invalid_input", f"date does not select rows of {name}.")
    if (start is not None or end is not None) and name not in {
        "daily",
        "trades",
        "orders",
        "rebalances",
        "periods",
    }:
        raise ContractError("invalid_input", f"start and end do not select rows of {name}.")
    base_offset, total = offset(args), None
    page_args = args
    if name in {"trades", "orders", "rebalances"}:
        method = getattr(services.strategy, name)
        values = []
        # A row request reads one slice of the stored report. Files collect every slice.
        file = args.get("format") == "file"
        index = 0 if file else base_offset
        size = min(200 if name == "rebalances" else 500, 500 if file else args.get("limit", 25))
        while index is not None:
            if name == "trades":
                found = await asyncio.to_thread(
                    method,
                    identity,
                    index,
                    size,
                    filters.get("side"),
                    filters.get("exit"),
                    filters.get("losing"),
                    start,
                    end,
                )
            elif name == "orders":
                found = await asyncio.to_thread(
                    method, identity, index, size, filters.get("status"), start, end
                )
            else:
                found = await asyncio.to_thread(
                    method, identity, filters.get("only", "changed"), at, index, size, start, end
                )
            if name == "rebalances" and not file:
                values.extend(strategy_ai.rebalance_row(row) for row in found["rows"])
            else:
                values.extend(found["rows"])
            total = found["total"]
            index = found["nextOffset"] if file else None
        page_args = {**args, "cursor": None}
    elif name == "crossSection":
        values = sorted(
            (await asyncio.to_thread(services.factors.cross_section, identity, at, record))["rows"],
            key=lambda r: (r["rank"] is None, r["rank"] or 0),
        )
    elif name == "values":
        values = (await services.models.values(identity, at))["rows"]
    elif name == "windows":
        values = record.get("windows") or []
    elif name == "periods":
        values = await asyncio.to_thread(services.allocation.periods_done, identity)
    elif name == "cells":
        values = await sweep_cells(services, record)
        if filters.get("state"):
            values = [r for r in values if r["state"] == filters["state"]]
    elif name == "log":
        stream = filters.get("stream", "stderr")
        if stream not in {"stdout", "stderr", "environment_stdout", "environment_stderr"}:
            raise ContractError("invalid_input", "Unknown log stream.")
        if stream.startswith("environment_"):
            saved = (record.get("environment") or {}).get(
                "stdoutPath" if stream == "environment_stdout" else "stderrPath"
            )
            path = Path(saved) if saved else None
        else:
            path = run_directory(services.runs, record) / "output" / (stream + ".log")
        content = (
            await asyncio.to_thread(path.read_text, encoding="utf-8")
            if path and path.is_file()
            else ""
        )
        values = [
            {"line": index, "text": line} for index, line in enumerate(content.splitlines(), 1)
        ]
    else:
        values = await asyncio.to_thread(daily_records, services, record)
    processed = []
    identities = (
        await row_symbols(services, ctx, record)
        if values and name in {"trades", "orders", "crossSection", "values"}
        else {}
    )
    for row in values:
        value = dict(row)
        for key in ("nativeInstrument", "instrument", "instrumentId", "ticker", "column"):
            match = identities.get(value.get(key)) if isinstance(value.get(key), str) else None
            if match:
                value["symbol"], value["instrumentId"] = match
                break
        for k in ("time", "date", "entry", "exit", "filledAt", "at", "heldFrom"):
            if type(value.get(k)) in (int, float):
                value[k] = stamp(value[k])
        date = value.get("date") or value.get("time") or value.get("at")
        if date and (start is not None or end is not None):
            moment = public_settings.milliseconds(date)
            if start is not None and moment < start or end is not None and moment > end:
                continue
        processed.append(value)
    units = {
        "ret": "fraction",
        "return": "fraction",
        "drawdown": "fraction",
        "rankIc": "correlation",
        "holding": "bars",
        "days": "days",
        "bars": "bars",
        "qty": "units",
        "weight": "fraction",
        "turnover": "fraction",
        "riskShare": "fraction",
    }
    result = await table(ctx, processed, page_args, units=units)
    if total is not None:
        result["total"] = total
        if "rows" in result:
            finish = base_offset + len(result["rows"])
            result["nextCursor"] = str(finish) if finish < total else None
    return result
