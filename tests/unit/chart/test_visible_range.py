"""The chart's visible range in Pine (chart.left_visible_bar_time, chart.right_visible_bar_time), the
built-in Visible Range Volume Profile, and lower timeframes of raw price fields read in the worker
itself (round 2 D)."""
import asyncio
import math

import pytest
from talyx_sdk import ContractError

from modules.chart.backend.app import ChartApp
from modules.chart.backend.pine.engine import PineEngine, pool_size, view_index
from modules.chart.backend.pine.indicators import Catalog, Pins
from modules.chart.backend.pine.visible import VIEW
from modules.chart.backend.wire import ChartCreate, VisibleRange
from tests.unit.chart.fake_market import BTC, FakeMarket
from tests.unit.chart.test_builtin_volume import minutes_and_fives
from tests.unit.chart.test_chart_flow import Reader, commit
from tests.unit.chart.test_pine_engine import (
    SYMINFO,
    Collector,
    bars,
    builtin,
    columns,
    project,
    run,
)

VISIBLE = """
    \"\"\"@pyne\"\"\"
    from pynecore.lib import chart, last_bar_index, last_bar_time, plot, script, time

    @script.indicator("Visible")
    def main():
        plot(chart.left_visible_bar_time, "left")
        plot(chart.right_visible_bar_time, "right")
        plot(1 if chart.left_visible_bar_time <= time <= chart.right_visible_bar_time else 0, "inside")
        plot(last_bar_index, "last_index")
        plot(last_bar_time, "last_time")
    """


def test_the_range_is_the_charts_per_instance_and_newest_bars_follow_a_new_bar_without_a_new_run(tmp_path):
    async def main():
        engine = PineEngine(tmp_path / "logs")
        rows = bars(101, 1)
        times = [row["time"] for row in rows]
        engine.set_series("s", SYMINFO, columns(rows[:100]), None)
        definition = project(tmp_path / "visible", {"main.py": VISIBLE})
        fixed, newest, plain, again = Collector(), Collector(), Collector(), Collector()
        on_fixed = await engine.attach("s", definition, {VIEW: {"left": times[10], "right": times[50]}}, fixed)
        on_newest = await engine.attach("s", definition, {VIEW: {"bars": 5}}, newest)
        without = await engine.attach("s", definition, {}, plain)
        shared = await engine.attach("s", definition, {VIEW: {"left": times[10], "right": times[50]}}, again)
        for collector in (fixed, newest, plain, again):
            await collector.wait(1)
            assert collector.items[0]["op"] == "replace", collector.items[0]

        # Each range is an instance of its own, in the visible-range process; the same range is shared.
        assert shared is on_fixed and on_newest is not on_fixed
        assert on_fixed.worker == on_newest.worker == ((), view_index(()))
        assert without.worker[1] < pool_size(())

        values = fixed.items[0]["values"]
        assert set(values["left"]) == {times[10]} and set(values["right"]) == {times[50]}
        assert [index for index, inside in enumerate(values["inside"]) if inside] == list(range(10, 51))
        # Pine's last bar on every historical bar: the dataset's (PyneCore's live run reported each bar's own).
        assert set(values["last_index"]) == {99} and set(values["last_time"]) == {times[99]}
        values = newest.items[0]["values"]
        assert (values["left"][-1], values["right"][-1]) == (times[95], times[99])
        assert [index for index, inside in enumerate(values["inside"]) if inside] == list(range(95, 100))

        # A new bar: the newest five move with it, with no new run; two bar times stay where they are.
        engine.bar("s", rows[100], True)
        await newest.wait(2)
        await fixed.wait(2)
        assert [item["op"] for item in newest.items] == ["replace", "append"]
        assert (newest.items[1]["values"]["left"], newest.items[1]["values"]["right"]) == ([times[96]], [times[100]])
        assert newest.items[1]["values"]["inside"] == [1]
        assert (fixed.items[1]["values"]["left"], fixed.items[1]["values"]["inside"]) == ([times[10]], [0])

        # With the market open the real-time bar is the dataset's last.
        engine.set_series("open", SYMINFO, columns(rows[:100]), rows[100])
        forming = Collector()
        await engine.attach("open", definition, {VIEW: {"bars": 3}}, forming)
        await forming.wait(1)
        values = forming.items[0]["values"]
        assert set(values["last_index"]) == {100} and set(values["last_time"]) == {times[100]}
        assert [index for index, inside in enumerate(values["inside"]) if inside] == [98, 99]  # and the forming bar
        await engine.close()
    run(main())


COUNTED = """
    \"\"\"@pyne\"\"\"
    from pynecore.lib import bar_index, last_bar_index, plot, script

    @script.indicator("Counted", calc_bars_count=10)
    def main():
        plot(bar_index, "index")
        plot(last_bar_index, "last_index")
    """


def test_calc_bars_count_calculates_the_last_bars_only(tmp_path):
    """With the dataset's last bar known, Pine's calc_bars_count holds: bar_index starts at 0 on the
    first calculated bar and last_bar_index is N - 1 (PyneCore's measured TradingView semantics)."""
    async def main():
        engine = PineEngine(tmp_path / "logs")
        rows = bars(50, 3)
        engine.set_series("s", SYMINFO, columns(rows), None)
        collector = Collector()
        await engine.attach("s", project(tmp_path / "counted", {"main.py": COUNTED}), {}, collector)
        await collector.wait(1)
        item = collector.items[0]
        assert item["time"] == [row["time"] for row in rows[-10:]]
        assert item["values"]["index"] == list(range(10)) and set(item["values"]["last_index"]) == {9}
        await engine.close()
    run(main())


def shapes(item):
    return {kind: sorted((shape["left"], shape["right"], shape["top"], shape["bottom"]) if kind == "boxes" else
                         (shape["x1"], shape["x2"], shape["y1"], shape["y2"]) for shape in item["drawings"][kind])
            for kind in ("boxes", "lines")}


def same_shapes(left, right):
    """Equal up to the numpy sums' last bits: a box end may land a millisecond apart."""
    for kind in ("boxes", "lines"):
        assert len(left[kind]) == len(right[kind]), kind
        for one, other in zip(left[kind], right[kind], strict=True):
            assert all(math.isclose(a, b, rel_tol=1e-12, abs_tol=1) for a, b in zip(one, other, strict=True)), (one, other)


def test_the_visible_range_volume_profile_is_fixed_range_volume_profiles_over_the_range(tmp_path):
    async def main():
        minute, five = minutes_and_fives()
        engine = PineEngine(tmp_path / "logs")
        engine.supply = lambda symbol, timeframe, start: (
            asyncio.get_running_loop().call_soon(engine.set_secondary, (symbol, timeframe), columns(minute), None)
            or (lambda: None))
        engine.set_series("s", {**SYMINFO, "period": "5"}, columns(five[:-1]), None)

        async def drawn(name, params):
            collector = Collector()
            await engine.attach("s", builtin(name), params, collector)
            await collector.wait(1)
            assert collector.items[0]["op"] == "replace", collector.items[0]
            return collector

        times = [bar["time"] for bar in five]
        ranged = await drawn("visible_range_volume_profile",
                             {VIEW: {"left": times[20], "right": times[59]}, "placement": "Left"})
        fixed = await drawn("fixed_range_volume_profile", {"start_time": times[20], "end_time": times[60]})
        same_shapes(shapes(ranged.items[0]), shapes(fixed.items[0]))
        assert shapes(ranged.items[0])["lines"][0][:2] == (times[20], five[59]["closeTime"])
        # The default placement (TradingView's): the same rows against the range's right edge.
        right = shapes((await drawn("visible_range_volume_profile",
                                    {VIEW: {"left": times[20], "right": times[59]}})).items[0])
        end = five[59]["closeTime"]
        widths = sorted(round(box[1] - box[0]) for box in shapes(ranged.items[0])["boxes"])
        assert sorted(round(box[1] - box[0]) for box in right["boxes"]) == widths
        assert max(box[1] for box in right["boxes"]) == end and all(box[0] >= times[20] for box in right["boxes"])

        newest = await drawn("visible_range_volume_profile", {VIEW: {"bars": 40}, "placement": "Left"})
        following = await drawn("fixed_range_volume_profile", {"start_time": times[-41]})
        same_shapes(shapes(newest.items[0]), shapes(following.items[0]))

        # A new bar moves the newest 40 on by one, without a new run.
        engine.bar("s", five[-1], True)
        await newest.wait(2)
        assert newest.items[1]["op"] == "append"
        moved = await drawn("fixed_range_volume_profile", {"start_time": times[-40]})
        kept = {family: {item["id"]: item for item in newest.items[0]["drawings"][family]}
                for family in ("boxes", "lines")}
        for family, change in newest.items[1]["drawingChanges"].items():
            kept[family].update((item["id"], item) for item in change["set"])
            for key in change["drop"]:
                del kept[family][key]
        same_shapes(shapes({"drawings": {family: list(items.values()) for family, items in kept.items()}}),
                    shapes(moved.items[0]))
        await engine.close()
    run(main())


INTRABARS = """
    \"\"\"@pyne\"\"\"
    from pynecore.lib import array, hl2, na, plot, request, script, syminfo, volume

    @script.indicator("Intrabars")
    def main():
        # The first two are raw fields (read in the worker), the last two are not (a security child runs them).
        volumes, middles = request.security_lower_tf(syminfo.tickerid, "1", (volume, hl2))
        computed = request.security_lower_tf(syminfo.tickerid, "1", volume * 1.0)
        middles_computed = request.security_lower_tf(syminfo.tickerid, "1", hl2 * 1.0)
        plot(array.size(volumes), "count")
        plot(array.sum(volumes) if array.size(volumes) > 0 else na, "volume")
        plot(array.sum(middles) if array.size(middles) > 0 else na, "middle")
        plot(array.size(computed), "count_child")
        plot(array.sum(computed) if array.size(computed) > 0 else na, "volume_child")
        plot(array.sum(middles_computed) if array.size(middles_computed) > 0 else na, "middle_child")
    """


def test_raw_price_intrabars_read_in_the_worker_equal_a_security_childs_history_and_live(tmp_path):
    async def main():
        minute, five = minutes_and_fives(600)
        history = 100
        engine = PineEngine(tmp_path / "logs")
        engine.supply = lambda symbol, timeframe, start: (
            asyncio.get_running_loop().call_soon(engine.set_secondary, (symbol, timeframe),
                                                 columns(minute[:history * 5]), None) or (lambda: None))
        engine.set_series("s", {**SYMINFO, "period": "5"}, columns(five[:history]), None)
        collector = Collector()
        await engine.attach("s", project(tmp_path / "intrabars", {"main.py": INTRABARS}), {}, collector)
        await collector.wait(1)
        own = (f"{SYMINFO['prefix']}:{SYMINFO['ticker']}", "1")
        for index in range(history, history + 3):
            for row in minute[index * 5:index * 5 + 5]:
                engine.secondary_bar(own, {**row, "close": row["open"]}, False)  # the intrabar forming
                engine.secondary_bar(own, row, True)
                count = len(collector.items)
                engine.bar("s", {**five[index], "close": row["close"], "closeTime": five[index]["closeTime"]}, False)
                await collector.wait(count + 1)
            count = len(collector.items)
            engine.bar("s", five[index], True)
            await collector.wait(count + 1)
        rows = []
        for item in collector.items:
            assert item["op"] != "error", item
            rows.extend(zip(*(item["values"][key] for key in ("count", "volume", "middle", "count_child",
                                                               "volume_child", "middle_child")), strict=True))
        for count, total, middle, count_child, total_child, middle_child in rows:
            assert count == count_child
            assert total == total_child or total is None and total_child is None
            assert middle == pytest.approx(middle_child) if middle is not None else middle_child is None
        assert collector.items[0]["values"]["count"][-1] == 5  # a closed chart bar: its five intrabars
        await engine.close()
    run(main())


def test_scripts_reading_the_range_are_for_charts_only(tmp_path):
    engine = PineEngine(tmp_path / "logs")
    catalog = Catalog(engine, tmp_path / "indicators")
    assert catalog.reads_view(catalog.definition({"source": "builtin", "id": "visible_range_volume_profile"}))
    assert not catalog.reads_view(catalog.definition({"source": "builtin", "id": "fixed_range_volume_profile"}))
    (tmp_path / "indicators" / "project").mkdir()
    (tmp_path / "indicators" / "project" / "main.py").write_text("from edge import left\n", encoding="utf-8")
    (tmp_path / "indicators" / "project" / "edge.py").write_text(
        "def left(chart):\n    return chart.left_visible_bar_time\n", encoding="utf-8")
    assert catalog.reads_view(catalog.definition({"source": "user", "id": "project"}))
    pins = Pins(catalog, tmp_path / "pins")
    with pytest.raises(ContractError) as refused:
        pins.pin({"source": "builtin", "id": "visible_range_volume_profile"})
    assert refused.value.code == "unsupported"
    assert pins.pin({"source": "builtin", "id": "fixed_range_volume_profile"})


def test_a_chart_runs_its_range_and_a_new_range_replaces_the_run(tmp_path):
    async def main():
        market = FakeMarket()
        (tmp_path / "indicators").mkdir(exist_ok=True)
        (tmp_path / "indicators" / "visible.py").write_text(
            "\n".join(line[4:] for line in VISIBLE.strip("\n").splitlines()) + "\n", encoding="utf-8")
        app = ChartApp(market, tmp_path / "data", tmp_path / "indicators")
        rows = bars(300, 2)
        times = [row["time"] for row in rows]
        readers = []
        for _ in range(2):
            state = await app.create_chart("w1", ChartCreate(instrumentId=BTC["instrumentId"], timeframe="1m"))
            reader = Reader(await app.chart_stream("w1", state["chartId"]))
            await reader.until(lambda item: item["type"] == "document")
            readers.append((state["chartId"], reader))
        session = await market.session(BTC["instrumentId"])
        session.replace(rows)
        for chart_id, reader in readers:
            await reader.until(lambda item: item["type"] == "bars")
            await app.commit("w1", commit(chart_id, 1, {"type": "indicator.add", "value": {
                "id": "v", "definition": {"source": "user", "id": "visible.py"}, "paneId": "pane-v"}}))
            first = await reader.until(lambda item: item["type"] == "indicator" and item["instanceId"] == "v" and item["op"] == "replace")
            assert first["values"]["left"][-1] == times[-200]  # until the chart says, its newest 200 bars

        (one, first), (other, second) = readers
        await app.set_view("w1", VisibleRange(chartId=one, left=times[10], right=times[20]))
        await app.set_view("w1", VisibleRange(chartId=other, bars=30))
        item = await first.until(lambda item: item["type"] == "indicator" and item["instanceId"] == "v" and item["op"] == "replace")
        assert (item["values"]["left"][-1], item["values"]["right"][-1]) == (times[10], times[20])
        item = await second.until(lambda item: item["type"] == "indicator" and item["instanceId"] == "v" and item["op"] == "replace")
        assert (item["values"]["left"][-1], item["values"]["right"][-1]) == (times[-30], times[-1])
        # Dragging: ranges in quick succession end on the newest; the older runs are gone.
        for left in range(30, 60, 5):
            await app.set_view("w1", VisibleRange(chartId=one, left=times[left], right=times[left + 10]))
        await first.until(lambda item: item["type"] == "indicator" and item["instanceId"] == "v" and item["op"] == "replace"
                          and item["values"]["left"][-1] == times[55])
        engine = app.engine
        assert len([instance for instance in engine.instances.values() if instance.key and VIEW in instance.params]) == 2
        with pytest.raises(ContractError):
            await app.set_view("w1", VisibleRange(chartId=one, left=times[20], right=times[10]))
        await app.close()
    asyncio.run(main())


COMPUTED = """
    \"\"\"@pyne\"\"\"
    from pynecore.lib import array, na, plot, request, script, syminfo, volume

    @script.indicator("Computed intrabars")
    def main():
        volumes = request.security_lower_tf(syminfo.tickerid, "1", volume * 2.0)  # not raw: a security child runs it
        plot(array.sum(volumes) if array.size(volumes) > 0 else na, "volume")
    """


def test_chart_bars_before_the_intrabars_begin_are_empty_and_not_logged(tmp_path):
    """Market's intrabar window often starts well after the chart's first bar: those chart bars have no
    intrabars, which is no gap to report (PyneCore logged a line for each of them, tens of thousands a run)."""
    async def main():
        minute, five = minutes_and_fives(1000)
        engine = PineEngine(tmp_path / "logs")
        engine.supply = lambda symbol, timeframe, start: (
            asyncio.get_running_loop().call_soon(engine.set_secondary, (symbol, timeframe), columns(minute[500:]),
                                                 None) or (lambda: None))
        engine.set_series("s", {**SYMINFO, "period": "5"}, columns(five), None)
        collector = Collector()
        await engine.attach("s", project(tmp_path / "computed", {"main.py": COMPUTED}), {}, collector)
        await collector.wait(1)
        values = collector.items[0]["values"]["volume"]
        assert values[:100] == [None] * 100
        assert values[100:] == pytest.approx([2 * sum(row["volume"] for row in minute[index * 5:index * 5 + 5])
                                              for index in range(100, 200)])
        await engine.close()
        logs = "".join(path.read_text(encoding="utf-8", errors="replace") for path in (tmp_path / "logs").glob("*.log"))
        assert "missing its final intrabar" not in logs
    run(main())
