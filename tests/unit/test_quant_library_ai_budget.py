"""Every library read the AI may call fits the Host's AI response budget on a realistic library, and still says what the AI acts on.

On 70 names each read was 30 to 530 KB ("Response exceeds AI budget": the Agent could read nothing of the library). Here: 650 names, a factor, a
model reading it and an allocation, two versions each, sixty periods with failures, users and differing code, called as the AI through the same
action handlers the platform calls. The page in the app keeps the full answers."""
from __future__ import annotations

import asyncio
import hashlib
from types import SimpleNamespace

import pytest
from talyx_sdk import wire

from tests.unit.quant_library_fakes import DAY, close_of

N = 650
DAYS = 80
SYMBOLS = [f"S{number:04d}" for number in range(N)]
AI = SimpleNamespace(caller=SimpleNamespace(origin="ai", module=None, workspace_id="ws", ai={"client": "Agent"}, run=None))
PERSON = SimpleNamespace(caller=SimpleNamespace(origin="user", module=None, workspace_id="ws", ai=None, run=None))


def ident(number):
    return "vi1_" + hashlib.sha256(f"instrument {number}".encode()).hexdigest()


def instrument(number):
    """A data-settings symbol as the store keeps it: the whole instrument object (about 430 bytes each)."""
    symbol = SYMBOLS[number]
    return {"instrument": {"instrumentId": ident(number), "identityVersion": 1, "sourceId": "yfinance", "venueId": "xnys", "symbol": symbol, "displaySymbol": symbol,
                           "marketType": "spot", "assetClass": "equity", "base": symbol, "quote": "USD", "settle": None, "contractOrientation": "none", "contractSize": None,
                           "expiry": None, "strike": None, "optionSide": None, "underlyingSymbol": None, "calendarId": "XNYS", "adjustmentEligible": True}}


INFO = {"timeframe": "1d", "calendarId": "XNYS", "sessionScope": "regular", "timezone": "America/New_York", "annualization": 252, "since": DAY, "until": DAYS * DAY,
        "instruments": [{"column": SYMBOLS[number], "instrumentId": ident(number), "displaySymbol": SYMBOLS[number], "sourceId": "yfinance"} for number in range(N)]}
DATA = {"symbols": [instrument(number) for number in range(N)], "timeframe": "1d", "since": "2018-01-02", "until": "2026-09-30", "heldOutFrom": "2025-06-01"}
POOL = [{"currency": "USD", "from": "", "group": "Stocks" if number % 3 else "Bonds", "instrumentId": ident(number), "name": SYMBOLS[number], "proxy": "", "ticker": SYMBOLS[number]}
        for number in range(N)]


def value(seed, day, number):
    return int.from_bytes(hashlib.sha256(f"{seed}:{day}:{number}".encode()).digest()[:4], "little") / 2 ** 32 - .5


def rows(seed, field, days):
    return [(ident(number), field, day * DAY, value(seed, day, number), close_of(day), close_of(day), "history") for day in range(1, days + 1) for number in range(N)]


def shares(seed):
    raw = {ident(number): abs(value(seed, 0, number)) for number in range(N)}
    total = sum(raw.values())
    return {key: share / total for key, share in raw.items()}


def spec(name, kind, version_id, *, fields, meta=None, published=None, data_rows=None, holds=()):
    base = {"producer": "fake", "fields": fields, "timeframe": "1d", "info": INFO, "data": DATA, "edge": "close", "publication": {"kind": kind, "name": name, "field": fields[0]},
            "horizon": {"holding": 5, "delay": 1}, "publishedThrough": DAYS * DAY, "measure": "Volatility" if kind == "allocation" else "Rank IC 5D",
            "universe": "S0000, S0001, S0002, S0003, S0004 and 645 more", "settingsText": "650 names, 5D, industry+size", "notes": ["Published from the Agent's run."],
            "origin": {"objectId": "o-" + name, "runId": "r-" + version_id, "objectName": name, "objectKind": kind, "runNumber": 3},
            "params": {"window": 20, "neutralize": "industry+size", "features": [f"feature_{number}" for number in range(158)]}, "inputs": []}
    return {"name": name, "kind": kind, "meta": {**base, **(meta or {})}, "rows": data_rows if data_rows is not None else rows(version_id, fields[0], DAYS),
            "objectId": "o-" + name, "runId": "r-" + version_id, "workspaceId": "ws", "state": "updating", "cursor": DAYS * DAY, "versionId": version_id,
            "checks": published or {"metric": "rank_ic", "value": 0.0153, "t": 2.1, "ls": 0.05, "turnover": 0.4, "names": N, "holding": 5},
            "holds": [{"versionId": held} for held in holds]}


@pytest.fixture(scope="module")
def library(tmp_path_factory):
    from modules.quant.backend.library import Library
    from modules.quant.backend.library_routes import handle_library_actions
    from tests.unit.quant_library_fakes import Context
    root = tmp_path_factory.mktemp("library")
    library = Library(Context(), SimpleNamespace(root=root))
    store = library.store
    allocation_meta = {"allocation": {"settings": {"lookback": 60}, "pool": POOL}, "rebalance": {"every": "month"},
                       "params": {"lookback": 60, "objective": "min_risk", "pool": POOL, "costs": [{"bps": 5.0, "group": "Stocks"}]}}
    allocation_published = {"metric": "volatility", "value": 0.08, "sharpe": 1.1, "maxDrawdown": -0.07, "turnover": 5.2, "vsEqualWeight": -0.14, "riskShares": shares("p")}
    store.commit_versions([spec("Momentum", "factor", "ver-f1", fields=["value"], meta={"formula": "-Ref($close, 20) / $close + 1"}),
                           spec("Momentum", "factor", "ver-f2", fields=["value"], meta={"formula": "-(Ref($close, 20) / $close - 1) / Std($close, 20)"})])
    model_meta = {"inputs": [{"itemId": None, "versionId": "ver-f2", "name": "Momentum", "version": 2, "parameter": "momentum"}],
                  "pipeline": {"kind": "model", "trainedThrough": 60 * DAY, "heldOutFrom": 61 * DAY, "reads": ["ver-f2"], "algorithm": "lightgbm"}}
    store.commit_versions([spec("Blend", "model", "ver-m1", fields=["prediction", "label"], meta=model_meta, holds=["ver-f2"]),
                           spec("Blend", "model", "ver-m2", fields=["prediction", "label"], meta=model_meta, holds=["ver-f2"])])
    store.commit_versions([spec("Risk parity", "allocation", version_id, fields=["weight"], meta=allocation_meta, published=allocation_published,
                                data_rows=[(ident(number), "weight", day * DAY, 1 / N, close_of(day), close_of(day), "history") for day in range(1, DAYS + 1) for number in range(N)])
                           for version_id in ("ver-a1", "ver-a2")])
    for version_id in ("ver-f1", "ver-f2", "ver-m1", "ver-m2", "ver-a1", "ver-a2"):
        periods = [{"label": day * DAY, "due_at": close_of(day) + 3_600_000, "result": "fail" if day % 7 == 0 else "late" if day % 3 == 0 else "ok", "ready_at": close_of(day) + 7_200_000,
                    "names": N, "distinct_values": N, "tries": 3 if day % 7 == 0 else 1,
                    "why": f"The code failed on the new data: KeyError: 'epsTrend' (factor.py line {day})" if day % 7 == 0 else "TALYX was not open when it was due" if day % 3 == 0 else None}
                   for day in range(DAYS - 59, DAYS + 1)]
        store.put_periods(version_id, periods)
        for attempt in range(60):
            store.log_update(version_id, "failed", f"The code failed on the new data: KeyError: 'epsTrend' (attempt {attempt})",
                             failure={"label": DAYS * DAY, "location": {"file": "factor.py", "line": 10},
                                      "traceback": "Traceback (most recent call last):\n" + "".join(f'  File "frame{line}.py", line {line}, in step\n    value = data[key]\n' for line in range(120))
                                      + "KeyError: 'epsTrend'\n", "output": "reading estimates\n" * 200})
        after = {"kind": "signal", "value": -0.004, "se": 0.0011, "t": -3.6, "ls": -0.02, "names": float(N), "settled": 70, "settledDays": 70, "pending": 5}
        if version_id.startswith("ver-a"):
            now = shares("n" + version_id)
            worst = max(now, key=lambda key: abs(now[key] - allocation_published["riskShares"][key]))
            after = {"kind": "allocation", "value": 0.13, "published": 0.08, "settled": 47, "pending": 0, "multiple": 1.6, "drift": True, "sharpe": 0.9, "turnover": 1.9, "rebalances": 2,
                     "benchmark": {"name": "equal weight", "published": -0.14, "since": 0.02}, "shares": {"published": allocation_published["riskShares"], "now": now},
                     "worst": {"asset": worst, "delta": now[worst] - allocation_published["riskShares"][worst]}}
        store.mark(version_id, after=after)
        source = library.version_directory(version_id) / "source"
        source.mkdir(parents=True, exist_ok=True)
        (source / "factor.py").write_text("".join(f"step_{line} = data['{version_id}'][{line}]\n" for line in range(500)), encoding="utf-8")
    for owner in range(30):
        library.acquire("ver-f2", f"qobject:strategy-{owner}", "ws", kind="strategy", name=f"Top 50 by momentum, variant {owner}", how="research, pinned v2")
    handlers = {}
    ctx = SimpleNamespace(handlers=handlers)
    handle_library_actions(ctx, SimpleNamespace(library=library, publisher=None, lists=None, history=None, research_bars=None,
                                                source=SimpleNamespace(search=None, history=None, live=None), home=None))
    items = {row["name"]: row["itemId"] for row in library.list({})["items"]}
    return SimpleNamespace(library=library, handlers=handlers, items=items)


def call(world, action, args, caller=AI):
    return asyncio.run(world.handlers[action](args, caller))


def answered(out):
    """What the Host measures: the result inside its answer, as JSON (host/ai.py bounded)."""
    return len(wire.encode(out).decode("utf-8"))


VERSIONS = ("ver-f1", "ver-f2", "ver-m1", "ver-m2", "ver-a1", "ver-a2")


def test_each_library_read_is_paged_for_all_callers(library):
    for item in library.items.values():
        for origin in (AI,PERSON):
            args = {"itemId":item}
            result = call(library,"read_library_item",args,origin)
            assert answered(result)<=18000 and "figures" not in result
            assert result == call(library,"read_library_item",args,PERSON)
    listing = call(library,"list_library",{})
    assert {row["name"] for row in listing["items"]} == {"Momentum","Blend","Risk parity"}
    assert answered(listing)<=18000
    for vid in VERSIONS:
        log = call(library,"read_library_log",{"versionId":vid})
        assert answered(log)<=18000
        if log["nextCursor"] is not None:
            next_page = call(library,"read_library_log",{"versionId":vid,"cursor":log["nextCursor"]})
            assert log["rows"] != next_page["rows"] and log["total"]==next_page["total"]


def test_private_library_view_keeps_its_full_materials(library):
    version = library.library.read_version("ver-a2")
    assert len(version["data"]["symbols"]) == N and len(version["published"]["riskShares"])==N
    assert library.library.detail(library.items["Momentum"])["figures"] is not None
