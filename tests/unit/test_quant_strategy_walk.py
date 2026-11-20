"""Walk-forward as a run: windows that fit, each window picked on its own months and run again on the ones after it, the stitched equity, and a window
in which nothing traded keeping the values before it. The engine is a stand-in that writes the equity and trades a real window would."""
import asyncio
import json
from datetime import UTC, datetime

import pandas as pd
import pytest
from talyx_sdk import ContractError

from modules.quant.backend import object_data
from modules.quant.backend.run_queue import RunQueue
from tests.fixtures.strategy.fakes import services, strategy
from tests.unit.test_quant_object_runs import STARTER
from tests.unit.test_quant_strategy_sweeps import GB, Runner, axes_of, settled

DAY = 86_400_000


def ms(text):
    return int(datetime.fromisoformat(text).replace(tzinfo=UTC).timestamp() * 1000)


def scenario(tmp_path, body):
    async def run():
        ctx, records, shell = services(tmp_path)
        try:
            return await body(ctx, shell)
        finally:
            records.close()
    return asyncio.run(run())


class WalkRunner(Runner):
    """Daily equity whose daily growth is the fast window over 1,000; a value in `silent` never trades."""

    def __init__(self, shell, silent=()):
        super().__init__(shell)
        self.silent, self.windows = silent, []

    async def run_case(self, sweep_id, obj, directory, prepared, state, *, identity, params, overrides, link, extra, scale=None, on_start=None):
        record = await super().run_case(sweep_id, obj, directory, prepared, state, identity=identity, params=params, overrides=overrides, link=link, extra=extra, scale=scale, on_start=on_start)
        if record["state"] != "completed":
            return record
        window = (extra or {}).get("window")
        begin, end = (extra or {}).get("tradeFrom") or ms("2022-01-01"), (window or {}).get("to") or ms("2024-12-31")
        if window is None:
            begin = ms("2022-01-01")
        growth = params.get("fast", 10) / 1_000
        capital = 10_000 * (scale or 1.0)
        points, level, at = [], capital, begin
        while at <= end:
            points.append([at, level])
            level *= 1 + growth * (1 if (at // DAY) % 3 else -0.5)
            at += DAY
        if window is not None:
            self.windows.append({"params": params, "tradeFrom": begin, "capital": capital})
        folder = self.shell.runs.directory(identity) / "output" / "strategy"
        folder.mkdir(parents=True, exist_ok=True)
        (folder / "work.json").write_text(json.dumps({"equity": points, "annualization": 365, "dailyRiskFree": [[at, 0.0] for at, _ in points[1:]], "tradesTotal": 0 if params.get("fast") in self.silent else 5}), encoding="utf-8")
        entries = [] if params.get("fast") in self.silent else [begin + DAY, begin + 5 * DAY]
        pd.DataFrame({"entry": entries}).to_parquet(folder / "trades.parquet")
        return record


def patch(shell, monkeypatch, since="2022-01-01", until="2024-12-31"):
    async def resolve(ctx, data):
        return {"since": since, "until": until}
    monkeypatch.setattr(object_data, "resolve_until", resolve)


async def walk(shell, runner, **plan):
    shell.sweeps.queue = shell.runs.queue = RunQueue(memory=64 * GB, cpus=8)

    async def per_case(obj, settings, newest):
        return GB
    shell.sweeps.case_memory = per_case
    obj = await strategy(shell, STARTER)
    record = await shell.sweeps.start(obj, {"axes": axes_of(fast=[5, 10, 20]), **plan}, kind="walk")
    return obj, await settled(shell, record["id"])


def test_walk_needs_three_windows(tmp_path, monkeypatch):
    async def body(ctx, shell):
        patch(shell, monkeypatch, until="2022-10-31")
        WalkRunner(shell)
        with pytest.raises(ContractError) as caught:
            await walk(shell, None, pickOn=6, trade=2)
        assert caught.value.code == "too_few_windows" and "at least 3 needed" in str(caught.value)
    scenario(tmp_path, body)


def test_walk_windows_stitched_and_each_rerun(tmp_path, monkeypatch):
    async def body(ctx, shell):
        patch(shell, monkeypatch)
        runner = WalkRunner(shell)
        _, done = await walk(shell, runner, pickOn=6, trade=2)
        assert done["state"] == "completed", done.get("message")
        info = done["walk"]
        assert len(info["windows"]) >= 3 and len(runner.windows) == len(info["windows"])  # every window ran again, once
        # Each window started with the capital the stitched equity had reached.
        level = 10_000.0
        for window, row in zip(runner.windows, info["windows"], strict=True):
            assert window["capital"] == pytest.approx(level) and window["tradeFrom"] == row["traded"][0]
            level = window["capital"] * (1 + (row["returnTraded"] or 0))
        assert info["stitched"][-1][1] == pytest.approx(level / 10_000, abs=1e-9)
        assert info["efficiency"] is not None and info["changed"] >= 0
        # A walk-forward has no report of its own: the page shows its windows, and does not call it a run from an earlier TALYX.
        assert not any("earlier TALYX" in note for note in shell.strategy.run(done["id"])["run"]["notes"])
    scenario(tmp_path, body)


def test_walk_window_without_trades_keeps_previous(tmp_path, monkeypatch):
    async def body(ctx, shell):
        patch(shell, monkeypatch)
        # The best value (20) never trades: no window can pick it, so every window picks among the others.
        runner = WalkRunner(shell, silent=(20,))
        _, done = await walk(shell, runner, pickOn=6, trade=2)
        assert all(row["values"]["fast"] != 20 for row in done["walk"]["windows"])
    scenario(tmp_path, body)


def test_walk_with_nothing_traded_uses_current_values(tmp_path, monkeypatch):
    async def body(ctx, shell):
        patch(shell, monkeypatch)
        runner = WalkRunner(shell, silent=(5, 10, 20))
        _, done = await walk(shell, runner, pickOn=6, trade=2)
        rows = done["walk"]["windows"]
        assert all(row["sharpePicked"] is None for row in rows) and rows[0]["kept"] == 0
        assert all(row["kept"] == row["n"] - 1 and row["values"] == rows[0]["values"] for row in rows[1:])
    scenario(tmp_path, body)


def test_a_failed_window_is_named_and_the_walk_goes_on_from_it(tmp_path, monkeypatch):
    """a window that fails stops the walk (the windows after it start from the capital it ends with, so none can be skipped); the run says
    which window and why, the window's row says it failed, and resuming runs that window and the ones after it, not the ones before."""
    async def body(ctx, shell):
        patch(shell, monkeypatch)
        runner = WalkRunner(shell)
        _, whole = await walk(shell, runner, pickOn=6, trade=2)
        broken = WalkRunner(shell)
        failing = {"n": 3}

        original = broken.run_case

        async def run_case(sweep_id, obj, directory, prepared, state, **kwargs):
            if kwargs["link"].get("window") == failing["n"]:
                runs = shell.runs
                record = runs.read(sweep_id)
                base = {key: value for key, value in record.items() if key not in ("of", "walk")}
                runs.records.put("qrun", {**base, "id": kwargs["identity"], "runKind": "case", "mode": "run", "state": "running", "number": None, "sweep": kwargs["link"]})
                broken.ran.append(("window", failing["n"]))
                return runs.update(kwargs["identity"], state="failed", message="MemoryError: the process ran out of memory", endedAt=1)
            return await original(sweep_id, obj, directory, prepared, state, **kwargs)
        shell.sweeps.run_case = run_case
        _, failed = await walk(shell, broken, pickOn=6, trade=2)
        assert failed["state"] == "failed"
        rows = failed["walk"]["windows"]
        assert [row["n"] for row in rows] == [1, 2, 3] and rows[2]["failed"] == "MemoryError: the process ran out of memory"
        traded = "–".join(datetime.fromtimestamp(at / 1000, UTC).date().isoformat() for at in rows[2]["traded"])
        assert failed["message"] == f"Window 3 ({traded}) did not run: MemoryError: the process ran out of memory"
        assert failed["error"]["details"]["window"] == 3
        done_before = [row["runId"] for row in rows[:2]]
        failing["n"], broken.windows, broken.ran = None, [], []
        again = await shell.sweeps.resume(failed["id"])
        assert again["state"] == "preparing"
        finished = await settled(shell, failed["id"])
        assert finished["state"] == "completed" and finished["error"] is None, finished.get("message")
        rows = finished["walk"]["windows"]
        assert [row["runId"] for row in rows[:2]] == done_before and not any(row.get("failed") for row in rows)
        assert [item for item in broken.ran if isinstance(item, int)] == list(range(3, len(rows) + 1))  # no combination ran again, windows 3 on did
        assert len(rows) == len(whole["walk"]["windows"])
        assert finished["walk"]["stitched"][-1][1] == pytest.approx(whole["walk"]["stitched"][-1][1], rel=1e-12)
        assert finished["walk"]["efficiency"] == pytest.approx(whole["walk"]["efficiency"])
        with pytest.raises(ContractError) as caught:
            await shell.sweeps.resume(failed["id"])
        assert caught.value.code == "conflict"
    scenario(tmp_path, body)
