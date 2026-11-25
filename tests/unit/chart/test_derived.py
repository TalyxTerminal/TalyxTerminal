"""Market's open interest and funding read with request.security ("_OI", "_FR"; derived.py).

The rule is request.security's: a bar that closed live equals what a fresh recompute gives for
it later; only the forming bar may repaint (open interest's live observation)."""
import asyncio
import random

from modules.chart.backend import derived
from modules.chart.backend.derived import PointsFeed, derived_of
from modules.chart.backend.feeds import Hub
from modules.chart.backend.marketdata import Market
from modules.chart.backend.pine.engine import PineEngine
from modules.chart.backend.pine.indicators import Catalog, Indicators
from tests.unit.chart.fake_market import BTC, FakeMarket
from tests.unit.chart.test_pine_engine import Collector, bars
from vocabulary.identity import instrument_id

MINUTE = 60_000
SWAP = {**BTC, "symbol": "BTC/USDT:USDT", "displaySymbol": "BTC/USDT:USDT", "marketType": "swap",
        "contractOrientation": "linear"}
SWAP["instrumentId"] = instrument_id(SWAP)
T0 = 1_700_000_100_000 // (5 * MINUTE) * (5 * MINUTE)


class Clock:
    def __init__(self, monkeypatch, now):
        self.now = now
        monkeypatch.setattr(derived, "now_ms", lambda: self.now)


class Closed:
    """Closed bars as a script's feed sees them: the replace, then each append."""

    def __init__(self):
        self.columns, self.formings = None, []

    def on_replaced(self, feed):
        self.columns = {name: list(values) for name, values in feed.columns.items()}

    def on_appended(self, feed, count):
        for name, values in feed.columns.items():
            self.columns[name].extend(values[-count:])

    def on_forming(self, feed):
        self.formings.append(feed.forming)


async def settle():
    for _ in range(20):
        await asyncio.sleep(0)


async def open_feed(fake, suffix, timeframe):
    feed = PointsFeed(Market(fake), BTC, suffix, timeframe, lambda message: (_ for _ in ()).throw(AssertionError(message)))
    await feed.open()
    session = fake.sessions[-1]
    assert session.params["series"]["capability"] == derived.SUFFIXES[suffix][0]
    return feed, session


def test_empty_opening_page_waits_for_points_while_filling_and_reports_the_final_reason(monkeypatch):
    async def main():
        Clock(monkeypatch, T0 + MINUTE)
        errors = []
        feed = PointsFeed(Market(FakeMarket()), SWAP, "_FR", "5m", errors.append)

        async def replace(times, state, problem=None):
            await feed.take({"type": "replace", "page": {
                "columns": {"time": times, "rate": [0.002] * len(times)}, "fill": {"state": state, "problem": problem}}})

        await replace([], "filling")
        assert not errors and not feed.ready.is_set()
        await replace([str(T0 * 1_000_000)], "filling")
        assert feed.ready.is_set() and feed.forming["close"] == 0.002
        await replace([], "failed", {"message": "No source provides funding_rate history for BTC/USDT:USDT"})
        assert errors == ["No source provides funding_rate history for BTC/USDT:USDT"]
        await feed.close()
    asyncio.run(main())


def test_the_suffix_table():
    assert derived_of("OKX:BTC/USDT:USDT_OI") == ("OKX:BTC/USDT:USDT", "_OI")
    assert derived_of("OKX:BTC/USDT:USDT_FR") == ("OKX:BTC/USDT:USDT", "_FR")
    assert derived_of("OKX:ETHUSDT") is None


def test_open_interest_bars_that_closed_live_equal_a_recompute(monkeypatch):
    """1-minute bars from 5-minute points: closed bars step every fifth bar, historically and
    live; the forming bar shows the latest 30-second observation; a point published inside
    its bar is in that bar when it closes."""
    async def main():
        rng = random.Random(5)
        points = {"time": [T0 + index * 5 * MINUTE for index in range(60)],
                  "value": [round(1000 + rng.random() * 100, 1) for _ in range(60)],
                  "valueQuote": [1.0] * 60}
        history = 40
        clock = Clock(monkeypatch, points["time"][history - 1] + 30_000)
        fake = FakeMarket()
        live, session = await open_feed(fake, "_OI", "1m")
        seen = Closed()
        live.listeners.append(seen)
        session.replace_points({name: values[:history] for name, values in points.items()})
        await settle()
        assert live.columns["close"][-1] == points["value"][history - 2]  # the bar before the last point's
        observed = []
        for second in range(30, (60 - history) * 300, 30):
            clock.now = points["time"][history - 1] + second * 1000
            index = (clock.now - T0) // (5 * MINUTE)
            if (clock.now - T0) % (5 * MINUTE) == 30_000 and index < 60:  # published 30 s after its time
                session.append_points({name: values[index:index + 1] for name, values in points.items()})
            value = round(2000 + rng.random() * 100, 1)  # never a stored value
            observed.append(value)
            session.observe({"time": clock.now, "value": value, "valueQuote": 1.0})
            await settle()
            assert live.forming["close"] == value and live.forming["time"] == clock.now // MINUTE * MINUTE
        # Closed bars: carried 5-minute values, the live observations nowhere in them.
        assert seen.columns == live.columns
        assert not set(observed) & set(live.columns["close"][-(len(observed) // 2):])

        fresh, fresh_session = await open_feed(fake, "_OI", "1m")
        fresh_session.replace_points(points)
        await settle()
        assert fresh.columns == seen.columns
        assert seen.columns["close"][-5:] == [points["value"][57]] + [points["value"][58]] * 4  # steps
        await live.close()
        await fresh.close()
    asyncio.run(main())


def test_funding_bars_that_closed_live_equal_a_recompute(monkeypatch):
    """Settled rates only, as a step from each settlement; the predicted rate Market's live
    observation carries never shows."""
    async def main():
        settlements = {"time": [T0 + index * 30 * MINUTE for index in range(12)],
                       "rate": [0.0001 * (index - 4) for index in range(12)], "intervalHours": [8.0] * 12}
        clock = Clock(monkeypatch, settlements["time"][7] + 10_000)
        fake = FakeMarket()
        live, session = await open_feed(fake, "_FR", "5m")
        seen = Closed()
        live.listeners.append(seen)
        session.replace_points({name: values[:8] for name, values in settlements.items()})
        await settle()
        for second in range(20, 5 * 30 * 60, 10):
            clock.now = settlements["time"][7] + second * 1000
            index = (clock.now - T0) // (30 * MINUTE)
            if (clock.now - T0) % (30 * MINUTE) == 10_000:
                session.append_points({name: values[index:index + 1] for name, values in settlements.items()})
            session.observe({"time": clock.now, "rate": 0.5, "nextFundingTime": clock.now + 3_600_000})
            await settle()
            published = (clock.now - T0) % (30 * MINUTE) >= 10_000
            assert live.forming["close"] == settlements["rate"][index if published else index - 1]
        assert 0.5 not in seen.columns["high"]
        fresh, fresh_session = await open_feed(fake, "_FR", "5m")
        fresh_session.replace_points(settlements)
        await settle()
        assert fresh.columns == seen.columns
        assert seen.columns["close"][-6:] == [settlements["rate"][10]] + [settlements["rate"][11]] * 5  # a step from each settlement
        await live.close()
        await fresh.close()
    asyncio.run(main())


async def points_session(fake, capability):
    for _ in range(500):
        found = [item for item in fake.sessions if item.params["series"]["capability"] == capability]
        if found:
            return found[-1]
        await asyncio.sleep(0.02)
    raise AssertionError(f"Chart never subscribed to {capability}")


def test_the_open_interest_and_funding_builtins_closed_live_equal_a_recompute(tmp_path, monkeypatch):
    """The built-ins on a 1-minute chart, stepped live (points published inside their bar,
    open interest observed every 30 s), against a fresh run over the same chart bars and points."""
    async def main():
        rows = [{**row, "time": T0 + index * MINUTE, "closeTime": T0 + (index + 1) * MINUTE}
                for index, row in enumerate(bars(260, 8))]
        rng = random.Random(9)
        interest = {"time": [T0 + index * 5 * MINUTE for index in range(52)],
                    "value": [round(5000 + rng.random() * 500, 1) for _ in range(52)], "valueQuote": [1.0] * 52}
        funding = {"time": [T0 + index * 30 * MINUTE for index in range(9)],
                   "rate": [0.0001 * (index - 3) for index in range(9)], "intervalHours": [8.0] * 9}
        history = 200
        clock = Clock(monkeypatch, rows[history - 1]["closeTime"] + 1000)

        def known(points, until):
            return {name: [value for time, value in zip(points["time"], values, strict=True) if time <= until]
                    for name, values in points.items()}

        async def start(name, count):
            fake = FakeMarket()
            market = Market(fake)
            hub = Hub(market)
            engine = PineEngine(tmp_path / name)
            indicators = Indicators(engine, Catalog(engine, tmp_path / "none"), hub)
            feed = await hub.acquire(BTC, market.default_series(BTC, "1m"))
            chart = await fake.session(BTC["instrumentId"])
            chart.replace(rows[:count])
            collectors = {}
            for builtin_id in ("open_interest", "funding_rate"):
                collectors[builtin_id] = Collector()
                await indicators.attach(feed, {"source": "builtin", "id": builtin_id}, {}, collectors[builtin_id])
            sessions = {"oi": await points_session(fake, "open_interest"),
                        "fr": await points_session(fake, "funding_rate")}
            sessions["oi"].replace_points(known(interest, clock.now))
            sessions["fr"].replace_points(known(funding, clock.now))
            for collector in collectors.values():
                await collector.wait(1)
                assert collector.items[0]["op"] == "replace", collector.items[0]
            funding_meta = collectors["funding_rate"].items[0]["meta"]
            assert next(meta for meta in funding_meta if meta["id"] == "Funding rate")["precision"] == 4
            return engine, hub, chart, sessions, collectors

        engine, hub, chart, sessions, live = await start("live", history)
        for index in range(history, len(rows)):
            opens = rows[index]["time"]
            clock.now = opens + 30_000  # points published inside their bar
            for key, points in (("oi", interest), ("fr", funding)):
                if opens in points["time"]:
                    at = points["time"].index(opens)
                    sessions[key].append_points({name: values[at:at + 1] for name, values in points.items()})
            sessions["oi"].observe({"time": clock.now, "value": 9999.0, "valueQuote": 1.0})
            await settle()
            clock.now = rows[index]["closeTime"] + 1000
            sessions["oi"].observe({"time": clock.now, "value": 9998.0, "valueQuote": 1.0})
            sessions["fr"].observe({"time": clock.now, "rate": 0.5, "nextFundingTime": clock.now})
            await settle()
            counts = {key: len(collector.items) for key, collector in live.items()}
            chart.append([rows[index]])
            for key, collector in live.items():
                await collector.wait(counts[key] + 1)
        # The forming bar shows open interest's latest observation; closed bars never do.
        forming = {**rows[-1], "time": rows[-1]["closeTime"], "closeTime": rows[-1]["closeTime"] + MINUTE}
        clock.now = forming["time"] + 20_000
        sessions["oi"].observe({"time": clock.now, "value": 9997.0, "valueQuote": 1.0})
        await settle()
        count = len(live["open_interest"].items)
        chart.update(forming)
        await live["open_interest"].wait(count + 1)
        assert live["open_interest"].items[-1]["op"] == "update"
        assert live["open_interest"].items[-1]["values"]["Open Interest (close)"] == [9997.0]
        assert not {9997.0, 9998.0, 9999.0} & set(live["open_interest"].rows()[1]["Open Interest (high)"])
        await engine.close()
        await hub.close()

        engine, hub, _chart, _sessions, fresh = await start("fresh", len(rows))
        for key in live:
            times, values = live[key].rows()
            fresh_times, fresh_values = fresh[key].rows()
            assert times[-60:] == fresh_times[-60:]
            for name in values:
                assert values[name][-60:] == fresh_values[name][-60:], (key, name)
            assert any(value is not None for value in values[next(iter(values))][-60:])
        await engine.close()
        await hub.close()
    asyncio.run(main())


def test_mark_and_index_read_price_basis_bars_and_a_venue_without_them_fails_the_indicator(tmp_path):
    async def main():
        fake = FakeMarket(instruments=(SWAP,))
        market = Market(fake)
        hub = Hub(market)
        engine = PineEngine(tmp_path / "logs")
        indicators = Indicators(engine, Catalog(engine, tmp_path / "none"), hub)
        feed = await hub.acquire(SWAP, market.default_series(SWAP, "1m"))
        rows = [{**row, "time": T0 + index * MINUTE} for index, row in enumerate(bars(300, 3))]
        (await fake.session(SWAP["instrumentId"])).replace(rows)
        collector = Collector()
        await indicators.attach(feed, {"source": "builtin", "id": "derivatives_basis"}, {}, collector)
        sessions = {}
        for _ in range(500):
            sessions = {item.params["series"]["priceBasis"]: item for item in fake.sessions}
            if "mark" in sessions:  # contexts resolve one after another: the index waits for the mark
                break
            await asyncio.sleep(0.02)
        assert sessions["mark"].params["series"]["timeframe"] == "1m"
        sessions["mark"].status("failed", {"code": "unsupported", "message": "okx publishes no mark price for BTC/USDT"})
        await collector.wait(1)
        assert collector.items[0]["op"] == "error" and collector.items[0]["code"] == "missing_data"
        assert collector.items[0]["message"] == ("request.security('OKX:BTC/USDT:USDT_MARK', '1'): "
                                                 "okx publishes no mark price for BTC/USDT")
        await engine.close()
        await hub.close()
    asyncio.run(main())


def test_mark_and_index_on_a_spot_pair_are_refused(tmp_path):
    async def main():
        fake = FakeMarket()  # BTC/USDT is spot
        market = Market(fake)
        hub = Hub(market)
        engine = PineEngine(tmp_path / "logs")
        indicators = Indicators(engine, Catalog(engine, tmp_path / "none"), hub)
        feed = await hub.acquire(BTC, market.default_series(BTC, "1m"))
        (await fake.session(BTC["instrumentId"])).replace(
            [{**row, "time": T0 + index * MINUTE} for index, row in enumerate(bars(300, 3))])
        collector = Collector()
        await indicators.attach(feed, {"source": "builtin", "id": "derivatives_basis"}, {}, collector)
        await collector.wait(1)
        assert collector.items[0]["op"] == "error" and collector.items[0]["code"] == "missing_data"
        assert collector.items[0]["message"] == ("request.security('OKX:BTC/USDT_MARK', '1'): mark and index prices "
                                                 "are for futures and perpetual contracts")
        assert [item.params["series"]["priceBasis"] for item in fake.sessions] == ["last"]  # nothing subscribed
        await engine.close()
        await hub.close()
    asyncio.run(main())
