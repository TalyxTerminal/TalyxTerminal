import asyncio
from types import SimpleNamespace

import pandas as pd
import pytest
from talyx_sdk import ContractError

from modules.quant import talyx_quant as q
from modules.quant.backend.research_bars import ResearchBars
from tests.unit.quant_runtime_fakes import adapt


class Facts:
    async def get(self,args):
        from test_quant_object_runs import INDEX
        return {"items":[{"status":"found","symbol":"^GSPC","instrument":INDEX} if s=="^GSPC" else {
                "status":"not_found","symbol":s} for s in args["symbols"]],"nextCursor":None}


def bars(items=(), lists=()):
    item = ResearchBars.__new__(ResearchBars)
    item.ctx = adapt(SimpleNamespace(require=lambda name: Facts()))
    item.records = SimpleNamespace(list=lambda kind: list(lists))
    return item


def test_research_index_resolves_to_markets_exact_identity():
    from test_quant_object_runs import INDEX

    from modules.quant.backend.public_settings import store_data
    assert asyncio.run(store_data(bars().ctx,{"index":{"symbol":"^GSPC"}},{}))["index"]["instrument"] == INDEX
    with pytest.raises(ContractError,match="Market cannot identify"):
        asyncio.run(store_data(bars().ctx,{"index":{"symbol":"Missing"}},{}))

def test_q_data_by_research_list_name():
    members = [{"instrumentId": "i1", "displaySymbol": "AAPL"}]
    item = bars([], [{"name": "My list", "members": members}, {"name": "Gone", "members": [], "deleted": True}])
    assert item.list_named("my LIST") == [{"instrument": members[0]}]
    with pytest.raises(ContractError):
        item.list_named("Gone")


def test_q_data_asks_for_exactly_one_of_symbols_universe_list():
    for args in ({}, {"symbols": ["a:B"], "index":{"symbol":"^GSPC"}}):
        with pytest.raises(ContractError):
            asyncio.run(bars([]).export({**args, "timeframe": "1d", "since": "2024-01-01", "until": "2024-02-01", "adjusted": True}))


def test_q_data_columns_read_as_symbols_and_the_ids_stay_in_attrs(monkeypatch, tmp_path):
    frame = pd.DataFrame({"label": [1, 1, 2, 2], "column": ["id1", "id2", "id1", "id2"], "close": [1.0, 2.0, 3.0, 4.0]})
    frame.to_parquet(tmp_path / "bars.parquet")
    info = {"instruments": [{"column": "id1", "instrumentId": "id1", "displaySymbol": "AAPL"}, {"column": "id2", "instrumentId": "id2", "displaySymbol": "MSFT"}], "timeframe": "1d"}
    (tmp_path / "info.json").write_text(__import__("json").dumps(info), encoding="utf-8")   # the information comes by file, as the bars do
    seen = {}

    def channel(name, value, timeout=None):
        if name == "quant.read_research_bars":
            seen.update(value)
            return {"taskId":"r1"}
        assert name == "platform.tasks.get" and value == {"taskId":"r1"}
        return {"state":"completed","result":{"bars":{"path":str(tmp_path/"bars.parquet")},"info":{"path":str(tmp_path/"info.json")}}}

    monkeypatch.setattr(q, "_channel", lambda: channel)
    monkeypatch.setattr(q, "_loaded", [])
    table = q.data(index={"symbol":"^GSPC"}, timeframe="1d", start="2024-01-01", end="2024-02-01").close
    assert list(table.columns) == ["AAPL", "MSFT"] and table.attrs["instruments"] == {"AAPL": "id1", "MSFT": "id2"}
    assert seen["index"] == {"symbol":"^GSPC"} and "symbols" not in seen and q._loaded[0]["call"].startswith("q.data(index=")
    with pytest.raises(TypeError):
        q.data(symbols="a:B", index={"symbol":"^GSPC"}, timeframe="1d", start="2024-01-01", end="2024-02-01")


def test_a_long_bars_read_is_followed_and_can_be_stopped(monkeypatch):
    calls = []
    def channel(name,value,timeout=None):
        calls.append((name,value))
        if name == "quant.read_research_bars":
            return {"taskId":"q1"}
        if name == "platform.tasks.get":
            raise KeyboardInterrupt
        assert name == "platform.tasks.cancel" and value == {"taskId":"q1"}
    monkeypatch.setattr(q,"_channel",lambda:channel)
    with pytest.raises(KeyboardInterrupt):
        q._read_bars({}, {}, every=0)
    assert [name for name,_ in calls] == ["quant.read_research_bars","platform.tasks.get","platform.tasks.cancel"]


def test_the_same_bars_asked_again_are_the_freeze_while_markets_revisions_hold(monkeypatch, tmp_path):
    """the same quarter asked twice went through Market and arranged every name each time; now the second read is one batch of
    revision reads, and a series Market has revised freezes again."""
    from modules.quant.backend import research_data
    prepared, revision = [], {"value": 1}

    async def prepare(ctx, data, owner, directory, progress=None):
        prepared.append(data)
        (directory / "bars.parquet").write_bytes(b"bars" + bytes([len(prepared)]))
        return {"research": {"barsPath": str(directory / "bars.parquet"), "info": {"revisions": {"s1": revision["value"]}}}}

    async def unchanged(ctx, info):
        return info["revisions"]["s1"] == revision["value"]
    monkeypatch.setattr(research_data, "prepare", prepare)
    monkeypatch.setattr(research_data, "unchanged", unchanged)
    item = bars()
    item.root, item.locks = tmp_path, {}
    data = {"symbols": [{"instrument": {"instrumentId": "i1"}}], "timeframe": "1d", "since": "2024-01-01", "until": "2024-04-01", "adjustment": "adjusted"}
    first = asyncio.run(item.frozen(data, "a", None))
    again = asyncio.run(item.frozen(data, "b", None))
    assert again == first and len(prepared) == 1 and (first / "bars.parquet").read_bytes() == b"bars\x01"
    revision["value"] = 2
    revised = asyncio.run(item.frozen(data, "c", None))
    assert revised == first and len(prepared) == 2 and (revised / "bars.parquet").read_bytes() == b"bars\x02"
    other = asyncio.run(item.frozen({**data, "since": "2024-02-01"}, "d", None))
    assert other != first and len(prepared) == 3
    assert sorted(path.name for path in (tmp_path / "frozen").iterdir()) == sorted([first.name, other.name])
