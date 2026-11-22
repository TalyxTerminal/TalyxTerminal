"""Quant's data from before format 2 (the "cash plus beta times the S&P 500" benchmark) reads as before once brought up."""

import json
import sqlite3

from modules.quant.backend import data_formats, strategy_settings
from modules.quant.backend.work_records import encoded


def test_a_beta_benchmark_falls_back_to_the_default_and_frozen_runs_keep_their_spy_closes(tmp_path):
    db = sqlite3.connect(tmp_path / "work-records.sqlite3")
    db.execute("CREATE TABLE records (kind TEXT, id TEXT, content TEXT, PRIMARY KEY(kind,id))")
    beta = {"id": "o1", "kind": "strategy", "settings": {"run": {"benchmark": {"kind": "beta"}, "capital": 5000}, "data": {}}}
    held = {"id": "o2", "kind": "strategy", "settings": {"run": {"benchmark": {"kind": "equal"}}, "data": {}}}
    run = {"id": "r1", "objectId": "o1", "settings": {"run": {"benchmark": {"kind": "beta"}}}}
    for kind, record in (("qobject", beta), ("qobject", held), ("qrun", run)):
        db.execute("INSERT INTO records VALUES (?,?,?)", (kind, record["id"], encoded(record)))
    db.commit()
    db.close()
    rows = [[1_700_000_000_000, 450.0], [1_700_086_400_000, 452.5]]
    (tmp_path / "qruns" / "r1").mkdir(parents=True)
    (tmp_path / "qruns" / "r1" / "invocation.json").write_text(json.dumps({"benchmark": {"kind": "beta", "label": "Cash + β × S&P 500", "symbol": "US:SPY",
                                                                                         "rows": rows}, "riskFree": {"basis": "irx"}}), encoding="utf-8")

    data_formats.drop_beta(tmp_path)
    data_formats.drop_beta(tmp_path)  # a step may run again after failing part way

    db = sqlite3.connect(tmp_path / "work-records.sqlite3")
    found = {(kind, identity): json.loads(content) for kind, identity, content in db.execute("SELECT kind, id, content FROM records")}
    db.close()
    assert found[("qobject", "o1")]["settings"]["run"] == {"capital": 5000}
    assert found[("qobject", "o2")]["settings"]["run"] == {"benchmark": {"kind": "equal"}}
    assert found[("qrun", "r1")]["settings"]["run"] == {}
    # the strategy now measures against its market's index: SPY for US stocks, what the beta benchmark was built from
    stored = found[("qobject", "o1")]["settings"]["run"]
    assert strategy_settings.check(stored) == stored
    assert strategy_settings.effective("stock", stored, {}, market="US")["benchmark"] == {"kind": "symbol", "symbol": "US:SPY"}
    frozen = json.loads((tmp_path / "qruns" / "r1" / "invocation.json").read_text(encoding="utf-8"))
    assert frozen["benchmark"] == {"kind": "series", "label": "SPY", "symbol": "US:SPY", "rows": rows}
    assert frozen["riskFree"] == {"basis": "irx"}


def test_no_data_yet_is_nothing_to_do(tmp_path):
    data_formats.drop_beta(tmp_path)
    data_formats.allocation_benchmark(tmp_path)
    assert list(tmp_path.iterdir()) == []


def test_an_allocation_follows_the_60_40_it_followed_before(tmp_path):
    db = sqlite3.connect(tmp_path / "work-records.sqlite3")
    db.execute("CREATE TABLE records (kind TEXT, id TEXT, content TEXT, PRIMARY KEY(kind,id))")
    run = {"id": "a1", "allocation": True, "settings": {"params": {"objective": "benchmark_tracking"}}}
    db.execute("INSERT INTO records VALUES (?,?,?)", ("qrun", "a1", encoded(run)))
    db.commit()
    db.close()
    for name, pool in (("a1", ["SPY", "IEF", "GLD"]), ("a2", ["GLD", "TLT"])):
        (tmp_path / "qallocations" / name / "engine").mkdir(parents=True)
        job = {"settings": {"objective": "benchmark_tracking", "pool": [{"ticker": ticker} for ticker in pool], "proxyColumns": {}}, "data": {"barsPath": "x"}}
        (tmp_path / "qallocations" / name / "engine" / "invocation.json").write_text(json.dumps({"mode": "once", "job": job}), encoding="utf-8")
    (tmp_path / "library2").mkdir()
    library = sqlite3.connect(tmp_path / "library2" / "library.sqlite3")
    library.execute("CREATE TABLE versions (id TEXT PRIMARY KEY, meta TEXT NOT NULL)")
    library.execute("INSERT INTO versions VALUES (?,?)", ("v1", json.dumps({"allocation": {"settings": {"objective": "min_risk"}, "pool": []}})))
    library.execute("INSERT INTO versions VALUES (?,?)", ("v2", json.dumps({"factor": True})))
    library.commit()
    library.close()

    data_formats.allocation_benchmark(tmp_path)
    data_formats.allocation_benchmark(tmp_path)

    db = sqlite3.connect(tmp_path / "work-records.sqlite3")
    assert json.loads(db.execute("SELECT content FROM records").fetchone()[0])["settings"]["params"]["benchmark"] == "SPY 60%, IEF 40%"
    db.close()
    both = json.loads((tmp_path / "qallocations" / "a1" / "engine" / "invocation.json").read_text(encoding="utf-8"))["job"]["settings"]
    assert both["benchmarkColumns"] == {"SPY": 60.0, "IEF": 40.0} and both["benchmark"] == "SPY 60%, IEF 40%"
    neither = json.loads((tmp_path / "qallocations" / "a2" / "engine" / "invocation.json").read_text(encoding="utf-8"))["job"]["settings"]
    assert neither["benchmarkColumns"] == {}
    library = sqlite3.connect(tmp_path / "library2" / "library.sqlite3")
    metas = dict(library.execute("SELECT id, meta FROM versions").fetchall())
    library.close()
    assert json.loads(metas["v1"])["allocation"]["settings"]["benchmark"] == "SPY 60%, IEF 40%" and json.loads(metas["v2"]) == {"factor": True}
