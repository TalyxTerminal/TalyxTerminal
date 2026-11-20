"""A real Chart restores the objects belonging to a recorded call, including after restarting the Host."""

from datetime import UTC, datetime, timedelta

import pytest
from talyx_sdk.errors import ContractError

from tests.platform.harness import real_chart, route, run


def test_chart_restores_the_original_conversations_changes_after_restart(tmp_path, monkeypatch):
    async def scenario():
        yesterday = (datetime.now(UTC) - timedelta(days=1)).isoformat()
        monkeypatch.setattr("host.records._now", lambda: yesterday)
        async with real_chart(tmp_path) as platform:
            ws = await platform.workspace()
            module = {"origin": "module", "module": "client", "workspaceId": ws}
            request = {"workspaceId": ws, "client": "research", "conversation": "one"}
            chart = await route(platform, "chart", "POST", "/charts/create", ws, {"timeframe": "1h"})
            key = chart["chartId"]
            before = chart["document"]
            change = {"operation": {"op": "edit", "chartId": key, "baseRevision": chart["revision"],
                                    "changes": [{"kind": "indicator", "name": name, "params": {"period": period}}
                                                for name, period in (("rsi", 14), ("sma", 20))]}}
            receipt = await platform.call("platform.ai.call", {**request, "action": "chart.write_chart",
                                                              "input": change}, caller=module)
            call_id = receipt["callId"]
            edited = await route(platform, "chart", "POST", "/charts/open", ws, {"chartId": key})
            after = edited["document"]
            assert len(after["indicators"]) == len(before["indicators"]) + 2
            await route(platform, "chart", "POST", "/charts/save", ws,
                        {"charts": [{"chartId": key, "revision": edited["revision"]}]})
            created = await platform.call("platform.ai.call", {**request, "action": "chart.write_watchlist",
                                          "input": {"operation": {"op": "create", "name": "Trial list"}}}, caller=module)
            watch_id = created["result"]["watchlist"]["id"]
            assert created["callId"] != call_id
            renamed = await platform.call("platform.ai.call", {**request, "action": "chart.write_watchlist",
                                          "input": {"operation": {"op": "update", "watchlistId": watch_id,
                                          "revision": created["result"]["watchlist"]["revision"], "name": "Renamed"}}},
                                          caller=module)
            alert = await platform.call("platform.ai.call", {**request, "action": "chart.write_alert",
                                        "input": {"operation": {"op": "create", "name": "Above 100",
                                                               "target": {"chartId": key},
                                                               "conditions": [{"left": {"kind": "price"},
                                                               "op": "crossing_up", "right": {"kind": "value",
                                                                                              "value": 100}}]}}},
                                        caller=module)

        monkeypatch.undo()
        async with real_chart(tmp_path) as platform:
            assert (await platform.host.calls.lookup([call_id]))[0]["startedAt"] == yesterday
            await platform.call("chart.write_chart", {"operation": {**change["operation"],
                                "baseRevision": edited["revision"], "changes": [{"kind": "chart_type",
                                "chartType": "line"}]}}, workspace=ws)
            args = {"callId": call_id, "direction": "undo"}
            origin = {"module": "client", "origin": "ai", "workspaceId": ws,
                      "ai": {"client": "research", "conversation": "one"}}
            for other in [{**origin, "ai": {"client": "research", "conversation": "two"}},
                          {**origin, "module": None}, {**module, "module": "another"}]:
                with pytest.raises(ContractError) as denied:
                    await platform.call("platform.changes.revert", args, caller=other)
                assert denied.value.code == "permission_denied"
            changes = await platform.call("platform.changes.list", {"callIds": [call_id, created["callId"]]},
                                          caller=origin)
            assert [(c["objectId"], c["kind"]) for c in changes] == [(key, "updated"), (watch_id, "created")]
            for direction, name in (("undo", "Trial list"), ("redo", "Renamed")):
                await platform.call("platform.changes.revert", {"callId": renamed["callId"], "direction": direction},
                                    caller=origin)
                lists = await platform.call("chart.read_watchlist", {"view": "list"}, workspace=ws)
                assert next(item["name"] for item in lists["watchlists"] if item["id"] == watch_id) == name
            for direction, exists in (("undo", False), ("redo", True)):
                await platform.call("platform.changes.revert", {"callId": alert["callId"], "direction": direction},
                                    caller=origin)
                alerts = await platform.call("chart.read_alert", {"view": "list"}, workspace=ws)
                assert (alert["result"]["alert"]["id"] in [item["id"] for item in alerts["alerts"]]) == exists
            for direction, expected in (("undo", before), ("redo", after)):
                await platform.call("platform.changes.revert", {**args, "direction": direction}, caller=origin)
                current = await route(platform, "chart", "POST", "/charts/open", ws, {"chartId": key})
                assert current["document"] == expected
            await platform.call("platform.changes.revert", {**args, "callId": created["callId"]}, caller=origin)
            lists = await platform.call("chart.read_watchlist", {"view": "list"}, workspace=ws)
            assert watch_id not in [item["id"] for item in lists["watchlists"]]

    run(scenario())
