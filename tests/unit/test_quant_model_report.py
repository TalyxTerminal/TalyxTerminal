"""M section, P3: what the Host assembles for the model's Work page (the window figure, the comparison figure) from a run's summary, and the report
rules that need no research environment. The report's own rules (Information, Returns, hover texts, labels of any formula) run on bars in the
research environment: tests/native/test_quant_model_pipeline.py (their names follow the build draft, section 3 and 4)."""
from __future__ import annotations

ROLES = {"main", "benchmark", "compare", "group", "updown", "attention", "detail"}
DAY = 86_400_000


def window(k, test_ic, *, valid_ic=0.01, train_ic=0.08, best=0.01, equal=0.012):
    start = 1_600_000_000_000 + k * 90 * DAY
    return {"k": k, "train": {"from": start - 400 * DAY, "to": start - 100 * DAY}, "valid": {"from": start - 90 * DAY, "to": start - 10 * DAY},
            "test": {"from": start, "to": start + 89 * DAY}, "trainIc": train_ic, "validIc": valid_ic, "testIc": test_ic, "bestIc": best, "equalIc": equal}


def summary(done=5, total=5, *, state="completed", failure=None, per_window=True, number=12, horizon=5):
    windows = [window(k, 0.02 + 0.005 * (k % 3)) for k in range(1, done + 1)]
    return {"id": f"run-{number}", "number": number, "state": state, "status": "completed" if state == "completed" else "running" if state == "running" else state,
            "windows": windows, "windowsDone": done, "windowsTotal": total, "perWindow": per_window, "failure": failure, "horizon": horizon,
            "baselines": {"bestInput": {"name": "ROC5", "ic": 0.01, "windows": [{"k": w["k"], "name": "ROC5", "sign": 1.0, "ic": w["bestIc"]} for w in windows]},
                          "equalWeight": {"mean": 0.012, "windows": [w["equalIc"] for w in windows]}}}


def figure_of(*args, **kwargs):
    from modules.quant.backend.model_work_figure import work_figure
    return work_figure(*args, **kwargs)


def traces(figure):
    return {trace["name"]: trace for trace in figure["data"]}


def test_work_figure_has_the_roles_and_traces_of_the_draft():
    figure = figure_of(summary())
    assert figure["layout"]["meta"]["key"] == "model.work.windows"
    found = traces(figure)
    assert {"Train", "Validation", "Test", "Test, cumulative", "Best input", "Inputs equal-weight"} <= set(found)
    assert found["Train"]["meta"]["role"] == "group" and found["Train"]["opacity"] == 0.35 and found["Validation"]["meta"]["role"] == "group"
    assert found["Test"]["meta"]["role"] == "updown" and found["Test, cumulative"]["meta"]["role"] == "main" and found["Test, cumulative"]["yaxis"] == "y2"
    assert found["Best input"]["meta"]["role"] == "benchmark" and found["Best input"]["yaxis"] == "y2"
    assert found["Inputs equal-weight"]["meta"]["role"] == "benchmark" and found["Inputs equal-weight"]["line"]["dash"] == "dot"
    # one value axis a plot: the bars above, the running totals below, on one axis of windows
    layout = figure["layout"]
    assert "overlaying" not in layout["yaxis2"] and layout["yaxis"]["domain"][0] > layout["yaxis2"]["domain"][1] and layout["xaxis"]["anchor"] == "y2"
    assert list(found["Test"]["x"]) == ["1", "2", "3", "4", "5"]
    assert all(trace["meta"]["role"] in ROLES and trace["meta"]["key"] for trace in figure["data"])


def test_work_figure_for_a_running_run_draws_finished_windows_only():
    figure = figure_of(summary(done=3, total=8, state="running"))
    found = traces(figure)
    assert list(found["Test"]["x"]) == ["1", "2", "3"] and len(found["Test, cumulative"]["y"]) == 3
    marks = [item for item in figure["layout"]["shapes"] if item["meta"]["role"] == "benchmark"]
    assert marks and marks[0]["x0"] == 3.5
    assert any(note["text"] == "training" for note in figure["layout"]["annotations"])


def test_work_figure_marks_the_window_that_failed():
    failure = {"window": 4, "title": "out of memory in window 4", "message": "needed 14.2 GB"}
    figure = figure_of(summary(done=3, total=8, state="failed", failure=failure))
    shapes = [item for item in figure["layout"]["shapes"] if item["meta"]["role"] == "attention"]
    assert shapes and any(note["text"] == "out of memory" for note in figure["layout"]["annotations"])


def test_work_figure_says_when_a_runs_code_keeps_no_windows():
    figure = figure_of(summary(per_window=False))
    assert figure["missing"] == "This run's code keeps no windows (no q.record); numbers are for the whole period."


def test_work_figure_overlays_the_other_run_only_when_both_predict_the_same_horizon():
    mine, other = summary(), summary(number=5)
    assert "#5, cumulative" in traces(figure_of(mine, compare=other)) and traces(figure_of(mine, compare=other))["#5, cumulative"]["meta"]["role"] == "compare"
    assert "#5, cumulative" not in traces(figure_of(mine, compare=summary(number=5, horizon=10)))
    assert "#5, cumulative" not in traces(figure_of(mine, compare=summary(number=5, done=3, total=5, state="running")))


def test_compare_figure_has_one_line_per_run_the_selected_one_in_main():
    from modules.quant.backend.model_work_figure import compare_figure
    figure = compare_figure([summary(number=12), summary(number=5), summary(number=3)])
    assert figure["layout"]["meta"]["key"] == "model.work.compare"
    roles = [trace["meta"]["role"] for trace in figure["data"]]
    assert roles == ["main", "compare", "group"] and figure["data"][1]["line"]["dash"] != "solid"
    assert [trace["name"] for trace in figure["data"]] == ["#12", "#5", "#3"] and any(shape["y0"] == 0 for shape in figure["layout"]["shapes"])


def test_every_figure_has_role_and_key_and_missing_ones_say_why():
    figure = figure_of(summary())
    for trace in figure["data"]:
        assert trace["meta"]["role"] in ROLES and trace["meta"]["key"]
    assert "missing" not in figure or figure["missing"]


def test_cumulative_lines_start_at_the_first_day_anything_moves():
    from modules.quant.native import model_figures as G
    days = [f"2025-01-{day:02d}" for day in range(1, 11)]
    flat_then_moving = {"data": [{"type": "scatter", "mode": "lines", "name": "Long-short", "x": days, "y": [0, 0, 0, 0, 0, 0.01, 0.02, 0.01, 0.03, 0.04]},
                                  {"type": "scatter", "mode": "lines", "name": "Group1", "x": days, "y": [0, 0, 0, 0, 0, -0.01, -0.02, 0, 0.01, 0.0]}], "layout": {}}
    drawn = G.tag_native(flat_then_moving, "model.returns.groups")
    assert drawn["data"][0]["x"][0] == "2025-01-05" and drawn["data"][0]["y"][:2] == [0, 0.01]
    bars = {"data": [{"type": "bar", "name": "IC", "x": days, "y": [0, 0, 0, 0, 0, 1, 2, 1, 3, 4]}], "layout": {}}
    assert len(G.tag_native(bars, "model.information.ic")["data"][0]["x"]) == 10, "bars are left whole"


def test_a_cut_cumulative_figure_drops_the_axis_range_qlib_fixed():
    from modules.quant.native import model_figures as G
    days = [f"2025-01-{day:02d}" for day in range(1, 11)]
    found = {"data": [{"type": "scatter", "mode": "lines", "name": "Long-short", "x": days, "y": [0, 0, 0, 0, 0, 0.01, 0.02, 0.01, 0.03, 0.04]}], "layout": {"xaxis": {"range": ["2025-01-01", "2025-01-10"], "type": "date"}}}
    assert G.tag_native(found, "model.returns.groups")["layout"]["xaxis"] == {"type": "date"}


def test_cumulative_lines_start_at_the_first_day_with_a_value():
    from modules.quant.native import model_figures as G
    days = [f"2025-01-{day:02d}" for day in range(1, 7)]
    found = {"data": [{"type": "scatter", "mode": "lines", "name": "Long-short", "x": days, "y": [None, None, None, 0.01, 0.02, 0.01]},
                      {"type": "scatter", "mode": "lines", "name": "Group1", "x": days, "y": [None, None, None, -0.01, 0.0, 0.01]}], "layout": {}}
    drawn = G.tag_native(found, "model.returns.groups")["data"]
    assert drawn[0]["x"][0] == "2025-01-04" and drawn[0]["y"][0] == 0.01 and len(drawn[1]["x"]) == 3


def test_qlib_traces_without_a_mode_are_cut_too():
    import numpy as np
    import pandas as pd

    from modules.quant.native import model_figures as G
    found = {"data": [{"type": "scatter", "name": "Q1", "mode": None, "x": pd.date_range("2024-11-07", periods=10), "y": np.array([np.nan] * 4 + [0.0, 0.1, 0.2, 0.1, 0.3, 0.2])}], "layout": {}}
    assert G.tag_native(found, "model.returns.groups")["data"][0]["x"][0] == "2024-11-11"


def test_a_windows_title_is_said_once_in_the_readout():
    from modules.quant.backend import model_work_figure as W
    traces = [{"hovertemplate": "%{customdata}<br>Test %{y:+.4f}<extra></extra>"}, {"hovertemplate": "%{customdata}<br>Validation %{y:+.4f}<extra></extra>"}, {"hovertemplate": "Best %{y:+.3f}<extra></extra>"}]
    drawn = W._title_once(traces)
    assert drawn[0]["hovertemplate"].startswith("%{customdata}<br>") and "customdata" not in drawn[1]["hovertemplate"] and "customdata" not in drawn[2]["hovertemplate"]


def test_the_daily_rank_ic_is_drawn_by_talyx_when_qlib_cannot():
    import numpy as np
    import pandas as pd

    from modules.quant.native import model_figures as G
    index = pd.MultiIndex.from_product([pd.date_range("2026-01-01", periods=40), list("abcdefgh")], names=["datetime", "instrument"])
    frame = pd.DataFrame({"score": np.random.default_rng(0).standard_normal(len(index)), "label": np.random.default_rng(1).standard_normal(len(index))}, index=index)
    drawn = G._own_ic(frame, "model.information.ic")
    assert [trace["name"] for trace in drawn["data"]] == ["Rank IC", "Rank IC, 1-month mean", "IC, 1-month mean"] and len(drawn["data"][0]["x"]) == 40  # the draft: daily Rank IC, the 1-month means of Rank IC and IC
    assert all("+" in trace["hovertemplate"] for trace in drawn["data"])
