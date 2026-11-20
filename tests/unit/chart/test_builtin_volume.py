"""Volume built-ins that read intrabars (request.security_lower_tf): values against an
independent computation, and stepped live equal to a fresh run."""
import asyncio
import math

from modules.chart.backend.pine.engine import PineEngine
from tests.unit.chart.test_pine_engine import SYMINFO, Collector, bars, builtin, columns, run

DAY = 86_400_000


def minutes_and_fives(count=1000):
    minute = [{**row, "time": row["time"] - 200_000, "closeTime": row["closeTime"] - 200_000} for row in bars(count, 3)]
    for index in range(0, count, 7):  # unchanged intrabars count by their move from the one before
        minute[index] = {**minute[index], "close": minute[index]["open"]}
    five = []
    for start in range(0, count, 5):
        group = minute[start:start + 5]
        five.append({"time": group[0]["time"], "closeTime": group[-1]["closeTime"], "open": group[0]["open"],
                     "close": group[-1]["close"], "high": max(row["high"] for row in group),
                     "low": min(row["low"] for row in group), "volume": sum(row["volume"] for row in group)})
    return minute, five


def expected_cvd(minute, five):
    total, previous, day, out = 0.0, None, None, []
    for bar in five:
        if day is not None and bar["time"] // DAY != day:
            total = 0.0
        day = bar["time"] // DAY
        start = top = bottom = total
        for row in (row for row in minute if bar["time"] <= row["time"] < bar["closeTime"]):
            if row["close"] > row["open"] or row["close"] == row["open"] and previous is not None and row["close"] > previous:
                total += row["volume"]
            elif row["close"] < row["open"] or row["close"] == row["open"] and previous is not None and row["close"] < previous:
                total -= row["volume"]
            top, bottom, previous = max(top, total), min(bottom, total), row["close"]
        out.append((start, top, bottom, total))
    return out


def close_to(left, right):
    return all(math.isclose(a, b, rel_tol=1e-9, abs_tol=1e-6) for a, b in zip(left, right, strict=True))


def test_cumulative_volume_delta_matches_its_definition_and_steps_live_like_a_recompute(tmp_path):
    async def main():
        minute, five = minutes_and_fives()
        history = 180

        def start(name, count):
            engine = PineEngine(tmp_path / name)
            engine.supply = lambda symbol, timeframe, start: (
                asyncio.get_running_loop().call_soon(engine.set_secondary, (symbol, timeframe), columns(minute[:count * 5]),
                                                     None)
                or (lambda: None))
            engine.set_series("s", {**SYMINFO, "period": "5"}, columns(five[:count]), None)
            return engine

        engine = start("live", history)
        live = Collector()
        await engine.attach("s", builtin("cumulative_volume_delta"), {}, live)
        await live.wait(1)
        assert live.items[0]["op"] == "replace", live.items[0]
        for index in range(history, len(five)):
            for row in minute[index * 5:index * 5 + 5]:
                engine.secondary_bar((f"{SYMINFO['prefix']}:{SYMINFO['ticker']}", "1"), row, True)
            engine.bar("s", five[index], True)
            await live.wait(index - history + 2)
        times, values = live.rows()
        await engine.close()

        fresh_engine = start("fresh", len(five))
        fresh = Collector()
        await fresh_engine.attach("s", builtin("cumulative_volume_delta"), {}, fresh)
        await fresh.wait(1)
        fresh_times, fresh_values = fresh.rows()
        await fresh_engine.close()

        expected = expected_cvd(minute, five)
        assert times == fresh_times == [bar["time"] for bar in five]
        assert any(bar["time"] // DAY != five[0]["time"] // DAY for bar in five)  # an anchor reset is inside
        for position, name in enumerate(("open", "high", "low", "close")):
            assert close_to(values[f"CVD ({name})"], [item[position] for item in expected]), name
            assert values[f"CVD ({name})"][history:] == fresh_values[f"CVD ({name})"][history:], name
    run(main())


def poc_of(rows_in, count):
    """The point of control of intrabars, volume spread over the price rows each one covers."""
    top, bottom = max(row["high"] for row in rows_in), min(row["low"] for row in rows_in)
    step = (top - bottom) / count
    totals = [0.0] * count
    for row in rows_in:
        for index in range(count):
            overlap = min(row["high"], bottom + (index + 1) * step) - max(row["low"], bottom + index * step)
            totals[index] += row["volume"] * max(overlap, 0.0) / (row["high"] - row["low"])
    return bottom + (totals.index(max(totals)) + 0.5) * step


def shapes(item):
    return {kind: sorted(tuple(sorted((key, value) for key, value in shape.items() if key != "id"))
                         for shape in item["drawings"][kind]) for kind in ("boxes", "lines")}


def test_volume_profiles_draw_sessions_and_ranges_and_step_live_like_a_recompute(tmp_path):
    async def main():
        minute, five = minutes_and_fives()
        history = 180
        forming = {**five[-1], "time": five[-1]["closeTime"], "closeTime": five[-1]["closeTime"] + 300_000}
        sessions = sorted({bar["time"] // DAY for bar in five})
        frvp_params = {"start_time": five[20]["time"], "end_time": five[60]["time"]}

        async def run_profiles(name, count, steps):
            engine = PineEngine(tmp_path / name)
            engine.supply = lambda symbol, timeframe, start: (
                asyncio.get_running_loop().call_soon(engine.set_secondary, (symbol, timeframe),
                                                     columns(minute[:count * 5]), None) or (lambda: None))
            engine.set_series("s", {**SYMINFO, "period": "5"}, columns(five[:count]), None)
            collectors = {"session_volume_profile": Collector(), "fixed_range_volume_profile": Collector()}
            instances = {"session_volume_profile": await engine.attach(
                "s", builtin("session_volume_profile"), {}, collectors["session_volume_profile"]),
                "fixed_range_volume_profile": await engine.attach(
                "s", builtin("fixed_range_volume_profile"), frvp_params, collectors["fixed_range_volume_profile"])}
            for collector in collectors.values():
                await collector.wait(1)
            for index in steps:
                for row in minute[index * 5:index * 5 + 5]:
                    engine.secondary_bar((f"{SYMINFO['prefix']}:{SYMINFO['ticker']}", "1"), row, True)
                counts = {key: len(collector.items) for key, collector in collectors.items()}
                engine.bar("s", five[index], True)
                for key, collector in collectors.items():
                    await collector.wait(counts[key] + 1)
            counts = {key: len(collector.items) for key, collector in collectors.items()}
            engine.bar("s", forming, False)  # the last bar: the developing session is drawn
            for key, collector in collectors.items():
                await collector.wait(counts[key] + 1)
            kept = {key: instance.replace() for key, instance in instances.items()}  # the steps' changes folded in
            await engine.close()
            return kept

        live = await run_profiles("live", history, range(history, len(five)))
        fresh = await run_profiles("fresh", len(five), ())
        for key in live:
            assert shapes(live[key]) == shapes(fresh[key]), key

        session = live["session_volume_profile"]["drawings"]
        assert len(session["lines"]) == len(sessions)  # a point of control per session, the developing one too
        first = [row for row in minute if row["time"] // DAY == sessions[0]]
        poc = min(session["lines"], key=lambda shape: shape["x1"])
        assert poc["x1"] == five[0]["time"] and math.isclose(poc["y1"], poc_of(first, 24))
        assert 0 < len(session["boxes"]) <= 48 * len(sessions)

        ranged = live["fixed_range_volume_profile"]["drawings"]
        inside = [row for row in minute if frvp_params["start_time"] <= row["time"] < frvp_params["end_time"]]
        assert len(ranged["lines"]) == 1
        line = ranged["lines"][0]
        assert (line["x1"], line["x2"]) == (frvp_params["start_time"], frvp_params["end_time"])
        assert math.isclose(line["y1"], poc_of(inside, 24))
        assert {shape["left"] for shape in ranged["boxes"]} >= {frvp_params["start_time"]}
    run(main())


def test_the_intrabar_timeframe_follows_tradingviews_rules_unless_chosen(tmp_path):
    """Volume profiles take TradingView's table (Session Volume Profile help: 1-15 minutes read 1, 16-30 5,
    31-60 10, 61-120 15, 121-240 30, longer 60); Cumulative Volume Delta its own rule (intraday 1, daily 5,
    longer 60); "Use custom timeframe" takes the chosen one."""
    async def main():
        engine = PineEngine(tmp_path / "logs")
        def supply(symbol, timeframe, start):
            asyncio.get_running_loop().call_soon(engine.set_secondary, (symbol, timeframe), columns(bars(50, 7)), None)
            return lambda: None
        engine.supply = supply
        profiles = ("session_volume_profile", "fixed_range_volume_profile", "visible_range_volume_profile")
        cases = [*((period, {}, builtin_id, lower) for builtin_id in profiles for period, lower in (
                      ("5", "1"), ("15", "1"), ("30", "5"), ("45", "10"), ("60", "10"), ("120", "15"), ("240", "30"),
                      ("1D", "60"), ("1W", "60"))),
                 *(("5", {}, "cumulative_volume_delta", "1"), ("1D", {}, "cumulative_volume_delta", "5"),
                   ("1W", {}, "cumulative_volume_delta", "60")),
                 *(("5", {"custom": True, "chosen": "3"}, builtin_id, "3") for builtin_id in
                   ("cumulative_volume_delta", *profiles))]
        for period in {case[0] for case in cases}:
            engine.set_series(period, {**SYMINFO, "period": period}, columns(bars(50, 4)), None)
        own = f"{SYMINFO['prefix']}:{SYMINFO['ticker']}"
        for period, params, builtin_id, expected in cases:
            collector = Collector()
            instance = await engine.attach(period, builtin(builtin_id), params, collector)
            await collector.wait(1)
            assert collector.items[0]["op"] == "replace", collector.items[0]
            assert instance.needs == {(own, expected)}, (builtin_id, period, params, instance.needs)
        await engine.close()
    run(main())
