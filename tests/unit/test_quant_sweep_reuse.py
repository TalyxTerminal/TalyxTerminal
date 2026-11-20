"""Costs and capacity run only what is missing: the value the run itself ran with, and values an earlier sweep from the same run already ran."""
import json
import sqlite3
from types import SimpleNamespace

from modules.quant.backend.object_sweeps import Sweeps


def world(earlier):
    db = sqlite3.connect(":memory:")
    db.execute("CREATE TABLE records (kind TEXT, id TEXT, content TEXT)")
    for record in earlier:
        db.execute("INSERT INTO records VALUES ('qrun', ?, ?)", (record["id"], json.dumps(record)))
    source = {"id": "src", "number": 6, "metrics": {"sharpe": 1.1}}
    sweeps = object.__new__(Sweeps)
    sweeps.runs = SimpleNamespace(read=lambda identity: source, records=SimpleNamespace(db=db))
    return sweeps


def sweep(identity, name, values, state="completed"):
    return {"id": identity, "number": 9, "objectId": "o", "runKind": "sweep", "of": "src", "state": state,
            "sweep": {"axes": [{"name": name}], "cases": [{"index": i, "params": {name: v}, "state": "completed", "runId": f"{identity}-{i}", "metrics": {"sharpe": v}} for i, v in enumerate(values)]}}


def new(name, value):
    record = {"id": "now", "objectId": "o", "runKind": "sweep", "of": "src", "sweep": {"axes": [{"name": name}]}}
    return record, {"params": {name: value}}


def test_only_the_missing_values_run():
    sweeps = world([sweep("a", "@costMultiple", [0.0, 2.0]), sweep("b", "@capital", [1e6], state="failed")])
    prepared = {"runSettings": {"capital": 100000.0}}
    record, case = new("@costMultiple", 1.0)
    assert sweeps.reusable(record, case, prepared)[0] == "src"  # as set: the run itself
    record, case = new("@capital", 100000.0)
    assert sweeps.reusable(record, case, prepared)[0] == "src"
    record, case = new("@costMultiple", 2.0)
    assert sweeps.reusable(record, case, prepared)[0] == "a-1"  # an earlier sweep ran it
    record, case = new("@capital", 1e6)
    assert sweeps.reusable(record, case, prepared) is None  # that sweep did not complete
    record, case = new("@costMultiple", 0.5)
    assert sweeps.reusable(record, case, prepared) is None
    record = {"id": "now", "objectId": "o", "runKind": "sweep", "of": "src", "sweep": {"axes": [{"name": "top_n"}]}}
    assert sweeps.reusable(record, {"params": {"top_n": 5}}, prepared) is None  # a code setting is never reused


def test_capacity_axis_ticks_are_round_numbers_that_say_what_the_data_is():
    from modules.quant.backend import sweep_figures as S
    rows = [{"capital": 1e5, "cagr": 0.3453, "sharpe": 1.6177}, {"capital": 1e6, "cagr": 0.3449, "sharpe": 1.6152}, {"capital": 1e7, "cagr": 0.3251, "sharpe": 1.5276}]
    layout = S.capacity(rows)["layout"]
    for axis, values in (("yaxis", [34.53, 34.49, 32.51]), ("yaxis2", [1.6177, 1.6152, 1.5276])):
        ticks, text = layout[axis]["tickvals"], layout[axis]["ticktext"]
        assert layout[axis]["range"] == [ticks[0], ticks[-1]] and ticks[0] <= min(values) and ticks[-1] >= max(values)
        steps = {round(b - a, 9) for a, b in zip(ticks, ticks[1:], strict=False)}
        assert len(steps) == 1  # evenly spaced
        assert all(float(label.rstrip("%")) == tick for label, tick in zip(text, ticks, strict=True))  # a label is its tick, not a rounding of it
        assert len(set(text)) == len(text)
    assert S.nice_axis([0.1, 0.1])["range"][0] < 0.1 < S.nice_axis([0.1, 0.1])["range"][1] and S.nice_axis([None]) is None
