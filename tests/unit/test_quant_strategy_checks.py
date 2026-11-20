"""The strategy checks: thresholds, neighbours and the grid edge, shuffled trades, the deflated Sharpe, the walk-forward efficiency and the stitching."""
from __future__ import annotations

import math

import pytest

from modules.quant.backend import object_sweeps as S
from modules.quant.backend import strategy_checks as C
from modules.quant.native import metrics as M

# check: (value just passing, value just failing)
EDGES = {"heldout": (0.5, 0.499), "walk": (0.5, 0.499), "neighbours": (0.8, 0.799), "costs": (0.5, 0.499), "shuffle": (2.0, 2.001), "top": (0.5, 0.501), "tries": (0.95, 0.949)}


@pytest.mark.parametrize("check", EDGES)
def test_strategy_check_thresholds(check):
    passing, failing = EDGES[check]
    assert M.strategy_check(check, passing) == "ok" and M.strategy_check(check, failing) == "watch"
    assert M.strategy_check(check, None) == "notrun" and M.strategy_check(check, float("nan")) == "notrun"


def grid(values):
    axes = [{"name": "a", "values": [1, 2, 3]}, {"name": "b", "values": [1, 2, 3]}]
    cases = []
    for row in range(3):
        for col in range(3):
            cases.append({"index": row * 3 + col, "params": {"a": axes[0]["values"][row], "b": axes[1]["values"][col]}, "state": "completed", "metrics": {"sharpe": values[row][col]}})
    return axes, cases


def test_neighbours_of_a_known_matrix():
    axes, cases = grid([[0.5, 0.5, 0.5], [0.5, 1.0, 0.5], [0.5, 0.5, 0.5]])
    centre = C.neighbour_ratio(axes, cases, 4)
    assert centre["total"] == 8 and centre["mean"] == 0.5 and centre["ratio"] == 0.5 and M.strategy_check("neighbours", centre["ratio"]) == "watch"
    assert C.neighbour_ratio(axes, cases, 0)["total"] == 3  # a corner has three neighbours
    assert C.neighbour_ratio(axes, cases, 1)["total"] == 5


def test_one_axis_neighbours_are_left_and_right():
    axes = [{"name": "a", "values": [1, 2, 3]}]
    cases = [{"index": i, "params": {"a": i + 1}, "state": "completed", "metrics": {"sharpe": 1.0 + i}} for i in range(3)]
    assert C.neighbours(axes, cases, 1)["total"] == 2 and C.neighbours(axes, cases, 0)["total"] == 1


def test_neighbour_of_a_negative_cell_is_not_judged():
    axes, cases = grid([[0.5] * 3, [0.5, -1.0, 0.5], [0.5] * 3])
    assert C.neighbour_ratio(axes, cases, 4)["ratio"] is None


def test_edge_is_smallest_or_largest_value_and_a_single_value_has_none():
    axes = [{"name": "a", "values": [1, 2, 3]}, {"name": "b", "values": [7]}, {"name": "@period", "values": [2023, 2024]}]
    assert [item["name"] for item in C.edges(axes, {"a": 1, "b": 7, "@period": 2023})] == ["a"]
    assert C.edges(axes, {"a": 2, "b": 7}) == []
    assert C.edges(axes, {"a": 3, "b": 7})[0]["end"] == "largest"


def test_shuffle_known_sequence():
    shuffled = M.shuffled_drawdown([10, -5, 10, -5], 100)
    assert shuffled["actual"] == pytest.approx(-5 / 110)
    assert shuffled["p95"] == pytest.approx(-0.1, abs=1e-9)
    assert shuffled["p95"] / shuffled["actual"] == pytest.approx(2.2, abs=0.01)
    assert M.strategy_check("shuffle", shuffled["p95"] / shuffled["actual"]) == "watch"
    assert M.shuffled_drawdown([10, -5, 10, -5], 100) == shuffled  # fixed seed


def paper():
    return dict(per_period=2.5 / math.sqrt(250), observations=1250, skew=-3, kurtosis=10)


def test_deflated_sharpe_matches_the_paper():
    assert M.deflated_sharpe(**paper(), tries=100, variance=0.5 / 250) == pytest.approx(0.9004, abs=0.01)


def test_deflated_with_one_try_is_the_probabilistic_sharpe():
    p = paper()
    assert M.deflated_sharpe(**p, tries=1) == pytest.approx(M.probabilistic_sharpe(p["per_period"], p["observations"], p["skew"], p["kurtosis"]))


def test_deflated_never_rises_with_tries():
    values = [M.deflated_sharpe(**paper(), tries=n, variance=0.5 / 250) for n in (1, 2, 5, 20, 100)]
    assert all(b <= a + 1e-12 for a, b in zip(values, values[1:], strict=False))


def test_deflated_helper_uses_the_spread_of_the_runs():
    inputs = {"perPeriod": paper()["per_period"], "observations": 1250, "skew": -3, "kurtosis": 10}
    spread = C.deflated(inputs, [0.5, 2.5, 1.5, -1.0], 4, 250)
    tight = C.deflated(inputs, [2.4, 2.5, 2.6, 2.5], 4, 250)
    assert spread < tight  # a wider spread of tried Sharpes means more luck is expected
    assert C.deflated(None, [1.0], 1, 250) is None


def test_walk_efficiency_just_passes():
    windows = [{"sharpePicked": 1.0, "sharpeTraded": 0.5}, {"sharpePicked": 1.2, "sharpeTraded": 0.6}, {"sharpePicked": 0.8, "sharpeTraded": 0.4}]
    assert S.efficiency(windows) == pytest.approx(0.5) and M.strategy_check("walk", S.efficiency(windows)) == "ok"
    assert S.efficiency([{"sharpePicked": -1.0, "sharpeTraded": 1.0}]) is None
    # A window that placed no trade made nothing out of sample: Sharpe 0, not left out (one window of two traded, at the Sharpe it was picked on).
    assert S.efficiency([{"sharpePicked": 1.0, "sharpeTraded": 1.0, "trades": 9}, {"sharpePicked": 1.0, "sharpeTraded": None, "trades": 0}]) == pytest.approx(0.5)
    assert S.efficiency([{"sharpePicked": 1.0, "sharpeTraded": 1.0, "trades": 9}, {"sharpePicked": 1.0, "sharpeTraded": None, "trades": None}]) == pytest.approx(1.0)


def test_a_walk_picked_on_a_sharpe_at_or_below_zero_fails_rather_than_reading_as_not_run():
    """windows picked on a mean Sharpe of -0.147 and traded at -1.355 (s3, walk #5) have no efficiency to divide out; the walk fails."""
    windows = [{"sharpePicked": value, "sharpeTraded": traded, "trades": 3} for value, traded in
               ((-0.2504, 1.2430), (0.4721, -8.0050), (-0.9103, -0.1619), (-0.2249, 0.1509), (0.1766, None))]
    windows[-1]["trades"] = 0
    state, efficiency, picked = C.walk_outcome(windows)
    assert (state, efficiency) == ("fail", None) and picked == pytest.approx(-0.14738)
    assert C.walk_outcome([{"sharpePicked": None, "sharpeTraded": None, "trades": None}])[0] == "notrun"
    assert C.walk_outcome([{"sharpePicked": 1.0, "sharpeTraded": 0.6, "trades": 4}])[:2] == ("pass", pytest.approx(0.6))


def test_stitched_equity_is_the_product_of_the_windows():
    day = 86_400_000
    parts = [[[0, 100.0], [day, 110.0], [2 * day, 105.0]], [[2 * day, 105.0], [3 * day, 115.5]], [[3 * day, 50.0], [4 * day, 45.0]]]
    stitched = S.stitch(parts)
    assert stitched[-1][1] == pytest.approx((105 / 100) * (115.5 / 105) * (45 / 50), abs=1e-9)
    assert [at for at, _ in stitched] == [0, day, 2 * day, 3 * day, 4 * day]


def test_walk_windows_need_at_least_three():
    first, last = S.months_later(0, 0), S.months_later(0, 10)
    assert len(S.window_spans(first, last, 6, 2)) == 2
    assert len(S.window_spans(first, S.months_later(0, 14), 6, 2)) >= 3
    spans = S.window_spans(first, S.months_later(0, 20), 6, 2)
    assert all(a < b < c <= S.months_later(0, 20) for a, b, c in spans)


def test_every_walk_window_trades_the_whole_stretch():
    """data 2024-11-12 → 2026-11-12, pick on 12 months, trade 3 (s3, walk #5): the windows made a fifth that traded one day (2026-11-12) and,
    with no trade in it, counted as Sharpe 0 in the efficiency's mean. A window trades its whole `trade` months; the tail shorter than that is no
    window. Data that ends on a window's last day (its end is the next midnight) still covers it."""
    from modules.quant.backend.object_data import day_ms
    first = day_ms("2024-11-12", False)
    spans = S.window_spans(first, day_ms("2026-11-12", True), 12, 3)
    assert [(day(b), day(c)) for _, b, c in spans] == [("2025-11-12", "2026-02-12"), ("2026-02-12", "2026-05-12"), ("2026-05-12", "2026-08-12"),
                                                      ("2026-08-12", "2026-11-12")]
    assert len(S.window_spans(first, day_ms("2026-11-11", True), 12, 3)) == 4  # through 11-11 covers the window ending at 11-12 00:00
    assert len(S.window_spans(first, day_ms("2026-11-11", False), 12, 3)) == 4  # a daily equity point stamped at the start of its day
    assert len(S.window_spans(first, day_ms("2026-11-10", True), 12, 3)) == 3   # a day short: the fourth is no window
    for pick, trade in ((6, 2), (12, 3), (3, 1), (24, 5)):
        for found in (S.window_spans(first, day_ms(until, True), pick, trade) for until in ("2026-11-12", "2026-07-31", "2027-01-03")):
            assert all(S.months_later(b, trade) - c <= 86_400_000 for _, b, c in found)


def day(ms):
    from datetime import UTC, datetime
    return datetime.fromtimestamp(ms / 1000, UTC).date().isoformat()


def test_window_without_trades_is_skipped_in_picking():
    day = 86_400_000
    curves = {0: ([[i * day, 100 + i] for i in range(10)], 365, None), 1: ([[i * day, 100 + 2 * i] for i in range(10)], 365, None)}
    span = (0, 9 * day, 12 * day)
    assert S.pick_window(curves, {0: [day], 1: []}, span, "total")[0] == 0
    assert S.pick_window(curves, {0: [], 1: []}, span, "total") is None


def test_a_window_sharpe_takes_the_runs_risk_free_rate():
    # a window's Sharpe was taken without the rate while the run's own took ^IRX; equity 100, 102, 101, 104, rate 0.01% a day, A = 252:
    # returns 0.02, −0.0098039, 0.0297030; above the rate 0.0199, −0.0099039, 0.0296030
    day = 86_400_000
    points = [[i * day, value] for i, value in enumerate([100.0, 102.0, 101.0, 104.0])]
    rate = [[i * day, 0.0001] for i in range(1, 4)]
    excess = [0.02 - 0.0001, 101 / 102 - 1 - 0.0001, 104 / 101 - 1 - 0.0001]
    mean = sum(excess) / 3
    deviation = (sum((item - mean) ** 2 for item in excess) / 2) ** 0.5
    found = S.window_stats(points, 0, 10 ** 18, 252, rate)
    assert found["sharpe"] == pytest.approx(mean / deviation * 252 ** 0.5)
    assert found["total"] == pytest.approx(0.04) and found["maxDrawdown"] == pytest.approx(101 / 102 - 1)
    assert S.window_stats(points, 0, 10 ** 18, 252, None)["sharpe"] is None  # the rate unavailable: no Sharpe, not one against zero


def test_costs_and_capacity_rerun_fill_source(tmp_path):
    """A completed sweep of the cost multiple that started from a run gives that run's Costs doubled check its number; before it, the row says Not run."""
    import asyncio

    from tests.fixtures.strategy.fakes import insert_run, services, strategy

    async def body(ctx, shell):
        obj = await strategy(shell)
        run = insert_run(shell, obj, "single", created=1_000)
        row = {item["id"]: item for item in shell.strategy.run(run["id"])["work"]["checks"]}["costs"]
        assert row["state"] == "notrun" and row["value"] == "Not run" and row["go"] == {"kind": "costs"}
        path = shell.runs.directory(run["id"]) / "output" / "strategy" / "work.json"
        import json
        work = json.loads(path.read_text(encoding="utf-8"))
        work["stats"]["sharpe"] = base = 1.0
        path.write_text(json.dumps(work), encoding="utf-8")
        cases = [{"index": i, "params": {"@costMultiple": m}, "state": "completed", "runId": None, "metrics": {"sharpe": base * f}} for i, (m, f) in enumerate([(0, 1.5), (1, 1.0), (2, 0.6)])]
        insert_run(shell, obj, "single", created=2_000, runKind="sweep", of=run["id"], mode="sweep",
                   sweep={"format": 2, "axes": [{"name": "@costMultiple", "values": [0, 1, 2]}], "metric": "sharpe", "selected": None, "cases": cases})
        row = {item["id"]: item for item in shell.strategy.run(run["id"])["work"]["checks"]}["costs"]
        assert base > 0 and row["state"] == "pass" and row["go"]["kind"] == "open"
    asyncio.run((lambda: _with(tmp_path, body, services))())


def test_neighbours_come_from_a_sweep_the_page_started_from_the_run(tmp_path):
    """The page starts a sweep from the shown run (`of`); that sweep is the one the run's Neighbours and Edge checks read, while the cost reruns are not."""
    import asyncio
    import json

    from tests.fixtures.strategy.fakes import insert_run, services, strategy

    async def body(ctx, shell):
        obj = await strategy(shell)
        run = insert_run(shell, obj, "single", created=1_000, params={"every": 24})
        path = shell.runs.directory(run["id"]) / "output" / "strategy" / "work.json"
        work = json.loads(path.read_text(encoding="utf-8"))
        work["stats"]["sharpe"] = 1.0
        path.write_text(json.dumps(work), encoding="utf-8")
        costs = [{"index": i, "params": {"@costMultiple": m}, "state": "completed", "runId": None, "metrics": {"sharpe": 1.0}} for i, m in enumerate([0, 1, 2])]
        insert_run(shell, obj, "single", created=2_000, runKind="sweep", of=run["id"], mode="sweep",
                   sweep={"format": 2, "axes": [{"name": "@costMultiple", "values": [0, 1, 2]}], "metric": "sharpe", "selected": None, "cases": costs})
        rows = {item["id"]: item for item in shell.strategy.run(run["id"])["work"]["checks"]}
        assert rows["neighbours"]["state"] == "notrun" and rows["neighbours"]["value"] == "Not run"
        assert rows["tries"]["detail"] == "1 try"  # the cost rerun tried nothing new
        cases = [{"index": i, "params": {"every": v}, "state": "completed", "runId": None, "metrics": {"sharpe": s}} for i, (v, s) in enumerate([(12, 0.9), (24, 1.0), (48, 0.95)])]
        insert_run(shell, obj, "single", created=3_000, runKind="sweep", of=run["id"], mode="sweep",
                   sweep={"format": 2, "axes": [{"name": "every", "values": [12, 24, 48]}], "metric": "sharpe", "selected": None, "cases": cases})
        rows = {item["id"]: item for item in shell.strategy.run(run["id"])["work"]["checks"]}
        assert rows["neighbours"]["state"] == "pass" and rows["edge"]["state"] == "pass" and rows["neighbours"]["value"] == "0.93"
        cases = [{"index": i, "params": {"every": v}, "state": "completed", "runId": None, "metrics": {"sharpe": s}} for i, (v, s) in enumerate([(6, 0.9), (12, 0.95), (24, 1.0)])]
        insert_run(shell, obj, "single", created=4_000, runKind="sweep", of=run["id"], mode="sweep",
                   sweep={"format": 2, "axes": [{"name": "every", "values": [6, 12, 24]}], "metric": "sharpe", "selected": None, "cases": cases})
        edge = {item["id"]: item for item in shell.strategy.run(run["id"])["work"]["checks"]}["edge"]
        assert edge["state"] == "fail" and edge["value"] == "largest value" and edge["title"].startswith("every 24 is the largest value tried (6–24) in sweep #4")
    asyncio.run((lambda: _with(tmp_path, body, services))())


async def _with(tmp_path, body, services):
    ctx, records, shell = services(tmp_path)
    try:
        return await body(ctx, shell)
    finally:
        records.close()


def test_results_costs_section_fills_from_the_reruns(tmp_path):
    """Cost sensitivity (none, as set, doubled) and capacity at larger capital appear in the Costs section once the reruns of that run have completed."""
    import asyncio

    from tests.fixtures.strategy.fakes import insert_run, services, strategy

    def sweep(name, values, rows):
        return {"format": 2, "axes": [{"name": name, "values": values}], "metric": "sharpe", "selected": None,
                "cases": [{"index": i, "params": {name: value}, "state": "completed", "runId": None, "metrics": row} for i, (value, row) in enumerate(zip(values, rows, strict=True))]}

    async def body(ctx, shell):
        obj = await strategy(shell)
        run = insert_run(shell, obj, "single", created=1_000)
        before = shell.strategy.results(run["id"])["sections"]["costs"]
        assert "sensitivity" not in before and "capacityFigure" not in before
        row = lambda sharpe: {"sharpe": sharpe, "totalReturn": sharpe / 4, "cagr": sharpe / 5, "maxDrawdown": -0.2}  # noqa: E731
        insert_run(shell, obj, "single", created=2_000, runKind="sweep", of=run["id"], mode="sweep", sweep=sweep("@costMultiple", [0, 1, 2], [row(1.5), row(1.0), row(0.6)]))
        insert_run(shell, obj, "single", created=3_000, runKind="sweep", of=run["id"], mode="sweep", sweep=sweep("@capital", [10_000, 100_000, 1_000_000], [row(1.0), row(0.9), row(0.5)]))
        costs = shell.strategy.results(run["id"])["sections"]["costs"]
        assert [item["costs"] for item in costs["sensitivity"]] == ["None", "As set", "Doubled"] and costs["doubled"]["sharpe"] == 0.6 and costs["doubled"]["sharpeNow"] == 1.0
        assert [item["capital"] for item in costs["capacity"]] == [10_000, 100_000, 1_000_000]
        figure = costs["capacityFigure"]
        # Capital on a log axis (as the draft): the values stay numbers, the axis and the readout format them.
        assert figure["data"][0]["x"] == [10_000, 100_000, 1_000_000] and figure["layout"]["xaxis"]["type"] == "log"
        assert "Capital %{x:,.0f}" in figure["data"][0]["hovertemplate"] and "%{y:+.1f}%" in figure["data"][0]["hovertemplate"]
    asyncio.run((lambda: _with(tmp_path, body, services))())


def test_one_combination_is_one_try_however_many_times_it_ran():
    """(s3, walk #5): a sweep and a walk-forward over the same 15 cells, and two runs that are cells of it, are 15 tries, not 33."""
    import json
    import sqlite3
    from types import SimpleNamespace
    db = sqlite3.connect(":memory:")
    db.execute("CREATE TABLE records (kind TEXT, id TEXT, content TEXT)")
    data = {"symbols": ["BTC"], "timeframe": "1h", "since": "2024-11-13", "until": "2026-11-12"}
    cells = [{"lookback": lookback, "stop_atr": stop} for lookback in (24, 48, 72, 96, 120) for stop in (1, 2, 3)]
    base = {"objectId": "o", "state": "completed", "code": {"digest": "d"}, "data": data, "params": {"lookback": 48, "stop_atr": 2, "risk": 1}}
    grid = {"axes": [{"name": "lookback"}, {"name": "stop_atr"}], "cases": [{"state": "completed", "params": cell, "metrics": {"sharpe": 0.1}} for cell in cells]}
    records = [{**base, "runKind": "sweep", "sweep": grid}, {**base, "runKind": "walk", "sweep": grid},
               {**base, "runKind": "run", "stats": {"sharpe": 0.1}}, {**base, "runKind": "run", "params": {"lookback": 24, "stop_atr": 1, "risk": 1}, "stats": {"sharpe": 0.2}}]
    for number, record in enumerate(records):
        db.execute("INSERT INTO records VALUES ('qrun', ?, ?)", (str(number), json.dumps(record)))
    checks = C.StrategyChecks(SimpleNamespace(records=SimpleNamespace(db=db)))
    count, sharpes = checks.tries("o")
    assert count == 15 and len(sharpes) == 15
    # Another period is another try.
    db.execute("INSERT INTO records VALUES ('qrun', 'x', ?)", (json.dumps({**base, "runKind": "run", "data": {**data, "since": "2025-01-01"}, "stats": {"sharpe": 0.3}}),))
    assert checks.tries("o")[0] == 16
