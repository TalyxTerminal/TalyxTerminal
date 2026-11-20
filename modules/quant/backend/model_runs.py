"""A model's training runs: the check before training, batches, the shared queue, window-by-window progress, stop and resume, the
experiment table, compare and combine, a held-out validation, Results and a day's values.

Each run is one experiment, numbered within its model (#1, #2 …); a batch is up to 20 runs sent together, folded in the table. A run
records every setting (the declared values, the data settings and the run settings), the inputs pinned to what they were (library
versions, factor runs), and as it trains each window's numbers. Runs are background work in Quant's queue: as many at once as the
model's concurrency allows, each within the memory budget, each window kept in the run's window cache the moment it is done. A run
stopped by the user can be resumed; one the application stopped while it ran is resumed by itself when Quant opens again, as background
work of the workspace it was started in (when that workspace is open, or once it opens). A run's
end goes to the platform's notifications (Notifications · Long runs).
"""
from __future__ import annotations

import asyncio
import contextlib
import contextvars
import copy
import json
import os
import shutil
import time
from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4

import numpy as np
import pandas as pd
from talyx_sdk import ContractError

from ..native import model_algorithms as A
from ..native.model_data import quick_formula
from . import model_prep as P
from .library_publish import universe_text
from .model_work_figure import compare_figure, epoch_figure, work_figure
from .object_data import day_ms, resolve_until
from .runtime import ACTIVITY, WORKSPACE, call_attribution

WARM_SECONDS = 900  # the first look at a run builds its inputs (minutes on a long history); the process is not restarted before that

RUN = "qrun"
BATCH = "qmbatch"
SETTINGS = "qsettings"
WORK_KEY = "model.work.windows"
EPOCH_KEY = "model.work.epochs"
TERMINAL = {"completed", "failed", "cancelled", "interrupted"}
KINDS = {"train", "heldout", "combine"}
READ_LIMIT = 300
GB = 1024 ** 3
RUN_DEFAULTS = {"cost": 5.0, "topN": 50}


def is_model_run(record):
    """Whether a run record is a model run (older records marked themselves with `pipeline: true`; now `pipeline` says which standard pipeline it ran)."""
    return bool(record.get("modelRun") or record.get("pipeline") is True)


def status_of(record):
    """The experiment table's status: queued, running, completed, failed or stopped (by hand, or when Quant closed)."""
    state = record["state"]
    if state in {"queued"}:
        return "queued"
    if state in {"preparing", "running", "cancel_requested"}:
        return "running"
    if state in {"cancelled", "interrupted"}:
        return "stopped"
    return state


QUICK = {quick_formula(days, form): f"{days}D " + {"raw": "return", "demean": "less the mean", "rank": "rank"}[form] for days in (1, 5, 10, 20) for form in ("raw", "demean", "rank")}


def label_of(params):
    """The label as the table says it: "5D rank" for one of the twelve quick choices, the formula itself for any other."""
    source = (params.get("label") or "").strip() if params.get("label") else quick_formula(params.get("label_days", 5), params.get("label_form", "rank"))
    return QUICK.get(source, source)


def summary(record):
    """One row of the experiment table (and what `get_object_run` shows of a model run)."""
    params = record["settings"]["params"]
    numbers = record.get("numbers") or None
    metrics = None
    if numbers:
        metrics = {key: numbers.get(key) for key in ("ric", "t", "vsInput", "lsNet", "lsGross", "turnover", "trainIc", "positive", "windows", "breakeven", "like", "sharpe")}
    progress = record.get("progress") or {}
    windows = record.get("windows") or []
    return {"id": record["id"], "objectId": record["objectId"], "objectKind": "model", "name": record.get("name"), "number": record["number"], "kind": record["runKind"], "batch": record.get("batch"),
            "group": record.get("group"), "status": status_of(record), "state": record["state"], "stage": record["stage"], "createdAt": record["createdAt"],
            "started": record.get("startedAt"), "finished": record.get("endedAt"), "time": record.get("seconds"), "mode": record.get("mode"),
            "algo": params.get("algorithm") or None, "label": label_of(params), "inputs": [row.get("name") for row in params.get("inputs") or []],
            "features": record.get("features"), "params": params, "values": record.get("values") or {}, "run": record["settings"].get("run"),
            "progress": progress if status_of(record) == "running" else None, "queue": record.get("queue") if record["state"] == "queued" else None,
            "failure": record.get("failure"), "stopped": record.get("stopped") if status_of(record) == "stopped" else None,
            "windowsTotal": record.get("windowsTotal"), "windowsDone": len(windows), "windows": windows, "perWindow": record.get("perWindow", True),
            "portfolio": (record.get("portfolio") or {}).get("state"), "metrics": metrics, "baselines": record.get("baselines"), "checks": record.get("checks"),
            "review": sum(1 for item in record.get("checks") or [] if item["state"] == "watch"), "passed": sum(1 for item in record.get("checks") or [] if item["state"] == "ok"),
            "combinedFrom": record.get("combinedFrom"), "of": record.get("of"), "ofNumber": record.get("ofNumber"),
            "dataTo": (record.get("data") or {}).get("dataTo"), "dataFrom": (record.get("data") or {}).get("dataFrom"), "memory": record.get("peakMemory"),
            "estimate": record.get("estimate"), "starred": record.get("starred", False), "note": record.get("note", ""), "published": record.get("version"),
            "heldOut": record.get("heldOut"), "message": record.get("message"), "updatedAt": record.get("updatedAt"), "overrides": record.get("overrides") or {}, "overrideFrom": record.get("overrideFrom"),
            "resumedAfterClose": bool(record.get("resumedAfterClose")),
            "pipeline": record.get("pipeline") if isinstance(record.get("pipeline"), dict) else None, "reach": record.get("reach"), "horizon": record.get("horizon"),
            "heldOutFrom": (record["settings"].get("data") or {}).get("heldOutFrom")}


def flat(value, prefix=""):
    if isinstance(value, dict):
        return {key: item for name, part in value.items() for key, item in flat(part, f"{prefix}.{name}" if prefix else name).items()}
    return {prefix: value}


class ModelRuns:
    def __init__(self, ctx, records, store, factors, library, notifications):
        self.ctx, self.records, self.store, self.factors, self.library, self.notifications = ctx, records, store, factors, library, notifications
        self.data, self.engine, self.queue = factors.data, factors.engine, factors.queue
        self.tasks = {}
        self.stopping = set()
        self.closed = False
        self.recovered = set()  # workspaces whose runs from before this process were picked up (None: all of them, at start)
        saved = records.find(SETTINGS, "models") or {}
        if saved.get("concurrency"):
            self.queue.cpus = int(saved["concurrency"])

    # ------------------------------------------------------------------------------------------------------------ records
    def directory(self, identity):
        return self.records.root / "qmodels" / identity

    def read(self, identity):
        record = self.records.find(RUN, identity)
        if record is None or not is_model_run(record):
            raise ContractError("not_found", "This model run does not exist.")
        return record

    def owns(self, identity):
        record = self.records.find(RUN, identity)
        return bool(record and is_model_run(record))

    def save(self, record, **changes):
        previous = record["state"]
        updated = self.records.put(RUN, {**record, **changes})
        if updated["state"] != previous or {"stage", "numbers", "progress", "starred", "checks", "windows", "queue", "portfolio"} & set(changes):
            self.ctx.publish("object_run_changed", {"runId": updated["id"], "objectId": updated["objectId"], "workspaceId": updated["workspaceId"], "state": updated["state"]})
        return updated

    def update(self, identity, **changes):
        return self.save(self.read(identity), **changes)

    def of_object(self, object_id, *, since=None):
        clauses = ["kind='qrun'", "json_extract(content,'$.objectId')=?", "coalesce(json_extract(content,'$.modelRun'), json_extract(content,'$.pipeline'))=1", "coalesce(json_extract(content,'$.removed'),0)=0"]
        values = [object_id]
        if since is not None:
            clauses.append("json_extract(content,'$.updatedAt')>?")
            values.append(since)
        rows = self.records.db.execute("SELECT content FROM records WHERE " + " AND ".join(clauses) + " ORDER BY json_extract(content,'$.number') DESC LIMIT ?",
                                       [*values, READ_LIMIT]).fetchall()
        return [json.loads(row[0]) for row in rows]

    def next_number(self, object_id):
        row = self.records.db.execute("SELECT MAX(json_extract(content,'$.number')) FROM records WHERE kind='qrun' AND json_extract(content,'$.objectId')=? "
                                      "AND coalesce(json_extract(content,'$.modelRun'), json_extract(content,'$.pipeline'))=1", (object_id,)).fetchone()
        return int(row[0] or 0) + 1

    def model(self, object_id):
        obj = self.store.read(object_id)
        if obj["kind"] != "model":
            raise ContractError("invalid_input", "Only a model trains models.")
        return obj

    # ------------------------------------------------------------------------------------------------------------ the preparation
    async def prep(self, object_id):
        """The preparation as the page draws it: the declared settings in their groups with the model's values, the algorithms, the
        inputs it can add, the run settings, and how many run at once."""
        obj = self.model(object_id)
        declared = P.declarations(self.store, obj)
        values = {item["name"]: obj["settings"].get("params", {}).get(item["name"], item["default"]) for item in declared["parameters"]}
        groups = []
        for item in declared["parameters"]:
            group = item.get("group") or "Settings"
            if not groups or groups[-1]["name"] != group:
                groups.append({"name": group, "settings": []})
            groups[-1]["settings"].append(item)
        try:
            offers = await P.offers(self, obj)
        except ContractError:
            offers = list(P.BUILTIN)
        return {"objectId": obj["id"], "name": obj["name"], "mode": obj["settings"].get("mode", "standard"), "ownCode": obj["settings"].get("mode") == "code",
                "groups": groups, "values": values, "errors": declared["errors"], "algorithms": P.algorithms(), "offers": offers,
                "run": {**RUN_DEFAULTS, **(obj["settings"].get("run") or {})}, "data": obj["settings"].get("data") or {}, "batchLimit": P.BATCH_LIMIT,
                "running": self.running_view(), "version": obj["version"], "baseRuns": self.base_runs(obj["id"]),
                "concepts": P.concept_sources(self.library)}

    def base_runs(self, object_id):
        """The finished LSTM and GRU runs of this model GATs can start from (the page keeps those with the same inputs, look back, width and layers): `run #14 · LSTM`."""
        out = []
        for record in self.of_object(object_id):
            params = (record.get("settings") or {}).get("params") or {}
            if record["state"] == "completed" and params.get("algorithm") in ("lstm", "gru") and record.get("runKind", "train") == "train":
                out.append({"id": record["id"], "number": record["number"], "label": f"run #{record['number']} · {params['algorithm'].upper()}", "algorithm": params["algorithm"],
                            "inputs": params.get("inputs"), "lookback": params.get("nn_lookback", 20), "width": params.get("nn_width", 64), "layers": params.get("nn_layers", 2)})
        return out

    def concepts(self, object_id):
        """The Concepts row's choices: `{sources: [{id, label, kind: "market" | "library"}]}`."""
        self.model(object_id)
        return {"sources": P.concept_sources(self.library)}

    def concept_file(self, params, info, folder):
        """HIST's concept table of the chosen source, written beside the run (None when none is chosen or it cannot be read)."""
        if params.get("algorithm") != "hist" or not params.get("nn_concepts"):
            return None
        try:
            self.library.store.version(params["nn_concepts"])
        except ContractError:
            return None
        Path(folder).mkdir(parents=True, exist_ok=True)
        return P.concept_table(self.library, params["nn_concepts"], info, Path(folder) / "concepts.parquet")

    def start_from(self, params):
        """The run this model's network starts from, and what it has to match: the same inputs, look back, width and layers (the check says when it does not)."""
        if params.get("algorithm") not in ("gats", "igmtf", "add", "hist") or not params.get("nn_base"):
            return None
        try:
            base = self.read(params["nn_base"])
        except ContractError:
            return {"own": {}, "base": {"label": "The run", "inputs": None}}
        theirs = base["settings"]["params"]
        facts = lambda found: {"inputs": found.get("inputs"), "lookback": found.get("nn_lookback", 20), "width": found.get("nn_width", 64), "layers": found.get("nn_layers", 2)}
        return {"own": facts(params), "base": {"label": f"run #{base['number']}", **facts(theirs)}}

    def running_view(self):
        view = self.queue.view()
        return {"concurrency": self.queue.cpus, "cpus": os.cpu_count() or 1, "memoryUsed": view["used"] / GB, "memoryTotal": view["memory"] / GB,
                "budget": view["budget"] / GB, "running": len(view["running"]), "waiting": len(view["waiting"])}

    # ------------------------------------------------------------------------------------------------------------ the check before training
    async def check(self, object_id, args=None, report=None):
        """Without training, in seconds: each window's rows and names a day, the inputs' coverage and start, inputs that are nearly the
        same, the label, look-ahead, and each group's memory against what is free; the time axis; the batch's groups. Only what would
        fail for certain blocks sending."""
        args = args or {}
        started = time.perf_counter()
        obj = self.model(object_id)
        params = P.values_of(self.store, obj, args.get("params"))
        data_settings = obj["settings"].get("data") or {}
        P.data_ready(data_settings)
        data_settings = await resolve_until(self.ctx, data_settings, instruments=await self.data.calendar_items(data_settings))
        groups = P.expand(params, args.get("vary"))
        ready = await self.data.prepare(data_settings, report=report)
        folder = self.records.root / "qmodel-checks" / obj["id"]
        made = folder / "inputs"
        inputs, pins = await P.prepare_inputs(self, params.get("inputs") or [], ready["info"], made)
        held = P.held_out_of(data_settings, day_ms)
        concurrency = max(1, min(self.queue.cpus, len(groups)))
        threads = max(1, (os.cpu_count() or 2) // concurrency)
        job = {"output": str(folder / "output"), "data": {key: ready[key] for key in ("key", "barsPath", "info", "extrasPath")}, "inputs": inputs,
               "settings": P.settings_of(params), "algorithm": params.get("algorithm") or "", "params": P.algorithm_params(params), "threads": threads,
               "budget": self.queue.budget, "heldOut": held,
               "groups": [{"algorithm": group["params"].get("algorithm") or "", "params": P.algorithm_params(group["params"])} for group in groups],
               "calibration": self.calibration(obj["id"]), "engineText": params.get("engine_params") or "",
               "deepInstalled": self.engine.packages.deep_status()["installed"], "concurrency": concurrency,
               "devices": self.engine.packages.devices()["nvidia"], "startFrom": self.start_from(params),
               "concepts": await asyncio.to_thread(self.concept_file, params, ready["info"], made)}
        answer = await self.engine.call("model_check", job)
        answer.pop("talyx", None)
        answer.pop("id", None)
        if not answer.get("ok"):
            failure = answer.get("failure") or {"title": "cannot be checked", "message": "The check could not run."}
            return {"ok": False, "failure": failure, "blocked": True, "checks": [{"name": failure.get("title", "Data"), "value": failure["message"], "need": "", "state": "block",
                                                                                 "group": failure.get("stage", "data"), "detail": failure["message"]}],
                    "seconds": round(time.perf_counter() - started, 2)}
        estimates = answer.get("estimates") or []
        plan = []
        for index, group in enumerate(groups):
            found = estimates[index] if index < len(estimates) else {}
            plan.append({"group": index + 1, "values": group["values"], "time": found.get("seconds"), "memory": found.get("memory"), **({"vram": found["vram"]} if found.get("vram") else {}), "blocked": found.get("blocked", False)})
        total = sum(item["time"] or 0 for item in plan) / concurrency
        waiting = self.queue.view()["waiting"]
        answer["batch"] = {"vary": [item["name"] for item in args.get("vary") or [] if item.get("values")], "groups": plan, "total": total,
                           "peak": max([item["memory"] or 0 for item in plan] + [0]), "free": self.queue.free(), "queue": len(waiting) + 1,
                           "concurrency": concurrency, "threads": threads, "limit": P.BATCH_LIMIT}
        answer["pins"] = pins
        answer["seconds"] = round(time.perf_counter() - started, 2)
        answer["dataKey"] = ready["key"]
        answer["frozenData"] = self.frozen_data(ready)
        if answer.get("blocked"):
            reasons = [item for item in answer["checks"] if item["state"] == "block"]
            answer["blockedBy"] = reasons[0]["name"] if reasons else None
        return answer

    def calibration(self, object_id):
        """Measured time against the estimate of this model's last finished runs, so the next estimate learns from them."""
        rows = [record for record in self.of_object(object_id)[:10] if record["state"] == "completed" and (record.get("estimate") or {}).get("raw")]
        if not rows:
            return None
        ratios = [record["seconds"] / record["estimate"]["raw"] for record in rows if record.get("seconds")]
        return sum(ratios) / len(ratios) if ratios else None

    async def inspect(self, object_id, input_id, feature=None):
        """One input before and after processing, on the training segments: the preparation's look at an input."""
        obj = self.model(object_id)
        params = P.values_of(self.store, obj)
        data_settings = obj["settings"].get("data") or {}
        P.data_ready(data_settings)
        data_settings = await resolve_until(self.ctx, data_settings, instruments=await self.data.calendar_items(data_settings))
        ready = await self.data.prepare(data_settings)
        folder = self.records.root / "qmodel-checks" / obj["id"]
        inputs, _ = await P.prepare_inputs(self, params.get("inputs") or [], ready["info"], folder)
        job = {"output": str(folder), "data": {key: ready[key] for key in ("key", "barsPath", "info", "extrasPath")}, "inputs": inputs,
               "settings": P.settings_of(params), "input": input_id, "feature": feature, "heldOut": P.held_out_of(data_settings, day_ms)}
        answer = await self.engine.call("model_inspect", job)
        answer.pop("talyx", None)
        answer.pop("id", None)
        if not answer.get("ok"):
            raise ContractError("invalid_input", answer["failure"]["message"], {"failure": answer["failure"]})
        return answer

    # ------------------------------------------------------------------------------------------------------------ sending
    async def train(self, object_id, args=None):
        """Send one run, or a batch (`vary`: settings with several values, at most 20 groups). The runs are made at once, which is the button's answer,
        and the check is their first step, in the background (two or three minutes passed with nothing to show). What would fail for certain
        (too few names, an algorithm this environment cannot run, more memory than runs on this computer ever get) ends them with the reason; one that
        needs more memory than is free now waits in the queue for it."""
        if self.closed:
            raise ContractError("unavailable", "Quant is closing.")
        args = args or {}
        obj = self.model(object_id)
        params = P.values_of(self.store, obj, args.get("params"))
        if not params.get("algorithm"):
            raise ContractError("invalid_input", "Choose an algorithm first.")
        groups = P.expand(params, args.get("vary"))
        if args.get("params"):
            obj = self.store.save(obj, settings={**obj["settings"], "params": {**obj["settings"].get("params", {}), **args["params"]}})
        batch = None
        if len(groups) > 1:
            labels = {item["name"]: item["label"] for item in P.declarations(self.store, obj)["parameters"]}   # "Leaves", not lgb_leaves
            batch = {"id": "batch-" + uuid4().hex[:12], "objectId": obj["id"], "title": args.get("title") or " × ".join(labels[item["name"]] for item in args["vary"] if item.get("values")),
                     "createdAt": int(time.time() * 1000), "vary": [item["name"] for item in args["vary"] if item.get("values")], "runs": []}
        created = []
        for index, group in enumerate(groups):
            created.append(await self.create(obj, "train", group["params"], values=group["values"], batch=batch["id"] if batch else None, group=index + 1 if batch else None,
                                             state="preparing", stage="check"))
        if batch:
            self.records.put(BATCH, {**batch, "runs": [record["id"] for record in created]})
        identities = [record["id"] for record in created]
        task = self.ctx.start(self.check_then_queue(object_id, args, identities), f"Training {obj['name']}", run_record=created[0])
        for identity in identities:
            self.tasks[identity] = task
        return {"runs": [summary(record) for record in created], "batch": batch}

    async def check_then_queue(self, object_id, args, identities):
        """The runs' first step: the check, once for the batch. Then each run gets its estimate and goes to the queue, or every run ends with what blocks it."""
        try:
            last = {"at": 0.0, "stage": None}

            def report(rows, stage, done=None, progress=None):
                # Fetching the data can take minutes (new bars for hundreds of names): the runs say so with the count, not a bare "Checking".
                now = time.time()
                if now - last["at"] < .5 and stage == last["stage"] and stage != "ready":
                    return
                last.update(at=now, stage=stage)
                if stage == "ready":
                    shown = {"stage": "check", "progress": {}}
                else:
                    total = progress["total"] if progress is not None else len(rows)
                    count = done if done is not None else sum(1 for row in rows if row.get("result") not in (None, "waiting"))
                    shown = {"stage": "data", "progress": {"stage": "facts" if stage == "facts" else "prices", "done": count, "total": total,
                                                           "waitingFor": (progress or {}).get("waitingFor")}}
                for identity in identities:
                    if self.read(identity)["state"] == "preparing":
                        self.update(identity, **shown)

            try:
                checked = await self.check(object_id, args, report=report)
            except asyncio.CancelledError:
                await self.end_checking(identities, lambda identity: {"state": "cancelled" if identity in self.stopping else "interrupted", "message": "Stopped before training."})
                raise
            except ContractError as error:
                said = error.message
                await self.end_checking(identities, lambda identity: self.check_failure(identity, {"stage": "check", "title": "cannot be checked", "message": said}))
                return
            except Exception as error:  # a background step must end its runs and say what broke
                import logging
                logging.getLogger(__name__).exception("The check of model runs failed (%s)", identities)
                message = f"Quant hit an internal error ({type(error).__name__}) while checking; the details are in Quant's log."
                await self.end_checking(identities, lambda identity: self.check_failure(identity, {"stage": "check", "title": "stopped", "message": message}))
                return
            if checked.get("blocked"):
                reasons = [item for item in checked["checks"] if item["state"] == "block"]
                first = reasons[0] if reasons else {"name": "Check", "value": "The check blocks training."}
                await self.end_checking(identities, lambda identity: self.check_failure(identity, {"stage": "check", "title": first["name"], "message": first.get("detail") or first["value"],
                                                                                             "checks": reasons}))
                return
            for index, identity in enumerate(identities):
                estimate = checked["batch"]["groups"][index] if index < len(checked["batch"]["groups"]) else {}
                self.tasks.pop(identity, None)
                if identity in self.stopping:
                    continue
                self.update(identity, state="queued", stage="queued", windowsTotal=checked.get("windows"), data=checked.get("frozenData"),
                            estimate={"memory": estimate.get("memory"), "seconds": estimate.get("time"), "raw": estimate.get("time"), **({"vram": estimate["vram"]} if estimate.get("vram") else {})})
                self._launch(identity, activity=ACTIVITY.get())
        finally:
            for identity in identities:
                if self.tasks.get(identity) is asyncio.current_task():
                    self.tasks.pop(identity, None)

    def check_failure(self, identity, failure):
        return {"state": "failed", "message": failure["message"], "failure": {**self.failure_of(identity, failure), **({"checks": failure["checks"]} if failure.get("checks") else {})},
                "error": {"stage": "check", "code": "invalid_input", "message": failure["message"]}}

    async def end_checking(self, identities, ending_of):
        for identity in identities:
            self.save(self.read(identity), **ending_of(identity), endedAt=int(time.time() * 1000))
            self.stopping.discard(identity)
        for identity in identities:
            await self.notify(identity)

    async def create(self, obj, kind, params, *, values=None, batch=None, group=None, estimate=None, windows_total=None, frozen=None, of=None, extra=None, state="queued", stage="queued"):
        identity = "run-" + uuid4().hex
        directory = self.directory(identity)
        directory.mkdir(parents=True, exist_ok=True)
        mode = of["mode"] if of else obj["settings"].get("mode", "standard")
        common, code = None, None
        own_files = mode == "code" or any(row.get("source") == "code" for row in params.get("inputs") or [])
        if of:
            code, common = of.get("codeSnapshot"), of.get("common")
        elif own_files:
            root = directory if mode == "code" else directory / "features"
            root.mkdir(parents=True, exist_ok=True)
            await self.factors.save_project(obj)
            captured = await self.factors.runs.capture_code(obj, root)
            code = {"source": str(root / "source"), "entry": obj["entry"], "files": [item["path"] for item in captured["files"]],
                    "digest": captured["digest"], "hashes": {item["path"]: item["sha256"] for item in captured["files"]}}
            common = await self.factors.common.capture(self.records.root / "capture", directory / "common")
        pipeline = None
        if mode == "standard":
            source = self.directory(of["id"]) / "source" / P.STANDARD.name if of else P.STANDARD
            target = directory / "source" / P.STANDARD.name
            target.parent.mkdir(exist_ok=True)
            shutil.copyfile(source, target)
            pipeline = P.pipeline_of(target)
        now = int(time.time() * 1000)
        data = obj["settings"].get("data") or {}
        data = copy.deepcopy(await resolve_until(self.ctx, data, instruments=await self.data.calendar_items(data)))  # the day the data end, written into the run
        run = {**RUN_DEFAULTS, **(obj["settings"].get("run") or {})}
        record = {"id": identity, "objectId": obj["id"], "objectKind": "model", "modelRun": True, "pipeline": pipeline, "name": obj["name"], "runKind": kind, "number": self.next_number(obj["id"]),
                  "mode": mode, "workspaceId": self.store.workspace(), "state": state, "stage": stage, "createdAt": now, "startedAt": None, "endedAt": None,
                  "seconds": None, "settings": {"data": data, "params": copy.deepcopy(params), "run": run}, "values": values or {}, "batch": batch, "group": group,
                  "estimate": estimate or {}, "windowsTotal": windows_total, "windows": [], "progress": {"steps": [], "startedAt": now}, "numbers": None, "checks": None,
                  "baselines": None, "failure": None, "stopped": None, "starred": False, "note": "", "version": None, "heldOut": None, "of": of["id"] if of else None,
                  "ofNumber": of["number"] if of else None, "combinedFrom": None, "data": copy.deepcopy(frozen), "codeSnapshot": code,
                  "overrides": {}, "removed": False,
                  # Fields the shared run history, activity and actions read.
                  "message": None, "error": None, "params": params, "metrics": {}, "code": None, "common": common, "environment": None, "recipe": None,
                  "exitCode": None, **(extra or {})}
        record = self.records.put(RUN, record)
        self.ctx.publish("object_run_changed", {"runId": identity, "objectId": obj["id"], "workspaceId": record["workspaceId"], "state": state})
        return record

    def _launch(self, identity, *, activity=None):
        """The run as background work of the workspace it was started in, whoever launches it (a resume at start too):
        closing that workspace stops it."""
        record = self.read(identity)
        context = contextvars.copy_context()
        context.run(WORKSPACE.set, record["workspaceId"])
        self.tasks[identity] = context.run(self.ctx.start, self.execute(identity), f"Training {record['name']} #{record['number']}",
                                           run_record=record, activity=activity)

    # ------------------------------------------------------------------------------------------------------------ one run
    async def execute(self, identity):
        record = self.read(identity)
        directory = self.directory(identity)
        output = directory / "output"
        started = time.perf_counter()
        ending = {}
        try:
            output.mkdir(parents=True, exist_ok=True)
            (output / "progress.jsonl").unlink(missing_ok=True)
            (output / "answer.json").unlink(missing_ok=True)
            memory = int((record.get("estimate") or {}).get("memory") or 2 * GB)
            alone = bool(record.get("alone"))
            async with self.queue.slot(owner=identity, label=f"{record['name']} #{record['number']}", interactive=alone, memory=memory, gpu=self.card_of(record),
                                       on_wait=lambda position: self.queued(identity, position)):
                self.update(identity, state="preparing", stage="data", startedAt=self.read(identity).get("startedAt") or int(time.time() * 1000), queue=None)
                ready = await self.ready_data(identity)
                job = await self.job(identity, ready)
                self.update(identity, state="running", stage="compute")
                answer = await self.compute(identity, job, ready)
            ending = self.finish(identity, answer, started)
        except asyncio.CancelledError:
            stopped_by_hand = identity in self.stopping
            current = self.read(identity)
            done = len(current.get("windows") or [])
            ending = {"state": "cancelled" if stopped_by_hand else "interrupted", "message": "Stopped by hand." if stopped_by_hand else "Quant stopped; it continues from the windows done.",
                      "stopped": {"window": done + 1 if done < (current.get("windowsTotal") or done + 1) else done, "at": int(time.time() * 1000), "auto": not stopped_by_hand}}
        except ContractError as error:
            failure = error.details.get("failure") if isinstance(error.details, dict) and isinstance(error.details.get("failure"), dict) else None
            failure = failure or {"stage": self.read(identity)["stage"], "title": "stopped", "message": error.message}
            ending = {"state": "failed", "message": failure["message"], "failure": self.failure_of(identity, failure), "error": {"stage": failure.get("stage"), **error.to_dict()}}
        except Exception as error:  # a background run must end and say what broke
            import logging
            logging.getLogger(__name__).exception("Model run failed (%s)", identity)
            message = f"Quant hit an internal error ({type(error).__name__}) and stopped this run; the details are in Quant's log."
            ending = {"state": "failed", "message": message, "failure": self.failure_of(identity, {"stage": "compute", "title": "stopped", "message": message}),
                      "error": {"stage": "compute", "code": "internal_error", "message": message}}
        finally:
            self.tasks.pop(identity, None)
            self.stopping.discard(identity)
            if ending:
                record = self.read(identity)
                self.save(record, **{**ending, "endedAt": int(time.time() * 1000), "progress": {**(record.get("progress") or {}), "fraction": 1.0}
                                     if ending["state"] == "completed" else record.get("progress")})
                if ending["state"] != "interrupted":
                    await self.notify(identity)

    def card_of(self, record):
        """The card a network trains on and the graphics memory the check estimated for it ({index, memory}), or None for the CPU."""
        device = str(({**(record.get("settings") or {}).get("params", {}), **(record.get("overrides") or {})}).get("nn_device") or "")
        if not device.startswith("cuda:"):
            return None
        return {"index": int(device.split(":")[1]), "memory": int((record.get("estimate") or {}).get("vram") or 0)}

    def queued(self, identity, position):
        """Where a waiting run stands: its place, how long it takes, and about when it starts (the soonest running run's time left, then
        the estimates of the runs before it, shared over the runs that go at once)."""
        record = self.read(identity)
        takes = (record.get("estimate") or {}).get("seconds")
        waiting = [self.records.find(RUN, owner) for owner in self.queue.waiting_owners()[:max(position - 1, 0)]]
        before = sum(((item or {}).get("estimate") or {}).get("seconds") or 0 for item in waiting if item and is_model_run(item))
        running = [self.records.find(RUN, item["owner"]) for item in self.queue.view()["running"]]
        now = time.time() * 1000
        left = [max(0.0, (((item or {}).get("estimate") or {}).get("seconds") or 0) - (now - ((item or {}).get("startedAt") or now)) / 1000)
                for item in running if item and is_model_run(item)]
        starts = (min(left) if left else 0) + before / max(1, self.queue.cpus)
        self.save(record, stage="queued", queue={"position": position, "takes": takes, "startsIn": round(starts)})

    @staticmethod
    def frozen_data(ready):
        return {**{key: ready[key] for key in ("key", "fingerprint", "dataFrom", "dataTo", "names", "requested", "notes", "frozenAt")},
                "annualization": ready["info"]["annualization"], "bars":ready.get("bars")}

    async def ready_data(self, identity):
        """The run's frozen data: frozen from its settings on its first start. Data recycled since is frozen again and used only when it is
        the same (its fingerprint; a run kept from before fingerprints counts as different)."""
        record = self.read(identity)
        data = record["data"]
        if data is None:
            ready = await self.data.prepare(record["settings"]["data"])
            self.update(identity, data=self.frozen_data(ready))
            return ready
        ready = await asyncio.to_thread(self.data.ready, data["key"])
        if ready is None:
            ready = await self.data.prepare(record["settings"]["data"])
            if ready["fingerprint"] != data.get("fingerprint"):
                raise ContractError("unavailable", "This run's frozen data is no longer available.")
            self.update(identity, data={**data, "key": ready["key"]})
        elif "fingerprint" in data and ready["fingerprint"] != data["fingerprint"]:
            raise ContractError("unavailable", "This run's frozen data is no longer available.")
        return ready

    async def job(self, identity, ready):
        record = self.read(identity)
        directory = self.directory(identity)
        params = {**record["settings"]["params"], **(record.get("overrides") or {})}
        inputs, pins = await P.prepare_inputs(self, params.get("inputs") or [], ready["info"], directory / "inputs")
        if record.get("pins") is None:
            self.update(identity, pins=pins)
        held = P.held_out_of(record["settings"]["data"], day_ms)
        concurrency = 1 if record.get("alone") else max(1, self.queue.cpus)
        threads = max(1, (os.cpu_count() or 2) // concurrency)
        data = record.get("data") or {}
        # A held-out run inherits its source's last training day. Score the rest of the same frozen snapshot.
        cutoff = None
        if record["runKind"] != "heldout" and data.get("frozenAt") and data.get("dataTo") is not None and data["dataTo"] < ready["dataTo"]:
            cutoff = data["dataTo"]
        job = {"mode": record["runKind"] if record["runKind"] in ("combine",) else "train", "output": str(directory / "output"), "cache": str(directory / "windows"),
               "data": {key: ready[key] for key in ("key", "barsPath", "info", "extrasPath")}, "inputs": inputs, "settings": P.settings_of(params), "params": params,
               "threads": threads, "run": record["settings"]["run"], "number": record["number"], "heldOutFrom": held["from"] if held else None, "kind": record["runKind"],
               "keepDone": bool(record.get("windows")) or record["runKind"] == "heldout", "memoryEstimate": (record.get("estimate") or {}).get("memory"),
               "cutoff": cutoff}
        job["concepts"] = await asyncio.to_thread(self.concept_file, params, ready["info"], directory / "inputs")
        if params.get("algorithm") in ("gats", "igmtf", "add", "hist") and params.get("nn_base"):
            base = self.read(params["nn_base"])
            job["baseRun"] = {"id": base["id"], "algorithm": "GRU" if (base["settings"]["params"].get("algorithm") == "gru") else "LSTM", "windows": str(self.directory(base["id"]) / "windows")}
        snapshot = record.get("codeSnapshot")
        if record["mode"] == "standard":
            job["pipelinePath"] = str(directory / "source" / P.STANDARD.name)
        if snapshot:
            common = (record.get("common") or {}).get("snapshotPath")
            job["code"] = {"source": snapshot["source"], "entry": snapshot["entry"], "own": record["mode"] == "code",
                           "common": common, "digest": snapshot["digest"], "roots": [snapshot["source"], *([common] if common else [])]}
        if record["runKind"] == "combine":
            sources = [self.read(item) for item in record["combinedFrom"]]
            job["sources"] = [str(self.directory(item["id"]) / "output") for item in sources]
            job["sourceWindows"] = [json.loads((self.directory(sources[0]["id"]) / "windows" / str(window["k"]) / "window.json").read_text(encoding="utf-8"))
                                    for window in sources[0]["windows"]]
        self.update(identity, threads=threads)
        return job

    async def compute(self, identity, job, ready):
        record = self.read(identity)
        output = Path(job["output"])
        progress_file = output / "progress.jsonl"
        stop = asyncio.Event()

        async def follow():
            seen = 0
            while not stop.is_set():
                with contextlib.suppress(asyncio.TimeoutError):
                    await asyncio.wait_for(stop.wait(), 1)
                if progress_file.is_file():
                    lines = progress_file.read_text(encoding="utf-8").splitlines()
                    fresh = [json.loads(line) for line in lines[seen:] if line.strip()]
                    seen = len(lines)
                    if fresh:
                        self.follow(identity, fresh)

        follower = asyncio.create_task(follow())
        try:
            answer = await self.engine.run_model(job, self.directory(identity) / "engine", context={"objectId": record["objectId"], "runId": identity})
        finally:
            stop.set()
            await follower
        if progress_file.is_file():
            lines = progress_file.read_text(encoding="utf-8").splitlines()
            self.follow(identity, [json.loads(line) for line in lines if line.strip()], replay=True)
        if not answer.get("ok"):
            failure = answer.get("failure") or {"stage": "compute", "title": "stopped", "message": "The run failed."}
            raise ContractError("invalid_input" if failure.get("stage") in ("code", "settings", "data") else "unavailable", failure["message"], {"failure": failure})
        return answer

    def follow(self, identity, lines, *, replay=False):
        """The worker's progress lines into the record: the window training now (and its round), each finished window's numbers."""
        record = self.read(identity)
        windows = {item["k"]: item for item in record.get("windows") or []}
        progress = dict(record.get("progress") or {})
        total = record.get("windowsTotal")
        features = record.get("features")
        # A held-out validation counts only the windows it retrains: the source run's windows are copied, not trained.
        cached = len(self.read(record["of"]).get("windows") or []) if record.get("runKind") == "heldout" and record.get("of") else 0
        for line in lines:
            if line["stage"] == "inputs" and line.get("state") == "done":
                total, features = (line["windows"] - cached if "windows" in line else total), line.get("features", features)
                progress.update(stage="windows")
            elif line["stage"] == "inputs":
                progress.update(stage="inputs")
            elif line["stage"] == "window" and line.get("state") == "running":
                total = line["windows"] - cached if "windows" in line else total
                if progress.get("window") != line["window"] - cached:
                    progress.pop("curve", None)
                progress.update(stage="window", window=line["window"] - cached, round=line.get("round"), rounds=line.get("rounds"), windowFraction=line.get("fraction", 0.0))
                if line.get("epochs"):
                    progress.update({key: line.get(key) for key in ("epoch", "epochs", "trainLoss", "validLoss", "bestLoss", "bestEpoch", "pretrain", "pretrains")})
                    if line.get("epoch") and line.get("validLoss") is not None:
                        curve = progress.setdefault("curve", {"train": [], "valid": []})
                        for name, key in (("train", "trainLoss"), ("valid", "validLoss")):
                            curve[name] = ([*curve[name], line.get(key)])[-1000:]
            elif line["stage"] == "window" and line.get("state") == "done":
                windows[line["window"]] = {key: line.get(key) for key in ("trainIc", "validIc", "testIc", "best", "rows", "secs")} | {"k": line["window"]}
            elif line["stage"] == "numbers":
                progress.update(stage="numbers")
        done = len(windows)
        if total:
            secs = [item["secs"] for item in windows.values() if item.get("secs")]
            average = sum(secs) / len(secs) if secs else None
            current = progress.get("windowFraction") or 0.0
            progress["fraction"] = round(min(1.0, (done + (current if progress.get("stage") == "window" else 0)) / total), 3)
            progress["windows"] = total
            progress["left"] = None if average is None else round(average * max(0, total - done - current))
        started = record.get("startedAt")
        progress["used"] = round((time.time() * 1000 - started) / 1000) if started else None
        ordered = [windows[k] for k in sorted(windows)]
        spans = self.window_spans(identity)
        for item in ordered:
            item.update(spans.get(item["k"], {}))
        self.save(record, windows=ordered, progress=progress, windowsTotal=total, features=features)
        if record.get("runKind") == "heldout" and record.get("of"):
            with contextlib.suppress(ContractError):
                source = self.read(record["of"])
                self.save(source, heldOut={**(source.get("heldOut") or {}), "runId": identity, "number": record["number"], "state": "running",
                                           "window": progress.get("window"), "windows": total})

    def window_spans(self, identity):
        out = {}
        folder = self.directory(identity) / "windows"
        for path in folder.glob("*/window.json") if folder.is_dir() else []:
            with contextlib.suppress(OSError, ValueError):
                found = json.loads(path.read_text(encoding="utf-8"))
                out[int(found["k"])] = {"train": found["train"], "valid": found["valid"], "test": found["test"]}
        return out

    def failure_of(self, identity, failure):
        """The failure as the table says it: in which window, why, the memory it needed and had, from where it can continue."""
        record = self.read(identity)
        done = len(record.get("windows") or [])
        return {"window": failure.get("window"), "title": failure.get("title", "stopped"), "detail": failure.get("detail") or failure.get("message"),
                "message": failure.get("message"), "need": failure.get("need"), "free": failure.get("free"), "at": int(time.time() * 1000), "stage": failure.get("stage"),
                "file": failure.get("file"), "line": failure.get("line"), "checkpoint": done or None, "resumeFrom": done + 1 if done else None,
                **({"epoch": failure["epoch"]} if failure.get("epoch") else {}),
                **{key: failure[key] for key in ("kind", "saved", "batch", "tryBatch") if failure.get(key) is not None}, "windowsTotal": record.get("windowsTotal")}

    def finish(self, identity, answer, started):
        record = self.read(identity)
        output = self.directory(identity) / "output"
        work = json.loads((output / "work.json").read_text(encoding="utf-8"))
        windows = {item["k"]: item for item in record.get("windows") or []}
        for row in work.get("windows") or []:
            windows[row["k"]] = {**windows.get(row["k"], {}), **row}
        ending = {"state": "completed", "stage": "done", "message": None, "seconds": round(time.perf_counter() - started, 2) + (record.get("seconds") or 0),
                  "computeSeconds": answer.get("seconds"), "numbers": work["numbers"], "checks": work["checks"], "baselines": work["baselines"],
                  "windows": [windows[k] for k in sorted(windows)], "perWindow": work["perWindow"], "reach": work.get("reach"), "horizon": work.get("horizon"), "features": len(work["features"]), "inputsUsed": work["inputs"],
                  "peakMemory": work.get("peakMemory"), "figureCount": work.get("figureCount"), "failure": None, "stopped": None,
                  "metrics": {"rankIc": work["numbers"].get("ric"), "t": work["numbers"].get("t"), "longShortNet": work["numbers"].get("lsNet")},
                  "data": {**(record.get("data") or {}), "dataFrom": work["dataFrom"], "dataTo": work["dataTo"],
                           **({"tested": work["tested"]} if work.get("tested") else {})}}
        if record["runKind"] == "heldout" and record.get("of"):
            source = self.read(record["of"])
            self.save(source, heldOut={"runId": identity, "number": record["number"], "state": "completed", "numbers": work["numbers"]})
            ending["heldOut"] = {"of": source["id"], "ofNumber": source["number"], "numbers": work["numbers"]}
            # Its score covers only the windows testing in the held-out period; the ones before are the source run's.
            kept = {row["k"] for row in work.get("windows") or []}
            ending["windows"] = [item for item in ending["windows"] if item["k"] in kept]
            ending["windowsTotal"] = len(ending["windows"])
        return ending

    async def notify(self, identity):
        record = self.read(identity)
        if record.get("batch"):
            others = [self.read(item) for item in (self.records.find(BATCH, record["batch"]) or {}).get("runs", []) if self.owns(item)]
            if any(item["state"] not in TERMINAL for item in others):
                return
            done = sum(1 for item in others if item["state"] == "completed")
            title = f"Quant · {record['name']}: batch finished"
            body = f"{done} of {len(others)} groups trained" + ("" if done == len(others) else f"; {len(others) - done} stopped or failed")
            key = f"model-batch:{record['batch']}"
        else:
            numbers = record.get("numbers") or {}
            title = f"Quant · {record['name']} #{record['number']} " + {"completed": "finished", "failed": "failed"}.get(record["state"], "stopped")
            body = (f"Test rank IC {numbers['ric']:.4f}, t {numbers['t']:.2f}" if record["state"] == "completed" and numbers.get("ric") is not None and numbers.get("t") is not None
                    else (record.get("failure") or {}).get("detail") or record.get("message") or "")
            key = f"model-run:{identity}:{record['state']}"
        await self.notifications.deliver("longRuns", key, title, body)   # a send the platform refuses is retried and kept by Notifications

    # ------------------------------------------------------------------------------------------------------------ stop, resume, queue
    async def stop(self, identity):
        """Stop a run (or every unfinished run of a batch); finished windows stay, and it can be resumed from there."""
        targets = [identity]
        batch = self.records.find(BATCH, identity)
        if batch:
            targets = batch["runs"]
        for target in targets:
            record = self.read(target)
            task = self.tasks.get(target)
            if record["state"] in TERMINAL or task is None:
                continue
            self.stopping.add(target)
            self.save(record, state="cancel_requested")
            task.cancel()
        await asyncio.gather(*[self.tasks[target] for target in targets if target in self.tasks], return_exceptions=True)
        return summary(self.read(targets[0])) if not batch else {"batch": batch["id"], "runs": [summary(self.read(target)) for target in targets]}

    async def resume(self, identity, args=None):
        """Continue a stopped, interrupted or failed run from the windows it finished; `alone` runs it by itself with every core,
        `override` changes settings for the windows still to train (recorded on the run)."""
        args = args or {}
        record = self.read(identity)
        if record["state"] not in {"failed", "cancelled", "interrupted"}:
            raise ContractError("conflict", "Only a stopped or failed run can be resumed.")
        if identity in self.tasks:
            raise ContractError("conflict", "This run is already running.")
        if "epoch" in args:
            params = record["settings"]["params"]
            current = (record.get("progress") or {}).get("epoch")
            if params.get("algorithm") not in A.NEURAL_IDS or args["epoch"] != current:
                raise ContractError("invalid_input", f"epoch must identify the saved training checkpoint ({current}).")
        overrides = {**(record.get("overrides") or {}), **(args.get("override") or {})}
        if args.get("override"):
            obj = self.store.read(record["objectId"])
            P.values_of(self.store, obj, {**record["settings"]["params"], **overrides})
        self.save(record, state="queued", stage="queued", failure=None, stopped=None, message=None, error=None, endedAt=None, overrides=overrides,
                  **call_attribution(),
                  alone=bool(args.get("alone")), resumedAt=int(time.time() * 1000), resumedAfterClose=False,
                  **({"overrideFrom": len(record.get("windows") or []) + 1} if args.get("override") else {}))
        self._launch(identity)
        return summary(self.read(identity))

    async def run_next(self, identity):
        record = self.read(identity)
        if record["state"] != "queued" or not await self.queue.promote(identity):
            raise ContractError("conflict", "Only a run waiting in the queue can go next.")
        return summary(self.read(identity))

    async def remove(self, identity):
        """Take a run that has not started out of the queue; it leaves the table."""
        record = self.read(identity)
        if record["state"] != "queued":
            raise ContractError("conflict", "Only a run waiting in the queue can be removed; stop a running one.")
        task = self.tasks.get(identity)
        if task is not None:
            self.stopping.add(identity)
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)
        self.save(self.read(identity), removed=True, state="cancelled")
        return {"runId": identity, "removed": True}

    def set_concurrency(self, count):
        cpus = os.cpu_count() or 1
        if not 1 <= int(count) <= cpus:
            raise ContractError("invalid_input", f"Run 1 to {cpus} at once.")
        self.queue.cpus = int(count)
        self.records.put(SETTINGS, {"id": "models", "concurrency": int(count)})
        return self.running_view()

    def recover(self, workspace_id=None):
        """Runs the application stopped while they ran or waited continue by themselves, from the windows they finished. A process that was killed
        (no time to mark its runs interrupted) leaves them queued or running with no task in this process: they are the same."""
        rows = self.records.db.execute("SELECT content FROM records WHERE kind='qrun' AND coalesce(json_extract(content,'$.modelRun'), json_extract(content,'$.pipeline'))=1 AND json_extract(content,'$.state') IN ('interrupted','queued','preparing','running')").fetchall()
        resumed = []
        for row in rows:
            record = json.loads(row[0])
            if record.get("removed") or record["id"] in self.tasks or workspace_id is not None and record["workspaceId"] != workspace_id:
                continue
            if record.get("stopped") and not record["stopped"].get("auto", True):
                continue
            self.save(record, state="queued", stage="queued", message="Continuing from the windows done.", endedAt=None, resumedAfterClose=True)
            try:
                self._launch(record["id"])
            except ContractError:
                self.save(self.read(record["id"]), state="interrupted")
                continue
            resumed.append(record["id"])
        self.recovered.add(workspace_id)
        return resumed

    # ------------------------------------------------------------------------------------------------------------ reading
    def list(self, object_id, since=None):
        """The experiment table: every run of the model, newest first, with the batches they belong to and what runs now."""
        self.model(object_id)
        # Once a process: a run made a moment ago is in the records before its task is (it read "Resumed after Quant closed").
        if None not in self.recovered:
            with contextlib.suppress(Exception):
                workspace = self.store.workspace()
                if workspace not in self.recovered:
                    self.recover(workspace)
        items = [summary(record) for record in self.of_object(object_id, since=since)]
        batches = [batch for batch in self.records.list(BATCH) if batch["objectId"] == object_id]
        return {"items": items, "batches": [{"id": batch["id"], "title": batch["title"], "started": batch["createdAt"], "vary": batch["vary"], "runs": batch["runs"]}
                                            for batch in sorted(batches, key=lambda item: -item["createdAt"])],
                "running": self.running_view(), "now": int(time.time() * 1000), "complete": since is None}

    def experiment(self, identity):
        record = self.read(identity)
        return {**summary(record), "settings": record["settings"], "pins": record.get("pins"), "inputsUsed": record.get("inputsUsed"), "data": record.get("data"),
                "threads": record.get("threads"), "figureCount": record.get("figureCount"), "codeSnapshot": record.get("codeSnapshot")}

    def results(self, identity, compare=None):
        """Results of one run: every section's tables and the keys of its figures; `compare` adds another run's numbers beside them."""
        record = self.read(identity)
        path = self.directory(identity) / "output" / "results.json"
        if record["state"] != "completed" or not path.is_file():
            raise ContractError("not_found", "This run has no Results yet.")
        results = json.loads(path.read_text(encoding="utf-8"))
        if record.get("heldOut") and record["heldOut"].get("numbers"):
            # A source run names its held-out run; the held-out run itself carries {of, ofNumber, numbers} and is its own number.
            results.setdefault("robustness", {})["heldOut"] = {"number": record["heldOut"].get("number", record["number"]), "numbers": record["heldOut"]["numbers"],
                                                               "shortSample": bool(record["heldOut"]["numbers"].get("shortSample"))}
        elif record.get("heldOut"):
            results.setdefault("robustness", {})["heldOut"] = {"state": record["heldOut"].get("state"), "window": record["heldOut"].get("window"),
                                                               "windows": record["heldOut"].get("windows"), "number": record["heldOut"].get("number")}
        if record.get("portfolio"):
            results.setdefault("returns", {})["portfolio"] = record["portfolio"]
        source = (self.directory(identity) / "source" / P.STANDARD.name if record["mode"] == "standard"
                  else Path(record["codeSnapshot"]["source"]) / record["codeSnapshot"]["entry"])
        declarations = P.read_parameters(source.read_text(encoding="utf-8"), source.name)["parameters"] if source.is_file() else []
        params = record["settings"]["params"]
        parameter_labels = {item["name"]: item["label"] for item in declarations
                            if all((params.get(key) in wanted if isinstance(wanted, list) else params.get(key) == wanted)
                                   for key, wanted in (item.get("showIf") or {}).items())}
        out = {"parameterLabels": parameter_labels, "run": summary(record), "results": results, "settings": record["settings"], "pins": record.get("pins"), "data": record.get("data"),
               "environment": self.environment_of(record)}
        if compare:
            other = self.read(compare)
            out["compare"] = {"run": summary(other), "settings": other["settings"]}
        return out

    def environment_of(self, record):
        """What the run ran in: threads and memory, and for a neural network the deep learning environment's PyTorch build and the device it trained on."""
        out = {"threads": record.get("threads"), "peakMemory": record.get("peakMemory"), "device": "CPU"}
        algorithm = (record["settings"]["params"] or {}).get("algorithm")
        if algorithm in A.NEURAL_IDS:
            from .packages import DEEP_VARIANTS
            installed = self.engine.packages.state.get("deep") or {}
            device = (record["settings"]["params"] or {}).get("nn_device") or "cpu"
            cards = {f"cuda:{card['index']}": card for card in self.engine.packages.devices()["nvidia"]}
            out.update(name="Deep learning", torch=DEEP_VARIANTS.get(installed.get("variant"), {}).get("torch"), qlib="0.9.8", python="3.12",
                       device=f"{cards[device]['name']}, {cards[device]['memoryTotal'] / 2**30:.0f} GB" if device in cards else f"CPU, {record.get('threads') or 1} threads")
        return out

    def figures(self, identity, keys=None, compare=None):
        """The run's figures (one set for the whole run), by key; `compare` adds another run's same lines, dashed (role compare)."""
        record = self.read(identity)
        path = self.directory(identity) / "output" / "figures.json"
        work = {}
        if keys and EPOCH_KEY in keys:
            work[EPOCH_KEY] = epoch_figure(summary(record))
            keys = [key for key in keys if key != EPOCH_KEY]
            if not keys:
                return {"runId": identity, "figures": work}
        if keys and WORK_KEY in keys:
            # The Work figure is made from the run's summary, so it is there while the run is going, window by window.
            work[WORK_KEY] = work_figure(summary(record), summary(self.read(compare)) if compare else None)
            keys = [key for key in keys if key != WORK_KEY]
            compare = compare if keys else None
            if not keys:
                return {"runId": identity, "figures": work}
        if not path.is_file():
            raise ContractError("not_found", "This run has no figures yet.")
        found = json.loads(path.read_text(encoding="utf-8"))
        chosen = {key: value for key, value in found.items() if not keys or key in keys}
        if compare:
            other_path = self.directory(compare) / "output" / "figures.json"
            other = json.loads(other_path.read_text(encoding="utf-8")) if other_path.is_file() else {}
            number = self.read(compare)["number"]
            for key, figure in chosen.items():
                if key not in other:
                    continue
                for trace in other[key]["data"]:
                    if (trace.get("meta") or {}).get("role") in ("main", "drawdown") and trace.get("type") in ("scatter", None):
                        figure["data"].append({**trace, "name": f"{trace.get('name') or ''} #{number}".strip(), "meta": {"role": "compare", "key": trace["meta"]["key"] + ":compared"}})
        return {"runId": identity, "figures": {**work, **chosen}}

    async def portfolio(self, identity):
        """Qlib's portfolio backtest of a finished run's predictions (top N by the prediction, N dropped a day, after cost), on request: it is an extra result of
        the run, in its Returns, and not an experiment of its own."""
        record = self.read(identity)
        if record["state"] != "completed":
            raise ContractError("conflict", "Only a completed run can be backtested as a portfolio.")
        if (record.get("portfolio") or {}).get("state") == "running":
            raise ContractError("conflict", "The portfolio backtest of this run is already running.")
        self.save(record, portfolio={"state": "running", "at": int(time.time() * 1000)})
        self.ctx.start(self._portfolio(identity), f"Portfolio backtest · {record['name']}", run_record={**record, **call_attribution()})
        return {"runId": identity, "state": "running"}

    async def _portfolio(self, identity):
        record = self.read(identity)
        folder = self.directory(identity) / "portfolio"
        try:
            shutil.rmtree(folder, ignore_errors=True)
            folder.mkdir(parents=True)
            ready = await self.ready_data(identity)
            job = {"mode": "portfolio", "output": str(folder), "runOutput": str(self.directory(identity) / "output"), "run": record["settings"]["run"],
                   "data": {key: ready[key] for key in ("key", "barsPath", "info", "extrasPath")}}
            answer = await self.engine.run_model(job, folder / "engine", context={"objectId": record["objectId"], "runId": identity})
            if not answer.get("ok"):
                raise ContractError("unavailable", (answer.get("failure") or {}).get("message", "The backtest failed."))
            found = json.loads((folder / "portfolio.json").read_text(encoding="utf-8"))
            figures_path = self.directory(identity) / "output" / "figures.json"
            figures = json.loads(figures_path.read_text(encoding="utf-8"))
            figures["model.returns.portfolio"] = found.pop("figure")
            figures_path.write_text(json.dumps(figures), encoding="utf-8")
            self.save(self.read(identity), portfolio={"state": "done", "risk": found["risk"], "days": found["days"], "top": found["top"], "drop": found["drop"]})
        except Exception as error:  # noqa: BLE001 - the Returns tab says why
            self.save(self.read(identity), portfolio={"state": "failed", "message": getattr(error, "message", None) or str(error)[:300]})
            activity = ACTIVITY.get()
            if activity is not None:
                activity.outcome = "failed"

    def compare_figure(self, identities):
        """The test IC window by window of up to four runs, the first in the body colour."""
        if not 1 <= len(identities) <= 4:
            raise ContractError("invalid_input", "Compare one to four runs.")
        return compare_figure([summary(self.read(item)) for item in identities])

    def code(self, identity):
        """The code a run ran: its snapshot's files, or the standard pipeline as it was then."""
        record = self.read(identity)
        source = Path(record["codeSnapshot"]["source"]) if record["mode"] == "code" else self.directory(identity) / "source"
        files = [{"file": path.relative_to(source).as_posix(), "text": path.read_text(encoding="utf-8", errors="replace")} for path in sorted(source.rglob("*.py"))] if source.is_dir() else []
        if record.get("mode") == "standard":
            files = [item for item in files if item["file"] == "model_standard.py"]
        return {"files": files, "pipeline": record.get("pipeline") if isinstance(record.get("pipeline"), dict) else None}

    def log(self, identity):
        """The end of what the run's process wrote."""
        self.read(identity)
        folder = self.directory(identity) / "output"
        engine = self.directory(identity) / "engine"

        def tail(name):
            for base in (folder, engine):
                path = base / name
                if path.is_file():
                    return "\n".join(path.read_text(encoding="utf-8", errors="replace").splitlines()[-400:])
            return ""
        return {"stdout": tail("stdout.log"), "stderr": tail("stderr.log")}

    def held_out_windows(self, source):
        """How many windows a held-out validation retrains: the walk-forward windows of this data (the same layout the run uses) beyond the ones the
        source run already has. The confirm and the run's own progress count the same windows."""
        from ..native import model_data as D
        key = (source.get("data") or {}).get("key")
        done = source.get("windows") or []
        bars = self.data.directory(key) / "bars.parquet" if key else None
        if bars is None or not done or not bars.is_file():
            data = source["settings"]["data"]
            months = max(1, round((day_ms(data["until"], True) - day_ms(data["heldOutFrom"], False)) / 86_400_000 / 30.4))  # the data is gone: the held-out span over the schedule
            return max(1, -(-months // int(source["settings"]["params"].get("every_months") or 3)))
        labels = np.sort(pd.read_parquet(bars, columns=["label"])["label"].unique())
        split = {**P.settings_of({**source["settings"]["params"], **(source.get("overrides") or {})})["split"],
                 "firstTest": datetime.fromtimestamp(done[0]["test"]["from"] / 1000, UTC).strftime("%Y-%m-%d")}
        ahead = (source.get("reach") or {}).get("ahead") or int(source.get("horizon") or 5) + 1
        return max(1, len(D.layout(labels, split, ahead, warmup=0)) - len(done))

    def held_out_plan(self, identity):
        """What validating on the held-out period does: the period, how many windows it retrains, about how long, and where this data's
        held-out period has been used before, in any model."""
        source = self.read(identity)
        data = source["settings"]["data"]
        if not data.get("heldOutFrom"):
            raise ContractError("invalid_input", "Set where the held-out period starts in the run settings first.")
        start, end = day_ms(data["heldOutFrom"], False), day_ms(data["until"], True)
        windows = self.held_out_windows(source)
        # what a window took in the run being scored, the whole run over its windows (data, fitting, recording), not only the fit
        trained = len(source.get("windows") or [])
        seconds = [source["seconds"] / trained] if source.get("seconds") and trained else [item.get("secs") for item in source.get("windows") or [] if item.get("secs")]
        identity_of = lambda settings: json.dumps({key: (settings.get(key) if key != "symbols" else sorted(str(item) for item in settings.get(key) or [])) for key in ("heldOutFrom", "index", "list", "symbols", "timeframe")}, sort_keys=True, default=str)  # noqa: E731
        mine = identity_of(data)
        uses = []
        rows = self.records.db.execute("""SELECT content FROM records WHERE kind='qrun' AND json_extract(content,'$.runKind')='heldout'
            """).fetchall()
        for row in rows:
            record = json.loads(row[0])
            if not is_model_run(record) or record.get("removed") or record["state"] != "completed" or identity_of(record["settings"]["data"]) != mine:
                continue
            of = self.records.find(RUN, record.get("of")) if record.get("of") else None
            uses.append({"objectId": record["objectId"], "objectName": record["name"], "number": record.get("ofNumber") or (of or {}).get("number") or record["number"],
                         "version": ((of or {}).get("version") or {}).get("version") if of else None})
        return {"from": start, "to": end, "windows": windows, "minutes": round(sum(seconds) / len(seconds) * windows / 60, 1) if seconds else None, "uses": uses}

    async def values(self, identity, date=None):
        """One day of the run's test period: each name's prediction, its rank, the return that followed, and each input's contribution
        (computed on demand, kept for the next look). The same day asked for twice at once is made once."""
        tasks = self.__dict__.setdefault("_values_tasks", {})
        key = (identity, None if date is None else int(date))
        task = tasks.get(key)
        if task is None:
            task = asyncio.ensure_future(self._values(identity, date))
            tasks[key] = task
            task.add_done_callback(lambda _task, key=key: tasks.pop(key, None))
        return await asyncio.shield(task)

    async def _values(self, identity, date=None):
        record = self.read(identity)
        if record["state"] != "completed":
            raise ContractError("conflict", "Only a completed run has values to show.")
        labels = json.loads((self.directory(identity) / "output" / "pred.json").read_text(encoding="utf-8"))["labels"] if (self.directory(identity) / "output" / "pred.json").is_file() else None
        # Without a day the engine picks the last test day whose label every name has realised (native default_day), kept as "first".
        cached = self.directory(identity) / "values" / ("first.json" if date is None else f"{int(date)}.json")
        if cached.is_file():
            return json.loads(cached.read_text(encoding="utf-8"))
        ready = await self.ready_data(identity)
        # The job's inputs are exported from the library once for a run: a run that is complete does not change, and a day's values only change its date.
        kept = self.__dict__.setdefault("_values_jobs", {})
        if identity not in kept or kept[identity][0] != ready.get("key"):
            kept[identity] = (ready.get("key"), await self.job(identity, ready))
        job = copy.deepcopy(kept[identity][1])
        (self.directory(identity) / "values" / "work").mkdir(parents=True, exist_ok=True)
        if not any(other[0] == identity and other != (identity, None if date is None else int(date)) for other in self.__dict__.get("_values_tasks", {})):
            (self.directory(identity) / "values" / "work" / "progress.jsonl").unlink(missing_ok=True)  # what the screen shows is this look's
        job.update(mode="values", date=None if date is None else int(date), keepDone=True, runOutput=str(self.directory(identity) / "output"), output=str(self.directory(identity) / "values" / "work"))
        answer = None
        if record.get("mode") != "code":
            # A process kept for values: the run's inputs and models stay loaded, so the next day is read, not built (a run written as code is run alone).
            try:
                answer = await self.values_engine((job.get("params") or {}).get("algorithm") in A.NEURAL_IDS).call("model_values", job, seconds=WARM_SECONDS)
            except ContractError as error:
                if error.code != "unavailable":
                    raise
        if answer is None:
            answer = await self.engine.run_model(job, self.directory(identity) / "values-engine", context={"objectId": record["objectId"], "runId": identity})
        if not answer.get("ok"):
            raise ContractError("unavailable", answer["failure"]["message"], {"failure": answer["failure"]})
        answer["run"] = record["number"]
        answer["dates"] = {"first": labels[0], "last": labels[-1]} if labels else None
        cached.parent.mkdir(parents=True, exist_ok=True)
        cached.write_text(json.dumps(answer), encoding="utf-8")
        return answer

    def values_engine(self, deep):
        """The process that keeps one run's values ready: the research environment's, or the deep learning one's for a network (it needs PyTorch)."""
        from .factor_engine import FactorEngine
        kept = self.__dict__.setdefault("_values_engines", {})
        if deep not in kept:
            kept[deep] = FactorEngine(self.ctx, self.engine.packages, self.records.root / ("values-engine-deep" if deep else "values-engine"), deep=deep)
        return kept[deep]

    def values_progress(self, identity):
        """Where a day's values are: building the run's inputs (the first look at a run only), then reading the day."""
        path = self.directory(identity) / "values" / "work" / "progress.jsonl"
        last = None
        with contextlib.suppress(OSError, ValueError):
            for line in path.read_text(encoding="utf-8").splitlines():
                last = json.loads(line)
        if last is None:
            return {"stage": "starting"}
        return {"stage": "inputs" if last.get("stage") == "inputs" and last.get("state") == "running" else "reading"}

    def compare(self, identities):
        """Several runs side by side: what settings differ, the key numbers, and each window's test IC together."""
        records = [self.read(identity) for identity in identities]
        if len(records) < 2:
            raise ContractError("invalid_input", "Compare at least two runs.")
        settings = [flat({"params": record["settings"]["params"], "run": record["settings"]["run"], "data": record["settings"]["data"]})
                    for record in records]
        keys = sorted(set().union(*settings))
        differ = [{"what": key, "values": [item.get(key) for item in settings]} for key in keys if len({json.dumps(item.get(key), sort_keys=True, default=str) for item in settings}) > 1]
        numbers = [summary(record)["metrics"] for record in records]
        traces = []
        for index, record in enumerate(records):
            windows = record.get("windows") or []
            traces.append({"type": "scatter", "mode": "lines+markers", "x": [item["k"] for item in windows], "y": [item.get("testIc") for item in windows],
                           "name": f"#{record['number']}", "meta": {"role": "main" if index == 0 else "compare", "key": f"run:{record['number']}"}})
        figure = {"data": traces, "layout": {"meta": {"key": "model.compare.windows"}, "xaxis": {"title": {"text": "Window"}}, "yaxis": {"title": {"text": "Test rank IC"}},
                                             "showlegend": True}}
        return {"runs": [summary(record) for record in records], "settings": differ, "numbers": numbers, "figure": figure}

    async def combine(self, identities, args=None):
        """A new experiment: the mean of these runs' predictions, each ranked within its day first (Qlib's average ensemble); it can be
        published like any run. The runs must share the data and the windows."""
        records = [self.read(identity) for identity in identities]
        if len(records) < 2 or any(record["state"] != "completed" for record in records):
            raise ContractError("invalid_input", "Combine two or more completed runs.")
        shapes = [json.dumps(record["settings"]["data"], sort_keys=True) for record in records]
        if len(set(shapes)) > 1:
            first = records[0]
            other = next(record for record, shape in zip(records, shapes, strict=True) if shape != shapes[0])

            def named(record):
                end = (record.get("data") or {}).get("dataTo") or (record["settings"]["data"] or {}).get("until")
                stamp = datetime.fromtimestamp(end / 1000, UTC).strftime("%b %-d") if isinstance(end, (int, float)) and os.name != "nt" else (
                    datetime.fromtimestamp(end / 1000, UTC).strftime("%b %d").replace(" 0", " ") if isinstance(end, (int, float)) else str(end))
                return f"{universe_text(record['settings']['data'])} to {stamp}"
            raise ContractError("invalid_input", f"#{first['number']} and #{other['number']} use different data ({named(first)} vs {named(other)}); combine runs on the same data.")
        spans = {json.dumps([item.get("test") for item in record.get("windows") or []], sort_keys=True) for record in records}
        if len(spans) > 1:
            raise ContractError("invalid_input", "Combine runs with the same windows (the same split settings).")
        obj = self.model(records[0]["objectId"])
        record = await self.create(obj, "combine", records[0]["settings"]["params"], values={"combined": [item["number"] for item in records]},
                                   estimate={"memory": GB, "seconds": 30}, windows_total=len(records[0]["windows"]), frozen=records[0]["data"],
                                   extra={"combinedFrom": [item["id"] for item in records], "combinedNumbers": [item["number"] for item in records]})
        self._launch(record["id"])
        return summary(record)

    async def held_out(self, identity):
        """Score a completed run once on the held-out period: the windows there trained on the same schedule, the score kept on both."""
        source = self.read(identity)
        if source["state"] != "completed" or source["runKind"] != "train":
            raise ContractError("invalid_input", "Validate a completed training run on the held-out period.")
        previous = self.records.held_out_for(identity)
        if previous is not None:
            return summary(previous)
        if not source["settings"]["data"].get("heldOutFrom"):
            raise ContractError("invalid_input", "Set where the held-out period starts in the run settings first.")
        obj = self.model(source["objectId"])
        record = await self.create(obj, "heldout", source["settings"]["params"], estimate=source.get("estimate"), frozen=source["data"], of=source)
        record = self.save(record, settings=copy.deepcopy(source["settings"]), mode=source["mode"], pipeline=source.get("pipeline"))
        cache = self.directory(source["id"]) / "windows"
        if cache.is_dir():
            shutil.copytree(cache, self.directory(record["id"]) / "windows", dirs_exist_ok=True)
        self.save(source, heldOut={"runId": record["id"], "number": record["number"], "state": "queued"})
        self._launch(record["id"])
        return summary(self.read(record["id"]))

    # ------------------------------------------------------------------------------------------------------------ settings
    def annotate(self, identity, args):
        record = self.read(identity)
        return summary(self.save(record, **{key: args[key] for key in ("note", "starred") if key in args}))

    async def apply(self, identity, expected_version=None):
        """Use this run's settings as the model's (the base of the next run): its values, data and run settings; a run written as code
        puts its code version back too."""
        record = self.read(identity)
        obj = self.model(record["objectId"])
        self.store.check_version(obj, expected_version)
        settings = {**obj["settings"], "params": copy.deepcopy(record["settings"]["params"]), "data": copy.deepcopy(record["settings"]["data"]),
                    "run": copy.deepcopy(record["settings"]["run"])}
        from .run_restore import restore_files

        snapshot = record.get("codeSnapshot")
        report = await restore_files(self.ctx, obj, snapshot["source"] if snapshot else None, record.get("common"), self.factors.common.root,
                                     expected=snapshot.get("hashes") if snapshot else None)
        self.store.check_version(self.store.read(obj["id"]), obj["version"])
        settings["mode"] = record["mode"]
        return {**self.store.detail(self.store.save(obj, settings=settings)), "restore": report}

    def reset(self, object_id):
        obj = self.model(object_id)
        return self.store.detail(self.store.save(obj, settings={**obj["settings"], "mode": "standard", "params": {}}))

    async def write_as_code(self, object_id):
        """The standard pipeline copied into the model's project as its entry, opened in Developer; the preparation keeps the same settings
        in the same places, and the model now runs its own copy."""
        obj = self.model(object_id)
        if obj["settings"].get("mode") == "code":
            raise ContractError("conflict", "This model is already written as code.")
        developer = await self.store.write_entry(obj, obj["entry"], P.STANDARD.read_text(encoding="utf-8"))
        record = self.store.save(obj, settings={**obj["settings"], "mode": "code"})
        record = self.records.put("qobject", {**record, "developer": developer})
        return self.store.detail(record)

    def test_predictions(self, record, destination):
        """This run's out-of-sample predictions (each window's test days, never the training fit) as a file of `datetime`, `instrument`, `value` rows,
        the shape a strategy reads a signal in; returns the number of rows written."""
        output = self.directory(record["id"]) / "output"
        if not (output / "pred.npy").is_file():
            raise ContractError("not_found", "This run's predictions were not kept; run the model again.")
        pred = np.load(output / "pred.npy")
        meta = json.loads((output / "pred.json").read_text(encoding="utf-8"))
        calendar = np.asarray(meta["labels"], dtype="int64")
        inside = np.zeros(len(calendar), dtype=bool)
        for entry in record.get("windows") or []:
            if entry.get("test"):
                inside |= (calendar >= entry["test"]["from"]) & (calendar <= entry["test"]["to"])
        if not inside.any():
            raise ContractError("invalid_input", "This run has no test window, so it has no out-of-sample predictions to trade.")
        pred = np.where(inside[:, None], pred, np.nan)
        ready = self.data.ready((record.get("data") or {}).get("key"))
        instruments = {item["column"]: item["instrumentId"] for item in (ready or {}).get("info", {}).get("instruments", [])}
        t, i = np.nonzero(np.isfinite(pred))
        columns = np.asarray(meta["columns"], dtype=object)
        table = pd.DataFrame({"datetime": calendar[t], "instrument": [instruments.get(name, name) for name in columns[i]], "value": pred[t, i].astype(float)})
        destination.parent.mkdir(parents=True, exist_ok=True)
        table.to_parquet(destination, index=False)
        return len(table)

    async def create_strategy(self, identity):
        """A new strategy that trades this run's predictions, through the strategy block's own entry (`ObjectStore.start_from`): the Top N preset with this
        run's data settings and held-out start. It trades the run's own out-of-sample predictions (the test windows), kept with the strategy as its signal,
        whether or not the model was published: started from a run, it stays on that run (choosing a library version is done in the strategy)."""
        record = self.read(identity)
        if record["state"] != "completed":
            raise ContractError("invalid_input", "Use a completed run.")
        kept = self.directory(record["id"]) / "signal-values.parquet"
        await asyncio.to_thread(self.test_predictions, record, kept)
        run = record["settings"]["run"]
        bps = float(run["cost"])
        # The run's cost of a trade is the strategy's whole cost of a trade (as from a factor run); a position is held the label's horizon, the bars the
        # report's long-short holds it. A model has no rate (its returns pay for themselves): the strategy takes its own default.
        costs = {"commissionBps": bps, "feePct": bps / 100, "makerPct": bps / 100, "halfSpreadBps": 0.0, "impact": 0.0, "borrowPct": 0.0}
        data = {key: value for key, value in record["settings"]["data"].items() if key != "untilLatest"}
        return await self.store.start_from("strategy", f"{record['name']} #{record['number']} strategy", data=data, preset="ranked",
                                           params={"signal": "", "rebalance_every": int(record["horizon"])}, run={"costs": costs},
                                           keep={"signalRun": {"param": "signal", "runId": record["id"], "model": record["name"], "number": record["number"], "kind": "model",
                                                               "path": str(kept)}})

    def active(self, workspace_id):
        return [self.read(identity) for identity in self.tasks if self.read(identity)["workspaceId"] == workspace_id]

    async def close(self):
        self.closed = True
        tasks = list(self.tasks.values())
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
