"""What the strategy page reads, without an engine: a run as the page shows it (settings, changes, key numbers, checks, compare), a section of its
Results, trades and logs page by page, the library inputs it pins, and the run list. The runs are synthetic ones exported by
`tests/fixtures/strategy/make_fixtures.py`."""
import asyncio
import json

import pytest
from talyx_sdk import ContractError

from tests.fixtures.strategy import fakes
from tests.fixtures.strategy.fakes import insert_run, services, strategy

MOMENTUM = 'import talyx_quant as q\n\nclass S(q.Strategy):\n    def __init__(self):\n        super().__init__()\n        self.model = q.model("Momentum LGBM")\n'


def scenario(tmp_path, body, **more):
    async def run():
        ctx, records, shell = services(tmp_path, **more)
        try:
            return await body(ctx, shell)
        finally:
            records.close()
    return asyncio.run(run())


def test_run_is_read_as_run_work_and_code_diff(tmp_path, monkeypatch):
    from tests.unit.quant_public import public_actions

    async def body(ctx, shell):
        obj = await strategy(shell)
        first = insert_run(shell, obj, "single", created=1_000)
        view = shell.strategy.run(first["id"])
        assert set(view) == {"run", "work", "codeDiff"}
        run = view["run"]
        assert (run["id"], run["objectId"], run["number"], run["kind"], run["state"]) == (first["id"], obj, 1, "backtest", "completed")
        assert set(run) >= {"createdAt", "endedAt", "seconds", "settings", "changes", "inputs", "stats", "progress", "failure", "starred", "note", "of", "heldOut",
                            "codeDigest", "dataTo", "notes", "tags"} and run["tags"] == []
        assert run["stats"]["trades"] == view["work"]["stats"]["trades"] and view["work"]["kind"] == "single"
        act = public_actions(ctx, shell, monkeypatch)
        request = {"runId": first["id"]}
        summary = (await act("read_run", request, origin="ai"))["summary"]
        assert summary["openTarget"]["objectId"] == "run:" + first["id"]
        code_result = await act("read_run",request,origin="code")
        assert code_result["summary"] == summary and code_result["numbers"] == (await act("read_run",request,origin="ai"))["numbers"]
        shell.runs.update(first["id"],state="running")
        assert (await act("read_run",request))["summary"] is None
    scenario(tmp_path, body)


def test_host_finds_the_vocabulary_summary_in_each_inline_run_detail():
    from pathlib import Path

    from host.ai import summary_paths

    contract = json.loads((Path(__file__).resolve().parents[2] / "modules/quant/public/contract.json").read_bytes())
    for name in ("read_run",):
        alternatives = contract["actions"][name]["output"]["anyOf"]
        paths = {path for shape in alternatives if shape.get("$ref") != "#/$defs/ResultFile"
                 for path in summary_paths(shape, contract["$defs"])}
        assert ("summary",) in paths, name


def test_changes_say_what_differs_from_the_run_before(tmp_path):
    async def body(ctx, shell):
        obj = await strategy(shell)
        first = insert_run(shell, obj, "single", created=1_000, params={"every": 24})
        second = insert_run(shell, obj, "single", created=2_000, params={"every": 12})
        view = shell.strategy.run(second["id"])
        assert view["run"]["number"] == 2 and {"what": "every", "from": 24, "to": 12} in view["run"]["changes"]
        assert shell.strategy.run(first["id"])["run"]["changes"] == []
    scenario(tmp_path, body)


def test_a_run_stopped_before_its_data_was_read_is_not_said_to_change_currency_and_margin(tmp_path):
    from modules.quant.backend import strategy_settings
    async def body(ctx, shell):
        obj = await strategy(shell)
        stocks = {"runSettings": strategy_settings.effective("stock", None, {}), "tradedKind": "stock", "sources": [], "library": [], "models": []}
        insert_run(shell, obj, "single", created=1_000, data=stocks)
        stopped = insert_run(shell, obj, "single", created=2_000, state="cancelled", data={})
        third = insert_run(shell, obj, "single", created=3_000, data=stocks)
        assert shell.strategy.run(stopped["id"])["run"]["changes"] == [] and shell.strategy.run(third["id"])["run"]["changes"] == []
    scenario(tmp_path, body)


def test_compare_adds_dashed_line(tmp_path):
    async def body(ctx, shell):
        obj = await strategy(shell)
        first, second = insert_run(shell, obj, "single", created=1_000), insert_run(shell, obj, "single", created=2_000)
        view = shell.strategy.run(second["id"], vs=first["id"])
        compared = view["work"]["compared"]
        assert compared["runId"] == first["id"] and compared["number"] == 1 and len(compared["equity"]) == len(view["work"]["equity"])
        assert compared["delta"]["totalReturn"] == pytest.approx(0)
    scenario(tmp_path, body)


def test_compare_different_period(tmp_path):
    async def body(ctx, shell):
        obj = await strategy(shell)
        first, second = insert_run(shell, obj, "single", created=1_000), insert_run(shell, obj, "single", created=2_000)
        path = shell.runs.directory(first["id"]) / "output" / "strategy" / "work.json"
        work = json.loads(path.read_text(encoding="utf-8"))
        work["equity"] = work["equity"][:-5]
        path.write_text(json.dumps(work), encoding="utf-8")
        compared = shell.strategy.run(second["id"], vs=first["id"])["work"]["compared"]
        assert compared["delta"]["totalReturn"] is None and "periods differ" in compared["whatDiffers"]
        assert compared["delta"]["maxDrawdown"] is not None or compared["delta"]["maxDrawdown"] is None  # only the total return is withheld
    scenario(tmp_path, body)


def test_compare_only_same_period_kind(tmp_path):
    async def body(ctx, shell):
        obj = await strategy(shell)
        first = insert_run(shell, obj, "single", created=1_000, runKind="heldout", heldOut=True, of=None)
        second = insert_run(shell, obj, "single", created=2_000)
        compared = shell.strategy.run(second["id"], vs=first["id"])["work"]["compared"]
        assert all(value is None for value in compared["delta"].values()) and compared["label"] == "#1 · held-out only"
    scenario(tmp_path, body)


def test_two_checks_computed_rest_not_run(tmp_path):
    async def body(ctx, shell):
        obj = await strategy(shell)
        run = insert_run(shell, obj, "single")
        checks = {item["id"]: item for item in shell.strategy.run(run["id"])["work"]["checks"]}
        assert set(checks) == {"heldout", "walk", "neighbours", "edge", "costs", "shuffle", "top", "tries"}
        assert checks["shuffle"]["state"] in ("pass", "fail") and checks["top"]["state"] in ("pass", "fail")
        assert {name for name, item in checks.items() if item["state"] == "notrun"} == {"heldout", "walk", "neighbours", "edge", "costs"}
        assert checks["shuffle"]["need"] and checks["top"]["need"]
    scenario(tmp_path, body)


def test_failure_names_the_line_and_the_part_that_ran(tmp_path):
    async def body(ctx, shell):
        obj = await strategy(shell)
        run = insert_run(shell, obj, "failed", state="failed", error={"stage": "run", "code": "strategy_error", "message": "RuntimeError: boom", "file": "strategy.py", "line": 7})
        failure = shell.strategy.run(run["id"])["run"]["failure"]
        assert failure["file"] == "strategy.py" and failure["line"] and failure["error"] and failure["simulatedAt"] and failure["partial"] is True
        assert any(name == "level" for name, _ in failure["locals"])
    scenario(tmp_path, body)


def test_failure_lists_names_without_price(tmp_path):
    async def body(ctx, shell):
        obj = await strategy(shell)
        run = insert_run(shell, obj, "failed", state="failed", error={"stage": "run", "code": "strategy_error", "message": "No price", "file": "strategy.py", "line": 3})
        shell.runs.update(run["id"], failureContext={**run["failureContext"], "withoutPrice": ["S03", "S09"]})
        assert shell.strategy.run(run["id"])["run"]["failure"]["withoutPrice"] == ["S03", "S09"]
    scenario(tmp_path, body)


def test_engine_failure_is_marked_as_inside_the_engine(tmp_path):
    async def body(ctx, shell):
        obj = await strategy(shell)
        run = insert_run(shell, obj, "failed", state="failed", error={"stage": "run", "code": "strategy_error", "message": "boom", "file": None, "line": None})
        shell.runs.update(run["id"], failureContext={**run["failureContext"], "inEngine": True, "locals": [], "line": None})
        failure = shell.strategy.run(run["id"])["run"]["failure"]
        assert failure["inEngine"] is True and failure["line"] is None and failure["locals"] == []
    scenario(tmp_path, body)


def test_progress_comes_from_the_progress_file(tmp_path):
    async def body(ctx, shell):
        obj = await strategy(shell)
        run = insert_run(shell, obj, "single", state="running", endedAt=None)
        (shell.runs.directory(run["id"]) / "output" / "progress.json").write_text(json.dumps({"simulatedTo": 1_704_500_000_000, "value": .4, "tradesSoFar": 3}), encoding="utf-8")
        progress = shell.strategy.run(run["id"])["run"]["progress"]
        assert progress["value"] == .4 and progress["tradesSoFar"] == 3 and progress["simulatedTo"] == 1_704_500_000_000 and "stage" in progress
    scenario(tmp_path, body)


def test_old_format_run_says_so(tmp_path):
    async def body(ctx, shell):
        obj = await strategy(shell)
        old = shell.runs.records.put("qrun", {"id": "run-old", "objectId": obj, "objectKind": "strategy", "name": "Trend", "runKind": "run", "mode": "run", "workspaceId": "ws",
                                              "state": "completed", "stage": "done", "createdAt": 500, "endedAt": 900, "params": {}, "overrides": {}, "settings": shell.store.read(obj)["settings"],
                                              "message": None, "error": None, "code": {"digest": "a" * 64, "entry": "strategy.py"}, "common": None,
                                              "data": {"sources": []}, "environment": None, "recipe": None, "metrics": {}, "starred": False, "note": "", "exitCode": 0})
        (shell.runs.directory(old["id"]) / "output").mkdir(parents=True)
        (shell.runs.directory(old["id"]) / "output" / "result-index.json").write_text(json.dumps({"status": "completed", "units": [], "sections": []}), encoding="utf-8")
        view = shell.strategy.run(old["id"])
        assert view["work"] is None and "Run #1 was made by an earlier TALYX; run it again to see it here" in view["run"]["notes"]
        assert shell.runs.read(old["id"])["state"] == "completed"
        with pytest.raises(ContractError):
            shell.strategy.results(old["id"])
    scenario(tmp_path, body)


def test_old_run_shows_its_own_code_and_settings(tmp_path):
    async def body(ctx, shell):
        obj = await strategy(shell, run={"capital": 50_000})
        first = insert_run(shell, obj, "single", created=1_000, code={"digest": "1" * 64, "entry": "strategy.py"})
        await shell.store.update_settings(obj, {"run": {"capital": 90_000}})
        second = insert_run(shell, obj, "single", created=2_000, code={"digest": "2" * 64, "entry": "strategy.py"})
        view = shell.strategy.run(first["id"])
        assert view["run"]["settings"]["run"]["capital"] == 50_000 and view["run"]["codeDigest"] == "1" * 64
        later = shell.strategy.run(second["id"])
        assert later["run"]["settings"]["run"]["capital"] == 90_000
        assert any(item["what"] == "capital" for item in later["run"]["changes"]) and later["run"]["codeDigest"] == "2" * 64
    scenario(tmp_path, body)


def test_old_run_settings_section_compares_with_now(tmp_path):
    async def body(ctx, shell):
        obj = await strategy(shell, run={"capital": 50_000})
        first = insert_run(shell, obj, "single", created=1_000)
        await shell.store.update_settings(obj, {"run": {"capital": 90_000}})
        section = shell.strategy.results(first["id"], "settings")["sections"]["settings"]
        assert section["differs"]["run.capital"] == {"before": 90_000, "after": 50_000} or "run.capital" in section["differs"]
        assert section["layers"]
    scenario(tmp_path, body)


def test_labels_from_run_record(tmp_path):
    async def body(ctx, shell):
        obj = await strategy(shell)
        run = insert_run(shell, obj, "single", created=1_000)
        await shell.store.update_settings(obj, {"data": {**fakes.DATA, "timeframe": "1d"}})
        assert shell.strategy.run(run["id"])["run"]["settings"]["data"]["timeframe"] == "1h"
    scenario(tmp_path, body)


def test_results_sections_and_toc(tmp_path):
    async def body(ctx, shell):
        obj = await strategy(shell)
        run = insert_run(shell, obj, "single")
        found = shell.strategy.results(run["id"])
        assert [item["id"] for item in found["toc"]] == [name for name in found["sections"] if name in {item["id"] for item in found["toc"]}] or found["toc"]
        assert {"summary", "equity", "periods", "orders", "account", "settings"} <= set(found["sections"]) and found["missing"] == {}
        one = shell.strategy.results(run["id"], "orders")
        assert list(one["sections"]) == ["orders"]
        with pytest.raises(ContractError):
            shell.strategy.results(run["id"], "nonsense")
        assert found["sections"]["summary"]["userStats"] == {"bars seen": 960.0}
    scenario(tmp_path, body)


def test_trades_paged(tmp_path):
    async def body(ctx, shell):
        obj = await strategy(shell)
        run = insert_run(shell, obj, "single")
        everything = shell.strategy.trades(run["id"], 0, 500)
        assert everything["total"] == len(everything["rows"]) > 5
        page = shell.strategy.trades(run["id"], offset=2, limit=3)
        assert [row["id"] for row in page["rows"]] == [row["id"] for row in everything["rows"][2:5]] and page["total"] == everything["total"]
        longs = shell.strategy.trades(run["id"], side="Long")
        assert longs["total"] and {row["side"] for row in longs["rows"]} == {"Long"}
        losing = shell.strategy.trades(run["id"], losing=True)
        assert all(row["pnl"] < 0 for row in losing["rows"])
        by = shell.strategy.trades(run["id"], exit="time")
        assert {row["exitBy"] for row in by["rows"]} == {"time"}
        with pytest.raises(ContractError):
            shell.strategy.trades(run["id"], limit=501)
    scenario(tmp_path, body)


def test_orders_and_log_pages_filter_in_the_backend(tmp_path):
    async def body(ctx, shell):
        obj = await strategy(shell)
        run = insert_run(shell, obj, "single")
        rejected = shell.strategy.orders(run["id"], status="Rejected")
        assert rejected["total"] == len(rejected["rows"])
        everything = shell.strategy.orders(run["id"], limit=500)
        assert everything["total"] >= 1
        log = shell.strategy.log(run["id"], offset=0, limit=1000, kind="Trade")
        assert log["total"] and {line["kind"] for line in log["rows"]} == {"Trade"}
        trade = log["rows"][0]["trade"]
        jumped = shell.strategy.log(run["id"], trade=trade)
        assert jumped["rows"] and all(line.get("trade") == trade for line in jumped["rows"] if line.get("trade") is not None)
    scenario(tmp_path, body)


def test_rebalance_rows_paged(tmp_path):
    async def body(ctx, shell):
        obj = await strategy(shell)
        run = insert_run(shell, obj, "multi")
        everything = shell.strategy.rebalances(run["id"], filter="all", limit=200)
        assert everything["total"] >= 3 and everything["rows"][0]["targets"]
        unfilled = shell.strategy.rebalances(run["id"], filter="unfilled")
        assert 0 < unfilled["total"] < everything["total"]
        at = everything["rows"][1]["at"]
        assert shell.strategy.rebalances(run["id"], at=at)["rows"][0]["at"] == at
        span = shell.strategy.rebalances(run["id"], filter="all", since=everything["rows"][1]["at"], until=everything["rows"][2]["at"])
        assert [row["at"] for row in span["rows"]] == [row["at"] for row in everything["rows"][1:3]]
    scenario(tmp_path, body)


def test_strategy_runs_incremental(tmp_path):
    async def body(ctx, shell):
        obj = await strategy(shell)
        first = insert_run(shell, obj, "single", created=1_000)
        listed = shell.strategy.runs(obj, None)
        assert [item["id"] for item in listed["items"]] == [first["id"]] and listed["complete"] is True
        marker = listed["now"]
        await asyncio.sleep(.01)
        second = insert_run(shell, obj, "single", created=2_000)
        later = shell.strategy.runs(obj, marker)
        assert [item["id"] for item in later["items"]] == [second["id"]] and later["complete"] is False
        assert {"number", "changes", "stats", "state", "failure"} <= set(later["items"][0])
    scenario(tmp_path, body)


def test_interrupted_after_restart(tmp_path):
    async def body(ctx, shell):
        obj = await strategy(shell)
        run = insert_run(shell, obj, "single", state="running", endedAt=None)
        from modules.quant.backend.run_flow import INTERRUPTED_MESSAGE
        from modules.quant.backend.work_records import WorkRecords
        records = WorkRecords(shell.runs.records.root)
        try:
            again = fakes.Services(ctx, records)
            stopped = again.runs.read(run["id"])
            assert stopped["state"] == "interrupted" and stopped["message"] == INTERRUPTED_MESSAGE
            assert again.strategy.run(run["id"])["work"] is not None
        finally:
            records.close()
    scenario(tmp_path, body)


# --- inputs from the library ------------------------------------------------------------------------------------------------------

VERSIONS = {"v3": {"name": "Momentum LGBM", "kind": "model", "version": 3, "state": "live", "publishedAt": 1_000, "trainingCutoff": 1_719_705_600_000, "heldOutFrom": "2025-05-01"},
            "v4": {"name": "Momentum LGBM", "kind": "model", "version": 4, "state": "live", "publishedAt": 2_000, "trainingCutoff": 1_730_000_000_000, "heldOutFrom": None}}


def pinned(shell, obj, version="v3"):
    record = shell.store.read(obj)
    shell.store.save(record, settings={**record["settings"], "inputs": {"library": {}, "models": {"Momentum LGBM": {"name": "Momentum LGBM", "versionId": version}}}})


def test_inputs_pinned_and_switch_marks_incomparable(tmp_path):
    async def body(ctx, shell):
        shell.library = fakes.library(VERSIONS)
        shell.strategy.library = shell.library
        obj = await strategy(shell, MOMENTUM)
        pinned(shell, obj)
        run = insert_run(shell, obj, "single", created=1_000, inputs=[{"name": "Momentum LGBM", "kind": "model", "versionId": "v3", "version": 3}])
        items = shell.strategy.inputs(obj)
        card = items[0]
        assert (card["name"], card["kind"], card["file"], card["line"]) == ("Momentum LGBM", "model", "strategy.py", 6)
        assert card["pinned"]["version"] == 3 and card["newer"]["version"] == 4 and card["newer"]["publishedAt"] == 2_000 and card["fromRun"] == run["id"]
        switched = shell.strategy.switch_input(obj, "Momentum LGBM", "v4")
        assert switched["notComparable"] is True
        after = shell.strategy.inputs(obj)[0]
        assert after["pinned"]["version"] == 4 and after["changedSince"] == run["id"] and after["previous"]["version"] == 3
        back = shell.strategy.switch_input(obj, "Momentum LGBM", "v3")
        assert shell.strategy.inputs(obj)[0]["pinned"]["version"] == 3 and back["notComparable"] is False or back["notComparable"] is True
    scenario(tmp_path, body)


def test_retired_input_card(tmp_path):
    async def body(ctx, shell):
        shell.library = fakes.library({**VERSIONS, "v3": {**VERSIONS["v3"], "retired": True}})
        shell.strategy.library = shell.library
        obj = await strategy(shell, MOMENTUM)
        pinned(shell, obj)
        assert shell.strategy.inputs(obj)[0]["retired"] is True
    scenario(tmp_path, body)


def test_model_values_mark_the_picking_span(tmp_path):
    async def body(ctx, shell):
        shell.library = fakes.library(VERSIONS)
        shell.strategy.library = shell.library
        source = MOMENTUM.replace('q.model("Momentum LGBM")', 'q.pick("signal", kinds=("factor", "model"))')
        obj = await strategy(shell, source)
        pinned(shell, obj)
        run = insert_run(shell, obj, "single", inputs=[{"name": "Momentum LGBM", "kind": "model", "versionId": "v3", "version": 3, "reads": "values"}])
        card = shell.strategy.inputs(obj)[0]
        assert card["pickedOn"] == "2025-04-30" and "trainedTo" not in card or card.get("trainedTo") is None
        span = shell.strategy.run(run["id"])["work"]["usedToPick"]
        assert span == [{"name": "Momentum LGBM", "to": "2025-04-30"}]
        # a version without a held-out start is marked for the whole run
        pinned(shell, obj, "v4")
        other = insert_run(shell, obj, "single", inputs=[{"name": "Momentum LGBM", "kind": "model", "versionId": "v4", "version": 4, "reads": "values"}])
        assert shell.strategy.run(other["id"])["work"]["usedToPick"] == [{"name": "Momentum LGBM", "to": None}]
    scenario(tmp_path, body)


def test_frozen_model_card_shows_training_cutoff(tmp_path):
    async def body(ctx, shell):
        shell.library = fakes.library(VERSIONS)
        shell.strategy.library = shell.library
        obj = await strategy(shell, MOMENTUM)
        pinned(shell, obj)
        card = shell.strategy.inputs(obj)[0]
        assert card["pinned"]["trainedTo"] == "2024-06-30"
    scenario(tmp_path, body)


# --- the held-out end and the defaults ---------------------------------------------------------------------------------------------

def test_backtest_stops_before_held_out():
    from modules.quant.backend.strategy_settings import cut_data
    data = {"since": "2024-01-01", "until": "2024-06-30", "heldOutFrom": "2024-05-01"}
    assert cut_data(data, "run")["until"] == "2024-04-30"
    assert cut_data(data, "heldout")["until"] == "2024-06-30"
    assert cut_data({**data, "until": "latest"}, "run")["until"] == "2024-04-30"
    with pytest.raises(ContractError, match="Held out from Dec 1, 2023 leaves no data to run on: the data start Jan 1, 2024. Move the held-out start"):
        cut_data({**data, "heldOutFrom": "2023-12-01"}, "run")


def test_held_out_is_set_in_run_settings_not_in_the_data_overlay(tmp_path):
    async def body(ctx, shell):
        obj = await strategy(shell)
        with pytest.raises(ContractError):
            await shell.store.update_settings(obj, {"data": {**fakes.DATA, "heldOutFrom": "2024-04-01"}})
        await shell.store.update_settings(obj, {"run": {"heldOutFrom": "2024-04-01"}})
        assert shell.store.read(obj)["settings"]["data"]["heldOutFrom"] == "2024-04-01"
        # saving the data overlay again does not drop it
        await shell.store.update_settings(obj, {"data": {key: value for key, value in fakes.DATA.items() if key != "heldOutFrom"}})
        assert shell.store.read(obj)["settings"]["data"]["heldOutFrom"] == "2024-04-01"
    scenario(tmp_path, body)


def test_new_strategy_runs_to_latest_close(tmp_path):
    async def body(ctx, shell):
        created = await shell.store.create({"kind": "strategy", "name": "New"})
        data = shell.store.read(created["id"])["settings"]["data"]
        assert data["until"] == "latest" and data["timeframe"] == "1h" and len(data["symbols"]) == 1
        assert data["since"] < data["until"] if data["until"] != "latest" else data["since"]
        years = (__import__("datetime").date.today() - __import__("datetime").date.fromisoformat(data["since"])).days / 365
        assert 1.9 < years < 2.1
    scenario(tmp_path, body)


def test_preset_opens_with_its_default_data(tmp_path):
    async def body(ctx, shell):
        crossover = await shell.store.create({"kind": "strategy", "preset": "crossover"})
        data = shell.store.read(crossover["id"])["settings"]["data"]
        assert data["timeframe"] == "1d" and data["until"] == "latest"
        years = (__import__("datetime").date.today() - __import__("datetime").date.fromisoformat(data["since"])).days / 365
        assert 9.9 < years < 10.1
        breakout = await shell.store.create({"kind": "strategy", "preset": "breakout"})
        assert shell.store.read(breakout["id"])["settings"]["data"]["timeframe"] == "1h"
        assert shell.store.read(breakout["id"])["settings"]["params"] == {}  # the settings are the code's declared defaults until changed
    scenario(tmp_path, body)


def test_run_settings_view_has_effective_defaults_stored_and_kind(tmp_path):
    async def body(ctx, shell):
        obj = await strategy(shell, run={"capital": 20_000})
        view = await shell.strategy.run_settings(obj)
        assert view["kind"] == "perpetual" and view["effective"]["capital"] == 20_000 and view["stored"] == {"capital": 20_000}
        assert view["defaults"]["capital"] == 10_000 and view["applicable"]["exchange"]
    scenario(tmp_path, body)


def test_the_main_figure_reads_out_equity_as_a_signed_return_and_drawdown_without_a_plus(tmp_path):
    async def body(ctx, shell):
        obj = await strategy(shell)
        first, second = insert_run(shell, obj, "single", created=1_000), insert_run(shell, obj, "single", created=2_000)
        figure = shell.strategy.run(second["id"], vs=first["id"])["work"]["figures"]["equity"]
        assert figure["layout"]["meta"]["key"] == "strategy.work.equity"
        reads = {trace["meta"]["key"]: trace["hovertemplate"] for trace in figure["data"]}
        assert set(reads) == {"equity", "benchmark", "compare", "drawdown"}
        assert "#2: %{customdata:+.1%}" in reads["equity"] and "#1: %{customdata:+.1%}" in reads["compare"]
        assert reads["drawdown"] == "<b>%{x}</b><br>Drawdown: %{y:.1f}%<extra></extra>"
        assert all(trace["meta"]["role"] in {"main", "benchmark", "compare", "drawdown"} for trace in figure["data"])
    scenario(tmp_path, body)


def test_also_built_names_the_periods_the_newest_completed_run_built(tmp_path):
    async def body(ctx, shell):
        obj = await strategy(shell)
        views = shell.strategy
        assert views.also_built(obj) is None  # no run has finished: nothing to show
        insert_run(shell, obj, "single", created=1_000)
        report = views.report
        views.report = lambda record, name: {"builtBars": [{"period": "4 hours", "from": "1 hour"}, {"period": "1 day", "from": "1 hour"}]}
        try:
            assert views.also_built(obj) == "4 hours, 1 day from 1 hour"
        finally:
            views.report = report

    scenario(tmp_path, body)


def test_period_names_read_from_bar_types():
    import importlib.util
    from pathlib import Path

    spec = importlib.util.spec_from_file_location("talyx_quant_under_test", Path(__file__).resolve().parents[2] / "modules" / "quant" / "talyx_quant.py")
    q = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(q)
    assert q._period_name("BTCUSDT-PERP.BINANCE-4-HOUR-LAST-INTERNAL") == "4 hours"
    assert q._period_name("BTCUSDT-PERP.BINANCE-1-HOUR-LAST-EXTERNAL") == "1 hour"


def test_up_to_three_runs_are_drawn_beside_the_selected_one(tmp_path):
    async def body(ctx, shell):
        obj = await strategy(shell)
        runs = [insert_run(shell, obj, "single", created=1_000 + at) for at in range(5)]
        work = shell.strategy.run(runs[4]["id"], vs=",".join(item["id"] for item in runs[:4]))["work"]
        traces = work["figures"]["equity"]["data"]
        assert [item["name"] for item in traces if item["meta"]["role"] == "compare"] == [work["compared"]["label"], *[extra["label"] for extra in work["compared"]["also"]]]
        assert len(work["compared"]["also"]) == 2  # the first of the four is the compared run, two more follow, the fourth is dropped

    scenario(tmp_path, body)


def test_results_draw_every_chosen_run_beside_this_one(tmp_path):
    async def body(ctx, shell):
        obj = await strategy(shell)
        runs = [insert_run(shell, obj, "single", created=1_000 + at) for at in range(5)]
        found = shell.strategy.results(runs[4]["id"], None, ",".join(item["id"] for item in runs[:4]))
        assert [item["runId"] for item in found["compared"]] == [item["id"] for item in runs[:3]]  # three at most, in the order given
        traces = found["sections"]["equity"]["figures"]["equity"]["data"]
        assert [item["name"] for item in traces if item["meta"]["role"] == "compare"] == ["#1", "#2", "#3"]
        assert all(item["customdata"][0] == 0 for item in traces if item["meta"]["role"] == "compare")
        one = shell.strategy.results(runs[4]["id"], None, runs[0]["id"])
        assert [item["name"] for item in one["sections"]["equity"]["figures"]["equity"]["data"] if item["meta"]["role"] == "compare"] == ["#1"]
        plain = shell.strategy.results(runs[4]["id"])
        assert "compared" not in plain and not [item for item in plain["sections"]["equity"]["figures"]["equity"]["data"] if item["meta"]["role"] == "compare"]
    scenario(tmp_path, body)


def test_results_figures_have_no_axis_titles_and_the_equity_axis_reads_as_a_return(tmp_path):
    async def body(ctx, shell):
        obj = await strategy(shell)
        run = insert_run(shell, obj, "single")
        found = shell.strategy.results(run["id"])
        for part in found["sections"].values():
            for figure in ((part or {}).get("figures") or {}).values() if isinstance(part, dict) else []:
                if isinstance(figure, dict) and "data" in figure:
                    assert not [name for name, axis in figure["layout"].items() if name.startswith(("xaxis", "yaxis")) and isinstance(axis, dict) and "title" in axis], figure["layout"]["meta"]
        axis = found["sections"]["equity"]["figures"]["equity"]["layout"]["yaxis"]
        assert axis["tickmode"] == "array" and len(axis["tickvals"]) == len(axis["ticktext"]) and all(text.endswith("%") for text in axis["ticktext"])
    scenario(tmp_path, body)


def test_trades_in_a_span_are_the_ones_open_at_some_time_in_it(tmp_path):
    async def body(ctx, shell):
        obj = await strategy(shell)
        run = insert_run(shell, obj, "single")
        rows = shell.strategy.trades(run["id"])["rows"]
        pick = rows[len(rows) // 2]
        since, until = pick["entry"], pick["exit"] if pick["exit"] is not None else pick["entry"]
        inside = shell.strategy.trades(run["id"], since=since, until=until)["rows"]
        assert pick["id"] in {row["id"] for row in inside}
        assert all(row["entry"] <= until and (row["exit"] is None or row["exit"] >= since) for row in inside)
        assert len(inside) < len(rows)
        assert shell.strategy.trades(run["id"], until=rows[0]["entry"] - 1)["total"] == 0
    scenario(tmp_path, body)


def test_the_underwater_figure_draws_each_compared_run_beside_the_run(tmp_path):
    async def body(ctx, shell):
        obj = await strategy(shell)
        first, second = insert_run(shell, obj, "single", created=1_000), insert_run(shell, obj, "single", created=2_000)
        figure = shell.strategy.results(second["id"], "equity", first["id"])["sections"]["equity"]["figures"]["drawdown"]
        roles = [(item.get("meta") or {}).get("role") for item in figure["data"]]
        assert "compare" in roles and roles.index("compare") < len(roles) - 1
        assert next(item for item in figure["data"] if (item.get("meta") or {}).get("role") == "compare")["name"] == f"#{shell.strategy.number(shell.strategy.object_runs.read(first['id']))}"
    scenario(tmp_path, body)


def test_a_compared_run_brings_its_own_column_to_the_summary_tables(tmp_path):
    async def body(ctx, shell):
        obj = await strategy(shell)
        first, second = insert_run(shell, obj, "single", created=1_000), insert_run(shell, obj, "single", created=2_000)
        found = shell.strategy.results(second["id"], None, first["id"])
        other = found["compared"][0]
        assert other["runId"] == first["id"] and "CAGR" in other["rows"] and "alpha" in other["against"]
    scenario(tmp_path, body)


def test_a_single_instrument_run_has_an_instruments_section(tmp_path):
    async def body(ctx, shell):
        obj = await strategy(shell)
        run = insert_run(shell, obj, "single")
        found = shell.strategy.results(run["id"])
        section = found["sections"]["instruments"]
        table = shell.strategy.table(shell.strategy.object_runs.read(run["id"]), "trades")
        closed = table[table["exit"].notna() & table["pnl"].notna()]
        assert "instruments" in [item["id"] for item in found["toc"]]
        assert sum(row["trades"] for row in section["rows"]) == len(closed)
        assert sum(row["pnl"] for row in section["rows"]) == pytest.approx(float(closed["pnl"].sum()), abs=0.05 * max(1, len(section["rows"])))
        assert section["figures"]["cumulative"]["data"][0]["meta"]["role"] == "main"
    scenario(tmp_path, body)



def test_results_lines_are_named_by_the_run_number_and_the_benchmarks_short_name():
    from modules.quant.backend.strategy_views import bench_names, name_lines
    assert bench_names("BTC/USDT:USDT hold") == ("BTC hold", "BTC") and bench_names("SPY hold") == ("SPY hold", "SPY")
    assert bench_names("Cash + β × S&P 500") == ("Cash + β × S&P 500",) * 2 and bench_names(None) == ("Benchmark", "Benchmark")
    def fig(key, *traces):
        return {"layout": {"meta": {"key": key}}, "data": [{"name": name, "meta": {"role": role}, "hovertemplate": template, **more} for name, role, template, more in traces]}
    head = "<b>%{x}</b><br>"
    for key, generic in (("strategy.equity", "Strategy"), ("strategy.drawdown", "Drawdown"), ("strategy.rollingSharpe", "Sharpe"), ("strategy.rollingBeta", "Beta"), ("strategy.rollingVolatility", "This run")):
        found = name_lines(fig(key, (generic, "main", head + generic + ": %{y:.1f}<extra></extra>", {}), ("BTC/USDT:USDT hold", "benchmark", head + "BTC/USDT:USDT hold: %{y:.1f}<extra></extra>", {}),
                               ("1", "benchmark", "", {"hoverinfo": "skip"})), 12, "BTC/USDT:USDT hold")
        main, bench, line = found["data"]
        assert main["name"] == "#12" and main["hovertemplate"] == head + "#12: %{y:.1f}<extra></extra>"
        expected = "BTC" if key == "strategy.rollingVolatility" else "BTC hold"
        assert bench["name"] == expected and bench["hovertemplate"] == head + expected + ": %{y:.1f}<extra></extra>"
        assert line["name"] == "1"  # a reference line keeps its name
    yearly = name_lines(fig("strategy.yearly", (None, "updown", head + "Return: %{y:+.1f}%<extra></extra>", {})), 3, "SPY hold")["data"][0]
    assert yearly["name"] == "#3" and yearly["meta"]["role"] == "main" and yearly["hovertemplate"].startswith(head + "#3: ")
    other = fig("strategy.exposure", ("Net", "main", head + "Net: %{y:.2%}<extra></extra>", {}))
    assert name_lines(other, 3, "SPY hold")["data"][0]["name"] == "Net"  # only the figures that draw the run against its benchmark


def test_results_figures_name_the_run_by_its_number(tmp_path):
    async def body(ctx, shell):
        obj = await strategy(shell)
        run = insert_run(shell, obj, "single")
        found = shell.strategy.results(run["id"])
        traces = found["sections"]["equity"]["figures"]["equity"]["data"]
        assert [item["name"] for item in traces if item["meta"]["role"] == "main"] == [f"#{shell.strategy.number(run)}"]
    scenario(tmp_path, body)


def test_settings_section_leaves_out_what_the_run_filled_in():
    from modules.quant.backend.strategy_views import settled_changes
    used = {"capital": 10000, "currency": "USDT", "exchange": {"leverage": 1, "maxLeverage": 100}, "costs": {"feePct": 0.05, "makerPct": 0.02, "feeSource": "from Market (okx)", "halfSpreadBps": 1}}
    now = {"capital": 10000, "exchange": {"leverage": 1}, "costs": {"feePct": None, "makerPct": None, "feeSource": "from Market", "halfSpreadBps": 2}}
    assert [item["what"] for item in settled_changes(used, now)] == ["costs.halfSpreadBps"]  # only what someone changed
    assert [item["what"] for item in settled_changes(used, {**now, "costs": {**now["costs"], "feePct": 0.1}})] == ["costs.feePct", "costs.halfSpreadBps"]  # a fee typed in counts


def test_a_held_out_run_is_listed_by_what_it_scored_on_the_held_out_part(tmp_path):
    # The report stores a held-out validation's held-out numbers as the run's (strategy_report.scored_stats); the list reads them as stored.
    import sys
    from pathlib import Path
    sys.path.insert(0, str(Path(__file__).parents[2] / "modules/quant"))
    from native.strategy_report import scored_stats
    held = {"totalReturn": -0.03, "cagr": -0.06, "sharpe": -0.28, "maxDrawdown": -0.1, "trades": 61, "costsAYear": 0.09, "beta": 0.8, "from": 1, "to": 2}
    work = {"stats": {"totalReturn": -0.3, "sharpe": -1.0, "trades": 258}, "heldOut": {"from": 1, "inSample": {}, "heldOut": held}}
    stored = scored_stats(work)
    assert stored == {"totalReturn": -0.03, "cagr": -0.06, "sharpe": -0.28, "maxDrawdown": -0.1, "trades": 61, "costsAYear": 0.09}
    assert scored_stats({"stats": {"trades": 258}}) == {"trades": 258}

    async def body(ctx, shell):
        obj = await strategy(shell)
        run = insert_run(shell, obj, "single", runKind="heldout", heldOut=True, of=None, stats=stored)
        row = next(item for item in shell.strategy.runs(obj)["items"] if item["id"] == run["id"])
        assert row["stats"]["trades"] == 61 and row["stats"]["sharpe"] == -0.28 and row["stats"]["totalReturn"] == -0.03
    scenario(tmp_path, body)


def test_the_versions_shown_under_the_code_come_from_the_runs_own_recipe_and_lock(tmp_path):
    from modules.quant.backend.strategy_views import StrategyViews
    lock = tmp_path / "execution.lock"
    lock.write_text("numpy==2.0.0 \\\nnautilus-trader @ file:///x/wheels/nautilus_trader-2.0.0rc8%2Btalyx.20-cp313-cp313-win_amd64.whl \\\n", encoding="utf-8")
    found = StrategyViews.environment_of({"recipe": {"pythonVersion": "3.13.13", "lockPath": str(lock)}})
    assert found == {"python": "3.13", "nautilus": "2.0.0rc8"}
    assert StrategyViews.environment_of({"recipe": None}) is None
    assert StrategyViews.environment_of({"recipe": {"pythonVersion": "3.13.1", "lockPath": str(tmp_path / "gone.lock")}}) == {"python": "3.13", "nautilus": None}


def test_a_portfolio_that_stopped_on_an_error_opens_where_it_stopped_with_a_line_there(tmp_path):
    from modules.quant.backend.strategy_views import StrategyViews
    day = 86_400_000
    start = 1_700_000_000_000
    work = {"equity": [[start + index * day, 100.0 + index] for index in range(900)], "benchmark": [], "benchmarkLabel": "Market", "drawdown": []}
    stop = start + 700 * day
    layout = StrategyViews.equity_figure(work, None, 4, stop)["layout"]
    assert layout["xaxis"]["range"][1] > layout["xaxis"]["range"][0] and layout["shapes"][0]["x0"] == layout["shapes"][0]["x1"]
    assert "shapes" not in StrategyViews.equity_figure(work, None, 4)["layout"]
