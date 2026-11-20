"""Symbols never become paths: names such as BTC/USDT:USDT, A:B, a Windows reserved word and two that differ only in case go through a whole
factor run (frozen through stand-in Market answers, computed by the real engine in the research environment), and the values of a day.

Not part of the daily suite: set TALYX_NATIVE_SMOKE=1 (see environments.py).
"""
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
from modules.quant.backend.object_routes import Services
from modules.quant.backend.work_records import WorkRecords
from tests.native.environments import environment
from tests.unit.test_quant_object_runs import INSTRUMENT, FakeContext

pytestmark = pytest.mark.skipif(not os.environ.get("TALYX_NATIVE_SMOKE"), reason="Set TALYX_NATIVE_SMOKE=1 to run native engines")

DAY = 86_400_000
START = 1_704_067_200_000  # 2024-01-01
DAYS = 160
SYMBOLS = ["BTC/USDT:USDT", "ETH/USDT", "A:B", "abc", "ABC", "CON", "x y*", "back\\slash", "SOL/USDT", "DOGE/USDT", "XRP/USDT", "ADA/USDT"]
QUANT = Path(__file__).resolve().parents[2] / "modules" / "quant"


def instrument(index, symbol):
    return {**INSTRUMENT, "identityVersion": 2, "instrumentId": f"vi2_{index:032x}",
            "symbol": symbol, "displaySymbol": symbol, "nativeId": symbol.replace("/", "")}


def bars(index):
    rng = np.random.default_rng(index)
    close = 100 * np.exp(np.cumsum(rng.normal(0, .02, DAYS)))
    times = START + np.arange(DAYS, dtype="int64") * DAY
    return pd.DataFrame({"time": times, "open": close, "high": close * 1.01, "low": close * .99, "close": close, "volume": rng.integers(1_000, 9_000, DAYS).astype(float)})


class Captures:
    """Market's capture: one parquet of bars per selection, in a folder of its own."""

    def __init__(self, root):
        self.root = root

    def __call__(self, ctx):
        return self

    async def capture(self, request, owner):
        found = []
        for selection in request["market"]["selections"]:
            index = int(selection["instrument"]["instrumentId"][4:], 16)
            path = self.root / f"capture-{index}.parquet"
            path.parent.mkdir(parents=True, exist_ok=True)
            frame = bars(index)
            frame["instrumentId"] = selection["instrument"]["instrumentId"]
            frame["closeTime"] = pd.to_datetime((frame["time"] + DAY) * 1000000, unit="ns", utc=True)
            frame["knownAt"] = frame["closeTime"]
            frame["time"] = pd.to_datetime(frame["time"] * 1000000, unit="ns", utc=True)
            frame.to_parquet(path, index=False)
            found.append({**selection, "files": [{"path": str(path)}], "version": "r1", "source": {"sourceId": "fixture", "name": "Fixture"},
                "coverage": {"exportedRows": DAYS, "exportedRange": {"start": START, "end": START + (DAYS - 1) * DAY}}})
        return {"manifest": {"selections": found}, "references": []}


def run_engine(job):
    """The real engine, once, in the research environment: what the research process does with the job."""
    output = Path(job["output"])
    output.mkdir(parents=True, exist_ok=True)
    invocation = output.parent / "invocation.json"
    invocation.write_text(json.dumps({"mode": "once", "job": job}), encoding="utf-8")
    done = subprocess.run([str(environment("research.lock", "3.12")), "-B", str(QUANT / "native" / "factor_resident.py"), str(invocation)], capture_output=True, text=True, timeout=300)
    assert (output / "answer.json").is_file(), done.stderr[-3000:]
    answer = json.loads((output / "answer.json").read_text(encoding="utf-8"))
    return answer


def factor_run(tmp_path, monkeypatch, symbols, settings):
    """Freeze these names through the stand-in Market, run the formula in the real engine; returns (factors, the run)."""
    async def scenario():
        ctx = FakeContext(tmp_path, None)
        ctx.platform.artifacts = SimpleNamespace(import_file=lambda args: asyncio.sleep(0, {"artifactId": "art", "digest": "0" * 64}),
                                                 acquire=lambda args: asyncio.sleep(0, None), snapshot_project=ctx.snapshot)
        from modules.quant.backend import research_data
        monkeypatch.setattr(research_data, "Materials", Captures(tmp_path / "market"))
        boundaries = [(START + day * DAY, START + (day + 1) * DAY) for day in range(DAYS)]
        monkeypatch.setattr(research_calendar, "boundaries", lambda *args: asyncio.sleep(0, ("UTC", boundaries)))
        shell = Services(ctx, WorkRecords(tmp_path / "records"))
        factors = shell.factors

        async def run_open(job, on_future=None, publish=None):
            return await asyncio.to_thread(run_engine, job)
        monkeypatch.setattr(factors.engine, "run_open", run_open)
        chosen = [{"instrument": instrument(index, symbol)} for index, symbol in enumerate(symbols)]
        created = await shell.store.create({"kind": "factor", "name": "Reversal"})
        obj = shell.store.read(created["id"])
        data = {"symbols": chosen, "timeframe": "1d", "since": "2024-01-01", "until": "2024-06-08"}
        shell.store.save(obj, settings={**obj["settings"], "data": data})
        started = await factors.start(created["id"], {"formula": "-($close / Ref($close, 5) - 1)", "settings": settings})
        async with asyncio.timeout(300):
            while started["id"] in factors.tasks:
                await asyncio.sleep(.05)
        return factors, factors.read(started["id"])
    return asyncio.run(scenario())


def test_symbols_with_slashes_colons_reserved_words_and_case_go_through_a_run_and_the_values(tmp_path, monkeypatch):
    factors, run = factor_run(tmp_path, monkeypatch, SYMBOLS, {"grouping": {"count": 3}})
    assert run["state"] == "completed", run.get("failure")

    async def scenario():
        day = await asyncio.to_thread(factors.cross_section, run["id"], None, run)
        assert {row["ticker"] for row in day["rows"]} == set(SYMBOLS)
    asyncio.run(scenario())


@pytest.mark.parametrize("symbols, groups, title", [(["BTC/USDT:USDT"], 5, "needs more than one name"), (["BTC/USDT:USDT", "ETH/USDT", "A:B"], 5, "has fewer names than groups")])
def test_too_few_names_to_rank_end_the_run_with_a_reason_not_a_crash(tmp_path, monkeypatch, symbols, groups, title):
    _, run = factor_run(tmp_path, monkeypatch, symbols, {"grouping": {"count": groups}})
    assert run["state"] == "failed" and run["failure"]["title"] == title
    assert symbols[0] in run["failure"]["message"] and str(tmp_path) not in run["failure"]["message"]
