"""The same day of a run's values asked for twice at once (the page asks again while the first look is building) is made once; another day waits its turn."""
from __future__ import annotations

import asyncio

from modules.quant.backend.model_runs import ModelRuns


def test_the_same_day_asked_twice_at_once_is_made_once(monkeypatch):
    calls = []

    async def made(self, identity, date=None):
        calls.append((identity, date))
        await asyncio.sleep(.05)
        return {"date": date}
    runs = object.__new__(ModelRuns)
    monkeypatch.setattr(ModelRuns, "_values", made)

    async def both():
        return await asyncio.gather(runs.values("r1", None), runs.values("r1", None), runs.values("r1", 5), runs.values("r2", None))
    answers = asyncio.run(both())
    assert [answer["date"] for answer in answers] == [None, None, 5, None]
    assert sorted(calls, key=str) == sorted([("r1", None), ("r1", 5), ("r2", None)], key=str)
    assert not runs._values_tasks, "nothing is kept once they are done"
