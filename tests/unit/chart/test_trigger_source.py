"""Alert trigger cursors survive reconnects and equal millisecond timestamps."""
import asyncio

from modules.chart.backend.store import Store


def test_trigger_resumes_the_next_matching_workspace_firing_at_the_same_time(tmp_path):
    path = tmp_path / "chart.db"

    def record(identity, instrument="btc"):
        return {"id": identity, "alertId": "a", "instrumentId": instrument, "at": 1234, "barTime": 1200}

    async def main():
        store = Store(path)
        store.add_fired("A", record("one"), "Price above 1")
        stream = store.follow_firings("A", "a", "btc", 0)
        event, cursor = await anext(stream)
        assert event == {"alertId": "a", "instrumentId": "btc", "triggeredAt": "1234000000", "barOpen": "1200000000",
                         "condition": "Price above 1", "openTarget": {"module": "chart", "panelType": "main",
                                                                      "objectId": "alert:a"}}
        await stream.aclose()
        store.close()
        store = Store(path)
        store.add_fired("B", record("two"), "Wrong workspace")
        store.add_fired("A", record("three", "eth"), "Wrong instrument")
        store.add_fired("A", record("four"), "Next firing")
        stream = store.follow_firings("A", "a", "btc", cursor)
        event, next_cursor = await anext(stream)
        assert next_cursor > cursor and event["condition"] == "Next firing"
        await stream.aclose()
        store.close()

    asyncio.run(main())
