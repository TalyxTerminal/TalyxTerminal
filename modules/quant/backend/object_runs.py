"""Runs of one Quant object: fix the code, data and environment, run the process, keep what happened."""
from __future__ import annotations

import asyncio
import contextlib
import copy
import json
import logging
import os
import shutil
import tempfile
import time
from datetime import UTC, date, datetime
from pathlib import Path
from urllib.parse import unquote
from uuid import uuid4

import pandas as pd
from talyx_sdk import ContractError

from . import (
    index_universe,
    library_inputs,
    model_inputs,
    object_data,
    research_data,
    research_process,
    run_flow,
    strategy_inputs,
    strategy_settings,
    training_factors,
)
from .execution_recipe import recipe_lock, write_recipe
from .materials import plain
from .model_sessions import Router as ModelRouter
from .native_environment import prepare_lock, project_lock
from .object_history import data_label, describe_failure, metrics_of
from .object_params import check_values
from .project_paths import EXCLUDED, MODULE, filesystem_path, source_identity
from .project_snapshots import snapshot_project
from .work_records import digest

GB = 1024 ** 3
RUN = "qrun"
TERMINAL = {"completed", "failed", "cancelled", "interrupted"}
# A stopping backtest has this long to write its part before its process is ended; a closing application gives it less.
STOP_SECONDS, CLOSE_SECONDS = 120, 10
RESEARCH_KINDS = ("factor", "model", "allocation")
RUNNABLE = ("strategy", *RESEARCH_KINDS)
STRATEGY_LOCK = MODULE / "runtime" / "strategy.lock"
RESEARCH_LOCK = MODULE / "runtime" / "research.lock"


@contextlib.contextmanager
def new_run_directory(directory):
    """Own the new directory until its run record is written."""
    with contextlib.ExitStack() as cleanup:
        directory.mkdir(parents=True)
        cleanup.callback(shutil.rmtree, filesystem_path(directory))
        yield directory
        cleanup.pop_all()


async def finish_copy(copy_files):
    """A thread copying into this directory must finish before cancellation removes it."""
    task = asyncio.create_task(copy_files)
    try:
        return await asyncio.shield(task)
    except asyncio.CancelledError:
        await asyncio.gather(task, return_exceptions=True)
        raise


def project_of(obj, packages):
    """The environment a kind of object runs in, with the lock new work uses (the bundled one, or it with the user's packages): strategies in the strategy environment, research in the one research environment."""
    if obj["kind"] == "strategy":
        return {"workbench": "strategy", "engine": "strategy"}, Path(packages.effective("strategy")["lockPath"])
    return {"workbench": obj["kind"], "engine": "research"}, Path(packages.effective("research")["lockPath"])




class ObjectRuns:
    def __init__(self, ctx, records, store, common, lists, packages, library):
        self.ctx, self.records, self.store, self.common, self.lists, self.packages, self.library = ctx, records, store, common, lists, packages, library
        self.tasks, self.processes = {}, {}
        self.stopping = set()  # the waits that end a stopping backtest's process if it has not written its part within STOP_SECONDS
        self.models = ModelRouter(ctx)
        self.model_pumps = {}
        self.fetching = {}  # run -> how far fetching its data is, until the engine writes output/progress.json
        self.queue = None  # the run queue (set by the services that share it): the machine's budget a strategy's memory estimate is held to
        self.closed = False

    def directory(self, identity):
        return self.records.root / "qruns" / identity

    def read(self, identity):
        record = self.records.find(RUN, identity)
        if record is None:
            raise ContractError("not_found", "This run does not exist.")
        return record

    @staticmethod
    def asset_labels(record):
        """What a report's asset column shows for each name in it: a backtest names its native instruments, research its columns of bars."""
        data = record["data"]
        if record["objectKind"] == "strategy":
            return {item["nativeInstrument"]: item["instrument"]["displaySymbol"] for item in object_data.frozen_lists(data)["sources"]}
        return {item["column"]: item["displaySymbol"] for item in data["instruments"]} if "instruments" in data else {}

    def result(self, record):
        index = self.directory(record["id"]) / "output" / "result-index.json"
        return json.loads(index.read_text(encoding="utf-8")) if index.is_file() else None

    def save(self, record, **changes):
        previous = record["state"]
        updated = self.records.put(RUN, {**record, **changes})
        if updated["state"] != previous or "environment" in changes or "stage" in changes:
            self.ctx.publish("object_run_changed", {"runId": updated["id"], "objectId": updated["objectId"],
                                                           "workspaceId": updated["workspaceId"], "state": updated["state"]})
        return updated

    def update(self, identity, **changes):
        return self.save(self.read(identity), **changes)

    async def start(self, object_id, args=None):
        if self.closed:
            raise ContractError("unavailable", "Quant is closing.")
        args = args or {}
        obj = self.store.read(object_id)
        if obj["kind"] not in RUNNABLE:
            raise ContractError("unavailable", f"A {obj['kind']} cannot be run yet.")
        if args.get("quick") and obj["kind"] != "factor":
            raise ContractError("invalid_input", "Only a factor has a quick check.")
        held_out = self.held_out_of(obj, args.get("of")) if args.get("kind") == "heldout" else None
        if args.get("kind") not in (None, "run", "heldout"):
            raise ContractError("invalid_input", "A strategy run is a backtest or a held-out validation.")
        source_run = held_out or (self.read(args["of"]) if args.get("of") else None)
        if source_run:
            if source_run["objectId"] != object_id or source_run["state"] != "completed" or source_run["runKind"] not in ("run", "case", "heldout"):
                raise ContractError("invalid_input", "Select a completed run of this strategy.")
            obj = self.source_object(obj, source_run)
            saved = (Path(obj["directory"]) / obj["entry"]).read_text(encoding="utf-8")
            values = dict(source_run["params"])
            settings = obj["settings"]
        else:
            saved = await self.saved_source(obj)
            values = self.store.effective_parameters(obj, saved)
            settings = await self.settings_for(obj)
        if args.get("params") and not source_run:
            definitions = self.store.parameters(saved)["parameters"]
            try:
                check_values(definitions, args["params"])
            except ValueError as error:
                raise ContractError("invalid_input", str(error)) from error
            values.update(args["params"])
        held = settings.get("signalRun")
        if held and not values.get(held["param"]):
            values[held["param"]] = f"{held['model']} #{held['number']}"
        if not source_run and obj["kind"] == "strategy" and (args.get("settings") or {}).get("run"):
            # The second layer for this run only: what is given over what the object keeps.
            given = dict(strategy_settings.check(args["settings"]["run"]))
            stored = settings.get("run") or {}
            if "heldOutFrom" in given:
                settings["data"] = {**settings["data"], "heldOutFrom": given.pop("heldOutFrom")}
            settings["run"] = {**stored, **{key: ({**stored.get(key, {}), **value} if key in ("exchange", "costs") else value) for key, value in given.items()}}
        self.store.check_short(obj, values, settings["data"])
        if held_out and held and held.get("lastValueAt") is not None and settings["data"].get("heldOutFrom") \
                and held["lastValueAt"] < object_data.day_ms(settings["data"]["heldOutFrom"], False):
            # The run the signal comes from held the same stretch out, so it has no values there to trade.
            raise ContractError("invalid_input", f"{held['model']} #{held['number']} held out the same stretch, so it has no values to trade in it. "
                                "Publish the factor and choose its library version as the signal to validate on the held-out stretch.", {"parameter": held["param"]})
        identity = "run-" + uuid4().hex
        with new_run_directory(self.directory(identity)) as directory:
            code, common = await finish_copy(self.fix_project(obj, directory, source_run))
            lineage = {"tags": list(args.get("tags") or [])}
            if source_run:
                lineage["of"] = source_run["id"]
            if held_out:
                lineage.update(heldOut=True, tags=["Held-out"])
            signals = {}
            override = source_run.get("signalOverride") if source_run else args.get("signalOverride")
            signal = obj["settings"].get("signalRun")
            if override or signal:
                key = "signalOverride" if override else "signalRun"
                kept = directory / ("signal-from-notebook.parquet" if override else "signal-from-model-run.parquet")
                material = override or signal
                try:
                    await finish_copy(asyncio.to_thread(shutil.copyfile, material["path"], kept))
                except FileNotFoundError as error:
                    raise ContractError("unavailable", "The signal values of this run are no longer available.") from error
                signals[key] = {**material, "path": str(kept)}
            record = self.records.put(RUN, {"id": identity, "objectId": obj["id"], "objectKind": obj["kind"], "name": obj["name"], "runKind": "heldout" if held_out else "run", "number": self.next_number(obj["id"]) if obj["kind"] == "strategy" else None,
                **lineage, **signals,
                "mode": "quick" if args.get("quick") else "run", "workspaceId": self.store.workspace(), "state": "preparing", "stage": "check" if obj["kind"] == "strategy" else "code", "createdAt": int(time.time() * 1000),
                "params": values, "overrides": args.get("params", {}), "settings": settings, "message": None, "error": None,
                "code": code, "common": common, "data": None, "environment": None, "recipe": None, "metrics": {}, "starred": False, "note": "", "exitCode": None, "endedAt": None})
        self.ctx.publish("object_run_changed", {"runId": identity, "objectId": obj["id"],
                                                       "workspaceId": record["workspaceId"], "state": "preparing"})
        # The run exists from the click (its answer is this record); a strategy's checks are its first step (design.md, run flow).
        obj = {**obj, "directory": str(directory / "source"), "settings": settings}
        check = (lambda: self.preflight(obj, saved, values, settings)) if obj["kind"] == "strategy" and (not source_run or held_out) else None
        self.tasks[identity] = self.ctx.start(self.execute(identity, obj, check), run_record=record)
        return record

    def next_number(self, object_id):
        return run_flow.next_number(self.records, object_id)

    def project_texts(self, obj):
        """The project's Python files as saved: {relative path: text}."""
        root = Path(obj["directory"])
        return {path.relative_to(root).as_posix(): path.read_text(encoding="utf-8") for path in sorted(root.rglob("*.py"))
                if not any(part in EXCLUDED for part in path.relative_to(root).parts)}

    async def preflight(self, obj, source, values, settings):
        """A strategy run's first step, before anything is fetched: what ends it at once with the reason: a library item to choose, an input that was
        retired, a model the backtest start would overlap, a route Market has not verified, a series Market lacks, accounts that cannot be
        added up, and a run the machine cannot hold."""
        data = settings["data"]
        files = self.project_texts(obj)
        held = obj["settings"].get("signalRun")
        if held and not Path(held["path"]).is_file():
            raise ContractError("not_found", f"{held['model']} #{held['number']}'s predictions are gone; use the run again, or choose a library signal.", {"parameter": held["param"]})
        definitions = self.store.parameters(source)["parameters"]
        for item in definitions:
            if item["type"] == "library" and not values.get(item["name"]) and item["name"] != (obj["settings"].get("signalRun") or {}).get("param"):
                raise ContractError("invalid_input", "Choose the signal this strategy ranks by (a library item)." if item["name"] == "signal"
                                    else f"Choose the library item for {item['label'] or item['name']}.", {"parameter": item["name"]})
        pins = obj["settings"].get("inputs") or {}
        if isinstance(pins, list):
            pins = self.store.strategy_bindings(obj, obj["settings"], {})
        for group in ("library", "models"):
            for pin in (pins.get(group) or {}).values():
                try:
                    version = self.library.read_version(pin["versionId"])
                except ContractError:
                    continue
                if version.get("retired") or version.get("state") in ("retired", "delisted"):
                    raise ContractError("input_retired", f"v{version['version']} is retired; switch to a live version", {"name": pin["name"], "versionId": pin["versionId"]})
        since = object_data.day_ms(data["since"], False) if data.get("since") else None
        for name in sorted({name for text in files.values() for name in model_inputs.declarations(text)}):
            pin = (pins.get("models") or {}).get(name)
            found = await asyncio.to_thread(model_inputs.binding, self.library, name, version_id=pin["versionId"] if pin else None)
            if since is not None and since < found["trainingCutoff"]:
                trained = datetime.fromtimestamp(found["trainingCutoff"] / 1000, tz=UTC).date().isoformat()
                raise ContractError("model_training_overlap", f"Model '{name}' v{found['version']} was trained on data to {trained}. Start after that date, or read its published "
                                    "values, which cover earlier dates.", {"name": name, "versionId": found["versionId"], "trainingCutoff": found["trainingCutoff"], "requestedStart": since})
        symbols = [item["instrument"] for item in data.get("symbols", [])]
        if symbols and data.get("timeframe"):
            await object_data.check_routes(self.ctx, symbols, data["timeframe"], *([data["fillBars"]] if data.get("fillBars") else []))
        for name in sorted(strategy_inputs.series_names(files) - strategy_inputs.OPTIONAL_SERIES):
            if name in {"mark_price", "index_price"}:
                continue  # Coverage does not describe price bases; the exact Market fetch/read decides.
            for instrument in symbols:
                rows = await object_data.M.coverage(self.ctx, [instrument], name)
                if not any(row["capability"] == name for row in rows):
                    raise ContractError("series_missing", f"{instrument['displaySymbol']} has no {name} history in Market", {"series": name, "instrumentId": instrument["instrumentId"]})
        run = settings.get("run") or {}
        accounts = run.get("accounts")
        if accounts and len(accounts) > 1:
            report = run.get("reportCurrency") or accounts[0]["balances"][0].split()[1]
            for currency in sorted({amount.split()[1] for item in accounts for amount in item["balances"]} - {report}):
                pair = await object_data.resolve_text(self.ctx, f"{currency}/{report}")
                found = pair.get("base") == currency and pair.get("quote") == report
                if not found:
                    raise ContractError("no_conversion", f"No {report} price for {currency} in Market; the two accounts can't be added up", {"currency": currency, "report": report})
        need = await self.memory_need(settings)
        if need is not None and need > self.queue.budget:
            raise ContractError("over_machine_budget", f"This run needs about {need / GB:.1f} GB; this machine gives runs {self.queue.budget / GB:.1f} GB (half of {self.queue.total / GB:.1f} GB)",
                                {"needGb": round(need / GB, 1), "canGiveGb": round(self.queue.budget / GB, 1)})

    async def memory_need(self, settings):
        """Bytes one run of these settings is expected to need (names and bars from the data settings; see `strategy_settings.memory_estimate`), or None when
        there is no queue to hold it to or the settings do not say enough."""
        data = settings["data"]
        symbols = [item["instrument"] for item in data.get("symbols", [])]
        if self.queue is None or not symbols or not data.get("since"):
            return None
        resolved = await object_data.resolve_until(self.ctx, data)
        until = date.fromisoformat(resolved["until"])
        days = max(1, (until - date.fromisoformat(data["since"])).days)
        minutes = {"m": 1, "h": 60, "d": 1440}[data["timeframe"][-1]] * int(data["timeframe"][:-1])
        stock = any(item["assetClass"] in {"equity", "fund"} for item in symbols)
        per_day = (1 if minutes >= 1440 else 6.5 * 60 / minutes) if stock else 1440 / minutes
        fine = data.get("fillBars")
        fine_minutes = {"m": 1, "h": 60}[fine[-1]] * int(fine[:-1]) if fine else None
        return strategy_settings.memory_estimate(len(symbols), int(days * per_day), int(days * 1440 / fine_minutes) if fine_minutes else 0)

    @staticmethod
    def source_settings(source):
        settings = copy.deepcopy(source["settings"])
        data = source.get("data") or {}
        if settings["data"].get("until") == "latest":
            if not data.get("until") or settings["data"].get("heldOutFrom"):
                raise ContractError("unavailable", "This run did not retain the resolved end of its full data range.")
            settings["data"]["until"] = datetime.fromtimestamp(data["until"] / 1000, UTC).date().isoformat()
        if source.get("runSettings"):
            overrides = source["overrides"]
            actual = source["runSettings"]
            form = dict(settings.get("run") or {})
            for key in ("capital", "fill"):
                if "@" + key in overrides:
                    form[key] = actual[key]
            if "@costMultiple" in overrides:
                from .object_sweeps import COST_KEYS
                form["costs"] = {**form.get("costs", {}), **{k: actual["costs"][k] for k in COST_KEYS if actual["costs"].get(k) is not None}}
            settings["run"] = form
            if "@period" in overrides:
                settings["data"].update(overrides["@period"], untilLatest=False)
        if source.get("signalRun"):
            settings["signalRun"] = {**settings.get("signalRun", {}), **source["signalRun"]}
        pins = settings["inputs"] if isinstance(settings.get("inputs"), dict) else {}
        settings["inputs"] = {"library": {**pins.get("library", {}), **{row["parameter"]: {"name": row["name"], "versionId": row["versionId"]}
                                                                       for row in data.get("library", []) if row.get("versionId")}},
                              "models": {**pins.get("models", {}), **{row["name"]: {"name": row["name"], "versionId": row["versionId"]} for row in data.get("models", [])}}}
        return settings

    def source_object(self, obj, source):
        code = source["code"]
        return {**obj, "directory": code["snapshotPath"], "entry": code["entry"], "settings": self.source_settings(source)}

    async def fix_project(self, obj, directory, source=None):
        if source:
            code, common = source["code"], source["common"]
            for name, snapshot in (("source", code), ("common", common)):
                if snapshot:
                    await asyncio.to_thread(shutil.copytree, filesystem_path(snapshot["snapshotPath"]), filesystem_path(directory / name))
            return code, common
        captured = await self.capture_code(obj, directory)
        common = await self.common.capture(self.records.root / "capture", directory / "common")
        return ({"entry": obj["entry"], "digest": captured["digest"], "files": captured["files"], "snapshotPath": captured["snapshotPath"], "directory": obj["directory"]},
                {key: common[key] for key in ("digest", "files", "snapshotPath")} if common else None)

    def held_out_of(self, obj, of):
        """The run a held-out validation scores: a completed backtest of this strategy, validated once; the object must keep a held-out end."""
        if obj["kind"] != "strategy":
            raise ContractError("invalid_input", "Only a strategy run is validated on the held-out end here.")
        if not of:
            raise ContractError("invalid_input", "Say which run to validate (of).")
        run = self.read(of)
        if run["objectId"] != obj["id"] or run["runKind"] not in ("run", "case") or run["state"] != "completed":
            raise ContractError("invalid_input", "Validate a completed backtest of this strategy.")
        if not run["settings"]["data"].get("heldOutFrom"):
            raise ContractError("no_held_out_period", "This run has no held-out period.")
        return run

    async def settings_for(self, obj):
        """The object's settings as a run keeps them: a research list's members as they are now (the list may change afterwards), a historical index's name."""
        settings = copy.deepcopy(obj["settings"])
        data = settings["data"]
        if data.get("list"):
            settings["data"] = {**data, "listName": self.lists.read(data["list"])["name"], "symbols": self.lists.members(data["list"])}
        if data.get("index"):
            selection = data["index"]
            instrument = selection["instrument"]
            settings["data"] = {**data, "indexName": instrument.get("name") or instrument["displaySymbol"]}
        return settings

    async def saved_source(self, obj):
        """The code a run uses is what is saved: every open Developer document of the project is saved first, so Run means what the editors show (a
        file that changed on disk under unsaved edits stops the run and is named). A ready-made strategy's code is the module's."""
        if obj["preset"]:
            return self.store.code(obj)
        path = self.store.source_path(obj)
        opened = plain(await self.ctx.developer("developer.documents.open", {"source": {"kind": "file", "uri": path.as_uri()}}))
        if opened is None:   # no Developer: the file on disk is the code
            return path.read_text(encoding="utf-8")
        if opened["conflict"]:
            raise ContractError("conflict", f"{obj['entry']} changed on disk while it has unsaved edits. Resolve it in Developer, then run again.")
        root = Path(obj["directory"]).resolve().as_uri().rstrip("/") + "/"
        documents, cursor = [], None
        while True:
            page = plain(await self.ctx.call("developer.documents.list", {"rootUris": [root], **({"cursor": cursor} if cursor else {})}))
            documents.extend(page["documents"])
            cursor = page.get("nextCursor")
            if not cursor:
                break
        if opened["dirty"] and all(item["documentId"] != opened["documentId"] for item in documents):
            documents.append({"documentId": opened["documentId"], "uri": path.as_uri(), "version": opened["version"], "dirty": True})
        for document in documents:
            if not document.get("dirty"):
                continue
            saved = plain(await self.ctx.call("developer.documents.save", {"documentId": document["documentId"], "expectedVersion": document["version"]}))
            if saved["status"] not in {"saved", "clean"}:
                name = unquote(document["uri"]).rsplit("/", 1)[-1]
                raise ContractError("conflict", f"{name} could not be saved before running: {saved['message'] or saved['status']}.", {"file": name})
        return path.read_text(encoding="utf-8")

    async def capture_code(self, obj, directory):
        staging_parent = self.records.root / "capture"
        staging_parent.mkdir(exist_ok=True)
        with tempfile.TemporaryDirectory(dir=filesystem_path(staging_parent)) as staging_io:
            staging = str(staging_parent / Path(staging_io).name)
            root = Path(obj["directory"])
            before = await asyncio.to_thread(source_identity, root)
            await asyncio.to_thread(shutil.copytree, filesystem_path(root), filesystem_path(staging), dirs_exist_ok=True, ignore=shutil.ignore_patterns(*EXCLUDED, "*.pyc"))
            captured = await asyncio.to_thread(snapshot_project, self.ctx.data_dir, staging)
        if before != {item["path"]: item["sha256"] for item in captured["files"]}:
            raise ContractError("conflict", "The strategy changed while it was being fixed for the run. Run again.")
        await asyncio.to_thread(shutil.copytree, filesystem_path(captured["snapshotPath"]), filesystem_path(directory / "source"))
        return captured

    async def environment(self, identity, obj, captured, directory):
        def publish(state):
            progress = {key: state[key] for key in ("state", "preparationId", "message", "stdoutPath", "stderrPath") if key in state}
            if progress != self.read(identity)["environment"]:
                self.update(identity, environment=progress)
        record = self.read(identity)
        if record.get("of") or record.get("recipe"):
            original = self.read(record["of"]) if record.get("of") else record
            recipe, environment = original["recipe"], original["environment"]
            recipe_lock(recipe)
            lock = directory / "execution.lock"
            if lock.resolve() != Path(recipe["lockPath"]).resolve():
                shutil.copyfile(recipe["lockPath"], lock)
            state = plain(self.ctx.environments.get(environment["preparationId"]))
            if state["state"] != "completed":
                raise ContractError("unavailable", "The environment of the source run is no longer available.")
            self.ctx.environments.hold(environment["preparationId"], "qrun:" + identity)
            self.update(identity, recipe={**recipe, "lockPath": str(lock)}, environment=environment)
            return state
        project, baseline = project_of(obj, self.packages)
        lock, origin = await project_lock(self.ctx, project, captured, directory, baseline)
        recipe = write_recipe(directory, lock, origin, captured, project["engine"])
        try:
            state = await prepare_lock(self.ctx, recipe_lock(recipe), origin, publish)
        except ContractError as error:
            # The platform keeps a failed preparation under its key, so each new run asks it to try again once.
            failed = (error.details or {}).get("preparation")
            if failed is None or failed["state"] != "failed":
                raise
            self.ctx.environments.retry(failed["preparationId"])
            state = await prepare_lock(self.ctx, recipe_lock(recipe), origin, publish)
        # The run's record names this environment for as long as the record exists.
        self.ctx.environments.hold(state["preparationId"], "qrun:" + identity)
        self.update(identity, recipe={key: recipe[key] for key in ("pythonVersion", "lockPath", "lockDigest", "engine")},
                    environment={key: state[key] for key in ("state", "preparationId", "interpreterPath", "environmentPath", "pythonVersion", "stdoutPath", "stderrPath") if key in state})
        return state

    async def execute(self, identity, obj, check=None):
        process, ending = None, {}
        directory = self.directory(identity)
        research = obj["kind"] in RESEARCH_KINDS
        try:
            if check is not None:
                await check()
                self.update(identity, stage="code")
            directory.mkdir(parents=True, exist_ok=True)
            record = self.read(identity)
            captured, common = record["code"], record["common"]
            self.update(identity, stage="data")
            prepared = await (self.prepare_research(identity, directory) if research else self.prepare_data(identity, obj, directory))
            self.fetching.pop(identity, None)
            self.update(identity, data=prepared["summary"], stage="environment")
            state = await self.environment(identity, obj, captured, directory)
            self.update(identity, stage="run" if research else "backtest")
            if research:
                process = await self.launch_research(identity, obj, directory, prepared, state, common)
                await self.finish(identity, process, directory, research)
            else:
                # A backtest takes its place in Quant's one queue: it waits while what already runs leaves too little of the machine's budget.
                record = self.read(identity)
                need = await self.memory_need(record["settings"])
                async with self.queue.slot(owner=identity, label=f"{record['name']} #{record['number']}", interactive=True, memory=need or GB,
                                           on_wait=lambda position: self.update(identity, state="queued")):
                    process = await self.launch(identity, obj, directory, prepared, state)
                    await self.finish(identity, process, directory, research)
            ending = self.ending(identity, directory)
        except asyncio.CancelledError:
            ending = {"state": "interrupted" if self.closed else "cancelled", "message": "Stopped before the run finished."}
            if process:
                with contextlib.suppress(ContractError):
                    await asyncio.shield(self.ctx.processes.cancel(process))
        except ContractError as error:
            ending = {"state": "interrupted" if self.closed else "cancelled", "message": "Stopped before the run finished."} if error.code == "cancelled" else {
                "state": "failed", "message": error.message, "error": self.failure(identity, error)}
        except Exception as error:  # A background run must reach a terminal state even when backend preparation itself breaks.
            logging.getLogger(__name__).exception('Quant run failed (%s)', identity)
            failure = ContractError('internal_error', f'{type(error).__name__}: {error}', {'exceptionType': type(error).__name__})
            ending = {'state': 'failed', 'message': failure.message, 'error': self.failure(identity, failure)}
        finally:
            await self.close_models(identity)
            if process:
                with contextlib.suppress(ContractError):
                    await self.ctx.processes.close(process)
            self.processes.pop(identity, None)
            self.tasks.pop(identity, None)
            if ending:
                self.update(identity, **ending, endedAt=int(time.time() * 1000))

    def failure(self, identity, error):
        """The error as the owner sent it; a data-stage failure also says which symbols and range were being fetched."""
        record = self.read(identity)
        return {"stage": record["stage"], **error.to_dict(), **({"request": data_label(record["settings"])} if record["stage"] == "data" else {})}

    def data_progress(self, identity):
        """How far fetching the data is, kept in memory for the run's progress read (the Host both writes and reads it, so no file): the step
        (names, terms, prices), names done of all, and the names left out so far."""
        def report(step, done, total, left_out, **waiting):
            self.fetching[identity] = {"fetch": {"step": step, "done": done, "total": total, "leftOut": list(left_out), **waiting}}
        return report

    def prepared_source(self, source):
        """Reopen the source worker's market, signal and model materials for an unchanged date range."""
        path = self.directory(source["id"]) / "invocation.json"
        if not path.is_file():
            raise ContractError("unavailable", "This run's frozen backtest inputs are no longer available.")
        invocation = json.loads(path.read_text(encoding="utf-8"))
        config = invocation["backtest"]
        summary = source["data"]
        settings = source.get("runSettings") or summary["runSettings"]
        return {"barsPath": next(item["path"] for item in invocation["materials"] if item["role"] == "market_data"),
                "instruments": config["instruments"], "venues": config["venues"], "referencePrices": config["referencePrices"],
                "index": invocation["index"], "annualization": invocation["annualization"], "barTypes": invocation["barTypes"],
                "runSettings": settings, "libraryPath": invocation["libraryValues"]["path"], "benchmark": invocation["benchmark"],
                "riskFree": invocation["riskFree"], "fundingRates": invocation["fundingRates"], "extras": invocation["extras"],
                "industry": invocation["industry"], "accountsInfo": invocation["accounts"], "stockSessions": invocation["stockSessions"],
                "heldOutFrom": invocation["heldOutFrom"], "sources": object_data.frozen_lists(summary)["sources"],
                "sourceWindow": invocation.get("window"), "tradeFrom": config.get("tradeFrom"), "summary": {**summary, "runSettings": settings}}

    async def prepare_data(self, identity, obj, directory):
        record = self.read(identity)
        if record.get("of") and record["runKind"] != "heldout":
            return await asyncio.to_thread(self.prepared_source, self.read(record["of"]))
        settings = record["settings"]
        data = settings["data"]
        kind = "stock" if data.get("index") else strategy_settings.traded([item["instrument"] for item in data["symbols"]]) if data.get("symbols") else "perpetual"
        stored = settings.get("run") or {}
        held = data.get("heldOutFrom")
        names = len(data.get("symbols") or []) or 1
        first = strategy_settings.effective(kind, stored, settings["account"], held_out=held, names=names)
        # Every run but a held-out validation stops before the held-out end; the checks never see it.
        if data.get("until") == "latest":
            data = await object_data.resolve_until(self.ctx, data)
            settings = {**settings, "data": data}
            self.update(identity, settings=settings)
        cut = dict(strategy_settings.cut_data(data, record["runKind"]))
        trigger = first["exchange"]["stopTrigger"]
        if kind == "perpetual" and trigger in {"mark", "index"} and trigger not in cut.get("referencePrices", []):
            cut["referencePrices"] = [*cut.get("referencePrices", []), trigger]
        account = strategy_settings.account_for(settings["account"], first, kind)
        report = self.data_progress(identity)
        # The seconds each step of the data stage took, kept in the run's data summary (what to speed up next is measured, not guessed).
        clock = {"at": time.perf_counter(), "seconds": {}}

        def step(name):
            now = time.perf_counter()
            clock["seconds"][name] = round(now - clock["at"], 2)
            clock["at"] = now
        # The same data settings (to the same last closed bar) and account as a run before: its freeze, if Market has not changed it.
        resolved = await object_data.resolve_until(self.ctx, cut)
        step("lastBar")
        kept = self.records.root / "strategy-data" / digest({"data": resolved, "account": account})[:24]
        prepared = await object_data.prepare_reusing(self.ctx, {**settings, "data": cut, "account": account}, identity, directory, kept, report=report)
        step("pricesReused" if prepared.get("reused") else "prices")
        prepared["referencePrices"] = cut.get("referencePrices", [])
        left = object_data.left_out(prepared["index"], prepared["summary"]["skipped"])
        report("library", 0, 0, left)
        definitions = self.store.parameters((directory / "source" / obj["entry"]).read_text(encoding="utf-8"))["parameters"]
        summary = prepared["summary"]
        # Library items and models are read at the version the object is pinned to; the first run pins the newest (switchInput moves a pin).
        pins = obj["settings"].get("inputs") or {}
        if isinstance(pins, list):
            pins = self.store.strategy_bindings(obj, obj["settings"], {})
        named = library_inputs.named_items(definitions, record["params"])
        if record.get("signalRun"):
            named = [item for item in named if item[0] != record["signalRun"]["param"]]
        versions = {parameter: self.pinned(pins.get("library", {}), parameter, name, lambda found=name: self.library.resolve_values(found)["versionId"]) for parameter, name in named}
        overrides = {record["signalOverride"]["param"]: record["signalOverride"]["path"]} if record.get("signalOverride") else None
        if record.get("signalRun"):
            sig = record["signalRun"]
            named = [*named, (sig["param"], f"{sig['model']} #{sig['number']}")]
            overrides = {sig["param"]: {"path": sig["path"], "from": {key: sig[key] for key in ("runId", "model", "number")}}}
        library = await library_inputs.prepare(self.library, named, prepared["sources"], summary["since"], summary["until"], summary["timeframe"],
                                               directory / "library-values.parquet", versions=versions,
                                               overrides=overrides)
        prepared["libraryPath"], summary["library"] = library["path"], library["items"]
        step("library")
        frozen = [{"name": name, "versionId": self.pinned(pins.get("models", {}), name, name, lambda found=name: self.library.resolve(found, kind="model")["versionId"])}
                  for name in model_inputs.source_names(directory)]
        summary['models'] = await model_inputs.prepare(self.library, directory, summary['since'], frozen=frozen)
        fixed_pins = {"library": {row["parameter"]: {"name": row["name"], "versionId": row["versionId"]} for row in library["items"] if row.get("versionId")},
                      "models": {row["name"]: {"name": row["name"], "versionId": row["versionId"]} for row in summary["models"]}}
        if fixed_pins["library"] or fixed_pins["models"]:
            self.update(identity, settings={**settings, "inputs": fixed_pins})
        if not record.get("of"):
            self.pin_inputs(obj["id"], fixed_pins)
        # The run settings as this run uses them: Market's fee (or the account's) is known once the venue is built.
        venue = prepared["venues"][0]
        fee = {"maker": venue["fees"]["maker"], "taker": venue["fees"]["taker"]} if venue["fees"].get("type") == "maker_taker" else None
        source = "set here" if "feePct" in (stored.get("costs") or {}) or "commissionBps" in (stored.get("costs") or {}) else \
            "account settings" if settings["account"].get("fee") or settings["account"].get("stockCommission") else \
            "from Market (" + ", ".join(sorted({item["marketInstrument"]["venueId"] for item in prepared["sources"]})) + ")"
        commission = next((item.get("stockCommission") for item in prepared["instruments"] if item["type"] == "equity"), None)
        def venue_kind(venue):
            types = {row["type"] for row in prepared["instruments"] if row["id"].endswith("." + venue["name"])}
            return "stock" if "equity" in types else "perpetual" if "perpetual" in types else "spot"
        venue_kinds = [{"name": item["name"], "kind": venue_kind(item)} for item in prepared["venues"]]
        run = strategy_settings.effective(kind, stored, account, fee, source, commission, held_out=held, venues=venue_kinds if len(prepared["venues"]) > 1 else None, names=names)
        prepared["venues"] = [strategy_settings.native_venue(item, run, kind) for item in prepared["venues"]]
        prepared["instruments"] = [strategy_settings.native_instrument(item, run, kind) for item in prepared["instruments"]]
        step("modelsAndSessions")
        since, until = summary["since"], summary["until"]
        report("benchmark", 0, 0, left)
        benchmark, risk_free, funding = await asyncio.gather(
            strategy_inputs.benchmark(self.ctx, run["benchmark"], since, until, identity),
            strategy_inputs.risk_free(self.ctx, run["riskFree"], since, until, identity),
            strategy_inputs.funding(self.ctx, prepared["sources"], since, until, directory) if kind == "perpetual" and run["costs"].get("funding", True) else asyncio.sleep(0))
        step("benchmarkAndRate")
        files = {path.relative_to(directory / "source").as_posix(): path.read_text(encoding="utf-8") for path in sorted((directory / "source").rglob("*.py"))}
        wanted = strategy_inputs.series_names(files)
        report("extras", 0, 0, left)
        extras = await strategy_inputs.extras(self.ctx, wanted - strategy_inputs.OPTIONAL_SERIES, prepared["sources"], since, until, directory,
                                              progress=lambda value: report("extras", value["done"], value["total"], left))
        for name in wanted & strategy_inputs.OPTIONAL_SERIES:
            try:
                extras.update(await strategy_inputs.extras(self.ctx, {name}, prepared["sources"], since, until, directory))
            except ContractError:
                pass  # a name Market has none for is left to the code to cope with
        industry = {}
        for native, path in (extras.get("industry") or {}).items():
            table = await asyncio.to_thread(pd.read_parquet, path)
            if len(table):
                industry[native] = str(table.sort_values("time")["value"].iloc[-1])
        accounts = None
        if run.get("accounts") and len(run["accounts"]) > 1:
            currencies = {amount.split()[1] for item in run["accounts"] for amount in item["balances"]}
            accounts = {"reportCurrency": run["reportCurrency"],
                        "conversions": await strategy_inputs.conversions(self.ctx, currencies, run["reportCurrency"], since, until, directory, identity)}
        prepared.update(runSettings=run, benchmark=benchmark, riskFree=risk_free, fundingRates=funding, fundingPath=funding["path"] if funding else None,
                        extras=extras, industry=industry, accountsInfo=accounts,
                        heldOutFrom=object_data.day_ms(held, False) if held and record["runKind"] == "heldout" else None)
        summary["runSettings"], summary["tradedKind"] = run, kind
        summary["benchmark"] = {key: benchmark.get(key) for key in ("kind", "label", "symbol", "unavailable")}
        summary["riskFree"] = {key: risk_free.get(key) for key in ("basis", "pct", "unavailable")}
        # The engine takes the gaps in nanoseconds; the record keeps them in milliseconds like every other time in it (nanoseconds are beyond the exact JSON integers).
        summary["funding"] = {"settlements": funding["settlements"],
                              "gaps": {name: [[start // 1_000_000, end // 1_000_000, *rest] for start, end, *rest in gaps] for name, gaps in funding["gaps"].items()}} if funding else None
        # The lists that grow with the names (each one's source and bars, the index's exclusions: 5 MB for the S&P 500 over ten years) go to
        # info.json beside the bars (object_data.frozen_lists); the record keeps the counts, so the queries over every run stay cheap.
        sources = [{**{key: item[key] for key in ("instrument", "marketInstrument", "series", "nativeInstrument", "barType") if key in item},
                    # A stock's sessions and corporate actions (some 30 KB a name) stay in the fixed run.
                    **({"stock": {key: value for key, value in item["stock"].items() if key not in ("sessions", "corporateActions")}} if "stock" in item else {})}
                   for item in prepared["sources"]]
        info = {"instruments": summary.pop("instruments"), "sources": sources}
        if prepared["index"] is not None:
            summary["index"] = index_universe.summary(prepared["index"])
            info["index"] = {**summary["index"], "exclusions": prepared["index"]["exclusions"]}
        summary["infoPath"] = str(directory / "info.json")
        await asyncio.to_thread(lambda: (directory / "info.json").write_text(json.dumps(info), encoding="utf-8"))
        summary["venues"] = prepared["venues"]
        step("extrasAndRest")
        summary["seconds"] = clock["seconds"]
        return prepared

    @staticmethod
    def pinned(pins, key, name, newest):
        """The version a library input is pinned to, when the pin is for the same item; the newest otherwise."""
        pin = pins.get(key)
        return pin["versionId"] if pin and pin.get("name") == name else newest()

    def pin_inputs(self, object_id, resolved):
        """Only bind still-floating inputs that the live object reads, including after a per-run override or a concurrent edit."""
        if any(resolved.values()):
            obj = self.store.read(object_id)
            pins = self.store.strategy_bindings(obj, obj["settings"], {}, resolved=resolved)
            if any(pins.values()) and pins != obj["settings"].get("inputs"):
                self.store.save(obj, settings={**obj["settings"], "inputs": pins})

    async def prepare_research(self, identity, directory):
        record = self.read(identity)
        prepared = await research_data.prepare(self.ctx, record['settings']['data'], identity, directory)
        definitions = self.store.parameters((directory / 'source' / record['code']['entry']).read_text(encoding='utf-8'))['parameters']
        factors = await training_factors.prepare(self.library, definitions, record['params'], prepared['research'], directory)
        if factors is not None:
            prepared['research']['factors'] = factors
            prepared['summary']['factors'] = factors['items']
        self.pin(self.store.read(record['objectId']), [item['versionId'] for item in (factors or {}).get('items', [])])
        return prepared

    def pin(self, obj, version_ids):
        """The library versions an object's newest run read are held by the object: they show among their users, and it is told when one stops."""
        self.library.pin("qobject:" + obj["id"], obj["workspaceId"], kind=obj["kind"], name=obj["name"], how="research, its newest run reads it", version_ids=version_ids)

    async def launch_research(self, identity, obj, directory, prepared, state, common):
        record = self.read(identity)
        output = directory / "output"
        output.mkdir()
        support = research_process.write_support(directory)
        invocation = {"runId": identity, "title": obj["name"], "workbench": obj["kind"], "entry": obj["entry"], "source": str(directory / "source"),
                      "output": str(output), "common": str(directory / "common") if common else None, "support": str(support), "mode": record["mode"],
                      "params": record["params"], "research": prepared["research"]}
        research_process.write_invocation(directory, invocation)
        process = await research_process.start(self.ctx, key=identity, directory=directory, invocation=invocation, state=state,
                                               context={"objectId": obj["id"], "runId": identity})
        self.processes[identity] = process
        self.update(identity, state="running")
        return process

    async def launch(self, identity, obj, directory, prepared, state, snapshot=None, extra=None):
        """Start the backtest process. `extra` is what a sweep's combination or a walk-forward window adds to the invocation: `report: "light"` (key numbers,
        equity and trades only), `window` (the bars to read, `from`/`to`, and the months of them that only warm indicators up) and `tradeFrom` (no order
        is placed, and nothing is counted, before it)."""
        record = self.read(identity)
        extra = {**({"window": prepared["sourceWindow"]} if prepared.get("sourceWindow") else {}),
                 **({"tradeFrom": prepared["tradeFrom"]} if prepared.get("tradeFrom") is not None else {}), **(extra or {})}
        snapshot = snapshot or directory
        config = {"engine": "nautilus", "name": obj["name"], "task": "event", "dataType": "bars", "timezone": "UTC", "priceBasis": "split_dividend" if any(item['type'] == 'equity' for item in prepared['instruments']) else "raw",
                  'referencePrices': prepared.get('referencePrices', obj['settings']['data'].get('referencePrices', [])),
                  "instruments": prepared["instruments"], "venues": prepared["venues"], "strategy": {"kind": "object", "module": Path(obj["entry"]).stem,
                                                                                                           'nextRebalance': obj['preset'] in {'ranked', 'rebalance'}}}
        output = directory / "output"
        output.mkdir(exist_ok=True)  # the data stage's progress is already there (output/progress.json); the engine replaces it
        bindings = record['data']['models']
        if bindings:
            await self.models.open(bindings, identity, 1, directory / 'models')
        invocation = {"runId": identity, "title": obj["name"], "workbench": "strategy", "entry": obj["entry"], "definition": obj["entry"],
                      "source": str(snapshot / "source"), "output": str(output), "library": str(snapshot / "common") if record["common"] else None, "mode": "run", "operation": "strategy.backtest",
                      "options": {}, "sourceRefs": [], "recipeRef": identity, "annualization": prepared["annualization"],
                      "barTypes": prepared["barTypes"], "params": record["params"], "backtest": config, 'index': prepared['index'],
                      "libraryValues": {"path": prepared["libraryPath"], 'items': record['data']['library']},
                      "materials": [{"role": "market_data", "path": prepared["barsPath"]}],
                      'models': model_inputs.public_bindings(bindings), 'generation': 1}
        if "runSettings" in prepared:
            # What the report and the cost model read (native/strategy_report.py, native/strategy_costs.py).
            run = prepared["runSettings"]
            if record.get("runSettings"):
                run = record["runSettings"]  # a sweep combination's own run settings
            invocation.update(extras=prepared["extras"], industry=prepared["industry"], accounts=prepared["accountsInfo"], fillBars=record["data"].get("fillBars"),
                              runSettings=strategy_settings.invocation_settings(run), benchmark=prepared["benchmark"], riskFree=prepared["riskFree"],
                              fundingRates=prepared["fundingRates"], stockSessions=prepared["stockSessions"], heldOutFrom=prepared["heldOutFrom"],
                              display=[{"nativeInstrument": item["nativeInstrument"], "symbol": item["instrument"]["displaySymbol"]} for item in prepared["sources"]])
        invocation.update({key: extra[key] for key in ("report", "window") if key in extra})
        if "tradeFrom" in extra:
            invocation["backtest"]["tradeFrom"] = extra["tradeFrom"]
        # Off the event loop: with an index it is some 90 MB (each interval names its members in full), half a second of the Host's time.
        def write_invocation():
            # Stream the large membership table so encoding it yields the GIL to the module event loop.
            with (directory / "invocation.json").open("w", encoding="utf-8") as stream:
                json.dump(invocation, stream, separators=(",", ":"))
        await asyncio.to_thread(write_invocation)
        self.update(identity, materialsSeconds=round(time.time() - record["createdAt"] / 1000, 3))
        env = {key: os.environ[key] for key in ("SystemRoot", "WINDIR", "TEMP", "TMP", "PATH", "COMSPEC", "PATHEXT", "USERPROFILE", "APPDATA", "LOCALAPPDATA") if key in os.environ}
        env.update(OMP_NUM_THREADS="1", OPENBLAS_NUM_THREADS="1", MKL_NUM_THREADS="1", NUMEXPR_NUM_THREADS="1", MPLBACKEND="Agg",
                   PYTHONUNBUFFERED="1", PYTHONUTF8="1", PYTHONIOENCODING="utf-8", PYTHONDONTWRITEBYTECODE="1", GIT_CEILING_DIRECTORIES=str(directory))
        # The frozen models answer the backtest over its standard input and output.
        launch = {"submission_key": identity, "argv": [state["interpreterPath"], "-B", str(MODULE / "native" / "object_worker.py"), str(directory / "invocation.json")],
                  "cwd": str(snapshot / "source"), "env": env, "stdout_path": str(output / "stdout.log"), "stderr_path": str(output / "stderr.log"),
                  **({"lines": True} if bindings else {})}
        launching = asyncio.create_task(self.ctx.processes.start(launch))
        try:
            process = plain(await asyncio.shield(launching))
        except asyncio.CancelledError:
            process = plain(await launching)
            self.processes[identity] = process
            raise
        self.processes[identity] = process
        if bindings:
            self.model_pumps[identity] = asyncio.create_task(self.pump_models(identity, process))
        self.update(identity, state="running")
        return process

    async def pump_models(self, identity, process):
        while True:
            event = plain(await self.ctx.processes.receive(process, 1.0))
            if event['state'] == 'closed':
                return
            if event['state'] == 'message':
                response = await self.models.predict(event['message'], bound={'modelRunId': identity, 'generation': 1})
                await self.ctx.processes.send(process, response)

    async def close_models(self, identity):
        pump = self.model_pumps.pop(identity, None)
        if pump is not None:
            pump.cancel()
            await asyncio.gather(pump, return_exceptions=True)
        await self.models.close(identity, 1)

    async def finish(self, identity, process, directory, research=False):
        if research:
            code, calls = await research_process.wait(self.ctx, process)
            self.update(identity, exitCode=code, data={**self.read(identity)["data"], "calls": calls})
            return
        while plain(await self.ctx.processes.status(process))["running"]:
            await asyncio.sleep(.2)
        code = await self.ctx.processes.wait(process)
        pump = self.model_pumps.get(identity)
        if pump is not None:
            await pump
        self.update(identity, exitCode=code)

    def ending(self, identity, directory):
        record = self.read(identity)
        if record["objectKind"] == "strategy":
            receipt = directory / "output" / "outcome.json"
            result = json.loads(receipt.read_bytes()) if receipt.is_file() else None
        else:
            result = self.result(record)
        if record["exitCode"] == 0 and result is not None and result["status"] == "completed":
            return {"state": "completed", "message": None, "metrics": metrics_of(result), "stats": ((result.get("strategyReport") or {}).get("stats")),
                    **{key: result[key] for key in ("peakMemory", "timings") if key in result}}
        if result is not None and result.get("status") == "cancelled":
            if self.closed:
                return {"state": "interrupted", "message": "The application stopped while this ran; the part up to that moment is kept.", "metrics": {}}
            return {"state": "cancelled", "message": "Stopped by the user", "metrics": {}}
        stderr = directory / "output" / "stderr.log"
        failure = describe_failure(result, record["code"]["directory"], stderr.read_text(encoding="utf-8", errors="replace") if stderr.is_file() else "", record["exitCode"])
        # Where the run stopped stays in the record: reading a run never loads its (possibly large) result.
        return {"state": "failed", "message": failure["message"], "error": {"stage": "backtest", **failure},
                "failureContext": (result or {}).get("failureContext") or {}}

    async def cancel(self, identity):
        record = self.read(identity)
        if record["state"] in TERMINAL:
            return record
        task = self.tasks.get(identity)
        if task is None:
            raise ContractError("conflict", "This run is not running in this session.")
        self.update(identity, state="cancel_requested")
        environment = self.read(identity)["environment"]
        if environment is not None and environment["state"] in {"queued", "running", "cancel_requested"}:
            preparation = environment["preparationId"]
            shared = any(other["id"] != identity and other["state"] not in TERMINAL and other.get("environment") is not None and other["environment"]["preparationId"] == preparation
                         for other in self.records.list(RUN))
            if not shared:
                await self.ctx.environments.cancel(preparation)
        if identity in self.processes and record["stage"] == "backtest":
            # The backtest is asked to stop: its engine ends at the next bars and it writes the part so far. The answer is the run stopping;
            # it ends when that is written, and its process is ended only if that has not happened within STOP_SECONDS.
            self.request_stop(identity)
            wait = asyncio.ensure_future(self.end_if_still_going(identity, task))
            self.stopping.add(wait)
            wait.add_done_callback(self.stopping.discard)
            return self.read(identity)
        if identity in self.processes:
            await self.ctx.processes.cancel(self.processes[identity])
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)
        if self.read(identity)["state"] == "cancel_requested":
            message = "Stopped before the run started."
            self.update(identity, **run_flow.terminal_state(record, "cancelled", message), endedAt=int(time.time() * 1000))
            self.tasks.pop(identity, None)
        return self.read(identity)

    def request_stop(self, identity):
        (self.directory(identity) / "output").mkdir(parents=True, exist_ok=True)
        (self.directory(identity) / "output" / "stop.request").write_text("", encoding="utf-8")

    async def end_if_still_going(self, identity, task):
        with contextlib.suppress(TimeoutError, asyncio.CancelledError):
            async with asyncio.timeout(STOP_SECONDS):
                await asyncio.shield(task)
        if self.read(identity)["state"] in TERMINAL:
            return
        task.cancel()  # the run's own ending cancels its process
        await asyncio.gather(task, return_exceptions=True)

    def active(self, workspace_id):
        return [self.read(identity) for identity in self.tasks if self.read(identity)["workspaceId"] == workspace_id]

    async def close(self):
        """The application is closing: a backtest is asked to stop and write its part (a few seconds at most), then every process is ended."""
        self.closed = True
        going = [self.tasks[identity] for identity in self.processes if identity in self.tasks and self.read(identity)["stage"] == "backtest"]
        for identity in [identity for identity in self.processes if self.read(identity)["stage"] == "backtest"]:
            self.request_stop(identity)
        if going:
            await asyncio.wait([asyncio.shield(task) for task in going], timeout=CLOSE_SECONDS)
        for process in list(self.processes.values()):
            with contextlib.suppress(ContractError):
                await self.ctx.processes.cancel(process)
        tasks = list(self.tasks.values())
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)

    async def apply(self, identity, expected_version=None):
        """Put this run's code and parameter values back as the object's current ones; Developer keeps the code it replaces in file history."""
        run = self.read(identity)
        if run["code"] is None:
            raise ContractError("unavailable", "This run never fixed its code.")
        obj = self.store.read(run["objectId"])
        self.store.check_version(obj, expected_version)
        from .run_restore import restore_files

        code = run["code"]
        report = await restore_files(self.ctx, obj, None if obj["preset"] else code["snapshotPath"], run.get("common"), self.common.root,
                                     expected={item["path"]: item["sha256"] for item in code["files"]})
        self.store.check_version(self.store.read(obj["id"]), obj["version"])
        settings = {**self.source_settings(run), "params": dict(run["params"])}
        if run.get("signalRun"):
            signal = run["signalRun"]
            settings["signalRun"] = {**settings.get("signalRun", {}), **signal}
            settings["params"][signal["param"]] = ""
        if settings["data"].get("untilLatest"):
            settings["data"] = {**settings["data"], "until": "latest"}
            settings["data"].pop("untilLatest")
        return {**self.store.detail(self.store.save(obj, settings=settings)), "restore": report}
