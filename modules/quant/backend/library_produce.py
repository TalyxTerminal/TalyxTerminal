"""What makes one update of a published version: its frozen formula or code run on the data up to the newest due bar.

Two kinds of version. A factor published from a factor run is computed by the factor engine (the same code that tested it, with the same
preprocessing); a model or an allocation published from a research run is run by the research worker on its frozen code and environment.
Either way the answer is `Made`: the new values, which bars the data had, and the closes to judge the values by.
"""
from __future__ import annotations

import asyncio
import json
import shutil
import time
from dataclasses import dataclass, field

import pandas as pd
from talyx_sdk import ContractError
from talyx_sdk.i18n import t

from . import research_data, research_process
from .library_checks import close_prices
from .project_paths import filesystem_path


def day_of(milliseconds):
    return time.strftime("%Y-%m-%d", time.gmtime(milliseconds / 1000))


def day_text(milliseconds):
    """A bar as a message to the person writes it: "Nov 6" (a data day, UTC), "Nov 6, 14:00" for a bar inside the day."""
    moment = time.gmtime(milliseconds / 1000)
    text = f"{time.strftime('%b', moment)} {moment.tm_mday}"
    return text if moment.tm_hour == 0 and moment.tm_min == 0 else f"{text}, {time.strftime('%H:%M', moment)}"


class UpdateFailure(Exception):
    """A period that could not be made: the item shows Failed with this message; `details` may name the file and line, the traceback and output."""

    def __init__(self, message, details=None):
        super().__init__(message)
        self.message, self.details = message, details or {}


@dataclass
class Made:
    rows: pd.DataFrame  # datetime, instrument (instrument id), field, value
    present: set = field(default_factory=set)  # bar labels the data had
    prices: pd.DataFrame | None = None  # closes, bar labels by instrument id
    settled: pd.DataFrame | None = None  # labels of earlier periods whose horizon has now passed: datetime, instrument, field, value
    total: int = 0  # names the version covers now
    instruments: list = field(default_factory=list)  # the data's instruments (info entries): a name the published run's data lacked is named from here
    peak: int | None = None  # the most memory the update held, in bytes, when the producer could tell (the next update asks the run queue for 1.2 times it)
    risk_free: object = 0.0  # an allocation's risk-free rate an observation by bar label (library_after.rate_per_bar): its Sharpe since publication is over it
    solver: dict = field(default_factory=dict)  # an allocation's periods that were not solved as set: bar label -> how the fit ended ("relaxed: ...", "equal: ...")


def failure_of(failure):
    """A failed run's failure as the library logs it: where in the author's code, the traceback and the output before it."""
    location = {"file": failure.get("file"), "line": failure.get("line")} if failure.get("file") else None
    return {"location": location, "traceback": failure.get("traceback"), "output": failure.get("output")}


class FactorProducer:
    """A factor version: the engine computes the frozen formula or code on freshly frozen data, from the bar after the cursor on."""

    def __init__(self, ctx, factors, library):
        self.ctx, self.factors, self.library = ctx, factors, library

    async def make(self, version, *, through, after, now, attempt, due=None):
        meta = version["meta"]
        data = {**meta["data"], "until": day_of(through)}
        # A freeze made before the bar was due does not hold it: the store asks Market for the new bar only (versions on the same data share that one freeze).
        # After a try that found the bar missing, the next one asks again for the bar the source owed.
        refresh = now if attempt or due is None else due
        try:
            ready = await self.factors.data.prepare(data, refresh_after=refresh)
        except ContractError as error:
            raise UpdateFailure("The data could not be fetched: " + error.message, {"code": error.code}) from error
        base = self.library.version_directory(version["id"])
        directory = base / "updates" / str(now)
        output = directory / "output"
        output.mkdir(parents=True, exist_ok=True)
        try:
            job = {"output": str(output), "kind": "values", "settings": meta["settings"], "from": None if after is None else after + 1, "cutoffs": [],
                   "data": {"key": ready["key"], "barsPath": ready["barsPath"], "info": ready["info"], "extrasPath": ready["extrasPath"], "libraryPath": None}}
            if meta.get("formula") is not None:
                job["formula"] = meta["formula"]
            else:
                job["code"] = {"source": str(base / "source"), "entry": meta["code"]["entry"], "common": str(base / "common") if (base / "common").is_dir() else None,
                               "params": meta.get("params") or {}}
            answer = await self.factors.engine.run_open(job)
            if not answer.get("ok"):
                failure = answer.get("failure") or {"message": t("The factor engine gave no answer.")}
                raise UpdateFailure("The code failed on the new data: " + failure["message"], {"code": "code_failed", **failure_of(failure)})
            values = await asyncio.to_thread(pd.read_parquet, output / "values.parquet")
        finally:
            await asyncio.to_thread(shutil.rmtree, filesystem_path(directory), ignore_errors=True)
        return await asyncio.to_thread(self.project_values, ready, values, meta, answer)

    @staticmethod
    def project_values(ready, values, meta, answer):
        ids = {item["column"]: item["instrumentId"] for item in ready["info"]["instruments"]}
        bars = pd.read_parquet(ready["barsPath"], columns=["label", "column", "close"])
        rows = pd.DataFrame({"datetime": values["datetime"].astype("int64"), "instrument": values["instrument"].map(ids), "field": meta["fields"][0],
                             "value": values["value"].astype(float)})
        return Made(rows=rows[rows["instrument"].notna()], present={int(label) for label in bars["label"].unique()}, prices=close_prices(bars, ids),
                    total=len(ids), peak=answer.get("peakMemory"), instruments=ready["info"]["instruments"])


class RunProducer:
    """A model or allocation version: the research worker runs the frozen code once, on the data up to the newest due bar."""

    def __init__(self, ctx, library):
        self.ctx, self.library = ctx, library

    async def make(self, version, *, through, after, now, attempt, due=None):
        meta = version["meta"]
        info = meta["info"]
        base = self.library.version_directory(version["id"])
        directory = base / "updates" / str(now)
        directory.mkdir(parents=True, exist_ok=True)
        try:
            data = {**meta["data"], "until": day_of(through), "since": day_of(info["since"])}
            prepared = await research_data.prepare(self.ctx, data, "library-update:" + version["id"] + ":" + str(now), directory)
            bars = await asyncio.to_thread(pd.read_parquet, prepared["research"]["barsPath"])
            instruments = {item["column"]: item["instrumentId"] for item in prepared["research"]["info"]["instruments"]}
            if (meta.get("publication") or {}).get("kind") == "allocation" and after is not None and not rebalances(meta, through, previous=after):
                # Not a rebalance day: the weights held stay, no research process starts.
                held = await asyncio.to_thread(self.library.store.frame, version["id"], datetime=after, columns=["instrument", "field", "value"])
                return await asyncio.to_thread(self.carried_values, held, bars, instruments, after, through)
            made = await self.run(version, meta, directory, prepared, through, now)
            projected = await asyncio.to_thread(self.project_values, made, bars, instruments, after)
            projected.instruments = prepared["research"]["info"]["instruments"]
            return projected
        finally:
            await asyncio.to_thread(shutil.rmtree, filesystem_path(directory), ignore_errors=True)

    @staticmethod
    def project_values(made, bars, instruments, after):
        frames = []
        for entry in made:
            table = entry[entry["datetime"] > (after or -1)]
            frames.append(pd.DataFrame({"datetime": table["datetime"].astype("int64"), "instrument": table["instrument"].map(instruments),
                                        "field": table["field"], "value": table["value"].astype(float)}))
        rows = pd.concat(frames, ignore_index=True) if frames else pd.DataFrame(columns=["datetime", "instrument", "field", "value"])
        return Made(rows=rows[rows["instrument"].notna()], present={int(label) for label in bars["label"].unique()}, prices=close_prices(bars, instruments),
                    total=len(instruments))

    @staticmethod
    def carried_values(held, bars, instruments, after, through):
        labels = sorted(int(label) for label in bars["label"].unique() if after < int(label) <= through)
        carried = pd.concat([held.assign(datetime=label) for label in labels], ignore_index=True) if labels and not held.empty else held.iloc[0:0].assign(datetime=[])
        return Made(rows=carried[["datetime", "instrument", "field", "value"]], present={int(label) for label in bars["label"].unique()},
                    prices=close_prices(bars, instruments), total=len(instruments))

    async def run(self, version, meta, directory, prepared, through, now):
        environment = await self.library.environment_state(meta["environment"])
        base = self.library.version_directory(version["id"])
        shutil.copytree(filesystem_path(base / "source"), filesystem_path(directory / "source"))
        if (base / "common").is_dir():
            shutil.copytree(filesystem_path(base / "common"), filesystem_path(directory / "common"))
        (directory / "output").mkdir()
        support = research_process.write_support(directory)
        if "factors" in meta:
            from .training_factors import prepare
            prepared["research"]["factors"] = await prepare(self.library, [{"name": item["parameter"], "type": "library"} for item in meta["factors"]],
                                                            meta["params"], prepared["research"], directory, frozen=meta["factors"], known_by=now)
        invocation = {"runId": "update", "title": meta["origin"]["objectName"], "workbench": meta["origin"]["objectKind"], "entry": meta["code"]["entry"],
                      "source": str(directory / "source"), "output": str(directory / "output"), "common": str(directory / "common") if (directory / "common").is_dir() else None,
                      "support": str(support), "mode": "values", "cutoffs": [through], "params": meta["params"], "research": prepared["research"]}
        research_process.write_invocation(directory, invocation)
        lease = await research_process.start(self.ctx, key="update-" + version["id"] + "-" + directory.name, directory=directory, invocation=invocation,
                                             state=environment, context={"objectId": meta["origin"]["objectId"], "versionId": version["id"]})
        try:
            code, _calls = await research_process.wait(self.ctx, lease)
        except ContractError as error:
            if error.code != "target_gone":
                raise
            raise ContractError("interrupted", t("The update process was closed with its owning scope.")) from error
        finally:
            try:
                await self.ctx.processes.cancel(lease)
            except ContractError as error:
                if error.code != "target_gone":
                    raise
            finally:
                await self.ctx.processes.close(lease)
        output = directory / "output"
        if (output / "error.json").is_file():
            failure = json.loads((output / "error.json").read_text(encoding="utf-8"))
            inner = next((item for item in reversed(failure.get("frames") or []) if item.get("root") in {"source", "library"}), None)
            raise UpdateFailure("The code failed on the new data: " + failure["message"],
                                {"code": "code_failed", "location": {"file": inner["file"], "line": inner["line"]} if inner else None,
                                 "traceback": failure.get("traceback"), "output": (output / "stdout.log").read_text(encoding="utf-8", errors="replace")[-4000:]
                                 if (output / "stdout.log").is_file() else None})
        if code != 0 or not (output / "done.json").is_file():
            raise UpdateFailure(f"The update run ended with exit code {code} before it finished.", {"code": "process_failed"})
        report = json.loads((output / "values-0.json").read_text(encoding="utf-8"))
        items = await asyncio.to_thread(lambda: [pd.read_parquet(output / entry["path"]) for entry in report["publications"] if entry["name"] == meta["publication"]["name"] and "path" in entry])
        if not items:
            raise UpdateFailure(f"The code made no {meta['publication']['name']} when run on the data up to {day_text(through)}.", {"code": "no_publication"})
        return items


class PipelineProducer:
    """A factor or a model published by the training pipeline (`model_library`): its values for the newest due bar, never written here (the loop does
    that, with the same dates, lateness and figures as every other version). A model waits for the factor versions it reads."""

    def __init__(self, model_library):
        self.models = model_library
        self.library = model_library.library

    async def make(self, version, *, through, after, now, attempt, due=None):
        meta = version["meta"]
        pipeline = meta["pipeline"]
        data = {**meta["data"], "until": day_of(through)}
        data.pop("heldOutFrom", None)
        refresh = now if attempt or due is None else due
        if pipeline["kind"] == "model":
            behind = [item for item in (self.library.store.version(read) for read in pipeline.get("reads") or []) if item["cursor"] is None or item["cursor"] < through]
            if behind:
                names = ", ".join(self.library.store.summary_name(item["id"]) for item in behind)
                raise UpdateFailure(f"{names} has no value for {day_text(through)} yet", {"code": "up"})
        try:
            ready = await self.models.models.data.prepare(data, refresh_after=refresh)
        except ContractError as error:
            raise UpdateFailure("The data could not be fetched: " + error.message, {"code": error.code}) from error
        directory = self.library.version_directory(version["id"]) / "updates" / str(now)
        directory.mkdir(parents=True, exist_ok=True)
        settled = None
        try:
            try:
                if pipeline["kind"] == "factor":
                    values = await self.models.factor_update(pipeline, ready, directory, after or 0)
                    field = "value"
                else:
                    values = await self.models.model_update(version, meta, ready, directory, known_by=now)
                    settled = values.attrs.get("labels")
                    field = "prediction"
            except ContractError as error:
                raise UpdateFailure(error.message, {"code": error.code, **({"traceback": (error.details or {}).get("failure", {}).get("traceback")} if error.details else {})}) from error
        finally:
            await asyncio.to_thread(shutil.rmtree, filesystem_path(directory), ignore_errors=True)
        return await asyncio.to_thread(self.project_values, ready, values, field, after, through, settled)

    @staticmethod
    def project_values(ready, values, field, after, through, settled):
        info = ready["info"]
        ids = {item["column"]: item["instrumentId"] for item in info["instruments"]}
        values = values[(values["label"] > (after if after is not None else -1)) & (values["label"] <= through)]
        rows = pd.DataFrame({"datetime": values["label"].astype("int64"), "instrument": values["column"].map(ids), "field": field, "value": values["value"].astype(float)})
        price = "open" if field == "prediction" else "close"
        bars = pd.read_parquet(ready["barsPath"], columns=["label", "column", price]).rename(columns={price: "close"})
        return Made(rows=rows[rows["instrument"].notna()], present={int(label) for label in bars["label"].unique()}, prices=close_prices(bars, ids), total=len(ids),
                    settled=settled, instruments=info["instruments"])


def producer_for(version, factor_producer, run_producer, pipeline_producer=None, allocation_producer=None):
    if version["meta"].get("producer") == "allocation" and allocation_producer is not None:
        return allocation_producer
    if version["meta"].get("pipeline") and pipeline_producer is not None:
        return pipeline_producer
    return factor_producer if version["meta"].get("producer") == "factor" else run_producer


def rebalances(meta, label, *, previous):
    """Whether an allocation runs on the bar `label`: only on its own rebalance days (a monthly one once a month), `previous` being the label of the last period it ran;
    the days between carry the last weights and start no research process."""
    every = (meta.get("rebalance") or {}).get("every")
    if every is None:
        return True
    if previous is None:
        return True
    then, now = time.gmtime(previous / 1000), time.gmtime(label / 1000)
    if every == "month":
        return (now.tm_year, now.tm_mon) != (then.tm_year, then.tm_mon)
    if every == "week":
        return time.strftime("%G-%V", now) != time.strftime("%G-%V", then)
    if every == "year":
        return now.tm_year != then.tm_year
    return True
