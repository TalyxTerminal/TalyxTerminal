"""What the Agent reads of Quant stays inside the platform's response budget at real size (a 600-name data setting, a long run history,
a notebook with outputs), and carries what it needs to read on; the screen's answers are not cut."""
import asyncio
import json
from pathlib import Path
from types import SimpleNamespace

import numpy as np
from talyx_sdk import wire

from modules.quant.backend.object_routes import handle_actions
from modules.quant.backend.run_materials import RunMaterials
from tests.unit.test_quant_factor_runs import DATA, services, settled

LIMIT = 18_000
AI = SimpleNamespace(caller=SimpleNamespace(origin="ai", module=None, workspace_id="ws", ai={"client": "Agent"}, run=None))
SCREEN = SimpleNamespace(caller=SimpleNamespace(origin="user", module=None, workspace_id="ws", ai=None, run=None))
KERNEL = SimpleNamespace(caller=SimpleNamespace(origin="code", module="quant", workspace_id="ws", ai=None, run={"context": None}))


def size(value):
    return len(wire.encode(value).decode("utf-8"))


def world(tmp_path, monkeypatch, ranks=()):
    ctx, shell, factors = services(tmp_path, monkeypatch, list(ranks))
    handlers = {}
    ctx.handlers = handlers
    handle_actions(ctx, shell)
    return shell, factors, handlers


def test_the_funding_gaps_of_a_run_are_exact_json_whether_recorded_in_nanoseconds_or_milliseconds():
    from modules.quant.backend.object_data import public_summary
    old = {"funding": {"settlements": {"A": 3}, "gaps": {"A": [[1730937600000000000, 1786032000000000000, "No funding settlements reported"]]}}}
    shown = public_summary(old)
    assert shown["funding"]["gaps"]["A"] == [[1730937600000, 1786032000000, "No funding settlements reported"]]
    wire.encode(shown)    # the platform's wire: finite numbers, integers within 2^53
    new = {"funding": {"gaps": {"A": [[1730937600000, 1786032000000, "x"]]}}}
    assert public_summary(new)["funding"]["gaps"] == new["funding"]["gaps"]


def test_a_run_history_of_a_hundred_runs_is_cut_to_what_fits_and_says_where_the_rest_begins(tmp_path, monkeypatch):
    async def scenario():
        shell, factors, handlers = world(tmp_path, monkeypatch, [.01] * 60)
        created = await shell.store.create({"kind": "factor", "name": "Reversal"})
        await shell.store.update_settings(created["id"], {"data": DATA})
        for index in range(60):
            started = await factors.start(created["id"], {"formula": f"-($close / Ref($close, {index + 2}) - 1)", "settings": {"holding": index + 1}})
            await settled(factors, started["id"])
        args = {"objectId": created["id"]}
        seen = []
        while True:
            part = await handlers["list_runs"](args, AI)
            assert part == await handlers["list_runs"](args,KERNEL) and size(part)<=LIMIT and part["total"] == 60
            seen.extend(item["runId"] for item in part["items"])
            if part["nextCursor"] is None:
                break
            args["cursor"] = part["nextCursor"]
        assert len(set(seen)) == 60
    asyncio.run(scenario())


def test_a_factor_run_hands_the_agent_no_figures_even_when_asked(tmp_path, monkeypatch):
    async def scenario():
        shell, factors, handlers = world(tmp_path, monkeypatch, [.01])
        created = await shell.store.create({"kind": "factor", "name": "Reversal"})
        await shell.store.update_settings(created["id"], {"data": DATA})
        started = await factors.start(created["id"], {"formula": "-($close / Ref($close, 5) - 1)"})
        await settled(factors, started["id"])
        path = factors.directory(started["id"]) / "output" / "work.json"
        work = json.loads(path.read_text(encoding="utf-8"))
        work["figures"] = {"returns": {"data": [{"x": list(range(5000)), "y": list(np.arange(5000) * 1.5)}], "layout": {}}}
        path.write_text(json.dumps(work), encoding="utf-8")
        assert factors.work(started["id"])["work"]["figures"]
        asked = await handlers["read_run"]({"runId":started["id"]},AI)
        assert "figures" not in asked and asked["numbers"]["rankIc"] == .01 and size(asked)<=LIMIT
    asyncio.run(scenario())


def test_a_run_of_six_hundred_names_gives_the_agent_its_paths_and_counts_not_the_instrument_table(tmp_path, monkeypatch):
    async def scenario():
        shell, factors, handlers = world(tmp_path, monkeypatch, [.01])
        created = await shell.store.create({"kind": "factor", "name": "Reversal"})
        await shell.store.update_settings(created["id"], {"data": DATA})
        started = await factors.start(created["id"], {"formula": "-($close / Ref($close, 5) - 1)"})
        done = await settled(factors, started["id"])
        folder = shell.runs.records.root / "factor-data" / done["data"]["key"]
        folder.mkdir(parents=True, exist_ok=True)
        (folder / "bars.parquet").write_bytes(b"x")
        info = {"instruments": [{"column": f"N{i}", "displaySymbol": f"NAME{i}", "venueId": "XNAS", "sector": "Tech"} for i in range(600)],
                "index": {"poolId": "sp500", "memberCount": 744, "mappedCount": 616, "excludedCount": 128,
                          "exclusions": [{"kind": "no_prices", "symbol": f"S{i}", "assetId": f"a{i}", "since": 1, "until": 2, "sourceReason": "none"} for i in range(10_000)]}}
        (folder / "info.json").write_text(json.dumps(info), encoding="utf-8")
        materials = RunMaterials(shell.store, shell.runs, shell.runs.records.root / "factor-data")
        args = {"runId": started["id"]}
        full = materials.read(args)
        # the kernel's q.run reads all of it from the file; inline, the information alone is over the budget an answer to an AI has
        assert size(info) > LIMIT and len(json.loads(Path(full["data"]["infoPath"]).read_text(encoding="utf-8"))["instruments"]) == 600
        brief = materials.read(args, brief=True)
        assert size(brief) <= LIMIT
        assert brief["data"]["info"]["instruments"]["count"] == 600 and brief["data"]["info"]["index"]["exclusions"] == {"count": 10_000}
        assert json.loads(Path(brief["data"]["infoPath"]).read_text(encoding="utf-8"))["index"]["exclusions"][0]["symbol"] == "S0"
    asyncio.run(scenario())


def write_cross_section(directory, names=600, inputs=3):
    cross = directory / "output" / "cross"
    cross.mkdir(parents=True)
    rng = np.random.default_rng(1)
    days = 3
    for name in ("after", "raw", "next"):
        np.save(cross / f"{name}.npy", rng.normal(size=(days, names)))
    np.save(cross / "group.npy", rng.integers(1, 6, size=(days, names)))
    np.save(cross / "missing.npy", np.zeros((days, names), dtype=int))
    for index in range(inputs):
        np.save(cross / f"input-{index}.npy", rng.normal(size=(days, names)))
    meta = {"labels": [1_000, 2_000, 3_000], "columns": [f"NAME{i}" for i in range(names)], "inputs": [f"$input{i}" for i in range(inputs)], "flags": [],
            "names": [names] * days, "holding": 5, "groups": 5, "industry": ["Information technology"] * names}
    (cross / "meta.json").write_text(json.dumps(meta), encoding="utf-8")
    return meta


def test_one_day_of_six_hundred_names_comes_to_the_agent_in_rank_order_a_page_at_a_time(tmp_path, monkeypatch):
    async def scenario():
        shell, factors, handlers = world(tmp_path, monkeypatch, [.01])
        created = await shell.store.create({"kind": "factor", "name": "Reversal"})
        await shell.store.update_settings(created["id"], {"data": DATA})
        started = await factors.start(created["id"], {"formula": "-($close / Ref($close, 5) - 1)"})
        await settled(factors, started["id"])
        write_cross_section(factors.directory(started["id"]))
        args = {"runId":started["id"],"table":"crossSection"}
        seen = []
        while True:
            part = await handlers["read_run_table"](args,KERNEL)
            assert size(part) <= LIMIT and part["total"] == 600
            rank = [c["name"] for c in part["columns"]].index("rank")
            seen.extend(r[rank] for r in part["rows"])
            if part["nextCursor"] is None:
                break
            args["cursor"] = part["nextCursor"]
        assert seen == list(range(1,601))
    asyncio.run(scenario())


def test_a_notebook_with_outputs_reads_as_cells_and_a_long_file_is_cut_with_its_length_given(tmp_path, monkeypatch):
    async def scenario():
        shell, factors, handlers = world(tmp_path, monkeypatch)
        created = await shell.store.create({"kind": "notebook", "name": "Study"})
        record = shell.store.read(created["id"])
        picture = "iVBOR" + "A" * 30_000
        cells = [{"cell_type": "code", "id": f"c{i}", "metadata": {}, "source": [f"x{i} = {i}\n", f"print(x{i})"], "execution_count": i,
                  "outputs": [{"output_type": "stream", "name": "stdout", "text": [str(i) + "\n"]},
                              {"output_type": "display_data", "data": {"image/png": picture, "text/plain": ["<Figure>"]}, "metadata": {}}]} for i in range(10)]
        shell.store.source_path(record).write_text(json.dumps({"cells": cells, "metadata": {}, "nbformat": 4, "nbformat_minor": 5}), encoding="utf-8")
        args = {"objectId":created["id"],"part":"code"}
        asked = await handlers["read_object"](args,KERNEL)
        text = asked["files"][0]["text"]
        assert "x9 = 9" in text and "image/png" in text and picture not in text and size(asked)<=LIMIT
        assert asked["nextCursor"] is None
    asyncio.run(scenario())


def test_a_long_source_is_cut_for_the_agent_with_its_length_given(tmp_path, monkeypatch):
    async def scenario():
        shell, factors, handlers = world(tmp_path, monkeypatch, [.01])
        created = await shell.store.create({"kind": "factor", "name": "Reversal"})
        await shell.store.update_settings(created["id"], {"data": DATA})
        started = await factors.start(created["id"], {"formula": "-($close / Ref($close, 5) - 1)" + " " * 100})
        await settled(factors, started["id"])
        formula = "\n".join(f"-($close / Ref($close, {i}) - 1)" for i in range(3_000))
        shell.runs.save(factors.read(started["id"]), formula=formula, code=None)
        args = {"runId":started["id"],"part":"code"}
        pieces = []
        while True:
            part = await handlers["read_run"](args,KERNEL)
            assert size(part)<=LIMIT
            pieces.append(part["data"]["files"][0]["text"])
            if part["nextCursor"] is None:
                break
            args["cursor"] = part["nextCursor"]
        assert "".join(pieces) == formula
    asyncio.run(scenario())


def test_a_factor_run_finishes_beside_another_kinds_held_out_run(tmp_path, monkeypatch):
    """The held-out count looks at every kind's held-out runs; an allocation's has neither a formula nor code and must not stop a factor run."""
    async def scenario():
        shell, factors, handlers = world(tmp_path, monkeypatch, [.01, .02])
        created = await shell.store.create({"kind": "factor", "name": "Reversal"})
        await shell.store.update_settings(created["id"], {"data": DATA})
        first = await factors.start(created["id"], {"formula": "-($close / Ref($close, 5) - 1)"})
        await settled(factors, first["id"])
        shell.runs.records.put("qrun", {"id": "run-alloc", "objectId": "a", "objectKind": "allocation", "allocation": True, "runKind": "backtest", "state": "completed",
                                         "createdAt": 1, "settings": {"data": {}}})
        shell.runs.records.put("qrun", {"id": "run-alloc-held", "objectId": "a", "objectKind": "allocation", "allocation": True, "runKind": "heldout", "of": "run-alloc",
                                         "state": "completed", "createdAt": 2, "settings": {"data": {}}})
        second = await factors.start(created["id"], {"formula": "-($close / Ref($close, 6) - 1)"})
        assert (await settled(factors, second["id"]))["state"] == "completed"
    asyncio.run(scenario())


def test_a_data_setting_of_five_hundred_names_reaches_the_agent_as_a_count_and_the_first_names(tmp_path, monkeypatch):
    from tests.unit.test_quant_object_runs import INSTRUMENT

    async def scenario():
        shell, factors, handlers = world(tmp_path, monkeypatch, [.01])
        created = await shell.store.create({"kind": "factor", "name": "Reversal"})
        symbols = [{"instrument": {**INSTRUMENT, "displaySymbol": f"N{i}", "instrumentId": f"vi1_{i:064d}", "symbol": f"N{i}"}} for i in range(500)]
        await shell.store.update_settings(created["id"], {"data": {**DATA, "symbols": symbols}})
        started = await factors.start(created["id"], {"formula": "-($close / Ref($close, 5) - 1)"})
        await settled(factors, started["id"])
        for name,args in (("read_object",{"objectId":created["id"]}),("list_runs",{"objectId":created["id"]}),("read_run",{"runId":started["id"]})):
            code = await handlers[name](args,KERNEL)
            assert code == await handlers[name](args,AI) and size(code)<=LIMIT
            if name != "list_runs":
                assert len(code["settings"]["data"]["symbols"]) == 500
    asyncio.run(scenario())


# ---------------------------------------------------------------------------------------------------------- model and sweep reads
def test_the_models_preparation_gives_the_agent_one_algorithms_parameters_and_the_rest_by_name():
    settings = [{"name": f"alg{a}_p{i}", "label": f"P{i}", "help": "h" * 80, "group": "Algorithm", "showIf": None, "file": "m.py", "line": 3, "min": None, "max": None, "type": "float", "default": .1,
                 "step": None} for a in range(27) for i in range(2)]
    algorithms = [{"id": f"alg{a}", "label": f"A{a}", "group": "tree", "groupLabel": "Trees", "objectives": [{"id": "l2", "label": "L2", "weights": True}],
                   "params": [{"key": f"p{i}", "label": f"P{i}", "kind": "number", "type": "float", "default": .1, "setting": f"alg{a}_p{i}"} for i in range(2)]} for a in range(27)]
    found = {"objectId": "m", "groups": [{"name": "Processing", "settings": [{"name": "inputs_missing", "options": [{"value": "median", "label": "Day's median"}], "file": "m.py", "line": 1}]},
                                         {"name": "Algorithm", "settings": settings}],
             "values": {"algorithm": "alg3"}, "algorithms": algorithms, "offers": []}
    from modules.quant.backend.ai_views import model_prep
    own = model_prep(found)
    assert own["algorithm"] == "alg3" and [item["name"] for item in own["groups"][1]["settings"]] == ["alg3_p0", "alg3_p1"]
    assert [item["id"] for item in own["algorithms"] if "params" in item] == ["alg3"] and len(own["algorithms"]) == 27
    assert own["groups"][0]["settings"][0] == {"name": "inputs_missing", "options": ["median"]}
    assert [item["id"] for item in model_prep(found, "alg9")["algorithms"] if "params" in item] == ["alg9"]


def test_a_days_model_values_come_in_rank_order_a_page_at_a_time_without_the_figure():
    from modules.quant.backend.ai_views import model_values
    rows = [{"ticker": f"N{i}", "prediction": float(i), "rank": 600 - i, "later": .01, "sector": "Tech",
             "contributions": [{"input": f"Alpha158.F{k}", "value": .01 * k} for k in range(20)]} for i in range(600)]
    found = {"ok": True, "date": 1, "rows": rows, "figure": {"data": [{"x": list(range(5000))}]}, "next": 2}
    assert size(found) > LIMIT
    seen, offset = [], 0
    while offset is not None:
        part = model_values(found, offset)
        assert size(part) <= LIMIT and "figure" not in part and part["listed"] == 600 and len(part["rows"][0]["contributions"]) == 3
        seen += [row["rank"] for row in part["rows"]]
        offset = part["nextOffset"]
    assert seen == list(range(1, 601))
    assert [row["rank"] for row in model_values(found, 10, 3)["rows"]] == [11, 12, 13]


def test_a_long_results_table_and_a_long_source_come_to_the_agent_a_page_at_a_time():
    from modules.quant.backend.ai_views import chunk_files, table_page
    found = {"run": {"id": "r"}, "results": {"inputs": {"importance": {"a": 1}, "table": [{"feature": f"F{i}", "share": .1, "note": "x" * 100} for i in range(400)]}}}
    assert size(found) > LIMIT
    seen, offset = [], 0
    while offset is not None:
        part = table_page(found, "inputs", offset)
        assert size(part) <= LIMIT and part["results"]["inputs"]["tableTotal"] == 400 and part["results"]["inputs"]["importance"] == {"a": 1}
        seen += [row["feature"] for row in part["results"]["inputs"]["table"]]
        offset = part["results"]["inputs"]["tableNextOffset"]
    assert seen == [f"F{i}" for i in range(400)]
    files = [{"file": "a.py", "text": "a" * 20_000}, {"file": "b.py", "text": "b" * 5_000}]
    text, offset = "", 0
    while offset is not None:
        part = chunk_files(files, offset)
        assert size(part) <= LIMIT and part["chars"] == 25_000
        text += "".join(item["text"] for item in part["files"])
        offset = part["nextOffset"]
    assert text == "a" * 20_000 + "b" * 5_000


def test_long_curves_and_a_sweeps_pictures_reach_the_agent_as_a_hundred_and_twenty_points():
    from modules.quant.backend.ai_views import short_lists, thin
    points = [{"t": index, "equity": 100_000 + index} for index in range(1500)]
    assert size(points) > LIMIT
    short = thin(points)
    assert len(short) == 120 and short[0] == points[0] and short[-1] == points[-1] and [item["t"] for item in short] == sorted(item["t"] for item in short)
    assert thin(points[:50]) == points[:50]
    library = [{"name": "Rev", "instrumentIds": [f"vi1_{i:064d}" for i in range(600)], "fields": ["prediction", "label"]}]
    shown = short_lists(library)
    assert shown[0]["instrumentIds"]["count"] == 600 and len(shown[0]["instrumentIds"]["first"]) == 5 and shown[0]["fields"] == ["prediction", "label"]
    assert size(shown) < 2_000


def large_model_results(names=600, years=10, windows=40, inputs=300):
    """What a model's Results hold for a big run: a row per window, a column per window of every input's importance, a row per input, a row per year."""
    day = 86_400_000
    rows = [{"k": k, "train": {"from": k * day, "to": (k + 300) * day}, "valid": {"from": (k + 301) * day, "to": (k + 360) * day}, "test": {"from": (k + 361) * day, "to": (k + 540) * day},
             "trainIc": .1, "validIc": .04, "testIc": -.003, "best": 5, "rows": 116_204 + k, "secs": 19.16} for k in range(1, windows + 1)]
    features = [f"Alpha158.F{i}" for i in range(inputs)]
    params = {f"p{i}": i for i in range(77)}
    run = {"id": "run-x", "number": 3, "name": "New model", "params": params, "windows": rows, "windowsTotal": windows, "windowsDone": windows, "state": "completed",
           "baselines": {"bestInput": {"name": features[3], "ic": .01, "windows": [{"k": row["k"], "ic": .01} for row in rows]}},
           "checks": [{"id": f"c{i}", "name": "t", "need": "≥ 3.00", "state": "watch", "value": "x" * 120} for i in range(12)], "metrics": {"ric": -.007}}
    results = {
        "windows": {"table": rows, "perWindow": True, "importance": {"features": features[:30], "windows": list(range(1, windows + 1)), "values": [[.006] * windows for _ in range(30)]}},
        "returns": {"longShort": {"risk": {name: {"mean": -.0002, "std": .003} for name in ("Long–short", "After cost", "Top")}, "rows": [{"name": f"Group {g}", "annual": .01, "sharpe": .1} for g in range(1, 11)],
                                  "byYear": [{"year": 2016 + y, "annual": .01} for y in range(years)]}},
        "information": {"table": {"ic": -.009, "rows": [{"name": "IC", "mean": -.009}]}, "decay": [.01] * 20, "byYear": [{"year": 2016 + y, "ic": .01} for y in range(years)]},
        "turnover": {"table": {"rows": [{"name": "Long", "mean": .6}]}, "byYear": [{"year": 2016 + y, "turnover": .6} for y in range(years)]},
        "inputs": {"importance": {"features": [{"feature": name, "share": .003, "spread": .002} for name in features[:60]]}, "industry": [{"industry": f"Industry {i}", "tilt": .01, "share": .1} for i in range(40)],
                   "table": [{"feature": name, "source": "Alpha158", "share": .003, "split": None, "ownIc": .007, "t": .5, "missing": .01} for name in features]},
        "robustness": {"years": [{"year": 2016 + y, "rankIc": -.003, "days": 126, "t": -.27, "lsNet": -.11, "sharpe": -2.1} for y in range(years)],
                       "bySize": [{"part": p, "rankIc": -.001} for p in ("small", "middle", "large")], "byLiquidity": [{"part": p, "rankIc": -.001} for p in ("least", "middle", "most")]},
        "missing": [{"key": f"model.k{i}", "reason": "Qlib could not draw it: " + "x" * 80} for i in range(20)]}
    return {"run": run, "results": results, "settings": {"params": params, "run": {"cost": 5}, "data": {"names": names}}, "pins": [{"input": f"i{i}", "name": features[i]} for i in range(8)],
            "data": {"names": names, "notes": ["Sector is the current one"] * 4}, "environment": {"threads": 2}}


def test_every_section_of_a_large_models_results_fits_for_the_agent_whatever_the_size():
    from modules.quant.backend.ai_views import model_results
    found = large_model_results()
    assert size({**found, "results": {"windows": found["results"]["windows"]}}) > LIMIT
    names = list(found["results"])
    shown = model_results(found)
    assert shown["results"] == {"sections": names} and size(shown) <= LIMIT
    for section in names:
        part = {**found, "results": {section: found["results"][section]}}
        answer = model_results(part, section)
        assert size(answer) <= LIMIT, section
        assert "windows" not in answer["run"] and "params" not in answer["settings"] and "windows" not in answer["run"].get("baselines", {}).get("bestInput", {}), section
    # the long tables are read through, a page at a time
    for section, key in (("inputs", "table"), ("windows", "table")):
        seen, offset = [], 0
        while offset is not None:
            part = model_results({**found, "results": {section: found["results"][section]}}, section, offset)
            assert size(part) <= LIMIT
            block = part["results"][section]
            seen += [row.get("feature", row.get("k")) for row in block[key]]
            offset = block["tableNextOffset"]
        assert len(seen) == len(found["results"][section][key])


def test_the_budget_cut_shortens_the_longest_lists_and_names_each_cut():
    from modules.quant.backend.ai_views import bound
    big = {"a": list(range(30_000)), "b": [{"x": "y" * 50} for _ in range(300)], "small": [1, 2, 3]}
    assert size(big) > LIMIT
    cut = bound(big)
    assert size(cut) <= LIMIT and cut["small"] == [1, 2, 3] and set(cut["cut"]) <= {"a", "b"} and cut["cut"]["a"]["total"] == 30_000 and len(cut["a"]) == cut["cut"]["a"]["shown"]
    assert bound({"a": [1, 2]}) == {"a": [1, 2]}
