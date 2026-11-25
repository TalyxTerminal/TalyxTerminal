"""request.security whose symbol and timeframe come from inputs (Pine "simple" values): asked
for when PyneCore resolves the context, live like constants, released when an input changes,
and every failure on the indicator."""
import asyncio
import os
import textwrap

from modules.chart.backend.pine.engine import START_SECONDS, PineEngine
from tests.unit.chart.test_pine_engine import SYMINFO, Collector, bars, columns, project, run, same

OWN = f"{SYMINFO['prefix']}:{SYMINFO['ticker']}"
INPUTS = """
    \"\"\"@pyne\"\"\"
    from pynecore.lib import array, close, input, na, plot, request, script, syminfo, volume

    @script.indicator("Inputs")
    def main(sym: str = input.symbol("OKX:ETHUSDT", "Symbol"), tf: str = input.timeframe("15", "Timeframe"),
             lower: str = input.timeframe("1", "Intrabar")):
        plot(request.security(sym, tf, close), "other")
        volumes = request.security_lower_tf(syminfo.tickerid, lower, volume)
        plot(array.sum(volumes) if array.size(volumes) > 0 else na, "intrabar_volume")
        plot(close, "own")
    """


def grouped(rows, size, end=None):
    """Bars of ``size`` minutes from 1-minute rows[:end]; the last is forming when end is mid-period."""
    end = len(rows) if end is None else end
    out = []
    for start in range(0, end, size):
        group = rows[start:min(start + size, end)]
        out.append({"time": group[0]["time"], "closeTime": rows[start + size - 1]["closeTime"],
                    "open": group[0]["open"], "high": max(row["high"] for row in group),
                    "low": min(row["low"] for row in group), "close": group[-1]["close"],
                    "volume": sum(row["volume"] for row in group)})
    return out


def market():
    shift = -(1_700_000_000_000 % 900_000)  # onto the 15-minute grid
    minutes = {symbol: [{**row, "time": row["time"] + shift, "closeTime": row["closeTime"] + shift}
                        for row in bars(3000, seed)] for symbol, seed in ((OWN, 31), ("OKX:ETHUSDT", 32),
                                                                         ("OKX:SOLUSDT", 33))}
    return minutes


def history_of(minutes, symbol, timeframe, end):
    size = {"1": 1, "5": 5, "15": 15}[timeframe]
    return grouped(minutes[symbol], size, end - end % size) if size > 1 else list(minutes[symbol][:end])


class Supplier:
    """Chart's side for the engine: the history of each pair asked for, which ones were let go."""

    def __init__(self, engine, minutes, end):
        self.engine, self.minutes, self.end = engine, minutes, end
        self.asked, self.released, self.starts = [], [], []
        engine.supply = self

    def __call__(self, symbol, timeframe, start):
        self.asked.append((symbol, timeframe))
        self.starts.append(start)
        if symbol in self.minutes:
            asyncio.get_running_loop().call_soon(self.engine.set_secondary, (symbol, timeframe),
                                                 columns(history_of(self.minutes, symbol, timeframe, self.end)), None)
        return lambda: self.released.append((symbol, timeframe))


def test_input_chosen_contexts_step_live_like_a_recompute_and_an_input_change_lets_the_old_feed_go(tmp_path):
    async def main():
        minutes = market()
        chart = grouped(minutes[OWN], 5)
        definition = project(tmp_path / "inputs", {"main.py": INPUTS})
        history = 400  # 5-minute bars; live starts inside a 15-minute period
        engine = PineEngine(tmp_path / "live")
        supplier = Supplier(engine, minutes, history * 5)
        engine.set_series("s", {**SYMINFO, "period": "5"}, columns(chart[:history]), None)
        live = Collector()
        instance = await engine.attach("s", definition, {}, live)
        await live.wait(1)
        assert live.items[0]["op"] == "replace", live.items[0]
        assert sorted(supplier.asked) == [("OKX:ETHUSDT", "15"), (OWN, "1")]
        for index in range(history, 590):
            for minute in range(index * 5, index * 5 + 5):
                engine.secondary_bar((OWN, "1"), minutes[OWN][minute], True)
            end = index * 5 + 5
            engine.secondary_bar(("OKX:ETHUSDT", "15"), grouped(minutes["OKX:ETHUSDT"], 15, end)[-1], end % 15 == 0)
            engine.bar("s", chart[index], True)
            await live.wait(index - history + 2)
        times, values = live.rows()

        fresh_engine = PineEngine(tmp_path / "fresh")
        Supplier(fresh_engine, minutes, 590 * 5)
        fresh_engine.set_series("s", {**SYMINFO, "period": "5"}, columns(chart[:590]), None)
        fresh = Collector()
        await fresh_engine.attach("s", definition, {}, fresh)
        await fresh.wait(1)
        fresh_times, fresh_values = fresh.rows()
        await fresh_engine.close()
        assert times == fresh_times
        live_rows = range(history - 590, 0)
        for key in ("intrabar_volume", "own"):  # every closed bar
            assert same([values[key][i] for i in live_rows], [fresh_values[key][i] for i in live_rows]), key
        assert values["intrabar_volume"][-1] == sum(row["volume"] for row in minutes[OWN][589 * 5:590 * 5])
        closing = [i for i in live_rows if (590 + i + 1) % 3 == 0]  # the 15-minute bar's last 5 minutes
        assert same([values["other"][i] for i in closing], [fresh_values["other"][i] for i in closing])

        # Another symbol in the input: a new instance on SOL; the ETH feed is let go once nothing
        # reads it and the new instance has asked for its feeds, the intrabars both read stay.
        engine.grace = 0.0
        supplier.end = 590 * 5
        moved = Collector()
        await engine.attach("s", definition, {"sym": "OKX:SOLUSDT"}, moved)
        engine.detach(instance, live)
        await moved.wait(1)
        assert moved.items[0]["op"] == "replace", moved.items[0]
        assert supplier.asked[2:] == [("OKX:SOLUSDT", "15")] and supplier.released == [("OKX:ETHUSDT", "15")]
        sol = history_of(minutes, "OKX:SOLUSDT", "15", 590 * 5)  # its closed 15-minute bars
        assert moved.items[0]["values"]["other"][-1] == sol[-1]["close"]
        await engine.close()
    run(main())


def test_failures_land_on_the_indicator_and_never_hang_it(tmp_path):
    """An unknown symbol (Chart says why), no bars in time, too many pairs, and a derived suffix
    on an input symbol: each instance ends in its error state with the reason."""
    async def main():
        os.environ["TALYX_PINE_NEED_SECONDS"] = "3"
        try:
            engine = PineEngine(tmp_path / "logs")
            minutes = market()
            asked = []

            def supply(symbol, timeframe, start):
                asked.append((symbol, timeframe))
                if symbol in ("OKX:NOPE", "OKX:BTC/USDT_FR"):
                    reason = "No instrument okx:NOPE" if symbol == "OKX:NOPE" else "No source streams funding_rate"
                    asyncio.get_running_loop().call_soon(engine.secondary_failed, (symbol, timeframe),
                                                         f"request.security({symbol!r}, {timeframe!r}): {reason}")
                elif symbol == OWN or symbol.startswith("OKX:S") and symbol[5:].isdigit():
                    asyncio.get_running_loop().call_soon(engine.set_secondary, (symbol, timeframe),
                                                         columns(history_of(minutes, OWN, timeframe, 300)), None)
                return lambda: None  # OKX:SILENT never sends anything
            engine.supply = supply
            engine.set_series("s", {**SYMINFO, "period": "5"}, columns(grouped(minutes[OWN], 5)[:60]), None)
            many = "\n".join(f"    plot(request.security('OKX:S{index}', '15', close), 's{index}')" for index in range(41))
            scripts = {
                "unknown": INPUTS,
                "silent": INPUTS,
                "funding": """
                    \"\"\"@pyne\"\"\"
                    from pynecore.lib import close, input, plot, request, script

                    @script.indicator("Funding of")
                    def main(sym: str = input.symbol("OKX:BTC/USDT", "Symbol")):
                        plot(request.security(sym + "_FR", "", close), "rate")
                    """,
                "many": '"""@pyne"""\nfrom pynecore.lib import close, plot, request, script\n\n\n'
                        '@script.indicator("Many")\ndef main():\n' + many + "\n",
            }
            params = {"unknown": {"sym": "OKX:NOPE"}, "silent": {"sym": "OKX:SILENT"}, "funding": {}, "many": {}}
            collectors = {}
            for name, source in scripts.items():
                collectors[name] = Collector()
                definition = project(tmp_path / name, {"main.py": textwrap.dedent(source) if name != "many" else source})
                await engine.attach("s", definition, params[name], collectors[name])
            for collector in collectors.values():
                await collector.wait(1)
            errors = {name: collector.items[0] for name, collector in collectors.items()}
            assert all(item["op"] == "error" and item["code"] == "missing_data" for item in errors.values()), errors
            assert errors["unknown"]["message"] == "request.security('OKX:NOPE', '15'): No instrument okx:NOPE"
            assert errors["silent"]["message"] == "request.security('OKX:SILENT', '15'): Market sent no bars within 3 seconds"
            assert errors["funding"]["message"] == "request.security('OKX:BTC/USDT_FR', '5'): No source streams funding_rate"
            assert errors["many"]["message"].endswith("a script can request at most 40 symbol and timeframe pairs")
            assert ("OKX:S40", "15") not in asked
            await engine.close()
        finally:
            del os.environ["TALYX_PINE_NEED_SECONDS"]
    run(main())


def test_a_replaced_intrabar_history_restarts_its_readers_with_a_start_s_time_and_keeps_the_bar_behind_it(tmp_path):
    """Market replacing a request.security feed's history (a fill, a revised catch-up) starts
    the instances reading it again: the Host must time that as a start, not as the live bar
    queued just before it, or a long window is stopped as a stalled bar."""
    async def main():
        minutes = market()
        chart = grouped(minutes[OWN], 5)
        definition = project(tmp_path / "inputs", {"main.py": INPUTS})
        history = 400
        engine = PineEngine(tmp_path / "live")
        supplier = Supplier(engine, minutes, history * 5)
        engine.set_series("s", {**SYMINFO, "period": "5"}, columns(chart[:history]), None)
        live = Collector()
        instance = await engine.attach("s", definition, {}, live)
        await live.wait(1)
        for minute in range(history * 5, history * 5 + 5):
            engine.secondary_bar((OWN, "1"), minutes[OWN][minute], True)
        engine.bar("s", chart[history], True)
        engine.set_secondary((OWN, "1"), columns(history_of(minutes, OWN, "1", history * 5 + 5)), None)
        await engine.secondaries[(OWN, "1")]["writing"]  # handed to the worker as a file
        assert list(instance.pending) == [START_SECONDS]
        while not any(item["op"] == "replace" for item in live.items[1:]) or instance.pending:
            live.event.clear()
            await asyncio.wait_for(live.event.wait(), 60)
        times, values = live.rows()
        assert times[-1] == chart[history]["time"]
        assert values["intrabar_volume"][-1] == sum(row["volume"] for row in minutes[OWN][history * 5:history * 5 + 5])
        assert supplier.asked.count((OWN, "1")) == 1
        await engine.close()
    run(main())


def test_a_feed_is_supplied_from_its_charts_first_bar_and_again_from_an_earlier_one(tmp_path):
    """request.security covers the chart's bars: the supply starts at the first bar of the window of
    the instance that asks; an instance on a window reaching further back has it supplied again from
    there, and the narrower supply is let go."""
    async def main():
        minutes = market()
        chart = grouped(minutes[OWN], 5)
        definition = project(tmp_path / "inputs", {"main.py": INPUTS})
        engine = PineEngine(tmp_path / "logs")
        supplier = Supplier(engine, minutes, 600 * 5)
        engine.set_series("short", {**SYMINFO, "period": "5"}, columns(chart[300:600]), None)
        engine.set_series("long", {**SYMINFO, "period": "5"}, columns(chart[100:600]), None)
        short, long = Collector(), Collector()
        await engine.attach("short", definition, {}, short)
        await short.wait(1)
        assert supplier.starts == [chart[300]["time"]] * len(supplier.asked)
        asked = len(supplier.asked)
        await engine.attach("long", definition, {}, long)
        await long.wait(1)
        assert supplier.starts[asked:] == [chart[100]["time"]] * (len(supplier.asked) - asked)
        assert sorted(supplier.released) == sorted(supplier.asked[:asked])
        await engine.close()
    run(main())


def test_an_instance_waiting_for_a_feed_starts_again_on_a_whole_window(tmp_path):
    """Market's whole window comes while the script waits for a feed (a cold chart): the run that
    waited stops and lets go of the engine, and the new run gets its feeds and draws."""
    async def main():
        minutes = market()
        chart = grouped(minutes[OWN], 5)
        engine = PineEngine(tmp_path / "logs")
        whole, asked = asyncio.Event(), []

        def supply(symbol, timeframe, start):  # Chart answers once Market's whole window is there
            asked.append((symbol, timeframe))

            async def answer():
                await whole.wait()
                engine.set_secondary((symbol, timeframe), columns(history_of(minutes, symbol, timeframe, 600 * 5)),
                                     None)
            return asyncio.ensure_future(answer()).cancel

        engine.supply = supply
        engine.set_series("s", {**SYMINFO, "period": "5"}, columns(chart[500:600]), None)  # the newest page
        collector = Collector()
        await engine.attach("s", project(tmp_path / "inputs", {"main.py": INPUTS}), {}, collector)
        for _ in range(500):
            if asked:
                break
            await asyncio.sleep(0.02)
        assert asked, "the run never asked for a feed"
        engine.set_series("s", {**SYMINFO, "period": "5"}, columns(chart[100:600]), None)  # the whole window
        whole.set()
        await collector.wait(1)
        assert collector.items[0]["op"] == "replace" and len(collector.items[0]["time"]) == 500, collector.items[0]
        await engine.close()
    run(main())

OTHER = """
    \"\"\"@pyne\"\"\"
    from pynecore.lib import close, plot, request, script

    @script.indicator("Other")
    def main():
        plot(request.security("OKX:ETHUSDT", "15", close), "other")
    """


def test_a_feed_let_go_and_asked_for_again_waits_for_its_new_history(tmp_path, monkeypatch):
    """Nothing reads a feed any more and it is let go; a script asking for it again in the same
    process waits for the history supplied anew, not the one handed over before."""
    monkeypatch.setattr("modules.chart.backend.pine.engine.POOL", 1)

    async def main():
        minutes = market()
        chart = grouped(minutes[OWN], 5)
        need = ("OKX:ETHUSDT", "15")
        definition = project(tmp_path / "other", {"main.py": OTHER})
        engine = PineEngine(tmp_path / "logs")
        engine.grace = 0.0
        asked = []
        engine.supply = lambda symbol, timeframe, start: asked.append((symbol, timeframe)) or (lambda: None)
        engine.set_series("s", {**SYMINFO, "period": "5"}, columns(chart[:500]), None)

        async def until(condition):
            for _ in range(500):
                if condition():
                    return
                await asyncio.sleep(0.02)
            raise AssertionError("never")
        first = Collector()
        instance = await engine.attach("s", definition, {}, first)
        await until(lambda: asked)
        engine.set_secondary(need, columns(history_of(minutes, *need, 400 * 5)), None)
        await first.wait(1)
        engine.detach(instance, first)
        await until(lambda: need not in engine.supplies)  # let go
        again = Collector()
        await engine.attach("s", definition, {}, again)
        await until(lambda: len(asked) == 2)
        await asyncio.sleep(1)  # a run that went on with the old history has answered by now
        engine.set_secondary(need, columns(history_of(minutes, *need, 500 * 5)), None)
        await again.wait(1)
        assert again.items[0]["op"] == "replace", again.items[0]
        assert again.items[0]["values"]["other"][-1] == history_of(minutes, *need, 500 * 5)[-1]["close"]
        await engine.close()
    run(main())


def test_a_history_file_reads_back_as_the_feed_plugin_keeps_rows(tmp_path):
    from modules.chart.backend.pine import history
    rows = bars(5, 9)
    rows[2] = {**rows[2], "volume": None}
    asyncio.run(history.write(tmp_path / "h.bin", columns(rows), 4))
    assert list(history.read(tmp_path / "h.bin", 4)) == [
        ["c", row["time"], row["closeTime"], row["open"], row["high"], row["low"], row["close"], row["volume"]]
        for row in rows[:4]]



def test_a_feed_is_supplied_on_the_newest_page_and_again_from_the_whole_windows_first_bar(tmp_path):
    """While Market fills the chart's window, a script's feed is supplied as its own standard window
    (no start) so the script draws at once; the whole window supplies it again from its first bar."""
    async def main():
        minutes = market()
        chart = grouped(minutes[OWN], 5)
        engine = PineEngine(tmp_path / "logs")
        supplier = Supplier(engine, minutes, 600 * 5)
        engine.set_series("s", {**SYMINFO, "period": "5"}, columns(chart[500:600]), None,
                          whole=False)
        collector = Collector()
        await engine.attach("s", project(tmp_path / "inputs", {"main.py": INPUTS}), {}, collector)
        await collector.wait(1)
        assert collector.items[0]["op"] == "replace" and set(supplier.starts) == {None}
        engine.set_series("s", {**SYMINFO, "period": "5"}, columns(chart[100:600]), None)
        for _ in range(500):
            if chart[100]["time"] in supplier.starts:
                break
            await asyncio.sleep(0.02)
        assert chart[100]["time"] in supplier.starts
        await engine.close()
    run(main())


def test_a_lower_timeframe_reads_each_calendar_month_and_quarter_of_its_chart_bar(tmp_path):
    """A month, a quarter or a year is no fixed number of seconds: with 30.4 days a month, January 2025's bar missed
    January 31 and February's read March 1 and 2 (a look ahead); a quarter of 91.3 days took April 1 into Q1. The
    chart bar ends at Market's closeTime."""
    from datetime import UTC, date, datetime, timedelta

    def ms(day):
        return int(datetime(day.year, day.month, day.day, tzinfo=UTC).timestamp() * 1000)

    days = [date(2025, 1, 1) + timedelta(days=index) for index in range(181)]  # Jan 1 to Jun 30, 2025, every day
    daily = [{"time": ms(day), "closeTime": ms(day + timedelta(days=1)), "open": 1.0, "high": 1.0, "low": 1.0,
              "close": float(day.strftime("%Y%m%d")), "volume": 1.0} for day in days]

    def periods(starts):
        return [{"time": ms(start), "closeTime": ms(end), "open": 1.0, "high": 1.0, "low": 1.0, "close": 1.0,
                 "volume": 1.0} for start, end in zip(starts, starts[1:])]
    months = periods([date(2025, month, 1) for month in range(1, 8)])
    quarters = periods([date(2025, 1, 1), date(2025, 4, 1), date(2025, 7, 1)])
    script = """
        \"\"\"@pyne\"\"\"
        from pynecore.lib import array, close, na, plot, request, script, syminfo

        @script.indicator("Days")
        def main():
            days = request.security_lower_tf(syminfo.tickerid, "1D", close)
            plot(array.first(days) if array.size(days) > 0 else na, "first")
            plot(array.last(days) if array.size(days) > 0 else na, "last")
            plot(array.size(days), "count")
        """

    async def main():
        engine = PineEngine(tmp_path / "logs")
        engine.supply = lambda symbol, timeframe, start: (
            asyncio.get_running_loop().call_soon(engine.set_secondary, (symbol, timeframe), columns(daily), None),
            lambda: None)[1]
        engine.set_series("month", {**SYMINFO, "period": "1M"}, columns(months), None)
        engine.set_series("quarter", {**SYMINFO, "period": "3M"}, columns(quarters), None)
        definition = project(tmp_path / "days", {"main.py": script})
        found = {}
        for key in ("month", "quarter"):
            collector = Collector()
            await engine.attach(key, definition, {}, collector)
            await collector.wait(1)
            assert collector.items[0]["op"] == "replace", collector.items[0]
            values = collector.items[0]["values"]
            found[key] = list(zip(values["first"], values["last"], values["count"]))
        assert found["month"] == [(20250101, 20250131, 31), (20250201, 20250228, 28), (20250301, 20250331, 31),
                                  (20250401, 20250430, 30), (20250501, 20250531, 31), (20250601, 20250630, 30)]
        assert found["quarter"] == [(20250101, 20250331, 90), (20250401, 20250630, 91)]
        await engine.close()
    run(main())
