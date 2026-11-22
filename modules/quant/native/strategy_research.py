"""Fixed native event simulation; Nautilus owns orders, fills and accounting."""
from __future__ import annotations

import copy
import math
from decimal import Decimal

from native.research_tables import input_path, read_table


def read_inputs(invocation, config):
    path = input_path(invocation, config, "market_data")
    frame = read_table(path)
    required = {"instrument", "ts_event", "barType", "open", "high", "low", "close", "volume"}
    if missing := required - set(frame):
        raise ValueError("Market input needs source columns: " + ", ".join(sorted(missing)))
    for key in ("instrument", "ts_event"):
        if key not in frame or frame[key].isna().any():
            raise ValueError(f"Market input needs nonmissing {key} values.")
    known = {item["id"] for item in config.get("instruments", [])}
    if set(frame.instrument) - known:
        raise ValueError("Input contains instruments without exact native terms.")
    if "ts_init" not in frame:
        frame["ts_init"] = frame["ts_event"]
    return frame.sort_values("ts_init", kind="stable", ignore_index=True)


def instruments(config):
    from nautilus_trader.model import (
        CryptoPerpetual,
        Currency,
        CurrencyPair,
        Equity,
        InstrumentId,
        Price,
        Quantity,
        Symbol,
    )
    output = []
    for item in config["instruments"]:
        iid = InstrumentId.from_str(item["id"])
        currency = Currency.from_str(item["currency"])
        common = dict(instrument_id=iid, raw_symbol=Symbol(item.get("symbol", iid.symbol.value)), price_precision=int(item["pricePrecision"]), price_increment=Price.from_str(item["priceIncrement"]), ts_event=0, ts_init=0)
        if "takerFee" in item:
            common["info"] = {"taker_fee": item["takerFee"], **({"maker_fee": item["makerFee"]} if "makerFee" in item else {})}
        if item["type"] == "equity":
            if 'stock' in item:
                from native.stock_execution import instrument_info
                if "sessionWindowFrom" in config:
                    item = windowed(item, config["sessionWindowFrom"])
                common['info'] = instrument_info(item, raw=True, state=item['executionState'], rebuild_orders=item['rebuildOrders']) if 'executionRaw' in item and item['executionRaw'] else instrument_info(item)
            native = Equity(currency=currency, lot_size=Quantity.from_str(item.get("sizeIncrement", "1")), **common)
        else:
            common.update(base_currency=Currency.from_str(item["baseCurrency"]), quote_currency=currency, size_precision=int(item["sizePrecision"]), size_increment=Quantity.from_str(item["sizeIncrement"]))
            if item["type"] == "perpetual":
                native = CryptoPerpetual(settlement_currency=Currency.from_str(item.get("settlementCurrency", item["currency"])), is_inverse=bool(item.get("inverse", False)), margin_init=Decimal(str(item.get("marginInit", "0.1"))), margin_maint=Decimal(str(item.get("marginMaint", "0.05"))), **common)
            elif item["type"] == "spot":
                native = CurrencyPair(**common)
            else:
                raise ValueError("Use equity, spot or perpetual native instrument terms, or a Python instrument provider.")
        output.append(native)
    return output


def fixed_text(values, precision, instrument, key):
    """Decimal texts at the instrument's native precision ("1.5" at 2 is "1.50"), cast in Arrow: a value with more places than the
    precision is refused (Arrow will not drop digits), never rounded."""
    import pyarrow as pa
    try:
        return pa.array(values, type=pa.string()).cast(pa.decimal128(38, precision)).cast(pa.string()).to_pylist()
    except pa.ArrowInvalid as error:
        raise ValueError(f"{instrument} observation exceeds native {key}.") from error


def event_data(config, frame):
    """The bars as engine data, one instrument and bar type at a time over column arrays (a dict and five Decimals a row took 1.4 GiB and
    28 s for 1.37 million bars). Equal times keep the input's order."""
    from nautilus_trader.model import (
        Bar,
        BarType,
        Price,
        Quantity,
    )
    if (frame["ts_init"] < frame["ts_event"]).any():
        raise ValueError("Received time cannot precede the source event.")
    terms = {item["id"]: item for item in config["instruments"]}
    result = [None] * len(frame)
    for (instrument, bar_type), positions in frame.groupby(["instrument", "barType"], sort=False).indices.items():
        group = frame.iloc[positions]
        kind, item = BarType.from_str(bar_type), terms[instrument]
        prices = [fixed_text(group[key], int(item.get("pricePrecision", 0)), instrument, "pricePrecision") for key in ("open", "high", "low", "close")]
        volumes = fixed_text(group["volume"], int(item.get("sizePrecision", 0)), instrument, "sizePrecision")
        for at, o, h, low, c, v, event, received in zip(positions.tolist(), *prices, volumes, group["ts_event"].tolist(), group["ts_init"].tolist(), strict=True):
            result[at] = Bar(kind, Price.from_str(o), Price.from_str(h), Price.from_str(low), Price.from_str(c), Quantity.from_str(v), event, received)
    if config.get('referencePrices'):
        if config['dataType'] != 'bars':
            raise ValueError('Reference OHLC requires executable LAST bars.')
        from native.reference_prices import expand, rows
        return expand(result, rows(frame, config['referencePrices']), {str(item.id): item for item in instruments(config)}, author_barrier=True)
    return result


def stock_metadata(config):
    stock_materials = [item['stock'] for item in config['instruments'] if item['type'] == 'equity' and 'stock' in item]
    if stock_materials and not any('executionRaw' in item and item['executionRaw'] for item in config['instruments']):
        config['priceBasis'] = 'split_dividend'
        config['dataMeaning'] = 'Split-and-dividend adjusted research prices; simulated shares, cash and fees use adjusted notional. ' + ' '.join(dict.fromkeys(note for item in stock_materials for note in item['limitations']))


def instrument_fees():
    from nautilus_trader.execution import FeeModel
    from nautilus_trader.model import LiquiditySide, Money

    class InstrumentFees(FeeModel):
        """Each instrument's own maker/taker rate (its `info`) on the fill's notional: one venue's symbols may be charged differently."""

        def get_commission(self, order, fill_quantity, fill_px, instrument):
            notional = instrument.notional_value(fill_quantity, fill_px)
            rate = instrument.info["maker_fee" if order.liquidity_side == LiquiditySide.MAKER else "taker_fee"]
            return Money(notional.as_decimal() * Decimal(rate), notional.currency)
    return InstrumentFees


def make_engine(config, *, cache_database_factory=None, trader_id=None, simulation=None):
    stock_metadata(config)
    from native.strategy_options import latency_ns
    from nautilus_trader.backtest import BacktestEngine, BacktestEngineConfig
    from nautilus_trader.common import LoggerConfig
    from nautilus_trader.data import DataEngineConfig
    from nautilus_trader.execution import FixedFeeModel, MakerTakerFeeModel, StaticLatencyModel
    from nautilus_trader.model import (
        AccountType,
        BookType,
        Currency,
        LeveragedMarginModel,
        Money,
        OmsType,
        StandardMarginModel,
        Venue,
    )
    from nautilus_trader.portfolio import PortfolioConfig
    from nautilus_trader.risk import RiskEngineConfig
    native_config = BacktestEngineConfig(logging=LoggerConfig(bypass_logging=True, print_config=False), bypass_logging=True,
                           portfolio=PortfolioConfig(snapshot_interval_ms=config.get("equitySamplingMs")),
                           data_engine=DataEngineConfig(), risk_engine=RiskEngineConfig(**config.get("riskEngine", {})), **({"trader_id": trader_id} if trader_id is not None else {}))
    engine = BacktestEngine(native_config, cache_database_factory=cache_database_factory) if cache_database_factory is not None else BacktestEngine(native_config)
    InstrumentFees = instrument_fees()
    for venue in config["venues"]:
        fee = venue.get("fees", {})
        # Venue registration requires a model before instruments exist. The
        # native instrument condition model replaces this registration model;
        # malformed conditions reject orders instead of falling through to it.
        fee_model = InstrumentFees() if fee.get("type") == "per_instrument" else \
            MakerTakerFeeModel(Decimal(str(fee.get("maker", 0))), Decimal(str(fee.get("taker", 0)))) if fee.get("type") == "maker_taker" else FixedFeeModel(Money.from_str(fee.get("amount", "0 USD")))
        latency = dict(venue.get("latency", {}))
        extras = (simulation or {}).get(venue["name"], {})
        if extras.get("latency"):
            # A signal fills on the next bar: the order reaches the venue just after the bar it was decided on.
            latency["insert"] = max(int(latency.get("insert", 0)), extras["latency"])
        engine.add_venue(Venue(venue["name"]), OmsType.from_str(venue.get("omsType", "NETTING")), AccountType.from_str(venue.get("accountType", "CASH")), [Money.from_str(value) for value in venue["balances"]], base_currency=Currency.from_str(venue["baseCurrency"]) if venue.get("baseCurrency") else None, fee_model=fee_model,
                         latency_model=StaticLatencyModel(*[latency_ns(latency.get(key, 0), "Native " + key + " latency") for key in ("base", "insert", "update", "cancel")]), book_type=BookType.from_str(venue.get("bookType", "L1_MBP")), default_leverage=Decimal(str(venue.get("leverage", 1))), **({"fill_model": extras["fill_model"]} if extras.get("fill_model") is not None else {}), **({"modules": extras["modules"]} if extras.get("modules") else {}), margin_model=LeveragedMarginModel() if venue.get("marginModel") == "leveraged" else StandardMarginModel(),
                         liquidity_consumption=bool(venue.get("liquidityConsumption", True)), queue_position=bool(venue.get("queuePosition", False)), trade_execution=False, bar_execution=True, **({"bar_adaptive_high_low_ordering": True} if extras.get("adaptive") else {}), liquidation_enabled=False, liquidation_trigger_ratio=float(venue.get("liquidationTriggerRatio", 1)), allow_cash_borrowing=False, frozen_account=bool(venue.get("frozenAccount", False)))
    for instrument in instruments(config):
        engine.add_instrument(instrument)
    return engine


class Sessions:
    """One stock's daily sessions in time order, kept as Arrow columns: the venue reads a few weeks at a time, and a session becomes the dict
    `stock_execution.conditions` reads only when it is asked for. Slices share the columns."""

    def __init__(self, table):
        self.table = table
        self.open_at = table.column("openAt").to_numpy()
        self.close_at = table.column("closeAt").to_numpy()

    def __len__(self):
        return self.table.num_rows

    def __iter__(self):
        for row in self.table.to_pylist():
            yield {"sessionDate": row["sessionDate"], "openAt": row["openAt"], "closeAt": row["closeAt"], "lower": row["lower"], "upper": row["upper"],
                   "priceIncrement": row["priceIncrement"], "priceMap": {"scale": row["priceScale"], "offset": row["priceOffset"], "maxError": row["priceMaxError"]}}

    def between(self, first, last):
        """The sessions that close at or after `first` and open at or before `last` (milliseconds)."""
        import numpy as np
        start = int(np.searchsorted(self.close_at, first, "left"))
        stop = max(start, int(np.searchsorted(self.open_at, last, "right")))
        return Sessions(self.table.slice(start, stop - start))


def stock_sessions(invocation, config):
    """Stock sessions travel in their own file (one row a session, `backend/object_data.session_table`); the invocation stays small."""
    path = invocation.get("stockSessions")
    if not path:
        return
    import numpy as np
    import pyarrow.parquet as pq
    table = pq.read_table(path).sort_by([("instrument", "ascending"), ("closeAt", "ascending")])
    names = table.column("instrument").to_numpy(zero_copy_only=False)
    starts = np.flatnonzero(np.r_[True, names[1:] != names[:-1]])
    spans = {names[start]: (int(start), int(stop)) for start, stop in zip(starts, [*starts[1:], len(names)], strict=True)}
    for item in config["instruments"]:
        if item.get("type") == "equity" and "stock" in item and "sessions" not in item["stock"]:
            start, stop = spans.get(item["id"], (0, 0))
            item["stock"]["sessions"] = Sessions(table.slice(start, stop - start))


def sessions_kept(config):
    """The stocks' session lists: a copy of the configuration shares them (they are read, never changed, and they are most of its size)."""
    return {id(item["stock"]["sessions"]): item["stock"]["sessions"] for item in config.get("instruments", [])
            if isinstance(item.get("stock"), dict) and "sessions" in item["stock"]}


WINDOW_BEHIND_MS = 5 * 86_400_000
WINDOW_AHEAD_MS = 60 * 86_400_000
WINDOW_REFRESH_MS = 30 * 86_400_000


def windowed(item, at_ms):
    """The stock as the venue holds it at `at_ms`: only the sessions from a few days before to two months after. The native venue parses its
    sessions on every bar and every order, so it is given a short stretch, moved forward as the run goes (`SessionWindows`). A stock that
    starts trading after `at_ms` (listed, spun off or renamed during the run) or stops before it gets the stretch at its own first or last
    session: the venue refuses a stock whose stretch is empty, for the rest of the run. One with no session in the run (listed after the
    stretch the run covers) has no bar to trade on and keeps its empty stretch."""
    sessions = item["stock"]["sessions"]
    if len(sessions):
        at_ms = min(max(at_ms, int(sessions.open_at[0])), int(sessions.close_at[-1]))
    return {**item, "stock": {**item["stock"], "sessions": sessions.between(at_ms - WINDOW_BEHIND_MS, at_ms + WINDOW_AHEAD_MS)}}


def session_windows(engine, config, strategy_box):
    """Moves each stock's sessions in the venue forward: from the first bar, every month of simulated time, the next two months' sessions are
    written to the venue's trading-conditions rules (the engine's own way to change them while it runs), before any bar needs them."""
    import json as _json

    from native.stock_execution import conditions
    from nautilus_trader.common import DataActor, DataActorConfig
    from nautilus_trader.model import ActorId
    stocks = [item for item in config["instruments"] if item["type"] == "equity" and "stock" in item]
    if not stocks:
        return None

    class SessionWindows(DataActor):
        def __init__(self):
            super().__init__(DataActorConfig(actor_id=ActorId("TALYX-SESSIONS")))
            self.next = None

        def on_start(self):
            for bar_type in strategy_box[0].bar_types:
                self.subscribe_bars(bar_type)

        def on_bar(self, bar):
            now = self.clock.timestamp_ns() // 1_000_000
            if self.next is not None and now < self.next:
                return
            self.next = now + WINDOW_REFRESH_MS
            for item in stocks:
                rules = conditions(windowed(item, now), raw=bool(item.get("executionRaw")))["rules"]
                self.cache.add("trading-conditions/rules/" + item["id"], _json.dumps(rules, allow_nan=False).encode())

    actor = SessionWindows()
    engine.add_actor(actor)
    return actor


def lean(config):
    """The configuration as the run record keeps it: every stock's daily sessions are in the sessions file, not repeated here."""
    copied = copy.deepcopy(config, sessions_kept(config))
    for item in copied.get("instruments", []):
        if isinstance(item.get("stock"), dict):
            item["stock"].pop("sessions", None)
    return copied


def run_sessions(config, frame):
    """Each stock's sessions from the first bar of the run to a week after its last: the native venue looks a session up for every bar and
    order, so the rest of the history would only be carried and parsed."""
    if not len(frame):
        return
    times = frame["ts_event"]
    first, last = int(times.min()) // 1_000_000, int(times.max()) // 1_000_000 + 7 * 86_400_000
    for item in config["instruments"]:
        if item.get("type") == "equity" and "stock" in item:
            item["stock"]["sessions"] = item["stock"]["sessions"].between(first, last)


def simulation_for(invocation, config, frame, strategy_box):
    """TALYX's cost model and fill timing for each venue, and the ledger the report reads."""
    from native.strategy_costs import Ledger, Prices, fill_model, funding_rates, simulation_module
    settings = invocation.get("runSettings") or {}
    costs, fill = settings.get("costs") or {}, settings.get("fill", "nextOpen")
    opens = {item["id"]: dict(zip((item["stock"]["sessions"].close_at * 1_000_000).tolist(), (item["stock"]["sessions"].open_at * 1_000_000).tolist(), strict=True))
             for item in config["instruments"] if item.get("type") == "equity" and "stock" in item}
    prices = Prices(frame, invocation.get("annualization"), opens)
    ledger = Ledger()
    rates = {}
    funding = invocation.get("fundingRates") or {}
    if funding.get("path"):
        import pandas as pd
        rates = funding_rates(pd.read_parquet(funding["path"]).to_dict("records"))
    for name, gaps in (funding.get("gaps") or {}).items():
        ledger.gaps[name].extend(gaps)
    kinds = {item["id"]: item["type"] for item in config["instruments"]}
    clock = lambda: strategy_box[0].clock.timestamp_ns()
    fees = {venue["name"]: float((venue.get("fees") or {}).get("taker") or 0) for venue in config["venues"]}
    margin = {item["id"]: float(item.get("marginInit", 1)) for item in config["instruments"]}

    def guard(instrument, order, price, quantity):
        """Why this order cannot be paid for: the fee on top of the margin (or the whole price for a cash buy) is more than the account has."""
        strategy = strategy_box[0]
        if getattr(order, "is_reduce_only", False):
            return None
        held = sum(float(str(position.signed_qty)) for position in strategy.cache.positions_open(instrument_id=instrument.id))
        buying = order.side.name == "BUY" if hasattr(order.side, "name") else str(order.side).endswith("BUY")
        if held and (held > 0) != buying:
            return None  # reduces what is held
        account = strategy.cache.account_for_venue(instrument.id.venue)
        currency = getattr(instrument, "settlement_currency", None) or instrument.quote_currency
        free = float(account.balance_free(currency).as_double())
        notional = price * quantity
        cash = str(account.account_type).endswith("CASH")
        need = (notional if cash else notional * margin.get(str(instrument.id), 1.0)) + notional * fees.get(str(instrument.id.venue), 0.0)
        return "Not enough balance for the fee" if free < need - 1e-9 and notional * fees.get(str(instrument.id.venue), 0.0) > 0 and free >= need - notional * fees.get(str(instrument.id.venue), 0.0) - 1e-9 else None
    simulation = {}
    for venue in config["venues"]:
        simulation[venue["name"]] = {"adaptive": not invocation.get("fillBars"), "latency": 1 if fill in ("nextOpen", "nextClose") else 0,
                                     "fill_model": fill_model(costs, fill, prices, ledger, clock, int(settings.get("limitThroughTicks") or 0), guard, settings.get("halted", "keep")),
                                     "modules": [simulation_module(costs, prices, ledger, rates, kinds)]}
    return simulation, ledger, prices


STREAM_NS = 365 * 86_400 * 10 ** 9


class RunStopped(Exception):
    """The Host asked this run to stop (it wrote `stop.request` in the output folder): what has run so far is kept."""


def progress_actor(engine, output, frame, strategy_box):
    """Every few percent of simulated time: how far the backtest is, and the trades so far (`output/progress.json`)."""
    import json as _json
    import os
    import time
    from pathlib import Path

    from nautilus_trader.common import DataActor, DataActorConfig
    from nautilus_trader.model import ActorId
    times = frame["ts_event"]
    start, end = int(times.min()), int(times.max())
    step = max((end - start) // 50, 1)
    path = Path(output) / "progress.json"

    class Progress(DataActor):
        """Driven by the bars the strategy sees, not by a timer: a timer would wake the venue between two bars and fill a waiting order
        off the bar grid."""

        def __init__(self):
            super().__init__(DataActorConfig(actor_id=ActorId("TALYX-PROGRESS")))
            self.next = start
            self.seen = 0
            self.looked = 0.0  # when (wall clock) the stop request was last looked for
            self.stopped = False
            self.first, self.curve = None, []

        def on_start(self):
            for bar_type in strategy_box[0].bar_types[:1]:
                self.subscribe_bars(bar_type)

        def on_bar(self, bar):
            self.seen += 1
            if self.stopped:
                return
            if self.seen % 50 == 0 or time.monotonic() - self.looked > .5:  # every 50 bars, and at least twice a second of a slow run
                self.looked = time.monotonic()
                if (Path(output) / "stop.request").exists():
                    # The strategy stops getting bars and the engine ends here, not after the rest of the data (an S&P run went on for
                    # minutes and was killed, losing what it had); the run then reports the part up to this moment.
                    self.stopped = True
                    strategy_box[0].stop()
                    self.shutdown_system("Stopped by the user")
                    return
            now = self.clock.timestamp_ns()
            if now < self.next:
                return
            self.next = now + step
            strategy = strategy_box[0]
            value = {"simulatedTo": now // 1_000_000, "value": round(min(1.0, (now - start) / max(end - start, 1)), 4),
                     "tradesSoFar": (len(self.cache.positions_closed()) + len(getattr(self.cache, "position_snapshots", lambda: [])())) if strategy is not None else 0}
            # The equity so far (the account's total, as a fraction of what it began with), one point a step, for a notebook's small chart.
            from native import strategy_equity
            captured = strategy_equity._CAPTURES.get(id(engine))
            if captured is not None:
                captured.read_ring()  # a bars-only run has no quotes or trades to trigger the capture, so the snapshots so far are read here
            rows = [row for row in captured.rows if row["valuation"] == "complete"] if captured is not None else []
            if rows:
                first = self.first if self.first is not None else rows[0]["equityNumeric"]
                self.first = first
                if first:
                    self.curve.append([now // 1_000_000, round(rows[-1]["equityNumeric"] / first - 1, 6)])
            value["equity"] = self.curve
            temporary = path.with_suffix(".pending")
            temporary.write_text(_json.dumps(value), encoding="utf-8")
            os.replace(temporary, path)

    actor = Progress()
    engine.add_actor(actor)
    return actor


def failure_context(engine, strategy, error, source):
    """Where a failing run was: the simulation time, the instrument the code was handling, the local values of the author's frame that
    raised, and what had run before it."""
    import traceback as _traceback
    from pathlib import Path
    root = Path(source).resolve()
    context = {}
    try:
        at = strategy.clock.timestamp_ns()
        context["simulatedAt"] = at // 1_000_000 if at else None  # the clock is 0 until the simulation has started
    except Exception:
        pass
    found, seen = None, set()
    current = error
    while current is not None and id(current) not in seen:
        seen.add(id(current))
        tb = current.__traceback__
        while tb is not None:
            filename = tb.tb_frame.f_code.co_filename
            if Path(filename).resolve().is_relative_to(root):
                found = tb.tb_frame
            tb = tb.tb_next
        current = current.__cause__ or current.__context__
    if found is not None:
        values = []
        for name, value in list(found.f_locals.items())[:40]:
            if name.startswith("__") or name == "self":
                continue
            try:
                text = repr(value)
            except Exception:
                text = f"<{type(value).__name__}>"
            values.append([name, text[:200]])
        context["locals"] = values
        for value in found.f_locals.values():
            identity = getattr(getattr(value, "bar_type", None), "instrument_id", None) or getattr(value, "instrument_id", None)
            if identity is not None:
                context["instrument"] = str(identity)
                break
        context["line"] = found.f_lineno
        context["function"] = found.f_code.co_name
        import linecache
        context["sourceLine"] = linecache.getline(found.f_code.co_filename, found.f_lineno).strip()[:300]
    try:
        context["tradesBefore"] = len(engine.cache.positions_closed())
        context["equityBefore"] = float(strategy.equity())
    except Exception:
        pass
    seen_snapshot, current = set(), error
    while current is not None and id(current) not in seen_snapshot:  # the moment of the first failure, when the code recorded it
        seen_snapshot.add(id(current))
        snapshot = getattr(current, "_talyx_snapshot", None)
        if snapshot:
            context.update(snapshot)
            break
        current = current.__cause__ or current.__context__
    if found is None:
        context.update(inEngine=True, locals=[], line=None)
    context["traceback"] = "".join(_traceback.format_exception_only(type(error), error)).strip()[:400]
    return context



def sliced(frame, invocation):
    """The bars a combination of a sweep or a walk-forward window reads: from `window.from` to `window.to` (milliseconds). Where the data starts later than
    the months it should warm up on, the run says so; the warm-up it had is written down."""
    window = invocation.get("window")
    if not window:
        return frame
    times = frame["ts_event"] // 1_000_000
    kept = frame[(times >= window["from"]) & (times <= window["to"])]
    if len(kept) and invocation["backtest"].get("tradeFrom") is not None:
        first = int(kept["ts_event"].min()) // 1_000_000
        if first > window["from"] + 5 * 86_400_000 and window.get("warmUpMonths"):
            import datetime
            invocation["warmUpNote"] = (f"warm-up from {datetime.datetime.fromtimestamp(first / 1000, datetime.UTC).date().isoformat()}, "
                                        f"shorter than {window['warmUpMonths']} months")
    return kept


def trim_work(work, cut, extra, trades, error=None):
    """A run that stopped (an error in the code, or the user) shows only what happened up to that moment: the engine goes on through the data without the
    strategy, so what it recorded after `cut` (milliseconds of simulated time) is flat and does not belong to the result. `error` is the line the log
    ends with when the code failed. Returns the trades that are kept."""
    if not cut:
        return trades
    for key in ("equity", "drawdown", "benchmark"):
        work[key] = [point for point in work.get(key) or [] if point[0] <= cut]
    trades = [trade for trade in trades if trade["entry"] <= cut]
    work["tradesTotal"] = len(trades)
    log = [line for line in extra.get("log") or [] if line["t"] <= cut]
    if error:
        log.append({"t": cut, "instrument": error.get("instrument"), "kind": "Error", "text": error["text"]})
    extra["log"] = log
    work["log"] = log[:1000]
    work["logTotal"] = len(log)
    for series in work.get("plots") or []:
        keep = [at for at, moment in enumerate(series["t"]) if moment <= cut]
        series["t"] = [series["t"][at] for at in keep]
        series["values"] = [series["values"][at] for at in keep]
    return trades


def run_one(invocation, publisher, config, title):
    import time
    marks = {"start": time.perf_counter()}

    def mark(name):
        now = time.perf_counter()
        marks[name] = round(marks.get(name, 0) + now - marks["last"], 3)
        marks["last"] = now
    marks["last"] = marks["start"]
    from native.python_projects import NativeCollector
    from native.strategy_options import validate
    from native.strategy_results import NativeBacktestOutput, collect, display_statistics
    stock_sessions(invocation, config)
    stock_metadata(config)
    validate(config)
    frame = sliced(read_inputs(invocation, config), invocation)
    run_sessions(config, frame)
    mark("input")
    streams = [{"role": "market_data", "dataType": "bars", "frame": frame}]
    from native.strategy_equity import observed_day_sampling
    equity_sampling = observed_day_sampling(config, frame, invocation["annualization"])
    if config.get("priceBasis", "raw") != "raw":
        stock_adjusted = config['priceBasis'] == 'split_dividend' and all(item['type'] == 'equity' and 'stock' in item for item in config['instruments'])
        if not stock_adjusted and (config.get("priceBasis") != "split" or not config.get("adjustedPriceSimulation")) or config.get("dataType") != "bars" or config.get("task", "event") != "event":
            raise ValueError("Choose raw prices, or explicitly enable a split-adjusted bar-price simulation.")
    effective_config = lean(config)
    config = copy.deepcopy(config, sessions_kept(config))
    if len(frame):
        config["sessionWindowFrom"] = int(frame["ts_event"].min()) // 1_000_000
    # Bound native fine-grained samples well below the million-entry ring even
    # across data gaps. Daily and final snapshots remain native defaults.
    times = frame["ts_init"]
    config["equitySamplingMs"] = max(1000, math.ceil((int(times.max()) - int(times.min())) / 10_000 / 1_000_000)) if len(times) else None
    strategy_box = [None]
    light = invocation.get("report") == "light"
    simulation, ledger, prices = simulation_for(invocation, config, frame, strategy_box)
    engine = make_engine(config, simulation=simulation)
    strategy = None
    from native.strategy_callbacks import NativeCallbackObserver
    callbacks = NativeCallbackObserver(invocation["source"], publisher)
    display = {item["nativeInstrument"]: item["symbol"] for item in invocation.get("display", [])}
    try:
        callbacks.install()
        from native.object_backtest import load_strategy
        from native.reference_prices import bind
        strategy = load_strategy(invocation, config)
        strategy._halted = (prices, ledger.closing)  # the strategy closes a halted name at its next price ("Halted names: close")
        strategy._ledger = ledger  # its funding and borrow entries tell the strategy when to read the balance again
        strategy_box[0] = strategy
        engine.add_strategy(strategy)
        bind(engine, strategy, config.get('referencePrices', []))
        progress = progress_actor(engine, publisher.root, frame, strategy_box)
        session_windows(engine, config, strategy_box)
        mark("build")
        from native.library_data import events as library_events
        from native.library_data import values as library_values
        library = library_values(invocation)
        # A year of simulated time at a time: the engine keeps what it is given, and ten years of 559 names' bars and library values took
        # 2 GB in it; it carries its state from one part to the next (Nautilus streaming), so the result is the same as in one go.
        times = frame["ts_init"].to_numpy()
        for start in range(int(times.min()), int(times.max()) + 1, STREAM_NS) if len(times) else ():
            part = frame[(times >= start) & (times < start + STREAM_NS)]
            engine.add_data(event_data(config, part))
            if library is not None:
                found = library[(library["ts_init"] >= start) & (library["ts_init"] < start + STREAM_NS)]
                if len(found):
                    engine.add_data(library_events(invocation, found))
            mark("bars")
            engine.run(streaming=True)
            engine.clear_data()
            mark("engine")
            if progress.stopped or (strategy and strategy.failure is not None):
                break
        engine.end()
        callbacks.check()
        if progress.stopped:
            raise RunStopped("Stopped by the user")
        if strategy and strategy.failure is not None:
            raise ValueError(str(strategy.failure)) from strategy.failure
        output = NativeBacktestOutput.from_engine(engine, equity_sampling)
        if strategy:
            output.reports.update(strategy.reports(engine, frame))
        from native.strategy_report import Report, write, write_light
        report = Report(engine, strategy, config, invocation, frame, output.reports, ledger, prices, invocation["annualization"], display)
        if light:
            # A combination of a sweep: its key numbers, equity and trades; the full report is made when a person runs that combination.
            work, trades = report.build_light()
            mark("report")
            write_light(publisher.root, work, trades)
            publisher.index["strategyReport"] = {"work": "strategy/work.json", "trades": "strategy/trades.parquet", "kind": work["kind"], "stats": work["stats"], "light": True}
            publisher.index["metrics"] = report.light_metrics(work)
            publisher.write()
            mark("write")
            return output.result
        work, results, trades = report.build()
        mark("report")
        write(publisher.root, work, results, trades, report.extra)
        publisher.index["strategyReport"] = {"work": "strategy/work.json", "results": "strategy/results.json", "trades": "strategy/trades.parquet",
                                             "kind": work["kind"], "stats": work["stats"], "missing": results["missing"]}
        collector = NativeCollector(invocation, publisher)
        collect(collector, title, output)
        publisher.index["units"][-1]["effectiveConfig"] = effective_config
        stream_conditions = [{"role": item["role"], "dataType": item["dataType"], "observations": len(item["frame"]),
            "firstEventNs": str(item["frame"].ts_event.iloc[0]) if len(item["frame"]) else None,
            "lastEventNs": str(item["frame"].ts_event.iloc[-1]) if len(item["frame"]) else None} for item in streams]
        publisher.index["units"][-1]["eventStreams"] = stream_conditions
        publisher.index["strategy"] = {"task": config.get("task", "event"), "dataType": config.get("dataType", "quotes"), "dataMeaning": config.get("dataMeaning"), "priceBasis": config.get("priceBasis", "raw"), "economicModels": config["venues"], "costModel": invocation.get("runSettings", {}).get("costs"), "fill": invocation.get("runSettings", {}).get("fill", "nextOpen"), "nativeStatistics": display_statistics(output.result)}
        publisher.index["evaluationConditions"] = {"assets": [item["id"] for item in config["instruments"]], "sample": [str(output.result["backtest_start"]), str(output.result["backtest_end"])], "observations": output.result["iterations"], "dataType": config.get("dataType", "quotes"), "priceBasis": config.get("priceBasis", "raw"), "annualization": f"{invocation['annualization']} days a year (the instruments' calendar)", "fees": [item.get("fees") for item in config["venues"]], "liquidity": [item.get("liquidityConsumption", True) for item in config["venues"]], "latencyNanoseconds": [item.get("latency", {}) for item in config["venues"]]}
        publisher.index['evaluationConditions']['stockExecution'] = [{'instrument': item['id'], 'materialVersion': item['stock']['materialVersion'],
            'commission': item['stockCommission'], 'orderSize': item['stock']['orderSize'], 'settlement': item['stock']['settlement'], 'taxes': item['stock']['taxes']}
            for item in config['instruments'] if item['type'] == 'equity' and 'stock' in item]
        publisher.index["evaluationConditions"]["eventStreams"] = stream_conditions
        publisher.index["units"][-1]["economicModels"] = lean({"instruments": [], "venues": config["venues"]})["venues"]
        publisher.write()
        mark("write")
        return output.result
    except Exception as run_error:
        publisher.index["failureContext"] = failure_context(engine, strategy, run_error, invocation["source"]) if strategy is not None else {}
        try:
            partial = NativeBacktestOutput.from_engine(engine, equity_sampling)
            if strategy:
                partial.reports.update(strategy.reports(engine, frame))
            try:
                from native.strategy_report import Report, write
                report = Report(engine, strategy, config, invocation, frame, partial.reports, ledger, prices, invocation["annualization"], display)
                context = publisher.index["failureContext"]
                # Measured up to the moment it stopped, by the same definitions as a finished run.
                work, results, trades = report.build(context.get("simulatedAt"))
                work["partial"] = True
                line = None if isinstance(run_error, RunStopped) else {"instrument": context.get("instrument"), "text": f"{type(run_error).__name__}: {str(run_error)[:200]}" + (f" at line {context['line']}" if context.get("line") else "")}
                trades = trim_work(work, context.get("simulatedAt"), report.extra, trades, line)
                if isinstance(run_error, RunStopped):
                    work["stoppedAt"] = publisher.index["failureContext"].get("simulatedAt")
                    work["checks"] = {}
                write(publisher.root, work, results, trades, report.extra)
                publisher.index["strategyReport"] = {"work": "strategy/work.json", "results": "strategy/results.json", "trades": "strategy/trades.parquet",
                                                     "kind": work["kind"], "stats": work["stats"], "missing": results["missing"], "partial": True}
            except Exception as report_error:
                publisher.index["partialReportError"] = str(report_error)
            collect(NativeCollector(invocation, publisher), title + " · completed events before failure", partial)
            publisher.index["units"][-1]["state"] = "partial"
            publisher.index["units"][-1]["error"] = str(run_error)
            publisher.index["units"][-1]["effectiveConfig"] = effective_config
            publisher.write()
        except Exception as report_error:
            publisher.index["partialReportError"] = str(report_error)
            publisher.write()
        raise
    finally:
        marks.pop("last", None)
        started = marks.pop("start")
        marks["run"] = round(time.perf_counter() - started, 3)
        publisher.index["runTimings"] = marks
        try:
            engine.dispose()
        finally:
            callbacks.close()