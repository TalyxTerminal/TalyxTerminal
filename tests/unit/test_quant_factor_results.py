"""A factor run's Results: built in the background after Work's numbers without holding up the next run, read one tab at a time,
in the shape the public contract declares; every figure says what each line is (meta.role) and which figure it is (meta.key), one set a
run; a figure that cannot be drawn says why in its place. The research process stands in for the engine here; the engine's own
figures are checked on synthetic data without Alphalens (the sections that need it must say so) and, with Alphalens, in
tests/native/test_quant_factor_report.py."""
import asyncio
import json
from pathlib import Path

import numpy as np
import pandas as pd
import pytest
from jsonschema import Draft202012Validator

from tests.fixtures.quant_index import frozen_index
from tests.unit.test_quant_factor_runs import DATA, services, settled

SCHEMA = json.loads((Path(__file__).resolve().parents[2] / "tests" / "fixtures" / "quant" / "factor-results.schema.json").read_text(encoding="utf-8"))  # the shapes frontend/factor/resultTypes.ts reads
ROLES = {"main", "benchmark", "compare", "group", "updown", "attention", "detail"}
TABS = ("returns", "ic", "industry", "style", "turnover", "costs", "event")


def validator(name):
    return Draft202012Validator({"$ref": f"#/$defs/{name}", "$defs": SCHEMA["$defs"]})


def figure_of(key):
    return {"data": [{"type": "scatter", "x": [1], "y": [2], "meta": {"role": "main", "key": "line"}}], "layout": {"meta": {"key": f"factor.results.{key}"}}}


class FakeResults:
    """Writes each tab as the Results process would; `hold` keeps it building until released."""

    def __init__(self, hold=None, fail=False):
        self.hold, self.fail, self.jobs = hold, fail, []

    async def run_results(self, job, directory, *, context, publish=None):
        self.jobs.append(job)
        output = Path(job["output"])
        output.mkdir(parents=True, exist_ok=True)
        if self.fail:
            return {"ok": False, "message": "The Results stopped: MemoryError"}
        for done, name in enumerate(TABS):
            tab = {"tab": name, "holding": 5, "unit": "day", "periods": [1, 5, 10], "periodLabels": ["1D", "5D", "10D"], "missing": {}, "figures": {}}
            if name == "returns":
                tab["figures"] = {"groups": {"key": "factor.results.groups", "title": "Cumulative return by group", "figure": figure_of("groups")},
                                  "violin": {"key": "factor.results.violin", "title": "Return by group, each day", "unavailable": "ValueError: no days"}}
                tab["missing"] = {"violin": "ValueError: no days"}
            (output / f"{name}.json").write_text(json.dumps(tab), encoding="utf-8")
            with open(output / "progress.jsonl", "a", encoding="utf-8") as stream:
                stream.write(json.dumps({"stage": name, "state": "done", "done": done + 1, "total": len(TABS)}) + "\n")
            if self.hold is not None and done == 1:
                await self.hold.wait()
        return {"ok": True, "seconds": 1.5, "tabs": {name: "done" for name in TABS}, "about": {"styles": "Styles v1", "versions": {"alphalens": "0.5.1"}, "seconds": 1.5, "unit": "day", "shares": True}}


def with_values(run_open):
    async def wrapped(job, on_future=None, publish=None):
        answer = await run_open(job, on_future=on_future, publish=publish)
        np.save(Path(job["output"]) / "values.npy", np.zeros((3, 2)))
        (Path(job["output"]) / "ls.json").write_text(json.dumps({"x": ["2024-01-01"], "y": [0.01]}), encoding="utf-8")
        return answer
    return wrapped


def prepared(tmp_path, monkeypatch, rank_ics, fake):
    ctx, shell, factors = services(tmp_path, monkeypatch, rank_ics)
    monkeypatch.setattr(factors.engine, "run_open", with_values(factors.engine.run_open))
    monkeypatch.setattr(factors.engine, "run_results", fake.run_results)
    monkeypatch.setattr(factors, "results_job", lambda identity, cell=None: {"output": str(factors.results_directory(identity, cell)), "settings": {}, "values": "v",
                                                                               "data": {"key": "k", "barsPath": "bars.parquet", "info": {}, "extrasPath": None}})
    return ctx, shell, factors


async def results_settled(factors, identity, cell=None):
    async with asyncio.timeout(10):
        while (identity, cell) in factors.result_tasks:
            await asyncio.sleep(.01)
    return await factors.results(identity, None, (), cell)


def test_results_build_in_the_background_after_work_and_the_next_run_does_not_wait_for_them(tmp_path, monkeypatch):
    async def scenario():
        hold = asyncio.Event()
        fake = FakeResults(hold)
        ctx, shell, factors = prepared(tmp_path, monkeypatch, [.012, .015], fake)
        created = await shell.store.create({"kind": "factor", "name": "Reversal"})
        await shell.store.update_settings(created["id"], {"data": DATA})
        first = await factors.start(created["id"], {"formula": "-($close / Ref($close, 5) - 1)"})
        done = await settled(factors, first["id"])
        assert done["state"] == "completed"
        # Work's numbers are final while the Results are still building: two tabs are done, the rest wait.
        async with asyncio.timeout(10):
            while (await factors.results(first["id"]))["results"]["tabs"].get("ic") != "done":
                await asyncio.sleep(.02)
        building = await factors.results(first["id"], "returns")
        assert building["results"]["state"] == "running" and 0 < building["run"]["resultsProgress"] < 1
        assert building["tab"]["tab"] == "returns" and (await factors.results(first["id"], "event"))["tab"] is None
        validator("View").validate(building)
        # A second run starts and finishes while the first one's Results are still building.
        second = await factors.start(created["id"], {"formula": "-($close / Ref($close, 10) - 1)"})
        assert (await settled(factors, second["id"]))["state"] == "completed"
        hold.set()
        finished = await results_settled(factors, first["id"])
        assert finished["results"]["state"] == "done" and finished["results"]["progress"] == 1 and finished["run"]["resultsProgress"] is None
        assert set(finished["results"]["tabs"].values()) == {"done"}
        record = factors.read(first["id"])
        assert record["styles"]["version"] == "Styles v1" and {item["style"] for item in record["styles"]["definitions"]} >= {"Momentum", "Size", "Value"}
        await results_settled(factors, second["id"])
        for tab in TABS:
            validator("View").validate(await factors.results(first["id"], tab))
    asyncio.run(scenario())


def test_a_figure_that_cannot_be_drawn_says_why_and_other_runs_are_drawn_dashed_beside_the_groups(tmp_path, monkeypatch):
    async def scenario():
        ctx, shell, factors = prepared(tmp_path, monkeypatch, [.012, .015], FakeResults())
        created = await shell.store.create({"kind": "factor", "name": "Reversal"})
        await shell.store.update_settings(created["id"], {"data": DATA})
        first = await factors.start(created["id"], {"formula": "-($close / Ref($close, 5) - 1)"})
        await settled(factors, first["id"])
        second = await factors.start(created["id"], {"formula": "-($close / Ref($close, 10) - 1)"})
        await settled(factors, second["id"])
        await results_settled(factors, second["id"])
        view = await factors.results(second["id"], "returns", (first["id"],))
        figures = view["tab"]["figures"]
        assert figures["violin"]["unavailable"] == "ValueError: no days" and "figure" not in figures["violin"]
        compared = [trace for trace in figures["groups"]["figure"]["data"] if trace["meta"]["role"] == "compare"]
        assert [trace["meta"]["key"] for trace in compared] == ["ls-net:compared:1"]
        validator("View").validate(view)
        lean = await factors.results(second["id"], "returns", (), None, figures=False)
        assert "figure" not in lean["tab"]["figures"]["groups"] and lean["tab"]["figures"]["violin"]["unavailable"]
    asyncio.run(scenario())


def test_a_newer_run_puts_aside_waiting_results_which_build_when_asked_and_a_held_out_run_has_none(tmp_path, monkeypatch):
    async def scenario():
        fake = FakeResults()
        ctx, shell, factors = prepared(tmp_path, monkeypatch, [.012, .015, .02], fake)
        factors.queue.cpus = 0  # background work waits: nothing takes a background slot
        created = await shell.store.create({"kind": "factor", "name": "Reversal"})
        await shell.store.update_settings(created["id"], {"data": {**DATA, "heldOutFrom": "2024-06-01"}})
        first = await factors.start(created["id"], {"formula": "-($close / Ref($close, 5) - 1)"})
        await settled(factors, first["id"])
        assert (await factors.results(first["id"]))["results"]["state"] == "queued"
        second = await factors.start(created["id"], {"formula": "-($close / Ref($close, 10) - 1)"})
        await settled(factors, second["id"])
        put_aside = await results_settled(factors, first["id"])
        assert put_aside["results"]["state"] == "notbuilt" and "newer run" in put_aside["results"]["message"]
        assert (await factors.results(second["id"]))["results"]["state"] == "queued"
        factors.queue.cpus = 2
        async with factors.queue.changed:
            factors.queue.changed.notify_all()
        assert (await results_settled(factors, second["id"]))["results"]["state"] == "done"
        await factors.build_results(first["id"])
        assert (await results_settled(factors, first["id"]))["results"]["state"] == "done"
        held = await factors.start(created["id"], {"kind": "heldout", "of": second["id"]})
        await settled(factors, held["id"])
        assert (await factors.results(held["id"]))["results"]["state"] == "none"
        from talyx_sdk import ContractError
        with pytest.raises(ContractError):
            await factors.build_results(held["id"])
    asyncio.run(scenario())


def test_results_that_fail_say_why_and_ones_cut_short_by_closing_read_as_interrupted(tmp_path, monkeypatch):
    async def scenario():
        ctx, shell, factors = prepared(tmp_path, monkeypatch, [.012], FakeResults(fail=True))
        created = await shell.store.create({"kind": "factor", "name": "Reversal"})
        await shell.store.update_settings(created["id"], {"data": DATA})
        first = await factors.start(created["id"], {"formula": "-($close / Ref($close, 5) - 1)"})
        await settled(factors, first["id"])
        failed = await results_settled(factors, first["id"])
        assert failed["results"]["state"] == "failed" and "MemoryError" in failed["results"]["message"]
        record = factors.read(first["id"])
        factors.save(record, results={"state": "running", "progress": .4, "tabs": {}})
        assert (await factors.results(first["id"]))["results"]["state"] == "interrupted"
    asyncio.run(scenario())


# ---------------------------------------------------------------------------------------------------------------- the engine's figures
def panel(days=420, names=60, industry=True, seed=4):
    from modules.quant.native import factor_work as W
    rng = np.random.default_rng(seed)
    labels = pd.bdate_range("2019-01-01", periods=days).tz_localize("UTC").as_unit("ns").asi8 // 1_000_000
    close = np.exp(np.cumsum(rng.normal(0, .02, (days, names)), axis=0)) * 50
    fields = {"open": close, "high": close * 1.01, "low": close * .99, "close": close, "volume": rng.uniform(1e5, 1e6, (days, names))}
    fields.update(rawClose=fields["close"], rawVolume=fields["volume"])
    return W.Panel(labels, [f"S{i}" for i in range(names)], fields, np.ones((days, names), dtype=bool), {"annualization": 252, "timeframe": "1d"},
                   industry=[f"I{i % 7}" for i in range(names)] if industry else None, shares=np.full((days, names), 1e6) if industry else None)


def traces_of(tab):
    for item in tab["figures"].values():
        if "figure" in item:
            yield item["figure"]


@pytest.mark.parametrize("industry", [True, False])
def test_every_figure_of_the_results_says_what_it_is_and_what_alphalens_did_not_give_says_why(industry):
    from modules.quant.native import factor_results as R
    data = panel(industry=industry)
    raw = -(data.fields["close"] / np.vstack([np.full((5, data.shape[1]), np.nan), data.fields["close"][:-5]]) - 1)
    # Without an industry a run can only take nothing out (the industry settings stop it).
    settings = {} if industry else {"neutralize": "none"}
    results = R.Results({"settings": settings, "formula": True, "similar": [{"name": "Momentum 12-1", "version": "v3", "rho": .81}]}, data, raw)
    keys = set()
    for name in R.TABS:
        tab = results.build(name)
        validator({"returns": "ReturnsTab", "ic": "IcTab", "industry": "IndustryTab", "style": "StyleTab", "turnover": "TurnoverTab",
                   "costs": "CostsTab", "event": "EventTab"}[name]).validate(json.loads(json.dumps(tab)))
        for key, item in tab["figures"].items():
            # Each figure is drawn, or says why not, in its place.
            assert ("figure" in item) != ("unavailable" in item), (name, key)
            assert item["key"].startswith("factor.results.") and item["key"] not in keys, item["key"]
            keys.add(item["key"])
        for made in traces_of(tab):
            assert made["layout"]["meta"]["key"]
            for trace in made["data"]:
                assert trace["meta"]["role"] in ROLES and trace["meta"]["key"], (name, trace.get("name"))
    # One set a run: every figure of every tab has its own identity.
    assert len(keys) >= 17
    if not industry:
        tab = results.build("industry")
        assert "no industry" in tab["figures"]["sectors"]["unavailable"]
    tab = results.build("style")
    assert tab["version"] == "Styles v1" and not any(row.get("unavailable") for row in tab["styles"])  # only what can be measured is a row
    # A 5-day reversal shares about √(5/21) of the 21-day reversal style.
    assert next(row for row in tab["styles"] if row["style"] == "Reversal")["rho"] > .3
    over = next(trace for trace in tab["figures"]["like"]["figure"]["data"] if trace["meta"]["key"] == "over-limit")
    assert over["meta"]["role"] == "attention"  # a library factor above the 0.70 limit is the one to deal with


def test_the_cost_split_adds_up_to_the_net_and_the_holdings_include_the_run(tmp_path):
    from modules.quant.native import factor_results as R
    data = panel()
    raw = -(data.fields["close"] / np.vstack([np.full((5, data.shape[1]), np.nan), data.fields["close"][:-5]]) - 1)
    results = R.Results({"settings": {"costBps": 8}, "formula": True}, data, raw)
    tab = results.build("costs")
    split = tab["split"]
    assert split["gross"] - split["tradingLong"] - split["tradingShort"] - split["borrow"] == pytest.approx(split["net"], abs=1e-5)
    run = next(row for row in tab["holdings"] if row["holding"] == 5)
    assert run["net"] == pytest.approx(results.ev.numbers["longShortNet"], abs=1e-6)
    assert [row["holding"] for row in tab["holdings"]] == [1, 2, 3, 5, 10, 20]


# ---------------------------------------------------------------------------------------------------------------- F: the report at the draft's level
def built(name, raw_of=None, settings=None, **kwargs):
    from modules.quant.native import factor_results as R
    data = panel(**kwargs)
    close = data.fields["close"]
    raw = -(close / np.vstack([np.full((5, data.shape[1]), np.nan), close[:-5]]) - 1) if raw_of is None else raw_of(close)
    results = R.Results({"settings": settings or {}, "formula": True, "similar": []}, data, raw)
    return results.build(name), results


def drawn(tab):
    return {key: item["figure"] for key, item in tab["figures"].items() if "figure" in item}


def all_drawn():
    out = {}
    for name in ("returns", "ic", "industry", "style", "turnover", "costs", "event"):
        tab, _ = built(name)
        out.update({figure["layout"]["meta"]["key"]: figure for figure in drawn(tab).values()})
    return out


def test_signed_figures_are_updown():
    figures = all_drawn()
    for key in ("factor.results.ic-years", "factor.results.ic-by-sector"):
        bars = [trace for trace in figures[key]["data"] if trace["type"] == "bar"]
        assert bars and all(trace["meta"]["role"] == "updown" for trace in bars), key  # up and down by the sign of each value, the user's colours


def test_the_yearly_ic_marks_the_whole_period_mean():
    figure = all_drawn()["factor.results.ic-years"]
    marks = [trace for trace in figure["data"] if trace["meta"]["key"] == "whole-period"]
    texts = [item.get("text") for item in figure["layout"].get("annotations", [])]
    assert len(marks) == 1 and marks[0]["meta"]["role"] == "benchmark" and "whole period" in texts


def test_categories_of_a_horizontal_bar_are_on_the_left_and_no_figure_carries_an_axis_title():
    for key, figure in all_drawn().items():
        layout = figure["layout"]
        if any(trace["type"] == "bar" and trace.get("orientation") == "h" for trace in figure["data"]):
            assert layout["yaxis"].get("side") == "left", key
        for axis in ("xaxis", "yaxis"):
            title = (layout.get(axis) or {}).get("title")
            assert not (title.get("text") if isinstance(title, dict) else title), (key, axis)  # the unit is in the section's subtitle


def test_every_figure_reads_out_like_the_draft():
    from tests.unit.figure_reading import READOUT, check_reading
    figures = all_drawn()
    problems = [problem for key, figure in figures.items() if key in READOUT for problem in check_reading(key, figure)]
    assert not problems, "\n".join(problems)
    assert {"factor.results.ic-years", "factor.results.ic-by-sector", "factor.results.decay-neutral", "factor.results.like", "factor.results.net-by-cost",
            "factor.results.event", "factor.results.groups"} <= set(figures)


def test_style_table_lists_only_styles_it_can_measure():
    tab, _ = built("style")
    rows = tab["styles"]
    assert rows and not any(row.get("unavailable") for row in rows)
    assert {row["style"] for row in rows} == {item["style"] for item in __import__("modules.quant.native.styles", fromlist=["x"]).definitions() if item["family"] == "price"}
    assert not any(row["family"] == "financial" for row in rows)
    assert "Value" not in json.dumps(tab["missing"]) and "Market has not" not in json.dumps(tab)


def cost_texts(figure):
    return [item.get("text") for item in figure["layout"].get("annotations", [])] + [trace.get("text") for trace in figure["data"] if isinstance(trace.get("text"), str)]


def test_cost_chart_marks_the_assumed_cost_and_the_break_even():
    def ahead(close):  # the factor knows the next days' returns: costs have room
        return np.vstack([close[5:] / close[:-5] - 1, np.full((5, close.shape[1]), np.nan)])
    tab, _ = built("costs", ahead)
    figure = tab["figures"]["byCost"]["figure"]
    even = tab["split"]["breakEven"]
    assert even > 0
    texts = cost_texts(figure)
    assert "assumed" in texts and f"break-even {even:.1f}" in texts
    points = [trace for trace in figure["data"] if trace["meta"]["key"] == "break-even"]
    assert len(points) == 1 and points[0]["x"] == [pytest.approx(even, abs=.05)]  # at the cost where the net reaches zero


def test_break_even_below_zero_draws_no_point_and_says_it_loses_before_costs():
    def against(close):  # the factor is wrong about the next days: the long-short loses before any cost
        return -np.vstack([close[5:] / close[:-5] - 1, np.full((5, close.shape[1]), np.nan)])
    tab, _ = built("costs", against)
    figure = tab["figures"]["byCost"]["figure"]
    assert tab["split"]["breakEven"] < 0
    assert "loses before costs" in cost_texts(figure) and not any(trace["meta"]["key"] == "break-even" for trace in figure["data"])
    assert not any(str(text).startswith("break-even") for text in cost_texts(figure))


# What each tab of the page shows: its tables (with the name its reason is filed under) and its figures. The page writes no sentence of its own
# when one is missing: the reason always comes from here (Results.tsx `Why`).
PAGE_TABLES = {"returns": {"groupStats": "groupStats", "returnsByPeriod": "returnsByPeriod"}, "ic": {"information": "information", "years": "years"},
               "industry": {"sizeAndPeriod": "sizeAndPeriod", "decay": "decay", "method": "method"}, "style": {"styles": "styles", "residual": "residual"},
               "turnover": {"turnover": "turnover"}, "costs": {"split": "split", "holdings": "holdings"}, "event": {"table": "event"}}
PAGE_FIGURES = {"returns": ["groups", "meanByGroup", "violin", "factorWeighted", "spread"], "ic": ["daily", "histogram", "qq", "years", "monthly"],
                "industry": ["sectors", "decay"], "style": ["like"], "turnover": ["topBottom", "autocorrelation"], "costs": ["byCost"], "event": ["event"]}


@pytest.mark.parametrize("shape", [{}, {"industry": False}, {"names": 6, "days": 90}])
def test_every_table_and_figure_is_there_or_says_why(shape):
    """Whatever the data, every table and figure the page has a place for is either made or carries the reason it is not: nothing is left to the page to guess."""
    from modules.quant.native import factor_results as R
    data = panel(**shape)
    close = data.fields["close"]
    raw = -(close / np.vstack([np.full((5, data.shape[1]), np.nan), close[:-5]]) - 1)
    results = R.Results({"settings": {} if shape.get("industry", True) else {"neutralize": "none"}, "formula": True, "similar": []}, data, raw)
    for name in R.TABS:
        tab = results.build(name)
        for table, filed in PAGE_TABLES[name].items():
            assert tab.get(table) is not None or tab["missing"].get(filed), (shape, name, table)
        for key in PAGE_FIGURES[name]:
            item = tab["figures"].get(key)
            assert item is not None and (item.get("figure") or item.get("unavailable")), (shape, name, key)
        if tab.get("residual") is not None:
            assert tab["residual"]["rankIc"] is not None  # no rank IC after the styles is a reason, not an empty row


def test_every_work_figure_is_marked_and_keys_only_what_the_draft_keys(tmp_path):
    """Work's charts (groups, IC by holding days, held-out) carry what each line is and which figure it is, so the shared layer paints them; the decay and held-out
    charts give no key (the run's bar names its holding on top, the held-out stretch is labelled on the chart), and every line reads out on hover."""
    from modules.quant.native import factor_resident as FR
    from modules.quant.native import factor_work as W
    data = panel()
    close = data.fields["close"]
    raw = -(close / np.vstack([np.full((5, data.shape[1]), np.nan), close[:-5]]) - 1)
    settings = W.settings_of({})
    evaluation = W.Evaluation(data, raw, settings)
    figures = evaluation.figures(compared=evaluation.ls_path(), compared_label="#3")
    start = int(data.labels[300])
    held = FR.held_out({"formula": "x", "inSample": {"ls": evaluation.ls_path()}}, data, raw, settings, start, tmp_path)["figures"]
    for key, figure in {**figures, **held}.items():
        assert figure["layout"]["meta"]["key"], key
        for trace in figure["data"]:
            assert trace["meta"]["role"] in ROLES and trace["meta"]["key"], (key, trace.get("name"))
            assert trace.get("hovertemplate"), (key, trace.get("name"))
    for key in ("decay", "heldout"):
        assert all(trace.get("showlegend") is False for trace in {**figures, **held}[key]["data"]), key


def test_the_results_job_leaves_the_price_styles_to_the_style_tab(tmp_path):
    """Work compares a run with the library and the price styles; the Results' library list takes only the library factors, or the Style tab lists each
    style twice and holds a style to the library limit."""
    from types import SimpleNamespace

    from modules.quant.backend.factor_runs import FactorRuns
    run, data = tmp_path / "run", tmp_path / "data"
    (run / "output").mkdir(parents=True)
    data.mkdir()
    (data / "bars.parquet").write_bytes(b"")
    (data / "info.json").write_text("{}", encoding="utf-8")
    (run / "output" / "work.json").write_text(json.dumps({"similar": [
        {"name": "Reversal", "version": "Style", "rho": .87, "style": True}, {"name": "Momentum 12-1", "version": "v3", "rho": .2}]}), encoding="utf-8")
    owner = SimpleNamespace(read=lambda _: {"settings": {"run": {}}, "data": {"key": "k"}, "mode": "formula"}, directory=lambda _: run,
                            data=SimpleNamespace(directory=lambda _: data), results_directory=lambda *_: tmp_path / "results")
    job = FactorRuns.results_job(owner, "run")
    assert [item["name"] for item in job["similar"]] == ["Momentum 12-1"]


@pytest.mark.parametrize("seed", [0, 1, 2])
def test_ranks_from_a_kept_sort_order_are_the_same_as_ranking_again(seed):
    """The quick check sorts a run's values once and keeps the styles' and each holding's returns' sort orders with the data; the rank correlation
    from them is exactly the one from ranking each pair again, ties (rounded or clipped values) and missing cells included."""
    from modules.quant.native import factor_work as W
    from modules.quant.native import metrics as M
    rng = np.random.default_rng(seed)
    a = np.round(rng.normal(size=(200, 60)), 1)  # many ties
    a[rng.random(a.shape) < .15] = np.nan
    b = np.clip(rng.normal(size=(200, 60)), -1, 1)  # ties at the clip
    b[rng.random(b.shape) < .3] = np.nan
    c = rng.normal(size=(200, 60))  # no ties: the runs of equal values are not kept
    for left, right in ((a, b), (b, c), (c, a)):
        want = M.rank_correlation(left, right, 10)
        got = M.rank_correlation_on(left, M.RankBasis(left), right, M.RankBasis(right), 10)
        assert np.array_equal(np.isnan(want), np.isnan(got)) and np.nanmax(np.abs(want - got)) < 1e-12
    assert M.RankBasis(c).before is None and M.RankBasis(a).before is not None
    # Through the evaluation: the numbers are those of ranking again, the second run reuses what the first kept.
    data = panel()
    close = data.fields["close"]
    raw = -(close / np.vstack([np.full((5, data.shape[1]), np.nan), close[:-5]]) - 1)
    evaluation = W.Evaluation(data, raw, W.settings_of({}))
    for holding in (1, 5, 20):
        plain = M.mean(M.rank_correlation(evaluation.after, M.forward_returns(close, holding, 1), W.WINDOW_NAMES))
        assert evaluation.decay[holding - 1] == pytest.approx(plain, abs=1e-12)
    assert ("forward-rank-basis", 5, 1) in data._cache


def test_every_page_of_a_run_reads_one_ic_sample_each_period_its_own_names():
    """a name whose 10-day return is missing (it stopped trading) still counts in the 1-day IC that day, as in Work; the holding's
    column is Work's series itself, so Results, Costs, Industry and Work give one number."""
    from modules.quant.native import factor_results as R
    from modules.quant.native import factor_work as W
    data = panel(industry=False)
    data.fields["close"][-30:, :5] = np.nan   # five names stop trading 30 days before the end
    raw = -(data.fields["close"] / np.vstack([np.full((5, data.shape[1]), np.nan), data.fields["close"][:-5]]) - 1)
    results = R.Results({"settings": {"neutralize": "none"}, "formula": True}, data, raw)
    ic = results.ic()
    h = results.h
    assert np.allclose(ic[results.column(h)].to_numpy(), pd.Series(results.ev.ic, index=data.dates).loc[ic.index].to_numpy(), equal_nan=True)
    one = W.rank_ic(results.ev.after, data.fields["close"], 1, results.delay)[0]
    assert np.allclose(ic[results.column(1)].to_numpy(), pd.Series(one, index=data.dates).loc[ic.index].to_numpy(), equal_nan=True)
    table = results.build("ic")["information"]
    assert next(row for row in table if row["period"] == h)["mean"] == pytest.approx(np.nanmean(results.ev.ic), abs=1e-6)


def test_thresholds_in_days_count_days_on_hourly_bars_too():
    """"Listed under 60 days" left a crypto name out for 60 hourly bars, two and a half days; the dollar-volume windows of the liquidity
    cut and the size stand-in were 20 and 30 bars. They count days, whatever the bar."""
    from modules.quant.native import factor_work as W
    hours, names = 24 * 90, 3
    labels = (pd.Timestamp("2024-01-01", tz="UTC").value // 1_000_000) + np.arange(hours) * 3_600_000
    close = np.full((hours, names), 100.0)
    close[: 24 * 10, 2] = np.nan  # listed on day 11
    fields = {"open": close, "high": close, "low": close, "close": close, "volume": np.full((hours, names), 10.0)}
    fields.update(rawClose=fields["close"], rawVolume=fields["volume"])
    hourly = W.Panel(labels, ["A", "B", "C"], fields, np.ones((hours, names), dtype=bool), {"annualization": 365 * 24, "timeframe": "1h"})
    assert hourly.bars_a_day == 24
    reason = W.eligibility(hourly, {"newListings": True, "halted": True})
    young = np.flatnonzero(reason[:, 2] == 3)
    assert young[0] == 24 * 10 and len(young) == 24 * 60 - 1  # until its 60th day of hourly prices (as 59 daily bars before)
    assert panel().bars_a_day == 1


def test_the_event_study_follows_raw_demeaned_and_group_neutral():
    """the event study read returns relative to all names whatever the settings; Raw and Demeaned gave the same numbers."""
    from modules.quant.native import factor_results as R
    data = panel(days=300, names=42)
    industry = np.arange(42) % 7
    drift = 1 + .001 + .0005 * industry  # every name rises (Raw is not Demeaned) and each industry at its own pace (Group neutral is not Demeaned)
    data.fields["close"] = data.fields["close"] * np.cumprod(np.tile(drift, (300, 1)), axis=0)
    data.fields["rawClose"] = data.fields["close"]
    raw = (industry[None, :] + np.random.default_rng(1).normal(0, 1, data.shape)).astype(float)  # values that lean on the industry
    paths = {}
    for name, settings in {"raw": {"demeaned": False}, "all": {}, "group": {"groupAdjust": True}}.items():
        tab = R.Results({"settings": {"neutralize": "none", **settings}, "formula": True, "similar": []}, data, raw).build("event")
        assert tab["basis"] == name
        paths[name] = np.array([row["cumulative"] for row in tab["event"]], dtype=float)
    after = R.EVENT_BEFORE + 10
    # Raw is Demeaned plus the mean of all names, the same for every group.
    lift = paths["raw"][:, after] - paths["all"][:, after]
    assert np.all(lift > 0) and np.ptp(lift) < 1e-9
    # Group neutral takes off each name's own industry: a top group full of the fast industries no longer looks better for it.
    assert paths["all"][-1, after] - paths["all"][0, after] > .005
    assert abs(paths["group"][-1, after] - paths["group"][0, after]) < .5 * (paths["all"][-1, after] - paths["all"][0, after])


def test_the_event_tables_days_before_are_the_move_into_the_signal():
    """the table's 'before' column was the path at -5 days, which is minus the move over the five days into the signal: names that
    rose into it read as falling. On Raw with every name rising 0.1% a day or more, each group rose over those days."""
    from modules.quant.native import factor_results as R
    data = panel(days=300, names=42)
    industry = np.arange(42) % 7
    data.fields["close"] = data.fields["close"] * np.cumprod(np.tile(1 + .001 + .0005 * industry, (300, 1)), axis=0)
    data.fields["rawClose"] = data.fields["close"]
    raw = (industry[None, :] + np.random.default_rng(1).normal(0, 1, data.shape)).astype(float)
    tab = R.Results({"settings": {"neutralize": "none", "demeaned": False}, "formula": True, "similar": []}, data, raw).build("event")
    for row, path in zip(tab["table"], tab["event"], strict=True):
        assert row["before"] == pytest.approx(-path["cumulative"][0], abs=1e-7) and row["before"] > 0


def test_work_group_lines_follow_group_neutral():
    """Work's group lines were relative to all names under Group neutral too."""
    from modules.quant.native import factor_work as W
    data = panel(days=200, names=14)
    industry = np.arange(14) % 7
    returns = np.zeros(data.shape)
    returns[:, :] = .001 * industry[None, :]  # each industry its own pace, nothing else
    data._derived["returns"] = returns
    values = np.tile(industry.astype(float), (200, 1))
    groups = np.tile(np.where(industry >= 4, 2, 1), (200, 1))
    settings = {"holding": 1, "delay": 1, "costBps": 0, "borrowPct": 0}
    plain = W.returns_of(data, groups, 2, {**settings, "demeaned": True}, values)
    neutral = W.returns_of(data, groups, 2, {**settings, "demeaned": True, "groupAdjust": True}, values, codes=industry)
    assert np.nanmean(plain["relative"][:, 1]) > .001
    assert np.nanmax(np.abs(neutral["relative"])) < 1e-12  # every name moved exactly with its industry


def test_a_name_halted_for_long_carries_the_move_over_the_halt_into_its_return():
    """after more than 5 bars without a price the move over the halt was lost: the name earned nothing on the day it came back.
    A held name earns nothing while halted and the whole move on its first bar back; after its last price (delisted) there is no return."""
    from modules.quant.native import factor_work as W
    data = panel(days=40, names=3)
    close = np.full((40, 3), 100.0)
    close[10:22, 0] = np.nan  # halted 12 bars
    close[22:, 0] = 150.0
    close[30:, 1] = np.nan  # last price on bar 29
    data.fields["close"] = close
    data._derived.clear()
    returns = W.Panel.derived(data, "returns")
    assert returns[22, 0] == pytest.approx(0.5)
    assert np.all(returns[11:22, 0] == 0)
    assert np.all(np.isnan(returns[30:, 1]))


def test_a_library_value_enters_the_formula_at_the_first_bar_known_after_it_was_available():
    """`$lib.name` put each value on its own bar whatever its availableAt, so a value filled in late (TALYX was closed, the source gave
    it late) read as known at that bar's close. A value enters at the first bar at or after its own whose close is known when the value is;
    a newer value on the same bar wins over an older late one."""
    from modules.quant.native import factor_work as W
    day = 86_400_000
    labels = np.arange(6) * day
    clocks = np.tile((labels + day // 2)[:, None], (1, 2)).astype(float)  # each bar known at noon of its day
    clocks[3, 1] = np.nan  # B has no bar on day 3
    rows = pd.DataFrame([
        ("A", 0, day // 2, 1.0),             # on time
        ("A", day, 3 * day + day // 2 + 1, 2.0),  # day 1's value known only after day 3's close: first on day 4
        ("A", 2 * day, 2 * day + day // 2, 3.0),
        ("A", 4 * day, 4 * day + day // 2, 5.0),  # day 4's own value wins over day 1's late one
        ("B", 2 * day, 3 * day, 7.0),         # known after day 2's close; B has no bar on day 3, so day 4
        ("B", 5 * day, 9 * day, 8.0),         # known after the data ends: never
    ], columns=["column", "label", "availableAt", "value"])
    table = W.library_table(labels, ["A", "B"], rows, clocks)
    expected = np.full((6, 2), np.nan)
    expected[0, 0], expected[2, 0], expected[4, 0] = 1.0, 3.0, 5.0
    expected[4, 1] = 7.0
    np.testing.assert_array_equal(table, expected)
    late_only = W.library_table(labels, ["A", "B"], rows[rows["value"] == 2.0], clocks)
    assert np.flatnonzero(np.isfinite(late_only[:, 0])).tolist() == [4] and late_only[4, 0] == 2.0


def test_names_a_day_stands_beside_the_members_the_index_held_that_day():
    """how many index members a day had no value showed only in a folded note. The members a day count every member the index
    held, those without a Market instrument or prices too, so 'Names a day' reads against them."""
    from modules.quant.native import factor_work as W
    day = 86_400_000
    labels = np.arange(10) * day
    member = lambda asset, instrument=True: {"assetId": asset, "instrumentId": asset if instrument else None}
    a, b, c, d = member("a"), member("b"), member("c", False), member("d", False)
    index = frozen_index({"intervals": [
        {"since": 0, "until": 4 * day + day - 1, "members": [a, b, c, d]},
        {"since": 5 * day, "until": 7 * day + day - 1, "members": [a, b, c]},
    ]})
    assert W.members_a_day(labels, index).tolist() == [4, 4, 4, 4, 4, 3, 3, 3, 0, 0]
    assert W.members_a_day(labels, None) is None
    data = panel(days=120, names=8)
    data.info = {**data.info, "index": frozen_index({"intervals": [{"since": int(data.labels[0]), "until": int(data.labels[-1]),
                                                       "members": [member(str(n)) for n in range(8)] + [member(f"x{n}", False) for n in range(5)]}]})}
    raw = np.random.default_rng(3).normal(0, 1, data.shape)
    numbers = W.Evaluation(data, raw, W.settings_of({"neutralize": "none"}), formula=True).numbers
    assert numbers["members"] == 13 and numbers["names"] <= 8


def test_a_setting_that_needs_an_industry_stops_the_run_when_market_gives_none():
    """neutralizing by industry without an industry did nothing, and grouping inside each industry fell back to the whole pool,
    both without a word: the run read as done the way it was set. They stop, naming the setting, as Group neutral does."""
    from modules.quant.native import factor_work as W
    data = panel(days=60, names=12, industry=False)
    raw = np.random.default_rng(2).normal(0, 1, data.shape)
    reason = np.zeros(data.shape, dtype=np.int8)
    with pytest.raises(ValueError, match="Neutralize Industry needs an industry"):
        W.preprocess(raw, data, W.settings_of({"neutralize": "industry"}), reason)
    # Industry + size took out size alone without a word; it stops the same way, naming its own setting.
    with pytest.raises(ValueError, match=r"Neutralize Industry \+ size needs an industry"):
        W.preprocess(raw, data, W.settings_of({"neutralize": "industry+size"}), reason)
    with pytest.raises(ValueError, match="Grouping inside each industry needs an industry"):
        W.group_of(raw, W.settings_of({"grouping": {"withinIndustry": True}}), None)
    W.preprocess(raw, data, W.settings_of({"neutralize": "none"}), reason)
    assert W.group_of(raw, W.settings_of({}), None).max() == 5
    # The run stops before computing, and what it shows names the setting and what to set (it showed the engine's internal ValueError).
    from modules.quant.native import factor_resident as engine
    with pytest.raises(engine.JobError) as stopped:
        engine.industry_settings(data, W.settings_of({"neutralize": "industry+size", "grouping": {"withinIndustry": True}}))
    assert stopped.value.failure["message"] == ("Neutralize is Industry + size; Group within each industry is on, which needs an industry for each name, "
                                                "and Market gives none for these names. Set Neutralize to None and turn it off.")
    engine.industry_settings(data, W.settings_of({"neutralize": "none"}))
    engine.industry_settings(panel(days=60, names=12), W.settings_of({"neutralize": "industry", "grouping": {"withinIndustry": True}}))


def test_a_years_sharpe_is_the_long_short_books_own_with_no_rate_off():
    """design.md: the long-short book pays for itself, so its Sharpe takes no rate off, a year's as the whole run's. A year's Sharpe on the
    IC page is mean ÷ sample standard deviation × √A of that year's net returns (it took the run's T-bill or fixed rate off before)."""
    from modules.quant.native import factor_results as R
    data = panel(days=520)
    raw = -(data.fields["close"] / np.vstack([np.full((5, data.shape[1]), np.nan), data.fields["close"][:-5]]) - 1)
    results = R.Results({"settings": {}, "formula": True}, data, raw)
    net = results.ev.returns["net"]
    rows = results.years()
    assert len(rows) >= 2
    for row in rows:
        inside = net[(data.dates.year == row["year"]) & np.isfinite(net)]
        assert row["sharpe"] == pytest.approx(inside.mean() / inside.std(ddof=1) * np.sqrt(252), abs=1e-4)


def test_each_sectors_ic_carries_its_t_and_days():
    """Inside each sector had only bars. Each sector's row carries its days with an IC and the Newey-West t of that daily IC
    (lag holding - 1), the daily IC being each day's Spearman inside the sector, computed here one day at a time."""
    from modules.quant.native import factor_results as R
    from modules.quant.native import metrics as M
    data = panel(days=160, names=30)
    raw = -(data.fields["close"] / np.vstack([np.full((5, data.shape[1]), np.nan), data.fields["close"][:-5]]) - 1)
    results = R.Results({"settings": {"neutralize": "none"}, "formula": True, "similar": []}, data, raw)
    rows = {row["sector"]: row for row in results.build("industry")["sectors"]}
    frame, column = results.factor_data(), results.column(results.h)
    for sector, inside in frame.groupby("group"):
        daily = inside.groupby(level="date").apply(lambda day: day["factor"].rank().corr(day[column].rank()) if len(day) > 1 else np.nan).sort_index()
        row = rows[str(sector)]
        assert row["days"] == int(daily.notna().sum())
        assert row["t"] == pytest.approx(M.newey_west_t(daily.to_numpy(), results.h - 1), abs=1e-3)


def test_how_industry_was_done_says_only_the_notes_about_industry():
    """the Industry page's How it was done -> Industry carried every note of the data, the index's missing members among them."""
    from modules.quant.native import factor_results as R
    data = panel(days=160, names=30)
    data.notes = ["Industry is the source's current classification applied to the whole period.",
                  "No industry or shares outstanding for FISV: yfinance has no such data (HTTP 404)",
                  "Index members: 157 members over the period have no Market instrument and are left out"]
    raw = -(data.fields["close"] / np.vstack([np.full((5, data.shape[1]), np.nan), data.fields["close"][:-5]]) - 1)
    method = R.Results({"settings": {"neutralize": "none"}, "formula": True, "similar": []}, data, raw).build("industry")["method"]
    assert method["industry"] == " ".join(data.notes[:2]) and method["notes"] == data.notes


def test_a_sources_missing_facts_are_one_note_naming_the_names():
    """each name's fact problem was its own note, the source's message alone (once the raw response); one note per reason names them."""
    from modules.quant.backend.factor_data import _fact_note, fact_notes
    rows = [{"ticker": f"T{i}"} for i in range(8)]
    for row in rows[:7]:
        _fact_note(row, "yfinance has no such data (HTTP 404)")
        _fact_note(row, "yfinance has no such data (HTTP 404)")
    _fact_note(rows[7], "yfinance does not cover this instrument")
    assert fact_notes(rows, ["classification", "shares"]) == [
        "No industry or shares outstanding for T0, T1, T2, T3, T4 and 2 more: yfinance has no such data (HTTP 404)",
        "No industry or shares outstanding for T7: yfinance does not cover this instrument"]
    assert fact_notes(rows[:1], ["shares"]) == ["No shares outstanding for T0: yfinance has no such data (HTTP 404)"]
