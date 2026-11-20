"""The three ready-made strategies on synthetic bars and library values, in the fixed strategy environment.

Not part of the daily suite; see test_strategy_object.py for how to run the native checks.
"""
from __future__ import annotations

import itertools
import json
import os
import subprocess

import numpy as np
import pandas as pd
import pytest

from modules.quant.native.strategy_presets import PRESETS
from tests.native.environments import environment
from tests.native.test_strategy_object import QUANT

pytestmark = pytest.mark.skipif(not os.environ.get("TALYX_NATIVE_SMOKE"), reason="Set TALYX_NATIVE_SMOKE=1 to run native engines")

HOUR_NS = 3_600_000_000_000
NAMES = ["AAA", "BBB", "CCC", "DDD"]
HOURS = 24 * 12


def instrument_id(name, perpetual):
    return f"{name}USDT-PERP.OKX" if perpetual else f"{name}USDT.OKX"


def bars(root, perpetual, count):
    rng = np.random.default_rng(3)
    opened = pd.date_range("2024-01-01", periods=HOURS, freq="h", tz="UTC")
    ends = [str(value) for value in opened.as_unit("ns").asi8 + HOUR_NS]
    frames, terms = [], []
    for number, name in enumerate(NAMES[:count]):
        identity = instrument_id(name, perpetual)
        close = 100 * (1 + number) * np.exp(np.cumsum(rng.normal((number - 1.5) * 0.0004, 0.004, HOURS)))
        start = np.r_[close[0], close[:-1]]
        text = lambda values: [format(round(float(value), 2), "f") for value in values]
        frames.append(pd.DataFrame({"open": text(start), "high": text(np.maximum(start, close) * 1.001), "low": text(np.minimum(start, close) * .999),
                                    "close": text(close), "volume": [format(value, "f") for value in rng.uniform(1000, 5000, HOURS).round(3)], "instrument": identity,
                                    "barType": identity + "-1-HOUR-LAST-EXTERNAL", "ts_event": ends, "ts_init": ends}))
        terms.append({"id": identity, "type": "perpetual" if perpetual else "spot", "symbol": f"{name}USDT" + ("-PERP" if perpetual else ""), "currency": "USDT",
                      "baseCurrency": name, "pricePrecision": 2, "sizePrecision": 3, "priceIncrement": "0.01", "sizeIncrement": "0.001",
                      **({"settlementCurrency": "USDT"} if perpetual else {})})
    pd.concat(frames, ignore_index=True).to_parquet(root / "bars.parquet", index=False)
    return terms, [instrument_id(name, perpetual) + "-1-HOUR-LAST-EXTERNAL" for name in NAMES[:count]]


def library(root, perpetual, signal, count, skip=None):
    """One value per symbol at each midnight (the close of the day's last hour), AAA..DDD scored by `signal(day, number)`."""
    rows = []
    start = pd.Timestamp("2024-01-01", tz="UTC").value
    for day in range(1, HOURS // 24):
        available = start + day * 24 * HOUR_NS
        for number, name in enumerate(NAMES[:count]):
            if skip == (day, name):
                continue
            if signal(day, number) is None:
                continue
            rows.append({"name": "momentum", "instrument": instrument_id(name, perpetual), "field": "score", "value": float(signal(day, number)), "version": 2,
                         "ts_event": available - HOUR_NS, "ts_init": available})
    pd.DataFrame(rows).sort_values("ts_init", kind="stable").to_parquet(root / "library.parquet", index=False)


def run(root, preset, params, *, perpetual=False, signal=lambda day, number: number, skip=None, symbols=len(NAMES)):
    (root / "source").mkdir()
    (root / "output").mkdir()
    (root / "source" / "strategy.py").write_text(PRESETS[preset]["source"], encoding="utf-8")
    terms, bar_types = bars(root, perpetual, symbols)
    library(root, perpetual, signal, symbols, skip)
    margin = {"name": "OKX", "accountType": "MARGIN", "omsType": "NETTING", "balances": ["100000 USDT"], "baseCurrency": "USDT",
              "fees": {"type": "maker_taker", "maker": 0.0002, "taker": 0.0005}, "leverage": 1}
    cash = {**margin, "accountType": "CASH"}
    cash.pop("baseCurrency")
    backtest = {"engine": "nautilus", "name": "preset", "task": "event", "dataType": "bars", "timezone": "UTC", "priceBasis": "raw", "instruments": terms,
                "venues": [margin if perpetual else cash], "strategy": {"kind": "object", "module": "strategy"}}
    invocation = {"runId": "preset-smoke", "title": "preset", "workbench": "strategy", "entry": "strategy.py", "source": str(root / "source"),
                  "output": str(root / "output"), "library": None, "materials": [{"role": "market_data", "path": str(root / "bars.parquet")}],
                  "mode": "run", "operation": "strategy.backtest", "options": {}, "sourceRefs": [], "annualization": 365, "barTypes": bar_types,
                  "params": params, "backtest": backtest, "recipeRef": None,
                  "libraryValues": {"path": str(root / "library.parquet"), "items": [{"name": "momentum", "version": 2, "versionId": "momentum-v2"}]},
                  "models": [], "index": None}
    (root / "invocation.json").write_text(json.dumps(invocation), encoding="utf-8")
    interpreter = environment("strategy.lock", "3.13.13")
    done = subprocess.run([str(interpreter), "-B", str(QUANT / "native" / "object_worker.py"), str(root / "invocation.json")],
                          cwd=root / "source", capture_output=True, text=True, timeout=600)
    result = json.loads((root / "output" / "result-index.json").read_text(encoding="utf-8"))
    return result, done.stderr


def table(root, result, title):
    item = next(entry for entry in result["artifacts"] if entry["kind"] == "table" and entry["title"] == title)
    return pd.read_parquet(root / "output" / item["name"])


def held(root, result):
    """The symbols with a fill, and how many fills each had."""
    fills = table(root, result, "Fills")
    return fills.groupby("instrument_id").size().to_dict() if len(fills) else {}


def test_ranked_holds_the_top_names_and_ranks_a_period_with_a_missing_value_among_the_rest(tmp_path):
    result, stderr = run(tmp_path, "ranked", {"signal": "momentum", "top_n": 2, "rebalance_every": 1}, skip=(3, "BBB"))
    assert result["status"] == "completed", (result.get("error"), stderr[-1500:])
    assert set(held(tmp_path, result)) == {"CCCUSDT.OKX", "DDDUSDT.OKX"}
    rebalances = table(tmp_path, result, "Signal rebalances")
    skipped = rebalances[rebalances["state"] == "skipped"]
    assert len(skipped) == 0  # BBB has no value in one period: the top 2 are ranked among the three that have one
    assert (rebalances["state"] == "rebalanced").sum() == HOURS // 24 - 1
    ranking = table(tmp_path, result, "Signal ranking")
    assert set(ranking[ranking["rank"] <= 2]["instrument"]) == {"CCCUSDT.OKX", "DDDUSDT.OKX"}
    assert abs(ranking[ranking["ts_available"] == ranking["ts_available"].min()]["weight"].sum() - 0.95) < 1e-9


def test_ranked_shorts_the_bottom_names_on_perpetuals_and_refuses_to_on_spot(tmp_path):
    result, stderr = run(tmp_path, "ranked", {"signal": "momentum", "top_n": 1, "short_n": 1}, perpetual=True)
    assert result["status"] == "completed", (result.get("error"), stderr[-1500:])
    fills = table(tmp_path, result, "Fills")
    # Rebalancing to constant weights trades a little both ways as prices drift; what opened each position says which leg it is.
    opening = fills.groupby("instrument_id")["order_side"].first().map(str).to_dict()
    assert opening == {"DDDUSDT-PERP.OKX": "BUY", "AAAUSDT-PERP.OKX": "SELL"}
    spot = tmp_path / "spot"
    spot.mkdir()
    refused, _ = run(spot, "ranked", {"signal": "momentum", "top_n": 1, "short_n": 1})
    assert refused["status"] == "failed" and "cannot be shorted" in refused["error"]


def test_rebalance_trades_to_the_published_weights_and_waits_inside_the_tolerance(tmp_path):
    def weights(day, number):
        # Zero is an explicit weight; a missing member skips the complete batch.
        return [0.5, 0.3, 0.2, 0.0][number] if day % 2 else [0.4, 0.4, 0.2, 0.0][number]

    result, stderr = run(tmp_path, "rebalance", {"signal": "momentum", "tolerance": 0.0}, signal=weights, skip=(3, 'BBB'))
    assert result["status"] == "completed", (result.get("error"), stderr[-1500:])
    assert set(held(tmp_path, result)) == {"AAAUSDT.OKX", "BBBUSDT.OKX", "CCCUSDT.OKX"}
    targets = table(tmp_path, result, "Weight targets")
    first = targets[targets["ts_available"] == targets["ts_available"].min()].set_index("instrument")
    assert first["target_weight"].round(6).to_dict() == {"AAAUSDT.OKX": 0.5, "BBBUSDT.OKX": 0.3, "CCCUSDT.OKX": 0.2, "DDDUSDT.OKX": 0.0}
    skipped = table(tmp_path, result, "Signal rebalances")
    assert skipped.loc[skipped['state'] == 'skipped', 'reason'].str.contains('No weight for BBBUSDT.OKX', regex=False).any()
    (tmp_path / "loose").mkdir()
    loose, stderr = run(tmp_path / "loose", "rebalance", {"signal": "momentum", "tolerance": 0.3}, signal=weights)
    assert loose["status"] == "completed", (loose.get("error"), stderr[-1500:])
    rebalances = table(tmp_path / "loose", loose, "Signal rebalances")
    assert (rebalances["state"] == "skipped").any() and "within the tolerance" in " ".join(rebalances["reason"].dropna())


def test_signal_trader_buys_above_the_level_and_sells_below_it(tmp_path):
    result, stderr = run(tmp_path, "signal", {"signal": "momentum", "buy_above": 0.5, "sell_below": -0.5}, signal=lambda day, number: 1.0 if day in (2, 3) else -1.0 if day in (6, 7) else 0.0, symbols=1)
    assert result["status"] == "completed", (result.get("error"), stderr[-1500:])
    decisions = table(tmp_path, result, "Signal decisions")
    actions = decisions.set_index(decisions["ts_available"].rank().astype(int))["action"].to_dict()
    assert [actions[day] for day in (1, 2, 3)] == ["none", "buy", "none"] and actions[6] == "sell"
    fills = table(tmp_path, result, "Fills")
    # A bar's volume caps what one order fills, so an order can take more than one fill.
    assert [side for side, _ in itertools.groupby(map(str, fills["order_side"]))] == ["BUY", "SELL"]
