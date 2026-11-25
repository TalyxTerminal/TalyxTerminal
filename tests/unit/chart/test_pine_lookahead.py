"""request.security's lookahead on a higher timeframe, TradingView's merge rule (its "Repainting"
page): on history ``lookahead_off`` gets a period's value on the chart bar that closes it and the
last closed period's before that; ``lookahead_on`` with an expression offset by ``[1]`` gets the
last closed period's value from the period's first chart bar on (the non-repainting idiom, the
same on history and in real time; an expression other than one offset series is offset as a
whole with ``inline_series``). In real time ``lookahead_off`` follows the forming period.

A 1-hour chart of eight UTC days asks for its own symbol's and another symbol's daily bars."""
import asyncio
import math
import random

from modules.chart.backend.pine.engine import PineEngine
from tests.unit.chart.test_pine_engine import SYMINFO, Collector, columns, project, run

HOUR, DAY = 3_600_000, 86_400_000
START = 19676 * DAY  # a UTC midnight
OWN = f"{SYMINFO['prefix']}:{SYMINFO['ticker']}"
OTHER = "OKX:ETHUSDT"
DAYS = 8
HISTORY = 24 * 5 + 7  # hourly bars on history: live starts at 07:00 of the sixth day

SCRIPT = """
    \"\"\"@pyne\"\"\"
    from pynecore.core.series import inline_series
    from pynecore.lib import barmerge, close, plot, request, script, syminfo, ta

    @script.indicator("Lookahead")
    def main():
        plot(request.security(syminfo.tickerid, "D", close[1], lookahead=barmerge.lookahead_on), "on1")
        plot(request.security(syminfo.tickerid, "D", close, lookahead=barmerge.lookahead_on), "on0")
        plot(request.security(syminfo.tickerid, "D", close[1]), "off1")
        plot(request.security(syminfo.tickerid, "D", close), "off0")
        plot(request.security("OKX:ETHUSDT", "D", close[1], lookahead=barmerge.lookahead_on), "other_on1")
        plot(request.security(syminfo.tickerid, "D", inline_series(ta.sma(close, 3), 1),
                              lookahead=barmerge.lookahead_on), "sma1")
    """


def hours(seed):
    rng = random.Random(seed)
    price, rows = 100.0, []
    for index in range(24 * DAYS):
        open_ = price
        price = round(price + rng.uniform(-2, 2), 2)
        time = START + index * HOUR
        rows.append({"time": time, "closeTime": time + HOUR, "open": open_, "close": price,
                     "high": max(open_, price) + 0.5, "low": min(open_, price) - 0.5, "volume": 1.0})
    return rows


def daily(rows, end):
    """Daily bars of hourly rows[:end]; the last one is forming when end is inside a day."""
    out = []
    for start in range(0, end, 24):
        group = rows[start:min(start + 24, end)]
        out.append({"time": group[0]["time"], "closeTime": group[0]["time"] + DAY, "open": group[0]["open"],
                    "high": max(row["high"] for row in group), "low": min(row["low"] for row in group),
                    "close": group[-1]["close"], "volume": float(len(group))})
    return out


def expected(rows, index, forming_close=None):
    """Each plot's value on hourly bar ``index`` from the daily bars, worked out independently.
    ``forming_close``: the bar is the real-time bar, still forming with this close."""
    closes = [bar["close"] for bar in daily(rows, len(rows))]
    day, hour = divmod(index, 24)

    def close_of(d):
        return closes[d] if d >= 0 else math.nan
    sma1 = sum(closes[day - 3:day]) / 3 if day >= 3 else math.nan
    if forming_close is not None:  # real time: lookahead_off follows the forming day
        return {"on1": close_of(day - 1), "off1": close_of(day - 1), "off0": forming_close, "sma1": sma1}
    return {"on1": close_of(day - 1), "sma1": sma1,
            "off1": close_of(day - 1) if hour == 23 else close_of(day - 2),
            "off0": close_of(day) if hour == 23 else close_of(day - 1)}


def equal(a, b):
    if a is None or isinstance(a, float) and math.isnan(a):
        return math.isnan(b)
    return math.isclose(a, b, rel_tol=1e-12)


def test_lookahead_on_with_an_offset_gets_the_last_closed_period_on_every_bar_and_off_on_its_last(tmp_path):
    async def main():
        rows = {OWN: hours(1), OTHER: hours(2)}
        engine = PineEngine(tmp_path / "logs")

        def supply(symbol, timeframe, start):  # Market's closed days and the day still forming
            asyncio.get_running_loop().call_soon(engine.set_secondary, (symbol, timeframe),
                                                 columns(daily(rows[symbol], HISTORY - HISTORY % 24)),
                                                 daily(rows[symbol], HISTORY)[-1])
            return lambda: None
        engine.supply = supply
        engine.set_series("s", {**SYMINFO, "period": "60"}, columns(rows[OWN][:HISTORY]), None)
        live = Collector()
        await engine.attach("s", project(tmp_path / "lookahead", {"main.py": SCRIPT}), {}, live)
        await live.wait(1)
        assert live.items[0]["op"] == "replace", live.items[0]
        end = HISTORY + 30
        for index in range(HISTORY, end):  # closed in real time: Market's daily bar as it stands
            for symbol in (OWN, OTHER):
                day = daily(rows[symbol], index + 1)[-1]
                engine.secondary_bar((symbol, "D"), day, (index + 1) % 24 == 0)
            engine.bar("s", rows[OWN][index], True)
            await live.wait(1 + index - HISTORY + 1)
        times, values = live.rows()
        assert times == [row["time"] for row in rows[OWN][:end]]
        other_closes = [bar["close"] for bar in daily(rows[OTHER], len(rows[OTHER]))]
        for index in range(end):
            realtime = index >= HISTORY
            want = expected(rows[OWN], index, rows[OWN][index]["close"] if realtime else None)
            # The last historical day only has its accepted close at the snapshot.
            last = index if realtime else min((index // 24 + 1) * 24, HISTORY) - 1
            want["on0"] = rows[OWN][last]["close"]
            for key, value in want.items():
                assert equal(values[key][index], value), (key, index, values[key][index], value)
            prior = other_closes[index // 24 - 1] if index >= 24 else math.nan
            assert equal(values["other_on1"][index], prior), index

        # The forming bar: lookahead_on [1] keeps yesterday's close, lookahead_off follows today's.
        forming = {**rows[OWN][end], "close": 77.25}
        engine.secondary_bar((OWN, "D"), {**daily(rows[OWN], end + 1)[-1], "close": 77.25}, False)
        engine.secondary_bar((OTHER, "D"), daily(rows[OTHER], end + 1)[-1], False)
        engine.bar("s", forming, False)
        await live.wait(len(live.items) + 1)
        update = live.items[-1]
        assert update["op"] == "update", update
        want = expected(rows[OWN], end, 77.25)
        want["on0"] = 77.25
        for key, value in want.items():
            assert equal(update["values"][key][0], value), (key, update["values"][key], value)
        assert update["values"]["other_on1"] == [other_closes[end // 24 - 1]]
        await engine.close()
    run(main())
