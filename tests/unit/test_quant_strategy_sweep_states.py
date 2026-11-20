"""The sweep's books balance whatever happens to it: 50 seeds, each a random run of start, stop part-way, Host restart, resume, a combination that fails
and a machine that changes size. At the end: every combination has at most one completed run, completed + failed + stopped + interrupted is the total,
a resume runs only what did not complete (the failed again), nothing is selected unless the user selected it, and the number of tries only grows."""
import random

import pytest

from modules.quant.backend.run_queue import RunQueue
from tests.unit.test_quant_strategy_sweeps import AXES, GB, Runner, scenario, settled, started

SEEDS = range(50)
TERMINAL = {"completed", "failed", "cancelled", "interrupted"}


@pytest.mark.parametrize("seed", SEEDS)
def test_sweep_books_balance(tmp_path, seed):
    rng = random.Random(seed)

    async def body(ctx, shell):
        failing = {rng.randrange(6) for _ in range(rng.randrange(3))}
        runner = Runner(shell, stop_after=rng.randrange(0, 6), fail=lambda params: (params["fast"], params["slow"]) in {(a, b) for a in (5, 10, 20) for b in (30, 60)
                                                                                    if (( (5, 10, 20).index(a)) * 2 + (30, 60).index(b)) in failing})
        cpus = rng.choice([1, 2, 4, 8])
        obj, record = await started(shell, AXES, queue=RunQueue(memory=64 * GB, cpus=cpus))
        record = await settled(shell, record["id"])
        tries, resumed = 0, 0
        for _ in range(rng.randrange(1, 4)):
            cases = shell.runs.read(record["id"])["sweep"]["cases"]
            counted = sum(case["state"] == "completed" for case in cases)
            assert counted >= tries  # the tries only grow
            tries = counted
            unfinished = [case["index"] for case in cases if case["state"] != "completed"]
            if record["state"] not in ("cancelled", "interrupted") or not unfinished:
                break
            if rng.random() < .5:  # the Host restarts: what was stopped counts as interrupted
                shell.runs.update(record["id"], state="interrupted", sweep={**record["sweep"], "cases": [{**c, "state": "interrupted"} if c["state"] == "cancelled" else c for c in cases]})
            runner.stop_after = None
            runner.ran = []
            await shell.sweeps.resume(record["id"])
            record = await settled(shell, record["id"])
            assert sorted(runner.ran) == sorted(set(runner.ran))  # nothing ran twice
            assert set(runner.ran) <= set(unfinished)             # only what was not finished
            resumed += 1
        final = shell.runs.read(record["id"])["sweep"]
        by_state = {state: sum(case["state"] == state for case in final["cases"]) for state in TERMINAL}
        assert sum(by_state.values()) <= len(final["cases"]) == 6 and (by_state["completed"] + by_state["failed"] + by_state["cancelled"] + by_state["interrupted"]
                                                                       == len([c for c in final["cases"] if c["state"] in TERMINAL]))
        completed_runs = [case["runId"] for case in final["cases"] if case["state"] == "completed"]
        assert len(completed_runs) == len(set(completed_runs))
        assert final["selected"] is None
        assert all(case["state"] in TERMINAL for case in final["cases"]) or record["state"] not in TERMINAL
    scenario(tmp_path, body)
