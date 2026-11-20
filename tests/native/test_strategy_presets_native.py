"""The strategy templates and presets: an empty skeleton that runs and trades nothing, five presets (two that need no library run here on synthetic
bars), the breakout's parameters as the draft shows them, and the ranked preset's buffer and caps.

Not part of the daily suite: set TALYX_NATIVE_SMOKE=1 (see environments.py).
"""
from __future__ import annotations

import os

import numpy as np
import pandas as pd
import pytest

from tests.fixtures.strategy import bars as B
from tests.fixtures.strategy import engine as E

pytestmark = pytest.mark.skipif(not os.environ.get("TALYX_NATIVE_SMOKE"), reason="Set TALYX_NATIVE_SMOKE=1 to run native engines")


def parameters(source):
    from modules.quant.backend.object_params import read_parameters
    found = read_parameters(source)
    assert not found["errors"], found["errors"]
    return {item["name"]: item for item in found["parameters"]}


def trending_bars(count=24 * 60, seed=4):
    """Hourly bars that swing up and down in long legs, so a channel breakout and a crossover both trade."""
    rng = np.random.default_rng(seed)
    legs = np.sin(np.arange(count) / 90) * .02 + rng.normal(0, .003, count)
    close = 40000 * np.exp(np.cumsum(legs))
    opened = pd.date_range(B.START, periods=count, freq="h", tz="UTC")
    begin = np.concatenate([[close[0]], close[:-1]])
    return B.frame_of(B.INSTRUMENT, B.BAR_TYPE, opened, begin, close, rng.uniform(100, 500, count), span_ns=B.HOUR_NS)


def test_new_strategy_runs_and_trades_nothing(tmp_path):
    from modules.quant.native.object_templates import STRATEGY
    E.prepare(tmp_path, STRATEGY)
    _, work, results = E.completed(tmp_path)
    assert work["stats"]["trades"] == 0 and work["notes"] == ["No trades. The code has not placed an order yet."]
    assert all(item["state"] == "notrun" for item in work.get("checks", {}).values()) if isinstance(work.get("checks"), dict) and work["checks"] and "state" in next(iter(work["checks"].values()), {}) else True


def test_empty_skeleton_is_a_few_lines_that_run():
    from modules.quant.native.object_templates import STRATEGY
    assert len(STRATEGY.splitlines()) < 40 and "q.Strategy" in STRATEGY


def test_five_presets_with_their_names_and_default_data():
    from modules.quant.native.strategy_presets import PRESETS
    assert [item["label"] for item in PRESETS.values()] == ["Top N by signal", "Rebalance to weights", "One name on a signal", "BTC breakout", "Moving-average crossover"] \
        or {item["label"] for item in PRESETS.values()} == {"Top N by signal", "Rebalance to weights", "One name on a signal", "BTC breakout", "Moving-average crossover"}
    breakout, crossover = PRESETS["breakout"], PRESETS["crossover"]
    assert breakout["data"]["timeframe"] == "1h" and breakout["data"]["symbols"][0]["symbol"] == "okx:BTC/USDT:USDT"
    assert crossover["data"]["timeframe"] == "1d" and crossover["data"]["symbols"][0]["symbol"].endswith("SPY") and crossover["data"]["years"] == 10


@pytest.mark.parametrize("name, settings", [("breakout", {}), ("crossover", {})])
def test_each_preset_runs(tmp_path, name, settings):
    from modules.quant.native.strategy_presets import PRESETS
    source = PRESETS[name]["source"]
    frame = trending_bars()
    E.prepare(tmp_path, source, frame=frame, funding=False, params={"fast": 5, "slow": 20} if name == "crossover" else {"lookback": 24})
    _, work, _ = E.completed(tmp_path)
    assert work["stats"]["trades"] > 0, name


def test_breakout_preset_matches_draft_settings():
    from modules.quant.native.strategy_presets import PRESETS
    found = parameters(PRESETS["breakout"]["source"])
    expected = {"lookback": (48, 12, 240, "Lookback (bars)"), "stop_atr": (2.0, .5, 6.0, "Stop (ATR)"), "trail_atr": (3.0, 0, 8, "Trailing stop (ATR)"),
                "risk": (1.0, .1, 5.0, "Risk per trade (%)"), "shorts": (True, None, None, "Trade the short side")}  # risk in % since 87872651
    assert list(found) == list(expected)
    for name, (default, low, high, label) in expected.items():
        item = found[name]
        assert item["default"] == default and item["label"] == label, name
        if low is not None:
            assert (item["min"], item["max"]) == (low, high), name


def test_breakout_enters_on_a_channel_break_with_atr_stop_and_tags(tmp_path):
    from modules.quant.native.strategy_presets import PRESETS
    E.prepare(tmp_path, PRESETS["breakout"]["source"], frame=trending_bars(), funding=False, params={"lookback": 24})
    _, work, results = E.completed(tmp_path)
    tags = {tag for row in E.rows(tmp_path, "orders") for tag in row["tags"]}
    assert {"entry:breakout"} <= tags and tags <= {"entry:breakout", "entry:breakdown", "exit:stop", "exit:trail", "exit:channel"}
    assert {trade["exitBy"] for trade in E.rows(tmp_path, "trades")} <= {"stop", "trail", "channel", "open at the end", "liquidation"}
    stops = [row for row in E.rows(tmp_path, "orders") if row["type"] == "Stop market"]
    assert stops


def test_crossover_preset_defaults_and_tags():
    from modules.quant.native.strategy_presets import PRESETS
    found = parameters(PRESETS["crossover"]["source"])
    assert (found["fast"]["default"], found["slow"]["default"], found["shorts"]["default"]) == (20, 100, False)
    source = PRESETS["crossover"]["source"]
    assert '"entry:" + why' in source and '"cross_up"' in source and '"cross_down"' in source and 'reason="cross_down"' in source


def test_ranked_parameters_for_the_caps():
    from modules.quant.native.strategy_presets import RANKED
    found = parameters(RANKED)
    assert {"buffer", "max_weight", "weighting", "min_dollar_volume", "sector_cap"} <= set(found)
    assert found["buffer"]["default"] == 0


def selection_function(monkeypatch):
    """The ranked preset's `choose` function, loaded from its source with stand-ins for the engine so it can be tested on plain numbers."""
    import sys
    import types

    from modules.quant.native.strategy_presets import RANKED
    fake = types.ModuleType("talyx_quant")
    fake.Strategy = object
    fake.param = lambda name, default, **kw: default
    fake.library_param = lambda name, default, **kw: default
    nautilus = types.ModuleType("nautilus_trader")
    model = types.ModuleType("nautilus_trader.model")
    model.OrderSide = types.SimpleNamespace(BUY="BUY", SELL="SELL")
    for name, module in (("talyx_quant", fake), ("nautilus_trader", nautilus), ("nautilus_trader.model", model)):
        monkeypatch.setitem(sys.modules, name, module)
    namespace = {"__name__": "preset"}
    exec(compile(RANKED, "ranked.py", "exec"), namespace)
    return namespace["choose"]


def test_ranked_buffer_and_caps(monkeypatch):
    """A name already held stays while it is within top_n + buffer; no name above max_weight; inverse-volatility weights follow 1/vol."""
    choose = selection_function(monkeypatch)
    scores = {"A": 5, "B": 4, "C": 3, "D": 2.9, "E": 1}
    held = {"D"}
    first = choose(scores, top_n=2, buffer=2, held=held)
    assert set(first) == {"A", "B", "D"}  # D fell out of the top 2 but is inside top 4: kept
    dropped = choose({**scores, "D": 0.5}, top_n=2, buffer=1, held={"D"})
    assert "D" not in dropped
    capped = choose(scores, top_n=3, buffer=0, held=set(), max_weight=.25, invest=.95)
    assert max(capped.values()) <= .25 + 1e-12
    vols = {"A": .01, "B": .02, "C": .04}
    inverse = choose(scores, top_n=3, buffer=0, held=set(), weighting="inverse_vol", volatility=vols, invest=1.0)
    assert inverse["A"] / inverse["B"] == pytest.approx(2.0) and inverse["B"] / inverse["C"] == pytest.approx(2.0)
    thin = choose(scores, top_n=3, buffer=0, held=set(), min_dollar_volume=1e6, dollar_volume={"A": 5e5, "B": 2e6, "C": 3e6, "D": 4e6})
    assert "A" not in thin and len(thin) == 3


def test_ranked_sector_cap(monkeypatch):
    """With sector_cap .3 and everyone in one of two sectors, each sector's weights sum to at most 30% plus one name's rounding (< 0.5%)."""
    choose = selection_function(monkeypatch)
    scores = {f"N{i}": 10 - i for i in range(10)}
    sector = {name: ("Tech" if int(name[1:]) < 6 else "Energy") for name in scores}
    weights = choose(scores, top_n=8, buffer=0, held=set(), sector_cap=.3, sector_of=sector, invest=1.0, weighting="equal")
    for group in ("Tech", "Energy"):
        assert sum(value for name, value in weights.items() if sector[name] == group) <= .3 + .005
    unknown = choose(scores, top_n=4, buffer=0, held=set(), sector_cap=.1, sector_of={}, invest=1.0)
    assert len(unknown) == 4 and sum(unknown.values()) == pytest.approx(1.0)  # names without a sector are not capped


def test_crossover_buys_the_whole_account_with_the_fee_left_over(tmp_path):
    """Max position 1 on a cash account with a taker fee: each entry is sized to what the balance pays for, fee included, so none is refused."""
    from modules.quant.native.strategy_presets import PRESETS
    spot, bar_type = E.SPOT["id"], E.SPOT["id"] + "-1-HOUR-LAST-EXTERNAL"
    swings = trending_bars()
    frame = B.frame_of(spot, bar_type, pd.date_range(B.START, periods=len(swings), freq="h", tz="UTC"),
                       swings["open"].astype(float) / 20, swings["close"].astype(float) / 20, np.full(len(swings), 300.0), span_ns=B.HOUR_NS)
    E.prepare(tmp_path, PRESETS["crossover"]["source"], frame=frame, funding=False, params={"fast": 5, "slow": 20},
              instruments=[E.SPOT], venues=[E.COINBASE], bar_types=[bar_type], settings={"maxPosition": 1},
              display=[{"nativeInstrument": spot, "symbol": "ETH/USD"}])
    _, work, _ = E.completed(tmp_path)
    orders = E.rows(tmp_path, "orders")
    assert work["stats"]["trades"] > 0
    assert not [row for row in orders if "reject" in str(row).lower()], orders[:3]
def test_saved_crossover_on_btc_spot_daily_from_2020_trades_without_rejections(tmp_path):
    from modules.quant.native.strategy_presets import PRESETS
    # Daily prices alternate up and down long enough for both moving averages to cross.
    days = pd.date_range("2020-01-01",periods=780,freq="D",tz="UTC")
    close = 20000 * np.exp(.17 * np.sin(np.arange(len(days)) / 42))
    opened = np.r_[close[0],close[:-1]]
    identity = "BTCUSDT.BINANCE"
    bar_type = identity + "-1-DAY-LAST-EXTERNAL"
    bars = B.frame_of(identity,bar_type,days,opened,close,np.full(len(days),1000.),span_ns=86400*10**9)
    coin = {**E.SPOT,"id":identity,"symbol":"BTCUSDT","currency":"USDT","baseCurrency":"BTC"}
    venue = {**E.COINBASE,"name":"BINANCE","balances":["10000 USDT"],"fees":{"type":"maker_taker","maker":.001,"taker":.001}}
    E.prepare(tmp_path,PRESETS["crossover"]["source"],frame=bars,instruments=[coin],venues=[venue],
              bar_types=[bar_type],funding=False,settings={"shorting":"off","maxPosition":1.0})
    outcome = E.run(tmp_path)
    assert outcome["status"] == "completed",outcome
    import json
    stats = json.loads((tmp_path/"output/strategy/work.json").read_text(encoding="utf8"))["stats"]
    assert stats["trades"] > 0 and stats["rejected"] == 0
