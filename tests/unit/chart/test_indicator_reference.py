"""Built-in indicators against independent reference implementations (done criterion 5).

The references follow TradingView's conventions: RSI and ATR use Wilder smoothing (an RMA seeded
with the simple average of the first values), Bollinger Bands the population standard deviation,
MACD exponential averages (seeded with a simple average), VWAP resets at each session, which for
crypto starts at 00:00 UTC. The data is a real slice of OKX BTC-USDT 1-hour bars.
"""
import asyncio
import json
import math
from pathlib import Path

from modules.chart.backend.pine.engine import Definition, PineEngine

BUILTIN = Path(__file__).resolve().parents[3] / "modules/chart/backend/builtin"
FIXTURE = Path(__file__).resolve().parents[2] / "fixtures/chart/btc_1h.json"
SYMINFO = {"prefix": "OKX", "description": "BTC/USDT", "ticker": "BTCUSDT", "currency": "USDT", "basecurrency": "BTC",
           "period": "60", "type": "crypto", "mintick": 0.1, "pricescale": 10, "timezone": "UTC"}
HOUR = 3_600_000
WARMUP = 100  # bars before values are compared (the longest window here is 52 + 26)
TOLERANCE = 1e-9


def load():
    rows = json.loads(FIXTURE.read_text(encoding="utf-8"))["rows"]
    bars = {"time": [], "closeTime": [], "open": [], "high": [], "low": [], "close": [], "volume": []}
    for time, *values in rows:
        bars["time"].append(time)
        bars["closeTime"].append(time + HOUR)
        for name, value in zip(("open", "high", "low", "close", "volume"), values):
            bars[name].append(float(value))
    return bars


# ── references ──

def sma(values, length):
    out = [None] * len(values)
    for index in range(length - 1, len(values)):
        window = values[index - length + 1:index + 1]
        if None not in window:
            out[index] = sum(window) / length
    return out


def smoothed(values, length, alpha):
    """An exponential average seeded with the simple average of its first ``length`` values."""
    out, previous = [None] * len(values), None
    seed = sma(values, length)
    for index, value in enumerate(values):
        if value is None:
            continue
        previous = seed[index] if previous is None else alpha * value + (1 - alpha) * previous
        out[index] = previous
    return out


def ema(values, length):
    return smoothed(values, length, 2 / (length + 1))


def rma(values, length):
    return smoothed(values, length, 1 / length)


def rsi(close, length):
    changes = [None] + [close[i] - close[i - 1] for i in range(1, len(close))]
    up = rma([None if c is None else max(c, 0.0) for c in changes], length)
    down = rma([None if c is None else max(-c, 0.0) for c in changes], length)
    return [None if u is None or d is None else 100.0 if d == 0 else 0.0 if u == 0 else 100 - 100 / (1 + u / d)
            for u, d in zip(up, down)]


def macd(close, fast, slow, signal):
    line = [None if f is None or s is None else f - s for f, s in zip(ema(close, fast), ema(close, slow))]
    average = ema(line, signal)
    return line, average, [None if a is None else m - a for m, a in zip(line, average)]


def bollinger(close, length, mult):
    basis = sma(close, length)
    upper, lower = [None] * len(close), [None] * len(close)
    for index in range(length - 1, len(close)):
        window = close[index - length + 1:index + 1]
        deviation = math.sqrt(sum((value - basis[index]) ** 2 for value in window) / length)  # population
        upper[index], lower[index] = basis[index] + mult * deviation, basis[index] - mult * deviation
    return basis, upper, lower


def atr(high, low, close, length):
    ranges = [high[0] - low[0]] + [max(high[i] - low[i], abs(high[i] - close[i - 1]), abs(low[i] - close[i - 1]))
                                   for i in range(1, len(close))]
    return rma(ranges, length)


def midpoint(high, low, length):
    return [None if index < length - 1 else
            (max(high[index - length + 1:index + 1]) + min(low[index - length + 1:index + 1])) / 2
            for index in range(len(high))]


def vwap(bars):
    out, total, weight, day = [], 0.0, 0.0, None
    for time, high, low, close, volume in zip(bars["time"], bars["high"], bars["low"], bars["close"], bars["volume"]):
        if time // 86_400_000 != day:  # a new UTC day: a new session
            day, total, weight = time // 86_400_000, 0.0, 0.0
        total += (high + low + close) / 3 * volume
        weight += volume
        out.append(total / weight)
    return out


def test_builtins_equal_independent_references_on_real_btc_bars(tmp_path):
    bars = load()
    high, low, close = bars["high"], bars["low"], bars["close"]
    line, signal, histogram = macd(close, 12, 26, 9)
    basis, upper, lower = bollinger(close, 20, 2.0)
    tenkan, kijun, span_b = midpoint(high, low, 9), midpoint(high, low, 26), midpoint(high, low, 52)
    expected = {
        "rsi": {"rsi": rsi(close, 14)},
        "macd": {"macd": line, "signal": signal, "hist": histogram},
        "boll": {"Basis": basis, "Upper": upper, "Lower": lower},
        "ema": {"ema": ema(close, 20)},
        "atr": {"atr": atr(high, low, close, 14)},
        "ichimoku": {"tenkan": tenkan, "kijun": kijun, "senkou_a": [None if t is None or k is None else (t + k) / 2
                                                                    for t, k in zip(tenkan, kijun)],
                     "senkou_b": span_b, "chikou": close},  # unshifted values; the offsets are plot metadata
        "vwap": {"vwap": vwap(bars)},
    }

    async def main():
        engine = PineEngine(tmp_path / "logs")
        engine.set_series("btc", SYMINFO, bars, None)
        waiting = {}
        for name in expected:
            done = asyncio.get_running_loop().create_future()

            def listener(item, done=done):
                if not done.done():
                    done.set_result(item)
            await engine.attach("btc", Definition(digest=name, path=BUILTIN / f"{name}.py"), {}, listener)
            waiting[name] = done
        replaces = {name: await asyncio.wait_for(done, 120) for name, done in waiting.items()}
        await engine.close()
        return replaces

    replaces = asyncio.run(main())
    for name, outputs in expected.items():
        item = replaces[name]
        assert item["op"] == "replace", (name, item)
        assert item["time"] == bars["time"], name
        for output, reference in outputs.items():
            values = item["values"][output]
            for index in range(WARMUP, len(reference)):
                ours, theirs = values[index], reference[index]
                assert ours is not None and theirs is not None, (name, output, index)
                assert abs(ours - theirs) <= TOLERANCE * max(abs(theirs), 1e-12), (name, output, index, ours, theirs)
