"""Stand-ins for what a strategy run touches outside Quant: Market's capability table, its history and series, Developer's documents and the
library's version details."""
from __future__ import annotations

import json
import shutil
import time
from pathlib import Path

from talyx_sdk import ContractError

from modules.quant.backend.object_routes import Services
from modules.quant.backend.work_records import WorkRecords
from tests.unit.test_quant_object_runs import DATA, INSTRUMENT, SERIES, FakeContext
from vocabulary.instruments import MarketInstrument

SERIES_OF = SERIES

RUNS = Path(__file__).resolve().parent / "runs"
ROUTE = {"capability": "ohlcv", "timeframe": "1h", "source": {"sourceId": "fixture", "name": "Fixture"},
         "local": None, "status": "not_stored", "problem": None, "revision": 0, "publishDelay": None, "publishedThrough": str(time.time_ns()), "available": {"periods": ["1m", "1d"], "earliest": None, "live": "stream"}}
OKX_ETH = MarketInstrument(venueId="okx", symbol="ETH/USDT:USDT", displaySymbol="ETH/USDT:USDT", base="ETH", quote="USDT", settle="USDT",
                          assetClass="crypto", marketType="swap", contractOrientation="linear").to_dict()


class Documents:
    """Developer's open documents: `list` pages them, `save` saves one or says it conflicts."""

    def __init__(self, rows=None, conflicts=()):
        self.rows, self.conflicts, self.saved = list(rows or []), set(conflicts), []

    def listing(self, args):
        return {"documents": self.rows, "nextCursor": None}

    def save(self, args):
        self.saved.append(args["documentId"])
        if args["documentId"] in self.conflicts:
            return {"status": "conflict", "message": "changed on disk"}
        return {"status": "saved", "message": None}


class StrategyContext(FakeContext):
    """The Host as a strategy run sees it, with Market's tables set by the test."""

    def __init__(self, tmp_path, *, routes=None, series=None, documents=None):
        super().__init__(tmp_path, self.no_export)
        self.exported = []
        self.routes = [ROUTE] if routes is None else routes
        self.series = series if series is not None else {"funding_rate": {INSTRUMENT["instrumentId"]}}
        self.documents = documents or Documents()
        original = self.actions.call

        async def call(name, args):
            if name == "developer.documents.list":
                self.calls.append(name)
                return self.documents.listing(args)
            if name == "developer.documents.save":
                self.calls.append(name)
                return self.documents.save(args)
            return await original(name, args)
        self.actions.call = call

    async def no_export(self, args):
        self.exported.append(args)
        raise ContractError("unavailable", "the fake Market does not export")

    async def coverage(self, args):
        identity = args["symbols"][0]
        rows = list(self.routes) + [{**ROUTE, "capability": name, "timeframe": None,
                                   "available": {"periods": [], "earliest": None, "live": "stream"}}
                                  for name, identities in self.series.items() if identity in identities]
        from modules.quant.backend.public_settings import timestamp
        wanted = {"bars":"ohlcv"}.get(args.get("data"),args.get("data"))
        selected = [{**r,"publishedThrough":timestamp(int(r["publishedThrough"])//1_000_000)} for r in rows if wanted is None or r["capability"] == wanted]
        cols = list(ROUTE)
        return {"columns":[{"name":k} for k in cols],"rows":[[r[k] for k in cols] for r in selected],"nextCursor":None}


def services(tmp_path, **more):
    ctx = StrategyContext(tmp_path, **more)
    records = WorkRecords(tmp_path / "records")
    return ctx, records, Services(ctx, records)


async def strategy(shell, source=None, data=DATA, name="Trend", run=None, params=None):
    """A strategy object with the given code (the template when None) and settings."""
    created = await shell.store.create({"kind": "strategy", "name": name})
    obj = shell.store.read(created["id"])
    if source is not None:
        Path(obj["directory"], obj["entry"]).write_text(source, encoding="utf-8")
    await shell.store.update_settings(created["id"], {"data": data, **({"run": run} if run else {})})
    return created["id"]


def insert_run(shell, object_id, name="single", *, number_of=None, state="completed", settings=None, params=None, created=None, **more):
    """A finished run record whose output folder is a synthetic run exported by `make_fixtures.py`."""
    obj = shell.store.read(object_id)
    identity = "run-" + (more.pop("identity", None) or f"{name}-{int(time.time() * 1e6)}")
    created = created or int(time.time() * 1000)
    record = shell.runs.records.put("qrun", {
        "id": identity, "objectId": object_id, "objectKind": "strategy", "name": obj["name"], "runKind": "run", "mode": "run", "workspaceId": shell.store.workspace(),
        "state": state, "stage": "done", "createdAt": created, "endedAt": created + 12_000, "params": params or {}, "overrides": {},
        "settings": settings or obj["settings"], "message": None, "error": None,
        "code": {"digest": "c" * 64, "entry": obj["entry"]}, "common": None, "data": {"runSettings": None, "sources": [], "library": [], "models": []},
        "environment": None, "recipe": None, "metrics": {}, "starred": False, "note": "", "exitCode": 0, **more})
    target = shell.runs.directory(identity)
    (target / "output" / "strategy").mkdir(parents=True)
    for item in (RUNS / name).iterdir():
        destination = target / "output" / item.name if item.name == "result-index.json" else target / "output" / "strategy" / item.name
        shutil.copy(item, destination)
    if state == "failed":  # where it stopped stays in the record, as the run's ending writes it
        index = json.loads((target / "output" / "result-index.json").read_text(encoding="utf-8"))
        record = shell.runs.update(identity, failureContext=index.get("failureContext") or {})
    return record


def library(versions):
    """A stand-in library: `versions` maps version id to its details ({name, kind, version, state, retired, heldOutFrom, trainingCutoff, newer})."""
    class Fake:
        def read_version(self, version_id):
            if version_id not in versions:
                raise ContractError("not_found", "No such version.")
            return {"versionId": version_id, **versions[version_id]}

        def resolve(self, name, kind="model"):
            found = [(key, item) for key, item in versions.items() if item["name"] == name and not item.get("retired")]
            key, item = max(found, key=lambda pair: pair[1]["version"])
            return {"versionId": key, "version": item["version"]}

        def resolve_values(self, name):
            return self.resolve(name, "factor")
    return Fake()
