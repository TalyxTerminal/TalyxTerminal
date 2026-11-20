import asyncio
import hashlib
from types import SimpleNamespace

import pytest
from talyx_sdk import ContractError

from modules.chart.backend import actions, research, wire
from modules.chart.backend.models import ResearchLayer
from tests.unit.chart.fake_market import BTC, ETH
from tests.unit.chart.test_public_actions import AI, opened


def layer():
    return {"id": "run:1", "label": "Crossover #1", "instrumentId": BTC["instrumentId"],
            "timeframe": "1d", "adjustment": "raw",
            "window": {"from": "2025-06-02T00:00:00Z", "to": "2025-06-03T21:00:00Z"},
            "panes": [{"key": "equity", "title": "Equity", "position": "above"}],
            "series": [{"key": "nav", "name": "NAV", "pane": "equity", "role": "main", "style": "line",
                        "points": [{"time": "2025-06-02T20:00:00Z", "value": 102.5}]}],
            "markers": [{"time": "2025-06-02T13:30:00Z", "side": "buy", "price": 104.25, "quantity": 3}]}


def test_create_reuses_snapshot_and_saved_workspace_restores_its_values(tmp_path):
    async def run():
        _, app = opened(tmp_path)
        snapshot = layer()

        async def write(**op):
            return await actions.write_chart(app, {"operation": op}, AI)

        first = await write(op="create", layers=[snapshot])
        chart_id = first["chartId"]
        snapshot["series"][0]["points"][0]["value"] = 108.75
        file = tmp_path / "layer.json"
        content = ResearchLayer.model_validate(snapshot).model_dump_json(by_alias=True).encode()
        file.write_bytes(content)
        ref = {"path": str(file), "bytes": len(content), "sha256": hashlib.sha256(content).hexdigest()}
        reused = await write(op="create", layers=[ref])
        assert first["layerIds"] == reused["layerIds"] == ["run:1"]
        assert reused["chartId"] == chart_id
        assert app.store.charts("w1") == [chart_id]
        doc = app.charts.entry("w1", chart_id).document
        assert doc.series["adjustment"] == "raw" and doc.series["timeframe"] == "1d"
        assert doc.instrument["instrumentId"] == BTC["instrumentId"]
        await write(op="edit", chartId=chart_id, baseRevision=reused["revision"],
                    changes=[{"kind": "layer", "layer": {**snapshot, "label": "Reviewed"}}])
        revision = app.charts.entry("w1", chart_id).revision
        app.charts.save("w1", [{"chartId": chart_id, "revision": revision}])
        await write(op="remove", chartId=chart_id, baseRevision=revision, layers=["run:1"])
        assert not app.charts.entry("w1", chart_id).document.layers
        app.charts.workspace_closed("w1")
        restored = await app.stored_layer("w1", wire.LayerRead(chartId=chart_id, id="run:1"))
        assert restored == {**snapshot, "label": "Reviewed"}
        assert await actions.read_chart(app, {"view": "layer", "chartId": chart_id, "layerId": "run:1"}, AI) == {"layer": restored}
        app.store.close()
    asyncio.run(run())


def test_switching_back_to_the_layer_instrument_restores_its_adjustment(tmp_path):
    # BTC's default series is not_applicable; the layer was drawn on raw prices and shows only on them.
    async def run():
        _, app = opened(tmp_path)
        created = await actions.write_chart(app, {"operation": {"op": "create", "layers": [layer()]}}, AI)
        chart_id, revision = created["chartId"], created["revision"]
        for instrument, adjustment in ((ETH, "not_applicable"), (BTC, "raw")):
            command = {"type": "selection.set", "instrumentId": instrument["instrumentId"]}
            revision = (await actions.write_chart(app, {"operation": {"op": "edit", "chartId": chart_id,
                        "baseRevision": revision, "commands": [command]}}, AI))["revision"]
            assert app.charts.entry("w1", chart_id).document.series["adjustment"] == adjustment
        app.store.close()
    asyncio.run(run())


def test_layer_reports_count_exceeding_each_limit():
    data = layer()
    data["markers"] *= 20_001
    with pytest.raises(ContractError, match="20,001") as markers:
        research.check_layer(ResearchLayer.model_validate(data))
    assert markers.value.code == "too_large"
    data["markers"] = []
    data["series"][0]["points"] *= 200_001
    with pytest.raises(ContractError, match="NAV contains 200,001") as points:
        research.check_layer(ResearchLayer.model_validate(data))
    assert points.value.code == "too_large"


def test_market_window_counts_real_bars_across_pages(monkeypatch):
    # 3 bars across a 4-minute window: one minute is missing and is never counted as a candle.
    monkeypatch.setattr(research, "BAR_LIMIT", 2)
    monkeypatch.setattr(research, "PAGE_SIZE", 2)
    async def page(request):
        assert request["end"] == "240000000000"
        times = {"0": ["0", "60000000000"], "60000000001": ["180000000000"]}[request["start"]]
        return {"columns": {"time": times, "closeTime": times,
                            **{field: ["1"] * len(times) for field in ("open", "high", "low", "close", "volume")}}}
    market = SimpleNamespace(client=SimpleNamespace(read_series=page))
    with pytest.raises(ContractError, match="Window bars contains 3 entries, 1 over") as exc:
        asyncio.run(research.history(market, BTC, {}, 0, 240000))
    assert exc.value.code == "too_large"
