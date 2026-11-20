"""Performance line (build.md): 500 alerts watching — among them a 200-member price watchlist
alert and a 50-member indicator watchlist alert — keep the Host event loop's lag at p99 ≤ 20
ms while ticks arrive (spread over time as from many symbols; prices jumping around the alert
levels make alerts fire all the time). Lag is how long a callback scheduled with ``call_soon``
waits."""
import asyncio
import random
import statistics
import time

import pytest

from modules.chart.backend.app import ChartApp
from modules.chart.backend.wire import AlertCreate, WatchlistCreate
from tests.unit.chart.fake_market import BTC, FakeMarket, series_of
from tests.unit.chart.test_pine_engine import bars
from vocabulary.identity import instrument_id


def coin(index):
    record = {**BTC, "symbol": f"C{index}/USDT", "displaySymbol": f"C{index}/USDT", "base": f"C{index}"}
    record.pop("instrumentId")
    return {**record, "instrumentId": instrument_id(record)}


@pytest.mark.timing
def test_500_alerts_keep_the_event_loop_lag_under_20_ms(tmp_path):
    async def main():
        coins = [coin(index) for index in range(200)]
        market = FakeMarket(instruments=coins)
        app = ChartApp(market, tmp_path / "data", tmp_path / "indicators")

        class Quiet:
            async def deliver(self, *args):
                return {}
        app.alerts.notifier = Quiet()
        app.store.put_object("seeded", {"id": "w1"}, "w1")
        app.workspace_opened("w1")
        everyone = await app.create_watchlist("w1", WatchlistCreate.model_validate({"name": "All", "sections": [
            {"id": "a", "name": "", "members": [{"instrument": item} for item in coins]}]}))
        some = await app.create_watchlist("w1", WatchlistCreate.model_validate({"name": "Some", "sections": [
            {"id": "a", "name": "", "members": [{"instrument": item} for item in coins[:50]]}]}))
        price = [{"left": {"kind": "price"}, "op": "crossing", "right": {"kind": "value", "value": 100}}]
        await app.create_alert("w1", AlertCreate.model_validate({"spec": {
            "target": {"kind": "watchlist", "watchlistId": everyone["id"], "timeframe": "1m"},
            "conditions": price, "frequency": "once_per_bar"}}))
        await app.create_alert("w1", AlertCreate.model_validate({"spec": {
            "target": {"kind": "watchlist", "watchlistId": some["id"], "timeframe": "1m"},
            "conditions": [{"left": {"kind": "indicator", "definition": {"source": "builtin", "id": "rsi"},
                            "output": "rsi"}, "op": "crossing", "right": {"kind": "value", "value": 50}}],
            "frequency": "once_per_bar"}}))
        for index in range(498):
            item = coins[index % 200]
            await app.create_alert("w1", AlertCreate.model_validate({"spec": {
                "target": {"kind": "series", "instrument": item, "series": series_of(item)},
                "conditions": [{"left": {"kind": "price"}, "op": "crossing",
                                "right": {"kind": "value", "value": 95 + index % 10}}],
                "frequency": "once_per_bar"}}))
        sessions = [await market.session(item["instrumentId"]) for item in coins]
        history = bars(330, 4)
        for session in sessions:
            session.replace(history[:300])
            session.status("live")
        for _ in range(3000):  # every indicator instance has its window
            if all(tap.ready for watchings in app.alerts.running.values() for watching in watchings
                   for tap in watching.taps.values()):
                break
            await asyncio.sleep(0.05)

        lags, done = [], asyncio.Event()

        async def sample():
            loop = asyncio.get_running_loop()
            while not done.is_set():
                start = time.perf_counter()
                waited = loop.create_future()
                loop.call_soon(lambda: waited.set_result(time.perf_counter() - start))
                lags.append(await waited)
                await asyncio.sleep(0.01)
        sampler = asyncio.ensure_future(sample())
        rng = random.Random(1)
        began = time.perf_counter()
        for step in range(1000):  # 10 s: every symbol ticks 10 times a second (a tenth of them every 10 ms),
            bar = history[300 + step // 200]  # a bar closes every 2 s; prices jump around the alert levels
            for session in sessions[step % 10::10]:
                close = 95 + rng.random() * 10
                session.update({**bar, "close": close, "high": max(close, bar["high"]), "low": min(close, bar["low"])})
                if step % 200 >= 190:
                    session.append([{**bar, "close": close}])
            await asyncio.sleep(max(0.0, began + (step + 1) * 0.01 - time.perf_counter()))
        done.set()
        await sampler
        p99 = statistics.quantiles(lags, n=100)[98] * 1000
        fired = len(app.store.fired("w1", None, None, 500))
        print(f"\n500 alerts: loop lag p50 {statistics.median(lags) * 1000:.2f} ms, p99 {p99:.2f} ms, "
              f"max {max(lags) * 1000:.2f} ms over {len(lags)} samples; {fired} fired")
        assert fired > 0
        assert p99 <= 20
        await app.close()
    asyncio.run(main())
