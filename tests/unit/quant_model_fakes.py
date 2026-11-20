"""A model run's Host side with the data and the research engine standing in: a stub engine that takes 0.1 s a window, writes what the research
process writes (progress lines, each finished window's cache, work.json, predictions) and has predictions that are a pseudo-random function of the run's
settings digest, so a resumed run can be compared bit for bit with one that ran through. A fake clock is not needed: the stub's pace is its own."""
from __future__ import annotations

import asyncio
import hashlib
import json
from pathlib import Path
from types import SimpleNamespace

import numpy as np

from modules.quant.backend.object_routes import Services
from modules.quant.backend.work_records import WorkRecords
from tests.unit.test_quant_object_runs import DATA, FakeContext

DAY = 86_400_000
WINDOW_SECONDS = 0.1
DAYS, NAMES = 60, 6


def digest_of(job):
    return hashlib.sha256(json.dumps({"params": job["params"], "data": job["data"]["key"]}, sort_keys=True, default=str).encode()).hexdigest()


class StubEngine:
    """`run_model(job, ...)`: window by window; `fail_at[(object name or run number)]` makes a window run out of memory; `calls` records each job."""

    def __init__(self, windows=5):
        self.windows, self.calls, self.fail_at, self.running, self.peak = windows, [], {}, 0, 0

    async def run_model(self, job, directory, *, context=None, publish=None, on_lease=None):
        self.calls.append(job)
        self.running += 1
        self.peak = max(self.peak, self.running)
        try:
            output, cache = Path(job["output"]), Path(job["cache"])
            output.mkdir(parents=True, exist_ok=True)
            seed = int(digest_of(job)[:8], 16)
            progress = output / "progress.jsonl"
            lines = []
            rng = np.random.default_rng(seed)
            predictions = rng.normal(size=(DAYS, NAMES))
            lines.append({"stage": "inputs", "state": "done", "windows": self.windows, "features": 2})
            for k in range(1, self.windows + 1):
                folder = cache / str(k)
                if job.get("keepDone") and (folder / "window.json").is_file() and (folder / "test.npz").is_file():
                    continue
                lines.append({"stage": "window", "state": "running", "window": k, "windows": self.windows, "round": 1, "rounds": 10, "fraction": 0.0})
                progress.write_text("".join(json.dumps(line) + "\n" for line in lines), encoding="utf-8")
                await asyncio.sleep(WINDOW_SECONDS)
                if self.fail_at.get(context["runId"]) == k or self.fail_at.get(job.get("kind"), None) == k:
                    return {"ok": False, "failure": {"stage": "compute", "window": k, "title": f"out of memory in window {k}", "message": f"Window {k} needed about 14.2 GB; 9.8 GB was free.",
                                                     "need": 14.2e9, "free": 9.8e9}}
                folder.mkdir(parents=True, exist_ok=True)
                (folder / "window.json").write_text(json.dumps({"k": k, "train": {"from": 0, "to": 1}, "valid": {"from": 2, "to": 3}, "test": {"from": 4 + k, "to": 5 + k},
                                                                "testSpan": [4 + k, 5 + k]}), encoding="utf-8")
                np.savez(folder / "test.npz", pred=predictions[4 + k:6 + k])
                lines.append({"stage": "window", "state": "done", "window": k, "trainIc": .05, "validIc": .02, "testIc": .03, "best": .01, "rows": 100, "secs": WINDOW_SECONDS})
                progress.write_text("".join(json.dumps(line) + "\n" for line in lines), encoding="utf-8")
            np.save(output / "pred.npy", predictions)
            (output / "pred.json").write_text(json.dumps({"labels": [d * DAY for d in range(DAYS)], "columns": [f"S{i}" for i in range(NAMES)]}), encoding="utf-8")
            work = {"numbers": {"ric": float(predictions.mean()), "t": 1.0, "windows": self.windows, "positive": self.windows}, "checks": [], "baselines": {}, "perWindow": True,
                    "features": ["a", "b"], "inputs": [], "horizon": 5, "dataFrom": 0, "dataTo": DAYS * DAY, "windows": [], "peakMemory": 1e9, "figureCount": 0}
            (output / "work.json").write_text(json.dumps(work), encoding="utf-8")
            (output / "answer.json").write_text(json.dumps({"ok": True}), encoding="utf-8")
            return {"ok": True, "seconds": .1, "windows": self.windows}
        finally:
            self.running -= 1


async def prepared(settings, report=None, calendars="one"):
    return {"key": "k", "barsPath": "bars.parquet", "extrasPath": "extras.json", "info": {"annualization": 252, "instruments": []}, "dataFrom": 0, "dataTo": DAYS * DAY,
            "names": NAMES, "requested": NAMES, "notes": [], "frozenAt": 0, "fingerprint": "data-k"}


def model_world(tmp_path, monkeypatch, *, windows=5, cpus=2):
    """Services with the data and the engine stubbed, a check that passes, and `make()` for a model object with an algorithm chosen."""
    ctx = FakeContext(tmp_path, None)
    shell = Services(ctx, WorkRecords(tmp_path / "records"))
    models, factors = shell.models, shell.factors
    engine = StubEngine(windows)
    monkeypatch.setattr(factors.data, "prepare", prepared)
    monkeypatch.setattr(factors.data, "key", lambda data: "k")
    monkeypatch.setattr(factors.data, "library_values", lambda *args, **kwargs: asyncio.sleep(0, None))
    monkeypatch.setattr(factors.data, "calendar_items", lambda data: asyncio.sleep(0, None))
    monkeypatch.setattr(models.engine, "run_model", engine.run_model)
    notices = []
    monkeypatch.setattr(models.notifications, "deliver", lambda *args: asyncio.sleep(0, notices.append(args)))
    models.queue.cpus = cpus
    models.queue.memory = 64 * 1024 ** 3

    async def check(object_id, args=None, report=None):
        from modules.quant.backend import model_prep as P
        groups = P.expand(P.values_of(models.store, models.model(object_id), (args or {}).get("params")), (args or {}).get("vary"))
        return {"ok": True, "blocked": False, "checks": [], "windows": windows, "seconds": .1, "dataKey": "k", "frozenData": models.frozen_data(await prepared({})),
                "batch": {"groups": [{"group": i + 1, "values": g["values"], "time": 1, "memory": 1e9, "blocked": False} for i, g in enumerate(groups)]}}
    monkeypatch.setattr(models, "check", check)

    async def make(name="Model", params=None):
        created = await shell.store.create({"kind": "model", "name": name})
        await shell.store.update_settings(created["id"], {"data": {**DATA, "timeframe": "1d", "since": "2016-01-01", "until": "2024-12-31"}, "run": {"cost": 5, "topN": 10},
                                                            "params": {"algorithm": "lightgbm", **(params or {})}})
        return created["id"]
    return SimpleNamespace(ctx=ctx, shell=shell, models=models, engine=engine, make=make, notices=notices)


async def settled(models, identity, timeout=10):
    async with asyncio.timeout(timeout):
        while identity in models.tasks:
            await asyncio.sleep(.01)
    return models.read(identity)


async def wait_for(condition, timeout=5):
    async with asyncio.timeout(timeout):
        while not condition():
            await asyncio.sleep(.01)
