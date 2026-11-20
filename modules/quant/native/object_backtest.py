"""A strategy object's backtest: the settings become the venue, the author's class becomes the strategy."""
from __future__ import annotations

import importlib

import talyx_quant as q


def load_strategy(invocation, config):
    from nautilus_trader.model import BarType, InstrumentId
    q._bind_index(invocation.get('index'))
    module = importlib.import_module(config["strategy"]["module"])
    classes = [value for value in vars(module).values()
               if isinstance(value, type) and value.__module__ == module.__name__ and issubclass(value, q.Strategy)]
    if len(classes) != 1:
        raise ValueError(f"{module.__name__}.py must define exactly one class based on q.Strategy; found {len(classes)}.")
    strategy = classes[0]()
    strategy.instrument_ids = [InstrumentId.from_str(item["id"]) for item in config["instruments"]]
    strategy.bar_types = [BarType.from_str(value) for value in invocation["barTypes"]]
    q._bind_strategy(strategy, invocation.get("runSettings"), config.get("tradeFrom"), invocation.get("accounts"))
    q._bind_extras(invocation.get("extras"))
    return strategy


def run(invocation, publisher):
    import time

    from native.process_memory import peak_memory
    from native.strategy_research import lean, run_one
    config = invocation["backtest"]
    q._bind_parameters(invocation["params"])
    publisher.index["effectiveConfig"] = lean(config)
    try:
        run_one(invocation, publisher, config, invocation["title"])
    finally:
        # Where the process's time went: reading the inputs, building the engine, turning bars into engine data, the engine, the report, writing; `other`
        # is what is left of the whole process (imports, loading the code, closing the engine), so the parts add up to `total`.
        parts = publisher.index.pop("runTimings", {})
        total = round(time.time() - invocation.get("_started", time.time()), 3)
        named = {key: parts.get(key, 0.0) for key in ("input", "build", "bars", "engine", "report", "write")}
        publisher.index["timings"] = {**named, "total": total, "other": round(total - sum(named.values()), 3)}
        publisher.index["peakMemory"] = peak_memory()
    publisher.index["parameters"] = dict(q._used)
    publisher.write()
