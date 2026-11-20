"""The stream outbox drops stale forming updates without ever reordering past bars."""
import asyncio
import json
import random

from modules.chart.backend.streams import FRAME_BUDGET, Outbox


def drain(outbox: Outbox, count: int) -> list[dict]:
    async def read():
        items = []
        async for item in outbox:
            items.append(item)
            if len(items) == count:
                return items
    return asyncio.run(asyncio.wait_for(read(), 5))


def bar(time: int, close: float = 1.0) -> dict:
    return {"type": "update", "bar": {"time": time, "close": close}}


def row(kind: str, time: int, name: str = "rsi") -> dict:
    ref = {"instanceId": name} if kind == "indicator" else {"comparisonId": name}
    return {"type": kind, "op": "update", **ref, "time": [time]}


def test_coalescing_never_moves_an_update_ahead_of_earlier_bars():
    outbox = Outbox()
    outbox.put(bar(1))
    outbox.put(row("indicator", 1))
    outbox.put({"type": "bars", "op": "append", "time": [1]})
    outbox.put(bar(2))
    outbox.put(row("indicator", 2))
    got = drain(outbox, 5)
    assert [(item["type"], item["time"] if "time" in item else item["bar"]["time"]) for item in got] == \
        [("update", 1), ("indicator", [1]), ("bars", [1]), ("update", 2), ("indicator", [2])]


def test_a_newer_tick_of_the_same_bar_keeps_its_place_ahead_of_the_indicator_row():
    outbox = Outbox()
    outbox.put(bar(2, close=1.0))
    outbox.put(row("indicator", 2))
    outbox.put(bar(2, close=1.5))
    got = drain(outbox, 2)
    assert got[0] == bar(2, close=1.5)
    assert got[1] == row("indicator", 2)


def test_an_update_after_the_last_send_still_goes_out():
    outbox = Outbox()
    outbox.put(bar(1))
    assert drain(outbox, 1)[0]["bar"]["time"] == 1
    outbox.put(bar(2))
    assert drain(outbox, 1)[0]["bar"]["time"] == 2


def simulate(seed: int) -> tuple[list[tuple[int, dict]], list[tuple[int, dict]]]:
    """A chart's producer order: rows for the forming time only after a bar update for it
    since the last bars item; an append closes the forming bar. Reads interleave at random."""
    rng = random.Random(seed)
    outbox, put, sent = Outbox(), [], []
    forming, ticked = 1, False

    def emit(item):
        item = {**item, "seq": len(put)}
        put.append(item)
        outbox.put(item)

    async def run():
        nonlocal forming, ticked
        reader = outbox.__aiter__()
        for _ in range(rng.randint(5, 60)):
            choice = rng.random()
            if choice < 0.35:
                emit(bar(forming, close=rng.random()))
                ticked = True
            elif choice < 0.65 and ticked:
                kind = rng.choice(["indicator", "comparison"])
                emit(row(kind, forming, rng.choice(["a", "b"])))
            elif choice < 0.75:
                emit({"type": "bars", "op": "append", "time": [forming]})
                forming, ticked = forming + 1, False
            elif choice < 0.8:
                emit({"type": "bars", "op": rng.choice(["replace", "splice"]), "time": list(range(1, forming))})
                ticked = False
            elif choice < 0.85:
                emit({"type": "indicator", "op": "append", "instanceId": "a", "time": [forming - 1]})
            else:
                for _ in range(rng.randint(1, 4)):
                    if not outbox.items:
                        break
                    sent.append(await reader.__anext__())
        while outbox.items:
            sent.append(await reader.__anext__())

    asyncio.run(run())
    return put, sent


def test_random_orderings_keep_bars_ahead_of_their_rows_and_updates_behind_earlier_items():
    for seed in range(400):
        put, sent = simulate(seed)
        bars_out: set[int] = set()
        seen: set[int] = set()
        for item in sent:
            # every bars item and other non-update item put before it has gone out
            for earlier in put[:item["seq"]]:
                if earlier.get("op") != "update" and earlier["type"] != "update":
                    assert earlier["seq"] in seen, f"seed {seed}: {item} went out ahead of {earlier}"
            seen.add(item["seq"])
            if item["type"] == "bars":
                bars_out = set(item["time"])
            elif item["type"] == "update":
                bars_out.add(item["bar"]["time"])
            elif item["op"] == "update":
                assert item["time"][0] in bars_out, f"seed {seed}: {item} before its bar"
        # every bars item went out, and every key's last put went out last
        assert [item["seq"] for item in put if item["type"] == "bars"] == \
            [item["seq"] for item in sent if item["type"] == "bars"], f"seed {seed}"
        for kind in ("update", "indicator", "comparison"):
            updates = [item for item in put if item["type"] == kind and item.get("op", "update") == "update"]
            for name in {item.get("instanceId") or item.get("comparisonId") for item in updates}:
                mine = [item for item in updates if (item.get("instanceId") or item.get("comparisonId")) == name]
                got = [item for item in sent if item in mine]
                assert got[-1] is mine[-1], f"seed {seed}: the last {kind} {name} was lost"


def test_replaces_at_the_window_cap_go_out_in_frames_the_bridge_carries():
    """30,000 bars, and an indicator with six plots and a color channel over them: each piece
    stays under the frame budget, the first is a replace, the rest append, and together they
    are the original item."""
    count = 30_000
    times = [1_700_000_000_000 + index * 60_000 for index in range(count)]
    price = [70_000.123456 + index * 0.37 for index in range(count)]
    bars_item = {"type": "bars", "op": "replace", "replay": False, "time": times,
                 "closeTime": [time + 60_000 for time in times], "open": price, "high": price, "low": price,
                 "close": price, "volume": [12345.6789] * count, "priceFormat": {"precision": 1, "minMove": 0.1},
                 "hasMoreBefore": True, "limitReached": True}
    indicator_item = {"type": "indicator", "instanceId": "bb", "op": "replace", "replay": False, "time": times,
                      "meta": [{"id": f"p{index}", "kind": "plot"} for index in range(6)],
                      "values": {f"p{index}": [value * 1.0001 for value in price] for index in range(6)},
                      "colors": {"p0": ["#2962FF"] * count}}
    for item in (bars_item, indicator_item):
        outbox = Outbox()
        outbox.put(item)
        pieces = drain(outbox, len(outbox.items))
        assert len(pieces) > 1 and all(len(json.dumps(piece)) <= FRAME_BUDGET for piece in pieces)
        assert [piece["op"] for piece in pieces] == ["replace"] + ["append"] * (len(pieces) - 1)
        joined = dict(pieces[0])
        if item["type"] == "bars":
            assert joined.pop("windowRows") == 30_000
        for piece in pieces[1:]:
            assert piece["type"] == item["type"] and set(piece) <= set(item)
            for key, value in piece.items():
                if isinstance(value, list):
                    joined[key] = joined[key] + value
                elif key in ("values", "colors"):
                    joined[key] = {name: joined[key][name] + column for name, column in value.items()}
        assert joined == item
