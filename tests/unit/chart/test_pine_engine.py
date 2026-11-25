"""Stepped Pine instances in one worker process.

Every instance must equal its own full recompute value for value, however instances are
interleaved, and projects must not leak modules or state into each other.
"""
import asyncio
import json
import math
import random
import textwrap
import time
from pathlib import Path

from modules.chart.backend.pine.engine import Definition, PineEngine

BUILTIN = Path(__file__).resolve().parents[3] / "modules/chart/backend/builtin"
SYMINFO = {"prefix": "TEST", "description": "X", "ticker": "XUSDT", "currency": "USDT", "basecurrency": "X",
           "period": "1", "type": "crypto", "mintick": 0.01, "pricescale": 100, "timezone": "UTC"}


def bars(count, seed):
    rng = random.Random(seed)
    price, rows = 100.0, []
    for index in range(count):
        open_ = price
        price = max(1.0, price + rng.gauss(0, 1))
        time = 1_700_000_000_000 + index * 60_000
        rows.append({"time": time, "closeTime": time + 60_000, "open": open_, "close": price,
                     "high": max(open_, price) + rng.random(), "low": min(open_, price) - rng.random(),
                     "volume": rng.random() * 100})
    return rows


def columns(rows):
    return {key: [row[key] for row in rows] for key in ("time", "closeTime", "open", "high", "low", "close", "volume")}


def ticks(bar, count, rng):
    out = []
    for step in range(count):
        close = bar["open"] + (bar["close"] - bar["open"]) * (step + 1) / (count + 1) + rng.gauss(0, 0.2)
        out.append({**bar, "close": close, "high": max(bar["open"], close), "low": min(bar["open"], close)})
    return out


class Collector:
    def __init__(self):
        self.items = []
        self.event = asyncio.Event()

    def __call__(self, item):
        self.items.append(item)
        self.event.set()

    async def wait(self, count):
        while len(self.items) < count:
            self.event.clear()
            await asyncio.wait_for(self.event.wait(), 60)

    def rows(self):
        """Closed rows: the replace, then every append (updates are forming values)."""
        values, times = {}, []
        for item in self.items:
            if item["op"] == "replace":
                values, times = {key: list(column) for key, column in item["values"].items()}, list(item["time"])
            elif item["op"] == "append":
                for key, column in item["values"].items():
                    values[key].extend(column)
                times.extend(item["time"])
        return times, values


def builtin(name):
    path = BUILTIN / f"{name}.py"
    return Definition(digest=name, path=path)


def project(root: Path, files: dict, sites=()):
    root.mkdir(parents=True)
    for name, text in files.items():
        (root / name).write_text(textwrap.dedent(text).lstrip(), encoding="utf-8")
    return Definition(digest=str(root), path=root / "main.py", root=root, sites=tuple(sites))


def same(left, right):
    return all(a == b or a is None and b is None or isinstance(a, float) and isinstance(b, float)
               and math.isnan(a) and math.isnan(b) for a, b in zip(left, right, strict=True))


def run(coroutine):
    return asyncio.run(coroutine)


def test_interleaved_instances_equal_their_full_recompute(tmp_path, monkeypatch):
    monkeypatch.setattr("modules.chart.backend.pine.engine.POOL", 1)  # all six in one process

    async def main():
        engine = PineEngine(tmp_path / "logs")
        rng = random.Random(3)
        a_rows, b_rows = bars(3020, 1), bars(2520, 2)
        engine.set_series("a", SYMINFO, columns(a_rows[:3000]), None)
        engine.set_series("b", SYMINFO, columns(b_rows[:2500]), None)
        plan = [("a", "rsi", {}), ("a", "macd", {}), ("b", "rsi", {}), ("b", "ichimoku", {}), ("b", "boll", {}),
                ("a", "atr", {})]
        live = []
        for key, name, params in plan:
            collector = Collector()
            await engine.attach(key, builtin(name), params, collector)
            live.append((key, name, params, collector))
        for _, _, _, collector in live:
            await collector.wait(1)
        expected = 1
        for index in range(20):
            for key, rows, start in (("a", a_rows, 3000), ("b", b_rows, 2500)):
                for tick in ticks(rows[start + index], 4, rng):
                    engine.bar(key, tick, False)
                engine.bar(key, rows[start + index], True)
            expected += 5
        for _, _, _, collector in live:
            await collector.wait(expected)
        # A forming bar after the last closed one, then the reference: a fresh instance per key.
        forming = {"a": ticks(a_rows[-1], 1, rng)[0] | {"time": a_rows[-1]["time"] + 60_000},
                   "b": ticks(b_rows[-1], 1, rng)[0] | {"time": b_rows[-1]["time"] + 60_000}}
        for key in ("a", "b"):
            engine.bar(key, forming[key], False)
        for _, _, _, collector in live:
            await collector.wait(expected + 1)
        reference = PineEngine(tmp_path / "logs2")
        reference.set_series("a", SYMINFO, columns(a_rows), forming["a"])
        reference.set_series("b", SYMINFO, columns(b_rows), forming["b"])
        for key, name, params, collector in live:
            full = Collector()
            await reference.attach(key, builtin(name), params, full)
            await full.wait(2)
            times, values = collector.rows()
            full_times, full_values = full.rows()
            assert times == full_times, name
            assert values.keys() == full_values.keys(), name
            for output in values:
                assert same(values[output], full_values[output]), (name, output)
            stepped_forming = collector.items[-1]["values"]
            assert collector.items[-1]["op"] == "update" and full.items[-1]["op"] == "update"
            for output in stepped_forming:
                assert same(stepped_forming[output], full.items[-1]["values"][output]), (name, output)
        await engine.close()
        await reference.close()
    run(main())


def test_projects_keep_their_own_modules_and_state(tmp_path):
    main_source = '''
        """@pyne"""
        from pynecore.lib import close, plot, script
        import utils

        @script.indicator("P", overlay=False)
        def main():
            plot(utils.value(close), "value")
            plot(utils.count(), "calls")
    '''
    counting = "calls = 0\n\ndef count():\n    global calls\n    calls += 1\n    return calls\n"
    first = project(tmp_path / "one", {"main.py": main_source,
                                       "utils.py": "def value(x):\n    return x * 2\n" + counting})
    second = project(tmp_path / "two", {"main.py": main_source,
                                        "utils.py": "def value(x):\n    return x + 1000\n" + counting})

    async def main():
        engine = PineEngine(tmp_path / "logs")
        rows = bars(300, 5)
        engine.set_series("s", SYMINFO, columns(rows[:200]), None)
        collectors = [Collector() for _ in range(2)]
        await engine.attach("s", first, {}, collectors[0])
        await engine.attach("s", second, {}, collectors[1])
        for collector in collectors:
            await collector.wait(1)
        for row in rows[200:]:
            engine.bar("s", row, True)
        for collector in collectors:
            await collector.wait(101)
        closes = [row["close"] for row in rows]
        _, one = collectors[0].rows()
        _, two = collectors[1].rows()
        assert one["value"] == [close * 2 for close in closes]
        assert two["value"] == [close + 1000 for close in closes]
        assert one["calls"] == two["calls"] == [float(index + 1) for index in range(300)]
        await engine.close()
    run(main())


def test_two_instances_of_one_project_count_separately(tmp_path):
    counter = project(tmp_path / "count", {"main.py": '''
        """@pyne"""
        from pynecore.lib import input, plot, script
        import state

        @script.indicator("C", overlay=False)
        def main(step: int = input.int(1, "Step")):
            state.total += step
            plot(state.total, "total")
    ''', "state.py": "total = 0\n"})

    async def main():
        engine = PineEngine(tmp_path / "logs")
        engine.set_series("s", SYMINFO, columns(bars(50, 1)), None)
        ones, tens = Collector(), Collector()
        await engine.attach("s", counter, {"step": 1}, ones)
        await engine.attach("s", counter, {"step": 10}, tens)
        await ones.wait(1)
        await tens.wait(1)
        assert ones.rows()[1]["total"] == [float(index + 1) for index in range(50)]
        assert tens.rows()[1]["total"] == [float(10 * (index + 1)) for index in range(50)]
        await engine.close()
    run(main())


def test_projects_with_different_dependency_versions(tmp_path):
    sites = []
    for version in (1, 2):
        site = tmp_path / f"site{version}" / "talyxdep"
        site.mkdir(parents=True)
        (site / "__init__.py").write_text(f"VERSION = {version}\n", encoding="utf-8")
        sites.append(str(site.parent))
    source = '''
        """@pyne"""
        from pynecore.lib import plot, script
        import talyxdep

        @script.indicator("D", overlay=False)
        def main():
            plot(talyxdep.VERSION, "version")
    '''
    first = project(tmp_path / "p1", {"main.py": source}, sites=[sites[0]])
    second = project(tmp_path / "p2", {"main.py": source}, sites=[sites[1]])

    async def main():
        engine = PineEngine(tmp_path / "logs")
        engine.set_series("s", SYMINFO, columns(bars(10, 1)), None)
        one, two = Collector(), Collector()
        await engine.attach("s", first, {}, one)
        await engine.attach("s", second, {}, two)
        await one.wait(1)
        await two.wait(1)
        assert one.rows()[1]["version"] == [1.0] * 10
        assert two.rows()[1]["version"] == [2.0] * 10
        await engine.close()
    run(main())


def test_a_failing_script_leaves_the_others_untouched(tmp_path):
    boom = project(tmp_path / "boom", {"main.py": '''
        """@pyne"""
        from pynecore.lib import bar_index, close, plot, script

        @script.indicator("Boom", overlay=False)
        def main():
            if bar_index == 105:
                raise ValueError("boom at 105")
            plot(close, "close")
    '''})

    async def main():
        engine = PineEngine(tmp_path / "logs")
        rows = bars(120, 9)
        engine.set_series("s", SYMINFO, columns(rows[:100]), None)
        failing, rsi = Collector(), Collector()
        await engine.attach("s", boom, {}, failing)
        await engine.attach("s", builtin("rsi"), {}, rsi)
        await failing.wait(1)
        await rsi.wait(1)
        for row in rows[100:]:
            engine.bar("s", row, True)
        await rsi.wait(21)
        await failing.wait(7)
        assert failing.items[-1]["op"] == "error" and "boom at 105" in failing.items[-1]["message"]
        assert failing.items[-1]["line"] == 7
        reference = PineEngine(tmp_path / "logs2")
        reference.set_series("s", SYMINFO, columns(rows), None)
        full = Collector()
        await reference.attach("s", builtin("rsi"), {}, full)
        await full.wait(1)
        assert same(rsi.rows()[1]["rsi"], full.rows()[1]["rsi"])
        await engine.close()
        await reference.close()
    run(main())


def test_a_stopped_script_can_be_detached(tmp_path, monkeypatch):
    from modules.chart.backend.pine import engine as engine_module
    monkeypatch.setattr(engine_module, "STEP_SECONDS", 1.0)
    stuck = project(tmp_path / "stuck", {"main.py": '''
        """@pyne"""
        from pynecore.lib import bar_index, close, plot, script

        @script.indicator("Stuck", overlay=False)
        def main():
            while bar_index == 100:
                pass
            plot(close, "close")
    '''})

    async def main():
        engine = PineEngine(tmp_path / "logs")
        rows = bars(101, 3)
        engine.set_series("s", SYMINFO, columns(rows[:100]), None)
        listener = Collector()
        instance = await engine.attach("s", stuck, {}, listener)
        await listener.wait(1)
        engine.bar("s", rows[100], True)
        await listener.wait(2)
        assert "did not finish a bar in time" in listener.items[-1]["message"]
        engine.detach(instance, listener)
        await engine.close()
    run(main())


def test_bars_queued_behind_a_long_start_have_their_own_time(tmp_path, monkeypatch):
    """A bar's time limit runs from when the script gets to it: ticks that arrive while several
    long windows still compute (many charts switching at once) do not count as stuck."""
    from modules.chart.backend.pine import engine as engine_module
    monkeypatch.setattr(engine_module, "STEP_SECONDS", 1.0)
    slow = project(tmp_path / "slow", {"main.py": '''
        """@pyne"""
        from pynecore.lib import close, input, plot, script

        @script.indicator("Slow", overlay=False)
        def main(tag: int = input.int(1, "Tag")):
            total = 0
            for step in range(20000):
                total += step
            plot(close, "close")
    '''})

    async def main():
        engine = PineEngine(tmp_path / "logs")
        rows = bars(4_005, 13)
        engine.set_series("s", SYMINFO, columns(rows[:4_000]), None)
        listeners = [Collector() for _ in range(6)]
        for tag, listener in enumerate(listeners):
            await engine.attach("s", slow, {"tag": tag}, listener)
        rng = random.Random(5)
        for row in rows[4_000:]:  # a burst of ticks and closes while the windows compute
            for tick in ticks(row, 300, rng):
                engine.bar("s", tick, False)
            engine.bar("s", row, True)
        for listener in listeners:
            await listener.wait(1 + 5 * 301)
            assert listener.items[0]["op"] == "replace"
            assert [item for item in listener.items if item["op"] == "error"] == []
        await engine.close()
    run(main())


def test_a_restarted_instance_hears_only_from_its_new_run(tmp_path):
    """A new window restarts the instance while its earlier run is still replaying a longer
    one: nothing of the earlier run may land after the new replace (rows went missing)."""
    slow = project(tmp_path / "slow", {"main.py": '''
        """@pyne"""
        from pynecore.lib import close, plot, script

        @script.indicator("Slow", overlay=False)
        def main():
            total = 0
            for step in range(20000):
                total += step
            plot(close, "close")
    '''})

    async def main():
        engine = PineEngine(tmp_path / "logs")
        rows = bars(3_400, 12)
        engine.set_series("s", SYMINFO, columns(rows[:3_000]), None)
        listener = Collector()
        instance = await engine.attach("s", slow, {}, listener)
        await asyncio.sleep(0.5)  # the first run is replaying
        engine.set_series("s", SYMINFO, columns(rows[2_700:3_000]), None)
        for row in rows[3_000:3_005]:
            engine.bar("s", row, True)
        for _ in range(600):
            if instance.output and instance.output["time"][-1] == rows[3_004]["time"]:
                break
            await asyncio.sleep(0.1)
        await asyncio.sleep(4)  # long enough for the earlier run to finish its 3,000 bars
        assert instance.output["time"] == [row["time"] for row in rows[2_700:3_005]]
        assert [item["op"] for item in listener.items] == ["replace"] + ["append"] * 5
        await engine.close()
    run(main())


def test_a_consumer_attaching_while_the_instance_starts_again_gets_the_new_windows_output(tmp_path):
    """Market's whole window replaces its newest page while a chart computes on it: a reader
    attaching before the new run answers gets the new run's replace, not the newest page's."""
    async def main():
        engine = PineEngine(tmp_path / "logs")
        rows = bars(600, 13)
        engine.set_series("s", SYMINFO, columns(rows[500:]), None, whole=False)
        chart = Collector()
        await engine.attach("s", builtin("rsi"), {}, chart)
        await chart.wait(1)
        engine.set_series("s", SYMINFO, columns(rows), None)
        reader = Collector()
        await engine.attach("s", builtin("rsi"), {}, reader)
        await reader.wait(1)
        assert reader.items[0]["op"] == "replace" and len(reader.items[0]["time"]) == 600
        await engine.close()
    run(main())


def test_behind_chart_is_the_scripts_and_true_by_default(tmp_path):
    async def main():
        engine = PineEngine(tmp_path / "logs")
        engine.set_series("s", SYMINFO, columns(bars(50, 4)), None)
        front = project(tmp_path / "front", {"main.py": """
            \"\"\"@pyne\"\"\"
            from pynecore.lib import close, plot, script

            @script.indicator("Front", overlay=True, behind_chart=False)
            def main():
                plot(close, "close")
            """})
        collectors = {"front": Collector(), "default": Collector()}
        await engine.attach("s", front, {}, collectors["front"])
        await engine.attach("s", builtin("sma"), {}, collectors["default"])
        for collector in collectors.values():
            await collector.wait(1)
        assert collectors["front"].items[0]["behindChart"] is False
        assert collectors["default"].items[0]["behindChart"] is True
        await engine.close()
    run(main())


def test_no_scale_is_the_scripts_scale_none(tmp_path):
    async def main():
        engine = PineEngine(tmp_path / "logs")
        engine.set_series("s", SYMINFO, columns(bars(50, 4)), None)
        collectors = {"volume": Collector(), "sma": Collector()}
        for name, collector in collectors.items():
            await engine.attach("s", builtin(name), {}, collector)
            await collector.wait(1)
        assert collectors["volume"].items[0]["noScale"] is True
        assert collectors["sma"].items[0]["noScale"] is False
        await engine.close()
    run(main())


BARSTATE = """
    \"\"\"@pyne\"\"\"
    from pynecore.lib import bar_index, barstate, close, label, plot, script
    from pynecore.types import Persistent

    @script.indicator("States", overlay=True)
    def main():
        marker: Persistent[label] = None
        for name, value in (("islast", barstate.islast), ("lastconfirmed", barstate.islastconfirmedhistory),
                            ("confirmed", barstate.isconfirmed), ("realtime", barstate.isrealtime),
                            ("history", barstate.ishistory), ("new", barstate.isnew)):
            plot(1 if value else 0, name)
        if barstate.islast:
            if marker is not None:
                label.delete(marker)
            marker = label.new(bar_index, close, "last")
    """
STATES = ("islast", "lastconfirmed", "confirmed", "realtime", "history", "new")


def states(item, index):
    return tuple(item["values"][name][index] for name in STATES)


def test_barstate_on_a_closed_market_the_last_bar_is_islast_and_drawn_in_the_replace(tmp_path):
    """TradingView: islast on the dataset's last bar, which is the last historical bar when no
    real-time bar follows (islastconfirmedhistory too); the islast drawing is in the replace."""
    async def main():
        engine = PineEngine(tmp_path / "logs")
        rows = bars(60, 4)
        engine.set_series("s", SYMINFO, columns(rows[:50]), None)
        collector = Collector()
        await engine.attach("s", project(tmp_path / "states", {"main.py": BARSTATE}), {}, collector)
        await collector.wait(1)
        replace = collector.items[0]
        assert replace["op"] == "replace", replace
        assert states(replace, -2) == (0, 0, 1, 0, 1, 1)
        assert states(replace, -1) == (1, 1, 1, 0, 1, 1)
        assert [shape["x"] for shape in replace["drawings"]["labels"]] == [49]
        engine.bar("s", rows[50], False)  # the market opens: the real-time bar is the last
        await collector.wait(2)
        assert collector.items[1]["op"] == "update" and states(collector.items[1], -1) == (1, 0, 0, 1, 0, 1)
        changes = collector.items[1]["drawingChanges"]  # the label moved: only it, as a new one for the old
        assert list(changes) == ["labels"] and [shape["x"] for shape in changes["labels"]["set"]] == [50]
        assert changes["labels"]["drop"] == [replace["drawings"]["labels"][0]["id"]]
        await engine.close()
    run(main())


def test_barstate_on_an_open_market_the_real_time_bar_is_islast_and_drawn_in_the_replace(tmp_path):
    """TradingView: the dataset ends with the real-time bar, so its islast drawing is in the
    first paint; the bar before it is the last confirmed history bar."""
    async def main():
        engine = PineEngine(tmp_path / "logs")
        rows = bars(60, 4)
        engine.set_series("s", SYMINFO, columns(rows[:50]), rows[50])
        collector = Collector()
        await engine.attach("s", project(tmp_path / "states", {"main.py": BARSTATE}), {}, collector)
        await collector.wait(2)
        replace, update = collector.items[:2]
        assert replace["op"] == "replace" and len(replace["time"]) == 50, replace
        assert states(replace, -1) == (0, 1, 1, 0, 1, 1)
        assert [shape["x"] for shape in replace["drawings"]["labels"]] == [50]
        assert update["op"] == "update" and update["time"] == [rows[50]["time"]]
        assert states(update, -1) == (1, 0, 0, 1, 0, 1)
        engine.bar("s", {**rows[50], "close": rows[50]["close"] + 1}, False)  # a second tick: not new
        await collector.wait(3)
        assert states(collector.items[2], -1) == (1, 0, 0, 1, 0, 0)
        engine.bar("s", rows[50], True)
        await collector.wait(4)
        assert collector.items[3]["op"] == "append" and states(collector.items[3], -1) == (1, 0, 1, 1, 0, 0)
        await engine.close()
    run(main())


def test_every_builtin_runs(tmp_path):
    async def main():
        engine = PineEngine(tmp_path / "logs")
        rows = bars(401, 4)
        engine.set_series("s", SYMINFO, columns(rows[:400]), rows[400])  # live: the forming bar is the last
        engine.supply = lambda symbol, timeframe, start: (  # open interest, funding, intrabars
            asyncio.get_running_loop().call_soon(engine.set_secondary, (symbol, timeframe), columns(bars(400, 5)), None)
            or (lambda: None))
        collectors = {}
        for path in sorted(BUILTIN.glob("*.py")):
            collectors[path.stem] = Collector()
            await engine.attach("s", Definition(digest=path.stem, path=path), {}, collectors[path.stem])
        for name, collector in collectors.items():
            await collector.wait(1)
            item = collector.items[0]
            assert item["op"] == "replace", (name, item)
            if name.endswith("volume_profile"):  # boxes only; the open range is drawn on the last bar
                assert item.get("drawings", {}).get("boxes"), name
            else:
                assert any(value is not None for column in item["values"].values() for value in column), name
        # TradingView's Bollinger Bands: blue basis, red upper, green lower, a light blue fill between.
        meta = collectors["boll"].items[0]["meta"]
        assert [(entry["id"], entry["color"]) for entry in meta if entry["kind"] == "plot"] == [
            ("Basis", "#2962FFFF"), ("Upper", "#F23645FF"), ("Lower", "#089981FF")]
        assert [(entry["plot1"], entry["plot2"], entry["color"]) for entry in meta if entry["kind"] == "fill"] == [
            ("Upper", "Lower", "#2196F30D")]
        await engine.close()
    run(main())


def test_request_security_reads_other_symbols_and_timeframes_live(tmp_path):
    """request.security on another symbol (1m), a higher timeframe of it and of the chart's
    own symbol (5m), fed by Chart: stepped live values equal a fresh instance's over the same
    bars."""
    async def main():
        shift = -200_000  # onto the 5m grid
        chart_rows, other_rows = ([{**row, "time": row["time"] + shift, "closeTime": row["closeTime"] + shift}
                                   for row in bars(700, seed)] for seed in (21, 22))
        minutes = {"OKX:ETHUSDT": other_rows, "TEST:XUSDT": chart_rows}

        def five(rows, end):  # 5m bars from 1m rows[:end]; the last one is forming when end is mid-period
            out = []
            for start in range(0, end, 5):
                group = rows[start:min(start + 5, end)]
                out.append({"time": group[0]["time"], "closeTime": rows[start + 4]["closeTime"], "open": group[0]["open"],
                            "high": max(r["high"] for r in group), "low": min(r["low"] for r in group),
                            "close": group[-1]["close"], "volume": sum(r["volume"] for r in group)})
            return out

        def history_of(symbol, timeframe, end):
            return list(minutes[symbol][:end]) if timeframe == "1" else five(minutes[symbol], end - end % 5)

        history = 602  # live starts inside a 5m period
        supplied = {}

        def supply(symbol, timeframe, start):
            supplied[(symbol, timeframe)] = True
            asyncio.get_running_loop().call_soon(engine.set_secondary, (symbol, timeframe),
                                                 columns(history_of(symbol, timeframe, history)), None)
            return lambda: supplied.pop((symbol, timeframe))

        definition = project(tmp_path / "sec", {"main.py": """
            \"\"\"@pyne\"\"\"
            from pynecore.lib import close, plot, request, script, syminfo

            @script.indicator("Security")
            def main():
                plot(request.security("OKX:ETHUSDT", "1", close), "other")
                plot(request.security("OKX:ETHUSDT", "5", close), "other5")
                plot(request.security(syminfo.tickerid, "5", close), "own5")
                plot(close, "own")
            """})
        engine = PineEngine(tmp_path / "logs")
        engine.supply = supply
        engine.set_series("s", SYMINFO, columns(chart_rows[:history]), None)
        live = Collector()
        await engine.attach("s", definition, {}, live)
        await live.wait(1)
        assert live.items[0]["op"] == "replace", live.items[0]
        assert set(supplied) == {("OKX:ETHUSDT", "1"), ("OKX:ETHUSDT", "5"), ("TEST:XUSDT", "5")}
        for index in range(history, 650):
            engine.secondary_bar(("OKX:ETHUSDT", "1"), other_rows[index], True)
            for symbol in ("OKX:ETHUSDT", "TEST:XUSDT"):  # Market's 5m bar: closed, or forming so far
                engine.secondary_bar((symbol, "5"), five(minutes[symbol], index + 1)[-1], (index + 1) % 5 == 0)
            engine.bar("s", chart_rows[index], True)
            await live.wait(1 + index - history + 1)  # in step, as in real time: no later row is written yet
        times, values = live.rows()
        assert values["other"][-1] == other_rows[649]["close"]
        # Forming bars: the other symbol's developing close shows on the chart's forming bar.
        for close in (123.25, 124.5):
            engine.secondary_bar(("OKX:ETHUSDT", "1"), {**other_rows[650], "close": close}, False)
            engine.bar("s", {**chart_rows[650], "close": close + 1}, False)
            await live.wait(len(live.items) + 1)
            assert live.items[-1]["op"] == "update"
            assert live.items[-1]["values"]["other"] == [close] and live.items[-1]["values"]["own"] == [close + 1]

        fresh_engine = PineEngine(tmp_path / "logs2")
        fresh_engine.supply = lambda symbol, timeframe, start: (
            asyncio.get_running_loop().call_soon(fresh_engine.set_secondary, (symbol, timeframe),
                                                 columns(history_of(symbol, timeframe, 650)), None)
            or (lambda: None))
        fresh_engine.set_series("s", SYMINFO, columns(chart_rows[:650]), None)
        fresh = Collector()
        await fresh_engine.attach("s", definition, {}, fresh)
        await fresh.wait(1)
        fresh_times, fresh_values = fresh.rows()
        assert times == fresh_times
        for key in ("other", "own"):
            assert same(values[key][-50:], fresh_values[key][-50:]), key
        # A higher timeframe repaints in real time (TradingView's lookahead_off), the chart's own
        # symbol like any other: a bar that closed live shows the developing 5m bar (its close is
        # the 1m close by then), from the first live bar on; a fresh recompute shows the last
        # closed 5m bar; on the 5m bar's last minute both are its close.
        live_rows = range(history - 650, 0)
        assert same([values["own5"][i] for i in live_rows], [values["own"][i] for i in live_rows])
        assert same([values["other5"][i] for i in live_rows], [values["other"][i] for i in live_rows])
        closing = [offset for offset in live_rows if (650 + offset + 1) % 5 == 0]
        for key in ("other5", "own5"):
            assert same([values[key][i] for i in closing], [fresh_values[key][i] for i in closing]), key
            assert not same([values[key][i] for i in live_rows], [fresh_values[key][i] for i in live_rows])
        await engine.close()
        await fresh_engine.close()
    run(main())


def test_the_pool_spreads_instances_and_a_crashed_process_restarts_alone(tmp_path, monkeypatch):
    monkeypatch.setattr("modules.chart.backend.pine.engine.POOL", 2)

    async def main():
        engine = PineEngine(tmp_path / "logs")
        seen = []
        engine.health_listeners.append(lambda health: seen.append(health["state"]))
        await engine.warm()
        assert engine.health() == {"state": "ok", "reason": None,
                                   "processes": [{"state": "ok", "reason": None, "instances": 0}] * 2}
        rows = bars(300, 21)
        engine.set_series("a", SYMINFO, columns(rows[:200]), None)
        engine.set_series("b", SYMINFO, columns(rows[:200]), None)
        first, second = Collector(), Collector()
        one = await engine.attach("a", builtin("rsi"), {}, first)
        two = await engine.attach("b", builtin("rsi"), {}, second)
        assert {one.worker, two.worker} == {((), 0), ((), 1)}  # the least loaded process each time
        await first.wait(1)
        await second.wait(1)
        engine.workers[one.worker].process.kill()
        await first.wait(3)  # the error, then the replace from the new process
        assert [item["op"] for item in first.items] == ["replace", "error", "replace"]
        assert first.items[1]["code"] == "worker_restarted"
        assert "restarting" in seen and seen[-1] == "ok" and engine.health()["state"] == "ok"
        engine.bar("b", rows[200], True)
        await second.wait(2)
        assert [item["op"] for item in second.items] == ["replace", "append"]  # untouched
        engine.restart_pool()  # the health item's restart: every process anew
        await first.wait(5)
        await second.wait(4)
        assert [item["op"] for item in second.items][-2:] == ["error", "replace"]
        assert engine.health()["processes"] == [{"state": "ok", "reason": None, "instances": 1}] * 2
        await engine.close()
    run(main())


def test_a_new_window_delays_live_rows_elsewhere_by_a_slice(tmp_path, monkeypatch):
    """History holds the engine lock in slices and gives way to waiting real-time bars: another
    instance's live rows wait about a slice, not the whole history."""
    monkeypatch.setattr("modules.chart.backend.pine.engine.POOL", 1)  # both in one process

    async def main():
        engine = PineEngine(tmp_path / "logs")
        rows = bars(30_000, 22)
        engine.set_series("live", SYMINFO, columns(rows[:200]), None)
        engine.set_series("history", SYMINFO, columns(rows[:29_000]), None)
        live = Collector()
        await engine.attach("live", builtin("rsi"), {}, live)
        await live.wait(1)
        history = Collector()
        await engine.attach("history", builtin("macd"), {}, history)
        delays = []
        for row in rows[200:1_000]:
            sent = time.monotonic()
            engine.bar("live", row, True)
            await live.wait(len(live.items) + 1)
            delays.append(time.monotonic() - sent)
            if history.items:
                break
            await asyncio.sleep(0.02)
        assert len(delays) >= 5  # the rows arrived while history ran
        assert max(delays) < 0.3, delays
        await engine.close()
    run(main())


def test_a_plots_format_is_its_own_or_the_scripts(tmp_path):
    async def main():
        engine = PineEngine(tmp_path / "logs")
        engine.set_series("s", SYMINFO, columns(bars(50, 4)), None)
        mixed = project(tmp_path / "mixed", {"main.py": """
            \"\"\"@pyne\"\"\"
            from pynecore.lib import close, format, plot, script, volume

            @script.indicator("Mixed", format=format.volume, precision=4)
            def main():
                plot(volume, "volume")
                plot(close, "close", format=format.price, precision=1)
            """})
        collectors = {"mixed": Collector(), "obv": Collector()}
        await engine.attach("s", mixed, {}, collectors["mixed"])
        await engine.attach("s", builtin("obv"), {}, collectors["obv"])
        for collector in collectors.values():
            await collector.wait(1)
        assert {meta["id"]: meta["format"] for meta in collectors["mixed"].items[0]["meta"]} == {"volume": "volume", "close": "price"}
        assert {meta["id"]: meta["precision"] for meta in collectors["mixed"].items[0]["meta"]} == {"volume": 4, "close": 1}
        assert [meta["format"] for meta in collectors["obv"].items[0]["meta"]] == ["volume"]
        await engine.close()
    run(main())


def test_na_colours_are_sent_as_na_apart_from_colours_not_given(tmp_path):
    """Pine: an output or a bar coloured na draws nothing. PyneCore writes na and "no colour given" both as null; the
    worker sends na as "na", so null keeps meaning the meta's colour (rows from before the colour started to vary)."""
    async def main():
        engine = PineEngine(tmp_path / "logs")
        engine.set_series("s", SYMINFO, columns(bars(60, 4)), None)
        script = project(tmp_path / "na", {"main.py": """
            \"\"\"@pyne\"\"\"
            from pynecore.lib import bar_index, bgcolor, close, color, fill, na, plot, plotshape, script

            @script.indicator("Na", overlay=True)
            def main():
                plot(close, "hidden", color=na)
                plot(close, "gaps", color=color.red if bar_index < 10 or bar_index % 2 == 0 else na)
                top = plot(close + 1, "top")
                bottom = plot(close - 1, "bottom")
                fill(top, bottom, color=color.blue if bar_index % 3 else na)
                bgcolor(color.green if bar_index % 5 == 0 else na)
                plotshape(True, "marks", color=color.red if bar_index % 2 else na, textcolor=color.white)
            """})
        collector = Collector()
        await engine.attach("s", script, {}, collector)
        await collector.wait(1)
        item = collector.items[0]
        meta = {entry["id"]: entry for entry in item["meta"]}
        assert meta["hidden"]["color"] == "na" and not meta["hidden"].get("dynamic")
        assert meta["gaps"]["color"] == "#F23645FF" and meta["gaps"]["dynamic"]
        gaps = item["colors"]["gaps"]
        assert gaps[:11] == [None] * 11  # before the colour varied (the same red until bar 11): the meta's
        assert gaps[11:14] == ["na", "#F23645FF", "na"]
        assert "top" not in item["colors"]  # a plot naming no colour never varies
        # Bar 0 was na: the meta's colour is na, and so is the row before the colour varied.
        assert meta["fill#0"]["color"] == meta["marks"]["color"] == "na"
        assert item["colors"]["fill#0"][:4] == [None, "#2962FFFF", "#2962FFFF", "na"]
        assert item["colors"]["bgcolor#0"][:6] == ["#4CAF50FF", "na", "na", "na", "na", "#4CAF50FF"]
        assert item["colors"]["marks"][:3] == [None, ["#F23645FF", "#FFFFFFFF"], ["na", "#FFFFFFFF"]]
        await engine.close()
    run(main())


def test_inputs_are_described_in_the_form_a_param_takes(tmp_path):
    """A colour input's default is #RRGGBBAA and an enum's its member's value, options every member: what describe
    gives goes back as a param and the script reads the same input. input.time is a time; a colour param in short
    form reads as the long one."""
    async def main():
        engine = PineEngine(tmp_path / "logs")
        engine.set_series("s", SYMINFO, columns(bars(20, 4)), None)
        script = project(tmp_path / "inputs", {"main.py": """
            \"\"\"@pyne\"\"\"
            from enum import Enum
            from pynecore.lib import close, color, input, plot, script

            class Mode(Enum):
                FAST = "Fast"
                SLOW = "Slow"

            @script.indicator("Inputs", overlay=True)
            def main(tint: color.Color = input.color(color.rgb(255, 0, 0), "Tint"),
                     mode: Mode = input.enum(Mode.SLOW, "Mode"),
                     since: int = input.time(1700000000000, "Since")):
                plot(close, "close", color=tint)
                plot(1 if mode == Mode.FAST else 0, "fast")
            """})
        reply = await engine.describe(script)
        inputs = {entry["id"]: entry for entry in reply["ok"]["inputs"]}
        json.dumps(inputs)
        assert (inputs["tint"]["type"], inputs["tint"]["default"]) == ("color", "#FF0000FF")
        assert (inputs["mode"]["type"], inputs["mode"]["default"], inputs["mode"]["options"]) == ("enum", "Slow", ["Fast", "Slow"])
        assert (inputs["since"]["type"], inputs["since"]["default"]) == ("time", 1700000000000)
        collectors = {"default": Collector(), "chosen": Collector()}
        await engine.attach("s", script, {}, collectors["default"])
        # #RGB as an AI may write it reads as the settings would send it (PyneCore's Color read #0F0 as a number)
        await engine.attach("s", script, {"tint": "#0F0", "mode": "Fast"}, collectors["chosen"])
        for collector in collectors.values():
            await collector.wait(1)
        for name, color, fast in (("default", "#FF0000FF", 0.0), ("chosen", "#00FF00FF", 1.0)):
            item = collectors[name].items[0]
            assert next(entry["color"] for entry in item["meta"] if entry["id"] == "close") == color
            assert set(item["values"]["fast"]) == {fast}
        await engine.close()
    run(main())


def test_a_description_lists_only_its_own_scripts_inputs(tmp_path):
    """A script whose request.security reads history left its inputs in PyneCore's registry, and the next script
    described in that process showed them as its own (in the catalog and the settings)."""
    async def main():
        engine = PineEngine(tmp_path / "logs")
        first = project(tmp_path / "first", {"main.py": """
            \"\"\"@pyne\"\"\"
            from pynecore.lib import close, input, plot, request, script, syminfo

            @script.indicator("First")
            def main(length: int = input.int(5, "Length")):
                plot(request.security(syminfo.tickerid, "D", close[1]) + length, "daily")
            """})
        second = project(tmp_path / "second", {"main.py": """
            \"\"\"@pyne\"\"\"
            from pynecore.lib import close, plot, script

            @script.indicator("Second")
            def main():
                plot(close, "close")
            """})
        assert [item["id"] for item in (await engine.describe(first))["ok"]["inputs"]] == ["length"]
        assert (await engine.describe(second))["ok"]["inputs"] == []
        await engine.close()
    run(main())
