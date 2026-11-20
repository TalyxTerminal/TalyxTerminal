"""A model's whole Work on the real Quant backend with a stand-in Market and small synthetic data (40 names, three years of days),
the research engine run for real: preparation → check before training and time axis → a batch of two → the experiment table →
compare and combine → Results, figures and a day's values → held-out validation → publishing the model with the factor run it reads →
the library keeping it current (upstream missing until the factor has its new value, then only what is new).

Not part of the daily suite: set TALYX_NATIVE_SMOKE=1 (see environments.py).
"""
from __future__ import annotations

import asyncio
import json
import os
import subprocess
from pathlib import Path
from types import SimpleNamespace

import numpy as np
import pandas as pd
import pytest

from modules.quant.backend import research_calendar
from modules.quant.backend import research_data as D
from modules.quant.backend.object_routes import Services
from modules.quant.backend.work_records import WorkRecords
from tests.native.environments import environment
from tests.native.test_quant_model_pipeline import ROLES
from tests.unit.test_quant_object_runs import INSTRUMENT, FakeContext

pytestmark = pytest.mark.skipif(not os.environ.get("TALYX_NATIVE_SMOKE"), reason="Set TALYX_NATIVE_SMOKE=1 to run native engines")

DAY = 86_400_000
START = 1_609_459_200_000  # 2021-01-01
DAYS = 1100
NAMES = 40
UNTIL_DAY = 1060  # the model's data end here; the last 40 days arrive after publishing
QUANT = Path(__file__).resolve().parents[2] / "modules" / "quant"


def instrument(index):
    return {**INSTRUMENT, "identityVersion": 2, "instrumentId": f"vi2_{index:032x}",
            "symbol": f"C{index}/USDT", "displaySymbol": f"C{index}/USDT", "nativeId": f"C{index}USDT"}


def bars(index, signal):
    rng = np.random.default_rng(100 + index)
    returns = 0.003 * np.concatenate([[0.0], signal[:-1, index]]) + rng.normal(0, .02, DAYS)
    close = 100 * np.exp(np.cumsum(returns))
    times = START + np.arange(DAYS, dtype="int64") * DAY
    return pd.DataFrame({"time": times, "open": close, "high": close * 1.01, "low": close * .99, "close": close, "volume": rng.integers(1_000, 9_000, DAYS).astype(float)})


class Captures:
    """Market's capture: one parquet of bars per selection (prices follow yesterday's reversal a little, so models find something)."""

    def __init__(self, root):
        self.root = root
        closes = np.exp(np.cumsum(np.random.default_rng(7).normal(0, .02, (DAYS, NAMES)), axis=0))
        self.signal = -(closes / np.vstack([closes[:1], closes[:-1]]) - 1) * 10

    def __call__(self, ctx):
        return self

    async def capture(self, request, owner):
        found = []
        for selection in request["market"]["selections"]:
            index = int(selection["instrument"]["instrumentId"][4:], 16)
            path = self.root / f"capture-{index}.parquet"
            path.parent.mkdir(parents=True, exist_ok=True)
            frame = bars(index, self.signal)
            frame = frame[frame["time"].between(request["market"]["since"], request["market"]["until"])].copy()
            frame["instrumentId"] = selection["instrument"]["instrumentId"]
            frame["closeTime"] = pd.to_datetime((frame["time"] + DAY) * 1000000, unit="ns", utc=True)
            frame["knownAt"] = frame["closeTime"]
            first, last = int(frame["time"].min()), int(frame["time"].max())
            frame["time"] = pd.to_datetime(frame["time"] * 1000000, unit="ns", utc=True)
            frame.to_parquet(path, index=False)
            found.append({**selection, "files": [{"path": str(path)}], "version": last, "source": {"sourceId": "fixture", "name": "Fixture"},
                          "coverage": {"exportedRows": len(frame), "exportedRange": {"start": first, "end": last}}})
        return {"manifest": {"selections": found}, "references": []}


def once(script, invocation, output):
    output = Path(output)
    output.mkdir(parents=True, exist_ok=True)
    path = output.parent / f"invocation-{output.name}.json"
    path.write_text(json.dumps(invocation), encoding="utf-8")
    done = subprocess.run([str(environment("research.lock", "3.12")), "-B", str(QUANT / "native" / script), str(path)], capture_output=True, text=True, encoding="utf-8", timeout=900)
    (output / "stderr.log").write_text(done.stderr, encoding="utf-8")
    assert (output / "answer.json").is_file(), done.stderr[-3000:]
    return json.loads((output / "answer.json").read_text(encoding="utf-8"))


def stand_in(tmp_path, monkeypatch):
    ctx = FakeContext(tmp_path, None)
    notices = []
    ctx.platform.notifications = SimpleNamespace(toast=lambda args: asyncio.sleep(0, notices.append(args)), channels=lambda args: asyncio.sleep(0, []),
                                                 send=lambda args: asyncio.sleep(0, None))
    monkeypatch.setattr(D, "Materials", Captures(tmp_path / "market"))
    boundaries = [(START + day * DAY, START + (day + 1) * DAY) for day in range(DAYS)]
    monkeypatch.setattr(research_calendar, "boundaries", lambda *args: asyncio.sleep(0, ("UTC", [(opened, closed) for opened, closed in boundaries if args[-2] <= opened <= args[-1]])))
    shell = Services(ctx, WorkRecords(tmp_path / "records"))
    engine = shell.factors.engine

    async def run_open(job, on_future=None, publish=None):
        return await asyncio.to_thread(once, "factor_resident.py", {"mode": "once", "job": job}, job["output"])

    async def call(op, job, publish=None, seconds=120):
        output = Path(job.get("output") or tmp_path / "calls") / ("call-" + op)
        return await asyncio.to_thread(once, "factor_resident.py", {"mode": "once", "op": op, "job": {**job, "output": str(output)}}, output)

    async def run_model(job, directory, *, context, publish=None, on_lease=None):
        return await asyncio.to_thread(once, "model_run.py", {"job": job}, job["output"])
    async def run_results(job, directory, *, context, publish=None):
        return await asyncio.to_thread(once, "factor_results.py", {"mode": "once", "op": "run", "job": job}, job["output"])
    monkeypatch.setattr(engine, "run_results", run_results)
    monkeypatch.setattr(engine, "run_open", run_open)
    monkeypatch.setattr(engine, "call", call)
    monkeypatch.setattr(engine, "run_model", run_model)
    monkeypatch.setattr(shell.models, "values_engine", lambda deep: SimpleNamespace(call=call))  # the kept values process, run once here like the others
    return ctx, shell, notices


async def settled(tasks, identities, seconds=600):
    async with asyncio.timeout(seconds):
        while any(identity in tasks for identity in identities):
            await asyncio.sleep(.05)


def assert_roles(figure, key):
    assert figure["layout"]["meta"]["key"] == key
    for trace in figure["data"]:
        assert trace["meta"]["role"] in ROLES | {"detail"} and trace["meta"]["key"], (key, trace.get("name"))


def test_a_model_goes_from_preparation_to_a_published_model_kept_current(tmp_path, monkeypatch):
    async def scenario():
        ctx, shell, notices = stand_in(tmp_path, monkeypatch)
        store, models, factors, publishing = shell.store, shell.models, shell.factors, shell.model_library
        symbols = [{"instrument": instrument(index)} for index in range(NAMES)]
        until = time_day(UNTIL_DAY)
        data = {"symbols": symbols, "timeframe": "1d", "since": "2021-01-01", "until": until}
        # A factor run, not published: the model's first input.
        factor = await store.create({"kind": "factor", "name": "Reversal"})
        await store.update_settings(factor["id"], {"data": data})
        run = await factors.start(factor["id"], {"formula": "-($close / Ref($close, 1) - 1)", "settings": {"grouping": {"count": 5}}})
        await settled(factors.tasks, [run["id"]])
        assert factors.read(run["id"])["state"] == "completed", factors.read(run["id"]).get("failure")

        # Preparation: the standard pipeline's groups, nothing chosen.
        created = await store.create({"kind": "model"})
        assert created["name"] == "New model" and not created["hasCode"] and created["settings"]["mode"] == "standard"
        model_id = created["id"]
        await store.update_settings(model_id, {"data": {**data, "heldOutFrom": "2023-07-01"}})
        prep = await models.prep(model_id)
        assert [group["name"] for group in prep["groups"]] == ["Data", "Inputs", "Processing", "Label", "Split", "Algorithm"]
        assert prep["values"]["algorithm"] == "" and prep["values"]["inputs"] == [] and prep["values"]["label"] == "CSRank(Ref($open, -6) / Ref($open, -1) - 1)"
        assert {item["source"] for item in prep["offers"]} >= {"run", "builtin", "market"}
        xgb = next(item for item in prep["algorithms"] if item["id"] == "xgboost")
        assert [entry["label"] for entry in xgb["params"]][2:7] == ["Column sample", "Row sample", "Alpha", "Lambda", "Min child weight"]
        lstm = next((item for item in prep["algorithms"] if item["id"] == "lstm"), None)
        assert lstm is not None and lstm["deep"], "neural networks are listed; choosing one without the deep environment offers its install"
        if not models.engine.packages.deep_status()["installed"]:
            await store.update_settings(model_id, {"params": {"algorithm": "lstm"}})
            blocked = await models.check(model_id)
            assert blocked["blocked"] and any(item["name"] == "Environment" and item["state"] == "block" for item in blocked["checks"]), blocked.get("checks")
        params = {"inputs": [{"source": "run", "name": "Reversal #1", "ref": run["id"]}, {"source": "market", "name": "Volume", "ref": "$volume"}],
                  "algorithm": "lightgbm", "lgb_rounds": 30, "lgb_stop": 10, "lgb_leaves": 15, "lgb_minleaf": 20, "train_months": 12, "valid_months": 3,
                  "every_months": 3, "leave_out_young": False}
        await store.update_settings(model_id, {"params": params})

        # The check before training and the time axis; a batch of two.
        checked = await models.check(model_id, {"vary": [{"name": "lgb_lr", "values": [0.05, 0.1]}]})
        assert checked["ok"] and not checked["blocked"], checked
        assert checked["axis"]["windows"] and checked["axis"]["heldOut"] and checked["axis"]["gap"] == 5
        assert len(checked["batch"]["groups"]) == 2 and all(group["memory"] for group in checked["batch"]["groups"])
        sent = await models.train(model_id, {"vary": [{"name": "lgb_lr", "values": [0.05, 0.1]}]})
        ids = [item["id"] for item in sent["runs"]]
        assert sent["batch"] and len(ids) == 2
        await settled(models.tasks, ids)
        table = models.list(model_id)
        assert [item["status"] for item in table["items"]] == ["completed", "completed"], [item.get("failure") for item in table["items"]]
        first = models.experiment(ids[0])
        assert first["windowsDone"] == first["windowsTotal"] >= 3 and first["metrics"]["ric"] is not None and len(first["checks"]) == 8
        assert all(window["test"] and window["testIc"] is not None for window in first["windows"])
        assert table["batches"][0]["vary"] == ["lgb_lr"]

        # Compare and combine.
        compared = models.compare(ids)
        assert any(item["what"] == "params.lgb_lr" for item in compared["settings"])
        combined = await models.combine(ids)
        await settled(models.tasks, [combined["id"]])
        assert models.read(combined["id"])["state"] == "completed", models.read(combined["id"]).get("failure")

        # Results, figures with roles and keys, a day's values.
        results = models.results(ids[0])
        assert {"windows", "returns", "information", "turnover", "inputs", "robustness"} <= set(results["results"])
        figures = models.figures(ids[0])["figures"]
        for key, figure in figures.items():
            assert_roles(figure, key)
        compare = models.figures(ids[0], compare=ids[1])["figures"]
        assert any(trace["meta"]["role"] == "compare" for figure in compare.values() for trace in figure["data"])
        day = await models.values(ids[0])
        assert day["rows"] and day["rows"][0]["contributions"]

        # Held out: the windows of the held-out period, scored once.
        held = await models.held_out(ids[0])
        await settled(models.tasks, [held["id"]])
        assert models.read(held["id"])["state"] == "completed", models.read(held["id"]).get("failure")
        assert models.read(ids[0])["heldOut"]["state"] == "completed"

        # Publishing: one model item, the factor run published with it, the future-data check on one window.
        plan = publishing.plan(ids[0], "Reversal model")
        assert plan["produces"] == "model" and [item["runId"] for item in plan["alsoPublished"]] == [run["id"]]
        publication = await publishing.publish(ids[0], {"name": "Reversal model", "autoUpdate": True})
        await settled(publishing.tasks, [publication["id"]])
        done = publishing.read(publication["id"])
        assert done["state"] == "completed", done
        assert done["future"]["passed"] and len(done["versions"]) == 2
        items = {item["kind"]: item for item in shell.library.list({})["items"]}
        assert set(items) == {"factor", "model"}
        model_version = items["model"]["versions"][0]["versionId"]
        assert shell.library.read_version(model_version)["carriesValues"]
        assert shell.library.store.occupants(items["factor"]["itemId"])[0]["owner_id"] == "qlib:" + model_version
        assert next(item for item in models.read(ids[0])["checks"] if item["id"] == "future_data")["state"] == "ok"

        # Kept current: five new days; the loop makes the factor's values first (inputs first), then the model's from its window cache, only the new days.
        from modules.quant.backend.library_loop import DELAY
        version = shell.library.store.version(model_version)
        cursor = version["cursor"]
        bars = [(START + day * DAY, START + day * DAY, START + (day + 1) * DAY) for day in range(DAYS)]
        due = [bar for bar in bars[UNTIL_DAY + 1:UNTIL_DAY + 6] if bar[0] > cursor]
        shell.loop.clock = lambda: due[-1][2] + DELAY + 60_000
        shell.loop.calendar = lambda *_args: asyncio.sleep(0, [bar for bar in bars if bar[2] <= due[-1][2] + 3 * DAY])
        await shell.loop.cycle()
        factor_version = shell.library.store.version(items["factor"]["versions"][0]["versionId"])
        assert factor_version["cursor"] == due[-1][0], "the factor was made up to the newest bar first"
        after = [row for row in shell.library.store.read_values(model_version, start=due[0][0]) if row["field"] == "prediction"]
        assert sorted({row["datetime"] for row in after}) == [label for label, _, _ in due] and len(after) == len(due) * NAMES
        return checked
    checked = asyncio.run(scenario())
    assert checked["seconds"] < 30


def time_day(day):
    return pd.Timestamp(START + day * DAY, unit="ms").strftime("%Y-%m-%d")
