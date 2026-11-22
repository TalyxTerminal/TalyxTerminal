"""Publishing a model run into the library: what it publishes and its plan. Keeping it current is the library loop's (`PipelineProducer` asks this class for a
version's new values; the loop gives them their dates, lateness, periods and figures like any other version's).

A model is published as one item, kind "model", whose values are its predictions. The factor runs it uses that are not published yet
are published with it, as factor items, and the model's version occupies each factor version it reads. Before anything is added,
the future-data check retrains only the window covering a cut-off point on the data cut there and compares its predictions with the
run's. The version keeps the run's window cache: on a retraining day the update trains only the new window, on other days the newest
window's model predicts the new bars. A period in which a factor it reads has no new value is "upstream missing" and made from nothing older.
"""
from __future__ import annotations

import asyncio
import json
import os
import shutil
import time
from pathlib import Path
from uuid import uuid4

import numpy as np
import pandas as pd
from talyx_sdk import ContractError
from talyx_sdk.i18n import t

from ..native import metrics as M
from . import library_after, library_checks, research_calendar
from . import model_prep as P
from .library_publish import edge_times, long_rows, measure_text, settings_text, universe_text
from .object_data import day_ms
from .project_paths import filesystem_path

RUN = "qrun"
CHECK_AT = 0.75  # where the future-data check cuts the data, as a share of the run's test bars


def day_of(milliseconds):
    return time.strftime("%Y-%m-%d", time.gmtime(milliseconds / 1000))


class ModelLibrary:
    def __init__(self, ctx, records, models, library):
        self.ctx, self.records, self.models, self.library = ctx, records, models, library
        self.store = library.store
        self.tasks = {}

    # ------------------------------------------------------------------------------------------------------------ the plan
    def cutoff_window(self, record):
        """The window the future-data check retrains, and the bar it cuts the data at (inside that window's test period)."""
        windows = [item for item in record.get("windows") or [] if item.get("test")]
        if not windows:
            raise ContractError("invalid_input", t("This run has no windows to check; publish a run trained window by window."))
        first, last = windows[0]["test"]["from"], windows[-1]["test"]["to"]
        point = first + (last - first) * CHECK_AT
        window = next((item for item in windows if item["test"]["from"] <= point <= item["test"]["to"]), windows[-1])
        labels = json.loads((self.models.directory(record["id"]) / "output" / "pred.json").read_text(encoding="utf-8"))["labels"]
        inside = [label for label in labels if window["test"]["from"] <= label <= window["test"]["to"]]
        cut = inside[len(inside) // 2] if inside else window["test"]["from"]
        return window, int(cut)

    def cutoff_windows(self, record):
        """A combined run: the window to check for each source, with the source's own cut-off."""
        return [self.cutoff_window(self.models.read(source)) for source in record["combinedFrom"]]

    def pin_of(self, name, version, version_id):
        """A factor version the model reads, with whether it is still producing."""
        state = self.store.version(version_id)
        public = "archived" if state.get("archived_at") else "stopped" if state["state"] in ("stopped", "delisted", "expired") else "producing"
        return {"name": name, "version": version, "versionId": version_id, "state": public}

    def blocked_reason(self, name, found, pinned):
        """Why this cannot be published: the name is another kind in the library (field "name"), or an input version is stopped or archived (field "inputs":
        publishing without new values still goes out, with what the model has until today)."""
        other = [item for item in found if item["kind"] != "model"]
        if other:
            return {"reason": t("{name} is already a {kind} in the library; choose another name.", name=name, kind=other[0]['kind']), "field": "name"}
        for pin in pinned:
            if pin["state"] in ("stopped", "archived"):
                item = self.store.version(pin["versionId"])["item_id"]
                newer = [entry for entry in self.store.item_versions(item) if entry["version"] > pin["version"] and entry["state"] == "updating" and entry["archivedAt"] is None]
                action = f"retrain on v{newer[0]['version']}" if newer else "retrain on a producing version"
                return {"reason": t("{name} v{version} is {state}; {action}, or publish without new values.", name=pin['name'], version=pin['version'], state=pin['state'], action=action), "field": "inputs"}
        return None

    def compare_rows(self, record, previous):
        """This run's numbers beside the previous version's (a different label length makes the Rank IC not comparable, and says so)."""
        before = self.store.version(previous["versionId"])["meta"]
        old = (before.get("report") or {}).get("metrics") or {}
        new = record.get("numbers") or self.models.experiment(record["id"]).get("metrics") or {}
        old_horizon = (before.get("horizon") or {}).get("holding")
        new_horizon = record.get("horizon") or int(record["settings"]["params"].get("label_days") or 5)
        rows = []
        for measure, key in (("Test Rank IC", "ric"), ("t", "t"), ("Long–short net", "lsNet"), ("Turnover", "turnover")):
            before_value, now = old.get(key), new.get(key)
            row = {"measure": measure, "previous": before_value, "current": now, "change": None if before_value is None or now is None else now - before_value}
            if key == "ric" and old_horizon and old_horizon != new_horizon:
                row.update(change=None, note=f"v{previous['version']} predicts {old_horizon}D")
            rows.append(row)
        return rows

    def params_diff(self, record, previous):
        before = self.store.version(previous["versionId"])["meta"].get("params") or {}
        now = {**record["settings"]["params"], **(record.get("overrides") or {})}
        return [{"name": key, "previous": before.get(key), "current": now.get(key)} for key in sorted(set(before) | set(now))
                if key not in ("inputs",) and before.get(key) != now.get(key)]

    def plan(self, run_id, name=None):
        """What publishing this run does: the one model item and its next version, the factors published with it, the versions it
        pins, the checks to look at, who uses the item now, and the future-data check."""
        record = self.models.read(run_id)
        if record["state"] != "completed" or record["runKind"] not in ("train", "combine"):
            raise ContractError("invalid_input", t("Publish a completed training run."))
        name = (name or record["name"]).strip()
        found = self.store.find_item(name)
        existing = [item for item in found if item["kind"] == "model"]
        versions = self.store.item_versions(existing[0]["id"]) if existing else []
        also, pinned = [], []
        for pin in record.get("pins") or []:
            if pin.get("runId"):
                factor = self.models.factors.read(pin["runId"])
                if factor.get("version"):
                    pinned.append(self.pin_of(factor["version"]["name"], factor["version"]["version"], factor["version"]["versionId"]))
                else:
                    also.append({"name": self.models.store.read(factor["objectId"])["name"], "fromRun": factor["number"], "runId": factor["id"],
                                 "formula": factor.get("formula"), "autoUpdate": factor.get("mode") == "formula"})
            elif pin.get("versionId"):
                pinned.append(self.pin_of(pin["name"], pin["version"], pin["versionId"]))
        checks = self.cutoff_windows(record) if record["runKind"] == "combine" else [self.cutoff_window(record)]
        window, cut = checks[0]
        users = self.library.users(versions[0]["versionId"]) if versions else []
        return {"runId": run_id, "number": record["number"], "name": name, "version": (versions[0]["version"] + 1) if versions else 1,
                "previous": {"version": versions[0]["version"], "versionId": versions[0]["versionId"], "state": versions[0]["state"]} if versions else None,
                "produces": "model", "autoUpdate": True, "alsoPublished": also, "pinned": pinned,
                "checksToReview": [item for item in record.get("checks") or [] if item["state"] == "watch"], "measures": (self.models.experiment(run_id).get("metrics") or {}),
                "users": [{"ownerId": item["ownerId"], "workspaceId": item.get("workspaceId"), "name": item["name"], "kind": item["kind"]} for item in users],
                "compare": self.compare_rows(record, versions[0]) if versions else [], "paramsDiff": self.params_diff(record, versions[0]) if versions else [],
                "blocked": self.blocked_reason(name, found, pinned),
                "futureDataCheck": ([{"runId": source, "window": found_window["k"], "cutoff": point} for source, (found_window, point) in zip(record["combinedFrom"], checks, strict=True)]
                                    if record["runKind"] == "combine" else
                                    {"window": window["k"], "cutoff": cut, "text": t("Retrains window {k} on the data cut at {cut} and compares its predictions", k=window['k'], cut=day_of(cut))}),
                "schedule": {"every": record["settings"]["params"].get("every_months"), "text": t("Retrains a new window every {every_months} months; other days the newest window predicts", every_months=record['settings']['params'].get('every_months'))}}

    # ------------------------------------------------------------------------------------------------------------ publishing
    async def publish(self, run_id, args):
        name = (args.get("name") or "").strip()
        if not name or len(name) > 120:
            raise ContractError("invalid_input", t("Choose a name of 1 to 120 characters."))
        plan = self.plan(run_id, name)
        if plan["blocked"]:
            # An input version that is stopped lets the model go out when it does not produce new values (it keeps only what it has until today).
            if not (plan["blocked"]["field"] == "inputs" and args.get("autoUpdate", True) is False):
                raise ContractError("conflict", plan["blocked"]["reason"], {"blocked": plan["blocked"]})
        source = self.models.read(run_id)
        identity = "run-" + uuid4().hex
        record = self.records.put(RUN, {"id": identity, "objectId": source["objectId"], "objectKind": "model", "modelPublish": True, "name": source["name"], "runKind": "publish",
                                        "mode": "publish", "workspaceId": self.models.store.workspace(), "state": "running", "stage": "future", "createdAt": int(time.time() * 1000),
                                        "endedAt": None, "settings": source["settings"], "params": source["params"], "message": None, "error": None,
                                        "code": None, "common": None, "data": None, "environment": None, "recipe": None, "metrics": {}, "starred": False, "note": "",
                                        "exitCode": None, "overrides": {}, "publish": {"sourceRunId": run_id, "name": name, "autoUpdate": bool(args.get("autoUpdate", True)),
                                                                                        "plan": plan, "versions": []}})
        self.ctx.publish("object_run_changed", {"runId": identity, "objectId": record["objectId"], "workspaceId": record["workspaceId"], "state": "running"})
        self.tasks[identity] = self.ctx.start(self.execute(identity), run_record=record)
        return self.view(self.records.find(RUN, identity))

    @staticmethod
    def view(record):
        publish = record["publish"]
        return {"id": record["id"], "runId": record["id"], "state": record["state"], "stage": record["stage"], "message": record.get("message"), "name": publish["name"],
                "sourceRunId": publish["sourceRunId"], "versions": publish["versions"], "future": publish.get("future"), "factor": publish.get("factor"), "futureOf": publish.get("futureOf"), "createdAt": record["createdAt"], "endedAt": record.get("endedAt"),
                "cancellable": record["state"] not in {"completed", "failed", "cancelled", "interrupted"}}

    def read(self, identity):
        record = self.records.find(RUN, identity)
        if record is None or not record.get("modelPublish"):
            raise ContractError("not_found", t("This model publication does not exist."))
        return self.view(record)

    def owns(self, identity):
        record = self.records.find(RUN, identity)
        return bool(record and record.get("modelPublish"))

    async def cancel(self, identity):
        task = self.tasks.get(identity)
        if task is not None:
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)
        return self.read(identity)

    def _put(self, identity, **changes):
        record = self.records.find(RUN, identity)
        updated = self.records.put(RUN, {**record, **changes})
        self.ctx.publish("object_run_changed", {"runId": identity, "objectId": updated["objectId"], "workspaceId": updated["workspaceId"], "state": updated["state"]})
        return updated

    async def execute(self, identity):
        record = self.records.find(RUN, identity)
        publish = record["publish"]
        directory = self.models.directory(identity)
        ending, created, folders = {}, [], []
        try:
            directory.mkdir(parents=True, exist_ok=True)
            source = self.models.read(publish["sourceRunId"])
            ready = await self.models.ready_data(source["id"])
            checks = publish["plan"]["futureDataCheck"]
            if isinstance(checks, list):
                # A combined run: each of its sources is retrained at its own cut-off and compared; all must pass.
                futures = []
                for number, item in enumerate(checks):
                    self._put(identity, stage="future", publish={**self.records.find(RUN, identity)["publish"], "futureOf": {"n": number + 1, "of": len(checks)}})
                    futures.append(await self.future_check(self.models.read(item["runId"]), ready, directory / f"future-{number}", item))
                failed = next((item for item in futures if not item["passed"]), None)
                future = failed or {**futures[0], "sources": futures}
            else:
                future = await self.future_check(source, ready, directory, checks)
            self._put(identity, stage="commit", publish={**publish, "future": future})
            if not future["passed"]:
                raise ContractError("future_data", future["title"], {"future": future})
            created, folders = await self.commit(identity, source, ready, publish)
            ending = {"state": "completed", "message": None}
        except asyncio.CancelledError:
            ending = {"state": "cancelled", "message": t("Stopped before anything was added to the library.")}
        except ContractError as error:
            ending = {"state": "failed", "message": error.message, "error": error.to_dict()}
        except Exception as error:  # noqa: BLE001 - the publication ends and says what broke
            import logging
            logging.getLogger(__name__).exception("Model publication failed (%s)", identity)
            ending = {"state": "failed", "message": t("Quant hit an internal error ({name}) while publishing; nothing was added.", name=type(error).__name__)}
        finally:
            self.tasks.pop(identity, None)
            if ending.get("state") != "completed":
                for folder in folders:
                    shutil.rmtree(filesystem_path(folder), ignore_errors=True)
            record = self.records.find(RUN, identity)
            self._put(identity, **ending, endedAt=int(time.time() * 1000), publish={**record["publish"], "versions": [item["versionId"] for item in created]})
            if created:
                self.library.publish_change(created[-1]["itemId"], created[-1]["versionId"])

    async def future_check(self, source, ready, directory, spec):
        """The window covering the cut-off retrained on the data cut there; its predictions up to the cut must equal the run's."""
        job = await self.models.job(source["id"], ready)
        source_cache = self.models.directory(source["id"]) / "windows"
        written = directory / "future" / "run"   # what the check writes: its output and the window it retrains
        job.update(mode="cutoff", cutoff=int(spec["cutoff"]), windows=[int(spec["window"])], sourceCache=str(source_cache),
                   output=str(written / "output"), cache=str(written / "windows"), keepDone=False)
        answer = await self.models.engine.run_model(job, directory / "future" / "engine", context={"objectId": source["objectId"], "runId": source["id"]})
        if not answer.get("ok"):
            raise ContractError("unavailable", t("The future-data check could not run: {message}", message=answer['failure']['message']), {"failure": answer["failure"]})
        future = {"passed": answer["passed"], "value": "unchanged" if answer["passed"] else "changed", "title": answer["title"], "window": answer["window"], "cutoff": int(spec["cutoff"])}
        checks = [item if item["id"] != "future_data" else {**item, "value": future["value"], "state": "ok" if future["passed"] else "watch", "title": future["title"]}
                  for item in source.get("checks") or []]
        self.models.save(self.models.read(source["id"]), checks=checks, future=future)
        return future

    async def factor_values(self, factor, ready, directory):
        """A factor run's values on its own data: the formula on the frozen bars, or the values a code run kept."""
        if factor.get("mode") == "formula":
            job = {"data": {key: ready[key] for key in ("key", "barsPath", "info", "extrasPath")}, "formula": factor["formula"], "after": 0, "output": str(directory)}
            directory.mkdir(parents=True, exist_ok=True)
            answer = await self.models.engine.call("formula_values", job)
            if not answer.get("ok"):
                raise ContractError("invalid_input", f"{factor['name']}: {answer['failure']['message']}")
            return await asyncio.to_thread(pd.read_parquet, answer["path"])
        cross = self.models.factors.directory(factor["id"]) / "output" / "cross"
        if not (cross / "raw.npy").is_file():
            raise ContractError("invalid_input", t("{name} #{number}: this run's values were not kept; run the factor again before publishing.", name=factor['name'], number=factor['number']))
        return await asyncio.to_thread(self.code_factor_values, cross)

    @staticmethod
    def code_factor_values(cross):
        meta = json.loads((cross / "meta.json").read_text(encoding="utf-8"))
        raw = np.load(cross / "raw.npy")
        t, i = np.nonzero(np.isfinite(raw))
        return pd.DataFrame({"label": np.asarray(meta["labels"], dtype="int64")[t], "column": np.asarray(meta["columns"], dtype=object)[i], "value": raw[t, i].astype(float)})

    def rows_of(self, values, field, info, zone, found, recorded, mode="history"):
        instruments = {item["column"]: item["instrumentId"] for item in info["instruments"]}
        frame = pd.DataFrame({"instrument": values["column"], "field": field, "datetime": values["label"].astype("int64"), "value": values["value"].astype(float)})
        frame = frame[frame["instrument"].isin(instruments)]
        availability = edge_times(sorted(set(frame["datetime"])), info["timeframe"], zone, found, "close")
        return long_rows(frame, instruments, availability, recorded, mode)

    async def commit(self, identity, source, ready, publish):
        """The factors published with the model, then the model, in one transaction; the model's version occupies the factor versions."""
        info = ready["info"]
        zone, found = await research_calendar.date_boundaries(self.ctx, info["calendarId"], info["timeframe"], info["sessionScope"], info["since"], info["until"])
        bars = await asyncio.to_thread(pd.read_parquet, ready["barsPath"], columns=["label", "column", "close", "open"])
        prices = await asyncio.to_thread(library_checks.close_prices, bars, {item["column"]: item["instrumentId"] for item in info["instruments"]})
        recorded = int(time.time() * 1000)
        auto = publish["autoUpdate"]
        specs, folders, factor_versions, reads, numbering = [], [], {}, [], {}
        base = {"objectId": source["objectId"], "runId": source["id"], "workspaceId": self.models.store.workspace()}

        def next_number(name):
            found = self.store.find_item(name)
            return (self.store.item_versions(found[0]["id"])[0]["version"] if found else 0) + 1
        publisher = getattr(self, "publisher", None)
        total = len(publish["plan"]["alsoPublished"])
        for index, item in enumerate(publish["plan"]["alsoPublished"]):
            factor = self.models.factors.read(item["runId"])
            updating = auto and factor.get("mode") == "formula"
            if publisher is not None:
                # The factor's values and the three cuts of the future-data check on the model's own data, then its version as the store takes it: it joins the model's transaction.
                self._put(identity, stage="factors", publish={**self.records.find(RUN, identity)["publish"], "factor": {"name": item["name"], "n": index + 1, "of": total}})
                _ready, values, answer = await publisher.factor_values(identity, factor, self.models.directory(identity) / "factors" / factor["id"], checks=3, ready=ready,
                                                                      held_out=held_out_ms(source["settings"]["data"] or {}),
                                                                      report=lambda stage, done, of, name=item["name"], n=index + 1: self._put(identity, stage="factors", publish={
                                                                          **self.records.find(RUN, identity)["publish"], "factor": {"name": name, "n": n, "of": total, "cut": done, "cuts": of}}))
                spec, version_id, folder = await publisher.factor_spec(factor, ready, values, answer, name=item["name"], auto=bool(updating))
                folders.append(folder)
                spec["meta"].update(producer="pipeline", pipeline={"kind": "factor", "formula": factor.get("formula"), "runId": factor["id"], "publishedWith": source["id"]})
                spec.update(state="updating" if updating else "fixed", auto=bool(updating))
                specs.append(spec)
                factor_versions[factor["id"]] = version_id
                numbering[version_id] = {"versionId": version_id, "name": item["name"], "version": next_number(item["name"])}
                reads.append(version_id)
                continue
            values = await self.factor_values(factor, ready, self.models.directory(identity) / "factors" / factor["id"])
            rows = await asyncio.to_thread(self.rows_of, values, "value", info, zone, found, recorded)
            version_id = "ver-" + uuid4().hex[:12]
            frame = await asyncio.to_thread(pd.DataFrame, rows, columns=["instrument", "field", "datetime", "value", "availableAt", "recordedAt", "mode"])
            run = (factor.get("settings") or {}).get("run") or {}
            holding, delay = int(run.get("holding", 1)), int(run.get("delay", 1))
            # published with the figures it is followed on afterwards: one function, the run's horizon
            published_figures = await asyncio.to_thread(library_after.published, frame[frame["field"] == "value"][["datetime", "instrument", "value"]], prices,
                                                        settings=run, holding=holding, delay=delay, annualization=info["annualization"]) if rows else {}
            meta = {"fields": ["value"], "timeframe": info["timeframe"], "info": info, "data": source["settings"]["data"], "params": {}, "producer": "pipeline",
                    "publication": {"name": item["name"], "kind": "factor", "field": "value"}, "edge": "close", "horizon": {"holding": holding, "delay": delay},
                    "measure": measure_text(holding, info["timeframe"]), "universe": universe_text(source["settings"]["data"]), "settingsText": settings_text(source["settings"]["data"], run, info["timeframe"]),
                    "publishedThrough": max((row[2] for row in rows), default=None), "instrumentCount": len({row[0] for row in rows}),
                    "origin": {"objectId": factor["objectId"], "runId": factor["id"], "objectName": item["name"], "objectKind": "factor", "runNumber": factor.get("number")},
                    "pipeline": {"kind": "factor", "formula": factor.get("formula"), "runId": factor["id"], "publishedWith": source["id"]}, "environment": None,
                    "settings": run}
            specs.append({"name": item["name"], "kind": "factor", "meta": meta, "rows": rows, "objectId": factor["objectId"], "runId": factor["id"], "workspaceId": base["workspaceId"],
                          "state": "updating" if updating else "fixed", "auto": bool(updating), "cursor": max((row[2] for row in rows), default=None),
                          "checks": published_figures, "versionId": version_id})
            factor_versions[factor["id"]] = version_id
            numbering[version_id] = {"versionId": version_id, "name": item["name"], "version": next_number(item["name"])}
            reads.append(version_id)
        # The model: its test predictions, the window cache and the code as they were in the run.
        output = self.models.directory(source["id"]) / "output"
        rows, label_rows, calendar = await asyncio.to_thread(self.prediction_rows, output, source, info, zone, found, recorded)
        version_id = "ver-" + uuid4().hex[:12]
        folder = self.library.version_directory(version_id)
        folders.append(folder)
        await asyncio.to_thread(self.freeze, source, folder)
        param_inputs = []
        for row in source["settings"]["params"].get("inputs") or []:
            pin = next((pin for pin in source.get("pins") or [] if pin.get("runId") == row.get("ref")), None)
            if row.get("source") == "run" and pin is not None:
                published = factor_versions.get(row["ref"]) or ((self.models.factors.read(row["ref"]).get("version") or {}).get("versionId"))
                param_inputs.append({**row, "source": "library", "ref": published} if published else row)
            else:
                param_inputs.append(row)
        params = {**source["settings"]["params"], **(source.get("overrides") or {}), "inputs": param_inputs}
        frame = await asyncio.to_thread(pd.DataFrame, rows, columns=["instrument", "field", "datetime", "value", "availableAt", "recordedAt", "mode"])
        labels_frame = pd.DataFrame(label_rows, columns=["instrument", "field", "datetime", "value", "availableAt", "recordedAt", "mode"])[["datetime", "instrument", "value"]] if label_rows else None
        rows = rows + label_rows
        pinned = [{"versionId": pin["versionId"], "name": pin["name"], "version": pin["version"]} for pin in publish["plan"]["pinned"]]
        reads += [pin["versionId"] for pin in pinned]
        inputs = [numbering[version] for version in reads if version in numbering] + pinned
        numbers = source.get("numbers") or {}
        label_days = int(source.get("horizon") or params.get("label_days") or (source["settings"]["params"].get("label_days") or 5))
        ahead = int((source.get("reach") or {}).get("ahead") or label_days + 1)
        last_valid = max([entry["valid"]["to"] for entry in source.get("windows") or [] if entry.get("valid")] or [calendar[-1]])
        position = int(np.searchsorted(calendar, last_valid))
        trained_through = int(calendar[min(position + ahead, len(calendar) - 1)])  # the last bar the model has seen: its last validation label reads this far
        open_prices = bars.pivot(index="label", columns="column", values="open").rename(columns={item["column"]: item["instrumentId"] for item in info["instruments"]})
        published = {"metric": "rank_ic", "value": numbers.get("ric"), "t": numbers.get("t"), "ls": numbers.get("lsNet"),
                     "holding": label_days}
        meta = {"fields": ["prediction", "label"] if label_rows else ["prediction"], "timeframe": info["timeframe"], "info": info, "data": source["settings"]["data"], "params": params, "producer": "pipeline",
                "publication": {"name": publish["name"], "kind": "model", "field": "prediction"}, "report": {"metrics": source.get("numbers")}, "inputs": inputs,
                "edge": "close", "horizon": {"holding": label_days, "delay": 1, "ahead": ahead}, "measure": measure_text(label_days, info["timeframe"]),
                "universe": universe_text(source["settings"]["data"]), "settingsText": f"{params.get('algorithm') or 'Model'}, retrained every {params.get('every_months')} months",
                "publishedThrough": max((row[2] for row in rows), default=None), "instrumentCount": len({row[0] for row in rows}),
                "origin": {"objectId": source["objectId"], "runId": source["id"], "objectName": source["name"], "objectKind": "model", "runNumber": source.get("number")},
                "pipeline": {"kind": "model", "runId": source["id"], "number": source["number"], "mode": source["mode"], "reads": reads, "algorithm": params.get("algorithm"),
                             **({"sources": [{"runId": part, "number": self.models.read(part)["number"], "algorithm": self.models.read(part)["settings"]["params"].get("algorithm"),
                                              "params": self.models.read(part)["settings"]["params"]} for part in source["combinedFrom"]]} if source["runKind"] == "combine" else {}),
                             "every": params.get("every_months"), "future": publish.get("future"), "dataTo": (source.get("data") or {}).get("dataTo"),
                             "trainedThrough": trained_through, "heldOutFrom": (source["settings"]["data"] or {}).get("heldOutFrom"),
                             "code": {"entry": (source.get("codeSnapshot") or {}).get("entry"), "digest": (source.get("codeSnapshot") or {}).get("digest")} if source.get("codeSnapshot") else None},
                "model": None, "environment": None, "settings": {"costBps": source["settings"]["run"]["cost"], "borrowPct": M.DEFAULT_BORROW_PCT}}
        specs.append({"name": publish["name"], "kind": "model", "meta": meta, "rows": rows, **base, "state": "updating", "auto": bool(auto),
                      "cursor": max((row[2] for row in rows), default=None), "holds": [{"versionId": version} for version in reads],
                      "checks": {**published, "backtest": await asyncio.to_thread(library_after.published, frame[frame["field"] == "prediction"][["datetime", "instrument", "value"]], open_prices,
                                                                                  settings=meta["settings"], holding=label_days, delay=1, annualization=info["annualization"], realised=labels_frame, ahead=ahead)} if rows else {},
                      "versionId": version_id})
        created = await asyncio.to_thread(self.store.commit_versions, specs)
        model_version = created[-1]
        for factor_id, factor_version in factor_versions.items():
            number = next(item["version"] for item in created if item["versionId"] == factor_version)
            factor = self.models.factors.read(factor_id)
            self.models.factors.save(factor, version={"versionId": factor_version, "version": number, "name": next(spec["name"] for spec in specs if spec["versionId"] == factor_version)})
        self.models.save(self.models.read(source["id"]), version={"versionId": model_version["versionId"], "version": model_version["version"], "name": publish["name"]})
        return created, folders

    def prediction_rows(self, output, source, info, zone, found, recorded):
        pred = np.load(output / "pred.npy")
        meta_pred = json.loads((output / "pred.json").read_text(encoding="utf-8"))
        calendar = np.asarray(meta_pred["labels"], dtype="int64")
        inside = np.zeros(len(calendar), dtype=bool)
        for entry in source.get("windows") or []:
            if entry.get("test"):
                inside |= (calendar >= entry["test"]["from"]) & (calendar <= entry["test"]["to"])
        if not inside.any():
            inside[:] = True
        pred = np.where(inside[:, None], pred, np.nan)  # the history is what each window predicted on its own test days (out of sample)
        t, i = np.nonzero(np.isfinite(pred))
        columns = np.asarray(meta_pred["columns"], dtype=object)
        values = pd.DataFrame({"label": calendar[t], "column": columns[i], "value": pred[t, i].astype(float)})
        rows = self.rows_of(values, "prediction", info, zone, found, recorded)
        label_rows = []
        label_path = output / "label.npy"
        if label_path.is_file():
            realised = np.load(label_path)
            lt, li = np.nonzero(np.isfinite(pred) & np.isfinite(realised))
            label_rows = self.rows_of(pd.DataFrame({"label": calendar[lt], "column": columns[li], "value": realised[lt, li].astype(float)}), "label", info, zone, found, recorded)
        return rows, label_rows, calendar

    def freeze(self, source, folder):
        """The run's window cache and code beside the version: what its updates train from."""
        folder.mkdir(parents=True)
        run = self.models.directory(source["id"])
        if source["runKind"] == "combine":
            # Each source keeps its own window cache: the version is updated by each source's own model, then their predictions are ranked and averaged.
            for number, part in enumerate(source["combinedFrom"]):
                shutil.copytree(filesystem_path(self.models.directory(part) / "windows"), filesystem_path(folder / "parts" / str(number) / "windows"))
            (folder / "windows").mkdir()
        else:
            shutil.copytree(filesystem_path(run / "windows"), filesystem_path(folder / "windows"))
        if source["mode"] == "standard":
            (folder / "pipeline").mkdir()
            shutil.copyfile(run / "source" / P.STANDARD.name, folder / "pipeline" / P.STANDARD.name)
        if source.get("common"):
            shutil.copytree(source["common"]["snapshotPath"], folder / "common")
        snapshot = source.get("codeSnapshot")
        if snapshot and Path(snapshot["source"]).is_dir():
            shutil.copytree(filesystem_path(Path(snapshot["source"])), filesystem_path(folder / "source"))
        shutil.copyfile(run / "output" / "work.json", folder / "work.json")

    # ------------------------------------------------------------------------------------------------------------ what the producer asks for
    async def factor_update(self, pipeline, ready, directory, after):
        """A pipeline factor's values later than `after` (label, column, value); a factor published from code has no formula and is not updated."""
        if not pipeline.get("formula"):
            raise ContractError("invalid_input", t("This factor was published from code and does not update."))
        job = {"data": {key: ready[key] for key in ("key", "barsPath", "info", "extrasPath")}, "formula": pipeline["formula"], "after": after or 0, "output": str(directory)}
        answer = await self.models.engine.call("formula_values", job)
        if not answer.get("ok"):
            raise ContractError("code_failed", answer["failure"]["message"], {"failure": answer["failure"]})
        return await asyncio.to_thread(pd.read_parquet, answer["path"])

    async def model_update(self, version, meta, ready, directory, known_by=None):
        """The update job: the window cache of the version, the inputs pinned to the factor versions it reads (their values as known by `known_by`)."""
        folder = self.library.version_directory(version["id"])
        params = meta["params"]
        inputs, _ = await P.prepare_inputs(self.models, params.get("inputs") or [], ready["info"], Path(directory) / "inputs", known_by)
        cached = sorted(int(path.name) for path in (folder / "windows").iterdir() if path.is_dir() and path.name.isdigit())
        job = {"mode": "update", "output": str(Path(directory) / "output"), "cache": str(folder / "windows"), "data": {key: ready[key] for key in ("key", "barsPath", "info", "extrasPath")},
               "inputs": inputs, "settings": P.settings_of(params), "params": params, "threads": max(1, (os.cpu_count() or 2) // 2), "run": {}, "kind": "update",
               "keepDone": True, "cachedWindows": cached, "after": version["cursor"] or 0}
        need = await asyncio.to_thread(self.unsettled, version, meta)
        labelled = "label" in (meta.get("fields") or [])
        if labelled:
            # From the oldest prediction still without its label, or else this update's own first period: the periods of a catch-up whose horizon has
            # already passed settle with it, not one update later (a model published back to the start of its held-out period showed all 112 waiting).
            job["labelFrom"] = int(need["datetime"].min()) if len(need) else int((version["cursor"] or 0) + 1)
        if (folder / "parts").is_dir():
            # A combined version: each source is updated by its own model from its own window cache and settings, then their predictions are ranked and averaged.
            job["sources"] = []
            for part, entry in zip(sorted((folder / "parts").iterdir(), key=lambda path: int(path.name)), meta["pipeline"]["sources"], strict=True):
                part_inputs, _ = await P.prepare_inputs(self.models, entry["params"].get("inputs") or [], ready["info"], Path(directory) / f"inputs-{part.name}", known_by)
                job["sources"].append({"cache": str(part / "windows"), "params": entry["params"], "settings": P.settings_of(entry["params"]), "inputs": part_inputs,
                                       "cachedWindows": sorted(int(path.name) for path in (part / "windows").iterdir() if path.is_dir() and path.name.isdigit())})
        if meta["pipeline"].get("mode") != "code":
            job["pipelinePath"] = str(folder / "pipeline" / P.STANDARD.name)
        if (folder / "source").is_dir():
            job["code"] = {"source": str(folder / "source"), "entry": (meta["pipeline"].get("code") or {}).get("entry"), "own": meta["pipeline"].get("mode") == "code",
                           "digest": (meta["pipeline"].get("code") or {}).get("digest"), "roots": [str(folder / "source")]}
            if (folder / "common").is_dir():
                job["code"]["common"] = str(folder / "common")
                job["code"]["roots"].append(str(folder / "common"))
        answer = await self.models.engine.run_model(job, Path(directory) / "engine", context={"objectId": meta["origin"]["objectId"], "versionId": version["id"]})
        if not answer.get("ok"):
            raise ContractError("code_failed", t("The update failed: {message}", message=answer['failure']['message']), {"failure": answer["failure"]})
        if answer.get("trainedWindows"):
            self.store.merge_meta(version["id"], lastTrained={"windows": answer["trainedWindows"], "at": int(time.time() * 1000)})
        values = await asyncio.to_thread(pd.read_parquet, Path(job["output"]) / "values.parquet")
        # The labels of earlier periods whose horizon has passed by the bars of this update: written beside the predictions, so the record settles.
        settled = Path(job["output"]) / "labels.parquet"
        if labelled and settled.is_file():
            ids = {item["column"]: item["instrumentId"] for item in ready["info"]["instruments"]}
            labels = await asyncio.to_thread(pd.read_parquet, settled)
            values.attrs["labels"] = await asyncio.to_thread(self.settled_labels, labels, values, ids, need)
        return values

    @staticmethod
    def settled_labels(labels, values, ids, need):
        labels = pd.DataFrame({"datetime": labels["label"].astype("int64"), "instrument": labels["column"].map(ids), "value": labels["value"].astype(float)})
        fresh = pd.DataFrame({"datetime": values["label"].astype("int64"), "instrument": values["column"].map(ids)})
        wanted = pd.concat([need, fresh], ignore_index=True).drop_duplicates()
        return labels.merge(wanted, on=["datetime", "instrument"], how="inner").assign(field="label")[["datetime", "instrument", "field", "value"]]

    def unsettled(self, version, meta):
        """The predictions this version made after publication that have no label yet: (datetime, instrument) pairs."""
        if "label" not in (meta.get("fields") or []):
            return pd.DataFrame({"datetime": pd.Series(dtype="int64"), "instrument": pd.Series(dtype="object")})
        start = (meta.get("publishedThrough") + 1) if meta.get("publishedThrough") is not None else None
        made = self.library.store.frame(version["id"], field="prediction", start=start, columns=["datetime", "instrument"]).drop_duplicates()
        have = self.library.store.frame(version["id"], field="label", start=start, columns=["datetime", "instrument"]).drop_duplicates().assign(settled=True)
        merged = made.merge(have, on=["datetime", "instrument"], how="left")
        return merged[merged["settled"].isna()][["datetime", "instrument"]].reset_index(drop=True)


def held_out_ms(data):
    return day_ms(data["heldOutFrom"], False) if data.get("heldOutFrom") else None
