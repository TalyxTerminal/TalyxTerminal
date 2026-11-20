"""A factor's runs: one formula or the factor's code, its data and run settings, tested in seconds; the record keeps all of it.

Each run is numbered within its factor (#1, #2 …) and records the formula or a snapshot of the code with its difference from the run
before, the data and run settings and what changed from the run before, the checks, how long each step took and its progress. A sweep
is one run that tests several combinations; a held-out validation is one run that scores another run once on the held-out period.
Runs that a person waits for go first in Quant's queue.
"""
from __future__ import annotations

import asyncio
import contextlib
import copy
import difflib
import json
import shutil
import time
from pathlib import Path
from uuid import uuid4

import numpy as np
import pandas as pd
from talyx_sdk import ContractError

from ..native import factor_work, formula, metrics
from . import run_flow
from .factor_data import FactorData
from .factor_engine import MEMORY, FactorEngine
from .materials import plain
from .object_data import day_ms, resolve_until
from .project_paths import EXCLUDED, filesystem_path
from .run_queue import RunQueue
from .work_records import digest

RUN = "qrun"
TERMINAL = {"completed", "failed", "cancelled", "interrupted"}
KEEP_CROSS_SECTIONS = 30
SWEEP_LIMIT = 48
READ_LIMIT = 200
RESULT_TABS = ("returns", "ic", "industry", "style", "turnover", "costs", "event")
RESULTS_MEMORY = 1536 * 1024 ** 2  # the Results process for 500 names over ten years, about
BUILDING = set()  # (run id, sweep combination or None) whose Results this Host is building or has queued


def common_differences(left, right, run, other):
    """This run's numbers less the other's, each on the days both have a value. A rank IC and its t over another holding do not compare."""
    holding = run["settings"]["run"]["holding"]
    keys = ["longShortNet", "top", "bottom", "names"]
    if holding == other["settings"]["run"]["holding"]:
        keys.insert(0, "rankIc")
    result = {}
    for key in keys:
        matched = pd.concat([left[key], right[key]], axis=1, keys=["left", "right"], join="inner").dropna()
        if matched.empty:
            continue
        a, b = matched["left"], matched["right"]
        if key == "rankIc":
            ta, tb = metrics.newey_west_t(a, holding - 1), metrics.newey_west_t(b, holding - 1)
            if ta is not None and tb is not None:
                result["t"] = ta - tb
        if key == "names":
            result[key] = float(a.median() - b.median())
        elif key == "longShortNet":
            result[key] = metrics.annual_mean(a, run["data"]["annualization"]) - metrics.annual_mean(b, other["data"]["annualization"])
        else:
            result[key] = float(a.mean() - b.mean())
    sides = [result.pop(key) for key in ("top", "bottom") if key in result]
    if sides:
        result["turnover"] = sum(sides) / len(sides)
    return result


def results_of(record, cell=None):
    """Where a run's Results are: none (a held-out validation has none), notbuilt, queued, running, done, failed or interrupted, with
    the share done and each tab's state. Results that were queued or running when Quant closed read as interrupted."""
    if record.get("runKind") == "heldout":
        return {"state": "none", "progress": 0, "tabs": {}, "message": "A held-out validation has no Results of its own: its numbers sit beside the run it scored."}
    if record.get("state") != "completed":
        return {"state": "none", "progress": 0, "tabs": {}, "message": "Results follow once the run has finished."}
    found = (record.get("sweepResults") or {}).get(str(cell)) if cell is not None else record.get("results")
    if not found:
        message = "Choose a combination's Results to build them." if record.get("runKind") == "sweep" else "Not built yet."
        return {"state": "notbuilt", "progress": 0, "tabs": {}, "message": message}
    if found["state"] in {"queued", "running"} and (record["id"], cell) not in BUILDING:
        return {**found, "state": "interrupted", "message": "Quant closed before these Results were built; build them again."}
    return found


def published_version(record):
    """The library version this run was published as, by its number (the record keeps the version's id and item beside it), else None."""
    version = record.get("version")
    return version.get("version") if isinstance(version, dict) else version


def summary(record):
    """What lists show of a factor run: the runs table, the vs picker, the run head."""
    numbers = record.get("numbers")
    results = results_of(record)
    return {"id": record["id"], "objectId": record["objectId"], "objectKind": "factor", "number": record["number"], "kind": record["runKind"],
            "state": record["state"], "stage": record["stage"], "createdAt": record["createdAt"], "endedAt": record["endedAt"],
            "seconds": record.get("seconds"), "formula": record.get("formula"), "mode": record.get("mode"), "settings": record["settings"],
            "changes": record.get("changes", []), "numbers": numbers, "review": sum(1 for item in record.get("checks") or [] if item["state"] == "watch"),
            "failure": record.get("failure"), "progress": record.get("progress"), "starred": record["starred"], "note": record.get("note", ""),
            "of": record.get("of"), "ofNumber": record.get("ofNumber"), "sweep": record.get("sweep"), "version": published_version(record),
            "data": record.get("data"), "codeDigest": (record.get("code") or {}).get("digest"), "message": record.get("message"),
            "updatedAt": record.get("updatedAt"), "heldOut": record.get("heldOut"),
            "futureData": any(item["id"] == "future_data" and item["state"] == "watch" for item in record.get("checks") or []),
            "heldOutUses": record.get("heldOutUses"),
            "results": {key: results[key] for key in ("state", "progress", "tabs", "message", "seconds") if key in results},
            # Work's numbers are final; while the Results build in the background this is how far they are (0 to 1), else null.
            "resultsProgress": results["progress"] if results["state"] in {"queued", "running"} else None}


def changes_between(record, previous):
    """What differs from the run before: the formula or code, then each run and data setting, as {what, from, to}."""
    if previous is None:
        return []
    out = []
    if record.get("formula") != previous.get("formula") and record.get("formula") is not None and previous.get("formula") is not None:
        out.append({"what": "formula", "from": previous["formula"], "to": record["formula"]})
    if record.get("mode") != previous.get("mode"):
        out.append({"what": "written as", "from": previous.get("mode"), "to": record.get("mode")})
    # The run settings, the code's settings and the whole data settings, compared once by the shared flow; the names the page shows.
    sides = [{"run": item["settings"].get("run", {}), "params": item["settings"].get("params", {}), "data": item["settings"]["data"]} for item in (previous, record)]
    for change in run_flow.changes(*sides):
        section, _, key = change["what"].partition(".")
        out.append({**change, "what": key if section == "run" else ("code " if section == "params" else "data ") + key})
    if (record.get("code") or {}).get("digest") != (previous.get("code") or {}).get("digest") and record.get("code") and previous.get("code"):
        before = {item["path"]: item["sha256"] for item in previous["code"]["files"]}
        now = {item["path"]: item["sha256"] for item in record["code"]["files"]}
        changed = sorted(path for path in before.keys() | now.keys() if before.get(path) != now.get(path))
        out.append({"what": "code", "from": None, "to": ", ".join(changed) or "changed"})
    return out


def current_score(cell, record):
    """How many of the run's settings (and code parameters) this combination has as the settings column had them: the combination of the current settings scores highest."""
    settings = cell.get("settings") or {}
    run = record["settings"]["run"]
    score = sum(1 for key, value in run.items() if settings.get(key) == value)
    return score + sum(1 for key, value in (record["settings"].get("params") or {}).items() if (settings.get("params") or {}).get(key) == value)


def code_files(record):
    """The code the run was made with, file by file (text files up to 200 kB each), read from the run's snapshot; None for a run made from a formula."""
    code = record.get("code")
    if not code:
        return None
    out = []
    for item in code.get("files", []):
        path = Path(code["snapshotPath"]) / item["path"]
        if path.suffix in (".py", ".txt", ".md", ".toml", ".json", ".cfg", ".yaml", ".yml") and path.is_file() and path.stat().st_size <= 200_000:
            out.append({"file": item["path"], "text": path.read_text(encoding="utf-8", errors="replace")})
    return out


def code_diff(snapshot, files, previous):
    """Each changed file of the code against the run before, as a unified diff (text files; at most 400 lines each)."""
    if previous is None or not previous.get("code"):
        return None
    before = {item["path"]: item["sha256"] for item in previous["code"]["files"]}
    now = {item["path"]: item["sha256"] for item in files}
    out = []
    for path in sorted(before.keys() | now.keys()):
        if before.get(path) == now.get(path):
            continue
        status = "added" if path not in before else "removed" if path not in now else "changed"
        text = ""
        if path.endswith((".py", ".txt", ".md", ".toml", ".json", ".cfg", ".yaml", ".yml")):
            old = (Path(previous["code"]["snapshotPath"]) / path).read_text(encoding="utf-8", errors="replace").splitlines() if path in before else []
            new = (Path(snapshot) / path).read_text(encoding="utf-8", errors="replace").splitlines() if path in now else []
            lines = list(difflib.unified_diff(old, new, f"#{previous['number']} {path}", f"this run {path}", lineterm="", n=2))
            text = "\n".join(lines[:400])
        out.append({"file": path, "status": status, "diff": text})
    return out


class FactorRuns:
    def __init__(self, ctx, records, store, runs, lists, library, packages, common):
        self.ctx, self.records, self.store, self.runs, self.library, self.common = ctx, records, store, runs, library, common
        self.data = FactorData(ctx, records.root / "factor-data", lists, library)
        self.data.renamed = self._renamed
        self.data.kept_by = self.keys_in_use
        self.engine = FactorEngine(ctx, packages, records.root / "factor-engine")
        self.queue = RunQueue(devices=lambda: packages.devices()["nvidia"])
        self.tasks = {}
        self.result_tasks = {}  # (run id, sweep combination or None) -> the task building those Results
        self.fields_cache = {}
        self.closed = False
        self.notifications = None  # set by the services: long runs are told when they end

    # ------------------------------------------------------------------------------------------------------------ the data versions the runs use
    def keys_in_use(self):
        """The version folders runs still read: the newest runs of each object, the starred ones and those still going."""
        rows = self.records.db.execute("SELECT content FROM records WHERE kind='qrun' ORDER BY json_extract(content,'$.number') DESC").fetchall()
        kept, seen = set(), {}
        for row in rows:
            record = json.loads(row[0])
            key = (record.get("data") or {}).get("key")
            if not key:
                continue
            seen[record["objectId"]] = seen.get(record["objectId"], 0) + 1
            if seen[record["objectId"]] <= KEEP_CROSS_SECTIONS or record.get("starred") or record["state"] not in TERMINAL:
                kept.add(key)
        return frozenset(kept)

    def _renamed(self, key, aside, frozen_at):
        """A version folder was set aside under a new name: the runs that read it point at that name."""
        rows = self.records.db.execute("SELECT content FROM records WHERE kind='qrun' AND json_extract(content,'$.data.key')=?", (key,)).fetchall()
        for row in rows:
            record = json.loads(row[0])
            if (record["data"].get("frozenAt") or 0) <= frozen_at:
                self.save(record, data={**record["data"], "key": aside})

    @staticmethod
    def use_key(record):
        """What makes two runs the same test of the held-out period: the formula without its spaces or the code, the held-out start and the choice of names."""
        data = record["settings"]["data"]
        who = ["formula", "".join((record.get("formula") or "").split())] if record["mode"] == "formula" else ["code", (record.get("code") or {}).get("digest")]
        chosen = {"symbols": [item["instrument"]["instrumentId"] for item in data.get("symbols") or []], "list": data.get("list"), "index": data.get("index"),
                  "universe": data.get("universe"), "timeframe": data.get("timeframe"), "since": data.get("since"), "adjustment": data.get("adjustment", "adjusted"),
                  "heldOutFrom": data.get("heldOutFrom")}
        return digest([who, chosen])

    def held_out_keys(self):
        """The use key of each completed held-out run of a factor, in any factor: what `uses_of` counts against."""
        # The held-out runs are found on the record_runs index, then only they are read; the factor list reads them once for all its runs.
        ids = [row[0] for row in self.records.db.execute("SELECT id FROM records WHERE kind='qrun' AND json_extract(content,'$.runKind')='heldout'").fetchall()]
        keys = []
        for identity in ids:
            run = self.records.find(RUN, identity)
            source = self.records.find(RUN, run.get("of")) if run["state"] == "completed" else None
            if source is not None and source.get("objectKind") == "factor":  # another kind's held-out run has no formula or code
                keys.append(self.use_key(source))
        return keys

    def uses_of(self, record, keys=None):
        """How many times this run's formula (or code) was scored on the same held-out period and data, in any factor; None for what cannot be validated."""
        if record["runKind"] != "run":
            return None
        return (self.held_out_keys() if keys is None else keys).count(self.use_key(record))

    # ------------------------------------------------------------------------------------------------------------ records
    def directory(self, identity):
        return self.records.root / "qruns" / identity

    def read(self, identity):
        record = self.records.find(RUN, identity)
        if record is None or record.get("objectKind") != "factor":
            raise ContractError("not_found", "This factor run does not exist.")
        return record

    def save(self, record, **changes):
        previous = record["state"]
        updated = self.records.put(RUN, {**record, **changes})
        if updated["state"] != previous or {"stage", "numbers", "progress", "starred", "checks", "results", "sweepResults"} & set(changes):
            self.ctx.publish("object_run_changed", {"runId": updated["id"], "objectId": updated["objectId"],
                                                           "workspaceId": updated["workspaceId"], "state": updated["state"]})
        return updated

    def update(self, identity, **changes):
        return self.save(self.read(identity), **changes)

    def of_object(self, object_id, *, since=None):
        """The object's factor runs, newest first; `since` gives only those changed after that moment (the list is polled)."""
        # A publication is not a run of the factor: it shows on the run it published, as its version.
        clauses = ["kind='qrun'", "json_extract(content,'$.objectId')=?", "json_extract(content,'$.objectKind')='factor'", "json_extract(content,'$.runKind')!='publish'"]
        values = [object_id]
        if since is not None:
            clauses.append("json_extract(content,'$.updatedAt')>?")
            values.append(since)
        rows = self.records.db.execute("SELECT content FROM records WHERE " + " AND ".join(clauses) +
                                       " ORDER BY json_extract(content,'$.number') DESC LIMIT ?", [*values, READ_LIMIT]).fetchall()
        return [json.loads(row[0]) for row in rows]

    def list(self, object_id, since=None):
        self.store.read(object_id)
        records = self.of_object(object_id, since=since)
        keys = self.held_out_keys() if any(record["state"] == "completed" for record in records) else None
        items = [summary({**record, "heldOutUses": self.uses_of(record, keys) if record["state"] == "completed" else None}) for record in records]
        return {"items": items, "now": int(time.time() * 1000), "complete": since is None}

    def next_number(self, object_id):
        return run_flow.next_number(self.records, object_id)  # a publication is not a run of the factor

    def previous_of(self, record):
        """The run before this one that computed values: what its changes and code difference are against."""
        row = self.records.db.execute("""SELECT content FROM records WHERE kind='qrun' AND json_extract(content,'$.objectId')=? AND json_extract(content,'$.objectKind')='factor'
            AND json_extract(content,'$.number')<? AND json_extract(content,'$.runKind')='run' ORDER BY json_extract(content,'$.number') DESC LIMIT 1""",
                                      (record["objectId"], record["number"])).fetchone()
        return json.loads(row[0]) if row else None

    # ------------------------------------------------------------------------------------------------------------ fields and the formula
    async def fields(self, object_id):
        """What the formula box offers: the operators, and the fields Market declares for this object's instruments (with the data's limits)."""
        obj = self.store.read(object_id)
        data = obj["settings"]["data"]
        instruments = []
        selection = self.data.selection(data) if data.get("symbols") or data.get("list") or data.get("index") else None
        # What Market declares for these names is asked once for the same names and period: the formula is checked against it at every
        # pause in typing, which waited 2-4 s on Market (and an index's members) each time.
        key = json.dumps([data, [item["instrumentId"] for item in selection.get("instruments", [])] if selection else None], sort_keys=True, default=str)
        declared = self.fields_cache.get(key)
        if declared is None:
            if selection is not None:
                if selection.get("index"):
                    from .index_universe import freeze
                    last = (await resolve_until(self.ctx, data, instruments=await self.data.calendar_items(data)))["until"]
                    index = await freeze(self.ctx, selection["index"], day_ms(data["since"], False), day_ms(last, True))
                    instruments = index["instruments"][:400]
                else:
                    instruments = selection["instruments"]
            from .research_inputs import describe
            declared = await describe(self.ctx, instruments)
            self.fields_cache[key] = declared
            while len(self.fields_cache) > 8:  # the few factors being edited
                self.fields_cache.pop(next(iter(self.fields_cache)))
        return {"operators": formula.declared_operators(), "fields": self.formula_fields(declared, object_id), "limits": declared["limits"] if declared else [],
                "sources": declared["sources"] if declared else [], "industries": self.data.industries(data), "neutralizeDefault": self.data.default_neutralize(data)}

    def formula_fields(self, declared, object_id=None):
        groups = {"prices": "Prices and volume", "classification": "Classification and size", "size": "Classification and size",
                  "financials": "Point-in-time financials", "derivatives": "Crypto derivatives", "options": "Options"}
        out = []
        for item in (declared or {"fields": []})["fields"]:
            out.append({"name": "$" + item["key"], "label": item["label"], "group": groups[item["group"]],
                        **({"from": item["from"]} if item["from"] else {}), **({"note": item["note"]} if item["note"] else {}),
                        **({"unavailable": item["unavailable"]} if item["unavailable"] else {})})
        if not out:
            out = [{"name": name, "label": label, "group": "Prices and volume"} for name, label in formula.PRICE_FIELDS.items()]
        for item in self.library.store.catalog():
            if item["kind"] == "factor" and item["state"] != "delisted" and item["name"].replace(" ", "_").isidentifier():
                out.append({"name": "$lib." + item["name"].replace(" ", "_"), "label": f"{item['name']}, v{item['version']}", "group": "Library values"})
        return out

    def check(self, text, fields=None):
        return formula.check(text, None if fields is None else {item["name"]: item for item in fields})

    async def to_code(self, object_id, text):
        """The formula as a factor written in Python, in the object's code file, opened in Developer; the object switches to code."""
        obj = self.store.read(object_id)
        try:
            source = formula.to_code(text, obj["name"])
        except formula.FormulaError as error:
            raise ContractError("invalid_input", error.message, {"formula": error.to_dict()}) from error
        developer = await self.store.write_entry(obj, obj["entry"], source)
        settings = {**obj["settings"], "mode": "code", "formula": text}
        record = self.store.save(obj, settings=settings)
        record = self.records.put("qobject", {**record, "developer": developer})
        return self.store.detail(record)

    # ------------------------------------------------------------------------------------------------------------ start
    async def start(self, object_id, args):
        if self.closed:
            raise ContractError("unavailable", "Quant is closing.")
        obj = self.store.read(object_id)
        if obj["kind"] != "factor":
            raise ContractError("invalid_input", "Only a factor has factor runs.")
        kind = args.get("kind", "run")
        if kind not in {"run", "sweep", "heldout"}:
            raise ContractError("invalid_input", "A factor run is run, sweep or heldout.")
        source = None
        if kind == "heldout":
            source = self.read(args["of"]) if args.get("of") else None
            if source is None or source["state"] != "completed" or source["runKind"] != "run":
                raise ContractError("invalid_input", "Validate a completed run on the held-out period.")
            previous = self.records.held_out_for(source["id"])
            if previous is not None:
                return summary(previous)
            settings = copy.deepcopy(source["settings"])
            mode, text = source["mode"], source.get("formula")
        else:
            mode = obj["settings"].get("mode", "formula")
            text = args.get("formula", obj["settings"].get("formula", "")) if mode == "formula" else None
            given = {**factor_work.known_settings(obj["settings"].get("run", {})), **(args.get("settings") or {})}
            run_settings = {**factor_work.DEFAULT_SETTINGS, **given}
            try:
                run_settings = factor_work.settings_of(run_settings)
            except (ValueError, TypeError, KeyError) as error:
                raise ContractError("invalid_input", str(error)) from error
            run_settings.pop("heldOutFrom", None)
            run_settings.pop("params", None)
            data = obj["settings"]["data"]
            # Neutralize left out: the run records the default for these names; the object keeps it left out, so the default follows the names.
            kept = dict(run_settings)
            if "neutralize" not in given:
                kept.pop("neutralize")
                run_settings["neutralize"] = self.data.default_neutralize(data)
            if mode == "formula":
                problem = formula.check(text)
                if problem is not None:
                    raise ContractError("invalid_input", problem["message"], {"formula": problem})
            params = {}
            if mode == "code":
                from .object_params import check_values
                params = {**self.store.effective_parameters(obj), **(args.get("params") or {})}
                try:
                    check_values(self.store.project_parameters(obj)["parameters"], params)
                except ValueError as error:
                    raise ContractError("invalid_input", str(error)) from error
            # The day the data end is resolved with Market as the run's first step (the button waited on it before the run existed).
            settings = {"data": copy.deepcopy(data), "run": run_settings, "params": params if mode == "code" else {}}
            # The object keeps what was run last, so opening it again shows these settings.
            changes = {"run": kept, **({"formula": text} if mode == "formula" else {}), **({"params": params} if mode == "code" else {})}
            obj = self.store.save(obj, settings={**obj["settings"], **changes})
        if not all(settings["data"].get(key) for key in ("since", "until")) or not (settings["data"].get("symbols") or settings["data"].get("list") or settings["data"].get("index")):
            raise ContractError("invalid_input", "Choose the names and the period in the data settings first.")
        held = settings["data"].get("heldOutFrom")
        if kind == "heldout" and not held:
            raise ContractError("invalid_input", "Set where the held-out period starts in the run settings first.")
        axes = None
        if kind == "sweep":
            axes = [{"name": item["name"], "values": list(item["values"])} for item in args.get("sweep") or []]
            total = 1
            for axis in axes:
                if axis["name"] not in {"holding", "groups", "costBps", "delay"} and axis["name"] not in settings["params"]:
                    raise ContractError("invalid_input", f"A sweep can vary holding, groups, costBps, delay or a code setting, not {axis['name']}.")
                if not axis["values"]:
                    raise ContractError("invalid_input", f"{axis['name']} needs at least one value.")
                total *= len(axis["values"])
            if not axes or total < 2:
                raise ContractError("invalid_input", "A sweep needs a list of values in at least one setting.")
            if total > SWEEP_LIMIT:
                raise ContractError("invalid_input", f"A sweep tests at most {SWEEP_LIMIT} combinations; this one has {total}.")
        identity = "run-" + uuid4().hex
        number = self.next_number(obj["id"])
        now = int(time.time() * 1000)
        record = {"id": identity, "objectId": obj["id"], "objectKind": "factor", "name": obj["name"], "runKind": kind, "number": number,
                  # Made at once in the shared flow's first state: preparing, its check (the data's end) the first step.
                  "mode": mode, "formula": text, "workspaceId": self.store.workspace(), "state": "preparing", "stage": "check", "createdAt": now,
                  "endedAt": None, "seconds": None, "settings": settings, "changes": [], "codeDiff": None, "numbers": None, "checks": None,
                  "progress": {"steps": [], "startedAt": now}, "failure": None, "data": None, "starred": False, "note": "",
                  "of": source["id"] if source else None, "ofNumber": source["number"] if source else None,
                  "sweep": {"axes": axes, "total": total, "done": 0} if axes else None, "version": None, "heldOut": None,
                  # Fields the shared run history and workspace activity read.
                  "message": None, "error": None, "params": settings["params"], "metrics": {}, "code": None, "common": None, "environment": None,
                  "recipe": None, "overrides": {}, "exitCode": None}
        record = self.records.put(RUN, record)
        self.ctx.publish("object_run_changed", {"runId": identity, "objectId": obj["id"], "workspaceId": record["workspaceId"], "state": record["state"]})
        self._launch(identity)
        return summary(record)

    def _launch(self, identity):
        self.tasks[identity] = self.ctx.start(self.execute(identity), run_record=self.read(identity))

    async def retry(self, identity):
        """Run a run again that stopped while fetching prices: the names fetched are kept, only the rest are asked for."""
        record = self.read(identity)
        if record["state"] != "failed" or (record.get("failure") or {}).get("stage") != "data":
            raise ContractError("invalid_input", "Only a run that stopped while fetching prices can be retried.")
        if identity in self.tasks:
            raise ContractError("conflict", "This run is already running.")
        from .runtime import call_attribution
        self.save(record, state="queued", stage="queued", failure=None, message=None, error=None, endedAt=None, **call_attribution(),
                  progress={"steps": [], "startedAt": int(time.time() * 1000)})
        self._launch(identity)
        return summary(self.read(identity))

    # ------------------------------------------------------------------------------------------------------------ steps
    def step(self, identity, name, state, **values):
        record = self.read(identity)
        steps = [item for item in record["progress"]["steps"] if item["name"] != name]
        current = next((item for item in record["progress"]["steps"] if item["name"] == name), {})
        now = int(time.time() * 1000)
        entry = {**current, "name": name, "state": state, **values}
        entry.setdefault("startedAt", now)
        if state in {"done", "failed"}:
            entry["seconds"] = round((now - entry["startedAt"]) / 1000, 2)
        if state == "done" and entry.get("total") is not None:
            entry["done"] = entry["total"]   # a finished step has counted everything: the last count saved may be one the throttle held back ("✓ 0 of 13")
        order = ["code", "data", "facts", "environment", "compute", "numbers", "future"]
        steps.append(entry)
        steps.sort(key=lambda item: order.index(item["name"]) if item["name"] in order else 99)
        return self.save(record, progress={**record["progress"], "steps": steps, "stage": name})

    async def save_project(self, obj):
        """Every unsaved file of the factor's project is saved before the run, so the run is what the editor shows."""
        root = Path(obj["directory"]).resolve().as_uri()
        cursor = None
        while True:
            page = plain(await self.ctx.developer("developer.documents.list", {"rootUris": [root], **({"cursor": cursor} if cursor else {})}))
            if page is None:   # no Developer: nothing is open unsaved
                return
            for document in page["documents"]:
                if document["conflict"]:
                    raise ContractError("conflict", f"{Path(document['uri']).name} changed on disk while it has unsaved edits. Resolve it in Developer, then run again.")
                if document["dirty"]:
                    saved = plain(await self.ctx.call("developer.documents.save", {"documentId": document["documentId"], "expectedVersion": document["version"]}))
                    if saved["status"] not in {"saved", "clean"}:
                        raise ContractError("conflict", f"{Path(document['uri']).name} could not be saved before running: {saved.get('message') or saved['status']}.")
            cursor = page["nextCursor"]
            if not cursor:
                return

    async def execute(self, identity):
        record = self.read(identity)
        obj = self.store.read(record["objectId"])
        directory = self.directory(identity)
        output = directory / "output"
        started = time.perf_counter()
        ending = {}
        try:
            directory.mkdir(parents=True, exist_ok=True)
            if output.exists():
                shutil.rmtree(output, ignore_errors=True)
            output.mkdir()
            if record["stage"] == "check":
                await self.check_step(identity)
            if record["mode"] == "code" and record["code"] is None:
                self.step(identity, "code", "running")
                if record["runKind"] == "heldout":
                    source = self.read(record["of"])
                    self.update(identity, code=source["code"], common=source.get("common"))
                else:
                    await self.save_project(obj)
                    captured = await self.runs.capture_code(obj, directory)
                    common = await self.common.capture(self.records.root / "capture", directory / "common")
                    fresh = self.read(identity)
                    files = captured["files"]
                    code = {"entry": obj["entry"], "digest": captured["digest"], "files": files, "snapshotPath": str(directory / "source"), "directory": obj["directory"]}
                    previous = self.previous_of(fresh)
                    self.update(identity, code=code, common={"digest": common["digest"], "files": common["files"], "snapshotPath": str(directory / "common")} if common else None,
                                codeDiff=code_diff(directory / "source", files, previous), changes=changes_between({**fresh, "code": code}, previous))
                self.step(identity, "code", "done")
            interactive = self.read(identity)["runKind"] != "sweep"
            run_flow.check_fits(self.queue, MEMORY)
            async with self.queue.slot(owner=identity, label=f"{obj['name']} #{record['number']}", interactive=interactive, memory=MEMORY,
                                       on_wait=lambda position: self.update(identity, state="queued", stage="queued", progress={**self.read(identity)["progress"], "queue": position})):
                self.update(identity, state="running", stage="data")
                ready = await self.prepare_data(identity, obj)
                self.update(identity, stage="environment")
                job = await self.job(identity, ready)
                self.step(identity, "environment", "running")
                self.update(identity, state="running", stage="compute")
                answer = await self.compute(identity, job, ready)
            ending = await self.finish(identity, answer, started)
        except asyncio.CancelledError:
            ending = {"state": "interrupted" if self.closed else "cancelled", "message": "Stopped before the run finished."}
        except ContractError as error:
            stage = self.read(identity)["stage"]
            failure = error.details.get("failure") if isinstance(error.details, dict) and isinstance(error.details.get("failure"), dict) else None
            failure = failure or {"stage": "data" if stage == "data" else "compute", "title": "stopped while fetching prices" if stage == "data" else "stopped",
                                  "message": error.message}
            ending = {"state": "failed", "message": failure["message"], "failure": failure, "error": {"stage": stage, **error.to_dict()}}
        except Exception as error:  # a background run must end, and say what broke
            import logging
            logging.getLogger(__name__).exception("Factor run failed (%s)", identity)
            # The message names what broke, never a folder or an internal id: those stay in Quant's log.
            message = f"Quant hit an internal error ({type(error).__name__}) and stopped this run; the details are in Quant's log."
            ending = {"state": "failed", "message": message, "failure": {"stage": "compute", "title": "stopped", "message": message},
                      "error": {"stage": "compute", "code": "internal_error", "message": message}}
        finally:
            self.tasks.pop(identity, None)
            if ending:
                record = self.read(identity)
                record = self.save(record, **{"seconds": record.get("seconds"), **ending, "endedAt": int(time.time() * 1000)})
                if ending["state"] == "completed":
                    self.prune(record["objectId"])
                    if record["runKind"] == "run" and not self.closed:
                        # Work's numbers are out; the Results follow in the background without holding up the next run.
                        self.schedule_results(identity)
                if self.notifications is not None and ending["state"] in {"completed", "failed"}:
                    what = {"run": "Run", "sweep": "Sweep", "heldout": "Held-out validation"}.get(record["runKind"], "Run")
                    await self.notifications.long_run(identity, f"Quant · {what} {ending['state']}", f"{record['name']} #{record['number']} {ending['state']}"
                                                      + (f": {ending.get('message')}" if ending.get("message") else "."), record.get("seconds"))

    async def check_step(self, identity):
        """The run's first step: the day its data end (Market's last published bar for "Latest close") and what changed against the run
        before, both written into the run."""
        record = self.read(identity)
        changes = {"stage": "start"}
        if record["runKind"] != "heldout":
            data = record["settings"]["data"]
            resolved = await resolve_until(self.ctx, data, instruments=await self.data.calendar_items(data))
            settled = {**record, "settings": {**record["settings"], "data": copy.deepcopy(resolved)}}
            changes.update(settings=settled["settings"], changes=changes_between(settled, self.previous_of(settled)))
        self.update(identity, **changes)

    async def prepare_data(self, identity, obj):
        record = self.read(identity)
        self.step(identity, "data", "running")
        last = {"at": 0, "stage": None, "phase": None, "phaseAt": None}
        # the data step runs four counts one after another (names checked, prices fetched, series exported, prices checked), each
        # with its own total; shown as one count it jumped between 590 and 1180, went backwards, and its time left with it.
        phases = {"checking names": "names", "checking prices": "checking"}

        def report(rows, stage, done=None, progress=None):
            # At most two updates a second, but a new stage or phase always shows at once.
            now = time.time()
            phase = None if progress is None else progress.get("step") or phases.get(progress.get("message"), "prices")
            if now - last["at"] < .5 and stage == last["stage"] and phase == last["phase"] and stage != "ready":
                return
            entering = stage != last["stage"]
            if phase != last["phase"]:
                last["phaseAt"] = int(now * 1000)
            last.update(at=now, stage=stage, phase=phase)
            counts = {}
            for row in rows:
                counts[row["result"]] = counts.get(row["result"], 0) + 1
            name = "facts" if stage == "facts" else "data"
            if stage == "facts" and entering:
                # Prices are marked done once, when the facts start, not again with every facts update: each save is an event the pages read.
                self.step(identity, "data", "done")
            detail = None
            if progress is not None and progress["waitingFor"] is not None:
                from datetime import UTC, datetime
                until = progress["waitingUntil"]
                deadline = datetime.fromtimestamp(int(until) / 1_000_000_000, UTC).isoformat() if until is not None else None
                detail = f"Waiting for {progress['waitingFor']}" + (f" until {deadline}" if deadline else "")
            self.step(identity, name, "running" if stage != "ready" else "done", done=len(rows) - counts.get("waiting", 0) if done is None else done,
                      total=progress["total"] if progress is not None else len(rows), counts=counts, detail=detail,
                      **({"phase": phase, "phaseStartedAt": last["phaseAt"]} if name == "data" and phase else {}))

        key = self.data.key(record["settings"]["data"])
        self.update(identity, data={"key": key})
        ready = await self.data.prepare(record["settings"]["data"], report=report)
        key = ready["key"]
        steps = {item["name"] for item in self.read(identity)["progress"]["steps"]}
        self.step(identity, "data", "done", detail="from the same Market revisions and research facts" if ready.get("how") == "unchanged" else None)
        if "facts" in steps:
            self.step(identity, "facts", "done")
        library = await self.data.library_values(key, ready["info"], exclude=obj["name"])
        held = record["settings"]["data"].get("heldOutFrom")
        self.update(identity, data={"key": key, "dataFrom": ready["dataFrom"], "dataTo": ready["dataTo"], "bars": ready.get("bars"), "names": ready["names"], "requested": ready["requested"],
                                    "notes": ready["notes"], "frozenAt": ready["frozenAt"], "annualization": ready["info"]["annualization"],
                                    "heldOutFrom": day_ms(held, False) if held else None, "library": library is not None})
        return {**ready, "libraryPath": library}

    async def job(self, identity, ready):
        record = self.read(identity)
        held = record["settings"]["data"].get("heldOutFrom")
        job = {"output": str(self.directory(identity) / "output"), "kind": record["runKind"], "settings": record["settings"]["run"],
               "data": {"key": f"{ready['key']}@{ready['frozenAt']}", "barsPath": ready["barsPath"], "info": ready["info"], "extrasPath": ready["extrasPath"],
                        "libraryPath": ready.get("libraryPath")},
               "heldOutFrom": day_ms(held, False) if held else None}
        if record["mode"] == "formula":
            job["formula"] = record["formula"]
        else:
            job["code"] = {"source": record["code"]["snapshotPath"], "entry": record["code"]["entry"],
                           "common": record["common"]["snapshotPath"] if record.get("common") else None, "params": record["settings"]["params"]}
        if record["runKind"] == "sweep":
            job["sweep"] = record["sweep"]["axes"]
        if record["runKind"] == "heldout":
            ls = self.directory(record["of"]) / "output" / "ls.json"
            job["inSample"] = {"ls": json.loads(ls.read_text(encoding="utf-8")) if ls.is_file() else None}
        return job

    async def compute(self, identity, job, ready):
        record = self.read(identity)
        progress_file = Path(job["output"]) / "progress.jsonl"
        stop = asyncio.Event()

        def publish(state):
            self.step(identity, "environment", "running" if state.get("state") not in {"completed"} else "done", detail=state.get("message"))

        async def follow():
            # The worker appends a line per step; the record follows it once a second.
            seen = 0
            while not stop.is_set():
                with contextlib.suppress(asyncio.TimeoutError):
                    await asyncio.wait_for(stop.wait(), 1)
                if progress_file.is_file():
                    lines = progress_file.read_text(encoding="utf-8").splitlines()
                    for line in lines[seen:]:
                        item = json.loads(line)
                        if item["stage"] in {"compute", "numbers", "future"}:
                            if item["stage"] == "compute" and item["state"] == "running":
                                self.step(identity, "environment", "done")
                            values = {key: item[key] for key in ("done", "total") if key in item}
                            self.step(identity, item["stage"], item["state"], **values)
                            if item["stage"] == "numbers" and "done" in item and record["runKind"] == "sweep":
                                self.update(identity, sweep={**self.read(identity)["sweep"], "done": item["done"]})
                    seen = len(lines)

        follower = asyncio.create_task(follow())
        held = ready.get("key")
        self.data.in_use[held] = self.data.in_use.get(held, 0) + 1  # a folder the research process is reading is neither renamed nor pruned
        try:
            loop = asyncio.get_running_loop()

            def later(future):
                loop.create_task(self.apply_future(identity, future))
            answer = await self.engine.run_open(job, on_future=later if record["mode"] == "code" else None, publish=publish)
        finally:
            stop.set()
            await follower
            self.data.in_use[held] = self.data.in_use.get(held, 1) - 1
        if not answer.get("ok"):
            failure = answer.get("failure") or {"stage": "compute", "title": "stopped", "message": "The run failed."}
            raise ContractError("invalid_input" if failure.get("stage") == "code" else "unavailable", failure["message"], {"failure": failure})
        return answer

    async def apply_future(self, identity, future):
        """The code's future-data check arrives after the numbers: the check is replaced and a failure tags the run."""
        record = self.read(identity)
        if not record.get("checks"):
            return
        checks = [item if item["id"] != "future_data" else {**item, "value": future["value"],
                  "state": "notrun" if future.get("passed") is None else "ok" if future["passed"] else "watch", "title": future["title"]} for item in record["checks"]]
        self.save(record, checks=checks, future=future)

    async def finish(self, identity, answer, started):
        record = self.read(identity)
        work_path = self.directory(identity) / "output" / "work.json"
        work = json.loads(work_path.read_text(encoding="utf-8"))
        seconds = round(time.perf_counter() - started, 2)
        common = {"state": "completed", "stage": "done", "message": None, "seconds": seconds, "computeSeconds": answer.get("seconds")}
        if record["runKind"] == "run":
            numbers = work["numbers"]
            metrics = {"rankIc": numbers["rankIc"], "t": numbers["t"], "longShortNet": numbers["longShortNet"], "turnover": numbers["turnover"], "names": numbers["names"]}
            checks = work["checks"]
            future = record.get("future")
            if future is not None:
                checks = [item if item["id"] != "future_data" else {**item, "value": future["value"], "title": future["title"],
                          "state": "notrun" if future.get("passed") is None else "ok" if future["passed"] else "watch"} for item in checks]
            return {**common, "numbers": numbers, "checks": checks, "metrics": metrics, "decay": work.get("decay"), "heldOutUses": self.uses_of(record)}
        if record["runKind"] == "sweep":
            cells = work["cells"]
            # The sweep opens on the group the current settings make, never on the one that scored best (that would pick the result); the best is only marked.
            best = max(range(len(cells)), key=lambda index: (cells[index]["numbers"].get("longShortNet") or float("-inf")))
            chosen = max(range(len(cells)), key=lambda index: current_score(cells[index], record))
            return {**common, "sweep": {**record["sweep"], "done": len(cells), "selected": chosen, "best": best}, "numbers": cells[chosen]["numbers"], "checks": cells[chosen]["checks"]}
        source = self.read(record["of"])
        self.save(source, heldOut={"runId": identity, "number": record["number"]})
        return {**common, "numbers": work["numbers"], "heldOut": {"runId": identity, "number": record["number"], "of": source["number"]}}

    def prune(self, object_id):
        """The values of each day and the Results stay for the newest runs and the starred ones; older runs keep their numbers."""
        models = self.records.db.execute("SELECT json_extract(content, '$.settings.params.inputs') FROM records WHERE kind='qobject' "
                                         "AND json_extract(content, '$.kind')='model' AND NOT COALESCE(json_extract(content, '$.deleted'), 0)")
        referenced = {item["ref"] for (inputs,) in models if inputs for item in json.loads(inputs) if item.get("source") == "run"}
        for record in self.of_object(object_id)[KEEP_CROSS_SECTIONS:]:
            if record["starred"] or record["id"] in referenced or any(key[0] == record["id"] for key in self.result_tasks):
                continue
            output = self.directory(record["id"]) / "output"
            for name in ("cross", "results"):
                if (output / name).is_dir():
                    shutil.rmtree(output / name, ignore_errors=True)
            for found in output.glob("results-*") if output.is_dir() else ():
                shutil.rmtree(found, ignore_errors=True)
            with contextlib.suppress(OSError):
                (output / "values.npy").unlink(missing_ok=True)
            if record.get("results") or record.get("sweepResults"):
                self.save(record, results=None, sweepResults=None)

    # ------------------------------------------------------------------------------------------------------------ Results
    def results_directory(self, identity, cell=None):
        return self.directory(identity) / "output" / ("results" if cell is None else f"results-{int(cell)}")

    def _put_results(self, identity, cell, state):
        record = self.read(identity)
        if cell is None:
            return self.save(record, results=state)
        return self.save(record, sweepResults={**(record.get("sweepResults") or {}), str(cell): state})

    def schedule_results(self, identity, cell=None, *, run_record=None):
        """Queue a run's Results (or one sweep combination's) as background work. A newer run of the same factor puts aside the Results
        of earlier runs still waiting for their turn; those build when someone asks for them."""
        key = (identity, cell)
        if key in self.result_tasks:
            return results_of(self.read(identity), cell)
        record = self.read(identity)
        if cell is None:
            for (other, other_cell), task in list(self.result_tasks.items()):
                found = self.records.find(RUN, other)
                state = results_of(found, other_cell) if found else None
                if found and found["objectId"] == record["objectId"] and other != identity and state and state["state"] == "queued":
                    task.cancel()
        state = {"state": "queued", "progress": 0, "tabs": {name: "waiting" for name in RESULT_TABS}, "queuedAt": int(time.time() * 1000)}
        BUILDING.add(key)
        self._put_results(identity, cell, state)
        self.result_tasks[key] = self.ctx.start(self._build_results(identity, cell), run_record=run_record)
        return state

    async def build_results(self, identity, cell=None):
        """Build a run's Results on request: an earlier run's that were put aside, ones that failed or stopped, or a sweep combination's."""
        record = self.read(identity)
        if record["runKind"] == "heldout":
            raise ContractError("invalid_input", "A held-out validation has no Results of its own: open the run it scored.")
        if record["state"] != "completed":
            raise ContractError("conflict", "Results follow once the run has finished.")
        if record["runKind"] == "sweep":
            if cell is None:
                raise ContractError("invalid_input", "Choose which combination of the sweep to build Results for.")
            cells = self.sweep(identity)["cells"]
            if not 0 <= cell < len(cells):
                raise ContractError("invalid_input", "No such combination in this sweep.")
            if cells[cell]["settings"].get("params") and cells[cell]["settings"]["params"] != record["settings"]["params"]:
                raise ContractError("invalid_input", "This combination changes the code's own settings, whose values were not kept. Use these settings and run them for Results.")
        elif cell is not None:
            raise ContractError("invalid_input", "Only a sweep has combinations.")
        if not (self.directory(identity) / "output" / "values.npy").is_file():
            raise ContractError("not_found", "This run's values were not kept (only the newest runs and starred ones keep them); run it again for its Results.")
        state = results_of(record, cell)
        if state["state"] in {"queued", "running", "done"}:
            return state
        from .runtime import call_attribution
        return self.schedule_results(identity, cell, run_record={**record, **call_attribution()})

    def results_job(self, identity, cell=None):
        record = self.read(identity)
        work = json.loads((self.directory(identity) / "output" / "work.json").read_text(encoding="utf-8"))
        settings = record["settings"]["run"] if cell is None else {key: value for key, value in work["cells"][cell]["settings"].items() if key != "params"}
        directory = self.data.directory(record["data"]["key"])
        if not (directory / "bars.parquet").is_file():
            raise ContractError("not_found", "The prices this run used are no longer kept; run it again for its Results.")
        info = json.loads((directory / "info.json").read_text(encoding="utf-8"))
        return {"output": str(self.results_directory(identity, cell)), "settings": settings, "values": str(self.directory(identity) / "output" / "values.npy"),
                "data": {"key": record["data"]["key"], "barsPath": str(directory / "bars.parquet"), "info": info,
                         "extrasPath": str(directory / "extras.json") if (directory / "extras.json").is_file() else None},
                "heldOutFrom": record["data"].get("heldOutFrom"), "formula": record["mode"] == "formula",
                # The library factors most like the run: the price styles Work compared it with are measured again by the Style tab itself,
                # so they do not come along as library factors (they would be listed twice and held to the library limit).
                "similar": [{"name": item["name"], "version": item.get("version"), "rho": item.get("rho")} for item in (work.get("similar") or (work["cells"][cell].get("similar") if cell is not None else None) or [])
                            if not item.get("style")]}

    async def _build_results(self, identity, cell):
        key = (identity, cell)
        output = self.results_directory(identity, cell)
        stop = asyncio.Event()
        ending = None
        started = time.perf_counter()

        async def follow():
            seen = 0
            while not stop.is_set():
                with contextlib.suppress(asyncio.TimeoutError):
                    await asyncio.wait_for(stop.wait(), 1)
                path = output / "progress.jsonl"
                if not path.is_file():
                    continue
                lines = path.read_text(encoding="utf-8").splitlines()
                if len(lines) == seen:
                    continue
                state = dict(results_of(self.read(identity), cell))
                tabs = dict(state.get("tabs") or {})
                for line in lines[seen:]:
                    with contextlib.suppress(ValueError):
                        item = json.loads(line)
                        if item["stage"] in tabs:
                            tabs[item["stage"]] = item["state"]
                        if "total" in item and item["total"]:
                            state["progress"] = round(item.get("done", 0) / item["total"], 3)
                seen = len(lines)
                self._put_results(identity, cell, {**state, "state": "running", "tabs": tabs})

        try:
            record = self.read(identity)
            async with self.queue.slot(owner=f"{identity}:results" + ("" if cell is None else f":{cell}"), label=f"{record['name']} #{record['number']} Results",
                                       interactive=False, memory=RESULTS_MEMORY):
                if output.exists():
                    shutil.rmtree(output, ignore_errors=True)
                output.mkdir(parents=True)
                self._put_results(identity, cell, {**results_of(self.read(identity), cell), "state": "running", "startedAt": int(time.time() * 1000)})
                job = self.results_job(identity, cell)
                follower = asyncio.create_task(follow())
                try:
                    answer = await self.engine.run_results(job, self.directory(identity) / ("results-engine" if cell is None else f"results-engine-{cell}"),
                                                           context={"objectId": record["objectId"], "runId": identity, "kind": "results"})
                finally:
                    stop.set()
                    await follower
            if not answer.get("ok"):
                raise ContractError("unavailable", answer.get("message") or "The Results stopped.")
            ending = {"state": "done", "progress": 1, "tabs": answer["tabs"], "seconds": round(time.perf_counter() - started, 2),
                      "computeSeconds": answer["seconds"], "endedAt": int(time.time() * 1000), "about": answer.get("about")}
            if any(value == "failed" for value in answer["tabs"].values()):
                ending["message"] = "Some tabs could not be built; each says why."
        except asyncio.CancelledError:
            ending = {"state": "notbuilt" if not self.closed else "interrupted", "progress": 0, "tabs": {},
                      "message": "Put aside for a newer run; open these Results to build them." if not self.closed else "Quant closed before these Results were built; build them again."}
        except ContractError as error:
            ending = {"state": "failed", "progress": 0, "tabs": {}, "message": error.message, "endedAt": int(time.time() * 1000)}
        except Exception as error:  # a background job must end, and say what broke
            import logging
            logging.getLogger(__name__).exception("Factor Results failed (%s)", identity)
            ending = {"state": "failed", "progress": 0, "tabs": {}, "endedAt": int(time.time() * 1000),
                      "message": f"Quant hit an internal error ({type(error).__name__}) while building the Results; the details are in Quant's log."}
        finally:
            self.result_tasks.pop(key, None)
            BUILDING.discard(key)
            if ending is not None:
                with contextlib.suppress(ContractError):
                    previous = results_of(self.read(identity), cell)
                    self._put_results(identity, cell, {**{k: v for k, v in previous.items() if k in ("queuedAt", "startedAt")}, **ending})
                    if ending["state"] == "done" and cell is None:
                        from ..native import styles
                        about = ending.get("about") or {}
                        # The style definitions the Results measured against, with their version, kept with the run.
                        self.update(identity, styles={"version": styles.VERSION, "definitions": styles.definitions(about.get("unit", "day"), bool(about.get("shares", True)))})

    async def results(self, identity, tab=None, compare=(), cell=None, figures=True):
        """A run's Results: where they are, and one tab's tables and figures once that tab is built (the others may still be building).
        `compare` draws other runs' long–short net beside this one in the groups chart, dashed. The files are read off the event loop."""
        record = self.read(identity)
        if cell is not None and record["runKind"] != "sweep":
            raise ContractError("invalid_input", "Only a sweep has combinations.")
        state = results_of(record, cell)
        payload = None
        if tab is not None:
            if tab not in RESULT_TABS:
                raise ContractError("invalid_input", f"Results have the tabs {', '.join(RESULT_TABS)}.")
            path = self.results_directory(identity, cell) / f"{tab}.json"
            others = [self.read(other) for other in compare if other != identity]

            def load():
                if not path.is_file() or state["state"] not in {"running", "done", "interrupted", "failed"}:
                    return None
                found = json.loads(path.read_text(encoding="utf-8"))
                if tab == "returns" and others and "figure" in found["figures"].get("groups", {}):
                    traces = found["figures"]["groups"]["figure"]["data"]
                    for index, other in enumerate(others):
                        ls = self.directory(other["id"]) / "output" / "ls.json"
                        if ls.is_file():
                            line = json.loads(ls.read_text(encoding="utf-8"))
                            traces.append({"type": "scatter", "mode": "lines", "x": line["x"], "y": line["y"], "name": f"L–S net #{other['number']}",
                                           "meta": {"role": "compare", "key": f"ls-net:compared:{other['number']}", "index": index, "of": len(others)}})
                if not figures:
                    found["figures"] = {name: {key: value for key, value in item.items() if key != "figure"} for name, item in found["figures"].items()}
                return found
            payload = await asyncio.to_thread(load)
        return {"run": summary(record), "results": state, "tab": payload, **({"cell": cell} if cell is not None else {})}

    # ------------------------------------------------------------------------------------------------------------ reading
    def daily_numbers(self, record):
        """The daily numbers the run stored with its Work (factor_work.Evaluation.store_daily), on its own labels; None for a run made before they were stored."""
        path = self.directory(record["id"]) / "output" / "daily.npz"
        if not path.is_file():
            return None
        with np.load(path) as stored:
            daily = pd.DataFrame({key: stored[key] for key in ("rankIc", "longShortNet", "top", "bottom", "names")}, index=stored["labels"])
        daily["names"] = daily["names"].where(daily["names"] > 0)
        return daily

    def work(self, identity, compare=None):
        """The selected run's Work view: numbers, checks, figures and what is beside them; `compare` draws another run's long–short in the groups chart."""
        record = self.read(identity)
        path = self.directory(identity) / "output" / "work.json"
        work = json.loads(path.read_text(encoding="utf-8")) if path.is_file() else None
        if work is not None and record["runKind"] == "run":
            if record.get("checks"):
                work["checks"] = record["checks"]
            if compare:
                other = self.read(compare)
                left, right = self.daily_numbers(record), self.daily_numbers(other)
                if left is None or right is None:
                    # The Work view stays; its compare control names the runs to run again.
                    work["comparisonMissing"] = [item["number"] for item, daily in ((record, left), (other, right)) if daily is None]
                else:
                    work["comparison"] = common_differences(left, right, record, other)
                ls = self.directory(compare) / "output" / "ls.json"
                if ls.is_file() and "groups" in work["figures"]:
                    path_ = json.loads(ls.read_text(encoding="utf-8"))
                    work["figures"]["groups"]["data"].append({"type": "scatter", "mode": "lines", "x": path_["x"], "y": path_["y"], "name": f"L–S net #{other['number']}",
                                                              "meta": {"role": "compare", "key": "ls-net:compared"}})
        return {"run": summary(record), "work": work, "codeDiff": record.get("codeDiff"), "codeFiles": code_files(record)}

    def fetch(self, identity):
        record = self.read(identity)
        key = (record.get("data") or {}).get("key")
        return {"rows": self.data.fetch_rows(key) if key else [], "runId": identity}

    @staticmethod
    def _default_day(record, meta):
        """The last day that has a value, a full holding period of later returns, and is not in the held-out stretch: the day whose "Next" column is whole."""
        labels, names = meta["labels"], meta["names"]
        run = record["settings"]["run"]
        reach = int(meta.get("holding") or run.get("holding") or 1) + int(run.get("delay") or 0)
        held = (record["settings"].get("data") or {}).get("heldOutFrom")
        limit = None
        if held:
            try:
                limit = day_ms(str(held)[:10], False)
            except ValueError:
                limit = None
        ends = len(labels) - 1 - reach
        for index in range(min(ends, len(labels) - 1), -1, -1):
            if names[index] and (limit is None or labels[index] < limit):
                return index
        return max((index for index, count in enumerate(names) if count), default=len(labels) - 1)

    def cross_section(self, identity, date=None, record=None):
        """One day of the run's values: each member's inputs, the value before and after preprocessing, rank, group, the later return, or why it has none.
        Runs off the event loop, so the caller reads the record first (the records database belongs to the loop's thread)."""
        import numpy as np
        record = record or self.read(identity)
        cross = self.directory(identity) / "output" / "cross"
        if not (cross / "meta.json").is_file():
            raise ContractError("not_found", "This run's values by day were not kept (only the newest runs and starred ones keep them).")
        meta = json.loads((cross / "meta.json").read_text(encoding="utf-8"))
        labels = meta["labels"]
        if date is None:
            row = self._default_day(record, meta)
        else:
            row = int(np.clip(np.searchsorted(labels, date, side="right") - 1, 0, len(labels) - 1))
        load = lambda name: np.load(cross / name, mmap_mode="r")[row]  # noqa: E731
        after, raw, group, missing, following = load("after.npy"), load("raw.npy"), load("group.npy"), load("missing.npy"), load("next.npy")
        inputs = [load(f"input-{index}.npy") for index in range(len(meta["inputs"]))]
        industry = meta.get("industry") or [None] * len(meta["columns"])
        valued = ~np.isnan(after)
        order = np.argsort(-np.where(valued, after, -np.inf), kind="stable")
        rank = np.zeros(len(after), dtype=int)
        rank[order[:int(valued.sum())]] = np.arange(1, int(valued.sum()) + 1)
        flags = {item["i"]: item["text"] for item in meta["flags"] if item["t"] == row}
        reasons = factor_work.MISSING
        rows = []
        for index, column in enumerate(meta["columns"]):
            if missing[index] == 1:
                continue
            value = lambda array, i=index: None if np.isnan(array[i]) else float(array[i])  # noqa: E731
            rows.append({"ticker": column, "sector": industry[index], "inputs": {name: value(inputs[k]) for k, name in enumerate(meta["inputs"])},
                         "raw": value(raw), "after": value(after), "rank": int(rank[index]) or None, "group": int(group[index]) or None,
                         "next": value(following), **({"missing": reasons.get(int(missing[index]), "No value")} if not valued[index] else {}),
                         **({"flag": flags[index]} if index in flags else {})})
        # Members Market gave no instrument for are rows too, without a value and saying why.
        day = labels[row]
        info_path = self.data.directory((record.get("data") or {}).get("key") or "") / "info.json" if (record.get("data") or {}).get("key") else None
        if info_path is not None and info_path.is_file():
            seen = set()
            for item in (json.loads(info_path.read_text(encoding="utf-8")).get("index") or {}).get("exclusions", []):
                if item.get("kind") in {"unmapped", "no_prices"} and item["since"] <= day <= item["until"] and item["assetId"] not in seen:
                    seen.add(item["assetId"])
                    rows.append({"ticker": item["symbol"], "sector": None, "inputs": {}, "raw": None, "after": None, "rank": None, "group": None, "next": None,
                                 "missing": "No Market instrument for this member" if item["kind"] == "unmapped" else item.get("sourceReason") or "The source gave no prices",
                                 "unmapped": item["kind"] == "unmapped"})
        return {"date": labels[row], "run": record["number"], "runId": identity, "inputNames": meta["inputs"], "rows": rows,
                "count": int(valued.sum()), "members": len(rows), "holding": meta["holding"], "groups": meta["groups"],
                "dates": {"first": labels[0], "last": labels[-1]}, "previous": labels[row - 1] if row > 0 else None,
                "next": labels[row + 1] if row + 1 < len(labels) else None}

    def sweep(self, identity):
        record = self.read(identity)
        path = self.directory(identity) / "output" / "work.json"
        if record["runKind"] != "sweep" or not path.is_file():
            raise ContractError("not_found", "This run has no sweep results.")
        return {"run": summary(record), **json.loads(path.read_text(encoding="utf-8"))}

    # ------------------------------------------------------------------------------------------------------------ use this run
    async def apply(self, identity, expected_version=None, sweep_index=None):
        """Put this run's formula (or code version), data and run settings back as the factor's current ones; a sweep's combination by index."""
        record = self.read(identity)
        obj = self.store.read(record["objectId"])
        self.store.check_version(obj, expected_version)
        run = copy.deepcopy(record["settings"]["run"])
        if sweep_index is not None:
            cells = self.sweep(identity)["cells"]
            if not 0 <= sweep_index < len(cells):
                raise ContractError("invalid_input", "No such combination in this sweep.")
            run = {key: value for key, value in cells[sweep_index]["settings"].items() if key not in {"heldOutFrom", "params"}}
        settings = {**obj["settings"], "run": run, "data": copy.deepcopy(record["settings"]["data"]), "mode": record["mode"]}
        if record["mode"] == "formula":
            settings["formula"] = record["formula"]
        else:
            settings["params"] = copy.deepcopy(cells[sweep_index]["settings"]["params"] if sweep_index is not None else record["settings"]["params"])
        from .run_restore import restore_files

        code = record["code"] if record["mode"] == "code" else None
        report = await restore_files(self.ctx, obj, code["snapshotPath"] if code else None, record.get("common"), self.common.root,
                                     expected={item["path"]: item["sha256"] for item in code["files"]} if code else None)
        self.store.check_version(self.store.read(obj["id"]), obj["version"])
        return {**self.store.detail(self.store.save(obj, settings=settings)), "restore": report}

    async def create_from(self, identity, kind, sweep_index=None):
        """A new model or strategy that reads this run, with this run's data range and held-out start (the dates it used, not "to the latest"),
        its holding and its costs; nothing has to be published first. It reads this run as it was: a strategy trades the values this run
        computed, a model takes this run as an input. Created through the target's own entry, the checks its settings always get."""
        record = self.read(identity)
        if record["state"] != "completed" or record["runKind"] not in {"run", "sweep"}:
            raise ContractError("invalid_input", "Use a completed run.")
        if kind not in {"model", "strategy"}:
            raise ContractError("invalid_input", "A run can start a model or a strategy.")
        obj = self.store.read(record["objectId"])
        run = dict(record["settings"]["run"])
        if sweep_index is not None:
            cells = self.sweep(identity)["cells"]
            if not 0 <= sweep_index < len(cells):
                raise ContractError("invalid_input", "No such combination in this sweep.")
            if cells[sweep_index]["settings"].get("params") and cells[sweep_index]["settings"]["params"] != record["settings"]["params"]:
                raise ContractError("invalid_input", "This combination changes the code's own settings, whose values were not kept. Use these settings and run them first.")
            run.update({key: value for key, value in cells[sweep_index]["settings"].items() if key not in {"heldOutFrom", "params"}})
        label = f"#{record['number']}" + (f" ({sweep_index + 1})" if sweep_index is not None else "")
        name = f"{obj['name']} {label} {kind}"
        data = {key: value for key, value in record["settings"]["data"].items() if key != "untilLatest"}
        # A factor has no risk-free rate (its numbers are of a book that pays for itself): the model or strategy takes its own default.
        if kind == "model":
            return await self.store.start_from("model", name, data=data, params={"inputs": [{"source": "run", "ref": identity, "name": f"{obj['name']} {label}"}]},
                                               run={"cost": run["costBps"]})
        kept = self.directory(identity) / "signal-values.parquet"
        last = await asyncio.to_thread(self.signal_values, identity, record, kept)
        bps = float(run["costBps"])
        # The run's one cost of a trade is the strategy's whole cost of a trade; its holding is how many daily signals a position is kept.
        costs = {"commissionBps": bps, "feePct": bps / 100, "makerPct": bps / 100, "halfSpreadBps": 0.0, "impact": 0.0, "borrowPct": float(run.get("borrowPct", 0.0))}
        return await self.store.start_from("strategy", name, preset="ranked", data=data, params={"rebalance_every": int(run["holding"])},
                                           run={"costs": costs},
                                           keep={"signalRun": {"param": "signal", "runId": identity, "model": obj["name"], "number": record["number"], "kind": "factor",
                                                               "path": str(kept), "lastValueAt": last}})

    def signal_values(self, identity, record, destination):
        """This run's values (before preprocessing, the ones a published factor gives) for the names that were members each day, as a file of
        `datetime`, `instrument`, `value` rows, the shape a strategy reads a signal in; returns the last day with a value."""
        import numpy as np
        import pandas as pd
        output = self.directory(identity) / "output"
        cross = output / "cross"
        if not (cross / "meta.json").is_file():
            raise ContractError("not_found", "This run's values were not kept (only the newest runs and starred ones keep them); run the factor again.")
        meta = json.loads((cross / "meta.json").read_text(encoding="utf-8"))
        values = np.load(output / "values.npy") if (output / "values.npy").is_file() else np.load(cross / "raw.npy").astype(float)
        member = np.load(cross / "missing.npy") != 1
        info = json.loads((self.data.directory(record["data"]["key"]) / "info.json").read_text(encoding="utf-8"))
        instruments = {item["column"]: item["instrumentId"] for item in info["instruments"]}
        t, i = np.nonzero(np.isfinite(values) & member)
        if not len(t):
            raise ContractError("invalid_input", "This run has no values to trade.")
        labels, columns = np.asarray(meta["labels"], dtype="int64"), np.asarray(meta["columns"], dtype=object)
        table = pd.DataFrame({"datetime": labels[t], "instrument": [instruments.get(name, name) for name in columns[i]], "value": values[t, i].astype(float)})
        destination.parent.mkdir(parents=True, exist_ok=True)
        table.to_parquet(destination, index=False)
        return int(labels[t].max())

    async def cancel(self, identity):
        record = self.read(identity)
        if record["state"] in TERMINAL:
            return summary(record)
        task = self.tasks.get(identity)
        if task is None:
            raise ContractError("conflict", "This run is not running in this session.")
        self.save(record, state="cancel_requested")
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)
        return summary(self.read(identity))

    def annotate(self, identity, args):
        record = self.read(identity)
        changes = {key: args[key] for key in ("note", "starred") if key in args}
        return summary(self.save(record, **changes))

    def active(self, workspace_id):
        return [self.read(identity) for identity in self.tasks if self.read(identity)["workspaceId"] == workspace_id]

    async def close(self):
        self.closed = True
        tasks = list(self.tasks.values()) + list(self.result_tasks.values())
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)


def project_files(directory):
    """The text of every Python file of a project, by relative path, for reading its declared settings."""
    root = filesystem_path(directory)
    out = {}
    for path in Path(root).rglob("*.py"):
        relative = path.relative_to(root)
        if any(part in EXCLUDED for part in relative.parts):
            continue
        with contextlib.suppress(OSError, UnicodeDecodeError):
            out[relative.as_posix()] = path.read_text(encoding="utf-8")
    return out
