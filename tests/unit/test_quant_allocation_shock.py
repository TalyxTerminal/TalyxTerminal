"""A shock added on Results: the conditional mean (two assets, correlation .5, equal volatility: A falls 10%, B follows by 5%, the portfolio by 7.5%) and the row appearing at
once in a finished run's table, without another run."""
import asyncio
import json
from types import SimpleNamespace

import numpy as np
from talyx_sdk import ContractError

from modules.quant.backend.allocation_runs import AllocationRuns
from modules.quant.native.allocation_shock import shock_row

COV = [[0.04, 0.02], [0.02, 0.04]]


def test_shock_conditional_mean():
    row = shock_row(["A", "B"], {"A": .5, "B": .5}, COV, {}, "A", -10)
    assert row["scenario"] == "A \u221210%" and row["kind"] == "Shock" and row["worst"] == "A"
    assert abs(row["portfolio"] - (-.075)) < 1e-6


def test_shock_on_a_group_moves_its_members_together():
    row = shock_row(["A", "B", "C"], {"A": .2, "B": .3, "C": .5}, np.eye(3) * .01, {"A": "Stocks", "B": "Stocks", "C": "Bonds"}, "Stocks", -20)
    assert abs(row["portfolio"] - (-.2 * .5)) < 1e-9
    assert shock_row(["A"], {"A": 1}, [[1]], {}, "Nope", -5) is None


def runs_with_a_finished_run(tmp_path):
    out = tmp_path / "output"
    (out / "results").mkdir(parents=True)
    (out / "shock-basis.json").write_text(json.dumps({"names": ["A", "B"], "weights": {"A": .5, "B": .5}, "cov": COV, "groups": {"A": "Stocks", "B": "Bonds"}}), encoding="utf-8")
    (out / "results" / "risk.json").write_text(json.dumps({"scenarios": [{"scenario": "Stocks -20%", "kind": "Shock", "portfolio": -.1, "worst": "A"}]}), encoding="utf-8")
    saved = []

    async def update_settings(object_id, patch):
        saved.append(patch)

    runs = object.__new__(AllocationRuns)
    runs.read = lambda identity: {"state": "completed", "objectId": "o1"}
    runs.directory = lambda identity: tmp_path
    runs.store = SimpleNamespace(read=lambda object_id: {"settings": {"params": {"shocks": []}}}, update_settings=update_settings)
    return runs, out, saved


def test_a_shock_shows_in_the_table_at_once_and_is_kept_for_the_next_run(tmp_path, monkeypatch):
    from modules.quant.backend.allocation_routes import handle_allocation_actions
    from tests.unit.quant_public import service_actions
    runs, out, saved = runs_with_a_finished_run(tmp_path)
    act = service_actions(monkeypatch, handle_allocation_actions, SimpleNamespace(allocation=runs))
    row = asyncio.run(act("add_allocation_shock", {"runId":"r1","target":"A","pct":-10}))
    assert abs(row["portfolio"] + .075) < 1e-6
    table = json.loads((out / "results" / "risk.json").read_text(encoding="utf-8"))["scenarios"]
    assert [item["scenario"] for item in table] == ["Stocks -20%", "A \u221210%"]
    assert saved[0]["params"]["shocks"] == [{"target": "A", "pct": -10.0}]
    asyncio.run(runs.add_shock("r1", "A", -10))      # the same shock again adds nothing
    assert len(json.loads((out / "results" / "risk.json").read_text(encoding="utf-8"))["scenarios"]) == 2


def test_shock_refused_for_what_is_not_in_the_pool_or_out_of_range(tmp_path):
    runs, _, _ = runs_with_a_finished_run(tmp_path)
    for target, pct in (("Nope", -10), ("A", 0), ("A", -100)):
        try:
            asyncio.run(runs.add_shock("r1", target, pct))
        except ContractError as error:
            assert error.code == "invalid_input"
        else:
            raise AssertionError((target, pct))


def test_a_group_shock_moves_the_rest_by_their_covariance_with_the_group():
    """The conditional mean of the others given the group's move: Sigma_og Sigma_gg^-1 m. By hand for three assets: A and B fall 20%, C follows."""
    cov = np.array([[0.04, 0.01, 0.006], [0.01, 0.09, 0.012], [0.006, 0.012, 0.01]])
    m = np.array([-0.2, -0.2])
    c_move = float(cov[2, :2] @ np.linalg.solve(cov[:2, :2], m))
    row = shock_row(["A", "B", "C"], {"A": .2, "B": .3, "C": .5}, cov, {"A": "Stocks", "B": "Stocks", "C": "Bonds"}, "Stocks", -20)
    assert abs(row["portfolio"] - (.2 * -.2 + .3 * -.2 + .5 * c_move)) < 1e-12 and abs(c_move) > 1e-3
