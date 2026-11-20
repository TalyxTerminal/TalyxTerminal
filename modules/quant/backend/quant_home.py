"""Quant's home and the list of all objects: what to handle now, what runs and waits, and each object with the run that stands for it.

To handle lists only what needs a hand now, each with one action: a library version that failed, misses periods or lacks an input (an
input that failed itself is listed alone) and a research object pinned on a stopped version. Weakening, risk drift and late items only count toward a link to the library.

The run that stands for an object is the one published from; else the starred one; else the newest successful one (not simply the newest:
after a batch the newest may be a failure or the worst). Its key numbers depend on the kind. All of it is read in one pass over the run
records and the library's catalog, never by opening each run.
"""
from __future__ import annotations

import json

from talyx_sdk import ContractError

from . import library_summary
from .changes import library_pins
from .notebook_extract import data_settings
from .notebook_file import parse

RUN_FIELDS = ("id", "objectId", "objectKind", "runKind", "state", "starred", "createdAt", "number", "message", "tags", "horizon")
KEY_NUMBERS = {
    "factor": (("rank_ic", "rankIc"), ("t", "t"), ("long_short_net", "longShortNet"), ("turnover", "turnover")),
    "model": (("test_rank_ic", "ric"), ("icir", "icir"), ("long_short_net", "lsNet")),
    "allocation": (("sharpe", "sharpe"), ("return_a_year", "cagr"), ("volatility", "vol"), ("max_drawdown", "maxDrawdown")),
    "strategy": (("return_a_year", "cagr"), ("sharpe", "sharpe"), ("max_drawdown", "maxDrawdown"), ("trades", "trades")),
}


def key_numbers(kind, run):
    if run is None:
        return None
    source = run.get("numbers" if kind in {"factor", "model"} else "metrics") or {}
    out = {name: source.get(key) for name, key in KEY_NUMBERS.get(kind, ())}
    if not any(value is not None for value in out.values()):
        return None
    if kind == "factor":
        out["horizon"] = f"{run.get('holding') or 1}D"
    if kind == "model" and run.get("horizon"):
        out["horizon"] = f"{run['horizon']}D"
    return out


def run_summary(record):
    """A finished run's headline measures in their declared units, as the result card displays them."""
    if record["state"] not in {"completed", "failed", "cancelled", "interrupted"}:
        return None
    kind = record["objectKind"]
    numbers = key_numbers(kind, record) or {}
    percentages = {"long_short_net", "turnover", "return_a_year", "volatility", "max_drawdown"}
    labels = {"rank_ic":"Rank IC", "test_rank_ic":"Test rank IC", "icir":"ICIR", "t":"t-statistic",
              "long_short_net":"Long–short net", "turnover":"Turnover", "return_a_year":"Annual return",
              "volatility":"Volatility", "max_drawdown":"Max drawdown", "sharpe":"Sharpe", "trades":"Trades"}
    metrics = []
    for name, _ in KEY_NUMBERS.get(kind, ()):
        value = numbers.get(name)
        if not isinstance(value, (int, float)):
            continue
        percent = name in percentages
        signed = name in {"rank_ic", "test_rank_ic", "long_short_net", "return_a_year", "sharpe", "icir"}
        metrics.append({"label": labels[name], "value": f"{value * 100 if percent else value:.4g}",
                        "unit": "%" if percent else "", "direction": ("up" if value > 0 else "down")
                        if signed and value else "none"})
    return {"title": record["name"], "subtitle": f"{kind.capitalize()} · {record['state']}", "keyNumbers": metrics,
            "openTarget": {"module":"quant", "panelType":"workspace", "objectId":"run:"+record["id"]}}


class QuantHome:
    def __init__(self, services):
        self.services = services
        self.notebooks = {}  # object id -> (file and its modification time, its cells and what it reads)

    # --- every object with its representative run ----------------------------------------------------------------------------
    def runs_by_object(self, object_id=None):
        """One pass over the run records: each object's runs, newest first, with only what the lists need."""
        fields = ", ".join(f"json_extract(content,'$.{name}')" for name in RUN_FIELDS)
        clause = " AND json_extract(content,'$.objectId')=?" if object_id is not None else ""
        rows = self.services.store.records.db.execute(
            f"SELECT {fields}, json_extract(content,'$.numbers'), json_extract(content,'$.metrics'), json_extract(content,'$.checks'), "
            "json_extract(content,'$.settings.run.holding'), json_extract(content,'$.error.message') FROM records WHERE kind='qrun' "
            f"AND json_extract(content,'$.runKind') NOT IN ('case','publish'){clause} ORDER BY json_extract(content,'$.createdAt') DESC",
            (object_id,) if object_id is not None else ()).fetchall()
        out = {}
        for row in rows:
            run = dict(zip(RUN_FIELDS, row[:len(RUN_FIELDS)], strict=True))
            numbers, metrics, checks, holding, error = row[len(RUN_FIELDS):]
            run.update(numbers=json.loads(numbers) if numbers else None, metrics=json.loads(metrics) if metrics else None,
                       checks=json.loads(checks) if checks else None, holding=holding, error=error, starred=bool(run["starred"]))
            out.setdefault(run["objectId"], []).append(run)
        return out

    @staticmethod
    def representative(runs, published):
        candidates = [run for run in runs if run["runKind"] in {"run", "train", "backtest"} and run["state"] == "completed"]
        for how, test in (("published", lambda run: run["id"] in published), ("starred", lambda run: run["starred"] and run["state"] == "completed"),
                          ("latest_successful", lambda run: "notebook" not in (json.loads(run["tags"]) if isinstance(run.get("tags"), str) else run.get("tags") or []))):
            found = next((run for run in candidates if test(run)), None)
            if found is not None:
                versions = published.get(found["id"], [])
                return found, {"run": found["id"], "number": found.get("number"), "how": how, "version": f"v{versions[0]['version']}" if versions else None}
        failed = next((run for run in runs if run["state"] == "failed"), None)
        note = f"#{failed['number']} failed: {failed.get('message') or failed.get('error') or ''}".strip() if failed and failed.get("number") else \
            ("The newest run failed" if failed else None)
        return None, {"run": None, "number": None, "how": "none", "note": note}

    def objects(self, args):
        """GET /objects: the navigation's kinds and folders, and each object with its library versions, representative run and key numbers."""
        store = self.services.store
        listed = store.list({key: value for key, value in args.items() if key != "scope"} | ({"all": True} if args.get("scope") == "all" else {}) | {"lastRun": False})
        runs = self.runs_by_object()
        published = self.services.library.store.published_runs()
        by_object = {}
        for version in self.services.library.store.version_states():
            if version["object_id"]:
                by_object.setdefault(version["object_id"], []).append(version)
        current = store.workspace()
        items = []
        for item in listed["items"]:
            own = runs.get(item["id"], [])
            chosen, representative = self.representative(own, published)
            versions = sorted(by_object.get(item["id"], []), key=lambda version: -version["version"])
            checks = chosen.get("checks") if chosen else None
            items.append({**item, "lastRun": self.last_of(own), "workspace": item["workspaceId"], "inThisWorkspace": item["workspaceId"] == current,
                          "library": {"versions": [{"versionId": version["id"], "version": version["version"], "state": library_summary.public_state(version),
                                                    "auto": version["auto"]} for version in versions],
                                      "text": ", ".join(f"v{version['version']}" + ("" if library_summary.public_state(version) == "producing" else f" {library_summary.public_state(version)}")
                                                        + ("" if version["auto"] or library_summary.public_state(version) != "producing" else " auto off") for version in versions)}
                          if versions else None,
                          "representative": representative, "keyNumbers": key_numbers(item["kind"], chosen),
                          "checks": {"passed": sum(1 for check in checks if check.get("state") == "ok"), "total": len(checks)} if checks else None,
                          "runsCount": len(own), "changedAt": max([item["updatedAt"], *(run["createdAt"] for run in own[:1])]),
                          **(self.notebook(item["id"]) if item["kind"] == "notebook" else {})})
        return {**listed, "items": items}

    def notebook(self, object_id):
        """A notebook's number of cells and what it reads (the library items and runs its cells name in q.library.read and q.run), read from its
        file again only when the file changed."""
        store = self.services.store
        try:
            path = store.source_path(store.read(object_id))
            stamp = (str(path), path.stat().st_mtime_ns)
        except (OSError, ContractError):
            return {"cells": None, "reads": None}
        cached = self.notebooks.get(object_id)
        if cached and cached[0] == stamp:
            return cached[1]
        try:
            cells = parse(path.read_text(encoding="utf-8"))["cells"]
        except (OSError, ContractError):
            facts = {"cells": None, "reads": None}
        else:
            found = data_settings([cell for cell in cells if cell["type"] == "code"])
            reads = [f"{item['name']} v{item['version']}" if item["version"] else item["name"] for item in found["libraryInputs"]] + \
                [f"{item['object']} #{item['number']}" if "number" in item else item["object"] for item in found["runs"]]
            facts = {"cells": len(cells), "reads": ", ".join(dict.fromkeys(reads)) or None}
        self.notebooks[object_id] = (stamp, facts)
        return facts

    @staticmethod
    def last_of(runs):
        run = runs[0] if runs else None
        return None if run is None else {"id": run["id"], "state": run["state"], "createdAt": run["createdAt"], "runKind": run["runKind"], "number": run.get("number")}

    async def create_from_library(self, version_id, kind, name=None):
        """A new model, strategy or allocation that reads one library version: the item's data settings are copied and the version is pinned."""
        from talyx_sdk import ContractError
        if kind not in ("model", "strategy", "allocation"):
            raise ContractError("invalid_input", "A library version can start a model, a strategy or an allocation.")
        library = self.services.library
        version = library.read_version(version_id)
        if version["state"] == "delisted" or version["archivedAt"] is not None:
            raise ContractError("conflict", "Start from a producing version; this one was stopped.")
        store = self.services.store
        # A strategy reads a library item through a ready-made strategy's signal: Top N for a factor, a model or data, Rebalance for an allocation's
        # weights; the item is its signal and the version is pinned the way a run pins it (the empty skeleton read nothing).
        preset = ("rebalance" if version["kind"] == "allocation" else "ranked") if kind == "strategy" else None
        label = name or f"{version['name']} v{version['version']} {kind}"
        # The item's data range as the object takes it: a producing item runs to the latest close, so `untilLatest` becomes that;
        # the held-out start the item was researched with stays held out.
        data = dict(version["data"] or {})
        if data.pop("untilLatest", False):
            data["until"] = "latest"
        if kind == "strategy" and data:
            target = await store.start_from("strategy", label, preset=preset, data=data, params={"signal": version["name"]},
                                            keep={"inputs": {"library": {"signal": {"name": version["name"], "versionId": version_id}}, "models": {}}})
        elif kind == "model" and data:
            target = await store.start_from("model", label, data=data, params={"inputs": [{"source": "library", "ref": version_id, "name": version["name"]}]})
        else:
            created = await store.create({"kind": kind, "name": label, **({"preset": preset} if preset else {})})
            target = store.read(created["id"])
            settings = {**target["settings"], **({"data": data} if data else {}),
                        "inputs": [{"kind": "library", "itemId": version["itemId"], "versionId": version_id, "name": version["name"], "version": version["version"]}]}
            if preset:
                settings = {**settings, "params": {**(target["settings"].get("params") or {}), "signal": version["name"]},
                            "inputs": {"library": {"signal": {"name": version["name"], "versionId": version_id}}, "models": {}}}
            store.save(target, settings=settings)
        target = store.read(target["id"])
        if kind != "strategy":
            with library_pins(library, target):
                library.acquire(version_id, "qobject:" + target["id"], target["workspaceId"], kind=kind, name=target["name"], how=f"research, pinned v{version['version']}")
        return store.detail(target)

    def switch_version(self, object_id, version_id):
        """A research object stops reading a stopped version and reads this one: its library inputs of the same item point at the new version, and what it holds in the library follows."""
        from talyx_sdk import ContractError
        library, store = self.services.library, self.services.store
        version = library.read_version(version_id)
        if version["state"] == "delisted" or version["archivedAt"] is not None:
            raise ContractError("conflict", "Switch to a producing version; this one was stopped.")
        target = store.read(object_id)
        settings = target["settings"]
        if target["kind"] == "strategy":
            inputs = store.strategy_bindings(target, settings, {version["name"]: version_id})
            store.save(target, settings={**settings, "inputs": inputs})
            return {"objectId": object_id, "versionId": version_id, "version": version["version"]}
        inputs = [{**item, "versionId": version_id, "version": version["version"]} if item.get("kind") == "library" and item.get("itemId") == version["itemId"] else item
                  for item in settings.get("inputs") or []]
        if inputs != (settings.get("inputs") or []):
            target = store.save(target, settings={**settings, "inputs": inputs})
        owner = "qobject:" + object_id
        held = library.store.held_by(owner)
        keep = {other for other in held if library.store.version(other)["item_id"] != version["itemId"]} | {version_id}
        with library_pins(library, target):
            library.pin(owner, target["workspaceId"], kind=target["kind"], name=target["name"], how=f"research, pinned v{version['version']}", version_ids=list(keep))
        return {"objectId": object_id, "versionId": version_id, "version": version["version"]}

    def recent(self, limit=10):
        found = self.objects({})
        return sorted(found["items"], key=lambda item: -item["changedAt"])[:limit]

    # --- to handle --------------------------------------------------------------------------------------------------------
    def attention(self):
        """GET /quant/attention: what needs a hand now, one action each; and how many weakening or late items wait in the library."""
        rows = self.services.library.store.summaries()
        items, watch = library_summary.attention(rows)
        items.extend(self.pinned_on_stopped())
        return {"items": items, "watchInLibrary": watch}

    def pinned_on_stopped(self):
        """Research objects whose newest run reads a version that stopped: their values end that day; a producing version can take its place."""
        out = []
        library = self.services.library.store
        for row in library.summaries():
            for version in row["versions"]:
                if version["state"] != "stopped" or not version["users"]:
                    continue
                producing = [other for other in row["versions"] if other["state"] == "producing"]
                for user in library.users(version["versionId"]):
                    if not user["owner_id"].startswith("qobject:"):
                        continue
                    newer = producing[0] if producing else None
                    out.append({"tone": "watch", "label": "On a stopped version", "state": "pinned_stopped",
                                "subject": {"kind": user["kind"] or "object", "id": user["owner_id"].removeprefix("qobject:"), "name": user["name"]},
                                "why": f"reads {row['name']} v{version['version']}, stopped {library_summary_day(version['stoppedAt'])}; its values end that day."
                                       + (f" v{newer['version']} is producing" if newer else ""),
                                "since": version["stoppedAt"], "action": {"id": "switch_version", "label": f"Switch to v{newer['version']}" if newer else "Open the library item",
                                                                          "versionId": newer["versionId"] if newer else None, "itemId": row["itemId"]}})
        return out

    # --- running and waiting -----------------------------------------------------------------------------------------------
    def queue(self):
        """GET /quant/queue: every heavy job Quant runs or waits to run (factor runs, sweeps, publications, training, library updates and the next round)."""
        services = self.services
        jobs = []
        queue = services.factors.queue.view()
        for entry in [*queue["running"], *queue["waiting"]]:
            if entry["owner"].startswith("library:"):
                continue
            run = services.runs.records.find("qrun", entry["owner"])
            subject = {"kind": run["objectKind"], "id": run["objectId"], "name": run["name"], "runId": run["id"]} if run else {"kind": "run", "id": entry["owner"], "name": entry["label"]}
            progress = (run or {}).get("progress") or {}
            steps = progress.get("steps") or []
            done = sum(1 for step in steps if step.get("state") == "done")
            jobs.append({"subject": subject, "what": entry["label"] + (f", {progress.get('stage')}" if progress.get("stage") else ""),
                         "progress": round(done / 7, 2) if entry["state"] == "running" and steps else None, "elapsed": entry.get("started"),
                         "state": "running" if entry["state"] == "running" else "queued", "can_stop": run is not None})
        for identity in list(services.runs.tasks):
            run = services.runs.records.find("qrun", identity)
            if run is None or any(job["subject"].get("runId") == identity for job in jobs):
                continue
            progress = run.get("progress") or {}
            what = ("Publishing " + run["publish"]["name"]) if run["runKind"] == "publish" else f"#{run.get('number') or ''} {run['runKind']}".strip()
            if progress.get("of"):
                what += f", check {progress.get('step')} of {progress['of']}"
            elif run.get("stage"):
                what += f", {run['stage']}"
            jobs.append({"subject": {"kind": run["objectKind"], "id": run["objectId"], "name": run["name"], "runId": identity}, "what": what,
                         "progress": round(progress["step"] / progress["of"], 2) if progress.get("of") else None, "elapsed": run["createdAt"],
                         "state": "running", "can_stop": True})
        production = services.loop.view()
        for entry in production["running"]:
            jobs.append({"subject": {"kind": "library", "id": entry["itemId"], "name": f"{entry['name']} v{entry['version']}", "versionId": entry["versionId"]},
                         "what": "Library update", "progress": None, "elapsed": entry["startedAt"], "state": "running", "can_stop": True})
        for entry in production["queued"]:
            jobs.append({"subject": {"kind": "library", "id": entry["itemId"], "name": f"{entry['name']} v{entry['version']}", "versionId": entry["versionId"]},
                         "what": "Library update, waiting for its inputs or a free slot", "state": "queued", "can_stop": True})
        for entry in production["retries"]:
            jobs.append({"subject": {"kind": "library", "id": entry["itemId"], "name": f"{entry['name']} v{entry['version']}", "versionId": entry["versionId"]},
                         "what": f"Retry at {library_summary_clock(entry['at'])}" + (f", try {entry['tries'] + 1}" if entry.get("tries") else ""), "at": entry["at"],
                         "state": "queued", "can_stop": False})
        for entry in production["waiting"]:
            jobs.append({"subject": {"kind": "library", "id": entry["itemId"], "name": f"{entry['name']} v{entry['version']}", "versionId": entry["versionId"]},
                         "what": f"Waiting for its bar, next look {library_summary_clock(entry['at'])}", "at": entry["at"], "state": "queued", "can_stop": False})
        return {"jobs": jobs, "production": production}


def library_summary_day(milliseconds):
    """"Apr 30", with the year when it is not this year's."""
    import time
    if not milliseconds:
        return "earlier"
    day = time.gmtime(milliseconds / 1000)
    return f"{time.strftime('%b', day)} {day.tm_mday}" + ("" if day.tm_year == time.gmtime().tm_year else f", {day.tm_year}")


def library_summary_clock(milliseconds):
    """"Nov 4, 16:30 UTC"; the page shows it in the viewer's own time."""
    import time
    if not milliseconds:
        return "—"
    moment = time.gmtime(milliseconds / 1000)
    return f"{time.strftime('%b', moment)} {moment.tm_mday}, {time.strftime('%H:%M', moment)} UTC"
