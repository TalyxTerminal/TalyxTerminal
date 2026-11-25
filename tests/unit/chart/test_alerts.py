"""Alerts as TradingView judges them: crossing needs a baseline, only real-time bars
fire, a replace rebuilds silently, frequencies, a fired bar stays fired across restarts,
watchlist alerts per member, indicator conditions, Market's failed state pauses."""
import asyncio

import pytest
from talyx_sdk import ContractError

from modules.chart.backend.alert_rules import Rule, Values
from modules.chart.backend.app import ChartApp
from modules.chart.backend.wire import (
    AlertCreate,
    AlertLog,
    AlertState,
    WatchlistCreate,
    WatchlistId,
    WatchlistUpdate,
)
from tests.unit.chart.fake_market import BTC, ETH, FakeMarket, series_of
from tests.unit.chart.test_pine_engine import bars
from vocabulary.identity import instrument_id

XRP = {**BTC, "symbol": "XRP/USDT", "displaySymbol": "XRP/USDT", "base": "XRP"}
XRP["instrumentId"] = instrument_id({key: value for key, value in XRP.items() if key != "instrumentId"})

# ── the rule alone ──


def rule(op, frequency="once_per_bar", **extra):
    return Rule([{"op": op, "amount": extra.get("amount"), "bars": extra.get("bars")}], frequency)


def test_criterion_7_crossing_needs_a_baseline_and_only_real_time_fires():
    crossing = rule("crossing_up")
    assert not crossing.observe(1, False, [Values(101, 100)], True, 0)       # first observation: baseline only
    assert not crossing.observe(1, False, [Values(99, 100)], False, 0)       # not real time: baseline only
    assert crossing.observe(1, False, [Values(101, 100)], True, 0)
    assert not crossing.observe(1, False, [Values(99, 100)], True, 0)        # down is not up
    assert not crossing.observe(1, False, [Values(102, 100)], True, 0)       # once per bar
    assert crossing.observe(2, False, [Values(99, 100)], True, 0) is False
    assert crossing.observe(2, False, [Values(101, 100)], True, 0)           # the next bar may fire again
    crossing.rebase([[]])
    assert not crossing.observe(3, False, [Values(99, 100)], True, 0)        # a new window: baseline again
    assert crossing.observe(3, False, [Values(101, 100)], True, 0)


def test_frequencies():
    on_close = rule("greater", "once_per_bar_close")
    assert not on_close.observe(1, False, [Values(101, 100)], True, 0)
    assert on_close.observe(1, True, [Values(101, 100)], True, 0)
    minute = rule("greater", "once_per_minute")
    assert minute.observe(1, False, [Values(101, 100)], True, 0)
    assert not minute.observe(2, False, [Values(101, 100)], True, 59_999)
    assert minute.observe(2, False, [Values(101, 100)], True, 60_000)
    restarted = Rule([{"op": "greater", "amount": None, "bars": None}], "once_per_bar", fired_bar=7)
    assert not restarted.observe(7, False, [Values(101, 100)], True, 0)       # this bar already fired
    assert restarted.observe(8, False, [Values(101, 100)], True, 0)


def test_channels_and_moving():
    entering = rule("entering_channel")
    entering.observe(1, False, [Values(90, 100, 110)], True, 0)
    assert entering.observe(1, False, [Values(105, 110, 100)], True, 0)       # bounds in either order
    assert rule("outside_channel").observe(1, False, [Values(120, 100, 110)], True, 0)
    moving = rule("moving_up", amount=5, bars=2)
    moving.rebase([[100.0, 101.0, 102.0]])
    assert not moving.observe(4, False, [Values(105.9)], True, 0)             # 2 closed bars ago: 101
    assert moving.observe(4, False, [Values(106.0)], True, 0)
    percent = rule("moving_down_percent", "once_per_bar_close", amount=10, bars=1)
    percent.rebase([[100.0]])
    assert percent.observe(2, True, [Values(90.0)], True, 0)


# ── the service over a stand-in Market ──


class Notifier:
    def __init__(self):
        self.sent = []

    async def deliver(self, channels, title, body, notification_id):
        self.sent.append((title, body))
        return {"toast": "sent"}


def series_alert(instrument, op="crossing", value=100.0, frequency="once", **extra):
    return AlertCreate.model_validate({"spec": {
        "target": {"kind": "series", "instrument": instrument, "series": series_of(instrument)},
        "conditions": [{"left": {"kind": "price", "field": "close"}, "op": op,
                        "right": {"kind": "value", "value": value}}],
        "frequency": frequency, **extra}})


def flat(count, price=90.0, start=0):
    rows = bars(count, 1)
    return [{**row, "time": row["time"] + start * 60_000, "closeTime": row["closeTime"] + start * 60_000,
             "open": price, "high": price, "low": price, "close": price} for row in rows]


async def settle(predicate, timeout=60):
    for _ in range(int(timeout * 50)):
        if predicate():
            return
        await asyncio.sleep(0.02)
    raise AssertionError("never happened")


def make(tmp_path, market):
    app = ChartApp(market, tmp_path / "data", tmp_path / "indicators")
    app.alerts.notifier = Notifier()
    app.store.put_object("seeded", {"id": "w1"}, "w1")
    return app


def test_a_price_alert_fires_once_on_real_time_bars_and_is_logged(tmp_path):
    async def main():
        market = FakeMarket()
        app = make(tmp_path, market)
        stream = app.workspace_stream("w1")
        await anext(stream)
        app.workspace_opened("w1")
        alert = await app.create_alert("w1", series_alert(BTC))
        session = await market.session(BTC["instrumentId"])
        rows = flat(110)
        session.replace(rows[:100])
        session.status("recovering")
        session.append([{**rows[100], "close": 120.0}])  # a catch-up crossing: not real time
        session.status("live")
        session.update({**rows[101], "close": 95.0})
        session.update({**rows[101], "close": 101.0})  # crosses 100 live
        await settle(lambda: app.alerts.notifier.sent)
        current = app.alerts.get("w1", alert["id"])
        assert current["state"] == "triggered" and current["fireCount"] == 1
        page = await app.alert_log("w1", AlertLog(alertId=alert["id"]))
        assert len(page["events"]) == 1 and page["events"][0]["barTime"] == rows[101]["time"]
        assert page["events"][0]["message"] == "BTC/USDT: Close Crossing 100"
        await settle(lambda: app.store.fired("w1", alert["id"], None, 1)[0]["delivered"] == {"toast": "sent"})
        items = []
        while len(items) < 6:
            items.append(await asyncio.wait_for(anext(stream), 5))
        assert [item["type"] for item in items].count("fired") == 2  # the fire, then what was delivered
        assert session.closed  # a triggered alert watches nothing
        await app.close()
    asyncio.run(main())


def test_criterion_7_a_fired_bar_stays_fired_across_a_restart(tmp_path):
    async def main():
        rows = flat(110)
        for attempt in range(2):
            market = FakeMarket()
            app = make(tmp_path, market)
            app.workspace_opened("w1")
            if attempt == 0:
                alert = await app.create_alert("w1", series_alert(BTC, frequency="once_per_bar"))
            session = await market.session(BTC["instrumentId"])
            session.replace(rows[:100])
            session.status("live")
            session.update({**rows[100], "close": 95.0})
            session.update({**rows[100], "close": 105.0})
            if attempt == 0:
                await settle(lambda: app.alerts.notifier.sent)
            else:
                await session.drained()
                assert app.alerts.notifier.sent == []  # the same bar does not fire again
                session.update({**rows[101], "close": 95.0})
                session.update({**rows[101], "close": 105.0})
                await settle(lambda: app.alerts.notifier.sent)
            assert app.alerts.get("w1", alert["id"])["state"] == "active"
            await app.close()
    asyncio.run(main())


def test_criterion_7_watchlist_member_two_crosses_and_a_removed_member_is_not_judged(tmp_path):
    """Three members; the second crosses → 1 notification with its code; after the third is
    removed, its crossing is not judged (its subscription ends)."""
    async def main():
        market = FakeMarket(instruments=(BTC, ETH, XRP))
        app = make(tmp_path, market)
        app.workspace_opened("w1")
        listed = await app.create_watchlist("w1", WatchlistCreate.model_validate({"name": "Three", "sections": [
            {"id": "a", "name": "", "members": [{"instrument": item} for item in (BTC, ETH, XRP)]}]}))
        alert = await app.create_alert("w1", AlertCreate.model_validate({"spec": {
            "target": {"kind": "watchlist", "watchlistId": listed["id"], "timeframe": "1m"},
            "conditions": [{"left": {"kind": "price", "field": "close"}, "op": "crossing_up",
                            "right": {"kind": "value", "value": 100}}], "frequency": "once_per_bar"}}))
        sessions = [await market.session(item["instrumentId"]) for item in (BTC, ETH, XRP)]
        rows = flat(52)
        for session in sessions:
            session.replace(rows[:50])
            session.status("live")
            session.update({**rows[50], "close": 95.0})
        sessions[1].update({**rows[50], "close": 101.0})
        await settle(lambda: app.alerts.notifier.sent)
        for session in sessions:
            await session.drained()
        events = app.store.fired("w1", alert["id"], None, 10)
        assert len(events) == 1 and events[0]["symbol"] == "ETH/USDT" and events[0]["instrumentId"] == ETH["instrumentId"]
        assert app.alerts.notifier.sent[0][1].startswith("ETH/USDT")
        await app.update_watchlist("w1", WatchlistUpdate.model_validate({
            "watchlistId": listed["id"], "revision": 1,
            "sections": [{"id": "a", "name": "", "members": [{"instrument": BTC}, {"instrument": ETH}]}]}))
        await settle(lambda: sessions[2].closed)
        sessions[2].update({**rows[50], "close": 101.0})  # would cross, but nothing reads XRP any more
        assert len(app.store.fired("w1", alert["id"], None, 10)) == 1
        assert not sessions[0].closed and not sessions[1].closed  # the others keep watching, not restarted
        await app.delete_watchlist("w1", WatchlistId(watchlistId=listed["id"]))
        current = app.alerts.get("w1", alert["id"])
        assert current["state"] == "paused" and current["reason"]["code"] == "watchlist_deleted"
        await app.close()
    asyncio.run(main())


def test_criterion_1_only_the_crossing_after_live_notifies_and_reopening_sends_nothing(tmp_path):
    """Market sends history to T−3h, then 180 caught-up 1m bars (the 90th closes above the
    alert price), then live, then one more bar crossing up → exactly 1 notification. Closed
    for 3 hours and opened again: 0."""
    async def main():
        start = 1_800
        rows = flat(start + 180 + 1 + 180, price=90.0)
        rows[start + 89] = {**rows[start + 89], "close": 110.0, "high": 110.0}   # the 90th caught-up bar
        rows[start + 180] = {**rows[start + 180], "close": 110.0, "high": 110.0}  # the live one
        market = FakeMarket()
        app = make(tmp_path, market)
        app.workspace_opened("w1")
        alert = await app.create_alert("w1", series_alert(BTC, op="crossing_up", frequency="once_per_bar"))
        session = await market.session(BTC["instrumentId"])
        session.replace(rows[:start])
        session.status("recovering")
        for row in rows[start:start + 180]:
            session.append([row])
        session.status("live")
        session.append([rows[start + 180]])
        await settle(lambda: app.alerts.notifier.sent)
        await session.drained()
        events = app.store.fired("w1", alert["id"], None, 10)
        assert len(events) == 1 and events[0]["barTime"] == rows[start + 180]["time"]
        await app.close()

        market = FakeMarket()  # three hours later
        app = make(tmp_path, market)
        app.workspace_opened("w1")
        session = await market.session(BTC["instrumentId"])
        session.replace(rows)
        session.status("live")
        await session.drained()
        assert app.alerts.notifier.sent == [] and len(app.store.fired("w1", alert["id"], None, 10)) == 1
        await app.close()
    asyncio.run(main())


def test_criterion_7_four_frequencies_with_three_crossings_inside_one_bar(tmp_path, monkeypatch):
    """Price crosses up three times inside one bar and closes above: once → 1 and then stops;
    once per bar → 1; once per bar close → 1 (it holds at the close); once per minute → one
    per minute (the third crossing is 61 s after the first)."""
    from modules.chart.backend import alerting
    clock = {"now": 1_000_000}
    monkeypatch.setattr(alerting, "now_ms", lambda: clock["now"])

    async def main():
        market = FakeMarket()
        app = make(tmp_path, market)
        app.workspace_opened("w1")
        alerts = {frequency: await app.create_alert("w1", series_alert(BTC, op="crossing_up", frequency=frequency))
                  for frequency in ("once", "once_per_bar", "once_per_bar_close", "once_per_minute")}
        session = await market.session(BTC["instrumentId"])
        rows = flat(60)
        session.replace(rows[:50])
        session.status("live")
        for second, close in ((0, 95.0), (1, 101.0), (20, 95.0), (30, 102.0), (40, 95.0), (61, 103.0)):
            clock["now"] = 1_000_000 + second * 1000
            session.update({**rows[50], "close": close})
            await session.drained()  # judged at this clock
        session.append([{**rows[50], "close": 103.0}])
        session.update({**rows[51], "close": 95.0})
        session.update({**rows[51], "close": 104.0})  # a crossing on the next bar
        await session.drained()
        counts = {frequency: len(app.store.fired("w1", alert["id"], None, 10)) for frequency, alert in alerts.items()}
        assert counts == {"once": 1, "once_per_bar": 2, "once_per_bar_close": 1, "once_per_minute": 2}
        first_bar = {frequency: len([event for event in app.store.fired("w1", alert["id"], None, 10)
                                     if event["barTime"] == rows[50]["time"]]) for frequency, alert in alerts.items()}
        assert first_bar == {"once": 1, "once_per_bar": 1, "once_per_bar_close": 1, "once_per_minute": 2}
        assert app.alerts.get("w1", alerts["once"]["id"])["state"] == "triggered"
        await app.close()
    asyncio.run(main())


def test_once_per_minute_counts_from_the_last_fire_after_the_workspace_reopens(tmp_path, monkeypatch):
    """Reopening (or restarting) brought back only the fired bar: a crossing on the next bar fired again within the
    minute. The last fire's time comes from the log."""
    from modules.chart.backend import alerting
    clock = {"now": 1_000_000}
    monkeypatch.setattr(alerting, "now_ms", lambda: clock["now"])

    async def main():
        rows = flat(60)
        market = FakeMarket()
        app = make(tmp_path, market)
        app.workspace_opened("w1")
        alert = await app.create_alert("w1", series_alert(BTC, op="crossing_up", frequency="once_per_minute"))
        session = await market.session(BTC["instrumentId"])
        session.replace(rows[:50])
        session.status("live")
        for second, index, close in ((0, 50, 95.0), (1, 50, 101.0)):  # fires at 1 s
            clock["now"] = 1_000_000 + second * 1000
            session.update({**rows[index], "close": close})
            await session.drained()
        app.workspace_closed("w1")
        app.workspace_opened("w1")
        await settle(lambda: alert["id"] in app.alerts.running and not app.alerts.starting)
        session = await market.session(BTC["instrumentId"])
        session.replace(rows[:51])
        session.status("live")
        for second, close in ((20, 95.0), (30, 102.0), (40, 95.0), (61, 103.0)):  # 29 s and 60 s after the fire
            clock["now"] = 1_000_000 + second * 1000
            session.update({**rows[51], "close": close})
            await session.drained()
        times = sorted(event["at"] for event in app.store.fired("w1", alert["id"], None, 10))
        assert times == [1_001_000, 1_061_000]
        await app.close()
    asyncio.run(main())


def test_an_indicator_condition_reads_the_instance_rows(tmp_path):
    async def main():
        market = FakeMarket()
        app = make(tmp_path, market)
        app.workspace_opened("w1")
        alert = await app.create_alert("w1", AlertCreate.model_validate({"spec": {
            "target": {"kind": "series", "instrument": BTC, "series": series_of(BTC)},
            "conditions": [{"left": {"kind": "indicator", "definition": {"source": "builtin", "id": "rsi"},
                                     "output": "rsi"}, "op": "greater", "right": {"kind": "value", "value": 0}}],
            "frequency": "once_per_bar_close"}}))
        session = await market.session(BTC["instrumentId"])
        rows = bars(300, 2)
        session.replace(rows[:290])
        session.status("live")
        await settle(lambda: all(tap.ready for watching in app.alerts.running.get(alert["id"], [])
                                 for tap in watching.taps.values()) and app.alerts.running.get(alert["id"]))
        session.update(rows[290])
        await settle(lambda: all(tap.forming for watching in app.alerts.running[alert["id"]]
                                 for tap in watching.taps.values()))  # the instance's forming row is in
        await asyncio.sleep(0)
        assert app.alerts.notifier.sent == [] and app.store.fired("w1", alert["id"], None, 1) == []  # bar close only
        session.append([rows[290]])
        await settle(lambda: app.alerts.notifier.sent)
        assert app.store.fired("w1", alert["id"], None, 1)[0]["barTime"] == rows[290]["time"]
        await app.close()
    asyncio.run(main())


def test_a_failed_series_pauses_until_the_user_retries_and_closing_the_workspace_stops_watching(tmp_path):
    async def main():
        market = FakeMarket()
        app = make(tmp_path, market)
        app.workspace_opened("w1")
        alert = await app.create_alert("w1", series_alert(BTC, frequency="once_per_bar"))
        session = await market.session(BTC["instrumentId"])
        session.replace(flat(50))
        session.status("recovering", {"code": "network", "message": "Reconnecting to OKX"})
        await settle(lambda: (app.alerts.get("w1", alert["id"])["source"] or {}).get("code") == "recovering")
        session.status("failed", {"code": "refused", "message": "OKX refused the request"})
        await settle(lambda: app.alerts.get("w1", alert["id"])["state"] == "paused")
        paused = app.alerts.get("w1", alert["id"])
        assert paused["reason"] == {"code": "source_failed", "message": "OKX refused the request"}
        resumed = await app.set_alert_state("w1", AlertState(alertId=alert["id"], revision=paused["revision"],
                                                             state="active"))
        assert resumed["state"] == "active"
        await settle(lambda: len(market.sessions) == 2)  # the retry subscribes again
        app.workspace_closed("w1")
        await settle(lambda: market.sessions[-1].closed)
        with pytest.raises(ContractError) as stale:
            await app.set_alert_state("w1", AlertState(alertId=alert["id"], revision=1, state="paused"))
        assert stale.value.code == "conflict"
        await app.close()
    asyncio.run(main())


def test_criterion_7_expiry_and_back_adjusted_series_are_refused(tmp_path):
    async def main():
        market = FakeMarket()
        app = make(tmp_path, market)
        app.workspace_opened("w1")
        from modules.chart.backend.alerting import now_ms
        alert = await app.create_alert("w1", series_alert(BTC, expiresAt=now_ms() + 300))
        await settle(lambda: app.alerts.get("w1", alert["id"])["state"] == "expired")
        await settle(lambda: market.sessions[0].closed)
        with pytest.raises(ContractError) as one_sided:
            await app.create_alert("w1", AlertCreate.model_validate({"spec": {
                "target": {"kind": "series", "instrument": BTC, "series": series_of(BTC)},
                "conditions": [{"left": {"kind": "price"}, "op": "crossing"}]}}))
        assert one_sided.value.code == "invalid_input"
        back = {**series_of(BTC), "adjustmentAnchor": "earliest"}
        with pytest.raises(ContractError) as adjusted:
            await app.create_alert("w1", AlertCreate.model_validate({"spec": {
                "target": {"kind": "series", "instrument": BTC, "series": back},
                "conditions": [{"left": {"kind": "price"}, "op": "greater", "right": {"kind": "value", "value": 1}}]}}))
        assert "Back-adjusted" in adjusted.value.message
        await app.close()
    asyncio.run(main())


def test_criterion_7_drawing_alerts_follow_the_drawing_and_pause_when_it_is_deleted(tmp_path):
    """A horizontal line at 100: crossing it fires; moved to 200 in the chart, the alert's copy
    follows in the same commit; a trend line rising into the future (Market's calendar) is
    judged at the forming bar; deleting the drawing pauses the alert."""
    async def main():
        from modules.chart.backend.document import COMMANDS
        from modules.chart.backend.wire import ChartCommit, ChartCreate
        market = FakeMarket()
        app = make(tmp_path, market)
        app.workspace_opened("w1")
        chart = await app.create_chart("w1", ChartCreate(instrumentId=BTC["instrumentId"], timeframe="1m"))
        rows = flat(70)

        async def commit(revision, *commands):
            return await app.commit("w1", ChartCommit(chartId=chart["chartId"], baseRevision=revision,
                                                      commands=COMMANDS.validate_python(list(commands))))
        at = rows[0]["time"]
        await commit(1, {"type": "drawing.add", "value": {"id": "level", "tool": "hline",
                                                          "points": [{"time": at, "price": 100}]}},
                     {"type": "drawing.add", "value": {"id": "rise", "tool": "trendline", "points": [
                         {"time": rows[40]["time"], "price": 90}, {"time": rows[69]["time"] + 30 * 60_000, "price": 150}]}})

        def drawing_alert(drawing_id):
            return AlertCreate.model_validate({"spec": {
                "target": {"kind": "series", "instrument": BTC, "series": series_of(BTC), "chartId": chart["chartId"]},
                "conditions": [{"left": {"kind": "price", "field": "close"}, "op": "crossing_up",
                                "right": {"kind": "drawing", "chartId": chart["chartId"], "drawingId": drawing_id}}],
                "frequency": "once_per_bar"}})
        app.charts.save("w1", [{"chartId": chart["chartId"], "revision": 2}])
        level = await app.create_alert("w1", drawing_alert("level"))
        rise = await app.create_alert("w1", drawing_alert("rise"))
        session = await market.session(BTC["instrumentId"])
        session.replace(rows[:50])
        session.status("live")
        await settle(lambda: all(watching.watch.future[0] for watching in app.alerts.running.get(rise["id"], [])))
        # the trend line at bar 50: 90 + 60 * 10 / 59 ≈ 100.17; at 100.1 the level fires, the line does not
        session.update({**rows[50], "close": 95.0})
        session.update({**rows[50], "close": 100.1})
        await settle(lambda: app.alerts.notifier.sent)
        await session.drained()
        assert len(app.store.fired("w1", level["id"], None, 10)) == 1 and app.store.fired("w1", rise["id"], None, 10) == []
        session.update({**rows[50], "close": 100.3})  # now above the line too
        await settle(lambda: len(app.alerts.notifier.sent) == 2)
        assert len(app.store.fired("w1", rise["id"], None, 10)) == 1

        await commit(2, {"type": "drawing.update", "id": "level", "patch": {"points": [{"time": at, "price": 200}]}})
        session.update({**rows[51], "close": 150.0})
        session.update({**rows[51], "close": 201.0})
        await settle(lambda: len(app.store.fired("w1", level["id"], None, 10)) == 2)
        assert app.store.fired("w1", level["id"], None, 1)[0]["barTime"] == rows[51]["time"]

        app.workspace_closed("w1")
        app.workspace_opened("w1")
        await app.opening["w1"]
        await settle(lambda: app.alerts.running.get(level["id"]))
        restored = app.alerts.running[level["id"]][0].shapes[f"{chart['chartId']}/level"]
        assert restored["points"][0]["price"] == 100
        await commit(2, {"type": "drawing.delete", "id": "level"})
        current = app.alerts.get("w1", level["id"])
        assert current["state"] == "paused" and current["reason"] == {"code": "drawing_deleted",
                                                                      "message": "The drawing was deleted"}
        assert app.alerts.get("w1", rise["id"])["state"] == "active"
        with pytest.raises(ContractError) as channel:
            await app.create_alert("w1", AlertCreate.model_validate({"spec": {
                "target": {"kind": "series", "instrument": BTC, "series": series_of(BTC)},
                "conditions": [{"left": {"kind": "price"}, "op": "entering_channel",
                                "right": {"kind": "drawing", "chartId": chart["chartId"], "drawingId": "rise"}}]}}))
        assert channel.value.code == "invalid_input"
        await app.close()
    asyncio.run(main())


def test_an_alertcondition_of_a_user_indicator_triggers_the_alert(tmp_path):
    """Pine's ``alertcondition``: its title is in the catalog; the alert fires when its value is
    true on a real-time bar; the chart stream does not carry its values."""
    async def main():
        (tmp_path / "indicators").mkdir(exist_ok=True)
        (tmp_path / "indicators" / "cond.py").write_text(
            '"""@pyne"""\nfrom pynecore.lib import alertcondition, close, plot, script\n\n\n'
            '@script.indicator("Cond")\ndef main():\n    alertcondition(close > 100, title="Above 100")\n'
            '    plot(close, "close")\n', encoding="utf-8")
        market = FakeMarket()
        app = make(tmp_path, market)
        app.workspace_opened("w1")
        entry = next(item for item in (await app.catalog_entries("w1", None))["entries"] if item["id"] == "cond.py")
        assert entry["alertConditions"] == ["Above 100"]
        definition = {"source": "user", "id": "cond.py"}
        alert = await app.create_alert("w1", AlertCreate.model_validate({"spec": {
            "target": {"kind": "series", "instrument": BTC, "series": series_of(BTC)},
            "conditions": [{"left": {"kind": "alertcondition", "definition": definition, "condition": "Above 100"},
                            "op": "triggered"}], "frequency": "once_per_bar_close"}}))
        session = await market.session(BTC["instrumentId"])
        rows = flat(60)
        session.replace(rows[:50])
        session.status("live")
        await settle(lambda: app.alerts.running.get(alert["id"]) and all(
            tap.ready for watching in app.alerts.running[alert["id"]] for tap in watching.taps.values()))
        session.append([rows[50]])  # 90: false
        session.append([{**rows[51], "close": 110.0}])
        await settle(lambda: app.alerts.notifier.sent)
        event = app.store.fired("w1", alert["id"], None, 5)
        assert len(event) == 1 and event[0]["barTime"] == rows[51]["time"]
        assert event[0]["message"] == "BTC/USDT: Above 100 Triggered"
        with pytest.raises(ContractError):
            await app.create_alert("w1", AlertCreate.model_validate({"spec": {
                "target": {"kind": "series", "instrument": BTC, "series": series_of(BTC)},
                "conditions": [{"left": {"kind": "price"}, "op": "triggered"}]}}))
        await app.close()
    asyncio.run(main())


def test_an_alert_keeps_the_indicator_code_it_was_made_with_and_says_it_is_outdated(tmp_path):
    async def main():
        folder = tmp_path / "indicators"
        folder.mkdir(exist_ok=True)
        script = folder / "level.py"
        source = '"""@pyne"""\nfrom pynecore.lib import close, plot, script\n\n\n@script.indicator("Level")\ndef main():\n    plot({}, "out")\n'
        script.write_text(source.format("close"), encoding="utf-8")
        market = FakeMarket()
        app = make(tmp_path, market)
        app.workspace_opened("w1")
        alert = await app.create_alert("w1", AlertCreate.model_validate({"spec": {
            "target": {"kind": "series", "instrument": BTC, "series": series_of(BTC)},
            "conditions": [{"left": {"kind": "indicator", "definition": {"source": "user", "id": "level.py"},
                            "output": "out"}, "op": "greater", "right": {"kind": "value", "value": 100}}],
            "frequency": "once_per_bar_close"}}))
        paused = await app.set_alert_state("w1", AlertState(alertId=alert["id"], revision=1, state="paused"))
        script.write_text(source.format("close * 0"), encoding="utf-8")  # the indicator changes
        resumed = await app.set_alert_state("w1", AlertState(alertId=alert["id"], revision=paused["revision"],
                                                             state="active"))
        session = await market.session(BTC["instrumentId"])
        rows = flat(60)
        session.replace(rows[:50])
        session.status("live")
        await settle(lambda: app.alerts.get("w1", alert["id"])["outdated"])
        await settle(lambda: all(tap.ready for watching in app.alerts.running[resumed["id"]]
                                 for tap in watching.taps.values()))
        session.append([{**rows[50], "close": 110.0}])  # the old code plots close: 110 > 100
        await settle(lambda: app.alerts.notifier.sent)
        await app.close()
    asyncio.run(main())
