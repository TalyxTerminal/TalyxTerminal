"""Publishing a finished run into the library: its history, a look for future data, then one commit.

A factor run is published from the factor engine: the same formula or code, data and settings compute the values over the whole data period,
and again with the data cut at three dates inside the in-sample period (the held-out end is never used for a check); a value that changes
when later data is removed blocks the publication and names the time it changed at. A model or allocation run is published from its
research run: the values the run computed, checked the same way by its research worker. A model's predictions and its trained model are one
version. The published figures are the run's own (a factor's Rank IC at its holding, with t, long–short and the held-out validation when
there is one). Every step reports its progress on the publication's record; nothing heavy runs on the Host's loop.
"""
from __future__ import annotations

import asyncio
import contextlib
import json
import shutil
import time
from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4

import numpy as np
import pandas as pd
from talyx_sdk import ContractError

from . import library_after, library_checks, research_calendar, research_process
from .object_runs import RESEARCH_KINDS, RUN
from .project_paths import filesystem_path

VALUE_KINDS = ("factor", "prediction", "allocation", "model")
CHECK_POINTS = (0.55, 0.75, 0.92)
EDGE = {"factor": "close", "prediction": "close", "allocation": "open", "model": "close"}
FIELD = "value"


def stamp(label, timeframe):
    """A bar label as a person reads it: the date for daily bars, date and time otherwise."""
    moment = datetime.fromtimestamp(label / 1000, UTC)
    return moment.strftime("%Y-%m-%d") if timeframe[-1] == "d" else moment.strftime("%Y-%m-%d %H:%M UTC")


def human(label, timeframe):
    """A bar label in words: Mar 3, 2021, and the time of day for bars shorter than a day."""
    moment = datetime.fromtimestamp(label / 1000, UTC)
    text = f"{moment:%b} {moment.day}, {moment.year}"
    return text if timeframe[-1] == "d" else f"{text} {moment:%H:%M} UTC"


def first_difference(history, cut, cutoff):
    """The earliest value that changes when the data is cut at `cutoff`, or None. A value present with all the data but absent when cut is a change too."""
    before = history[history["datetime"] <= cutoff]
    merged = before.merge(cut[cut["datetime"] <= cutoff], on=["datetime", "instrument", "field"], how="left", suffixes=("", "Cut"))
    changed = merged[~np.isclose(merged["value"], merged["valueCut"].astype(float), rtol=1e-6, atol=1e-9, equal_nan=False)]
    if changed.empty:
        return None
    row = changed.sort_values(["datetime", "instrument"]).iloc[0]
    return {"row": row, "count": len(changed)}


def edge_times(labels, timeframe, zone, found, edge):
    """When each value could be known: the close of its bar (a value from that bar's close) or its open (weights that govern the bar)."""
    by_label = {research_calendar.label_of(opened, timeframe, zone): (opened, closed) for opened, closed in found}
    missing = [label for label in labels if label not in by_label]
    if missing:
        raise ContractError("unavailable", f"Market's calendar has no bar at {stamp(missing[0], timeframe)}, where this publication has a value.", {"label": missing[0]})
    return {label: by_label[label][1 if edge == "close" else 0] for label in labels}


def long_rows(values, instruments, availability, recorded, mode):
    """Value rows as the library stores them: instrument id, field, datetime, value, available at, recorded at, mode."""
    unknown = sorted(set(values["instrument"]) - set(instruments))
    if unknown:
        raise ContractError("invalid_input", f"The publication names {unknown[0]}, which is not one of the symbols in this run's data.", {"instruments": unknown})
    return [(instruments[row.instrument], row.field, int(row.datetime), float(row.value), int(availability[int(row.datetime)]), recorded, mode) for row in values.itertuples()]


def long_table(values, instruments, availability, recorded, mode, field=None):
    """The same as a table, for many rows at once."""
    unknown = sorted(set(values["instrument"]) - set(instruments))
    if unknown:
        raise ContractError("invalid_input", f"The publication names {unknown[0]}, which is not one of the symbols in this run's data.", {"instruments": unknown})
    labels = values["datetime"].astype("int64")
    return pd.DataFrame({"instrument": values["instrument"].map(instruments), "field": field if field is not None else values["field"], "datetime": labels,
                         "value": values["value"].astype(float), "availableAt": labels.map(availability).astype("int64"), "recordedAt": recorded, "mode": mode})


def measure_text(holding, timeframe):
    return f"Rank IC {holding}D" if timeframe and timeframe[-1] == "d" else f"Rank IC {holding} bars"


def universe_text(data):
    if data.get("index"):
        return data.get("indexName") or (data["index"].get("name") if isinstance(data["index"], dict) else None) or "Index members on each day"
    if data.get("list"):
        return data.get("listName") or "A research list"
    symbols = data.get("symbols") or []
    return ", ".join(item["instrument"].get("displaySymbol", "") for item in symbols[:5]) + (f" and {len(symbols) - 5} more" if len(symbols) > 5 else "") if symbols else "Chosen by the code"


def settings_text(data, run, timeframe):
    grouping = run.get("grouping") or {}
    parts = [universe_text(data), f"{run.get('holding', 1)}{'D' if timeframe and timeframe[-1] == 'd' else ' bars'}"]
    if run.get("neutralize") and run["neutralize"] != "none":
        parts.append(run["neutralize"])
    if grouping.get("count"):
        parts.append(f"{grouping['count']} groups")
    if run.get("costBps") is not None:
        parts.append(f"{run['costBps']:g} bps")
    return ", ".join(parts)


class Publisher:
    def __init__(self, ctx, records, store, runs, library, factors=None, notifications=None):
        self.ctx, self.records, self.store, self.runs, self.library, self.factors, self.notifications = ctx, records, store, runs, library, factors, notifications
        self.active = {}  # source run id -> the publication running for it
        self.allocations = None  # the allocation runs (set by the services): their backtests are frozen as research runs when published

    # --- starting ----------------------------------------------------------------------------------------------------------
    def check_source(self, source, args):
        if source["runKind"] != "run" or source["objectKind"] not in RESEARCH_KINDS:
            raise ContractError("invalid_input", "Publish a factor, model or allocation run.")
        if source["state"] != "completed" or source["mode"] != "run":
            raise ContractError("conflict", "Publish a completed standard run; a quick check or a failed run cannot be published.")
        result = self.runs.result(source)
        listed = result["publications"] if result and "publications" in result else []
        if not listed:
            raise ContractError("invalid_input", "This run's report holds nothing to publish. Call q.report.factor, q.report.model or q.report.allocation in the code.")
        names = args.get("items")
        chosen = [item for item in listed if names is None or item["name"] in names]
        if not chosen or (names and len(chosen) != len(set(names))):
            raise ContractError("invalid_input", "Choose among what this run can publish: " + ", ".join(item["name"] for item in listed) + ".", {"items": [item["name"] for item in listed]})
        return result, chosen

    def plan_factor(self, run_id, name=None):
        """What publishing this factor run does, for the publish sheet; no values are computed. The next version and the last one's figures to compare with,
        who uses the last version, the checks to look at, the library factor this run is closest to, and whether the name is taken by another kind."""
        source = self.runs.read(run_id)
        if source.get("objectKind") != "factor" or source["runKind"] != "run" or source["state"] != "completed":
            raise ContractError("conflict", "Publish a completed factor run; a sweep or a held-out validation cannot be published.")
        name = (name or source["name"]).strip()
        existing = self.store.find_item(name)
        blocked = {"reason": f"{name} is already a {existing[0]['kind']} in the library; choose another name."} if existing and existing[0]["kind"] != "factor" else None
        versions = self.store.item_versions(existing[0]["id"]) if existing and blocked is None else []
        last = versions[0] if versions else None
        before = self.store.version(last["versionId"])["meta"] if last else {}
        timeframe = ((source.get("data") or {}).get("info") or {}).get("timeframe") or before.get("timeframe") or "1d"
        run, data = source["settings"]["run"], source["settings"]["data"]
        numbers = source.get("numbers") or {}
        held = {}
        if source.get("heldOut"):
            with contextlib.suppress(ContractError):
                held = self.factors.read(source["heldOut"]["runId"]).get("numbers") or {}
        published = before.get("published") or {}
        unit_of = lambda holding: f"{holding}{'D' if timeframe[-1] == 'd' else ' bars'}"  # noqa: E731
        measures = [("Rank IC", published.get("value"), numbers.get("rankIc"), "ic"), ("t", published.get("t"), numbers.get("t"), "t"),
                    ("Long–short net", published.get("ls"), numbers.get("longShortNet"), "pct"), ("Turnover", published.get("turnover"), numbers.get("turnover"), "pct"),
                    ("Names a day", published.get("names"), numbers.get("names"), "count"),
                    ("Holding", unit_of(published["holding"]) if published.get("holding") else None, unit_of(run.get("holding", 1)), "text"),
                    ("Neutralize", (before.get("settings") or {}).get("neutralize"), run.get("neutralize"), "text"),
                    ("Universe", before.get("universe"), universe_text(data), "text"),
                    ("Held-out rank IC", published.get("heldOut"), held.get("rankIc"), "ic"), ("Held-out long–short", published.get("heldOutLs"), held.get("longShortNet"), "pct")]
        compare = [{"measure": measure, "previous": was, "current": now, "change": now - was if unit != "text" and isinstance(was, (int, float)) and isinstance(now, (int, float)) else None,
                    "unit": unit} for measure, was, now, unit in measures if last and (was is not None or now is not None)] if last else []
        closest = None
        work = self.factors.directory(run_id) / "output" / "work.json"
        if work.is_file():
            similar = json.loads(work.read_text(encoding="utf-8")).get("similar") or []
            found = next((item for item in similar if not item.get("style") and item.get("name") != name), None)
            if found is not None:
                items = self.store.find_item(found["name"])
                closest = {"name": found["name"], "version": found.get("version") or "", "rho": found["rho"], "itemId": items[0]["id"] if items else None}
        files = sorted(path.name for path in Path(filesystem_path(source["code"]["snapshotPath"])).glob("*.py")) if source.get("mode") == "code" and source.get("code") else []
        return {"runId": run_id, "name": name, "version": (last["version"] + 1) if last else 1, "runNumber": source.get("number"),
                "formula": source.get("formula") if source.get("mode") == "formula" else None, "files": files,
                "checks": [{"name": item.get("name"), "value": item.get("value"), "need": item.get("need"), "state": item["state"]} for item in source.get("checks") or [] if item.get("state") == "watch"],
                "previous": {"version": last["version"], "publishedAt": last["createdAt"]} if last else None, "compare": compare,
                "users": [{"name": user["name"], "kind": user["kind"]} for user in self.library.users(last["versionId"])] if last else [], "closest": closest, "blocked": blocked}

    async def start(self, run_id, args):
        # An allocation's backtest becomes a research run of the standard flow first (the frozen code, values, bars and lock the checks below read).
        source = await self.allocations.freeze(run_id) if self.allocations is not None and self.allocations.owns(run_id) else self.runs.read(run_id)
        if self.runs.closed:
            raise ContractError("unavailable", "Quant is closing.")
        if run_id in self.active:
            raise ContractError("conflict", f"Already publishing #{source.get('number')}", {"publicationId": self.active[run_id]})
        name = args["name"].strip()
        if not name or len(name) > 120:
            raise ContractError("invalid_input", "Choose a name of 1 to 120 characters.")
        factor = source.get("objectKind") == "factor"
        if factor:
            if source["runKind"] != "run" or source["state"] != "completed":
                raise ContractError("conflict", "Publish a completed factor run; a sweep or a held-out validation cannot be published.")
            items, result, chosen = [name], None, None
        else:
            result, chosen = self.check_source(source, args)
            items = [item["name"] for item in chosen]
        existing = self.store.find_item(name)
        kind = "factor" if factor else "model" if any(item["kind"] in ("prediction", "model") for item in chosen) else chosen[0]["kind"]
        if existing and existing[0]["kind"] != kind:
            raise ContractError("conflict", f"{name} is already a {existing[0]['kind']} in the library; choose another name.", {"kind": existing[0]["kind"]})
        identity = "run-" + uuid4().hex
        now = int(time.time() * 1000)
        record = self.records.put(RUN, {"id": identity, "objectId": source["objectId"], "objectKind": source["objectKind"], "name": source["name"], "runKind": "publish", "mode": "publish",
            "workspaceId": self.runs.store.workspace(), "state": "preparing", "stage": "values" if factor else "history", "createdAt": now, "updatedAt": now,
            "params": source.get("params") or {}, "overrides": {}, "settings": source["settings"], "message": None, "error": None,
            "code": source.get("code"), "common": source.get("common"), "data": None, "environment": source.get("environment"), "recipe": source.get("recipe"), "metrics": {},
            "starred": False, "note": "", "exitCode": None, "endedAt": None, "number": None,
            "progress": {"stage": "values" if factor else "history", "step": 0, "of": None, "startedAt": now},
            "publish": {"sourceRunId": run_id, "name": name, "items": items, "checks": args.get("checks", len(CHECK_POINTS)), "auto": args.get("auto", True), "versions": []}})
        self.ctx.publish("object_run_changed", {"runId": identity, "objectId": record["objectId"], "workspaceId": record["workspaceId"], "state": "preparing"})
        work = self.execute_factor(identity, source) if factor else self.execute(identity, source, result, chosen)
        self.active[run_id] = identity
        self.runs.tasks[identity] = self.ctx.start(work, run_record=record)
        return record

    def progress(self, identity, stage, step=None, of=None):
        record = self.runs.read(identity)
        progress = {**(record.get("progress") or {}), "stage": stage, "step": step, "of": of}
        self.runs.update(identity, stage=stage, progress=progress, updatedAt=int(time.time() * 1000))

    async def finish(self, identity, ending, versions):
        """Close the publication's record; anything copied for a version that was not committed goes, only the logs stay."""
        directory = self.runs.directory(identity)
        lease = self.runs.processes.pop(identity, None)
        if lease:
            with contextlib.suppress(ContractError):
                await asyncio.shield(self.ctx.processes.cancel(lease))
            with contextlib.suppress(ContractError):
                await self.ctx.processes.close(lease)
        self.runs.tasks.pop(identity, None)
        record = self.runs.read(identity)
        self.active.pop(record["publish"]["sourceRunId"], None)
        if ending["state"] != "completed":
            for item in record["publish"]["versions"]:
                shutil.rmtree(filesystem_path(self.library.version_directory(item)), ignore_errors=True)
            for path in ("source", "common", "support", "engine"):
                shutil.rmtree(filesystem_path(directory / path), ignore_errors=True)
            for path in (directory / "output").glob("values*"):
                path.unlink(missing_ok=True)
        ended = int(time.time() * 1000)
        self.runs.update(identity, **ending, publish={**record["publish"], "versions": [item["versionId"] for item in versions]} if versions else record["publish"],
                         endedAt=ended, updatedAt=ended)
        if versions:
            self.library.publish_change(versions[0]["itemId"], versions[0]["versionId"])
        if self.notifications is not None:
            name = record["publish"]["name"]
            text = f"{name} v{versions[0]['version']} is in the library." if versions else f"Publishing {name} {ending['state']}: {ending.get('message') or ''}"
            await self.notifications.long_run(identity, "Quant · Publication " + ("finished" if versions else ending["state"]), text, (ended - record["createdAt"]) / 1000)

    @staticmethod
    def ending_of(error, closed, stage):
        if isinstance(error, asyncio.CancelledError) or isinstance(error, ContractError) and error.code == "cancelled":
            return {"state": "interrupted" if closed else "cancelled", "message": "Stopped before anything was added to the library."}
        return {"state": "failed", "message": error.message, "error": {"stage": stage, **error.to_dict()}}

    # --- a factor run -----------------------------------------------------------------------------------------------------
    async def execute_factor(self, identity, source):
        directory = self.runs.directory(identity)
        ending, versions = {}, []
        try:
            directory.mkdir(parents=True, exist_ok=True)
            self.runs.update(identity, state="running")
            ready, values, answer = await self.factor_values(identity, source, directory)
            self.progress(identity, "commit")
            versions = await self.commit_factor(identity, source, ready, values, answer)
            ending = {"state": "completed", "message": None}
        except asyncio.CancelledError as error:
            ending = self.ending_of(error, self.runs.closed, None)
        except ContractError as error:
            ending = self.ending_of(error, self.runs.closed, self.runs.read(identity)["stage"])
        finally:
            await self.finish(identity, ending or {"state": "failed", "message": "The publication stopped."}, versions)

    async def factor_values(self, identity, source, directory, *, checks=None, report=None, ready=None, held_out=None):
        """The factor's values over its whole data period, and the same values with the data cut at three dates before the held-out start.
        A publication of its own reads how many cuts to make from its record; a model publishing the factor with it says (`checks`) and hears
        the progress (`report(stage, done, total)`), and can give the data (`ready`) the model itself was trained on and its own held-out start
        (`held_out`, UTC milliseconds): the cuts then stay before the earlier of the two held-out starts."""
        data = self.factors.data
        report = report or (lambda stage, done, total: self.progress(identity, stage, done, total))
        key = (source.get("data") or {}).get("key")
        ready = ready or (data.ready(key) if key else None)
        if ready is None:
            # The run's frozen data was cleared (it is kept a few days): the same settings are frozen again.
            ready = await data.prepare(source["settings"]["data"])
        labels = await asyncio.to_thread(lambda: np.sort(pd.read_parquet(ready["barsPath"], columns=["label"])["label"].unique()))
        held = min((start for start in ((source.get("data") or {}).get("heldOutFrom"), held_out) if start), default=None)
        inside = labels[labels < held] if held else labels
        count = max(1, min(int(checks if checks is not None else self.runs.read(identity)["publish"]["checks"]), len(CHECK_POINTS)))
        if len(inside) <= 20:
            raise ContractError("invalid_input", "There are too few bars before the held-out period to check this publication against future data.")
        cutoffs = [int(inside[int(len(inside) * point)]) for point in CHECK_POINTS[-count:]]
        output = directory / "output"
        output.mkdir(parents=True, exist_ok=True)
        job = {"output": str(output), "kind": "values", "settings": source["settings"]["run"], "cutoffs": cutoffs, "from": None,
               "data": {"key": ready["key"], "barsPath": ready["barsPath"], "info": ready["info"], "extrasPath": ready["extrasPath"], "libraryPath": None}}
        if source["mode"] == "formula":
            job["formula"] = source["formula"]
        else:
            job["code"] = {"source": source["code"]["snapshotPath"], "entry": source["code"]["entry"],
                           "common": source["common"]["snapshotPath"] if source.get("common") else None, "params": source["settings"]["params"]}
        report("future", 0, len(cutoffs))
        stop = asyncio.Event()

        async def follow():
            seen = 0
            path = output / "progress.jsonl"
            while not stop.is_set():
                with contextlib.suppress(TimeoutError):
                    await asyncio.wait_for(stop.wait(), .5)
                if path.is_file():
                    lines = path.read_text(encoding="utf-8").splitlines()
                    for line in lines[seen:]:
                        item = json.loads(line)
                        if item["stage"] == "future" and "done" in item:
                            report("future", item["done"], item["total"])
                    seen = len(lines)

        follower = asyncio.create_task(follow())
        try:
            answer = await self.factors.engine.run_open(job)
        finally:
            stop.set()
            await follower
        if not answer.get("ok"):
            failure = answer.get("failure") or {"message": "The factor engine gave no answer."}
            raise ContractError("code_failed", "The factor failed when it was computed for publication: " + failure["message"], {"failure": failure})
        # Not "future": the engine reads a message with that key as a later check of another run.
        found = answer.get("futureFound")
        if found:
            timeframe = ready["info"]["timeframe"]
            changed = found["changed"]
            raise ContractError("future_data", f"{found['instrument']}'s value on {human(found['datetime'], timeframe)} changes when data after {human(found['cutoff'], timeframe)} is removed "
                                f"({changed} value{'s change' if changed != 1 else ' changes'}). The formula or code reads later data.",
                                {"instrument": found["instrument"], "valueTime": found["datetime"], "cutoff": found["cutoff"], "withAll": found["withAll"],
                                 "whenCut": found["whenCut"], "changed": found["changed"]})
        report("future", len(cutoffs), len(cutoffs))
        values = await asyncio.to_thread(pd.read_parquet, output / "values.parquet")
        return ready, values, answer

    async def commit_factor(self, identity, source, ready, values, answer):
        publish = self.runs.read(identity)["publish"]
        spec, version_id, directory = await self.factor_spec(source, ready, values, answer, name=publish["name"], auto=publish.get("auto", True),
                                                             on_version=lambda made: self.runs.update(identity, publish={**publish, "versions": [made]}))
        try:
            created = await asyncio.to_thread(self.store.commit_versions, [spec])
        except BaseException:
            shutil.rmtree(filesystem_path(directory), ignore_errors=True)
            raise
        self.factors.update(source["id"], version={"versionId": version_id, "version": created[0]["version"], "itemId": created[0]["itemId"], "name": publish["name"]})
        return created

    async def factor_spec(self, source, ready, values, answer, *, name, auto, on_version=None):
        """The library version of a factor run as the store takes it (its rows, its meta, its frozen files), not yet committed: a factor published
        by itself commits it alone, a model publishing it with the model commits it in the model's transaction. Returns (spec, version id, folder)."""
        info = ready["info"]
        publish = {"name": name, "auto": auto}
        run = source["settings"]["run"]
        numbers = source.get("numbers") or {}
        held_out = held_out_ls = None
        if source.get("heldOut"):
            with contextlib.suppress(ContractError):
                validation = self.factors.read(source["heldOut"]["runId"]).get("numbers") or {}
                held_out, held_out_ls = validation.get("rankIc"), validation.get("longShortNet")
        labels = sorted(int(label) for label in values["datetime"].unique())
        zone, found = await research_calendar.date_boundaries(self.ctx, info["calendarId"], info["timeframe"], info["sessionScope"], info["since"], info["until"] + 7 * 86_400_000)
        availability = edge_times(labels, info["timeframe"], zone, found, "close")
        ids = {item["column"]: item["instrumentId"] for item in info["instruments"]}
        recorded = int(time.time() * 1000)
        rows = await asyncio.to_thread(long_table, values.assign(field=FIELD), ids, availability, recorded, "history", FIELD)
        extras = json.loads(Path(ready["extrasPath"]).read_text(encoding="utf-8")) if ready.get("extrasPath") else {}
        industry = extras.get("industry") or {}
        version_id = "ver-" + uuid4().hex[:12]
        if on_version is not None:
            on_version(version_id)
        directory = self.library.version_directory(version_id)
        await asyncio.to_thread(self.freeze_factor, source, directory)
        holding, delay = int(run.get("holding", 1)), int(run.get("delay", 1))
        meta = {"producer": "factor", "fields": [FIELD], "timeframe": info["timeframe"], "info": info, "edge": "close",
                "publication": {"kind": "factor", "name": publish["name"], "field": FIELD}, "data": source["settings"]["data"], "settings": run,
                "params": source["settings"].get("params") or {}, "formula": source.get("formula") if source["mode"] == "formula" else None,
                "code": {"entry": source["code"]["entry"], "digest": source["code"]["digest"]} if source["mode"] == "code" else None,
                "common": {"digest": source["common"]["digest"]} if source.get("common") else None,
                "origin": {"objectId": source["objectId"], "runId": source["id"], "objectName": source["name"], "objectKind": "factor", "runNumber": source.get("number")},
                "measure": measure_text(holding, info["timeframe"]), "horizon": {"holding": holding, "delay": delay}, "universe": universe_text(source["settings"]["data"]),
                "settingsText": settings_text(source["settings"]["data"], run, info["timeframe"]), "publishedThrough": labels[-1] if labels else None,
                "instrumentCount": len(ids), "sectors": {ids[column]: sector for column, sector in industry.items() if column in ids},
                "environment": None, "checked": answer.get("checked")}
        # The run's own numbers, by the definitions the library follows it on afterwards (the run's horizon; the long-short after costs, as after publication)
        published = {"metric": "rank_ic", "value": numbers.get("rankIc"), "t": numbers.get("t"), "se": numbers["rankIc"] / numbers["t"] if numbers.get("rankIc") is not None and numbers.get("t") else None,
                     "ls": numbers.get("longShortNet"), "turnover": numbers.get("turnover"), "names": numbers.get("names"), "heldOut": held_out, "heldOutLs": held_out_ls,
                     "holding": holding, "delay": delay}
        spec = {"name": publish["name"], "kind": "factor", "meta": meta, "rows": rows, "objectId": source["objectId"], "runId": source["id"],
                "workspaceId": self.runs.store.workspace(), "state": "updating", "auto": publish.get("auto", True), "cursor": labels[-1] if labels else None,
                "checks": published, "versionId": version_id}
        return spec, version_id, directory

    def freeze_factor(self, source, directory):
        directory.mkdir(parents=True)
        if source["mode"] == "code":
            shutil.copytree(filesystem_path(source["code"]["snapshotPath"]), filesystem_path(directory / "source"))
            if source.get("common"):
                shutil.copytree(filesystem_path(source["common"]["snapshotPath"]), filesystem_path(directory / "common"))

    # --- a model or allocation run ------------------------------------------------------------------------------------------------
    async def execute(self, identity, source, result, chosen):
        directory = self.runs.directory(identity)
        ending, versions = {}, []
        try:
            directory.mkdir(parents=True)
            self.runs.update(identity, state="running")
            prepared = await self.history(identity, source, result, chosen)
            await self.check(identity, source, prepared, directory)
            self.progress(identity, "commit")
            versions = await self.commit(identity, source, result, prepared)
            ending = {"state": "completed", "message": None, "metrics": prepared["metrics"]}
        except asyncio.CancelledError as error:
            ending = self.ending_of(error, self.runs.closed, None)
        except ContractError as error:
            ending = self.ending_of(error, self.runs.closed, self.runs.read(identity)["stage"])
        finally:
            await self.finish(identity, ending or {"state": "failed", "message": "The publication stopped."}, versions)

    async def history(self, identity, source, result, chosen):
        """The values the run already computed, with when each could be known; and the run's frozen bars, to check them and to judge them."""
        run_directory = self.runs.directory(source["id"])
        invocation = json.loads((run_directory / "invocation.json").read_text(encoding="utf-8"))
        research = invocation["research"]
        if research is None:
            raise ContractError("invalid_input", "This run's code picked its own symbols, so there are no frozen bars to check and update it against. Choose the symbols in the data settings to publish.")
        info = research["info"]
        instruments = {item["column"]: item["instrumentId"] for item in info["instruments"]}
        zone, found = await research_calendar.date_boundaries(self.ctx, info["calendarId"], info["timeframe"], info["sessionScope"], info["since"], info["until"])
        recorded = int(time.time() * 1000)
        publications = []
        for item in chosen:
            entry = {**item, "rows": None, "values": None}
            if item["kind"] in VALUE_KINDS:
                values = await asyncio.to_thread(pd.read_parquet, run_directory / "output" / item["path"])
                availability = edge_times(sorted(set(values["datetime"])), info["timeframe"], zone, found, EDGE[item["kind"]])
                entry.update(values=values, rows=await asyncio.to_thread(long_table, values, instruments, availability, recorded, "history"))
            publications.append(entry)
        bars = await asyncio.to_thread(pd.read_parquet, research["barsPath"])
        return {"publications": publications, "info": info, "invocation": invocation, "bars": bars, "zone": zone, "found": found, "instruments": instruments, "metrics": {}}

    async def check(self, identity, source, prepared, directory):
        """Cut the data at a few points and run the frozen code again: every value up to each cut must come out the same."""
        valued = [item for item in prepared["publications"] if item["values"] is not None]
        if not valued:
            return
        info, bars = prepared["info"], prepared["bars"]
        labels = sorted(set(bars["label"]))
        held = (source["settings"].get("data") or {}).get("heldOutFrom")
        if held:
            from .object_data import day_ms
            labels = [label for label in labels if label < day_ms(held, False)]
        count = max(1, min(int(self.runs.read(identity)["publish"]["checks"]), len(CHECK_POINTS)))
        cutoffs = [int(labels[int(len(labels) * point)]) for point in CHECK_POINTS[-count:]] if len(labels) > 20 else []
        if not cutoffs:
            raise ContractError("invalid_input", "There are too few bars to check this publication against future data.")
        self.progress(identity, "future", 0, len(cutoffs))
        source_directory = self.runs.directory(source["id"])
        shutil.copytree(filesystem_path(source_directory / "source"), filesystem_path(directory / "source"))
        if (source_directory / "common").is_dir():
            shutil.copytree(filesystem_path(source_directory / "common"), filesystem_path(directory / "common"))
        (directory / "output").mkdir()
        support = research_process.write_support(directory)
        invocation = {**prepared["invocation"], "mode": "values", "cutoffs": cutoffs, "output": str(directory / "output"), "source": str(directory / "source"),
                      "common": str(directory / "common") if (directory / "common").is_dir() else None, "support": str(support), "runId": identity}
        research_process.write_invocation(directory, invocation)
        state = await self.library.environment_state(source["environment"])
        lease = await research_process.start(self.ctx, key=identity, directory=directory, invocation=invocation, state=state,
                                             context={"objectId": source["objectId"], "runId": identity})
        self.runs.processes[identity] = lease
        output = directory / "output"
        stop = asyncio.Event()

        async def follow():
            while not stop.is_set():
                with contextlib.suppress(TimeoutError):
                    await asyncio.wait_for(stop.wait(), .5)
                done = sum(1 for number in range(len(cutoffs)) if (output / f"values-{number}.json").is_file())
                self.progress(identity, "future", done, len(cutoffs))

        follower = asyncio.create_task(follow())
        try:
            code, calls = await research_process.wait(self.ctx, lease)
        finally:
            stop.set()
            await follower
        self.runs.update(identity, exitCode=code, data={"calls": calls})
        if (output / "error.json").is_file():
            failure = json.loads((output / "error.json").read_text(encoding="utf-8"))
            raise ContractError("code_failed", "The code failed when it ran on the data cut for the check: " + failure["message"], {"failure": failure})
        if code != 0 or not (output / "done.json").is_file():
            raise ContractError("code_failed", f"The check run ended with exit code {code} before it finished.")
        for number, cutoff in enumerate(cutoffs):
            report = json.loads((output / f"values-{number}.json").read_text(encoding="utf-8"))
            for item in valued:
                made = next((entry for entry in report["publications"] if entry["name"] == item["name"]), None)
                if made is None:
                    raise ContractError("invalid_input", f"When the data was cut at {stamp(cutoff, info['timeframe'])} the code made no {item['name']}.")
                cut = await asyncio.to_thread(pd.read_parquet, output / made["path"])
                difference = await asyncio.to_thread(first_difference, item["values"], cut, cutoff)
                if difference:
                    row = difference["row"]
                    then = None if pd.isna(row["valueCut"]) else float(row["valueCut"])
                    raise ContractError("future_data", f"{item['name']} uses data from after the time it is dated: its value for {row['instrument']} at {stamp(int(row['datetime']), info['timeframe'])} "
                        f"is {float(row['value']):.6g} with all the data, but {'missing' if then is None else format(then, '.6g')} when the data is cut at {stamp(cutoff, info['timeframe'])} "
                        f"({difference['count']} value(s) change in this check).",
                        {"name": item["name"], "instrument": row["instrument"], "valueTime": int(row["datetime"]), "cutoff": cutoff, "withAll": float(row["value"]), "whenCut": then,
                         "changed": difference["count"]})

    def published_figures(self, kind, source, frame, prices, info):
        """The figures it is published with. An allocation: the run's own numbers and its newest period's risk shares (`publishedExtra`), what it is followed on
        afterwards. A signal: `library_after.published` over the history it holds, with the horizon it is followed on (the next bar to the one after)."""
        if kind == "allocation":
            return {"metric": "volatility", **source["publishedExtra"]}
        return library_after.published(frame[["datetime", "instrument", "value"]], prices, holding=1, delay=1, annualization=info["annualization"])

    async def commit(self, identity, source, result, prepared):
        """Freeze what the version needs beside it, then add every item in one transaction; a model's predictions and trained model are one version."""
        info = prepared["info"]
        publish = self.runs.read(identity)["publish"]
        prices = library_checks.close_prices(prepared["bars"], prepared["instruments"])
        valued = [item for item in prepared["publications"] if item["kind"] in VALUE_KINDS]
        trained = next((item for item in prepared["publications"] if item["kind"] == "model"), None)
        if not valued and trained is None:
            raise ContractError("invalid_input", "Nothing to publish.")
        item = valued[0] if valued else trained
        kind = "model" if item["kind"] in ("prediction", "model") else item["kind"]
        version_id = "ver-" + uuid4().hex[:12]
        self.runs.update(identity, publish={**publish, "versions": [version_id]})
        directory = self.library.version_directory(version_id)
        try:
            await asyncio.to_thread(self.freeze, source, result, directory)
            frame = item["rows"] if item.get("rows") is not None else None
            published = await asyncio.to_thread(self.published_figures, kind, source, frame, prices, info) if frame is not None and len(frame) else {}
            factors = prepared["invocation"]["research"].get("factors")
            inputs = []
            for entry in (factors or {}).get("items", []):
                found = self.store.version(entry["versionId"])
                inputs.append({"itemId": found["item_id"], "versionId": entry["versionId"], "name": entry["name"], "version": entry["version"], "parameter": entry["parameter"]})
            labels = sorted(set(frame["datetime"])) if frame is not None and len(frame) else []
            meta = {"producer": "run", "fields": [item["field"]] if frame is not None and len(frame) else [], "timeframe": info["timeframe"], "info": info,
                    "edge": EDGE.get(item["kind"], "close"),
                    "publication": {key: value for key, value in item.items() if key not in {"rows", "values"}} | {"kind": "allocation" if kind == "allocation" else item["kind"]},
                    "data": source["settings"]["data"], "params": source["params"], "code": {"entry": source["code"]["entry"], "digest": source["code"]["digest"]},
                    "common": {"digest": source["common"]["digest"]} if source["common"] else None, "environment": source["environment"],
                    "report": {"metrics": source["metrics"]}, "origin": {"objectId": source["objectId"], "runId": source["id"], "objectName": source["name"], "objectKind": source["objectKind"], "runNumber": source.get("number")},
                    "model": trained["model"] if trained is not None else None, "lock": {"digest": source["recipe"]["lockDigest"], "pythonVersion": source["recipe"]["pythonVersion"]},
                    "measure": "Volatility" if kind == "allocation" else "Rank IC 1D (next bar to the one after)", "horizon": {"holding": 1, "delay": 1},
                    "universe": universe_text(source["settings"]["data"]), "settingsText": universe_text(source["settings"]["data"]),
                    "publishedThrough": int(labels[-1]) if labels else None, "instrumentCount": len(prepared["instruments"]), "inputs": inputs}
            if factors:
                meta["factors"] = factors["items"]
            meta.update(source.get("libraryMeta") or {})  # an allocation: what its own producer and the monthly review read
            spec = {"name": publish["name"], "kind": kind, "meta": meta, "rows": frame if frame is not None else [], "objectId": source["objectId"], "runId": source["id"],
                    "workspaceId": self.runs.store.workspace(), "state": "updating" if frame is not None and len(frame) else "fixed", "auto": publish.get("auto", True),
                    "cursor": int(labels[-1]) if labels else None, "checks": published, "versionId": version_id,
                    "holds": [{"versionId": entry["versionId"]} for entry in inputs]}
            created = await asyncio.to_thread(self.store.commit_versions, [spec])
        except BaseException:
            shutil.rmtree(filesystem_path(directory), ignore_errors=True)
            raise
        self.ctx.environments.hold(source["environment"]["preparationId"], "library:" + version_id)
        return created

    def freeze(self, source, result, directory):
        """The code, the common code and the report as they were in the run; later edits and runs cannot change them."""
        run_directory = self.runs.directory(source["id"])
        directory.mkdir(parents=True)
        shutil.copytree(filesystem_path(run_directory / "source"), filesystem_path(directory / "source"))
        if (run_directory / "common").is_dir():
            shutil.copytree(filesystem_path(run_directory / "common"), filesystem_path(directory / "common"))
        shutil.copyfile(run_directory / "execution.lock", directory / "execution.lock")
        shutil.copytree(filesystem_path(run_directory / "output"), filesystem_path(directory / "report"),
                        ignore=shutil.ignore_patterns("channel", "qlib-*", "publication", "*.log"))


def _value(value):
    return "missing" if value is None else format(value, ".6g")
