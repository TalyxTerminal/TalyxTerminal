"""A small fake world for the library's tests: a daily calendar, a source whose bars can come late or never, prices, and producers that make
a factor's or a model's values the way the real ones answer (`Made`). Nothing here starts a process or calls Market."""
from __future__ import annotations

import asyncio
import hashlib
from types import SimpleNamespace

import numpy as np
import pandas as pd

from modules.quant.backend.work_records import WorkRecords
from tests.unit.quant_runtime_fakes import adapt

DAY = 86_400_000
HOUR = 3_600_000
NAMES = [f"i:{letter}" for letter in "ABCDEFGH"]
INFO = {"timeframe": "1d", "calendarId": "XNYS", "sessionScope": "regular", "timezone": "UTC", "annualization": 252, "since": 0, "until": 0,
        "instruments": [{"column": name[2:], "instrumentId": name, "displaySymbol": name[2:], "source": {"sourceId": "fake", "name": "Fake"}} for name in NAMES]}


def close_of(day):
    return day * DAY + 21 * HOUR


class Events:
    def __init__(self):
        self.published = []

    def publish(self, name, value):
        self.published.append((name, value))


class Toasts:
    def __init__(self):
        self.sent = []

    async def toast(self, value):
        self.sent.append(value)

    async def channels(self, value):
        return []

    async def send(self, value):
        self.sent.append(value)


class Context:
    def __init__(self, root=None):
        self.events = Events()
        self.toasts = Toasts()
        self.platform = SimpleNamespace(notifications=self.toasts, workspace_activity=SimpleNamespace(current=lambda: "ws"))

        adapt(self, root)

    def track(self, release):
        return release


class World:
    """The source and the code: which bars exist and when they come, and when a version's code fails."""

    def __init__(self, seed=1, days=400):
        rng = np.random.default_rng(seed)
        self.prices = pd.DataFrame(100 * np.exp(np.cumsum(rng.normal(0, .02, (days, len(NAMES))), axis=0)), index=[day * DAY for day in range(days)], columns=NAMES)
        self.late = {}  # day -> hours after its close the source gives the bar
        self.gaps = set()  # days the source never gives
        self.failing = {}  # version id -> fails until this moment
        self.calls = []
        self.present = {}  # version id -> the bar labels its newest update saw
        self.gate = None  # an asyncio.Event an update waits on while set, so a test can cancel it midway
        self.constant = set()  # days the code gives every name the same value
        self.joined = []  # instruments the source has besides NAMES (as the data's info lists them): a name that came into the data after publication

    def has(self, day, now):
        return day not in self.gaps and close_of(day) + self.late.get(day, 0) * HOUR <= now

    async def calendar(self, version, start, end):
        first, last = max(0, start // DAY), min(len(self.prices) - 1, end // DAY)
        return [(day * DAY, day * DAY + 14 * HOUR, close_of(day)) for day in range(first, last + 1)]


def factor_value(version_id, day, name):
    digest = hashlib.sha256(f"{version_id}:{day}:{name}".encode()).digest()
    return int.from_bytes(digest[:4], "little") / 2 ** 32 - .5


class Producer:
    """Makes a version's values for every bar the source has, from the bar after the cursor; a model's values read its input's same-day values."""

    def __init__(self, world, store, clock):
        self.world, self.store, self.clock = world, store, clock

    async def make(self, version, *, through, after, now, attempt, **_):
        from modules.quant.backend.library_produce import Made, UpdateFailure
        self.world.calls.append((version["id"], through, now))
        if self.world.gate is not None:
            await self.world.gate.wait()
        if self.world.failing.get(version["id"], -1) > now:
            raise UpdateFailure("KeyError: 'epsTrend'", {"code": "code_failed", "location": {"file": "revision.py", "line": 8},
                                                         "traceback": "Traceback ...\nKeyError: 'epsTrend'", "output": "reading estimates\n"})
        days = [day for day in range(0, through // DAY + 1) if self.world.has(day, now)]
        self.world.present[version["id"]] = [day * DAY for day in days]
        inputs = version["meta"].get("inputs") or []
        rows = []
        for day in days:
            if after is not None and day * DAY <= after:
                continue
            if inputs:
                read = self.store.frame(inputs[0]["versionId"], datetime=day * DAY, columns=["instrument", "value"])
                if read.empty:
                    continue
                rows += [(day * DAY, row.instrument, version["meta"]["fields"][0], 2 * row.value) for row in read.itertuples()]
            else:
                names = NAMES + [item["instrumentId"] for item in self.world.joined]
                rows += [(day * DAY, name, version["meta"]["fields"][0], 1.0 if day in self.world.constant else factor_value(version["item_id"], day, name)) for name in names]
        frame = pd.DataFrame(rows, columns=["datetime", "instrument", "field", "value"])
        prices = self.world.prices.loc[[day * DAY for day in days]]
        prices = prices.assign(**{item["instrumentId"]: prices[NAMES[0]] for item in self.world.joined})
        return Made(rows=frame, present={day * DAY for day in days}, prices=prices, total=len(NAMES) + len(self.world.joined),
                    instruments=INFO["instruments"] + self.world.joined)


def history_rows(item_seed, through_day, field="value", first_day=1):
    rows = []
    for day in range(first_day, through_day + 1):
        for name in NAMES:
            rows.append((name, field, day * DAY, factor_value(item_seed, day, name), close_of(day), close_of(day), "history"))
    return rows


def factor_spec(name, version_id, through_day, *, inputs=None, kind="factor", published=0.02, holding=2, delay=1, first_day=1, item_seed=None):
    meta = {"producer": "fake", "fields": ["value"], "timeframe": "1d", "info": {**INFO, "since": DAY, "until": through_day * DAY}, "edge": "close",
            "publication": {"kind": kind, "name": name, "field": "value"}, "horizon": {"holding": holding, "delay": delay}, "publishedThrough": through_day * DAY,
            "measure": f"Rank IC {holding}D", "universe": "Eight names", "settingsText": "fake", "origin": {"objectId": "o-" + name, "runId": "r-" + version_id,
                                                                                                          "objectName": name, "objectKind": kind},
            "inputs": inputs or [], "instrumentCount": len(NAMES), **({"rebalance": {"every": "month"}} if kind == "allocation" else {})}
    return {"name": name, "kind": kind, "meta": meta, "rows": history_rows(item_seed or name, through_day, first_day=first_day) if not inputs else [],
            "objectId": "o-" + name, "runId": "r-" + version_id, "workspaceId": "ws", "state": "updating", "cursor": through_day * DAY,
            "checks": {"metric": "rank_ic", "value": published, "t": 3.0}, "versionId": version_id,
            "holds": [{"versionId": entry["versionId"]} for entry in inputs or []]}


def build(tmp_path, *, seed=1, start_day=60):
    """A library, its loop on a fake clock, the fake world and the notices sent."""
    ctx = Context(tmp_path)
    records = WorkRecords(tmp_path / "records")
    clock = {"now": close_of(start_day) + 6 * 60_000}
    world = World(seed)
    built = SimpleNamespace(ctx=ctx, records=records, clock=clock, world=world, queue=RunQueue())
    return reopen(built)


def reopen(built):
    """TALYX closed and opened again: a new library over the same files and a new loop that remembers nothing of the last one."""
    from modules.quant.backend.library import Library
    from modules.quant.backend.library_loop import LibraryLoop
    from modules.quant.backend.notifications import Notifications
    clock, world = built.clock, built.world
    built.library = Library(built.ctx, built.records, clock=lambda: clock["now"])
    built.store = built.library.store
    built.notifications = Notifications(built.ctx, built.records)
    built.loop = LibraryLoop(built.ctx, built.records, built.library, built.notifications, clock=lambda: clock["now"], calendar=world.calendar, queue=built.queue,
                             producer=lambda version: Producer(world, built.library.store, lambda: clock["now"]))
    return built


# --- the synthetic numbers of the data checks (D2) -----------------------------------------------------------------------------------------
def synthetic_after(seed=7, names=50, days=300, published_days=200, holding=5, delay=0):
    """Prices, a factor that is the next day's return plus noise, and the labels: 50 names for 300 days, published after day 200 (labels 0..199)."""
    rng = np.random.default_rng(seed)
    columns = [f"i:{index:02d}" for index in range(names)]
    labels = [day * DAY for day in range(days)]
    prices = pd.DataFrame(100 * np.exp(np.cumsum(rng.normal(0, .02, (days, names)), axis=0)), index=labels, columns=columns)
    following = prices.shift(-1) / prices - 1
    noise = pd.DataFrame(rng.normal(0, .02, (days, names)), index=labels, columns=columns)
    values = (following.fillna(0) + noise)
    table = values.stack().rename("value").reset_index().rename(columns={"level_0": "datetime", "level_1": "instrument"})
    return SimpleNamespace(prices=prices, values=table, published_through=(published_days - 1) * DAY, holding=holding, delay=delay, labels=labels)


class RunQueue:
    """A run queue that only records the places asked for: owner, whether it is interactive, how much memory it was told to expect."""

    def __init__(self):
        self.slots = []
        self.limit = None  # an asyncio.Semaphore a test sets to make updates wait for a place

    def slot(self, *, owner, label, interactive, memory, on_wait=None):
        from contextlib import asynccontextmanager

        @asynccontextmanager
        async def held():
            if self.limit is not None:
                await self.limit.acquire()
            self.slots.append({"owner": owner, "label": label, "interactive": interactive, "memory": memory})
            try:
                yield
            finally:
                if self.limit is not None:
                    self.limit.release()
        return held()


# --- publishing a factor run (K5) ----------------------------------------------------------------------------------------------------------
PUBLISH_DAYS = 120


class PublishEngine:
    """The factor engine as publishing sees it: it writes the values of every bar and, per cut-off, a progress line; it can find future data."""

    def __init__(self):
        self.jobs = []
        self.gate = None  # an asyncio.Event the job waits on, so a test can cancel it midway
        self.future_at = None  # the 1-based check that finds future data
        self.found = None

    async def run_open(self, job):
        import json
        from pathlib import Path
        self.jobs.append(job)
        output = Path(job["output"])
        output.mkdir(parents=True, exist_ok=True)
        if self.gate is not None:
            await self.gate.wait()
        cutoffs = job.get("cutoffs") or []
        for number in range(1, len(cutoffs) + 1):
            with open(output / "progress.jsonl", "a", encoding="utf-8") as stream:
                stream.write(json.dumps({"stage": "future", "done": number - 1, "total": len(cutoffs)}) + "\n")
            if self.future_at == number:
                return {"ok": True, "futureFound": {"datetime": cutoffs[number - 1], **self.found, "cutoff": cutoffs[number - 1]}}
        rows = [(day * DAY, name[2:], factor_value("published", day, name)) for day in range(1, PUBLISH_DAYS + 1) for name in NAMES]
        pd.DataFrame(rows, columns=["datetime", "instrument", "value"]).to_parquet(output / "values.parquet", index=False)
        return {"ok": True, "checked": len(cutoffs)}


class PublishRuns:
    """The run records publishing reads and writes, over the real work records."""

    def __init__(self, records, root):
        self.records, self.root, self.closed = records, root, False
        self.tasks, self.processes = {}, {}
        self.store = SimpleNamespace(workspace=lambda: "ws")

    def read(self, identity):
        record = self.records.find("qrun", identity)
        if record is None:
            from talyx_sdk import ContractError
            raise ContractError("not_found", "No such run")
        return record

    def update(self, identity, **changes):
        return self.records.put("qrun", {**self.read(identity), **changes})

    def directory(self, identity):
        return self.root / identity

    def result(self, record):
        return None


def source_run(identity="run-src", *, number=7, kind="factor", name="Reversal", rank_ic=0.021, held_from=None, checks=None, run_kind="run", state="completed"):
    from pathlib import Path  # noqa: F401
    return {"id": identity, "objectId": "o-" + identity, "objectKind": kind, "name": name, "runKind": run_kind, "state": state, "mode": "formula", "number": number,
            "settings": {"run": {"holding": 5, "delay": 1, "neutralize": "none"}, "data": {"symbols": [{"instrument": {"displaySymbol": "AAPL"}}]}, "params": {}},
            "data": {"key": "k1", "heldOutFrom": held_from}, "numbers": {"rankIc": rank_ic, "t": 2.41, "longShortNet": 0.083, "longShortGross": 0.097, "turnover": 0.31, "names": 497},
            "formula": "Rank(-$close)", "code": None, "common": None, "environment": None, "checks": checks or [], "heldOut": None, "createdAt": 1, "starred": False}


def publishing(tmp_path, *, source=None, world=None):
    """A library world with a Publisher over fake runs, a fake factor engine and frozen bars of eight names for 120 days (labels day 1 to 120)."""
    from modules.quant.backend.library_publish import Publisher
    tmp_path.mkdir(parents=True, exist_ok=True)
    world = world or build(tmp_path)
    ctx = world.ctx

    async def cancel(args):
        return None

    async def sessions(args):
        return {"calendarId": args["calendarId"], "timezone": "UTC", "mode": "exchange", "evidence": "verified",
                "coveredFrom": None, "coveredUntil": None, "sessions": []}

    async def boundaries(args):
        first, last = int(args["start"]) // 1_000_000 // DAY, (int(args["end"]) - 1) // 1_000_000 // DAY
        days = [day for day in range(first, last + 1) if int(args["start"]) <= day * DAY * 1_000_000 < int(args["end"])]
        return {"evidence": "verified", "reason": None, "timezone": "UTC", "next": None,
                "open": [str(day * DAY * 1_000_000) for day in days], "close": [str(close_of(day) * 1_000_000) for day in days]}
    ctx.platform.execution = SimpleNamespace(cancel=cancel)
    ctx.platform.leases = SimpleNamespace(close=cancel)
    ctx.require = lambda name: SimpleNamespace(bar_times=boundaries, sessions=sessions)
    runs = PublishRuns(world.records, tmp_path / "runs")
    bars_path = tmp_path / "bars.parquet"
    bars = pd.DataFrame([(day * DAY, name[2:], 100.0 + day) for day in range(1, PUBLISH_DAYS + 1) for name in NAMES], columns=["label", "column", "close"])
    bars.assign(open=bars["close"]).to_parquet(bars_path, index=False)
    info = {**INFO, "since": DAY, "until": PUBLISH_DAYS * DAY}
    ready = {"key": "k1", "barsPath": str(bars_path), "info": info, "extrasPath": None, "directory": str(tmp_path / "frozen")}
    engine = PublishEngine()
    factors = SimpleNamespace(data=SimpleNamespace(ready=lambda key: ready, prepare=None), engine=engine, directory=lambda identity: tmp_path / "factors" / identity,
                              read=lambda identity: runs.read(identity), update=lambda identity, **changes: runs.update(identity, **changes))
    world.runs, world.factors, world.engine, world.ready = runs, factors, engine, ready
    world.publisher = Publisher(ctx, world.records, world.store, runs, world.library, factors, None)
    world.records.put("qrun", source or source_run())
    return world


def write_similar(world, identity, similar):
    """The run's `work.json` as Work wrote it: the library factors it resembles, in order of how much."""
    import json
    folder = world.factors.directory(identity) / "output"
    folder.mkdir(parents=True, exist_ok=True)
    (folder / "work.json").write_text(json.dumps({"similar": similar}), encoding="utf-8")


async def publish_and_wait(world, name="Reversal", run_id="run-src", **args):
    record = await world.publisher.start(run_id, {"name": name, **args})
    task = world.runs.tasks[record["id"]]
    await asyncio.gather(task, return_exceptions=True)
    return world.runs.read(record["id"])


# --- publishing a model with the factors it uses (K6) ---------------------------------------------------------------------------------------
class FakeModels:
    """The model runs, the factor runs they pin and the engine that makes a formula factor's values: what `ModelLibrary.commit` reads."""

    def __init__(self, root):
        self.root, self.runs, self.saved = root, {}, []
        self.store = SimpleNamespace(workspace=lambda: "ws", read=lambda identity: {"name": "Factor " + identity})
        self.engine = SimpleNamespace(call=self.call)
        self.factors = SimpleNamespace(read=lambda identity: self.runs[identity], directory=lambda identity: root / "factors" / identity,
                                       save=lambda record, **changes: self.save(record, **changes))

    def experiment(self, identity):
        return {"metrics": self.runs[identity].get("numbers")}

    def directory(self, identity):
        return self.root / identity

    def read(self, identity):
        return self.runs[identity]

    def save(self, record, **changes):
        self.saved.append((record["id"], changes))
        self.runs[record["id"]] = {**record, **changes}
        return self.runs[record["id"]]

    async def call(self, operation, job):
        from pathlib import Path
        assert operation == "formula_values"
        output = Path(job["output"])
        output.mkdir(parents=True, exist_ok=True)
        rows = [(day * DAY, name[2:], factor_value(job["formula"], day, name)) for day in range(1, PUBLISH_DAYS + 1) for name in NAMES if day * DAY > job["after"]]
        path = output / "values.parquet"
        pd.DataFrame(rows, columns=["label", "column", "value"]).to_parquet(path, index=False)
        return {"ok": True, "rows": len(rows), "path": str(path)}


def model_publishing(world, tmp_path):
    """A `ModelLibrary` over the library world (and `publishing`'s frozen bars and calendar), with `models.new_run(name, factors)` to put a trained run in."""
    import inspect
    import json

    from modules.quant.backend.model_library import ModelLibrary
    if not hasattr(world, "ready"):
        publishing(tmp_path, world=world)
    models = FakeModels(tmp_path / "models")
    parameters = inspect.signature(ModelLibrary).parameters
    arguments = [world.ctx, world.records, models, world.library] + ([SimpleNamespace(pipeline=None)] if "loop" in parameters else [])
    library = ModelLibrary(*arguments)

    def new_run(name, factor_names):
        number = len(models.runs) + 1
        factor_runs = []
        for position, factor_name in enumerate(factor_names):
            identity = f"frun-{name}-{position}"
            models.runs[identity] = {"id": identity, "objectId": "fo-" + identity, "name": factor_name, "number": position + 1, "mode": "formula", "formula": f"Rank($close, {position})",
                                     "version": None}
            factor_runs.append(identity)
        identity = f"mrun-{name}"
        labels = [day * DAY for day in range(1, PUBLISH_DAYS + 1)]
        columns = [item[2:] for item in NAMES]
        folder = models.directory(identity)
        (folder / "output").mkdir(parents=True)
        (folder / "source").mkdir()
        (folder / "source" / "model_standard.py").write_text("# frozen pipeline")
        (folder / "windows" / "0").mkdir(parents=True)
        (folder / "windows" / "0" / "model.bin").write_bytes(b"w")
        np.save(folder / "output" / "pred.npy", np.random.default_rng(number).normal(size=(len(labels), len(columns))))
        (folder / "output" / "pred.json").write_text(json.dumps({"labels": labels, "columns": columns}), encoding="utf-8")
        (folder / "output" / "work.json").write_text("{}", encoding="utf-8")
        models.runs[identity] = {"id": identity, "objectId": "mo-" + identity, "name": name, "number": number, "mode": "standard", "state": "completed", "runKind": "train",
                                 "settings": {"data": {"symbols": []}, "run": {"cost": 5}, "params": {"every_months": 3, "algorithm": "lightgbm",
                                                                                    "inputs": [{"source": "run", "ref": run, "parameter": f"f{index}"} for index, run in enumerate(factor_runs)]}},
                                 "pins": [{"runId": run} for run in factor_runs], "overrides": {}, "numbers": {"icMean": 0.03}, "codeSnapshot": None, "data": {"dataTo": labels[-1]},
                                 "checks": []}
        plan = {"alsoPublished": [{"name": factor_name, "fromRun": index + 1, "runId": run, "formula": models.runs[run]["formula"], "autoUpdate": True}
                                  for index, (factor_name, run) in enumerate(zip(factor_names, factor_runs, strict=True))], "pinned": [], "futureDataCheck": {}}
        return identity, {"name": name, "autoUpdate": True, "plan": plan, "versions": []}
    models.new_run = new_run
    return SimpleNamespace(library=library, models=models, world=world)
