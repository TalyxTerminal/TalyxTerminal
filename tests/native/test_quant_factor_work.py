"""The factor engine in the research environment on synthetic bars (505 names, about ten years of days): speed of a run in the resident
process, a formula and its code giving the same numbers, a code error naming its line, sweeps, held-out validation, the chart roles every
figure carries, and rank IC against Alphalens.

Not part of the daily suite: set TALYX_NATIVE_SMOKE=1 (see environments.py).
"""
from __future__ import annotations

import json
import os
import re
import subprocess
import time

import numpy as np
import pandas as pd
import pytest

from modules.quant.native import formula as F
from tests.native.environments import QUANT, environment

pytestmark = pytest.mark.skipif(not os.environ.get("TALYX_NATIVE_SMOKE"), reason="Set TALYX_NATIVE_SMOKE=1 to run native engines")

FORMULA = "-($close / Ref($close, 4) - 1) / Std($close / Ref($close, 1) - 1, 20)"
DAYS, NAMES = 2700, 505
ROLES = {"main", "benchmark", "compare", "group", "updown", "attention", "detail"}


def synthetic(root):
    rng = np.random.default_rng(5)
    labels = pd.bdate_range("2015-01-01", periods=DAYS).tz_localize("UTC").as_unit("ns").asi8 // 1_000_000
    columns = [f"S{index}" for index in range(NAMES)]
    close = np.exp(np.cumsum(rng.normal(0, .02, (DAYS, NAMES)), axis=0)) * 50
    close[:300, :40] = np.nan  # listed later
    volume = rng.integers(100_000, 1_000_000, (DAYS, NAMES)).astype(float)
    pd.DataFrame({"label": np.repeat(labels, NAMES), "knownAt": np.repeat(labels, NAMES), "column": np.tile(columns, DAYS), "open": close.ravel(), "high": close.ravel() * 1.01,
                  "low": close.ravel() * .99, "close": close.ravel(), "volume": volume.ravel(), "rawClose": close.ravel(), "rawVolume": volume.ravel()}).to_parquet(root / "bars.parquet", index=False)
    (root / "extras.json").write_text(json.dumps({"industry": {column: f"I{index % 11}" for index, column in enumerate(columns)},
                                                  "shares": {column: [[int(labels[0]), 1e6 * (index + 1)]] for index, column in enumerate(columns)}, "notes": []}))
    info = {"annualization": 252, "timeframe": "1d", "instruments": [{"column": column} for column in columns]}
    data = {"key": "synthetic", "barsPath": str(root / "bars.parquet"), "info": info, "extrasPath": str(root / "extras.json")}
    return data, labels, columns, close


class Resident:
    def __init__(self, root):
        (root / "invocation.json").write_text(json.dumps({"mode": "resident"}))
        self.process = subprocess.Popen([str(environment("research.lock", "3.12")), "-B", str(QUANT / "native" / "factor_resident.py"), str(root / "invocation.json")],
                                        stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=open(root / "stderr.log", "w"), text=True, encoding="utf-8")
        assert json.loads(self.process.stdout.readline())["ready"]

    def run(self, job):
        started = time.perf_counter()
        self.process.stdin.write(json.dumps({"op": "run", "id": job["output"], "job": job}) + "\n")
        self.process.stdin.flush()
        while True:
            answer = json.loads(self.process.stdout.readline())
            if answer.get("id") == job["output"] and "future" not in answer:
                return answer, time.perf_counter() - started

    def close(self):
        self.process.stdin.close()
        self.process.wait(timeout=60)


@pytest.fixture(scope="module")
def engine(tmp_path_factory):
    root = tmp_path_factory.mktemp("factor")
    data, labels, columns, close = synthetic(root)
    resident = Resident(root)
    yield resident, root, data, labels
    resident.close()


def work(root, name):
    return json.loads((root / name / "work.json").read_text(encoding="utf-8"))


def assert_roles(figure):
    assert figure["layout"]["meta"]["key"]
    for trace in figure["data"]:
        assert trace["meta"]["role"] in ROLES and trace["meta"]["key"], trace.get("name")


def test_a_run_takes_at_most_three_seconds_once_the_process_is_up_and_its_figures_carry_roles(engine):
    resident, root, data, labels = engine
    first, _ = resident.run({"output": str(root / "warm"), "data": data, "kind": "run", "formula": FORMULA, "settings": {}})
    assert first["ok"], first
    answer, took = resident.run({"output": str(root / "run"), "data": data, "kind": "run", "formula": FORMULA.replace("4", "5"), "settings": {}})
    assert answer["ok"] and took <= 3, took
    result = work(root, "run")
    assert set(result["numbers"]) >= {"rankIc", "t", "longShortNet", "turnover", "names"}
    assert len(result["checks"]) == 9 and len(result["decay"]) == 20
    for figure in result["figures"].values():
        assert_roles(figure)


def test_the_price_styles_take_part_in_the_most_like_comparison_and_the_run_stays_within_three_seconds(engine):
    resident, root, data, labels = engine
    resident.run({"output": str(root / "warm2"), "data": data, "kind": "run", "formula": FORMULA, "settings": {}})
    # Minus the return over 21 bars is the Reversal style's own definition: the most like is that style, with the library empty.
    answer, took = resident.run({"output": str(root / "reversal"), "data": data, "kind": "run", "formula": "-($close / Ref($close, 21) - 1)", "settings": {}})
    assert answer["ok"] and took <= 3, took
    result = work(root, "reversal")
    like = result["numbers"]["mostLike"]
    assert like["name"] == "Reversal" and like["version"] == "Style" and like["rho"] > .9, like
    assert any(item["name"] == "Reversal" and item.get("style") for item in result["similar"])
    library = next(item for item in result["checks"] if item["id"] == "library")
    assert library["state"] == "ok" and library["value"] == "Library empty"  # nothing published, nothing to repeat


def test_the_code_written_from_a_formula_gives_the_same_numbers(engine):
    resident, root, data, labels = engine
    resident.run({"output": str(root / "formula"), "data": data, "kind": "run", "formula": FORMULA, "settings": {}})
    source = root / "source"
    source.mkdir(exist_ok=True)
    (source / "factor.py").write_text(F.to_code(FORMULA, "Reversal"), encoding="utf-8")
    answer, _ = resident.run({"output": str(root / "code"), "data": data, "kind": "run", "code": {"source": str(source), "entry": "factor.py", "params": {}}, "settings": {}})
    assert answer["ok"], answer
    # the Results page is one set for both ways of writing: the same keys in the work, the numbers and the checks
    assert set(work(root, "formula")) == set(work(root, "code")) and set(work(root, "formula")["numbers"]) == set(work(root, "code")["numbers"])
    assert [item["id"] for item in work(root, "formula")["checks"]] == [item["id"] for item in work(root, "code")["checks"]]
    formula, code = work(root, "formula")["numbers"], work(root, "code")["numbers"]
    for key in ("rankIc", "t", "longShortNet", "turnover"):
        assert code[key] == pytest.approx(formula[key], abs=1e-10), key


def test_a_code_error_names_the_file_and_line(engine):
    resident, root, data, labels = engine
    source = root / "broken"
    source.mkdir(exist_ok=True)
    (source / "factor.py").write_text("import talyx_quant as q\nfrom talyx_quant import ops\ndata = q.data()\nfactor = -data.close / vol20\nq.report.factor(factor)\n", encoding="utf-8")
    answer, _ = resident.run({"output": str(root / "broken-run"), "data": data, "kind": "run", "code": {"source": str(source), "entry": "factor.py", "params": {}}, "settings": {}})
    assert not answer["ok"]
    failure = answer["failure"]
    assert (failure["stage"], failure["file"], failure["line"]) == ("code", "factor.py", 4)
    assert "vol20" in failure["message"] and failure["title"] == "stopped at line 4"


def test_a_sweep_scores_every_combination_and_held_out_scores_only_the_later_days(engine):
    resident, root, data, labels = engine
    held = int(labels[2200])
    answer, _ = resident.run({"output": str(root / "sweep"), "data": data, "kind": "sweep", "formula": FORMULA, "settings": {},
                              "sweep": [{"name": "holding", "values": [1, 5]}, {"name": "groups", "values": [5, 10]}]})
    assert answer["ok"], answer
    cells = work(root, "sweep")["cells"]
    assert sorted((cell["combo"]["holding"], cell["combo"]["groups"]) for cell in cells) == [(1, 5), (1, 10), (5, 5), (5, 10)]
    for cell in cells:
        for figure in cell["figures"].values():
            assert_roles(figure)
    resident.run({"output": str(root / "insample"), "data": data, "kind": "run", "formula": FORMULA, "settings": {}, "heldOutFrom": held})
    ls = json.loads((root / "insample" / "ls.json").read_text(encoding="utf-8"))
    answer, _ = resident.run({"output": str(root / "held"), "data": data, "kind": "heldout", "formula": FORMULA, "settings": {}, "heldOutFrom": held, "inSample": {"ls": ls}})
    assert answer["ok"], answer
    result = work(root, "held")
    assert result["from"] >= held and work(root, "insample")["dataTo"] < held
    assert_roles(result["figures"]["heldout"])


ALPHALENS = r'''
import json, sys, warnings
import numpy as np, pandas as pd
sys.path.insert(0, sys.argv[1])
from native import factor_work as W, formula as F
import alphalens.performance as performance, alphalens.utils as utils
rng = np.random.default_rng(3)
days, names = 1500, 300
labels = pd.bdate_range("2015-01-01", periods=days).tz_localize("UTC").as_unit("ns").asi8 // 1_000_000
columns = [f"S{index}" for index in range(names)]
close = np.exp(np.cumsum(rng.normal(0, .02, (days, names)), axis=0)) * 50
close[rng.random((days, names)) < .002] = np.nan
fields = {"open": close, "high": close * 1.01, "low": close * .99, "close": close, "volume": rng.uniform(1e5, 1e6, (days, names))}
fields.update(rawClose=fields["close"], rawVolume=fields["volume"])
panel = W.Panel(labels, columns, fields, np.ones((days, names), dtype=bool), {"annualization": 252}, industry=[f"I{i % 11}" for i in range(names)],
                shares=np.full((days, names), 1e6))
raw = F.evaluate(sys.argv[2], F.Context(panel.tables(), panel.industry)).to_numpy()
plain = W.settings_of({"leaveOut": {"halted": False, "newListings": False, "illiquid": False}, "extremes": "none", "scale": "none", "neutralize": "none",
                       "delay": 0, "holding": 5})
ours = W.Evaluation(panel, raw, plain, with_decay=False, with_similar=False).numbers["rankIc"]
dates = pd.to_datetime(labels, unit="ms", utc=True)
factor = pd.DataFrame(raw, index=dates, columns=columns).stack().rename("factor")
factor.index.names = ["date", "asset"]
with warnings.catch_warnings():
    warnings.simplefilter("ignore")
    clean = utils.get_clean_factor_and_forward_returns(factor, pd.DataFrame(close, index=dates, columns=columns), quantiles=5, periods=(5,), max_loss=1.0, filter_zscore=None)
    theirs = float(performance.factor_information_coefficient(clean)["5D"].mean())
print(json.dumps({"ours": ours, "theirs": theirs}))
'''


def test_rank_ic_matches_alphalens(tmp_path):
    script = tmp_path / "alphalens_check.py"
    script.write_text(ALPHALENS, encoding="utf-8")
    done = subprocess.run([str(environment("research.lock", "3.12")), "-B", str(script), str(QUANT), FORMULA], capture_output=True, text=True, encoding="utf-8", check=True)
    numbers = json.loads(done.stdout.strip().splitlines()[-1])
    assert abs(numbers["ours"] - numbers["theirs"]) < 1e-4, numbers


PARTS = r'''
import json, sys
import numpy as np, pandas as pd
sys.path.insert(0, sys.argv[1])
from native import factor_work as W, formula as F
rng = np.random.default_rng(4)
days, names = 400, 60
labels = pd.bdate_range("2019-01-01", periods=days).tz_localize("UTC").as_unit("ns").asi8 // 1_000_000
close = np.exp(np.cumsum(rng.normal(0, .02, (days, names)), axis=0)) * 50
close[rng.random((days, names)) < .01] = np.nan
fields = {"open": close, "high": close * 1.01, "low": close * .99, "close": close, "volume": rng.uniform(1e5, 1e6, (days, names))}
fields.update(rawClose=fields["close"], rawVolume=fields["volume"])
panel = W.Panel(labels, [f"S{i}" for i in range(names)], fields, np.ones((days, names), dtype=bool), {"annualization": 252, "timeframe": "1d"})
gaps = []
for formula in ["-($close / Ref($close, 4) - 1) / Std($close / Ref($close, 1) - 1, 20)", "CSRank(Mean($volume, 5)) - CSRank(Ref($close, 3) / $close)",
                "Corr($close / Ref($close, 1) - 1, Log($volume), 10) * Mean($close, 5)"]:
    parts = F.inputs(F.parse(formula), formula)
    together, found = F.evaluate(formula, F.Context(panel.tables(), panel.industry), parts)
    alone = F.evaluate(formula, F.Context(panel.tables(), panel.industry))
    gaps.append(float(np.nanmax(np.abs(together.to_numpy() - alone.to_numpy()))))
    assert np.array_equal(np.isnan(together.to_numpy()), np.isnan(alone.to_numpy()))
    for part in parts:
        own = F.evaluate(part, F.Context(panel.tables(), panel.industry))
        assert np.array_equal(np.isnan(found[part].to_numpy()), np.isnan(own.to_numpy())), part
        gaps.append(float(np.nanmax(np.abs(found[part].to_numpy() - own.to_numpy()))))
print(json.dumps({"gap": max(gaps)}))
'''


def test_the_inputs_computed_once_give_the_formula_its_own_values(tmp_path):
    """The values table's inputs (subexpressions) are computed first and the formula reads them as computed: the formula's values and each input
    are what each gives computed on its own."""
    script = tmp_path / "parts_check.py"
    script.write_text(PARTS, encoding="utf-8")
    done = subprocess.run([str(environment("research.lock", "3.12")), "-B", str(script), str(QUANT)], capture_output=True, text=True, encoding="utf-8", check=True)
    assert json.loads(done.stdout.strip().splitlines()[-1])["gap"] == 0.0


# ---------------------------------------------------------------------------------------------------------------- F: settings, the Alphalens options, the data checks
def synthetic_panel(start="2019-06-03", days=420, names=80, seed=3, industry=True, mutate=None):
    from modules.quant.native import factor_work as W
    rng = np.random.default_rng(seed)
    labels = pd.bdate_range(start, periods=days).tz_localize("UTC").as_unit("ns").asi8 // 1_000_000  # milliseconds whatever pandas' default unit
    close = np.exp(np.cumsum(rng.normal(0, .02, (days, names)), axis=0)) * 50
    volume = rng.uniform(4e5, 6e5, (days, names))  # steady, so that a day's dollar volume is within half to twice the usual
    if mutate is not None:
        close, volume = mutate(labels, close, volume)
    fields = {"open": close, "high": close * 1.01, "low": close * .99, "close": close, "volume": volume}
    fields.update(rawClose=fields["close"], rawVolume=fields["volume"])
    return W.Panel(labels, [f"S{i}" for i in range(names)], fields, np.ones((days, names), dtype=bool), {"annualization": 252, "timeframe": "1d"},
                   industry=[f"I{i % 6}" for i in range(names)] if industry else None, shares=np.full((days, names), 1e6) if industry else None)


PLAIN = {"leaveOut": {"halted": False, "newListings": False, "illiquid": False}, "extremes": "none", "scale": "none", "neutralize": "none", "delay": 0}


def ahead(panel, sign=1):
    close = panel.fields["close"]
    return sign * np.vstack([close[5:] / close[:-5] - 1, np.full((5, close.shape[1]), np.nan)])


def test_unknown_run_setting_is_refused():
    from modules.quant.native import factor_work as W
    with pytest.raises(ValueError, match="Unknown run setting: run"):
        W.settings_of({"run": {"holding": 10}})
    with pytest.raises(ValueError, match="Unknown run setting: speed"):
        W.settings_of({"holding": 5, "speed": 1})
    with pytest.raises(ValueError, match="Unknown run setting: leaveOut.speedy"):
        W.settings_of({"leaveOut": {"halted": True, "speedy": True}})
    with pytest.raises(ValueError, match="Unknown run setting: grouping.rows"):
        W.settings_of({"grouping": {"count": 5, "rows": 3}})
    everything = {"leaveOut": {"halted": False}, "holding": 3, "delay": 0, "grouping": {"count": 4, "method": "equal"}, "extremes": "none", "scale": "none", "neutralize": "none",
                  "costBps": 3, "borrowPct": 1, "heldOutFrom": None, "params": {}, "demeaned": False, "groupAdjust": True}
    kept = W.settings_of(everything)
    assert kept["holding"] == 3 and kept["demeaned"] is False and kept["groupAdjust"] is True
    assert W.settings_of({})["demeaned"] is True and W.settings_of({})["groupAdjust"] is False  # Alphalens' own defaults of the two options


def test_break_even_below_zero_reads_loses_before_costs():
    from modules.quant.native import factor_work as W
    panel = synthetic_panel()
    ev = W.Evaluation(panel, ahead(panel, -1), W.settings_of({**PLAIN, "holding": 5}), with_decay=False, with_similar=False)
    assert ev.numbers["breakEven"] < 0
    check = next(item for item in ev.checks if item["id"] == "cost_room")
    assert check["value"] == "loses before costs" and check["state"] == "watch"
    good = W.Evaluation(panel, ahead(panel, 1), W.settings_of({**PLAIN, "holding": 5}), with_decay=False, with_similar=False)
    assert next(item for item in good.checks if item["id"] == "cost_room")["value"].endswith("bps")


def test_the_two_alphalens_options_change_only_what_they_should_in_work():
    from modules.quant.native import factor_work as W
    panel = synthetic_panel()
    rng = np.random.default_rng(1)
    raw = ahead(panel) + rng.normal(0, .08, panel.shape)
    base = W.Evaluation(panel, raw, W.settings_of({**PLAIN, "holding": 5}), with_decay=False, with_similar=False)
    keep = W.Evaluation(panel, raw, W.settings_of({**PLAIN, "holding": 5, "demeaned": False}), with_decay=False, with_similar=False)
    # taking the day's mean off every return changes neither the ranking nor the top-minus-bottom gap
    assert keep.numbers["rankIc"] == pytest.approx(base.numbers["rankIc"], abs=1e-12) and keep.numbers["longShortNet"] == pytest.approx(base.numbers["longShortNet"], abs=1e-12)
    assert keep.details["groupMeans"] != base.details["groupMeans"]
    shift = [a - b for a, b in zip(keep.details["groupMeans"], base.details["groupMeans"], strict=True)]
    assert max(shift) - min(shift) < 1e-9 and abs(shift[0]) > 1e-6  # the same constant in every group: the market's own return
    neutral = W.Evaluation(panel, raw, W.settings_of({**PLAIN, "holding": 5, "groupAdjust": True}), with_decay=False, with_similar=False)
    assert neutral.numbers["rankIc"] != base.numbers["rankIc"]


GROUP_NEUTRAL = r'''
import json, sys, warnings
import numpy as np, pandas as pd
sys.path.insert(0, sys.argv[1])
warnings.simplefilter("ignore")
from native import factor_work as W
import alphalens.performance as performance, alphalens.utils as utils
rng = np.random.default_rng(4)
days, names = 900, 150
labels = pd.bdate_range("2016-01-01", periods=days).tz_localize("UTC").as_unit("ns").asi8 // 1_000_000
columns = [f"S{i}" for i in range(names)]
sector = [f"I{i % 7}" for i in range(names)]
drift = np.array([0.0006 * (i % 7) for i in range(names)])
close = np.exp(np.cumsum(rng.normal(0, .02, (days, names)) + drift, axis=0)) * 50
fields = {"open": close, "high": close * 1.01, "low": close * .99, "close": close, "volume": rng.uniform(1e5, 1e6, (days, names))}
fields.update(rawClose=fields["close"], rawVolume=fields["volume"])
panel = W.Panel(labels, columns, fields, np.ones((days, names), dtype=bool), {"annualization": 252}, industry=sector, shares=np.full((days, names), 1e6))
raw = rng.normal(0, 1, (days, names)) + np.array([0.7 * (i % 7) for i in range(names)])
plain = {"leaveOut": {"halted": False, "newListings": False, "illiquid": False}, "extremes": "none", "scale": "none", "neutralize": "none", "delay": 0, "holding": 5, "groupAdjust": True}
ours = W.Evaluation(panel, raw, W.settings_of(plain), with_decay=False, with_similar=False).numbers["rankIc"]
dates = pd.to_datetime(labels, unit="ms", utc=True)
factor = pd.DataFrame(raw, index=dates, columns=columns).stack().rename("factor")
factor.index.names = ["date", "asset"]
clean = utils.get_clean_factor_and_forward_returns(factor, pd.DataFrame(close, index=dates, columns=columns), groupby=dict(zip(columns, sector)), quantiles=5, periods=(5,),
                                                   max_loss=1.0, filter_zscore=None)
theirs = float(performance.factor_information_coefficient(clean, group_adjust=True)["5D"].mean())
plain_theirs = float(performance.factor_information_coefficient(clean)["5D"].mean())
print(json.dumps({"ours": ours, "theirs": theirs, "ungrouped": plain_theirs}))
'''


def test_work_numbers_follow_group_neutral(tmp_path):
    script = tmp_path / "group_neutral.py"
    script.write_text(GROUP_NEUTRAL, encoding="utf-8")
    done = subprocess.run([str(environment("research.lock", "3.12")), "-B", str(script), str(QUANT)], capture_output=True, text=True, encoding="utf-8")
    assert done.returncode == 0, done.stderr[-3000:]
    found = json.loads(done.stdout.strip().splitlines()[-1])
    assert abs(found["ours"] - found["theirs"]) < 1e-4, found
    assert abs(found["theirs"] - found["ungrouped"]) > 1e-3  # the option makes a difference on this data, so the match is not an accident


@pytest.mark.parametrize("holding", [1, 5, 10])
def test_rank_ic_matches_alphalens_at_each_holding(tmp_path, holding):
    script = tmp_path / "alphalens_check.py"
    text = ALPHALENS.replace('"holding": 5', f'"holding": {holding}').replace("periods=(5,)", f"periods=({holding},)").replace('["5D"]', f'["{holding}D"]')
    assert text.count(f"{holding}D") >= 1
    script.write_text(text, encoding="utf-8")
    done = subprocess.run([str(environment("research.lock", "3.12")), "-B", str(script), str(QUANT), FORMULA], capture_output=True, text=True, encoding="utf-8", check=True)
    found = json.loads(done.stdout.strip().splitlines()[-1])
    assert abs(found["ours"] - found["theirs"]) < 1e-4, (holding, found)


def test_a_run_with_holding_10_reports_10_and_trades_less():
    from modules.quant.native import factor_work as W
    panel = synthetic_panel()
    raw = ahead(panel) + np.random.default_rng(2).normal(0, .08, panel.shape)
    one = W.Evaluation(panel, raw, W.settings_of({**PLAIN, "holding": 1}), with_similar=False)
    ten = W.Evaluation(panel, raw, W.settings_of({**PLAIN, "holding": 10}), with_similar=False)
    assert ten.numbers["holding"] == 10 and np.nanmean(ten.returns["traded"]) < np.nanmean(one.returns["traded"])  # a longer hold trades less each day
    chosen = [trace for trace in ten.figures()["decay"]["data"] if trace["meta"]["key"] == "decay:holding"][0]
    assert [x for x, y in zip(chosen["x"], chosen["y"], strict=True) if y is not None] == ["10D"]  # the highlighted bar is 10D
    assert [note["text"] for note in ten.figures()["decay"]["layout"]["annotations"]] == ["10D"]  # and named over it


def test_the_crash_day_is_not_a_coverage_drop_and_its_next_returns_are_not_all_one_sign():
    """D6 on synthetic bars: every name falls about 12% on 2020-03-16, as the S&P 500 did; no name loses its value that day."""
    from modules.quant.native import factor_work as W

    def crash(labels, close, volume):
        day = int(np.nonzero(pd.to_datetime(labels, unit="ms").strftime("%Y-%m-%d") == "2020-03-16")[0][0])
        rng = np.random.default_rng(9)
        close = close.copy()
        close[day:] *= (1 - .12 + rng.normal(0, .03, close.shape[1]))[None, :]
        return close, volume
    panel = synthetic_panel(start="2019-06-03", days=420, names=100, mutate=crash)
    day = int(np.nonzero(pd.to_datetime(panel.labels, unit="ms").strftime("%Y-%m-%d") == "2020-03-16")[0][0])
    raw = -(panel.fields["close"] / np.vstack([np.full((5, 100), np.nan), panel.fields["close"][:-5]]) - 1)
    ev = W.Evaluation(panel, raw, W.settings_of({**PLAIN, "holding": 5, "extremes": "mad3", "scale": "zscore"}), with_similar=False)
    names = ev.details["names"]
    around = np.median(np.r_[names[day - 5:day], names[day + 1:day + 6]])
    assert names[day] >= around - .02 * around
    assert next(item for item in ev.checks if item["id"] == "coverage_drop")["state"] == "ok"
    following = ev.forward[day][np.isfinite(ev.forward[day])]
    assert len(following) > 50 and (following > 0).any() and (following < 0).any()


def test_a_split_nobody_adjusted_is_reported_as_suspicious_on_its_day():
    """D7's counter-test: a 2:1 split at bar 100 left in the prices (the price falls by more than half, the volume rises by as much) must be named by the check."""
    from modules.quant.native import factor_work as W

    def split(labels, close, volume):
        close, volume = close.copy(), volume.copy()
        close[100:, 7] = close[100:, 7] * .45  # about a 2:1 split: the price falls by more than half at once, the shares traded rise by as much
        volume[100:, 7] = volume[100:, 7] * 2.2
        return close, volume
    panel = synthetic_panel(mutate=split)
    flags = W.suspicious(panel)
    assert [(item["t"], panel.columns[item["i"]]) for item in flags] == [(100, "S7")] and re.search(r"fell 5\d%", flags[0]["text"])
    ev = W.Evaluation(panel, ahead(panel), W.settings_of({**PLAIN, "holding": 5}), with_similar=False)
    check = next(item for item in ev.checks if item["id"] == "suspicious_prices")
    assert check["state"] == "watch" and check["value"] == "1"


def test_unavailable_field_is_named_at_its_place():
    fields = {"$close": {}, "$shares": {"unavailable": "Market gives no shares outstanding for these names"}}
    text = "$close / $shares"
    error = F.check(text, fields)
    assert error["kind"] == "field" and (error["at"], error["length"]) == (text.index("$shares"), len("$shares"))
    assert error["message"].startswith("$shares") and error["message"].endswith("Market gives no shares outstanding for these names")
    assert F.check("$close * 2", fields) is None  # a field the data does give is no problem


def test_a_library_update_makes_values_only_for_the_names_that_are_index_members_that_day(tmp_path_factory):
    """D7 with the real formula engine: three of ten members are swapped on day 250; the values after it come for that day's members that have a price."""
    root = tmp_path_factory.mktemp("members")
    days, columns = 300, [f"S{index:02d}" for index in range(13)]
    rng = np.random.default_rng(4)
    day = 86_400_000
    rows = []
    for number in range(days):
        members = set(range(0, 10)) if number < 250 else set(range(3, 13))
        for index, column in enumerate(columns):
            if (number == 299 and index == 5) or (index >= 10 and number < 200):
                continue
            close = 100 + rng.normal() + number * .1
            rows.append((number * day, column, close, close, close, close, 1000.0, index in members))
    bars = pd.DataFrame(rows, columns=["label", "column", "open", "high", "low", "close", "volume", "isMember"])
    bars["rawClose"], bars["rawVolume"] = bars["close"], bars["volume"]
    bars.to_parquet(root / "bars.parquet", index=False)
    info = {"annualization": 252, "timeframe": "1d", "instruments": [{"column": column} for column in columns]}
    resident = Resident(root)
    try:
        resident.process.stdin.write(json.dumps({"op": "formula_values", "id": "m", "job": {"data": {"key": "members", "barsPath": str(root / "bars.parquet"), "info": info, "extrasPath": None},
                                                                                              "formula": "$close / Ref($close, 1) - 1", "after": 248 * day, "output": str(root)}}) + "\n")
        resident.process.stdin.flush()
        while True:
            answer = json.loads(resident.process.stdout.readline())
            if answer.get("id") == "m":
                break
    finally:
        resident.close()
    assert answer["ok"], answer
    made = pd.read_parquet(answer["path"])
    for number in (249, 250, 299):
        members = set(range(0, 10)) if number < 250 else set(range(3, 13))
        priced = {index for index, column in enumerate(columns) if ((bars["label"] == number * day) & (bars["column"] == column)).any()}
        assert {columns.index(column) for column in made.loc[made["label"] == number * day, "column"]} == members & priced


def test_the_long_short_sharpe_takes_no_rate_off_a_book_that_pays_for_itself():
    # the top-minus-bottom book is already a return over cash; over the T-bill the rate came off twice (0.3 where 0.8 was right
    # for 8% a year at 10% volatility with the rate at 5%).
    from modules.quant.native import factor_work as W
    panel = synthetic_panel()
    rng = np.random.default_rng(4)
    raw = ahead(panel) + rng.normal(0, .08, panel.shape)
    found = W.Evaluation(panel, raw, W.settings_of({**PLAIN, "holding": 5}), with_decay=False, with_similar=False)
    net = found.returns["net"][np.isfinite(found.returns["net"])]
    assert found.numbers["sharpe"] == pytest.approx(net.mean() / net.std(ddof=1) * np.sqrt(panel.annualization))
    with pytest.raises(ValueError, match="Unknown run setting: riskFree"):  # a factor has no rate to set
        W.settings_of({"riskFree": "fixed"})
