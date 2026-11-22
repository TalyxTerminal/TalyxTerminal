"""Writing an invocation and running the strategy process the way the Host does, for the native tests (set TALYX_NATIVE_SMOKE=1)."""
from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pandas as pd

from tests.fixtures.strategy.bars import BAR_TYPE, INSTRUMENT, funding_rows, perp_bars
from tests.native.environments import environment

QUANT = Path(__file__).resolve().parents[3] / "modules" / "quant"
PERP = {"id": INSTRUMENT, "type": "perpetual", "symbol": "BTCUSDT-PERP", "currency": "USDT", "baseCurrency": "BTC", "settlementCurrency": "USDT",
        "pricePrecision": 1, "sizePrecision": 6, "priceIncrement": "0.1", "sizeIncrement": "0.000001", "takerFee": "0.0005"}
OKX = {"name": "OKX", "accountType": "MARGIN", "omsType": "NETTING", "balances": ["100000 USDT"], "baseCurrency": "USDT",
       "fees": {"type": "maker_taker", "maker": 0.0002, "taker": 0.0005}, "leverage": 1, "marginModel": "leveraged"}
STOCK = {"type": "equity", "currency": "USD", "pricePrecision": 2, "sizePrecision": 0, "priceIncrement": "0.01", "sizeIncrement": "1"}
XNAS = {"name": "XNAS", "accountType": "MARGIN", "omsType": "NETTING", "balances": ["100000 USD"], "baseCurrency": "USD",
        "fees": {"type": "maker_taker", "maker": 0, "taker": 0}, "leverage": 2, "marginModel": "standard"}
COINBASE = {"name": "COINBASE", "accountType": "CASH", "omsType": "NETTING", "balances": ["10000 USD"], "baseCurrency": None,
            "fees": {"type": "maker_taker", "maker": 0.0004, "taker": 0.0006}, "leverage": 1, "marginModel": "standard"}
SPOT = {"id": "ETHUSD-SPOT.COINBASE", "type": "spot", "symbol": "ETHUSD", "currency": "USD", "baseCurrency": "ETH", "pricePrecision": 1, "sizePrecision": 4,
        "priceIncrement": "0.1", "sizeIncrement": "0.0001"}
STRATEGY_RUN = {"fill": "nextOpen", "costs": {"halfSpreadBps": 2.0, "impact": 0.0, "funding": True, "borrowPct": 0.3}, "exchange": {"stopTrigger": "last"}}


def sessions_file(root, instruments):
    """The stocks' sessions moved out of the instruments into `stock-sessions.parquet`, as the Host writes them; its path, or None without stocks."""
    import pyarrow as pa
    import pyarrow.parquet as pq

    from modules.quant.backend.object_data import SESSION_SCHEMA, session_table
    tables = [session_table(item["id"], item["stock"].pop("sessions")) for item in instruments if "sessions" in item.get("stock", {})]
    if not tables:
        return None
    path = Path(root) / "stock-sessions.parquet"
    pq.write_table(pa.concat_tables(tables).cast(SESSION_SCHEMA), path)
    return str(path)


def prepare(root, code, *, frame=None, settings=None, funding=True, instruments=None, venues=None, bar_types=None, annualization=365, display=None,
            benchmark=None, risk_free=None, extra=None, funding_frame=None, params=None):
    """Write the project, the bars and the invocation under `root`; `extra` overrides invocation keys. Returns the bars frame."""
    root = Path(root)
    (root / "source").mkdir(exist_ok=True)
    (root / "output").mkdir(exist_ok=True)
    (root / "source" / "strategy.py").write_text(code, encoding="utf-8")
    frame = perp_bars() if frame is None else frame
    frame.to_parquet(root / "bars.parquet", index=False)
    rows = funding_rows() if funding_frame is None else funding_frame
    rows.to_parquet(root / "funding.parquet", index=False)
    backtest = {"engine": "nautilus", "name": "object", "task": "event", "dataType": "bars", "timezone": "UTC", "priceBasis": "raw",
                "instruments": instruments or [PERP], "venues": venues or [OKX], "strategy": {"kind": "object", "module": "strategy"}}
    run_settings = {**STRATEGY_RUN, **(settings or {})}
    invocation = {"runId": "report-smoke", "title": "object", "workbench": "strategy", "entry": "strategy.py", "source": str(root / "source"),
                  "output": str(root / "output"), "library": None, "materials": [{"role": "market_data", "path": str(root / "bars.parquet")}],
                  "mode": "run", "operation": "strategy.backtest", "options": {}, "sourceRefs": [], "annualization": annualization,
                  "barTypes": bar_types or [BAR_TYPE], "params": params or {}, "backtest": backtest, "recipeRef": None, "libraryValues": {"path": None}, "models": [],
                  "index": None, "runSettings": run_settings, "benchmark": benchmark or {"kind": "hold"}, "riskFree": risk_free or {"basis": "zero"},
                  "fundingRates": {"path": str(root / "funding.parquet") if funding else None, "gaps": {}},
                  "display": display or [{"nativeInstrument": INSTRUMENT, "symbol": "BTC/USDT:USDT"}],
                  "stockSessions": sessions_file(root, backtest["instruments"]), **(extra or {})}
    (root / "invocation.json").write_text(json.dumps(invocation), encoding="utf-8")
    return frame


def run(root):
    """Run the invocation in the strategy environment; returns result-index.json (status, error, failureContext, ...)."""
    root = Path(root)
    interpreter = environment("strategy.lock", "3.13.13")
    done = subprocess.run([str(interpreter), "-B", str(QUANT / "native" / "object_worker.py"), str(root / "invocation.json")],
                          cwd=root / "source", capture_output=True, text=True, timeout=900)
    (root / "stderr.txt").write_text(done.stderr, encoding="utf-8")
    return json.loads((root / "output" / "result-index.json").read_text(encoding="utf-8"))


def read(root, name):
    return json.loads((Path(root) / "output" / "strategy" / f"{name}.json").read_text(encoding="utf-8"))


def completed(root):
    """Run and return (index, work, results), asserting the run completed."""
    index = run(root)
    assert index["status"] == "completed", (index.get("error"), (Path(root) / "stderr.txt").read_text(encoding="utf-8")[-3000:])
    return index, read(root, "work"), read(root, "results")


def trades_table(root):
    return pd.read_parquet(Path(root) / "output" / "strategy" / "trades.parquet")


def orders_table(root):
    return pd.read_parquet(Path(root) / "output" / "strategy" / "orders.parquet")


def rows(root, name):
    """A table of the run (trades, orders, rebalances) as the pages read it: lists and dicts back from the JSON text the report writes, NaN as None."""
    out = json.loads(pd.read_parquet(Path(root) / "output" / "strategy" / f"{name}.parquet").to_json(orient="records"))
    for row in out:
        for key, value in row.items():
            if isinstance(value, str) and value[:1] in ("[", "{"):
                row[key] = json.loads(value)
    return out


def figures_of(value):
    if isinstance(value, dict):
        if "data" in value and "layout" in value:
            yield value
            return
        for item in value.values():
            yield from figures_of(item)
    elif isinstance(value, list):
        for item in value:
            yield from figures_of(item)
