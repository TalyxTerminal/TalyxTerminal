"""q.model in a notebook: a published model loaded in the kernel from its frozen files."""
import hashlib
import sys
from pathlib import Path

import pytest

import modules.quant.talyx_quant as q


def frozen(tmp_path, loader_body):
    source = tmp_path / "source"
    source.mkdir()
    (source / "nb_model_entry.py").write_text(loader_body, encoding="utf-8")
    weights = tmp_path / "w.bin"
    weights.write_bytes(b"3")
    digest = hashlib.sha256(b"3").hexdigest()
    return {"name": "Net", "versionId": "v1", "version": 2, "trainingCutoff": 1000, "model": {
        "source": str(source), "common": None, "artifacts": [{"name": "w", "path": str(weights), "sha256": digest}],
        "loader": "nb_model_entry:load", "predictor": "nb_model_entry:predict"}}


@pytest.fixture(autouse=True)
def isolate(monkeypatch):
    monkeypatch.syspath_prepend(str(Path(q.__file__).parent))
    for name in [name for name in sys.modules if name == "native" or name.startswith("native.")]:
        monkeypatch.delitem(sys.modules, name)
    monkeypatch.setattr(q, "_models", None)
    monkeypatch.setattr(q, "_loaded", [])
    monkeypatch.setattr(q._Run, "invocation", {"output": "x"})
    yield
    sys.modules.pop("nb_model_entry", None)
    for path in list(sys.path):
        if path.endswith("source"):
            sys.path.remove(path)


def test_q_model_in_notebook_loads_and_predicts(tmp_path, monkeypatch):
    found = frozen(tmp_path, "def load(files):\n    return int(open(files['w'], 'rb').read())\n\ndef predict(state, payload):\n    return state * payload\n")
    asked = {}

    def channel(name, value, timeout=None):
        asked.update(action=name, **value)
        return found

    monkeypatch.setattr(q, "_channel", lambda: channel)
    model = q.model("Net", 2)
    assert model.predict(5) == 15
    assert asked == {"action": "quant.read_library_model", "name": "Net", "version": 2}
    assert q._loaded[0]["call"] == "q.model('Net', 2)" and "v2" in q._loaded[0]["what"]


def test_q_model_wrong_environment_names_the_switch(tmp_path, monkeypatch):
    found = frozen(tmp_path, "import torch_not_here_xyz\n")
    found["model"]["loader"] = "nb_model_entry:load"
    import builtins
    real = builtins.__import__

    def refuse(name, *args, **kwargs):
        if name == "torch_not_here_xyz":
            raise ModuleNotFoundError("No module named 'torch'", name="torch")
        return real(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", refuse)
    monkeypatch.setattr(q, "_channel", lambda: (lambda name, value, timeout=None: found))
    with pytest.raises(RuntimeError, match="needs the deep-learning environment; switch the environment at the top"):
        q.model("Net")


def test_q_model_in_a_research_run_still_refuses(monkeypatch):
    monkeypatch.setattr(q._Run, "invocation", {"research": {"barsPath": "p"}, "output": "x"})
    with pytest.raises(RuntimeError, match="strategy runs"):
        q.model("Net")


def test_library_read_shows_the_item_block_and_stays_a_table(monkeypatch, tmp_path):
    import pandas as pd
    path = tmp_path / "v.parquet"
    pd.DataFrame({"date":["2024-01-01","2024-01-02"], "instrumentId":["a","b"], "symbol":["AAPL","MSFT"], "field": ["x", "x"], "value": [1.0, 2.0],
                  "availableAt": ["2024-01-01T01:00:00Z","2024-01-02T01:00:00Z"], "recordedAt": ["2024-01-01T01:00:00Z","2024-01-02T01:00:00Z"]}).to_parquet(path)
    monkeypatch.setattr(q, "_channel", lambda: (lambda name, value, timeout=None: {"file":{"path":str(path)},"meta":{"version":2,"state":"producing","publishedAt":"2024-01-01","producedByRun":None}}))
    frame = q.library.read("Reversal 1M", version=2)
    assert list(frame.columns)[:3] == ["date","instrumentId","symbol"] and len(frame) == 2 and isinstance(frame.head(1), pd.DataFrame)
    # moments in UTC, not Unix ms
    assert frame["date"].iloc[0] == pd.Timestamp("2024-01-01", tz="UTC") and frame["availableAt"].iloc[1] == pd.Timestamp("2024-01-02 01:00", tz="UTC")
    block = q._library_block(frame)["application/vnd.talyx.runsummary+json"]
    assert block["label"] == "Library item" and block["title"] == "Reversal 1M v2" and block["meta"] == "producing"
    assert block["rows"][0]["text"] == "2 days × 2 names, Jan 1, 2024 – Jan 2, 2024, each value with its name and the time it became known"


def test_run_block_ends_with_the_headline(monkeypatch):
    found = {"state": "completed", "settings": {}, "number": 21, "object": "Short-term reversal", "kind": "factor", "runId": "r", "paths": {}, "metrics": {"rankIc": 0.0153, "t": 3.43, "longShortNet": 0.071, "holding": 5}}
    block = q.Run(found)._repr_mimebundle_()["application/vnd.talyx.runsummary+json"]
    assert block["rows"][-1] == {"name": "Headline", "code": "", "text": "Rank IC 5D +0.0153, t 3.43, long\u2013short net +7.1%"}
    found["metrics"] = {"cagr": -0.021, "sharpe": 0.8}
    assert q.Run(found)._repr_mimebundle_()["application/vnd.talyx.runsummary+json"]["rows"][-1]["text"] == "CAGR \u22122.1%, Sharpe 0.80"


def test_a_negative_sharpe_in_the_run_headline_takes_the_true_minus():
    found = {"state": "completed", "settings": {}, "number": 3, "object": "S", "kind": "strategy", "runId": "r", "paths": {}, "metrics": {"cagr": -0.039, "sharpe": -0.76}}
    assert q.Run(found)._repr_mimebundle_()["application/vnd.talyx.runsummary+json"]["rows"][-1]["text"] == "CAGR \u22123.9%, Sharpe \u22120.76"


def test_the_run_block_names_each_table_the_way_code_reaches_it_and_its_last_day_in_words(tmp_path):
    import pandas as pd
    for name in ("bars", "library-values", "report-0", "report-1"):
        pd.DataFrame({"a": [1]}).to_parquet(tmp_path / f"{name}.parquet")
    found = {"state": "completed", "settings": {}, "number": 19, "object": "S", "kind": "strategy", "runId": "r", "dataTo": "2026-11-06",
             "paths": {f"output/{name}.parquet": str(tmp_path / f"{name}.parquet") for name in ("bars", "library-values", "report-0", "report-1")}, "metrics": {}}
    block = q.Run(found)._repr_mimebundle_()["application/vnd.talyx.runsummary+json"]
    names = [row["name"] for row in block["rows"]]
    assert "run.bars" in names and "run['library-values']" in names and "run['report-0'] … run['report-1']" in names
    assert block["meta"] == "strategy · completed · data to Nov 6, 2026"
    assert q.Run(found)["library-values"]["a"].tolist() == [1]


def test_a_bars_read_empties_its_progress_line_when_it_ends(monkeypatch):
    """The 'Reading bars: 2 of 3 names' line updates in place while the read runs and is emptied when it ends, done or failed."""
    import types
    shown = []
    display = types.ModuleType("IPython.display")
    display.display = lambda bundle, **options: shown.append((bundle["text/plain"], options["update"]))
    monkeypatch.setitem(sys.modules, "IPython", types.ModuleType("IPython"))
    monkeypatch.setitem(sys.modules, "IPython.display", display)
    for ending in ({"state":"completed","result":{"bars":{}}}, {"state": "failed", "error": {"code": "source_denied", "message": "Market refused"}}):
        views = iter([{"state":"running","progress":{"total":3,"done":1}}, {"state":"running","progress":{"total":3,"done":2}}, ending])
        answers = {"quant.read_research_bars": lambda: {"taskId":"r1"}, "platform.tasks.get": lambda: next(views)}
        monkeypatch.setattr(q, "_channel", lambda: (lambda name, value, timeout=None: answers[name]()))
        shown.clear()
        if ending["state"] == "completed":
            assert q._read_bars({}, {}, every=0) == {"bars":{}}
        else:
            with pytest.raises(RuntimeError, match="Market refused"):
                q._read_bars({}, {}, every=0)
        assert shown == [("Reading bars: 1 of 3 names", False), ("Reading bars: 2 of 3 names", True), ("", True)]
