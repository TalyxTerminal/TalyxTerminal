"""A factor run's Results in the research environment, with Alphalens, on synthetic bars: every figure of the run (Work's and each
Results tab's) says what each line is and which figure it is, one set; each tab has the contract's shape and nothing is left out
without a reason; the numbers are Alphalens' (the IC table against Alphalens' own pipeline, the event study against its function).

Not part of the daily suite: set TALYX_NATIVE_SMOKE=1 (see environments.py).
"""
from __future__ import annotations

import json
import os
import subprocess

import pytest
from jsonschema import Draft202012Validator

from tests.native.environments import QUANT, environment

pytestmark = pytest.mark.skipif(not os.environ.get("TALYX_NATIVE_SMOKE"), reason="Set TALYX_NATIVE_SMOKE=1 to run native engines")

SCHEMA = json.loads((QUANT.parents[1] / "tests" / "fixtures" / "quant" / "factor-results.schema.json").read_text(encoding="utf-8"))  # the shapes frontend/factor/resultTypes.ts reads
ROLES = {"main", "benchmark", "compare", "group", "updown", "attention", "detail"}
TABS = ("returns", "ic", "industry", "style", "turnover", "costs", "event")
FORMULA = "-($close / Ref($close, 4) - 1) / Std($close / Ref($close, 1) - 1, 20)"

PREPARE = r'''
import json, sys
from pathlib import Path
import numpy as np, pandas as pd
sys.path.insert(0, sys.argv[1])
from native import factor_resident as R
root, formula = Path(sys.argv[2]), sys.argv[3]
days, names = 900, 150
rng = np.random.default_rng(11)
labels = pd.bdate_range("2016-01-01", periods=days).tz_localize("UTC").asi8 // 1_000_000
columns = [f"S{i}" for i in range(names)]
close = np.exp(np.cumsum(rng.normal(0, .02, (days, names)), axis=0)) * 50
close[:200, :12] = np.nan
volume = rng.integers(100_000, 1_000_000, (days, names)).astype(float)
pd.DataFrame({"label": np.repeat(labels, names), "knownAt": np.repeat(labels, names), "column": np.tile(columns, days), "open": close.ravel(), "high": close.ravel() * 1.01,
              "low": close.ravel() * .99, "close": close.ravel(), "volume": volume.ravel(), "rawClose": close.ravel(), "rawVolume": volume.ravel()}).to_parquet(root / "bars.parquet", index=False)
(root / "extras.json").write_text(json.dumps({"industry": {c: f"I{i % 9}" for i, c in enumerate(columns)},
                                              "shares": {c: [[int(labels[0]), 1e6 * (i + 1)]] for i, c in enumerate(columns)}, "notes": []}))
info = {"annualization": 252, "timeframe": "1d", "instruments": [{"column": c} for c in columns]}
data = {"key": "synthetic", "barsPath": str(root / "bars.parquet"), "info": info, "extrasPath": str(root / "extras.json")}
R.run_job({"output": str(root / "run"), "data": data, "kind": "run", "formula": formula, "settings": {}})
plain = {"leaveOut": {"halted": False, "newListings": False, "illiquid": False}, "extremes": "none", "scale": "none", "neutralize": "none", "delay": 0, "holding": 5}
R.run_job({"output": str(root / "plain"), "data": data, "kind": "run", "formula": formula, "settings": plain})
work = json.loads((root / "run" / "work.json").read_text())
for name, settings in (("run", {}), ("plain", plain)):
    job = {"output": str(root / name / "results"), "data": data, "settings": settings, "values": str(root / name / "values.npy"), "formula": True,
           "similar": work["similar"], "heldOutFrom": None}
    (root / f"{name}-invocation.json").write_text(json.dumps({"mode": "once", "job": job}))
print(json.dumps({"ok": True}))
'''

CHECK = r'''
import json, sys, warnings
from pathlib import Path
import numpy as np, pandas as pd
sys.path.insert(0, sys.argv[1])
warnings.simplefilter("ignore")
from native import factor_results as FR
from native.factor_resident import load_panel
import alphalens.performance as performance, alphalens.utils as utils
root = Path(sys.argv[2])
job = json.loads((root / "plain-invocation.json").read_text())["job"]
panel = load_panel(job["data"])
raw = np.load(job["values"])
built = FR.Results(job, panel, raw)
# The IC table against Alphalens' own pipeline (its forward returns and cleaning), same values, no preprocessing, no delay.
factor = pd.DataFrame(raw, index=panel.dates, columns=panel.columns).stack().rename("factor")
factor.index.names = ["date", "asset"]
close = pd.DataFrame(panel.fields["close"], index=panel.dates, columns=panel.columns)
theirs = {}
for period in (1, 5, 10):
    # A missing ten-day return must not exclude the same observation from a shorter horizon's IC.
    clean = utils.get_clean_factor_and_forward_returns(factor, close, quantiles=5, periods=(period,), max_loss=1.0, filter_zscore=None)
    column = f"{period}D"
    theirs[column] = performance.factor_information_coefficient(clean)[column].mean()
ours = json.loads((root / "plain" / "results" / "ic.json").read_text())["information"]
# The event study against Alphalens' function on the same factor table and daily returns.
frame = built.factor_data()
alpha = performance.average_cumulative_return_by_quantile(frame, close.pct_change(fill_method=None), periods_before=5, periods_after=20, demeaned=True)
event = json.loads((root / "plain" / "results" / "event.json").read_text())["event"]
# Alphalens' columns come in the order the dates first met each offset; by offset they line up with ours (-5 to 20).
gaps = [abs(alpha.loc[(row["group"], "mean")].sort_index().to_numpy() - np.asarray(row["mean"], dtype=float)).max() for row in event]
# The IC inside each sector against Alphalens' by-group IC, also with values rounded so that names tie and a one-name sector some days.
tied = frame.copy()
tied["factor"] = tied["factor"].round(1)
tied.loc[tied.index.get_level_values("asset").isin(["S1", "S2"]), "group"] = "Alone"
tied = tied[~((tied["group"] == "Alone") & (tied.index.get_level_values("asset") == "S2") & (tied.index.get_level_values("date").day % 2 == 0))]
grouped = []
for table in (frame, tied):
    mine = FR.Results.group_ic(table, "5D")
    alphalens = performance.mean_information_coefficient(table[["5D", "factor", "factor_quantile", "group"]], by_group=True)["5D"]
    grouped.append(float((mine - alphalens).abs().max()))
print(json.dumps({"ic": {str(row["period"]): [row["mean"], float(theirs[f"{row['period']}D"])] for row in ours}, "event": float(max(gaps)), "grouped": grouped}))
'''


def tab_validator(name):
    return Draft202012Validator({"$ref": f"#/$defs/{name}", "$defs": SCHEMA["$defs"]})


@pytest.fixture(scope="module")
def report(tmp_path_factory):
    root = tmp_path_factory.mktemp("report")
    python = str(environment("research.lock", "3.12"))
    (root / "prepare.py").write_text(PREPARE, encoding="utf-8")
    subprocess.run([python, "-B", str(root / "prepare.py"), str(QUANT), str(root), FORMULA], check=True, capture_output=True, text=True, encoding="utf-8")
    for name in ("run", "plain"):
        # The Results process as the Host starts it: one invocation file, the answer beside the tabs.
        done = subprocess.run([python, "-B", str(QUANT / "native" / "factor_results.py"), str(root / f"{name}-invocation.json")], capture_output=True, text=True, encoding="utf-8")
        assert done.returncode == 0, done.stderr[-3000:]
    return root


def figures_of(root, name):
    work = json.loads((root / name / "work.json").read_text(encoding="utf-8"))
    yield "work", work["figures"]
    for tab in TABS:
        found = json.loads((root / name / "results" / f"{tab}.json").read_text(encoding="utf-8"))
        yield tab, {key: item["figure"] for key, item in found["figures"].items() if "figure" in item}


def test_every_figure_of_a_run_carries_its_role_and_key_once(report):
    keys = []
    for tab, figures in figures_of(report, "run"):
        for name, figure in figures.items():
            keys.append(figure["layout"]["meta"]["key"])
            for trace in figure["data"]:
                assert trace["meta"]["role"] in ROLES and trace["meta"]["key"], (tab, name, trace.get("name"))
    assert len(keys) == len(set(keys)) >= 19, keys


def test_every_tab_has_the_contract_shape_and_says_why_for_anything_left_out(report):
    answer = json.loads((report / "run" / "results" / "answer.json").read_text(encoding="utf-8"))
    assert answer["ok"] and set(answer["tabs"].values()) == {"done"} and answer["about"]["styles"] == "Styles v1"
    names = {"returns": "ReturnsTab", "ic": "IcTab", "industry": "IndustryTab", "style": "StyleTab", "turnover": "TurnoverTab", "costs": "CostsTab", "event": "EventTab"}
    for tab in TABS:
        found = json.loads((report / "run" / "results" / f"{tab}.json").read_text(encoding="utf-8"))
        tab_validator(names[tab]).validate(found)
        # With prices, industry and shares nothing is missing; anything that were would say why in its place.
        assert found["missing"] == {}, (tab, found["missing"])
        for key, item in found["figures"].items():
            assert "figure" in item, (tab, key, item.get("unavailable"))


def test_the_numbers_are_alphalens(report, tmp_path):
    script = tmp_path / "check.py"
    script.write_text(CHECK, encoding="utf-8")
    done = subprocess.run([str(environment("research.lock", "3.12")), "-B", str(script), str(QUANT), str(report)], capture_output=True, text=True, encoding="utf-8", check=True)
    found = json.loads(done.stdout.strip().splitlines()[-1])
    for period, (ours, theirs) in found["ic"].items():
        assert abs(ours - theirs) < 1e-4, (period, ours, theirs)
    assert found["event"] < 1e-8  # the tab keeps 8 decimals
    assert max(found["grouped"]) < 1e-10, found["grouped"]


# ---------------------------------------------------------------------------------------------------------------- F: the figures that need Alphalens, at the draft's level
def by_key(root):
    return {figure["layout"]["meta"]["key"]: figure for _, figures in figures_of(root, "run") for figure in figures.values()}


def test_daily_series_are_detail_and_the_mean_is_main(report):
    figures = by_key(report)
    for key in ("factor.results.spread", "factor.results.turnover", "factor.results.rank-autocorrelation"):
        traces = figures[key]["data"]
        mains = [trace for trace in traces if trace["meta"]["role"] == "main"]
        details = [trace for trace in traces if trace["meta"]["role"] == "detail"]
        reads = mains or [trace for trace in traces if trace["meta"]["role"] == "group"]  # the 1-month mean reads: main, or the two ends of the ramp for the two groups' turnover
        assert len(reads) >= 1 and details, key
        assert all("mean" in str(trace.get("name", "")).lower() for trace in reads), key
        assert all(len(trace["x"]) > 100 for trace in details), key  # the days
    spread = figures["factor.results.spread"]["data"]
    assert any(trace["meta"]["role"] == "detail" and trace.get("fill") for trace in spread)  # the mean plus and minus one standard deviation, faint
    daily = figures["factor.results.ic-daily"]["data"]
    assert [trace["meta"]["role"] for trace in daily if trace["type"] == "bar"] == ["updown"]
    assert [trace["meta"]["role"] for trace in daily if "mean" in str(trace.get("name", "")).lower()] == ["main"]


def test_signed_figures_are_updown(report):
    figures = by_key(report)
    assert any(trace["type"] == "bar" and trace["meta"]["role"] == "updown" for trace in figures["factor.results.ic-daily"]["data"])
    heat = next(trace for trace in figures["factor.results.ic-monthly"]["data"] if trace["type"] == "heatmap")
    assert heat["meta"]["role"] == "updown"  # two colours by sign, not the neutral ramp of the sweeps


def test_top_and_bottom_turnover_are_two_ends_of_the_ramp(report):
    traces = [trace for trace in by_key(report)["factor.results.turnover"]["data"] if trace["meta"]["role"] == "group"]
    assert sorted((trace["meta"]["index"], trace["meta"]["of"]) for trace in traces) == [(0, 2), (1, 2)]


def test_factor_weighted_long_short_starts_at_zero_percent(report):
    figure = by_key(report)["factor.results.factor-weighted"]
    path = next(trace for trace in figure["data"] if trace["meta"]["role"] == "main")
    values = [value for value in path["y"] if value is not None]
    assert abs(values[0]) < .05 and figure["layout"]["yaxis"]["tickformat"].endswith("%")  # a cumulative return from 0%, not a net value from 1


def test_every_figure_reads_out_like_the_draft(report):
    from tests.unit.figure_reading import READOUT, check_reading
    figures = by_key(report)
    problems = [problem for key, figure in figures.items() if key in READOUT for problem in check_reading(key, figure)]
    assert not problems, "\n".join(problems)
    assert set(READOUT) - {"factor.groups"} <= set(figures) | {"factor.decay"}


def test_the_work_figures_read_out_like_the_draft(report):
    from tests.unit.figure_reading import check_reading
    work = json.loads((report / "run" / "work.json").read_text(encoding="utf-8"))
    problems = [problem for figure in work["figures"].values() for problem in check_reading(figure["layout"]["meta"]["key"], figure)]
    assert not problems, "\n".join(problems)


DEMEANED = r'''
import json, sys, warnings
import numpy as np, pandas as pd
sys.path.insert(0, sys.argv[1])
warnings.simplefilter("ignore")
from native import factor_results as FR, factor_work as W
import alphalens.performance as perf
rng = np.random.default_rng(8)
days, names = 700, 120
labels = pd.bdate_range("2017-01-01", periods=days).tz_localize("UTC").asi8 // 1_000_000
columns = [f"S{i}" for i in range(names)]
close = np.exp(np.cumsum(rng.normal(0, .02, (days, names)) + np.linspace(0, .01, names)[None, :] * rng.normal(size=(days, 1)), axis=0)) * 50
fields = {"open": close, "high": close * 1.01, "low": close * .99, "close": close, "volume": rng.uniform(1e5, 1e6, (days, names))}
fields.update(rawClose=fields["close"], rawVolume=fields["volume"])
panel = W.Panel(labels, columns, fields, np.ones((days, names), dtype=bool), {"annualization": 252, "timeframe": "1d"}, industry=[f"I{i % 6}" for i in range(names)], shares=np.full((days, names), 1e6))
raw = -(close / np.vstack([np.full((5, names), np.nan), close[:-5]]) - 1) + rng.normal(0, .05, (days, names))
out = {}
for case, extra in (("raw", {"demeaned": False}), ("neutral", {"groupAdjust": True}), ("both", {"demeaned": False, "groupAdjust": True})):
    plain = {"leaveOut": {"halted": False, "newListings": False, "illiquid": False}, "extremes": "none", "scale": "none", "neutralize": "none", "delay": 0, "holding": 5, **extra}
    built = FR.Results({"settings": plain, "formula": True, "similar": []}, panel, raw)
    frame = built.factor_data()
    demeaned, adjust = extra.get("demeaned", True), extra.get("groupAdjust", False)
    returns = built.build("returns")
    mean, _ = perf.mean_return_by_quantile(frame, by_group=False, demeaned=demeaned, group_adjust=adjust)
    import alphalens.utils as alutils
    rate = mean.apply(alutils.rate_of_return, axis=0, base_period=mean.columns[0])["5D"]  # a period's mean as a rate for one bar, as the tab keeps it
    ours = {item["group"]: item["means"][built.periods.index(5)] for item in returns["meanByGroup"]}
    # Alphalens' own rate of return of a period is the mean as it is for the base period
    gaps = [abs(ours[int(q)] - float(rate.loc[q]) * 1e4) for q in rate.index]
    prices = pd.DataFrame(close, index=panel.dates, columns=columns)
    factor = pd.DataFrame(raw, index=panel.dates, columns=columns).stack()
    factor.index.names = ["date", "asset"]
    five_days = alutils.get_clean_factor_and_forward_returns(factor, prices, periods=(5,), groupby=dict(zip(columns, panel.industry)),
                                                             quantiles=5, max_loss=1.0, filter_zscore=None)
    ic = perf.factor_information_coefficient(five_days, group_adjust=adjust)["5D"].mean()
    information = next(row for row in built.build("ic")["information"] if row["period"] == 5)["mean"]
    alpha = perf.factor_alpha_beta(frame, perf.factor_returns(frame, demeaned=demeaned, group_adjust=adjust), demeaned, adjust, annualization=252)
    table = {row["period"]: row for row in returns["returnsByPeriod"]}[5]
    out[case] = {"means": max(gaps), "ic": abs(information - float(ic)), "alpha": abs(table["alpha"] - float(alpha.loc["Ann. alpha", "5D"])),
                 "spread": abs(table["spread"] - (table["top"] - table["bottom"]))}
print(json.dumps(out))
'''


def test_demeaned_and_group_adjust_reach_alphalens(tmp_path):
    script = tmp_path / "demeaned.py"
    script.write_text(DEMEANED, encoding="utf-8")
    done = subprocess.run([str(environment("research.lock", "3.12")), "-B", str(script), str(QUANT)], capture_output=True, text=True, encoding="utf-8")
    assert done.returncode == 0, done.stderr[-3000:]
    found = json.loads(done.stdout.strip().splitlines()[-1])
    assert set(found) == {"raw", "neutral", "both"}
    for case, gaps in found.items():
        # the same Alphalens call with the same arguments; what is left is only the rounding of the numbers the tab keeps (4 decimals in bps, 6 elsewhere)
        assert gaps["means"] < 1e-4 and gaps["ic"] < 1e-6 and gaps["alpha"] < 1e-5, (case, gaps)
        assert gaps["spread"] < 1e-3, (case, gaps)   # the spread is the top group's mean less the bottom group's, Raw or Demeaned
