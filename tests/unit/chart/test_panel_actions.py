"""Public actions share the chart document, visible window and templates used by the panel."""
import asyncio
import hashlib
from pathlib import Path
from types import SimpleNamespace

import pyarrow.parquet as pq
import pytest
from talyx_sdk import ContractError

from modules.chart.backend import changes, entry, models, wire
from modules.chart.backend.actions import iso
from tests.unit.chart.fake_market import BTC
from tests.unit.chart.test_chart_flow import Reader
from tests.unit.chart.test_pine_engine import bars
from tests.unit.chart.test_public_actions import AI, opened


def test_chart_commands_imports_copies_and_global_templates(tmp_path, monkeypatch):
    async def main():
        _, app = opened(tmp_path)
        monkeypatch.setattr(entry, "app", app)
        call = SimpleNamespace(caller=AI.caller, workspace_id="w1", call_id="create")

        async def write(**op):
            return await entry.write_chart(models.WriteChartInput(operation=op), call)

        async def template(**op):
            return await entry.write_template(models.WriteTemplate(operation=op), call)

        chart_id = (await write(op="create", symbol="okx:BTC/USDT", timeframe="1h", importId="trial"))["chartId"]
        chart = app.charts.entry("w1", chart_id)
        assert chart.document.series["importId"] == "trial"
        call.call_id = "edit"
        drawing = {"id": "line", "tool": "hline", "points": [{"time": 1, "price": 20}], "settings": {"extendLeft": True}}
        indicator = {"id": "osc", "definition": {"source": "builtin", "id": "rsi"}, "paneId": "p1"}
        await write(op="edit", chartId=chart_id, baseRevision=1, commands=[
            {"type": "display.replace", "display": {"precision": 5}},
            {"type": "display.update", "patch": {"timeZone": "UTC", "scale": {"mode": "log"}}},
            {"type": "drawing.add", "value": drawing}, {"type": "drawing.duplicate", "ids": ["line"]},
            {"type": "drawing.update", "id": "line", "patch": {"settings": {"extendRight": True}}},
            {"type": "drawing.reorder", "id": "line", "direction": 1},
            {"type": "drawing.bulk", "locked": True},
            {"type": "indicator.add", "value": indicator}, {"type": "indicator.duplicate", "id": "osc"},
            {"type": "indicator.update", "id": "osc", "patch": {"styles": {"rsi": {"color": "#abcdef"}}}},
            {"type": "indicator.reorder", "id": "osc", "direction": -1},
            {"type": "pane.move", "id": "p1", "direction": 1}, {"type": "pane.update", "id": "p1", "height": 4}])
        one = await entry.read_chart(models.ReadChartInput(view="one", chartId=chart_id), call)
        assert one["display"] == {"precision": 5, "timeZone": "UTC", "scale": {"mode": "log"}}
        assert one["panes"][-1]["id"] == "p1" and one["panes"][-1]["height"] == 4
        assert one["indicators"][0]["styles"]["rsi"]["color"] == "#abcdef"
        assert all(item["locked"] for item in one["drawings"])
        assert one["drawings"][-1]["settings"] == {"extendRight": True}
        call.call_id = "copy"
        copied = (await write(op="create", copyOf=chart_id))["chartId"]
        clone = app.charts.entry("w1", copied)
        assert clone.document.drawings == chart.document.drawings and clone.document.series == chart.document.series
        changes.revert(app, "copy", "undo")
        assert app.store.chart(copied) is None
        changes.revert(app, "copy", "redo")
        assert app.charts.entry("w1", copied).document.drawings == chart.document.drawings

        call.call_id = "drawing-settings"
        await write(op="edit", chartId=copied, baseRevision=1, changes=[
            {"kind": "drawing", "id": "line", "settings": {"extendRight": False}}])
        assert next(item for items in app.charts.entry("w1", copied).document.drawings.values()
                    for item in items if item.id == "line").settings == {"extendRight": False}

        templates = {}
        for kind in ("indicators", "drawing", "chart"):
            call.call_id = f"save-{kind}"
            target = {"drawingId": "line"} if kind == "drawing" else {}
            saved = await template(op="save", chartId=chart_id, kind=kind, name=kind, **target)
            templates[kind] = saved["id"]
            actual = await entry.read_chart(models.ReadChartInput(view="templates", templateId=saved["id"]), call)
            assert actual == saved
        all_templates = await entry.read_chart(models.ReadChartInput(view="templates"), call)
        assert {item["id"] for item in all_templates["templates"]} == set(templates.values())

        call.call_id = "replace"
        replaced = [{"type": "drawings.replace", "drawings": [drawing]},
                    {"type": "indicators.replace", "indicators": [], "panes": [{"id": "price"}]},
                    {"type": "display.update", "patch": {"precision": 2, "timeZone": "Asia/Singapore"}}]
        with pytest.raises(ContractError, match="op=remove"):
            await write(op="edit", chartId=copied, baseRevision=2, commands=replaced)
        await write(op="remove", chartId=copied, baseRevision=2, commands=replaced)
        for revision, (kind, template_id) in enumerate(templates.items(), start=3):
            call.call_id = f"apply-{kind}"
            await template(op="apply", chartId=copied, templateId=template_id, baseRevision=revision,
                           **({"drawingId": "line"} if kind == "drawing" else {}))
        restored = app.charts.entry("w1", copied).document
        assert restored.indicators == chart.document.indicators
        assert restored.display == {"precision": 5, "timeZone": "Asia/Singapore", "scale": {"mode": "log"}}
        assert next(iter(restored.drawings.values()))[0].settings == {"extendRight": True}
        call.call_id = "delete-template"
        await template(op="delete", templateId=templates["drawing"])
        changes.revert(app, call.call_id, "undo")
        assert app.template(templates["drawing"])["kind"] == "drawing"
        changes.revert(app, call.call_id, "redo")
        assert len(app.templates()) == 2
        state = await entry.read_chart(models.ReadChartInput(view="workspace"), call)
        assert state == {"health": app.health(), "alertDefaults": {"toast": True, "sound": True}}
        await app.close()
    asyncio.run(main())


def test_public_backfill_export_and_replay_share_the_window(tmp_path, monkeypatch):
    async def main():
        market, app = opened(tmp_path)
        monkeypatch.setattr(entry, "app", app)
        chart_id = (await app.create_chart("w1", wire.ChartCreate(instrumentId=BTC["instrumentId"])))["chartId"]
        reader = Reader(await app.chart_stream("w1", chart_id))
        rows = bars(1020, 3)
        market.older = rows[:20]
        session = await market.session(BTC["instrumentId"])
        session.replace(rows[20:], has_more_before=True)
        await reader.until(lambda item: item["type"] == "indicator")
        backfill = asyncio.create_task(entry.backfill_chart(models.BackfillChart(chartId=chart_id, bars=20), AI))
        while len(market.sessions) < 2:
            await asyncio.sleep(0.01)
        market.sessions[-1].replace(rows[20:], has_more_before=True)
        assert (await backfill)["loaded"] == 20
        await reader.until(lambda item: item["type"] == "indicator" and len(item["time"]) == 1020)
        read = await entry.read_data(models.ReadDataInput(chartId=chart_id, limit=1), AI)
        assert read["window"]["first"] == iso(rows[0]["time"])

        async def replay(op, **fields):
            return await entry.replay_chart(models.ReplayInput(operation={"op": op, "chartId": chart_id, **fields}), AI)

        assert (await replay("start", at=iso(rows[9]["time"])))["at"] == rows[9]["time"]
        assert (await replay("step", bars=3))["at"] == rows[12]["time"]
        assert (await entry.read_data(models.ReadDataInput(chartId=chart_id, limit=1), AI))["rows"][0][0] == iso(rows[12]["time"])
        assert (await replay("stop"))["active"] is False
        result = await entry.export_chart(wire.ExportChart(chartId=chart_id, format="parquet"), AI)
        ref = result["file"]
        path = Path(ref["path"])
        assert path.stat().st_size == ref["bytes"] and hashlib.sha256(path.read_bytes()).hexdigest() == ref["sha256"]
        table = pq.read_table(path)
        assert result["rows"] == table.num_rows == 1020
        assert table.column("time").to_pylist() == [row["time"] for row in rows]
        assert table.column("volume: volume").to_pylist() == [row["volume"] for row in rows]
        await reader.stream.aclose()
        await app.close()
    asyncio.run(main())
