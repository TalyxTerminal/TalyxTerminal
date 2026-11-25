"""Freeze Market exports without a second mutable price store."""
from __future__ import annotations

import asyncio
from collections import defaultdict

from talyx_sdk import ContractError
from talyx_sdk.i18n import t

from . import market_inputs as M
from .materials import acquire, release, run_task


def failure(problem, instrument):
    """Keep Market's code and original text, and identify the affected instrument."""
    return ContractError(problem["code"], f"{instrument['displaySymbol']}: {problem['message']}",
                         {"instrumentId": instrument["instrumentId"], "problem": problem}, execution="failed")


async def fetch(ctx, selections, since, until, progress):
    """Twenty targets per job retain every original failure (the public result caps at twenty)."""
    groups = defaultdict(dict)
    for item in selections:
        series = item["series"]
        kind = ("bars" if series["capability"] == "ohlcv" else series["capability"],
                series["timeframe"], series["priceBasis"])
        groups[kind][item["instrument"]["instrumentId"]] = item["instrument"]
    failures = []
    total = sum(len(items) for items in groups.values())
    done = 0
    for (kind, timeframe, basis), instruments in groups.items():
        items = list(instruments.values())
        for start in range(0, len(items), 20):
            batch = items[start:start + 20]
            spec = {"kind": kind, **({"timeframe": timeframe} if timeframe else {})}
            if kind == "bars":
                spec["priceBasis"] = basis
            def report(value, before=done):
                if progress is not None:
                    progress({**value, "done": before + value["done"], "total": total, "step": "prices"})
            result = await run_task(ctx, "market.fetch", {
                "symbols": [item["instrumentId"] for item in batch], "data": [spec],
                "start": M.timestamp(since), "end": M.timestamp(until)}, report)
            failures.extend(result["failures"])
            if result.get("state") == "interrupted":
                raise ContractError("interrupted", t("Market fetch was interrupted."), {"jobId": result["jobId"]}, execution="failed")
            done += len(batch)
    return failures


def file_inventory(files):
    """Only identity/time columns are scanned; the full price table is never concatenated here."""
    import pyarrow as pa
    import pyarrow.parquet as pq

    found = {}
    for ref in files:
        table = pq.read_table(ref["path"], columns=["instrumentId", "time"])
        summary = table.group_by("instrumentId").aggregate([("time", "min"), ("time", "max"), ("time", "count")])
        firsts = summary["time_min"].cast(pa.int64()).to_pylist()
        lasts = summary["time_max"].cast(pa.int64()).to_pylist()
        for row, first_ns, last_ns in zip(summary.to_pylist(), firsts, lasts, strict=True):
            key = (ref["content"], row["instrumentId"])
            first, last = first_ns // M.MS, last_ns // M.MS
            before = found.get(key)
            if before is None:
                found[key] = {"files": [ref], "rows": row["time_count"], "start": first, "end": last}
            else:
                before["files"].append(ref)
                before["rows"] += row["time_count"]
                before["start"], before["end"] = min(before["start"], first), max(before["end"], last)
    return found


async def capture_window(ctx, request, owner):
    selections = request["selections"]
    since, until = request["since"], request["until"]
    progress = request.get("progress")
    allow_empty = request.get("allowEmpty", False)
    failures = await fetch(ctx, selections, since, until, progress) if request.get("fill", "missing") == "missing" else []
    instruments = {item["instrument"]["instrumentId"]: item["instrument"] for item in selections}
    if failures and not allow_empty:
        first = failures[0]
        raise failure(first["problem"], instruments[first["instrumentId"]])
    failed = {item["instrumentId"]: item["problem"] for item in failures}
    pending = [item for item in selections if item["instrument"]["instrumentId"] not in failed]
    before = await M.revisions(ctx, pending, until)
    references, selected, gaps = [], {}, []
    groups = defaultdict(list)
    for item in pending:
        series = item["series"]
        groups[(series["priceBasis"], series.get("importId"))].append(item)
    try:
        for items in groups.values():
            todo = items
            for attempt in range(3):
                result = await run_task(ctx, "market.export", {
                    "series": [{"instrumentId": item["instrument"]["instrumentId"], "series": item["series"]} for item in todo], "start": M.timestamp(since), "end": M.timestamp(until),
                    "fill": "none", "allowEmpty": allow_empty, "format": "parquet"},
                    progress and (lambda value: progress({**value, "step": "export"})))
                own, files = [], []
                # The file each series went to, as Market says.
                written = {series: item["content"] for item in result["files"] for series in item["series"]}
                try:
                    for item in result["files"]:
                        kept = await acquire(ctx, item["artifact"], owner, own)
                        files.append({**kept, "content": item["content"]})
                    after = await M.revisions(ctx, todo, until)
                    changed = [item for item in todo if before[item["series"]["seriesId"]] != after[item["series"]["seriesId"]]]
                    if changed:
                        await release(ctx, own, owner)
                        own = []
                        if attempt == 2:
                            raise ContractError("conflict", t("Market data changed repeatedly while freezing the run."),
                                                {"instruments": [item["instrument"]["instrumentId"] for item in changed]})
                        before.update(after)
                        # The artifact groups several names; regenerate this bounded group as one consistent file.
                        continue
                    inventory = await asyncio.to_thread(file_inventory, files)
                    for item in result["failures"]:
                        failed[item["instrumentId"]] = item["problem"]
                    gaps.extend(result["gaps"])
                    if result["failures"] and not allow_empty:
                        first = result["failures"][0]
                        raise failure(first["problem"], instruments[first["instrumentId"]])
                    for item in todo:
                        series, instrument = item["series"], item["instrument"]
                        detail = (None if instrument["instrumentId"] in failed or series["seriesId"] not in written else
                                  inventory[(written[series["seriesId"]], instrument["instrumentId"])])
                        revision = after[series["seriesId"]]
                        selected[series["seriesId"]] = {
                            **item, "files": detail["files"] if detail else [], "version": revision["revision"],
                            "source": revision["source"],
                            "coverage": {"exportedRows": detail["rows"] if detail else 0,
                                         "exportedRange": {"start": detail["start"], "end": detail["end"]} if detail else None,
                                         "gaps": [row["gap"] for row in result["gaps"] if row["instrumentId"] == instrument["instrumentId"]]},
                        }
                    references.extend(own)
                    own = []
                    break
                finally:
                    if own:
                        await release(ctx, own, owner)
        for item in selections:
            series, instrument = item["series"], item["instrument"]
            if series["seriesId"] not in selected:
                selected[series["seriesId"]] = {**item, "files": [], "version": 0, "source": None,
                                               "coverage": {"exportedRows": 0, "exportedRange": None, "gaps": []}}
            if instrument["instrumentId"] in failed:
                selected[series["seriesId"]]["problem"] = failed[instrument["instrumentId"]]
        return {"kind": "market", "references": references,
                "manifest": {"selections": [selected[item["series"]["seriesId"]] for item in selections],
                             "requestedRange": {"since": since, "until": until}, "gaps": gaps,
                             "failures": [{"instrumentId": key, "problem": problem} for key, problem in failed.items()]}}
    except BaseException:
        await release(ctx, references, owner)
        raise



async def capture(ctx, request, owner):
    """A date selection may span calendars; each export and its revision check uses that calendar's actual instants."""
    windows = request.get("windows")
    if windows is None:
        return await capture_window(ctx, request, owner)
    groups = defaultdict(list)
    for selection in request["selections"]:
        groups[tuple(windows[selection["instrument"]["instrumentId"]])].append(selection)
    references, selected, gaps, failures = [], {}, [], []
    try:
        for (since, until), selections in groups.items():
            captured = await capture_window(ctx, {**request, "selections": selections, "since": since, "until": until}, owner)
            references.extend(captured["references"])
            manifest = captured["manifest"]
            selected.update((item["series"]["seriesId"], item) for item in manifest["selections"])
            gaps.extend(manifest["gaps"])
            failures.extend(manifest["failures"])
        return {"kind": "market", "references": references, "manifest": {
            "selections": [selected[item["series"]["seriesId"]] for item in request["selections"]],
            "requestedRange": {"since": request["since"], "until": request["until"]}, "gaps": gaps, "failures": failures}}
    except BaseException:
        await release(ctx, references, owner)
        raise
