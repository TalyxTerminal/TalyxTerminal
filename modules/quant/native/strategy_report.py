"""A strategy run's report, built in the backtest process right after the engine: Work (key numbers, trades, the series under the chart,
the log, what the checks need) and Results (each section's numbers and figures), one set for the whole run.

Statistics are TALYX's (`native/metrics.py`): computed here once on the account's daily end-of-day returns,
with the capital in front, the instruments' calendar year and the run's risk-free rate, and stored with the run; every page reads them.
The engine's own figures (Nautilus's statistic classes) are kept apart as the engine's, never under a name the run's numbers use. Figures are Nautilus's tearsheet charts where it has them (equity with the benchmark,
drawdown, monthly, yearly, rolling Sharpe), relabelled by use: every trace carries `meta.role` and `meta.key`, every figure
`layout.meta.key`, and no colour; the rest are drawn here with the shared helpers. A section or figure that cannot be made says why.

Files, in the run's `output/strategy/`: `work.json`, `results.json`, and every row of the long tables in `trades.parquet`, `orders.parquet`, `log.parquet`
and, for a portfolio, `rebalances.parquet` and `decisions.parquet` (the pages read them a page at a time; Work and Results keep counts).
"""
from __future__ import annotations

import json
import math
from bisect import bisect_left, bisect_right
from contextlib import contextmanager
from pathlib import Path
from tempfile import TemporaryDirectory

import numpy as np
import pandas as pd
from native import metrics as M
from native.figures import (
    Missing,
    figure,
    iso,
    line,
    num,
    nums,
    read_out,
    reason_of,
    reference,
    trace,
)

from . import i18n

NS = 1_000_000
DAY_NS = 86_400_000_000_000
LOG_IN_WORK = 1000
BENCH_NONE = "No benchmark"
# Market stress periods a run is measured in when it covers them (UTC days, inclusive).
CRISES = {
    "stocks": (("2018 Q4 sell-off", "2018-10-03", "2018-12-24"), ("COVID crash", "2020-02-19", "2020-03-23"),
               ("2022 rate hikes", "2022-01-03", "2022-10-12"), ("2023 regional banks", "2023-03-08", "2023-03-17"),
               ("Aug 2024 yen carry unwind", "2024-07-16", "2024-08-05"), ("2025 tariff shock", "2025-02-19", "2025-04-08")),
    "crypto": (("COVID crash", "2020-03-08", "2020-03-13"), ("May 2021 crash", "2021-05-10", "2021-05-23"),
               ("LUNA collapse", "2022-05-05", "2022-05-18"), ("FTX collapse", "2022-11-06", "2022-11-21"),
               ("Aug 2024 unwind", "2024-08-01", "2024-08-05"), ("2025 tariff shock", "2025-01-21", "2025-04-08"),
               ("Oct 2025 deleveraging", "2025-10-06", "2025-11-21")),
}
ORDER_TYPES = {"MARKET": "Market", "LIMIT": "Limit", "STOP_MARKET": "Stop market", "STOP_LIMIT": "Stop limit", "MARKET_IF_TOUCHED": "Take profit",
               "LIMIT_IF_TOUCHED": "Take profit limit", "TRAILING_STOP_MARKET": "Trailing stop", "TRAILING_STOP_LIMIT": "Trailing stop limit",
               "MARKET_TO_LIMIT": "Market to limit"}
REASONS = (("INITIAL_MARGIN_EXCEEDS_FREE_BALANCE", "Not enough margin"), ("NOTIONAL_EXCEEDS_FREE_BALANCE", "Not enough balance"),
           ("exceeds free balance", "Not enough balance"))
STATUSES = {"FILLED": "Filled", "REJECTED": "Rejected", "CANCELED": "Canceled", "EXPIRED": "Expired", "PARTIALLY_FILLED": "Partly filled",
            "DENIED": "Rejected", "ACCEPTED": "Open", "INITIALIZED": "Open", "SUBMITTED": "Open", "TRIGGERED": "Triggered", "EMULATED": "Open",
            "RELEASED": "Open", "PENDING_UPDATE": "Open", "PENDING_CANCEL": "Open"}
AT_END = "Unfilled at end"
# TALYX's own stop at each position's liquidation price, moved and replaced as the position changes: counted apart from the strategy's orders.
LIQUIDATION_STOP = "Liquidation stop"
ORDER_STATES = ("Filled", "Partly filled", "Rejected", "Canceled", "Expired", AT_END)


def order_status(status, at_end):
    """An order's state in the orders table: the engine's, except that one the run's end left unfilled (sent on its last bar, or still working
    when the data ran out) is "Unfilled at end", not refused by the venue or open."""
    shown = STATUSES.get(str(status).upper().replace(" ", "_"), str(status).title())
    return AT_END if at_end or shown in ("Open", "Triggered") else shown


def is_liquidation_stop(row):
    return "exit:liquidation" in row["tags"]


def order_counts(rows):
    """How many orders the orders table has in each state, the states it has only: TALYX's liquidation stops under their own name, every other
    order by its state. The one count of rejected orders (the Trades key number's and the Orders section's)."""
    counts = {}
    for row in rows:
        name = LIQUIDATION_STOP if is_liquidation_stop(row) else row["status"]
        counts[name] = counts.get(name, 0) + 1
    order = {name: at for at, name in enumerate((*ORDER_STATES, LIQUIDATION_STOP))}
    return dict(sorted(counts.items(), key=lambda item: order.get(item[0], len(order))))


# ---------------------------------------------------------------------------------------------------------------- small helpers
def day_of_ns(values):
    """The UTC day a bar or an event belongs to: the day it ended in, where a bar that ends exactly at midnight ends the day before (so a 24/7 market's
    last hourly bar of Nov 6 is Nov 6's close, not Nov 7's)."""
    return pd.to_datetime(np.asarray(values, dtype="int64") - 1, unit="ns", utc=True).normalize()


def figures_in(value):
    """Every Plotly figure inside a section tree."""
    if isinstance(value, dict):
        if "data" in value and "layout" in value:
            yield value
            return
        for item in value.values():
            yield from figures_in(item)
    elif isinstance(value, list):
        for item in value:
            yield from figures_in(item)


def day(stamp_ms):
    """A date as the page writes it in a gap: "Jan 3" within one year, with the year when it is not this one's."""
    stamp = pd.Timestamp(stamp_ms, unit="ms", tz="UTC")
    return f"{stamp:%b} {stamp.day}, {stamp:%Y}"


def money(value):
    """A float from a Nautilus money value ("12.5 USDT", Money, a list of them summed), 0 for none."""
    if value is None or value is pd.NA or isinstance(value, float) and math.isnan(value):
        return 0.0
    if isinstance(value, (list, tuple)):
        return sum(money(item) for item in value)
    if hasattr(value, "as_double"):
        return float(value.as_double())
    text = str(value).strip()
    if text.startswith("["):
        return sum(money(part) for part in text.strip("[]").split(",") if part.strip())
    try:
        return float(text.split()[0]) if text else 0.0
    except ValueError:
        return 0.0


def clock_ns(value):
    """Native nanoseconds from an int, decimal text or a pandas timestamp; None for missing."""
    if value is None or value is pd.NaT or isinstance(value, float) and math.isnan(value):
        return None
    if isinstance(value, (int, np.integer)):
        return int(value) or None
    text = str(value)
    if text.isdigit():
        return int(text) or None
    try:
        return int(pd.Timestamp(value).value)
    except (TypeError, ValueError):
        return None


def ms(ns):
    return None if ns is None else int(ns) // NS


def day_ms(stamp):
    return int(pd.Timestamp(stamp).value // NS)


def series_points(series, digits=6):
    return [[day_ms(at), num(value, digits)] for at, value in series.items()]


def stat_of(statistic, returns, *extra):
    """A registered statistic's row values: {"value"}, or {"value": None, "why"} when it cannot be computed on these returns (left out,
    not zero, and the row says why)."""
    try:
        value = statistic.calculate_from_returns_with_benchmark(returns, *extra) if extra else statistic.calculate_from_returns(returns)
    except Exception as error:  # noqa: BLE001 - Nautilus raises what it likes; the row names it
        return {"value": None, "why": reason_of(error)}
    return {"value": num(value)}


def as_native(series):
    """{native ns: return} as Nautilus statistics take them."""
    return {int(pd.Timestamp(at).value): float(value) for at, value in series.items() if np.isfinite(value)}


def drawdown_of(equity):
    return equity / equity.cummax(skipna=False) - 1


def scored_stats(work):
    """The numbers a run is read by everywhere (Work, Results, the run list, the Agent): a held-out validation's are its held-out end's."""
    part = (work.get("heldOut") or {}).get("heldOut")
    return {key: value for key, value in part.items() if key not in ("beta", "from", "to")} if part else work["stats"]


def traded_value(fills):
    """The value bought and sold, in the price currency: |quantity × price| of every fill."""
    return float(sum(item["notional"] for item in fills))


def compounded_total(returns):
    return float(np.prod(1 + returns.to_numpy()) - 1) if len(returns) else 0.0


def label_native(fig, key, roles):
    """A Nautilus Plotly figure as plain JSON that says what each line is: colours, the template and the title go; `roles` is one
    (role, key) per trace in order (extra traces keep the last role)."""
    data = fig.to_plotly_json() if hasattr(fig, "to_plotly_json") else fig
    data = json.loads(json.dumps(data, default=_plain))
    traces = []
    for number, item in enumerate(data.get("data", [])):
        role, trace_key = roles[min(number, len(roles) - 1)]
        for drop in ("line", "marker", "fillcolor", "colorscale", "colorbar", "zmid", "reversescale", "showscale"):
            if drop in ("line", "marker") and isinstance(item.get(drop), dict):
                item[drop] = {name: value for name, value in item[drop].items() if "color" not in name}
                if not item[drop]:
                    item.pop(drop)
            elif drop not in ("line", "marker"):
                item.pop(drop, None)
        item["meta"] = {"role": role, "key": trace_key}
        traces.append(item)
    layout = {name: value for name, value in data.get("layout", {}).items() if name not in ("template", "title", "colorway", "paper_bgcolor", "plot_bgcolor", "font")}
    layout["meta"] = {"key": key}
    layout.setdefault("showlegend", False)
    layout["margin"] = {"l": 8, "r": 52, "t": 12, "b": 26}
    return {"data": traces, "layout": layout}


@contextmanager
def plain_templates():
    """Nautilus' tearsheet figures name Plotly's white and dark templates, which Plotly builds on first use (a third of a second) and copies
    into every figure, and `label_native` drops them again: while the tearsheet draws, the two names stand for empty templates."""
    import plotly.graph_objects as go
    import plotly.io as pio
    kept = {name: pio.templates._templates.get(name) for name in ("plotly_white", "plotly_dark")}
    for name in kept:
        pio.templates[name] = go.layout.Template()
    try:
        yield
    finally:
        pio.templates._templates.update(kept)


def _plain(value):
    if isinstance(value, np.ndarray):
        return value.tolist()
    if isinstance(value, (np.integer, np.floating)):
        return value.item()
    if hasattr(value, "isoformat"):
        return value.isoformat()
    return str(value)


def native_bdata(fig):
    """Plotly 6 packs arrays as base64 `bdata`; the display reads plain lists. Decode them in place."""
    import base64

    def walk(value):
        if isinstance(value, dict):
            if "bdata" in value and "dtype" in value:
                array = np.frombuffer(base64.b64decode(value["bdata"]), dtype=np.dtype(value["dtype"]))
                if "shape" in value:
                    array = array.reshape([int(part) for part in str(value["shape"]).split(",")])
                return [None if isinstance(item, float) and not math.isfinite(item) else item for item in array.tolist()] if array.ndim == 1 else [
                    [None if isinstance(cell, float) and not math.isfinite(cell) else cell for cell in row] for row in array.tolist()]
            return {key: walk(item) for key, item in value.items()}
        if isinstance(value, list):
            return [walk(item) for item in value]
        if isinstance(value, float) and not math.isfinite(value):
            return None
        return value
    return walk(fig)


# ---------------------------------------------------------------------------------------------------------------- the report
def price_text(value):
    """A fill price as the log reads it: two decimals from 1 up, four significant digits below (518.48, 0.06123), not the fill's full float."""
    try:
        number = float(value)
    except (TypeError, ValueError):
        return str(value)
    return f"{number:,.2f}" if abs(number) >= 1 else f"{number:.4g}"


def quantity_text(value):
    """A quantity without a float's tail: 9 shares, not 9.0; a fraction keeps up to six significant digits."""
    try:
        number = float(value)
    except (TypeError, ValueError):
        return str(value)
    return f"{number:,.0f}" if number == int(number) else f"{number:.6g}"


class Report:
    def __init__(self, engine, strategy, config, invocation, frame, reports, ledger, prices, annualization, display):
        self.engine, self.strategy, self.config, self.invocation = engine, strategy, config, invocation
        self.frame, self.reports, self.ledger, self.prices = frame, reports, ledger, prices
        self.A = int(annualization or 252)
        self.display = display  # native instrument id -> what a person calls it
        self.run_settings = invocation.get("runSettings") or {}
        self.kind = "single" if len(config["instruments"]) == 1 else "multi"
        self.crypto = all(item["type"] != "equity" for item in config["instruments"])
        self.missing = {}
        self.figures_failed = {}
        self.stopped = bool(invocation.get("stopped"))
        self.account_rows = []
        self.instruments = {item["id"]: item for item in config["instruments"]}
        self.rates = {}
        self.currency = (invocation.get("accounts") or {}).get("reportCurrency")
        self.trades_cache = []
        self.extra = {}
        self.trade_from = (invocation.get("backtest") or {}).get("tradeFrom")  # a run that read bars to warm up: it is measured from here
        self.cut_ns = None  # a run that stopped: everything is measured up to this moment (build sets it)

    # ------------------------------------------------------------------------------------------------ inputs
    def rate(self, unit, at):
        """Report-currency units per native unit at the given instant."""
        if unit == self.currency:
            return 1.0
        rates = self.rates.get(unit)
        index = -1 if rates is None else rates.index.searchsorted(at, side="right") - 1
        if index < 0:
            raise Missing(f"No known {unit}/{self.currency} rate at {at.isoformat()}.")
        return float(rates.iloc[index])

    def closing_rates(self, unit, days):
        if unit == self.currency:
            return 1.0
        ends = days + pd.Timedelta(days=1) - pd.Timedelta(nanoseconds=1)
        return pd.Series(self.rates[unit].reindex(ends, method="ffill").to_numpy(), index=days)

    def equity_series(self):
        """The accounts' equity at the end of each observed day, in the report currency: each account is in its own currency, converted by the
        run's frozen daily closes of that currency, and added."""
        table = self.reports.get("Account equity")
        if table is None or table.empty:
            raise Missing("The engine recorded no account equity.")
        spec = self.invocation.get("accounts") or {}
        first_currency = self.config["venues"][0]["balances"][0].split()[1]
        currency = spec.get("reportCurrency") or first_currency
        self.currency = currency
        rows = table.assign(at=pd.to_datetime(table["timestampNs"].map(int) - 1, unit="ns", utc=True), value=table["equityNumeric"].astype(float))
        days = day_of_ns(self.frame["ts_event"].map(int).to_numpy()).unique().sort_values()
        start = days[0] - pd.Timedelta(days=1)
        self.rates = {}
        for item in spec.get("conversions") or []:
            found = pd.read_parquet(item["path"]).sort_values("time")
            self.rates[item["currency"]] = pd.Series(found["close"].to_numpy(), index=pd.to_datetime(found["time"], unit="ms", utc=True)).groupby(level=0).last()
        capital = 0.0
        for venue in self.config["venues"]:
            for balance in venue["balances"]:
                amount, unit = balance.split()
                capital += float(amount) * self.rate(unit, days[0])
        accounts, parts = [], []
        for (account, unit), found in rows.groupby(["account", "currency"], sort=False):
            marks = found.sort_values("at").assign(day=found["at"].dt.normalize()).drop_duplicates("day", keep="last").set_index("day")
            daily = marks["value"].where(marks["valuation"] == "complete").reindex(days, method="ffill")
            converted = daily * self.closing_rates(unit, days)
            parts.append(converted)
            accounts.append({"account": str(account), "currency": str(unit), "equity": {str(day_ms(at)): num(value, 6) for at, value in daily.items()}})
        if not parts:
            raise Missing("The engine recorded no account equity in the report currency.")
        total = pd.concat(parts, axis=1).sum(axis=1, min_count=len(parts))
        daily = pd.concat([pd.Series([capital], index=[start]), total])
        if self.trade_from is not None:
            # Measured from the day it began to trade: the first point is the equity at the close of the day before (flat until then, so the capital).
            first = pd.Timestamp(int(self.trade_from), unit="ms", tz="UTC").normalize() - pd.Timedelta(days=1)
            daily = daily[daily.index >= first]
            capital = float(daily.iloc[0])
        if self.cut_ns is not None:
            # A run that stopped is measured to the day it stopped; the engine went on without the strategy after that.
            daily = daily[daily.index <= day_of_ns([self.cut_ns])[0]]
        self.currency, self.capital = currency, capital
        self.account_rows = accounts if len({item["account"] for item in accounts}) > 1 else []
        return daily

    def closes(self):
        """Close of every instrument at the end of each day (UTC), carried over days without a bar."""
        frame = self.frame
        table = pd.DataFrame({"at": day_of_ns(frame["ts_event"].map(int).to_numpy()),
                              "instrument": frame["instrument"].to_numpy(), "close": frame["close"].astype(float).to_numpy()})
        return table.pivot_table(index="at", columns="instrument", values="close", aggfunc="last").sort_index()

    def benchmark_members(self, closes):
        index = self.invocation.get("index")
        if not index:
            return closes.ffill().notna()
        from .index_data import eligible_times

        frame = self.frame
        times = frame["ts_event"].to_numpy(dtype="int64")
        last = pd.DataFrame({"day": day_of_ns(times), "instrument": frame["instrument"].to_numpy(), "at": times // 1_000_000})
        last = last.pivot_table(index="day", columns="instrument", values="at", aggfunc="max").reindex_like(closes)
        return pd.DataFrame({name: eligible_times(index, name, last[name].fillna(last.max(axis=1)).to_numpy(dtype="int64")) for name in closes}, index=closes.index)

    def first_opens(self):
        """Each instrument's first open in the run, the price the account could first have bought it at."""
        frame = self.frame.sort_values("ts_event", kind="stable")
        return frame.groupby("instrument", sort=False)["open"].first().astype(float)

    def benchmark_series(self, days):
        """The benchmark's level on each of `days` (the first is the account's starting point, the day before the first bar). Like the account
        it starts before the first day's return: a symbol from its close the day before (fetched a week early), the run's own names from their
        first open (they started at the first day's close, so the first day's move was missing from the benchmark)."""
        spec = self.invocation.get("benchmark") or {"kind": "hold"}
        kind = spec.get("kind")
        if kind == "none":
            raise Missing("No benchmark")
        if kind == "series":
            rows = spec.get("rows") or []
            if not rows:
                raise Missing(spec.get("unavailable") or f"Market gave no prices for {spec.get('label', 'the benchmark')}.")
            values = pd.Series([float(value) for _, value in rows], index=pd.to_datetime([int(at) for at, _ in rows], unit="ms", utc=True).normalize())
            values = values[~values.index.duplicated(keep="last")].sort_index()
            label = spec.get("label") or "Benchmark"
        elif kind == "equal":
            closes = self.closes()
            opens = self.first_opens()
            daily = closes.ffill().pct_change(fill_method=None)
            # the run's first day counts from the first open (a name that comes in later joins from its first close, as before)
            first_day = closes.index[0]
            for name in closes.columns:
                if pd.notna(closes.loc[first_day, name]) and name in opens.index:
                    daily.loc[first_day, name] = closes.loc[first_day, name] / opens[name] - 1
            daily = daily.where(self.benchmark_members(closes)).mean(axis=1, skipna=True).fillna(0.0)
            values = (1 + daily).cumprod()
            label = "Equal-weight members"
        else:
            closes = self.closes()
            first = self.first_opens().reindex(closes.columns)
            start = closes.index[0]
            available = self.benchmark_members(closes).loc[start] & first.notna()
            held = list(closes.columns[available])
            if not held:
                raise Missing("No index member had a price at the first close.")
            values = (closes[held].ffill() / first[held]).mean(axis=1)
            label = (self.display.get(held[0], held[0]) + " hold") if len(held) == 1 else f"Equal-weight hold of the {len(held)} names"
        if kind != "series" and len(days) and days[0] < values.index[0]:
            values = pd.concat([pd.Series([1.0], index=[days[0]]), values])  # the level before the first open, where the account starts
        values = values.reindex(values.index.union(days)).ffill().reindex(days)
        return values, label

    def risk_free(self, days):
        """The risk-free rate a day (a share), and how it was taken."""
        spec = self.invocation.get("riskFree") or {"basis": "zero" if self.crypto else "irx"}
        basis = spec.get("basis") or ("zero" if self.crypto else "irx")
        if basis == "zero":
            return pd.Series(0.0, index=days), {"basis": i18n.t("zero"), "label": i18n.t("zero")}
        if basis == "fixed":
            pct = float(spec.get("pct") or 0)
            return pd.Series(pct / 100 / self.A, index=days), {"basis": "fixed", "label": i18n.t("fixed {rate}% a year", rate=f'{pct:g}'), "meanPct": pct}
        rows = spec.get("rows") or []
        if not rows:
            return None, {"basis": "irx", "label": i18n.t("13-week T-bill"), "unavailable": spec.get("unavailable") or i18n.t("Market has no ^IRX closes for this period")}
        values = pd.Series([float(value) for _, value in rows], index=pd.to_datetime([int(at) for at, _ in rows], unit="ms", utc=True).normalize())
        values = values[~values.index.duplicated(keep="last")].sort_index()
        values = values.reindex(values.index.union(days)).ffill().reindex(days)
        return values / 100 / self.A, {"basis": "irx", "label": i18n.t("13-week T-bill (^IRX)"), "meanPct": num(values.mean(), 4) if values.notna().all() else None}

    # ------------------------------------------------------------------------------------------------ positions, fills, orders
    def trade_rows(self):
        """One row per round trip (a position from open to flat), with its exit reason, costs and how far it went against and for."""
        try:
            positions = self.engine.generate_positions_report()
        except Exception as error:
            raise Missing(f"Nautilus could not report positions: {reason_of(error)}") from error
        if positions is None or positions.empty:
            return []
        orders = {str(order.client_order_id): order for order in self.engine.cache.orders()}
        fills = self.fill_rows()
        cost_by_order = {}
        for row, (spread, impact) in zip(fills, self.order_costs(fills), strict=True):
            item = cost_by_order.setdefault(row["client_order_id"], [0.0, 0.0, 0.0])
            item[0] += spread
            item[1] += impact
            item[2] += row["commission"]
        funding = {}
        for entry in self.ledger.entries:
            if entry["kind"] in ("funding", "borrow"):
                funding.setdefault(entry["instrument"], []).append((entry["t"], entry["amount"], self.entry_cost(entry)))
        rows = []
        last_ns = self.prices_end()
        for number, record in enumerate(positions.reset_index().to_dict("records"), 1):
            name = str(record.get("instrument_id"))
            opened, closed = clock_ns(record.get("ts_opened")), clock_ns(record.get("ts_closed"))
            entry_price = float(record.get("avg_px_open") or 0)
            exit_price = float(record["avg_px_close"]) if closed and record.get("avg_px_close") not in (None, "") and pd.notna(record.get("avg_px_close")) else None
            quantity = float(record.get("peak_qty") or record.get("quantity") or 0)
            long = str(record.get("entry")).upper().endswith("BUY")
            pnl = money(record.get("realized_pnl"))
            mark = None
            if closed is None:
                mark = self.prices.close(name, last_ns)
                if mark is not None:
                    pnl += (mark - entry_price) * float(record.get("quantity") or quantity) * (1 if long else -1)
            ids = self.position_orders(record)
            spread = sum(cost_by_order.get(item, (0.0, 0.0))[0] for item in ids)
            impact = sum(cost_by_order.get(item, (0.0, 0.0))[1] for item in ids)
            fees = sum(cost_by_order[item][2] for item in ids if item in cost_by_order)
            carry_entries = [entry for entry in funding.get(name, ()) if opened is not None and opened <= entry[0] <= (closed or last_ns)]
            native_carry = sum(entry[1] for entry in carry_entries)
            carry = sum(entry[2] for entry in carry_entries)
            unit = self.instruments[name].get("settlementCurrency") or self.instruments[name]["currency"]
            rate = self.rate(unit, pd.Timestamp(closed or last_ns, unit="ns", tz="UTC"))
            mae, mfe, bars = self.excursion(name, opened, closed or last_ns, entry_price, long)
            notional = entry_price * quantity
            closing = orders.get(str(record.get("closing_order_id"))) if closed else None
            rows.append({"id": number, "instrument": self.display.get(name, name), "nativeInstrument": name, "side": "Long" if long else "Short",
                         "entry": ms(self.moment(record.get("opening_order_id"), opened)), "exit": ms(self.moment(record.get("closing_order_id"), closed) if closed else None), "entryPrice": num(entry_price, 10), "exitPrice": num(exit_price if exit_price is not None else None, 10),
                         "markPrice": num(mark, 10), "exitBy": self.exit_reason(closing) if closed else "open at the end", "qty": num(quantity, 10), "pnl": num(pnl * rate - carry, 6), "nativeFunding": num(native_carry, 6),
                         "ret": num((pnl - native_carry) / notional if notional else None), "fees": num(fees), "spread": num(spread), "impact": num(impact),
                         "funding": num(carry), "mae": mae, "mfe": mfe, "bars": bars,
                         "days": num(((closed or last_ns) - opened) / DAY_NS if opened else None, 3)})
        # the engine's own positions report has no funding or borrow in its realized P&L (TALYX charges them as cash adjustments): it
        # keeps its columns and gains the carry and the P&L after it, which is the trades table's.
        native = self.reports.get("Positions") if isinstance(self.reports, dict) else None
        if isinstance(native, pd.DataFrame) and len(native) == len(rows):
            native["funding"] = [row["nativeFunding"] for row in rows]
            native["realized_pnl_after_funding"] = [None if pd.isna(record.get("ts_closed")) or record.get("ts_closed") in (None, "") else num(money(record.get("realized_pnl")) - (row["nativeFunding"] or 0), 6)
                                                    for record, row in zip(positions.reset_index().to_dict("records"), rows, strict=True)]
        return rows

    def position_orders(self, record):
        events = record.get("events")
        if isinstance(events, str):
            try:
                events = json.loads(events.replace("'", '"'))
            except ValueError:
                events = []
        ids = {str(item.get("client_order_id")) for item in events or [] if isinstance(item, dict)}
        for key in ("opening_order_id", "closing_order_id"):
            if record.get(key):
                ids.add(str(record[key]))
        return ids

    def prices_end(self):
        return int(max(int(value) for value in self.frame["ts_event"])) if len(self.frame) else 0

    def excursion(self, name, opened, closed, entry, long):
        """Worst and best move against the entry price while the position was open (a share; MAE is ≤ 0), and the bars it lasted."""
        found = self.prices.by.get(name)
        if found is None or opened is None or not entry:
            return None, None, None
        start = int(np.searchsorted(found["times"], opened, side="left"))
        stop = int(np.searchsorted(found["times"], closed, side="right"))
        if stop <= start:
            return 0.0, 0.0, 0
        high, low = float(found["high"][start:stop].max()), float(found["low"][start:stop].min())
        if long:
            return num(min(0.0, low / entry - 1)), num(max(0.0, high / entry - 1)), stop - start
        return num(min(0.0, entry / high - 1) if high else None), num(max(0.0, entry / low - 1) if low else None), stop - start

    @staticmethod
    def exit_reason(order):
        """The exit a person gave (`tags=["exit:<why>"]`, or `self.close(reason=...)`), else what the closing order was."""
        if order is None:
            return "signal"
        for tag in order.tags or []:
            if str(tag).startswith("exit:"):
                return str(tag)[5:] or "signal"
        kind = str(order.order_type).rsplit(".", 1)[-1]
        return {"STOP_MARKET": "stop", "STOP_LIMIT": "stop", "TRAILING_STOP_MARKET": "trail", "TRAILING_STOP_LIMIT": "trail",
                "MARKET_IF_TOUCHED": "target", "LIMIT_IF_TOUCHED": "target", "LIMIT": "limit"}.get(kind, "signal")

    def moment(self, order_id, at):
        """When a fill at engine time `at` happened: the moment of its price (the fill model's), or `at` when the fill model did not price it."""
        return self.ledger.moments.get((str(order_id), at), at)

    def converted_closes(self, closes):
        if not self.rates:
            return closes
        return pd.DataFrame({name: closes[name] * self.closing_rates(self.instruments[name]["currency"], closes.index) for name in closes})

    def entry_cost(self, entry):
        return entry["amount"] * self.rate(entry["currency"], pd.Timestamp(entry["t"], unit="ns", tz="UTC"))

    def order_costs(self, fills):
        return [(spread * fill["quoteRate"], impact * fill["quoteRate"])
                for fill, (spread, impact) in zip(fills, self.ledger.order_costs(fills), strict=True)]

    def fill_rows(self):
        if hasattr(self, "_fills"):
            return self._fills
        try:
            table = self.engine.generate_fills_report()
        except Exception as error:  # was taken as no fills, so turnover, costs and trades came out as nothing traded
            raise Missing(f"Nautilus could not report the fills: {reason_of(error)}") from error
        rows = []
        if table is not None and not table.empty:
            for record in table.reset_index().to_dict("records"):
                name = str(record["instrument_id"])
                instrument = self.instruments[name]
                quote = instrument["currency"]
                t = self.moment(record["client_order_id"], clock_ns(record["ts_event"]))
                at = pd.Timestamp(t, unit="ns", tz="UTC")
                quote_rate = self.rate(quote, at)
                px, qty = float(record["last_px"]), float(record["last_qty"])
                raw_fee = record.get("commission")
                parts = str(raw_fee).split()
                unit = parts[-1] if len(parts) == 2 else quote
                fee_rate = px * quote_rate if unit == instrument.get("baseCurrency") else self.rate(unit, at)
                rows.append({"client_order_id": str(record.get("client_order_id")), "instrument": name,
                             "side": str(record.get("order_side")), "last_qty": qty, "last_px": px, "quoteRate": quote_rate,
                             "notional": abs(qty * px) * quote_rate, "t": t, "commissionNative": money(raw_fee),
                             "commissionCurrency": unit, "commission": money(raw_fee) * fee_rate})
        if self.cut_ns is not None:
            rows = [row for row in rows if row["t"] <= self.cut_ns]
        self._fills = rows
        return rows

    def order_rows(self):
        if hasattr(self, "_orders"):
            return self._orders
        rows = []
        filled_at = {}
        for fill in self.fill_rows():
            filled_at[fill["client_order_id"]] = ms(fill["t"])
        for order in self.engine.cache.orders():
            value = order.to_dict()
            identity = str(value.get("client_order_id"))
            reason = None
            for event in order.events():
                detail = event.to_dict()
                kind = str(detail.get("type", ""))
                if kind in ("OrderRejected", "OrderDenied", "OrderModifyRejected", "OrderCancelRejected"):
                    reason = str(detail.get("reason") or kind)
                elif kind == "OrderExpired" and reason is None:
                    reason = "Expired at its time in force"
            reason = self.ledger.unfilled.get(identity, reason) if value.get("status") != "FILLED" or identity in self.ledger.unfilled else reason
            for needle, words in REASONS:
                if reason and needle in reason:
                    reason = words
                    break
            status = order_status(value.get("status"), identity in self.ledger.at_end)
            if status == AT_END and reason is None:
                reason = "Still working when the run ended."
            kind = str(value.get("type"))
            price = value.get("avg_px") or value.get("price") or value.get("trigger_price")
            linked = value.get("linked_order_ids") or value.get("parent_order_id") or ("liquidation" if "exit:liquidation" in [str(tag) for tag in value.get("tags") or []] else None)
            rows.append({"id": identity, "time": ms(clock_ns(value.get("ts_init"))), "instrument": self.display.get(str(value.get("instrument_id")), str(value.get("instrument_id"))),
                         "side": "Buy" if str(value.get("side")) == "BUY" else "Sell", "type": ORDER_TYPES.get(kind, kind.replace("_", " ").title()),
                         "qty": num(value.get("quantity"), 10), "filled": num(value.get("filled_qty"), 10), "price": num(price, 10),
                         "trigger": num(value.get("trigger_price"), 10) if value.get("trigger_price") else None,
                         "tif": str(value.get("time_in_force") or ""), "status": status,
                         "filledAt": filled_at.get(identity),
                         "reason": reason, "linked": ", ".join(map(str, linked)) if isinstance(linked, list) else (str(linked) if linked else None),
                         "tags": [str(tag) for tag in value.get("tags") or []]})
        self._orders = rows
        return rows

    # ------------------------------------------------------------------------------------------------ exposure
    def holdings_by_day(self, days):
        """Signed quantity of each instrument at each day's end, from the fills."""
        fills = self.fill_rows()
        if not fills:
            return pd.DataFrame(index=days)
        table = pd.DataFrame(fills)
        table["signed"] = np.where(table["side"].str.upper().str.endswith("BUY"), table["last_qty"], -table["last_qty"])
        table["at"] = day_of_ns(table["t"].astype("int64").to_numpy())
        change = table.pivot_table(index="at", columns="instrument", values="signed", aggfunc="sum")
        held = change.reindex(change.index.union(days)).fillna(0.0).cumsum().reindex(days).ffill().fillna(0.0)
        return held

    # ------------------------------------------------------------------------------------------------ build
    def build(self, cut_ms=None):
        """The run's report; `cut_ms` is the moment a run that stopped stopped, and every number is measured up to it."""
        self.cut_ns = int(cut_ms) * NS if cut_ms else None
        equity = self.equity_series()
        returns = equity.pct_change(fill_method=None).iloc[1:]
        days = returns.index
        self.days = days
        drawdown = drawdown_of(equity)
        rf, rf_info = self.risk_free(days)
        excess = returns - rf if rf is not None and rf.notna().all() else None
        try:
            bench_level, bench_label = self.benchmark_series(equity.index)
            bench_returns = bench_level.pct_change().reindex(days)
            if len(bench_returns) and pd.isna(bench_returns.iloc[0]):
                bench_returns.iloc[0] = 0.0  # the benchmark starts at the first day's close, as the account does
            bench_equity = bench_level / bench_level.dropna().iloc[0] * self.capital
        except Missing as error:
            bench_level = bench_returns = bench_equity = None
            bench_label = str(error)
            if str(error) != BENCH_NONE:
                self.missing["benchmark"] = str(error)
        trades = self.trade_rows()
        if self.cut_ns is not None:
            trades = [item for item in trades if item["entry"] * NS <= self.cut_ns]
        self.trades_cache = trades
        fills = self.fill_rows()
        costs = self.cost_totals(fills, equity)
        stats = self.statistics(equity, rf, bench_returns, trades, costs, fills)
        # Orders the venue or the risk engine refused (not enough margin, no price): a run whose signals were not traded says how many.
        stats["rejected"] = order_counts(self.order_rows()).get("Rejected", 0)
        native = self.native_statistics(returns, excess, bench_returns)
        closes = self.closes().reindex(days).ffill()
        held = self.holdings_by_day(days)
        exposure = self.exposure(held, closes, equity.reindex(days), fills)
        orders = self.order_rows()
        log = self.log_lines(trades)
        work = {"kind": self.kind, "currency": self.currency, "capital": self.capital, "annualization": self.A, "riskFree": rf_info,
                "benchmarkLabel": bench_label, "stats": stats,
                "sharpeInputs": self.sharpe_inputs(excess),
                "equity": series_points(equity, 4), "drawdown": series_points(drawdown),
                "benchmark": series_points(bench_equity, 4) if bench_equity is not None else [],
                "tradesTotal": len(trades),
                "plots": self.plots(), "userStats": self.user_stats(), "builtBars": self.built_bars(),
                "log": log[:LOG_IN_WORK], "logTotal": len(log), "costs": costs, "notes": self.notes(stats),
                "checks": {} if self.stopped else self.check_inputs(trades, equity, returns, rf, costs, stats),
                "instruments": [{"id": item["id"], "symbol": self.display.get(item["id"], item["id"]), "type": item["type"]} for item in self.config["instruments"]],
                "exposure": exposure["points"]}
        # The daily returns the statistics were computed from, unrounded, so anything the page or a test derives from them agrees with the engine's own.
        work["dailyReturns"] = [[day_ms(at), num(value, 12), num(bench_returns.get(at), 12) if bench_returns is not None else None] for at, value in returns.items()]
        work["dailyRiskFree"] = series_points(rf, 12) if rf is not None else None
        if self.account_rows:
            work["accounts"] = self.account_rows
        self.extra = {"orders": orders, "log": log, "fills": [
            {"instrument": f["instrument"], "time": ms(f["t"]), "side": f["side"].lower(), "price": f["last_px"], "quantity": f["last_qty"]}
            for f in fills]}
        if self.kind == "multi":
            decisions = self.decision_rows()
            rebalances = self.rebalances(orders, held, closes, equity.reindex(days), decisions)
            work["multi"] = {**self.multi(held, closes, equity.reindex(days), trades, fills, orders), "rebalancesTotal": len(rebalances)}
            self.extra.update(rebalances=rebalances, decisions=decisions)
        if self.invocation.get("heldOutFrom") is not None:
            work["heldOut"] = self.held_out(equity, rf, trades, fills, int(self.invocation["heldOutFrom"]), bench_returns)
            work["stats"] = scored_stats(work)
        results = self.results(work, equity, returns, excess, bench_returns, bench_level, bench_label, drawdown, trades, native, exposure, closes, held, rf)
        return work, results, trades

    def build_light(self):
        """What a combination of a sweep keeps: the key numbers, the equity (and the benchmark's) and the trades; no pictures, sections, log or checks."""
        equity = self.equity_series()
        returns = equity.pct_change(fill_method=None).iloc[1:]
        days = returns.index
        self.days = days
        rf, rf_info = self.risk_free(days)
        excess = returns - rf if rf is not None and rf.notna().all() else None
        bench_equity = bench_returns = None
        try:
            bench_level, _ = self.benchmark_series(equity.index)
            bench_returns = bench_level.pct_change().reindex(days)
            if len(bench_returns) and pd.isna(bench_returns.iloc[0]):
                bench_returns.iloc[0] = 0.0
            bench_equity = bench_level / bench_level.dropna().iloc[0] * self.capital
        except Missing:
            pass
        trades = self.trade_rows()
        fills = self.fill_rows()
        costs = self.cost_totals(fills, equity)
        stats = self.statistics(equity, rf, bench_returns, trades, costs, fills)
        # Orders the venue or the risk engine refused (not enough margin, no price): a run whose signals were not traded says how many.
        stats["rejected"] = order_counts(self.order_rows()).get("Rejected", 0)
        work = {"kind": self.kind, "currency": self.currency, "capital": self.capital, "annualization": self.A, "riskFree": rf_info, "stats": stats, "light": True,
                "sharpeInputs": self.sharpe_inputs(excess), "equity": series_points(equity, 4),
                "benchmark": series_points(bench_equity, 4) if bench_equity is not None else [], "tradesTotal": len(trades), "costs": costs,
                "notes": self.notes(stats), "checks": {},
                # The rate a day the run's statistics took (None: unavailable), so a walk-forward window is scored against the same rate.
                "dailyRiskFree": series_points(rf, 12) if rf is not None else None}
        return work, trades

    @staticmethod
    def light_metrics(work):
        """The headline numbers the sweep's table and ranking read from a combination."""
        stats = work["stats"]
        bench = work["benchmark"]
        out = {key: stats.get(key) for key in ("totalReturn", "cagr", "sharpe", "maxDrawdown", "trades", "winRate", "profitFactor") if stats.get(key) is not None}
        if len(bench) > 1 and bench[0][1]:
            out["benchmarkReturn"] = bench[-1][1] / bench[0][1] - 1
        return out

    def notes(self, stats):
        """What the page says under the key numbers: no trades, and gaps in the bars."""
        notes = []
        if not stats["trades"]:
            rejected = stats["rejected"]
            decisions = self.decisions()
            if rejected:
                notes.append(f"No trades: {rejected} orders rejected")
            elif decisions is not None and len(decisions) and (decisions["state"] == "skipped").all():
                # A preset that skipped every rebalance says why where the page shows the key numbers, not only in its table.
                reason = str(decisions["reason"].iloc[0])
                notes.append(f"No trades: all {len(decisions)} rebalances were skipped. {reason[:160]}{'…' if len(reason) > 160 else ''} (see {decisions.attrs['table']})")
            else:
                notes.append("No trades. The code has not placed an order yet.")
        for name, found in self.prices.by.items():
            times = found["times"]
            if len(times) < 3:
                continue
            step = int(np.median(np.diff(times)))
            if step >= DAY_NS:
                continue
            gaps = np.diff(times)
            for index in np.nonzero(gaps > step * 1.5)[0]:
                missing = int(round(gaps[index] / step)) - 1
                start, end = int(times[index]) + step, int(times[index + 1]) - step
                stamp = lambda ns: pd.Timestamp(ns, unit="ns", tz="UTC")
                notes.append(f"{stamp(start):%b} {stamp(start).day}, {stamp(start):%Y %H:%M}–{stamp(end):%H:%M} UTC: {missing} bar{'s' if missing != 1 else ''} without trades"
                             + (f" ({self.display.get(name, name)})" if len(self.prices.by) > 1 else ""))
        if self.invocation.get("warmUpNote"):
            notes.append(self.invocation["warmUpNote"])
        return notes[:20]

    # ------------------------------------------------------------------------------------------------ statistics
    def statistics(self, equity, rf, bench, trades, costs, fills, traded=None):
        """The run's key numbers, by the one definition each in metrics.run_statistics; every place that shows them reads these."""
        closed = [item["pnl"] for item in trades if item["exit"] is not None and item["pnl"] is not None]
        found = M.run_statistics(equity.to_numpy(), self.A, rf.to_numpy() if rf is not None else None, pnls=closed, trades=len(trades),
                                 traded=traded_value(fills) if traded is None else traded, costs=costs["total"] if costs else None,
                                 benchmark=bench.to_numpy() if bench is not None else None)
        return {key: num(value) if isinstance(value, float) else value for key, value in found.items()}

    def held_out(self, equity, rf, trades, fills, start_ms, bench=None):
        """A held-out validation: the same run, scored before and from the held-out start (positions carry across it), each part by the same
        definitions as a whole run, its costs and turnover included."""
        cut = pd.Timestamp(start_ms, unit="ms", tz="UTC")
        position = int(np.searchsorted(equity.index, cut))
        daily_cost = self.daily_costs(equity.index[1:])
        fill_days = day_of_ns([item["t"] for item in fills]) if fills else pd.DatetimeIndex([], tz="UTC")
        values = np.array([item["notional"] for item in fills], dtype=float)

        def part(level, active, closed):
            if len(level) < 2:
                return None
            days = level.index[1:]
            inside = (fill_days > level.index[0]) & (fill_days <= level.index[-1])
            out = self.statistics(level, rf.reindex(days) if rf is not None else None, bench.reindex(days) if bench is not None else None,
                                  active, {"total": float(daily_cost.reindex(days).fillna(0.0).sum())}, [], traded=float(values[inside].sum()) if len(values) else 0.0)
            out["openAtEnd"] = len(active) - len(closed)
            flow = level.pct_change(fill_method=None).iloc[1:]
            market = bench.reindex(days).dropna() if bench is not None else None
            beta = None
            if market is not None and len(market) > 2 and market.var() > 0:
                joined = pd.concat([flow, market], axis=1).dropna()
                beta = num(joined.iloc[:, 0].cov(joined.iloc[:, 1]) / joined.iloc[:, 1].var())
            return {**out, "beta": beta, "from": int(level.index[0].timestamp() * 1000), "to": int(level.index[-1].timestamp() * 1000)}
        before, after = equity.iloc[:position], equity.iloc[max(position - 1, 0):]
        early = [item for item in trades if item["entry"] < start_ms]
        late = [item for item in trades if item["exit"] is None or item["exit"] >= start_ms]
        inside = part(before, [{**item, "pnl": item["pnl"] if item["exit"] is not None and item["exit"] < start_ms else None, "exit": item["exit"] if item["exit"] is not None and item["exit"] < start_ms else None} for item in early],
                      [item for item in early if item["exit"] is not None and item["exit"] < start_ms])
        outside = part(after, late, [item for item in late if item["exit"] is not None])
        ratio = num(outside["sharpe"] / inside["sharpe"]) if inside and outside and inside["sharpe"] and inside["sharpe"] > 0 and outside["sharpe"] is not None else None
        return {"from": start_ms, "inSample": inside, "heldOut": outside, "ratio": ratio}

    def sharpe_inputs(self, series):
        found = M.moments(series.to_numpy() if series is not None and series.notna().all() else [])
        return {**found, "perPeriod": num(found["perPeriod"], 8), "skew": num(found["skew"]), "kurtosis": num(found["kurtosis"])}

    def native_statistics(self, returns, excess, bench):
        """Nautilus's 22 default statistics as the engine reported them, the 14 it has but does not register, registered with this calendar,
        and the analyzer's statistics against the benchmark."""
        import nautilus_trader.analysis as A
        values = as_native(returns)
        rows = []
        result = self.engine.get_result()
        for group, found in (("Engine's own returns (252 days a year, every calendar day, no risk-free rate)", result.stats_returns), ("Engine general", result.stats_general)):
            rows.extend({"group": group, "name": key, "value": num(value)} for key, value in (found or {}).items())
        for currency, found in (result.stats_pnls or {}).items():
            rows.extend({"group": f"Engine PnL ({currency})", "name": key, "value": num(value)} for key, value in found.items())
        registered = [A.CAGR(self.A), A.CalmarRatio(self.A), A.MaxDrawdown(), A.ValueAtRisk(), A.ExpectedShortfall(), A.UlcerIndex(), A.OmegaRatio(),
                      A.ReturnsVolatility(self.A), A.SharpeRatio(self.A), A.SortinoRatio(self.A)]
        for statistic in registered:
            needs_rate = isinstance(statistic, (A.SharpeRatio, A.SortinoRatio))
            inputs = excess if needs_rate else returns
            value = stat_of(statistic, as_native(inputs)) if inputs is not None and inputs.notna().all() else {"value": None}
            rows.append({"group": f"Registered ({self.A} days)", "name": statistic.name, **value})
        against = {}
        if bench is not None and returns.notna().all():
            bench_values = as_native(bench.fillna(0.0))
            analyzer = A.PortfolioAnalyzer()
            rate = float(excess.index.size and (returns - excess).mean()) if excess is not None else 0.0
            rates = (A.Alpha(self.A, rate), A.TreynorRatio(self.A, rate)) if excess is not None else ()
            for statistic in (*rates, A.BetaRatio(), A.InformationRatio(self.A), A.TrackingError(self.A), A.UpCaptureRatio(self.A), A.DownCaptureRatio(self.A)):
                analyzer.register_statistic(statistic)
            for at, value in values.items():
                analyzer.add_return(at, value)
            try:
                against = {key: num(value, 10) for key, value in analyzer.get_performance_stats_returns_vs_benchmark(bench_values).items()}
            except Exception as error:
                self.missing["againstBenchmark"] = reason_of(error)
            joined = pd.concat([returns, bench], axis=1).dropna()
            if len(joined) > 2:
                against["Correlation"] = num(np.corrcoef(joined.iloc[:, 0], joined.iloc[:, 1])[0, 1])
            rows.extend({"group": "Against the benchmark", "name": key, "value": value} for key, value in against.items())
        return {"rows": rows, "againstBenchmark": against}

    # ------------------------------------------------------------------------------------------------ costs
    def cost_totals(self, fills, equity):
        fees = sum(item["commission"] for item in fills)
        spread_impact = self.order_costs(fills)
        spread = sum(item[0] for item in spread_impact)
        impact = sum(item[1] for item in spread_impact)
        funding = sum(self.entry_cost(entry) for entry in self.ledger.entries if entry["kind"] == "funding")
        borrow = sum(self.entry_cost(entry) for entry in self.ledger.entries if entry["kind"] == "borrow")
        incomplete = [{"instrument": self.display.get(name, name), "from": ms(gap[0]), "to": ms(gap[1]), "reason": gap[2] if len(gap) > 2 else None}
                      for name, gaps in self.ledger.gaps.items() for gap in gaps]
        # Each part is written to 6 decimals, and the total and gross are made from the written parts, so the page's own numbers add up.
        parts = [round(value, 6) for value in (fees, spread, impact, funding, borrow)]
        total = sum(parts)
        net = round(float(equity.iloc[-1] - equity.iloc[0]), 6)
        return {"fees": parts[0], "spread": parts[1], "impact": parts[2], "funding": parts[3], "borrow": parts[4], "total": num(total),
                "gross": num(net + total), "net": num(net), "incomplete": incomplete,
                "fundingIncluded": any(entry["kind"] == "funding" for entry in self.ledger.entries) or bool(self.invocation.get("fundingRates"))}

    # ------------------------------------------------------------------------------------------------ the checks' raw numbers
    def check_inputs(self, trades, equity, returns, rf, costs, stats):
        closed = [item for item in trades if item["exit"] is not None and item["pnl"] is not None]
        pnls = np.array([item["pnl"] for item in closed], dtype=float)
        out = {}
        net = float(pnls.sum()) if len(pnls) else 0.0
        if self.kind == "single":
            top = np.sort(pnls)[::-1][:5]
            out["top"] = {"value": num(top.sum() / net) if net > 0 else None, "count": int(min(5, len(pnls))), "by": "trades",
                          "reason": None if net > 0 else "No net profit to share out"}
        else:
            by = {}
            for item in trades:
                by[item["instrument"]] = by.get(item["instrument"], 0.0) + (item["pnl"] or 0.0)
            total = sum(by.values())
            best = sorted(by.values(), reverse=True)[:5]
            out["top"] = {"value": num(sum(best) / total) if total > 0 else None, "count": len(best), "by": "names",
                          "reason": None if total > 0 else "No net profit to share out"}
        if len(pnls) >= 5:
            found = M.shuffled_drawdown(pnls, self.capital)
            out["shuffle"] = {"actual": num(found["actual"]), "p95": num(found["p95"]), "trades": len(pnls), "shuffles": M.SHUFFLES}
        else:
            out["shuffle"] = {"actual": None, "p95": None, "trades": len(pnls), "reason": "Fewer than 5 closed trades"}
        extra = pd.Series(0.0, index=returns.index)
        if costs["total"]:
            daily_cost = self.daily_costs(returns.index)
            extra = daily_cost / equity.shift(1).reindex(returns.index)
        doubled = returns - extra.fillna(0.0)
        # The same run with every cost taken a second time out of each day's return, scored by the same definitions (and the same risk-free rate) as the run.
        level = pd.concat([equity.iloc[:1], equity.iloc[0] * (1 + doubled).cumprod(skipna=False)])
        again = self.statistics(level, rf, None, trades, None, [])
        out["costsDoubled"] = {"cagr": again["cagr"], "sharpe": again["sharpe"], "cagrNow": stats["cagr"], "sharpeNow": stats["sharpe"]}
        return out

    def daily_costs(self, days):
        fills = self.fill_rows()
        amounts = []
        for row, (spread, impact) in zip(fills, self.order_costs(fills), strict=True):
            amounts.append((row["t"], row["commission"] + spread + impact))
        amounts.extend((entry["t"], self.entry_cost(entry)) for entry in self.ledger.entries if entry["kind"] in ("funding", "borrow"))
        if not amounts:
            return pd.Series(0.0, index=days)
        table = pd.DataFrame(amounts, columns=["t", "amount"])
        table["at"] = day_of_ns(table["t"].astype("int64").to_numpy())
        return table.groupby("at")["amount"].sum().reindex(days).fillna(0.0)

    # ------------------------------------------------------------------------------------------------ own series and log
    def plots(self):
        import talyx_quant as q
        return [{"name": item["name"], "pane": item["pane"], "style": item["style"], "t": list(item["t"]), "values": [M.finite(value) for value in item["values"]]}
                for item in q._plots.values()]

    def built_bars(self):
        import talyx_quant as q
        return [{"period": period, "from": source} for period, source in q._built.items()]

    def user_stats(self):
        import talyx_quant as q
        return {key: num(value, 8) for key, value in q._stats.items()}

    def log_lines(self, trades):
        import talyx_quant as q
        lines = [{**line, "instrument": self.display.get(line["instrument"], line["instrument"])} for line in q._log]
        for order in self.order_rows():
            # An order canceled as the code or TALYX replaced it is routine and has no line; one refused, expired, cut short (halted, volume cap)
            # or still unfilled when the run ended has.
            if order["status"] in ("Rejected", "Expired", AT_END) or order["reason"] and order["status"] != "Filled":
                # The stop TALYX keeps at a position's liquidation price is moved and replaced as the position changes: its own kind, which the log
                # leaves out unless asked for (a filled one is the trade's "Liquidated" line).
                own = is_liquidation_stop(order)
                lines.append({"t": order["time"], "instrument": order["instrument"], "kind": LIQUIDATION_STOP if own else "Not filled",
                              "text": f"{order['side']} {quantity_text(order['qty'])} {order['type'].lower()}" + (f" at {price_text(order['trigger'])}" if own and order["trigger"] else "")
                              + f": {order['reason'] or order['status']}"})
        for item in trades:
            lines.append({"t": item["entry"], "instrument": item["instrument"], "kind": "Trade", "trade": item["id"],
                          "text": i18n.t("{side} {quantity} at {price}", side=i18n.t("Long") if item["side"] == "Long" else i18n.t("Short"), quantity=quantity_text(item['qty']), price=price_text(item['entryPrice']))})
            if item["exit"] is not None:
                lines.append({"t": item["exit"], "instrument": item["instrument"], "kind": "Stop" if item["exitBy"] in ("stop", "trail", "liquidation") else "Trade", "trade": item["id"],
                              "text": (f"Liquidated at {price_text(item['exitPrice'])}, P&L {item['pnl']:.2f}" if item["exitBy"] == "liquidation" else
                                       f"Exit ({item['exitBy']}) at {price_text(item['exitPrice'])}, P&L {item['pnl']:.2f}") if item["pnl"] is not None else f"Exit ({item['exitBy']})"})
        funding = [entry for entry in self.ledger.entries if entry["kind"] == "funding"]
        for entry in funding:
            wording = (i18n.mark("Funding {rate}%: paid {amount} {currency}") if entry["amount"] > 0
                       else i18n.mark("Funding {rate}%: received {amount} {currency}"))
            lines.append({"t": ms(entry["t"]), "instrument": self.display.get(entry["instrument"], entry["instrument"]), "kind": "Funding",
                          "text": i18n.t(wording, rate=f"{entry['rate'] * 100:.4f}", amount=f"{abs(entry['amount']):.2f}", currency=entry["currency"])})
        lines = [line for line in lines if line.get("t") is not None]
        lines.sort(key=lambda line: line["t"])
        return lines

    # ------------------------------------------------------------------------------------------------ many instruments
    def exposure(self, held, closes, equity, fills):
        if held.empty:
            zero = pd.Series(0.0, index=closes.index)
            return {"gross": zero, "net": zero, "long": zero, "short": zero, "turnover": zero, "legs": {}, "points": []}
        closes = self.converted_closes(closes)
        value = held.reindex(columns=closes.columns, fill_value=0.0) * closes.fillna(0.0)
        long = value.clip(lower=0).sum(axis=1)
        short = value.clip(upper=0).sum(axis=1)
        gross, net = (long - short) / equity, (long + short) / equity
        # The run's turnover over the last 30 days: the value of the fills (a position opened and closed the same day included) ÷ 2 ÷ equity, a year.
        traded = pd.Series([item["notional"] for item in fills], index=day_of_ns([item["t"] for item in fills]), dtype=float) if fills else pd.Series(dtype=float)
        traded = traded.groupby(level=0).sum().reindex(equity.index).fillna(0.0)
        turnover = (traded / 2 / equity).rolling(30, min_periods=10).mean() * self.A
        # Each leg on its own: the day's return of what was held at the last close, longs and shorts apart (the shorts as the names' own return, not the gain on the short).
        step = closes.pct_change().replace([np.inf, -np.inf], np.nan).fillna(0.0)
        before = value.shift(1).fillna(0.0)
        legs = {}
        for name, weights in (("long", before.clip(lower=0)), ("short", (-before).clip(lower=0))):
            total = weights.sum(axis=1).replace(0.0, np.nan)
            legs[name] = (1 + ((weights * step).sum(axis=1) / total).fillna(0.0)).cumprod()
        return {"gross": gross, "net": net, "long": long / equity, "short": short / equity, "turnover": turnover, "legs": legs,
                "points": [[day_ms(at), num(g), num(n)] for at, g, n in zip(gross.index, gross, net, strict=True)]}

    def multi(self, held, closes, equity, trades, fills, orders):
        closes = self.converted_closes(closes)
        table = pd.DataFrame(fills) if fills else pd.DataFrame(columns=["t", "last_qty", "last_px", "instrument", "client_order_id"])
        turnover, unfilled = [], []
        if not table.empty:
            table["at"] = day_of_ns(table["t"].astype("int64").to_numpy())
            traded = table["notional"].groupby(table["at"]).sum()
            turnover = [[day_ms(at), num(value / float(equity.get(at, np.nan)) if pd.notna(equity.get(at, np.nan)) else None)] for at, value in traded.items()]
        missed = pd.Series([pd.Timestamp(item["time"] - 1, unit="ms", tz="UTC").normalize() for item in orders if item["status"] not in ("Filled", "Open", AT_END)], dtype="datetime64[ns, UTC]")
        if len(missed):
            unfilled = [[day_ms(at), int(count)] for at, count in missed.value_counts().sort_index().items()]
        last = closes.index[-1] if len(closes.index) else None
        holdings = []
        if last is not None and not held.empty:
            row = held.loc[last]
            open_trades = {item["nativeInstrument"]: item for item in trades if item["exit"] is None}
            for name, quantity in row.items():
                if not quantity:
                    continue
                price = closes.loc[last].get(name)
                trade = open_trades.get(name)
                holdings.append({"name": self.display.get(name, name), "nativeInstrument": name, "sector": None,
                                 "weight": num(quantity * price / float(equity.iloc[-1])) if pd.notna(price) else None,
                                 "pnl": trade["pnl"] if trade else None, "days": trade["days"] if trade else None})
            holdings.sort(key=lambda item: -abs(item["weight"] or 0))
        by = {}
        for item in trades:
            entry = by.setdefault(item["nativeInstrument"], {"name": item["instrument"], "nativeInstrument": item["nativeInstrument"], "sector": None,
                                                            "pnl": 0.0, "fees": 0.0, "trades": 0})
            entry["pnl"] += item["pnl"] or 0.0
            entry["fees"] += (item["fees"] or 0.0) + (item["spread"] or 0.0) + (item["impact"] or 0.0) + (item["funding"] or 0.0)
            entry["trades"] += 1
        final_weights = {row["nativeInstrument"]: row["weight"] for row in holdings}
        ranked = sorted(by.values(), key=lambda entry: entry["pnl"], reverse=True)
        for entry in ranked:
            entry["pnl"], entry["fees"] = num(entry["pnl"]), num(entry["fees"])
            entry["gross"] = num((entry["pnl"] or 0) + (entry["fees"] or 0))
            entry["weight"] = final_weights.get(entry["nativeInstrument"], 0)
        return {"turnover": turnover, "unfilled": unfilled, "holdings": holdings[:200], "holdingsTotal": len(holdings),
                "contributions": {"top": ranked[:10], "bottom": list(reversed(ranked[-10:])) if len(ranked) > 10 else [], "all": len(ranked)},
                "byInstrument": ranked}

    def rebalances(self, orders, held, closes, equity, decisions):
        """Each rebalance: what the code aimed for (`rebalance_to`, or the ranking of a preset), the weights held when its orders had filled, and
        the orders that did not fill with why."""
        import talyx_quant as q
        closes = self.converted_closes(closes)
        calls = [{"t": item["t"], "targets": item["targets"]} for item in q._rebalances]
        if not calls and self.strategy is not None:
            reported = self.strategy.reports(self.engine, self.frame)
            ranking = reported.get("Signal ranking") if isinstance(reported, dict) else None
            if ranking is not None and not ranking.empty:
                for moment, rows in ranking.groupby("ts_available"):
                    calls.append({"t": ms(int(moment)), "targets": {row.instrument: float(row.weight) for row in rows.itertuples()},
                                  "ranks": {row.instrument: int(row.rank) for row in rows.itertuples()}, "values": {row.instrument: num(row.value, 6) for row in rows.itertuples()}})
        out = []
        starts = [call["t"] for call in calls]
        timed = sorted((row for row in orders if row["time"] is not None), key=lambda row: row["time"])
        times = [row["time"] for row in timed]
        for position, call in enumerate(calls):
            # A preset's ranking is dated when its signal became known (after the close); its orders go in on the next bar. So a rebalance's orders are
            # those from its moment up to the next rebalance, not only those within a second of it (which left every weight at 0.00% "Filled").
            end = starts[position + 1] - 1000 if position + 1 < len(starts) else None
            mine = timed[bisect_right(times, call["t"] - 1000):bisect_left(times, end) if end is not None else len(timed)]
            filled = [row["filledAt"] for row in mine if row["filledAt"]]
            when = pd.Timestamp((max(filled) if filled else call["t"]) - 1, unit="ms", tz="UTC").normalize()
            later = held.index[held.index >= when]
            day_at = later[0] if len(later) else (held.index[-1] if len(held.index) else None)
            actual = {}
            if day_at is not None and not held.empty and day_at in closes.index and float(equity.get(day_at, np.nan)) > 0:
                for name, quantity in held.loc[day_at].items():
                    price = closes.loc[day_at].get(name)
                    if quantity and pd.notna(price):
                        actual[self.display.get(name, name)] = num(quantity * price / float(equity[day_at]), 6)
            targets = {self.display.get(name, name): num(weight, 6) for name, weight in call["targets"].items()}
            ranks = {self.display.get(name, name): rank for name, rank in (call.get("ranks") or {}).items()}
            values = {self.display.get(name, name): value for name, value in (call.get("values") or {}).items()}
            out.append({"at": call["t"], "targets": targets, "ranks": ranks, "values": values, "actual": {name: actual.get(name, 0.0) for name in {*targets, *actual}},
                        "orders": [{"name": row["instrument"], "side": row["side"], "qty": row["qty"], "filled": row["filled"], "price": row["price"], "status": row["status"],
                                    "reason": row["reason"]} for row in mine],
                        "count": len(mine), "unfilled": [{"name": row["instrument"], "reason": row["reason"] or row["status"]} for row in mine if row["status"] != "Filled"],
                        "skipped": next((item["reason"] for item in decisions if item["state"] == "skipped" and abs(item["t"] - call["t"]) < 1000), None)})
        return out

    def decisions(self):
        """A preset's rebalance decisions (Signal or Weight rebalances), or None for code that keeps none."""
        reported = self.strategy.reports(self.engine, self.frame) if self.strategy is not None else {}
        if not isinstance(reported, dict):
            return None
        for table in ("Signal rebalances", "Weight rebalances"):
            if reported.get(table) is not None:
                found = reported[table]
                found.attrs["table"] = table
                return found
        return None

    def decision_rows(self):
        """A preset's decision at each rebalance (rebalanced, or skipped and why); none for code that keeps none."""
        decisions = self.decisions()
        if decisions is None or decisions.empty:
            return []
        return [{"t": ms(int(row["ts_available"])), "state": row["state"], "reason": row["reason"]} for row in decisions.to_dict("records")]

    # ------------------------------------------------------------------------------------------------ Results
    def results(self, work, equity, returns, excess, bench_returns, bench_level, bench_label, drawdown, trades, native, exposure, closes, held, rf):
        sections = {}

        def section(name, builder):
            try:
                sections[name] = builder()
            except Exception as error:  # one section that cannot be made says why; the others still show
                self.missing[name] = reason_of(error)

        section("summary", lambda: {**self.summary(work, returns, excess, rf, bench_returns, bench_label, native), "userStats": work["userStats"]})
        section("equity", lambda: self.equity_section(equity, returns, bench_returns, bench_label, drawdown))
        section("periods", lambda: self.periods(returns, bench_returns, bench_label))
        section("rolling", lambda: self.rolling(returns, excess, bench_returns))
        section("longShort", lambda: self.long_short(trades, exposure, bench_level, work["stats"]["turnover"]))
        section("trades", lambda: self.trades_section(trades))
        section("orders", lambda: self.orders_section())
        section("account", lambda: self.account_section(equity, exposure))
        if self.kind == "multi":
            section("sectors", lambda: self.sectors_section(work))
        section("style", lambda: self.style_section(returns, bench_returns, bench_label))
        section("costs", lambda: self.costs_section(work))
        section("crisis", lambda: self.crisis(equity, returns, bench_returns))
        for found in figures_in(sections):
            read_out(found)
        return {"sections": sections, "missing": dict(self.missing), "figuresFailed": dict(self.figures_failed)}

    def native_figure(self, key, roles, make):
        """A Nautilus tearsheet figure, relabelled; when Nautilus cannot draw it, the reason goes in its place."""
        try:
            with plain_templates():
                figure = make()
            return native_bdata(label_native(figure, key, roles))
        except Exception as error:
            self.figures_failed[key] = reason_of(error)
            return {"key": key, "unavailable": reason_of(error)}

    def summary(self, work, returns, excess, rf, bench, bench_label, native):
        stats = work["stats"]
        if work.get("heldOut") and work["heldOut"]["heldOut"]:
            # A held-out validation's table is the held-out end, like its numbers.
            start = pd.Timestamp(work["heldOut"]["heldOut"]["from"], unit="ms", tz="UTC")
            returns, bench = returns[returns.index > start], bench[bench.index > start] if bench is not None else None
            excess, rf = excess[excess.index > start] if excess is not None else None, rf[rf.index > start] if rf is not None else None
        rows = self.return_rows(returns, excess, stats)
        bench_rows = self.return_rows(bench.dropna(), (bench - rf).reindex(bench.dropna().index) if rf is not None and rf.notna().all() else None, None) if bench is not None else None
        against = native["againstBenchmark"]
        names = {"alpha": "Alpha", "beta": "Beta", "trackingError": "Tracking Error", "informationRatio": "Information Ratio", "treynor": "Treynor",
                 "upCapture": "Up Capture", "downCapture": "Down Capture", "correlation": "Correlation"}
        mapped = {key: next((value for name, value in against.items() if name.startswith(prefix)), None) for key, prefix in names.items()}
        return {"tiles": {key: stats.get(key) for key in ("totalReturn", "cagr", "maxDrawdown", "sharpe", "overBenchmark", "costsAYear", "trades", "winRate")},
                "returnStatistics": [{"name": name, "run": value, "benchmark": (bench_rows or {}).get(name)} for name, value in rows.items()],
                "benchmarkLabel": bench_label, "againstBenchmark": mapped, "engineAgainstBenchmark": mapped, "engine": native["rows"],
                "riskFree": work["riskFree"], "annualization": self.A}

    def return_rows(self, returns, excess, stats):
        """The return statistics table. The run's column is the run's stored numbers; the benchmark's (`stats` None) is its own returns by the same
        definitions and the same risk-free rate, and the same 60-day minimum."""
        if returns is None or not len(returns):
            return {}
        values = returns.to_numpy()
        shape = self.sharpe_inputs(excess if excess is not None else returns)
        if stats is None:
            equity = np.concatenate([[1.0], np.cumprod(1 + values)])
            rate = (returns - excess).to_numpy() if excess is not None else None
            grow, drawdown = M.cagr(values, self.A), M.equity_drawdown(equity)
            short = len(values) < M.RUN_MINIMUM
            stats = {"totalReturn": num(equity[-1] - 1), "cagr": None if short else num(grow), "maxDrawdown": num(drawdown),
                     "volatility": None if short else num(M.volatility(values, self.A)), "calmar": None if short else num(M.calmar(grow, drawdown)),
                     "sharpe": None if short or rate is None else num(M.sharpe(values, self.A, rate)),
                     "sortino": None if short or rate is None else num(M.sortino(values, self.A, rate)),
                     "probabilisticSharpe": None if short or rate is None else num(M.probabilistic_sharpe(shape["perPeriod"], shape["observations"], shape["skew"], shape["kurtosis"]))}
        var, cvar = M.value_at_risk(values) if returns.notna().all() else (None, None)
        return {"Total return": stats["totalReturn"], "CAGR": stats["cagr"], "Volatility": stats["volatility"], "Sharpe": stats["sharpe"], "Sortino": stats["sortino"],
                "Calmar": stats["calmar"], "Probabilistic Sharpe": stats["probabilisticSharpe"], "Max drawdown": stats["maxDrawdown"],
                "Skew": shape["skew"], "Excess kurtosis": num(shape["kurtosis"] - 3) if shape["kurtosis"] is not None else None,
                "Daily VaR 95%": num(var), "Daily CVaR 95%": num(cvar), "Best day": num(values.max()), "Worst day": num(values.min()),
                "Positive days": num((values > 0).mean()) if returns.notna().all() else None, "Days": len(values)}

    def equity_section(self, equity, returns, bench, bench_label, drawdown):
        from nautilus_trader.analysis import tearsheet
        x = iso(equity.index)
        if equity.isna().any():
            path = equity / equity.iloc[0] - 1
            traces = [line(x, nums(path.to_numpy()), "main", "equity", "This run")]
            if bench is not None:
                market = (1 + bench).cumprod() - 1
                traces.append(line(iso(market.index), nums(market.to_numpy()), "benchmark", "benchmark", bench_label))
            return {"figures": {"equity": figure("strategy.equity", traces, yaxis={"tickformat": ".0%"}),
                                "drawdown": figure("strategy.drawdown", [line(x, nums(drawdown.to_numpy()), "drawdown", "drawdown", "Drawdown")],
                                                   yaxis={"tickformat": ".0%"})}, "drawdowns": [], "x": len(x)}
        figures = {
            "equity": self.native_figure("strategy.equity", [("main", "equity"), ("benchmark", "benchmark")],
                                         lambda: tearsheet.create_equity_curve(returns, title="Equity", benchmark_returns=bench.fillna(0.0) if bench is not None else None, benchmark_name=bench_label)),
            "drawdown": self.native_figure("strategy.drawdown", [("drawdown", "drawdown")], lambda: tearsheet.create_drawdown_chart(returns, title="Drawdown")),
        }
        if bench is not None and "data" in figures["drawdown"]:
            # The benchmark's own fall under the run's (the draft draws both), from the same first day.
            level = (1 + bench.fillna(0.0)).cumprod()
            # from the same start as the run: its starting level (1) is a high too
            fall = pd.Series(M.drawdown_path(level, 1.0), index=level.index)
            figures["drawdown"]["data"].insert(0, trace("scatter", "benchmark", "benchmark", mode="lines", x=iso(level.index), y=nums(fall.to_numpy() * 100, 3), name=bench_label))
        return {"figures": figures, "drawdowns": M.drawdown_episodes(equity, limit=10), "x": len(x)}

    def periods(self, returns, bench, bench_label):
        from nautilus_trader.analysis import tearsheet
        def period_return(group):
            return (1 + group).prod(skipna=False) - 1
        monthly = returns.groupby([returns.index.year, returns.index.month]).agg(period_return)
        yearly = returns.groupby(returns.index.year).agg(period_return)
        bench_yearly = ((1 + bench.dropna()).groupby(bench.dropna().index.year).prod() - 1) if bench is not None else pd.Series(dtype=float)
        years = sorted(set(yearly.index))
        table = [{"year": int(year), "months": [num(monthly.get((year, month))) if (year, month) in monthly.index else None for month in range(1, 13)],
                  "total": num(yearly.get(year)), "benchmark": num(bench_yearly.get(year)) if year in bench_yearly.index else None} for year in years]
        yearly_figure = self.native_figure("strategy.yearly", [("updown", "run")], lambda: tearsheet.create_yearly_returns(returns, title="Yearly returns"))
        if "data" in yearly_figure and len(bench_yearly):
            yearly_figure["data"].append(trace("bar", "benchmark", "benchmark", x=[str(year) for year in bench_yearly.index], y=nums(bench_yearly.to_numpy() * 100, 4),
                                               name=bench_label))
        return {"figures": {"monthly": self.native_figure("strategy.monthly", [("updown", "monthly")], lambda: tearsheet.create_monthly_returns_heatmap(returns, title="Monthly returns")),
                            "yearly": yearly_figure},
                "table": table, "benchmarkLabel": bench_label}

    def rolling(self, returns, excess, bench):
        from nautilus_trader.analysis import tearsheet
        window = max(20, round(self.A / 4))
        previous = tearsheet.TRADING_DAYS_PER_YEAR
        tearsheet.TRADING_DAYS_PER_YEAR = self.A  # the instruments' calendar, not 252 for everything
        try:
            sharpe = self.native_figure("strategy.rollingSharpe", [("main", "sharpe"), ("benchmark", "zero")],
                                        lambda: tearsheet.create_rolling_sharpe(excess, window=window, title="Rolling Sharpe")) if excess is not None else {"unavailable": "Missing risk-free observations"}
        finally:
            tearsheet.TRADING_DAYS_PER_YEAR = previous
        x = iso(returns.index)
        volatility = returns.rolling(window).std() * math.sqrt(self.A)
        traces = [line(x, nums(volatility.to_numpy()), "main", "volatility", "This run")]
        figures = {"sharpe": sharpe}
        if bench is not None:
            market = bench.rolling(window).std() * math.sqrt(self.A)
            traces.append(line(x, nums(market.to_numpy()), "benchmark", "benchmark", "Benchmark"))
            joined = pd.concat([returns, bench], axis=1)
            beta = joined.iloc[:, 0].rolling(window).cov(joined.iloc[:, 1]) / joined.iloc[:, 1].rolling(window).var()
            figures["beta"] = figure("strategy.rollingBeta", [line(x, nums(beta.to_numpy()), "main", "beta", "Beta"), reference(x, [1.0] * len(x), "one", "1")],
                                     yaxis={"title": {"text": "Beta"}})  # Axis title discarded by figure().  # i18n: ignore
        else:
            figures["beta"] = {"key": "strategy.rollingBeta", "unavailable": self.missing.get("benchmark", "No benchmark")}
        figures["volatility"] = figure("strategy.rollingVolatility", traces, yaxis={"title": {"text": "Volatility, a year"}, "tickformat": ".0%"})  # Axis title discarded by figure().  # i18n: ignore
        return {"window": window, "longerThanRun": len(returns) <= window, "figures": figures}

    def long_short(self, trades, exposure, bench_level, turnover_a_year):
        def side(rows):
            closed = [item for item in rows if item["exit"] is not None and item["pnl"] is not None]
            wins = [item["pnl"] for item in closed if item["pnl"] > 0]
            losses = [item["pnl"] for item in closed if item["pnl"] < 0]
            return {"trades": len(closed), "pnl": num(sum(item["pnl"] for item in closed)), "winRate": num(len(wins) / len(closed)) if closed else None,
                    "profitFactor": num(sum(wins) / -sum(losses)) if losses else None, "averageReturn": num(np.mean([item["ret"] for item in closed if item["ret"] is not None])) if closed else None,
                    "averageDays": num(np.mean([item["days"] for item in closed if item["days"] is not None]), 3) if closed else None}
        x = iso(exposure["gross"].index)
        # A day's exposure holds until the next change: steps, gross a faint area under the net line (bars a day could not be read). A net that changes
        # often (a strategy that turns from long to short every few days) is a comb of steps that reads as a block: the days stay as a faint stepped area
        # and the 30-day means of gross and net are the lines read over them.
        gross, net = exposure["gross"], exposure["net"]
        if int((net.diff().abs() > 1e-9).sum()) > len(net) / 10:
            exposure_traces = [line(x, nums(net.to_numpy()), "detail", "netDaily", "Net, daily", fill="tozeroy", line={"shape": "hv"}),
                               line(x, nums(gross.rolling(30, min_periods=1).mean().to_numpy()), "compare", "gross", "Gross, 30d"),
                               line(x, nums(net.rolling(30, min_periods=1).mean().to_numpy()), "main", "net", "Net, 30d")]
        else:
            exposure_traces = [line(x, nums(gross.to_numpy()), "group", "gross", "Gross", fill="tozeroy", line={"shape": "hv"}),
                               line(x, nums(net.to_numpy()), "main", "net", "Net", line={"shape": "hv"})]
        exposure_figure = figure("strategy.exposure", exposure_traces, yaxis={"tickformat": ".0%"})
        turnover_figure = figure("strategy.turnover", [line(x, nums(exposure["turnover"].to_numpy()), "main", "turnover", "Turnover")], yaxis={"tickformat": ".0f", "ticksuffix": "×"})
        figures = {"exposure": exposure_figure, "turnover": turnover_figure}
        if self.kind == "multi" and exposure["legs"]:
            legs = [line(x, nums(exposure["legs"]["long"].to_numpy() - 1), "updown", "long", "Long leg"),
                    line(x, nums(exposure["legs"]["short"].to_numpy() - 1), "updown", "short", "Short leg")]
            legs[0]["meta"]["side"], legs[1]["meta"]["side"] = "up", "down"   # a leg is a direction: the user's up and down colours
            if bench_level is not None:
                market = bench_level.reindex(exposure["gross"].index)
                first = market.dropna()
                if len(first) and first.iloc[0]:
                    legs.insert(0, line(x, nums((market / first.iloc[0]).to_numpy() - 1), "benchmark", "market", "Market"))
            figures["legs"] = figure("strategy.legs", legs, yaxis={"tickformat": ".0%"})
        return {"long": side([item for item in trades if item["side"] == "Long"]), "short": side([item for item in trades if item["side"] == "Short"]),
                "all": side(trades), "exposure": {"averageGross": num(exposure["gross"].mean()), "averageNet": num(exposure["net"].mean()),
                                                  "turnoverAYear": turnover_a_year},
                "figures": figures}

    def trades_section(self, trades):
        closed = [item for item in trades if item["exit"] is not None and item["ret"] is not None]
        if not closed:
            raise Missing("No closed trades in this run.")
        returns = np.array([item["ret"] for item in closed])
        wins = [item for item in closed if item["pnl"] > 0]
        losses = [item for item in closed if item["pnl"] < 0]  # a loss is below zero (metrics.closed_trades)
        shared = M.closed_trades([item["pnl"] for item in closed])
        by_exit = {}
        for item in closed:
            entry = by_exit.setdefault(item["exitBy"], {"exit": item["exitBy"], "trades": 0, "pnl": 0.0, "wins": 0, "bars": [], "mae": [], "mfe": []})
            entry["trades"] += 1
            entry["pnl"] += item["pnl"]
            entry["wins"] += item["pnl"] > 0
            for key in ("bars", "mae", "mfe"):
                if item[key] is not None:
                    entry[key].append(item[key])
        exits = [{"exit": entry["exit"], "trades": entry["trades"], "pnl": num(entry["pnl"]), "winRate": num(entry["wins"] / entry["trades"]),
                  "averageBars": num(np.mean(entry["bars"]), 2) if entry["bars"] else None, "averageMae": num(np.mean(entry["mae"])) if entry["mae"] else None,
                  "averageMfe": num(np.mean(entry["mfe"])) if entry["mfe"] else None} for entry in by_exit.values()]
        best, worst = max(closed, key=lambda item: item["pnl"]), min(closed, key=lambda item: item["pnl"])
        statistics = {"averageWin": num(shared["averageWin"]), "averageLoss": num(shared["averageLoss"]),
                      "expectancy": num(np.mean([item["pnl"] for item in closed])), "largestWin": {"pnl": best["pnl"], "trade": best["id"]},
                      "largestLoss": {"pnl": worst["pnl"], "trade": worst["id"]}, "mostLossesInARow": shared["longestLosing"],
                      "barsHeldWinners": num(np.mean([item["bars"] for item in wins if item["bars"] is not None]), 2) if wins else None,
                      "barsHeldLosers": num(np.mean([item["bars"] for item in losses if item["bars"] is not None]), 2) if losses else None,
                      "averageMae": num(np.mean([item["mae"] for item in closed if item["mae"] is not None])),
                      "averageMfe": num(np.mean([item["mfe"] for item in closed if item["mfe"] is not None]))}
        def bars_of(values, key, name, index, of, edges=None):
            """Counts per bin as bars (the chart paints a histogram trace of this kind without a colour of its own)."""
            counts, edges = np.histogram(np.asarray(values, dtype=float), bins=edges if edges is not None else 40)
            return trace("bar", "group", key, x=nums((edges[:-1] + edges[1:]) / 2, 4), y=[int(count) for count in counts], width=float(edges[1] - edges[0]),
                         name=name, index=index, of=of, opacity=.75 if of == 2 and key != "returns" else 1, hovertemplate="%{x:.3g}<br>Trades %{y}<extra></extra>")
        winner_bars, loser_bars = [item["bars"] for item in wins if item["bars"] is not None], [item["bars"] for item in losses if item["bars"] is not None]
        edges, held_axis = whole_bins(winner_bars + loser_bars)
        figures = {
            "returns": figure("strategy.tradeReturns", [bars_of(returns * 100, "returns", "Trades", 1, 2)],
                              xaxis={"title": {"text": "Return of each trade, %"}}, bargap=0),  # Axis title discarded by figure().  # i18n: ignore
            "held": figure("strategy.barsHeld", [bars_of(winner_bars, "winners", "Winners", 0, 2, edges=edges), bars_of(loser_bars, "losers", "Losers", 1, 2, edges=edges)],
                           barmode="overlay", bargap=0, showlegend=True, xaxis={"title": {"text": "Bars held"}, **held_axis}),  # Axis title discarded by figure().  # i18n: ignore
            "mae": figure("strategy.mae", [trace("scatter", "updown", "mae", mode="markers", x=[num((item["mae"] or 0) * 100, 4) for item in closed], y=nums(returns * 100, 4),
                                                 text=[f"#{item['id']} {item['instrument']} {item['side'].lower()}" for item in closed], customdata=[item["exitBy"] for item in closed], name="MAE")],
                          xaxis={"title": {"text": "Worst move against, %"}}, yaxis={"title": {"text": "Trade return, %"}}),  # Axis title discarded by figure().  # i18n: ignore
            "mfe": figure("strategy.mfe", [trace("scatter", "updown", "mfe", mode="markers", x=[num((item["mfe"] or 0) * 100, 4) for item in closed], y=nums(returns * 100, 4),
                                                 text=[f"#{item['id']} {item['instrument']} {item['side'].lower()}" for item in closed], customdata=[item["exitBy"] for item in closed], name="MFE")],
                          xaxis={"title": {"text": "Best move for, %"}}, yaxis={"title": {"text": "Trade return, %"}}),  # Axis title discarded by figure().  # i18n: ignore
        }
        return {"statistics": statistics, "byExit": exits, "count": len(closed), "figures": figures}

    def orders_section(self):
        """How many orders in each state; the rows are in orders.parquet, read a page at a time."""
        rows = self.order_rows()
        return {"counts": order_counts(rows), "total": len(rows)}

    def account_section(self, equity, exposure):
        points = []
        for venue in self.engine.list_venues():
            try:
                report = self.engine.generate_account_report(venue=venue)
            except Exception as error:
                self.missing["account"] = reason_of(error)
                continue
            if report is None or report.empty:
                continue
            report = report.assign(total=report["total"].map(money), locked=report["locked"].map(money))
            report.index = pd.to_datetime(report.index, utc=True) - pd.Timedelta(nanoseconds=1)
            for unit, balances in report.groupby("currency"):
                if str(unit) != self.currency and str(unit) not in self.rates:
                    continue  # spot base assets are marked in equity; these lines show account cash
                daily = balances[["total", "locked"]].resample("1D").last()
                daily = daily.reindex(daily.index.union(self.days)).ffill().reindex(self.days)
                points.append(daily.mul(self.closing_rates(str(unit), self.days), axis=0))
        if not points:
            raise Missing("Nautilus reported no account balances.")
        daily = pd.concat(points, axis=1).T.groupby(level=0).sum().T.reindex(self.days).ffill()
        leverage = exposure["gross"].reindex(self.days)
        settings = self.run_settings.get("exchange") or {}
        # on a margin account Nautilus's "locked" is its own maintenance figure scaled down by the leverage (4.75 on 19,000 held at 20x),
        # which drew "Margin used" near 0 while the account was in many times over. The margin the positions hold is their value over the
        # leverage; the maintenance margin is the rate times their value, as liquidation counts it. A cash account's locked is cash held for orders.
        margined = any(str(self.engine.cache.account_for_venue(venue).account_type).endswith("MARGIN") for venue in self.engine.list_venues())
        value = leverage * pd.Series(equity).reindex(self.days)
        held = value / float(settings.get("leverage") or 1) if margined else daily["locked"]
        upkeep = value * float(settings.get("maintenancePct", 0.5)) / 100
        rows = [{"t": day_ms(at), "balance": num(row["total"], 4), "marginUsed": num(held.get(at), 4), "equity": num(equity.get(at), 4),
                 "leverage": num(leverage.get(at)), **({"marginMaint": num(upkeep.get(at), 4)} if margined else {})} for at, row in daily.iterrows()]
        events = [{"time": row["time"], "event": "Rejected for margin", "detail": f"{row['side']} {row['qty']} {row['instrument']}: {row['reason']}"} for row in self.order_rows()
                  if row["reason"] and "margin" in row["reason"].lower()]
        for item in self.trades_cache:
            if item["exitBy"] == "liquidation":
                events.append({"time": item["exit"], "event": "Liquidated", "detail": i18n.t("{side} {quantity} {instrument} closed at {price}", side=i18n.t("Long") if item["side"] == "Long" else i18n.t("Short"), quantity=item['qty'], instrument=item['instrument'], price=item['exitPrice'])})
        if settings.get("liquidation") and self.crypto:
            # Equity over the maintenance margin (the rate times the notional held), at each day's end: under 1.2 the account is near its liquidation.
            maintenance = float(settings.get("maintenancePct", 0.5)) / 100
            gross = exposure["gross"].reindex(self.days)
            ratio = (1 / (maintenance * gross)).where(gross > 0)
            liquidated_days = {pd.Timestamp(item["exit"] - 1, unit="ms", tz="UTC").normalize() for item in self.trades_cache if item["exitBy"] == "liquidation" and item["exit"]}
            for at, value in ratio[ratio < 1.2].items():
                if at not in liquidated_days:
                    events.append({"time": day_ms(at), "event": "Near liquidation", "detail": i18n.t("Equity {multiple}× the maintenance margin at the day's end", multiple=f'{value:.2f}')})
        events.sort(key=lambda row: row["time"] or 0)
        x = iso(daily.index)
        lines = [line(x, nums(daily["total"].to_numpy(), 4), "main", "balance", "Balance", customdata=nums(leverage.to_numpy()))]
        if margined:
            lines += [line(x, nums(held.to_numpy(), 4), "group", "margin", f"Margin at {float(settings.get('leverage') or 1):g}× leverage", index=1, of=2),
                      line(x, nums(upkeep.to_numpy(), 4), "group", "maintenance", "Maintenance margin", index=2, of=2)]
        else:
            lines.append(line(x, nums(daily["locked"].to_numpy(), 4), "group", "margin", "Cash held for orders", index=1, of=2))
        figures = {"balance": figure("strategy.account", lines, showlegend=True),
                   "leverage": figure("strategy.leverage", [line(x, nums(leverage.to_numpy()), "main", "leverage", "Leverage")], yaxis={"title": {"text": "Position value / equity"}})}  # Axis title discarded by figure().  # i18n: ignore
        return {"points": rows, "marginEvents": events[:500], "currency": self.currency, "figures": figures}

    def sectors_section(self, work):
        ranked = work["multi"]["byInstrument"]
        industry = self.invocation.get("industry") or {}
        by = {}
        for item in ranked:
            sector = industry.get(item["nativeInstrument"]) or "Unknown"
            entry = by.setdefault(sector, {"sector": sector, "net": 0.0, "gross": 0.0, "costs": 0.0, "names": 0})
            entry["net"] += item["pnl"] or 0.0
            entry["costs"] += item["fees"] or 0.0
            entry["gross"] += item["gross"] or 0.0
            entry["names"] += 1
        sectors = sorted(({**entry, "net": num(entry["net"]), "gross": num(entry["gross"]), "costs": num(entry["costs"])} for entry in by.values()), key=lambda entry: -entry["net"])
        return {"mostMade": ranked[:10], "mostLost": list(reversed(ranked[-10:])), "sectors": sectors}

    def style_section(self, returns, bench, bench_label):
        if bench is None:
            raise Missing(self.missing.get("benchmark", "No benchmark to measure exposure against."))
        joined = pd.concat([returns, bench], axis=1).dropna()
        if len(joined) < 20:
            raise Missing("Fewer than 20 days with both this run and the benchmark.")
        y, x = joined.iloc[:, 0].to_numpy(), joined.iloc[:, 1].to_numpy()
        beta = float(np.cov(y, x, ddof=1)[0, 1] / np.var(x, ddof=1)) if np.var(x, ddof=1) > 0 else None
        alpha = float(y.mean() - (beta or 0) * x.mean())
        market = (beta or 0) * x.mean() * self.A
        return {"version": "Styles v1", "exposures": [{"style": "Beta to " + bench_label, "exposure": num(beta), "contribution": num(market)}],
                "specific": num(alpha * self.A)}

    def costs_section(self, work):
        costs = work["costs"]
        names = [("Gross P&L", costs["gross"]), ("Fees", -(costs["fees"] or 0)), ("Spread", -(costs["spread"] or 0)), ("Impact", -(costs["impact"] or 0)),
                 ("Borrow", -(costs["borrow"] or 0)), ("Funding", -(costs["funding"] or 0)), ("Net P&L", costs["net"])]
        # A waterfall drawn as bars standing on `base`, so the display colours them up and down like every other bar.
        level, bases = 0.0, []
        for name, value in names:
            bases.append(0.0 if name in ("Gross P&L", "Net P&L") else level)
            level = (value or 0.0) if name == "Gross P&L" else level + (value or 0.0) if name != "Net P&L" else level
        waterfall = figure("strategy.costs", [trace("bar", "updown", "costs", x=[name for name, _ in names], y=[num(value) for _, value in names], base=nums(bases), name="Costs",
                                                    hovertemplate="%{x}: %{y:,.2f}<extra></extra>")], bargap=.3)
        gross = costs["gross"] or 0
        shares = [{"name": name, "amount": num(value), "shareOfGross": num(-value / gross) if gross and name not in ("Gross P&L", "Net P&L") else None,
                   "shareOfCapital": num(-value / self.capital) if name not in ("Gross P&L", "Net P&L") else None} for name, value in names]
        # Funding exists only for perpetuals; with it switched off the row says so instead of a 0.00 that reads as computed.
        if not any(item["type"] == "perpetual" for item in self.config["instruments"]):
            shares = [row for row in shares if row["name"] != "Funding"]
        elif not ((self.invocation.get("runSettings") or {}).get("costs") or {}).get("funding", True):
            for row in shares:
                if row["name"] == "Funding":
                    row["off"] = True
        doubled = work["checks"].get("costsDoubled")
        return {"breakdown": shares, "incomplete": costs["incomplete"], "doubled": doubled,
                "figures": {"waterfall": waterfall}}

    def crisis(self, equity, returns, bench):
        rows = []
        first, last = returns.index[0], returns.index[-1]
        for name, start, end in CRISES["crypto" if self.crypto else "stocks"]:
            begin, finish = pd.Timestamp(start, tz="UTC"), pd.Timestamp(end, tz="UTC")
            if finish < first or begin > last:
                rows.append({"name": name, "from": start, "to": end, "covered": False, "note": i18n.t("Not in this backtest")})
                continue
            inside = returns[(returns.index > begin) & (returns.index <= finish)]  # from the first day's close: the day's own move is not in it
            path = (1 + inside).cumprod()
            rows.append({"name": name, "from": start, "to": end, "covered": True, "return": num(compounded_total(inside)),
                         "benchmark": num(compounded_total(bench[(bench.index > begin) & (bench.index <= finish)].dropna())) if bench is not None else None,
                         "maxDrawdown": num(float(M.drawdown_path(path, 1.0).min())) if len(path) else None,
                         "partial": bool(begin < first or finish > last)})
        return {"periods": rows}


def whole_bins(values, most=30):
    """Bins for whole counts (bars held): each a whole number of counts wide and centred on whole numbers where one count wide, with the axis
    ticked at whole numbers (it read 4.2, 4.4 between 4 and 5 bars)."""
    if not values:
        return np.linspace(0, 1, 2), {}
    low, high = int(min(values)), int(max(values))
    width = max(1, math.ceil((high - low + 1) / most))
    edges = low - .5 + width * np.arange(math.ceil((high - low + 1) / width) + 1)
    step = next(step for step in (1, 2, 5, 10, 20, 50, 100, 200, 500, 1000, 2000, 5000, 10_000, math.inf) if (high - low) / step <= 10)
    return edges, ({"tick0": 0, "dtick": step} if step != math.inf else {"tickformat": "d"})


def write_light(output, work, trades):
    """The light report's two files: the key numbers with the equity, and every trade."""
    write(output, work, None, trades)


def write(output, work, results, trades, extra=None):
    from native.publish import safe

    output.mkdir(parents=True, exist_ok=True)
    folder = output / "strategy"
    with TemporaryDirectory(dir=output) as temporary:
        staged = Path(temporary) / "strategy"
        staged.mkdir()
        pd.DataFrame(trades).to_parquet(staged / "trades.parquet", index=False)
        for name, rows in (extra or {}).items():
            frame = pd.DataFrame(rows)
            for column in frame.columns[frame.dtypes == object]:
                frame[column] = frame[column].map(lambda value: json.dumps(value) if isinstance(value, (list, dict)) else value)
            frame.to_parquet(staged / f"{name}.parquet", index=False)
        for name, value in (("results", results), ("work", work)):
            if value is not None:
                (staged / f"{name}.json").write_text(json.dumps(safe(value), ensure_ascii=False, allow_nan=False, separators=(",", ":")), encoding="utf-8")
        if not folder.exists():
            staged.rename(folder)
        else:
            # Re-collecting a report replaces closed files, with Work visible last.
            for path in sorted(staged.iterdir(), key=lambda path: path.name == "work.json"):
                path.replace(folder / path.name)
    return folder
