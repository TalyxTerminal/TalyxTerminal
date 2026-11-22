"""Quant's data format steps (`Module.data_format`): each turns the data a version before kept into the next version's.

2, benchmarks a user chooses:
- the "cash plus beta times the S&P 500" strategy benchmark is gone. A strategy, or a run's asked settings, that chose it falls back to the default (a stock
  strategy's market index: SPY for US stocks, what that benchmark was built from); a run's frozen inputs keep its SPY closes as an ordinary symbol benchmark,
  so measuring that run again (a cost case) reads the same prices;
- an allocation's benchmark is a setting (`benchmark`, Market symbols with weights) frozen into its bars as `benchmarkColumns`, no longer the SPY and IEF
  of its pool. Run records and published versions get the 60/40 they followed written out; a run's frozen job follows the pool's SPY and IEF columns as before.
"""
from __future__ import annotations

import json
import os
import sqlite3
from pathlib import Path

SIXTY_FORTY = "SPY 60%, IEF 40%"


async def benchmarks_chosen(ctx) -> None:
    folder = Path(os.environ["TALYX_MODULE_DATA"])
    drop_beta(folder)
    allocation_benchmark(folder)


def drop_beta(folder: Path) -> None:
    path = folder / "work-records.sqlite3"
    if not path.exists():
        return
    db = sqlite3.connect(path)
    try:
        rows = db.execute("SELECT kind, id, content FROM records WHERE kind IN ('qobject', 'qrun') "
                          "AND json_extract(content, '$.settings.run.benchmark.kind') = 'beta'").fetchall()
        with db:
            for kind, identity, content in rows:
                record = json.loads(content)
                del record["settings"]["run"]["benchmark"]
                db.execute("UPDATE records SET content=? WHERE kind=? AND id=?", (json.dumps(record, sort_keys=True, ensure_ascii=False), kind, identity))
    finally:
        db.close()
    for invocation in (folder / "qruns").glob("*/invocation.json"):
        text = invocation.read_text(encoding="utf-8")
        if '"beta"' not in text:
            continue
        frozen = json.loads(text)
        if "benchmark" in frozen and frozen["benchmark"]["kind"] == "beta":
            frozen["benchmark"] = {"kind": "series", "label": "SPY", "symbol": "US:SPY", "rows": frozen["benchmark"]["rows"]}
            invocation.write_text(json.dumps(frozen, ensure_ascii=False), encoding="utf-8")


def allocation_benchmark(folder: Path) -> None:
    path = folder / "work-records.sqlite3"
    if path.exists():
        db = sqlite3.connect(path)
        try:
            rows = db.execute("SELECT id, content FROM records WHERE kind='qrun' AND json_extract(content, '$.allocation') "
                              "AND json_extract(content, '$.settings.params.benchmark') IS NULL").fetchall()
            with db:
                for identity, content in rows:
                    record = json.loads(content)
                    record["settings"]["params"]["benchmark"] = SIXTY_FORTY
                    db.execute("UPDATE records SET content=? WHERE kind='qrun' AND id=?", (json.dumps(record, sort_keys=True, ensure_ascii=False), identity))
        finally:
            db.close()
    for invocation in (folder / "qallocations").glob("*/engine/invocation.json"):
        frozen = json.loads(invocation.read_text(encoding="utf-8"))
        settings = frozen["job"]["settings"]
        if "benchmarkColumns" in settings:
            continue
        tickers = {row["ticker"] for row in settings["pool"]}
        settings["benchmark"] = SIXTY_FORTY
        settings["benchmarkColumns"] = {"SPY": 60.0, "IEF": 40.0} if {"SPY", "IEF"} <= tickers else {}
        invocation.write_text(json.dumps(frozen), encoding="utf-8")
    library = folder / "library2" / "library.sqlite3"
    if library.exists():
        db = sqlite3.connect(library)
        try:
            rows = db.execute("SELECT id, meta FROM versions WHERE json_extract(meta, '$.allocation.settings') IS NOT NULL "
                              "AND json_extract(meta, '$.allocation.settings.benchmark') IS NULL").fetchall()
            with db:
                for identity, content in rows:
                    meta = json.loads(content)
                    meta["allocation"]["settings"]["benchmark"] = SIXTY_FORTY
                    db.execute("UPDATE versions SET meta=? WHERE id=?", (json.dumps(meta), identity))
        finally:
            db.close()
