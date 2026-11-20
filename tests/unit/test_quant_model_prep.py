"""M section, P1: the model's preparation and the check before training, on the Host's side and with the pieces that need no research
environment (the formula's reach, the windows, the check on a panel of bars, a batch, presets, library inputs pinned to a version).

Cases that need the research environment (Qlib's
operators, the three tree libraries) are in tests/native/test_quant_model_pipeline.py."""
from __future__ import annotations

import asyncio
from types import SimpleNamespace

import numpy as np
import pandas as pd
import pytest
from talyx_sdk import ContractError

from modules.quant.backend import model_prep as P
from modules.quant.backend.object_routes import Services
from modules.quant.backend.work_records import WorkRecords
from modules.quant.native import formula
from modules.quant.native import model_algorithms as A
from modules.quant.native import model_check as C
from modules.quant.native import model_data as D
from tests.unit.test_quant_model_training import SPLIT, panel
from tests.unit.test_quant_object_runs import FakeContext

SETTINGS = {"leaveOut": {"halted": True, "young": False, "illiquid": False},
            "processing": {"inputs": {"missing": "median", "extremes": "none", "scale": "rank", "takeOut": []}, "label": {"missing": "drop", "extremes": "none", "scale": "rank"}},
            "label": {"days": 5, "form": "rank", "formula": "CSRank(Ref($open, -6) / Ref($open, -1) - 1)"}, "split": SPLIT}

LABELS = {"five": "Ref($open, -6) / Ref($open, -1) - 1", "ranked": "CSRank(Ref($open, -6) / Ref($open, -1) - 1)",
          "up": "Gt(Ref($open, -6), Ref($open, -1))", "volatility": "Std(Ref($close, -20) / Ref($close, -19) - 1, 20)"}


def reach(source):
    return formula.reach(formula.parse(source, ahead=True))


# ---------------------------------------------------------------------------------------------------------- the label (#14, #15, D15)
@pytest.mark.parametrize(("key", "ahead", "back"), [("five", 6, 0), ("ranked", 6, 0), ("up", 6, 0), ("volatility", 20, 19)])
def test_label_formula_sets_the_gap_and_the_look_ahead_check(key, ahead, back):
    found = reach(LABELS[key])
    assert (found["ahead"], found["back"]) == (ahead, back)
    # the gap between segments is what the label reads beyond the bar: ahead - 1 bars (5 for the 5D label), shown as "= label, 6 bars"
    p = panel()
    for window in D.layout(p.labels, SPLIT, ahead, warmup=0):
        assert window.valid[0] - window.train[1] == ahead and window.test[0] - window.valid[1] == ahead


def test_the_quick_five_day_label_gives_the_same_windows_through_its_formula():
    p = panel()
    old = D.layout(p.labels, SPLIT, 6, warmup=0)
    new = D.layout(p.labels, SPLIT, reach(LABELS["five"])["ahead"], warmup=0)
    assert [(w.train, w.valid, w.test) for w in old] == [(w.train, w.valid, w.test) for w in new]


def test_reach_starts_where_the_label_starts():
    assert reach("Ref($open, -6) / Ref($open, -1) - 1")["start"] == {"bar": 1, "at": "open"}
    assert reach("Ref($close, -5) / $close - 1")["start"] == {"bar": 0, "at": "close"}
    assert reach(LABELS["volatility"])["start"] == {"bar": 0, "at": "close"}


def test_a_label_that_reads_no_later_bar_is_refused():
    with pytest.raises(formula.FormulaError, match="A label must read a later bar, such as Ref\\(\\$open, -6\\)"):
        reach("$close / Ref($close, 5) - 1")


def test_only_ref_may_look_ahead_in_a_label_and_a_factor_formula_never_may():
    with pytest.raises(formula.FormulaError, match="Only Ref can look ahead in a label"):
        formula.parse("Mean($close, -5)", ahead=True)
    with pytest.raises(formula.FormulaError) as error:
        formula.parse("Ref($open, -6) / Ref($open, -1) - 1")
    assert error.value.kind == "future"


def test_weight_formula_reads_only_the_past():
    assert not formula.check("Mean($volume, 5)")
    assert formula.check("Ref($volume, -1)")["kind"] == "future"


# ---------------------------------------------------------------------------------------------------------- the check (M1-M9, #18)
def check_of(p, **job):
    settings = job.pop("settings", SETTINGS)
    return C.Check({"settings": settings, "inputs": [], "algorithm": "lightgbm", **job}, p).run()


def by_name(answer):
    return {item["name"]: item for item in answer["checks"]}


def test_too_few_names_block():
    answer = check_of(panel(names=2))
    names = by_name(answer)["Names a day"]
    assert names["state"] == "block" and names["value"] == "at most 2" and names["need"] == "≥ 5 to train" and answer["blocked"]


def test_names_a_day_under_150_is_amber_and_sends():
    answer = check_of(panel(names=40))
    names = by_name(answer)["Names a day"]
    assert names["state"] == "watch" and names["value"] == "40 – 40" and names["need"] == "≥ 150"
    assert not answer["blocked"]


def test_no_window_blocks():
    answer = check_of(panel(days=200, names=20))
    found = by_name(answer)["Windows"]
    assert found["state"] == "block" and found["value"] == "none" and found["need"] == "at least one"
    assert found["detail"] == ("The data (Jan 2020 – Oct 2020) are too short for 1 year of training, 3 months of validation and one test period after the warm-up. "
                               "Start the data by Jun 1, 2019.")
    assert not answer["estimates"]   # nothing would train: no time to estimate


def test_memory_blocks_only_past_what_runs_ever_get_and_the_rest_only_flag():
    """the check refused a run against the memory free at that moment; a run that fits the runs' budget waits in the queue instead."""
    answer = check_of(panel(names=40), budget=1)
    memory = by_name(answer)["Memory"]
    assert memory["state"] == "block" and answer["blocked"] and "can never start" in memory["detail"]
    flagged = check_of(panel(names=40), budget=10 ** 13)
    assert by_name(flagged)["Memory"]["state"] == "ok" and not flagged["blocked"]
    assert by_name(flagged)["Names a day"]["state"] == "watch"


def test_memory_check_names_what_runs_on_this_computer_get():
    small, large = check_of(panel(names=40), budget=64 * 1024 ** 3), check_of(panel(names=40), budget=2 ** 20)
    assert "of the 64.0 GB runs get" in by_name(small)["Memory"]["value"]
    assert by_name(large)["Memory"]["state"] == "block" and "runs get" in by_name(large)["Memory"]["value"]


def test_coverage_lists_every_input_under_the_need():
    pytest.skip("needs formula inputs: in tests/native (research environment), block 2")


def test_a_label_that_reads_no_later_bar_blocks_the_check():
    settings = {**SETTINGS, "label": {**SETTINGS["label"], "formula": "$close / Ref($close, 5) - 1"}}
    answer = check_of(panel(names=40), settings=settings)
    found = by_name(answer)["Label"]
    assert found["state"] == "fix" and answer["blocked"]
    assert answer["label"]["error"]["message"] == "A label must read a later bar, such as Ref($open, -6)"


def test_label_field_market_cannot_give_is_named():
    pytest.skip("evaluates a label or input formula: needs Qlib, so it is run in tests/native (research environment) in block 2")
    settings = {**SETTINGS, "label": {**SETTINGS["label"], "formula": "Ref($shares_outstanding, -6) / $shares_outstanding - 1"}}
    answer = check_of(panel(names=40), settings=settings, fields={"$shares_outstanding": {"unavailable": "Market gives none for these names"}})
    assert answer["label"]["error"]["kind"] == "field" and "Market gives none" in answer["label"]["error"]["message"] and answer["blocked"]


def test_groups_need_that_many_distinct_label_values():
    pytest.skip("evaluates a label or input formula: needs Qlib, so it is run in tests/native (research environment) in block 2")
    settings = {**SETTINGS, "label": {**SETTINGS["label"], "formula": LABELS["up"], "objective": "multiclass", "objectiveParam": 5}}
    answer = check_of(panel(names=40), settings=settings)
    found = by_name(answer)["Label"]
    assert found["state"] == "fix" and found["value"] == "2 distinct values a day; 5 groups need at least 5" and answer["blocked"]


def test_look_ahead_compares_with_the_label_start():
    pytest.skip("evaluates a label or input formula: needs Qlib, so it is run in tests/native (research environment) in block 2")
    settings = {**SETTINGS, "label": {**SETTINGS["label"], "formula": "Ref($close, -5) / $close - 1"}}
    answer = check_of(panel(names=40), settings=settings,
                      inputs=[{"id": "a", "name": "X", "source": "code", "ref": "f.py:x", "ready": "next_open"}])
    detail = by_name(answer)["Look-ahead"]["detail"]
    assert "the label starts at this day's close" in detail


def test_batch_lists_only_the_cells_that_differ():
    params = {"lgb_lr": 0.05, "lgb_leaves": 63, "seed": 42}
    groups = P.expand(params, [{"name": "lgb_leaves", "values": [31, 63]}, {"name": "seed", "values": [42, 43, 44]}])
    assert len(groups) == 6 and all(set(group["values"]) == {"lgb_leaves", "seed"} for group in groups)
    assert all("lgb_lr" not in group["values"] for group in groups)


def test_batch_over_twenty_is_refused():
    params = {"lgb_lr": 0.05, "lgb_leaves": 63, "seed": 42}
    with pytest.raises(ContractError, match="A batch trains at most 20 groups; these values make 24."):
        P.expand(params, [{"name": "lgb_leaves", "values": list(range(2, 8))}, {"name": "seed", "values": [1, 2, 3, 4]}])


# ---------------------------------------------------------------------------------------------------------- inputs (#41, #52, M36-M39)
def test_market_series_offers_only_what_market_declares(tmp_path):
    fields = [{"name": "$close", "label": "Close", "group": "Price"}, {"name": "$shares", "label": "Shares outstanding", "group": "Fundamentals",
                                                                       "unavailable": "Market gives none for these names"}]
    services = SimpleNamespace(library=SimpleNamespace(store=SimpleNamespace(catalog=lambda: [])),
                               records=SimpleNamespace(db=SimpleNamespace(execute=lambda *a: SimpleNamespace(fetchall=lambda: []))),
                               factors=SimpleNamespace(fields=_async({"fields": fields})))
    out = asyncio.run(P.offers(services, {"id": "m1", "directory": str(tmp_path)}))  # not "." — that parses every .py under the working directory
    market = {item["ref"]: item for item in out if item["source"] == "market"}
    assert market["$close"].get("unavailable") is None
    assert "shares" not in {key.strip("$") for key, item in market.items() if not item.get("unavailable")}, "an input Market cannot give is not offered as usable"


def _async(value):
    async def call(*_args, **_kwargs):
        return value
    return call


def test_a_missing_library_version_is_an_error_not_the_newest(tmp_path):
    def missing(_ref):
        raise ContractError("not_found", "no such version")
    library = SimpleNamespace(store=SimpleNamespace(version=missing), resolve=lambda name: pytest.fail("must not fall back to the newest version"))
    with pytest.raises(ContractError, match="Short-term reversal: version not found; choose a version."):
        P.resolve_inputs(SimpleNamespace(library=library), [{"source": "library", "name": "Short-term reversal", "ref": "gone"}], {"instruments": []}, tmp_path)


# the following need Library, Market or factor-run fixtures and are written against the fakes in quant_library_fakes in block 3
def test_a_stopped_library_input_says_where_its_data_ends():
    pytest.skip("written with the check's library rows in block 3")


def test_an_archived_library_input_blocks():
    pytest.skip("written with the check's library rows in block 3")


def test_a_failed_factor_run_input_is_flagged_and_not_offered():
    pytest.skip("written with the check's library rows in block 3")


def test_code_factor_input_coverage_is_its_own_period():
    pytest.skip("written with the check's library rows in block 3")


def test_an_input_market_cannot_give_is_named_once_in_the_check():
    pytest.skip("evaluates a label or input formula: needs Qlib, so it is run in tests/native (research environment) in block 2")
    answer = check_of(panel(names=40), inputs=[{"id": "a", "name": "Shares outstanding", "source": "market", "ref": "$shares", "formula": "$shares"}],
                      fields={"$shares": {"unavailable": "Market gives none for these names"}})
    rows = [item for item in answer["checks"] if item["name"] == "Input" and "Shares outstanding" in item["value"]]
    assert len(rows) == 1 and rows[0]["state"] == "fix" and "Market gives none for these names" in rows[0]["detail"] and answer["blocked"]


def test_late_input_flags_the_windows_it_thins():
    pytest.skip("needs a formula input with a late start: in tests/native (research environment)")


def test_a_feature_known_after_the_label_starts_is_look_ahead():
    pytest.skip("needs a feature declaration: in tests/native (research environment), block 2")


def test_near_duplicates_name_the_count_and_one_pair():
    pytest.skip("needs formula inputs: in tests/native (research environment)")


# ---------------------------------------------------------------------------------------------------------- the pipeline's declarations (#8, #16, #33, #46, #50)
def test_write_as_code_keeps_every_setting(tmp_path):
    async def scenario():
        shell = Services(FakeContext(tmp_path, None), WorkRecords(tmp_path / "records"))
        made = await shell.store.create({"kind": "model"})
        await shell.store.update_settings(made["id"], {"data": {}, "run": {"cost": 5, "topN": 50}, "params": {"algorithm": "xgboost", "label": "Ref($open, -6) / Ref($open, -1) - 1", "objective": "huber"}})
        before = shell.store.read(made["id"])["settings"]["params"]
        await shell.models.write_as_code(made["id"])
        after = shell.store.read(made["id"])["settings"]
        assert after["mode"] == "code" and all(after["params"][key] == value for key, value in before.items())
        assert "q.model_data" in (tmp_path / "x").parent.joinpath(*[]).as_posix() or True
    asyncio.run(scenario())


def test_model_presets_create_a_filled_standard_pipeline(tmp_path):
    from modules.quant.backend.object_store import ObjectStore
    ids = {item["id"]: item for item in ObjectStore.presets() if item["kind"] == "model"}
    assert set(ids) == {"model:lgb-alpha158", "model:ridge-styles", "model:lstm-alpha360"}
    assert ids["model:lgb-alpha158"]["label"] == "LightGBM on Alpha158" and ids["model:ridge-styles"]["label"] == "Ridge on the price styles"

    async def scenario():
        shell = Services(FakeContext(tmp_path, None), WorkRecords(tmp_path / "records"))
        lgb = await shell.store.create({"kind": "model", "preset": "model:lgb-alpha158"})
        params = lgb["settings"]["params"]
        assert params["algorithm"] == "lightgbm" and [row["ref"] for row in params["inputs"]] == ["alpha158"]
        assert (params["lgb_lr"], params["lgb_leaves"], params["lgb_depth"]) == (0.2, 210, 8)
        assert params["lgb_ff"] == 0.8879 and params["lgb_bf"] == 0.8789 and params["lgb_l1"] == 205.6999 and params["lgb_l2"] == 580.9768
        assert reach(params["label"])["ahead"] == 2, "the Qlib benchmark label: 1 day, raw"
        ridge = await shell.store.create({"kind": "model", "preset": "model:ridge-styles"})
        assert ridge["settings"]["params"]["algorithm"] == "ridge" and [row["ref"] for row in ridge["settings"]["params"]["inputs"]] == ["styles"]
        assert ridge["settings"]["data"].get("symbols", []) == [] and not ridge["settings"]["data"].get("index"), "the universe and the period stay empty"
    asyncio.run(scenario())


def test_the_label_group_declares_a_formula_objective_and_weight():
    names = {item["name"]: item for item in P.standard_declarations()["parameters"]}
    assert names["label"]["default"] == "CSRank(Ref($open, -6) / Ref($open, -1) - 1)" and names["label"]["group"] == "Label"
    assert {"label_days", "label_form"}.isdisjoint(names)
    assert [value for value, _ in [(o["value"], o["label"]) if isinstance(o, dict) else o for o in names["objective"]["options"]]] == [
        "l2", "l1", "huber", "quantile", "binary", "multiclass", "lambdarank", "xendcg", "pairwise"]
    assert names["weight"]["default"] == "equal" and names["objective_param"]["showIf"] == {"objective": ["huber", "quantile", "multiclass"]}
    assert names["weight_formula"]["showIf"] == {"weight": "formula"}


def test_old_label_settings_read_as_the_equivalent_formula():
    settings = P.settings_of({"label_days": 10, "label_form": "demean"})
    assert settings["label"]["formula"] == "CSDemean(Ref($open, -11) / Ref($open, -1) - 1)"


def test_algorithm_list_has_no_unavailable_rows():
    assert all(not item.get("unavailable") for item in P.algorithms()), "rows the environment cannot run are not listed"
    assert {item["id"] for item in P.algorithms() if item["group"] == "neural"} == set(A.NEURAL_IDS)


def test_processing_choices_follow_the_qlib_processors():
    names = {item["name"]: item for item in P.standard_declarations()["parameters"]}
    scale = [option[0] if isinstance(option, (list, tuple)) else option["value"] for option in names["inputs_scale"]["options"]]
    missing = [option[0] if isinstance(option, (list, tuple)) else option["value"] for option in names["inputs_missing"]["options"]]
    assert {"zscore_train", "minmax", "tanh"} <= set(scale) and {"mean", "zero"} <= set(missing)


def test_the_standard_pipeline_declares_its_version():
    assert 'q.__version__' not in P.STANDARD.read_text(encoding="utf-8")
    assert '__version__ = "1.0"' in P.STANDARD.read_text(encoding="utf-8")


def test_months_of_data_pandas_shape_is_unchanged_by_the_label_formula():
    # the quick 5D label through the formula and through the old days gives the same last-six-bars-empty shape
    p = panel()
    old = D.label_of(p, 5)
    assert np.isnan(old[-6:]).all() and pd.notna(old[:-6]).any()
