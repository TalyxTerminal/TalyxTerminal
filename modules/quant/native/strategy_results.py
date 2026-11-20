"""Snapshot original Nautilus reports without replacing execution or accounting."""
from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from uuid import uuid4

RESULT_FIELDS = (
    "trader_id", "machine_id", "instance_id", "run_config_id", "run_id",
    "run_started", "run_finished", "backtest_start", "backtest_end",
    "elapsed_time_secs", "iterations", "total_events", "total_orders",
    "total_positions", "summary", "stats_pnls", "stats_returns", "stats_general",
    "returns_series",
)


def exact_clock(item):
    """Native clocks become decimal text; a missing one (an open position's close) stays missing."""
    import pandas as pd
    return None if pd.isna(item) else str(item)


def display_statistics(result):
    """Publish native clocks as decimal text for JavaScript consumers."""
    output = dict(result)
    for name in ("run_started", "run_finished", "backtest_start", "backtest_end"):
        if output.get(name) is not None:
            output[name] = str(output[name])
    return output


def native_position_pnls(cache, snapshot):
    """Original Position owns unrealized money; source quote clocks stay exact."""
    rows = []
    for position in cache.positions_open():
        row = {"positionId": str(position.id), "instrument": str(position.instrument_id), "account": str(position.account_id),
               "quantity": str(position.quantity), "quantityNumeric": float(str(position.quantity)), "snapshotAtNs": str(snapshot), "basis": "Original native Position.unrealized_pnl; long bid / short ask; settlement currency"}
        try:
            quote = cache.quote(position.instrument_id)
            if quote is None or not quote.ts_event <= quote.ts_init <= int(snapshot):
                raise ValueError("Missing eligible original native snapshot quote")
            mark = quote.ask_price if position.is_short else quote.bid_price
            amount = position.unrealized_pnl(mark)
            row.update(markPrice=str(mark), unrealizedPnL=str(amount.as_decimal()), currency=str(amount.currency),
                       markPriceNumeric=float(str(mark)), unrealizedPnLNumeric=float(amount.as_decimal()),
                       pricePrecision=int(mark.precision), currencyPrecision=int(amount.currency.precision),
                       quoteObservedAtNs=str(quote.ts_event), quoteAvailableAtNs=str(quote.ts_init), state="completed")
        except Exception as error:
            row.update(state="unvalued", error=str(error))
        rows.append(row)
    return rows


@dataclass
class NativeBacktestOutput:
    """An author can capture before disposing their original engine."""
    engine_version: str
    result: dict
    reports: dict
    errors: list
    instruments: list = field(default_factory=list)
    figures: list = field(default_factory=list)

    @classmethod
    def from_engine(cls, engine, sampling):
        import nautilus_trader
        import pandas as pd
        from nautilus_trader.backtest import BacktestEngine
        if not isinstance(engine, BacktestEngine):
            raise TypeError("Select the original native BacktestEngine.")
        native = engine.get_result()
        result = {name: getattr(native, name) for name in RESULT_FIELDS}
        if result["run_started"] is None:
            raise ValueError("Run the original engine before capturing its reports.")
        # Keep identifiers and native numeric statistics; native currency and
        # instrument objects use their original display representation.
        result = json.loads(json.dumps(result, default=str))
        reports, errors = {}, []
        from native.strategy_equity import capture_reports
        equity, performance = capture_reports(engine, sampling)
        reports.update(equity)
        result["accountPerformance"] = performance
        for label, method in (
            ("Orders", "generate_orders_report"),
            ("Order fills", "generate_order_fills_report"),
            ("Fills", "generate_fills_report"),
            ("Positions", "generate_positions_report"),
        ):
            try:
                reports[label] = getattr(engine, method)()
            except Exception as error:
                errors.append({"title": label, "message": str(error)})
        for venue in engine.list_venues():
            try:
                reports["Account · " + str(venue)] = engine.generate_account_report(venue=venue)
            except Exception as error:
                errors.append({"title": "Account · " + str(venue), "message": str(error)})
        events = []
        for order in engine.cache.orders():
            for event in order.events():
                events.append({"nativeOrderId": str(order.client_order_id), **event.to_dict()})
        reports["Order events"] = pd.DataFrame(events)
        statistics = []
        for currency, values in result["stats_pnls"].items():
            statistics.extend({"report": "Native account PnL", "currency": currency, "statistic": key, "value": value} for key, value in values.items())
        statistics.extend({"report": "Native analyzer returns (account or closed-position basis)", "currency": None, "statistic": key, "value": value} for key, value in result["stats_returns"].items())
        statistics.extend({"report": "Native general", "currency": None, "statistic": key, "value": value} for key, value in result["stats_general"].items())
        reports["Financial statistics"] = pd.DataFrame(statistics)
        # Keep original pandas reports and exact native dictionaries. Optional
        # nanoseconds can otherwise become floats in mixed open/closed reports.
        def exact(rows):
            return pd.DataFrame([{key: str(item) if key.startswith("ts_") and item is not None else item for key, item in row.items()} for row in rows], dtype=object)
        reports["Exact native orders"] = exact([order.to_dict() for order in engine.cache.orders()])
        reports["Exact native positions"] = exact([position.to_dict() for position in engine.cache.positions()])
        if hasattr(engine, "dividend_report_json"):
            reports["Dividend entitlements and payments"] = pd.DataFrame(json.loads(engine.dividend_report_json()))
        if result["returns_series"]:
            reports["Native analyzer returns"] = pd.DataFrame(
                [{"timestampNs": int(time), "return": value} for time, value in result["returns_series"].items()]
            )
        def exact_times(value):
            if isinstance(value, dict):
                return {key: str(item) if item is not None and (key.startswith("ts_") or key.endswith("_ns")) else exact_times(item) for key, item in value.items()}
            if isinstance(value, list):
                return [exact_times(item) for item in value]
            return value
        original_instruments = [exact_times(without_sessions(instrument.to_dict())) for instrument in engine.cache.instruments()]
        reports["Native instrument terms"] = exact(original_instruments)
        marks = []
        for instrument in engine.cache.instruments():
            quote = engine.cache.quote(instrument.id)
            if quote is not None:
                marks.append({"instrument_id": str(instrument.id), "bid": str(quote.bid_price), "ask": str(quote.ask_price),
                              "ts_event": str(quote.ts_event), "ts_init": str(quote.ts_init)})
        reports["Position snapshot marks"] = pd.DataFrame(marks)
        reports["Native position unrealized PnL"] = pd.DataFrame(native_position_pnls(engine.cache, result["backtest_end"]))
        # The run's figures are drawn once for the whole run by the strategy report (native/strategy_report.py), not per account or bar type.
        figures = []
        return cls(nautilus_trader.__version__, result, reports, errors, original_instruments, figures)


def without_sessions(terms):
    """Instrument terms as a result keeps them: a stock's per-day execution sessions live in the run's sessions file, so the result's
    size does not grow with names times days."""
    info = terms.get("info")
    if not info or "execution_conditions" not in info:
        return terms
    conditions = json.loads(info["execution_conditions"])
    conditions["rules"].pop("sessions")
    return {**terms, "info": {**info, "execution_conditions": json.dumps(conditions, allow_nan=False)}}


def collect(collector, title, value):
    from nautilus_trader.backtest import BacktestEngine
    if not isinstance(value, (BacktestEngine, NativeBacktestOutput)):
        return False
    if id(value) in collector.seen:
        return True
    collector.seen[id(value)] = value
    from native.strategy_equity import engine_day_sampling
    # The backend puts the topic market's annualization into every invocation.
    output = NativeBacktestOutput.from_engine(value, engine_day_sampling(value, collector.invocation["annualization"])) if isinstance(value, BacktestEngine) else value
    identity = collector.name(title)
    publisher = collector.publisher
    artifacts = []
    metadata = publisher.root / (identity + "/native-result.json")
    metadata.parent.mkdir(parents=True, exist_ok=True)
    from native.publish import safe
    metadata.write_text(json.dumps(safe(output.result), indent=2), encoding="utf8")
    artifacts.append(publisher.artifact(metadata, "Complete native backtest statistics", "file"))
    files = [{"name": metadata.relative_to(publisher.root).as_posix(), "role": "native_statistics", "digest": hashlib.sha256(metadata.read_bytes()).hexdigest()}]
    version = publisher.index.get("strategyVersion")
    if version:
        for role, item in (("strategy_definition", version["definition"]), ("artifact_scores", version["scoreInput"])):
            files.append({"name": item["name"], "digest": item["digest"], "role": role})
    roles = {"Orders": "orders", "Fills": "fills", "Positions": "native_positions_report", "Exact native positions": "positions", "Exact native orders": "orders_exact", "Native instrument terms": "instrument_terms", "Position snapshot marks": "position_marks", "Native position unrealized PnL": "position_pnls", "Order events": "order_events", "Account equity": "account_equity", "Native analyzer returns": "native_analyzer_returns", "Execution parents": "execution_parents", "Execution child arrivals": "execution_arrivals", "Inventory timeline": "inventory", "Quote decisions": "quote_decisions", "Fill markouts": "fill_markouts", "Dividend entitlements and payments": "corporate_actions"}
    # Each native report is kept once, as Parquet with exact nanosecond clocks (no second JSON copy): the run record stays small.
    for number, (label, frame) in enumerate(output.reports.items()):
        displayed = frame.copy()
        import pandas as pd
        if isinstance(displayed.index, pd.DatetimeIndex):
            displayed.index = displayed.index.map(lambda item: item.isoformat())
        for column in displayed.columns:
            if str(column).startswith("ts_") or column == "timestampNs":
                # JSON consumers use binary64 numbers; native nanoseconds
                # need decimal text to preserve sub-millisecond event order.
                displayed[column] = [exact_clock(item) for item in displayed[column].tolist()]
        for column in displayed.select_dtypes(include="object").columns:
            values = displayed[column].tolist()
            if any(isinstance(item, (dict, list, tuple)) for item in values):
                displayed[column] = [json.dumps(item, default=str, ensure_ascii=False) if isinstance(item, (dict, list, tuple)) else item for item in values]
        table = publisher.table(displayed, f"{identity}/report-{number}", label)
        artifacts.append(table)
        path = publisher.root / table["name"]
        files.append({"name": table["name"], "role": roles.get(label, "account_returns" if label.startswith("Account daily returns · ") else "account" if label.startswith("Account · ") else "native_report"), "digest": hashlib.sha256(path.read_bytes()).hexdigest(), "format": "parquet"})
    publisher.section(identity, title, artifacts, nativeKind="nautilus_trader.backtest.BacktestEngine")
    publisher.index["units"].append({"id": identity, "title": title, "state": "completed", "nativeKind": "nautilus_trader.backtest.BacktestEngine", "reportErrors": output.errors, "nativeStatistics": display_statistics(output.result)})
    manifest = {"formatVersion": 1, "resultId": str(uuid4()), "engine": "nautilus", "engineVersion": output.engine_version, "nativeKind": "nautilus_trader.backtest.BacktestEngine", "files": files, "recipeRef": publisher.invocation.get("recipeRef"), "inputRefs": publisher.invocation.get("materials", []), "axes": {"time": "native_nanoseconds", "order": "client_order_id", "asset": "exact_native_instrument_id", "instruments": output.instruments, "snapshotAtNs": str(output.result["backtest_end"])}, "units": {"returns": "Account equity daily simple returns; no external cash-flow adjustment", "nativeAnalyzerReturns": "Original analyzer series; may use closed-position returns", "annualization": "Native statistics retain 252 days", "cash": "Original account currencies"}, "availableOperations": []}
    path = publisher.root / identity / "manifest.json"
    path.write_text(json.dumps(manifest, ensure_ascii=False, allow_nan=False), encoding="utf8")
    artifact = publisher.artifact(path, "Fixed native strategy results", "result", digest=hashlib.sha256(path.read_bytes()).hexdigest(), resultId=manifest["resultId"], availableOperations=[])
    publisher.index.setdefault("results", []).append({**manifest, "unitId": identity, "artifactName": artifact["name"], "digest": artifact["digest"]})
    # The immutable manifest references the fixed source recipe and complete native reports.
    from native.topic_artifacts import bundle
    bundle(publisher, publisher.index["results"][-1], identity + "/strategy_version", "strategy_version", sourceAxes=manifest["axes"], units=manifest["units"],
           range={"start": str(output.result["backtest_start"]), "end": str(output.result["backtest_end"])})
    equity = output.reports.get("Account equity")
    if equity is not None and not equity.empty:
        publisher.named_table(equity, identity + "/curve", "curve", sourceAxes=manifest["axes"], units=manifest["units"])
    publisher.index["engineVersion"] = output.engine_version
    publisher.write()
    return True
