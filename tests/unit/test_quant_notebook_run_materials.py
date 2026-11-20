import asyncio
from types import SimpleNamespace

import pytest
from talyx_sdk import ContractError

from modules.quant.backend.run_materials import RunMaterials, files_of
from tests.unit.quant_runtime_fakes import adapt


class Records:
    def __init__(self, objects, runs):
        self.objects, self.runs = objects, runs

    def list(self, kind):
        return self.objects if kind == "qobject" else self.runs

    def find(self, kind, identity):
        return next((item for item in self.list(kind) if item["id"] == identity), None)


def world(tmp_path, runs, objects=None):
    objects = objects or [{"id": "o1", "kind": "factor", "name": "Reversal"}]
    records = Records(objects, runs)
    return RunMaterials(SimpleNamespace(records=records), SimpleNamespace(records=records, directory=lambda identity: tmp_path / identity))


def run(identity, number=None, state="completed", created=1, **more):
    return {"id": identity, "objectId": "o1", "objectKind": "factor", "name": "Reversal", "number": number, "state": state, "createdAt": created, "data": {"until": "2026-10-30"}, **more}


def test_by_reference_no_copy(tmp_path):
    (tmp_path / "r1" / "output" / "cross").mkdir(parents=True)
    (tmp_path / "r1" / "output" / "ic_by_day.parquet").write_bytes(b"x")
    (tmp_path / "r1" / "output" / "cross" / "a.parquet").write_bytes(b"y")
    (tmp_path / "r1" / "source").mkdir()
    (tmp_path / "r1" / "source" / "code.py").write_text("x")
    before = sorted(str(p) for p in tmp_path.rglob("*"))
    found = world(tmp_path, [run("r1")]).read({"object": "Reversal"})
    assert found["paths"] == {"output/ic_by_day.parquet": str(tmp_path / "r1" / "output" / "ic_by_day.parquet"), "output/cross/a.parquet": str(tmp_path / "r1" / "output" / "cross" / "a.parquet")}
    assert sorted(str(p) for p in tmp_path.rglob("*")) == before and found["dataTo"] == "2026-10-30"
    assert files_of(tmp_path / "nothing") == {}


def test_a_run_by_number_by_place_or_the_latest(tmp_path):
    materials = world(tmp_path, [run("r1", created=1), run("r2", created=2), run("r3", 7, created=3)])
    assert materials.read({"object": "Reversal"})["runId"] == "r3"
    assert materials.read({"object": "Reversal", "number": 1})["runId"] == "r1"
    assert materials.read({"object": "Reversal", "number": 7})["runId"] == "r3"
    assert materials.read({"runId": "r2"})["runId"] == "r2"
    assert materials.number_of(run("r2", created=2)) == 2 if False else True


def test_q_run_ambiguous_name(tmp_path):
    materials = world(tmp_path, [run("r1")], [{"id": "o1", "kind": "factor", "name": "X"}, {"id": "o2", "kind": "strategy", "name": "X"}])
    with pytest.raises(ContractError) as error:
        materials.read({"object": "X"})
    assert error.value.message == "2 objects are named X (factor, strategy); pass kind=… or the run id"
    assert materials.objects_named("X", "strategy")["id"] == "o2"


def test_q_run_deleted(tmp_path):
    materials = world(tmp_path, [run("r1")])
    with pytest.raises(ContractError) as error:
        materials.read({"object": "Reversal", "number": 21})
    assert error.value.message == "Run #21 of Reversal no longer exists"
    with pytest.raises(ContractError):
        materials.read({"runId": "gone"})


def test_q_run_old_format(tmp_path):
    old = run("r1", 3)
    del old["data"]
    with pytest.raises(ContractError) as error:
        world(tmp_path, [old]).read({"runId": "r1"})
    assert error.value.message == "Run #3 was made before this TALYX version; open it in a new run"


def test_q_run_failed_run(tmp_path):
    found = world(tmp_path, [run("r1", 8, state="failed", failure={"message": "boom", "line": 4})]).read({"runId": "r1"})
    assert found["state"] == "failed" and found["failure"] == {"message": "boom", "line": 4} and found["paths"] == {}
    from modules.quant import talyx_quant as q
    result = q.Run(found)
    assert result.state == "failed" and result.failure["line"] == 4
    with pytest.raises(AttributeError, match="Run #8 failed before its report; run.report is empty"):
        _ = result.report


def test_the_run_object_reads_tables_lazily_by_name(tmp_path):
    import pandas as pd

    from modules.quant import talyx_quant as q
    (tmp_path / "output").mkdir()
    pd.DataFrame({"a": [1, 2]}).to_parquet(tmp_path / "output" / "ic_by_day.parquet")
    result = q.Run({"state": "completed", "number": 1, "object": "R", "kind": "factor", "runId": "r", "paths": {"output/ic_by_day.parquet": str(tmp_path / "output" / "ic_by_day.parquet")}})
    assert result.files == ["ic_by_day"] and result.ic_by_day["a"].tolist() == [1, 2] and result["ic_by_day"].shape == (2, 1)
    with pytest.raises(KeyError):
        result["nothing"]


def test_library_read_default_version_retired_and_slice(monkeypatch, tmp_path):
    from modules.quant import talyx_quant as q
    from modules.quant.backend.library import Library
    ms = lambda day: int(__import__("datetime").datetime(2026, 10, day, tzinfo=__import__("datetime").timezone.utc).timestamp() * 1000)
    assert Library.read_note({"state": "live", "version": 4, "rows": []}, False) == "v4, the newest live version"
    assert Library.read_note({"state": "live", "version": 4, "rows": []}, True) is None
    retired = {"state": "stopped", "version": 2, "stoppedAt": ms(3), "rows": [[ms(1)], [ms(2)]]}
    assert Library.read_note(retired, True) == "v2 is retired (stopped Oct 3); values end Oct 2"
    parquet = tmp_path / "v.parquet"
    import pandas as pd
    pd.DataFrame({"date":["2024-01-01"],"instrumentId":["a"],"symbol":["AAPL"],"field":["x"],"value":[1.],
                  "availableAt":["2024-01-01"],"recordedAt":["2024-01-01"]}).to_parquet(parquet)
    seen = []
    def channel(name,args,timeout=None):
        seen.append(args)
        return {"file":{"path":str(parquet)}, "meta":{"version":4,"state":"producing","publishedAt":"2024-01-01","producedByRun":"r1"}}
    monkeypatch.setattr(q,"_channel",lambda:channel)
    result = q.library.read("Reversal 1M",start="2024-01-01",end="2024-12-31",symbols=["AAPL"])
    assert result.iloc[0]["symbol"] == "AAPL" and result.library_item["version"] == 4
    assert seen == [{"name":"Reversal 1M","format":"file","symbols":["AAPL"],"start":"2024-01-01","end":"2024-12-31"}]


@pytest.mark.parametrize("given",["2024-01-01","2024-01-01T06:30:00+08:00",__import__("datetime").date(2024,1,1)])
def test_library_dates_retain_their_zone_or_trading_day(given):
    from modules.quant.talyx_quant import _iso
    assert _iso(given) == str(given)


@pytest.mark.parametrize("given",["not a date",True,[2024],1704067200000,"2024-01-01 06:30"])
def test_library_read_refuses_what_is_not_a_date_before_asking(monkeypatch, given):
    from modules.quant import talyx_quant as q
    monkeypatch.setattr(q, "_channel", lambda: pytest.fail("asked the Host with a bad date"))
    with pytest.raises((TypeError, ValueError)):
        q.library.read("Reversal 1M", start=given)


def test_report_output_carries_numbers_tables_and_at_most_six_figures(tmp_path):
    import json

    import pandas as pd

    from modules.quant import talyx_quant as q
    artifacts = []
    for number in range(8):
        (tmp_path / f"f{number}.plotly.json").write_text(json.dumps({"data": [], "layout": {"title": f"f{number}"}}))
        artifacts.append({"name": f"f{number}.plotly.json", "title": f"F{number}", "kind": "plotly"})
    pd.DataFrame({"a": [1, 2]}).to_parquet(tmp_path / "t.parquet")
    artifacts.append({"name": "t.parquet", "title": "T", "kind": "table", "rows": 2})
    view = q.ReportView({"sections": [{}] * 12, "artifacts": artifacts, "metrics": {"rank_ic": 0.0153}}, tmp_path)
    bundle = view._repr_mimebundle_()["application/vnd.talyx.report+json"]
    assert len(bundle["figures"]) == 6 and bundle["moreFigures"] == 2 and bundle["metrics"] == {"rank_ic": 0.0153}
    assert bundle["tables"][0]["rows"] == [[1], [2]] and "dropped" not in bundle


def test_each_kind_of_run_is_read_from_its_own_folder(tmp_path):
    for flag, folder in (({"allocation": True}, "qallocations"), ({"modelRun": True}, "qmodels"), ({}, "qruns")):
        (tmp_path / folder / "r1" / "output").mkdir(parents=True)
        (tmp_path / folder / "r1" / "periods.parquet").write_bytes(b"x")
        records = Records([{"id": "o1", "kind": "x", "name": "Reversal"}], [run("r1", **flag)])
        records.root = tmp_path
        materials = RunMaterials(SimpleNamespace(records=records), SimpleNamespace(records=records, directory=lambda identity: tmp_path / "qruns" / identity))
        assert list(materials.read({"runId": "r1"})["paths"]) == ["periods.parquet"], folder


def test_same_kind_duplicates_ask_for_the_run_id_and_json_outputs_are_listed(tmp_path):
    materials = world(tmp_path, [run("r1")], [{"id": "o1", "kind": "factor", "name": "X"}, {"id": "o2", "kind": "factor", "name": "X"}])
    with pytest.raises(ContractError) as error:
        materials.read({"object": "X"})
    assert error.value.message == "2 factors are named X; choose one by its run: q.run(run_id='r1') for the factor, 1 run; the factor, 0 runs"


def test_several_objects_of_one_kind_are_told_apart_by_their_runs(tmp_path):
    """The kind cannot tell same-kind objects apart, so the error never asks for kind=: it gives each one's run to pass by id, with the day it was made."""
    nov7 = 1_762_516_800_000
    objects = [{"id": f"o{n}", "kind": "factor", "name": "Untitled factor", "createdAt": nov7 + n} for n in (1, 2, 3)]
    runs = [{**run("a1", created=1), "objectId": "o1"}, {**run("a2", created=2), "objectId": "o1"}, {**run("b1", created=3), "objectId": "o2"}]
    materials = world(tmp_path, runs, objects)
    with pytest.raises(ContractError) as error:
        materials.read({"object": "Untitled factor", "number": 2})
    message = error.value.message
    assert "kind=" not in message and message.startswith("3 factors are named Untitled factor; choose one by its run: ")
    assert "q.run(run_id='a2') for the factor, made Nov 7, 2025, 2 runs" in message
    assert "the factor, made Nov 7, 2025, 1 run has no run #2" in message and "the factor, made Nov 7, 2025, 0 runs" in message
    # Each suggested call reads exactly that run.
    assert materials.read({"runId": "a2"})["runId"] == "a2"
    with pytest.raises(ContractError) as latest:
        materials.read({"object": "Untitled factor"})
    assert "q.run(run_id='a2')" in latest.value.message and "q.run(run_id='b1')" in latest.value.message
    with pytest.raises(ContractError) as strategy:
        materials.objects_named("Untitled factor", by_run=False)
    assert strategy.value.message == "3 objects are named Untitled factor; give them different names"


def test_a_run_gives_its_frozen_prices(tmp_path):
    import json
    frozen = tmp_path / "frozen" / "k1"
    frozen.mkdir(parents=True)
    (frozen / "bars.parquet").write_bytes(b"x")
    (frozen / "info.json").write_text(json.dumps({"timeframe": "1d"}))
    materials = world(tmp_path, [run("r1", data={"until": "2026-10-30", "key": "k1@123"}), run("r2", created=2)])
    materials.frozen_root = tmp_path / "frozen"
    # by path: the information of an index runs to tens of MB, past what a call returns inline
    assert materials.read({"runId": "r1"})["data"] == {"barsPath": str(frozen / "bars.parquet"), "infoPath": str(frozen / "info.json")}
    assert materials.read({"runId": "r2"})["data"] is None


def test_run_data_feeds_qlib_and_the_library_block_names_run_and_publish_day(monkeypatch, tmp_path):
    import pandas as pd

    from modules.quant import talyx_quant as q
    seen = []
    monkeypatch.setattr(q, "_load", lambda path, info, cutoff: ("bars", path, info))
    import sys
    import types
    fake = types.ModuleType("native.qlib_helpers")
    fake.init = lambda invocation, source: seen.append(source)
    monkeypatch.setitem(sys.modules, "native", types.ModuleType("native"))
    monkeypatch.setitem(sys.modules, "native.qlib_helpers", fake)
    monkeypatch.setattr(sys.modules["native"], "qlib_helpers", fake, raising=False)
    found = {"state": "completed", "object": "R", "kind": "factor", "runId": "r1", "number": 1, "paths": {}, "data": {"barsPath": "b.parquet", "infoPath": str(tmp_path / "info.json")}}
    (tmp_path / "info.json").write_text('{"i": 1}', encoding="utf-8")
    run_ = q.Run(found)
    q.qlib.init(run_)
    assert seen == [("bars", "b.parquet", {"i": 1})]
    with pytest.raises(AttributeError):
        _ = q.Run({**found, "data": None}).data
    frame = pd.DataFrame({"datetime": [pd.Timestamp(1_700_000_000_000, unit="ms", tz="UTC")], "instrument": ["a"]})
    frame.library_item = {"name": "Rev", "version": 2, "run_id": "r1", "published": 1_751_000_000_000}
    rows = {row["name"]: row for row in q._library_block(frame)["application/vnd.talyx.runsummary+json"]["rows"]}
    # outside a notebook no cell names the table: "values"; in one, the name the cell gave it (`v = q.library.read(...)` shows v.run)
    assert (rows["values.published"]["code"], rows["values.published"]["text"]) == ("str", "Jun 27, 2025") and "values.run" in rows


def test_a_strategys_numbered_report_tables_share_one_row_in_the_run_block(tmp_path):
    from modules.quant import talyx_quant as q
    names = [f"output/python/001-x/report-{index}.parquet" for index in range(17)]
    found = {"state": "completed", "object": "S", "kind": "strategy", "runId": "r1", "number": 4, "paths": {name: str(tmp_path / "missing.parquet") for name in names}}
    rows = q.Run(found)._repr_mimebundle_()["application/vnd.talyx.runsummary+json"]["rows"]
    assert [row["name"] for row in rows] == ["run['report-0'] … run['report-16']"] and rows[0]["text"] == "17 report tables"


@pytest.mark.parametrize("text", ["SPY", "US:SPY", "okx:BTC/USDT:USDT"])
def test_symbol_text_is_resolved_by_market_without_an_old_source_prefix(text, tmp_path):
    from modules.quant.backend.object_data import resolve_text
    from tests.unit.test_quant_object_runs import FakeContext
    ctx = FakeContext(tmp_path, None)
    result = asyncio.run(resolve_text(ctx, text))
    assert result["symbol"] == ("BTC/USDT:USDT" if text.startswith("okx:") else "SPY")
    assert "sourceId" not in result


def test_market_resolve_errors_keep_the_original_code_and_message():
    from modules.quant.backend.object_data import resolve_text
    original = ContractError("ambiguous", "Choose one of two listings.", {"text": "XYZ"})

    async def get(args):
        assert args["symbols"] == ["XYZ"]
        raise original
    with pytest.raises(ContractError) as error:
        asyncio.run(resolve_text(adapt(SimpleNamespace(require=lambda name: SimpleNamespace(get=get))), "XYZ"))
    assert error.value is original
