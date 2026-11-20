"""TALYX's one cost model inside a Nautilus backtest, and when a signal's order fills.

Nautilus keeps the account: its fee model (Market's rates, or the stock commission) charges every fill. TALYX adds the rest of the cost
model where it belongs: half the spread and the impact move the price a market order fills at (a fill model that offers the order one
price level), and perpetual funding and stock borrow are cash adjustments at their own times (a simulation module). Every amount is
kept in a ledger, so the report splits costs exactly as the account paid them.

A signal is computed on a bar after it closes; its market order fills on the next bar of that instrument, at its open (the default) or
its close, never at the close the signal saw. An instrument without a bar at that moment (halted, or not trading that day) does not
fill: the order is rejected, the position is kept, and the ledger says why.
"""
from __future__ import annotations

import bisect
import math
import re
from collections import defaultdict
from decimal import Decimal

import numpy as np
from native import metrics as M

NS_DAY = 86_400_000_000_000
# Orders whose own trigger decides the price: the fill model leaves their price alone and adds only spread and impact.
TRIGGERED = {"STOP_MARKET", "TRAILING_STOP_MARKET", "MARKET_IF_TOUCHED"}


class Prices:
    """Each instrument's bars as arrays (close times in native nanoseconds), with the daily volatility and traded value impact needs. `known` is
    when each bar is known (its close plus the source's publication delay): the engine's clock runs on it, so a bar is found at a moment of
    the simulation by when it was known, and what a bar closed at is read by its close."""

    def __init__(self, frame, annualization, opens=None):
        """`opens`: {instrument: {close ns: open ns}} where a bar's open is not its close less its length (a stock's session opens)."""
        self.by = {}
        per_day = {}
        for instrument, rows in frame.groupby("instrument", sort=False):
            times = rows["ts_event"].map(int).to_numpy(dtype=np.int64)
            order = np.argsort(times, kind="stable")
            times = times[order]
            known = rows["ts_init"].map(int).to_numpy(dtype=np.int64)[order]
            sessions = (opens or {}).get(instrument)
            opened = np.array([sessions[int(at)] for at in times], dtype=np.int64) if sessions else times - bar_length(str(rows["barType"].iloc[0]))
            values = {name: rows[name].astype(float).to_numpy()[order] for name in ("open", "high", "low", "close", "volume")}
            step = np.median(np.diff(times)) if len(times) > 1 else NS_DAY
            bars_a_day = max(1.0, NS_DAY / float(step)) if step < NS_DAY else 1.0
            per_day[instrument] = bars_a_day
            close = values["close"]
            change = np.zeros(len(close))
            with np.errstate(divide="ignore", invalid="ignore"):
                change[1:] = np.log(close[1:] / close[:-1])
            change[~np.isfinite(change)] = 0.0
            window = int(max(20, 20 * bars_a_day))
            import pandas as pd
            volatility = pd.Series(change).rolling(window, min_periods=2).std().shift(1).fillna(0.0).to_numpy() * math.sqrt(bars_a_day)
            value = pd.Series(values["volume"] * close).rolling(window, min_periods=1).mean().shift(1).bfill().fillna(0.0).to_numpy() * bars_a_day
            self.by[instrument] = {"times": times, "known": known, "opened": opened, **values, "volatility": volatility, "dailyValue": value}
        self.bars_a_day = per_day
        self.annualization = annualization
        # The moments a name on the same bar cadence trades (when its bars are known): the union over the names that share a cadence. A name
        # with no bar at a moment its peers have is halted then.
        cadence = {name: int(np.median(np.diff(found["times"]))) if len(found["times"]) > 1 else NS_DAY for name, found in self.by.items()}
        groups = defaultdict(list)
        for name, step in cadence.items():
            groups[step].append(self.by[name]["known"])
        self.calendar = {name: np.unique(np.concatenate(groups[cadence[name]])) for name in self.by}

    def halted_at(self, instrument, ts):
        """Whether this name's peers have a bar at `ts` (when known) and it has none: it is halted then."""
        times = self.calendar.get(instrument)
        index = int(np.searchsorted(times, ts, side="left"))
        return index < len(times) and times[index] == ts and self.at(instrument, ts) is None

    def next_moment(self, instrument, after):
        """The first moment after `after` at which this name's peers have a bar: when an order sent at `after` could first fill."""
        times = self.calendar.get(instrument)
        if times is None:
            return None
        index = int(np.searchsorted(times, after, side="right"))
        return int(times[index]) if index < len(times) else None

    def after_last(self, instrument, sent):
        """Whether an order sent at `sent` came after the last moment this name's peers trade: the run's data ends before any bar it could fill on."""
        return self.next_moment(instrument, sent) is None

    def at(self, instrument, ts):
        """The index of this instrument's bar the engine has at `ts` (known exactly then), or None."""
        found = self.by.get(instrument)
        if found is None:
            return None
        index = int(np.searchsorted(found["known"], ts, side="left"))
        return index if index < len(found["known"]) and found["known"][index] == ts else None

    def last(self, instrument, ts):
        """The index of this instrument's last bar closed at or before `ts`, or None."""
        found = self.by.get(instrument)
        if found is None:
            return None
        index = bisect.bisect_right(found["times"], ts) - 1
        return index if index >= 0 else None

    def close(self, instrument, ts):
        index = self.last(instrument, ts)
        return None if index is None else float(self.by[instrument]["close"][index])


UNITS = {"SECOND": 1_000_000_000, "MINUTE": 60_000_000_000, "HOUR": 3_600_000_000_000, "DAY": NS_DAY, "WEEK": 7 * NS_DAY}


def bar_length(bar_type):
    """A bar's length in nanoseconds from its Nautilus bar type ("BTCUSDT-PERP.OKX-1-HOUR-LAST-EXTERNAL" is an hour)."""
    found = re.search(r"-(\d+)-(SECOND|MINUTE|HOUR|DAY|WEEK)-", bar_type)
    if found is None:
        raise ValueError(f"{bar_type}: a bar's length is not known, so its open moment is not either.")
    return int(found.group(1)) * UNITS[found.group(2)]


class Ledger:
    """What TALYX's cost model charged, entry by entry (amounts are costs in the instrument's settlement currency; a negative cost is income),
    and why an order did not fill."""

    def __init__(self):
        self.orders = {}          # client order id -> {base, spread, impact, side, instrument, time}
        self.entries = []         # funding and borrow: {t, instrument, kind, amount, currency, rate?}
        self.unfilled = {}        # client order id -> reason
        self.at_end = set()       # client order ids sent on the run's last bar: no later bar to fill on, so unfilled when the run ends
        self.closing = set()      # client order ids of closes for a halted name ("Halted names: close"): they wait for its next bar
        self.moments = {}         # (client order id, engine time ns) -> the moment the fill's price belongs to: the bar's open, or its close
        self.gaps = defaultdict(list)  # instrument -> [[from, to]] funding periods Market has no rate for

    def order_costs(self, fills):
        """Spread and impact per fill row of the native fills report (`client_order_id`, `last_qty`): price moved × quantity filled."""
        out = []
        for row in fills:
            plan = self.orders.get(str(row["client_order_id"]))
            if plan is None:
                out.append((0.0, 0.0))
                continue
            quantity = float(row["last_qty"])
            out.append((quantity * plan["base"] * plan["spread"], quantity * plan["base"] * plan["impact"]))
        return out


def _stopped_in_entry_bar(identity):
    """(price, bar ts) of an exit a stop decided inside its position's opening bar (talyx_quant._entry_bar_stops), else None."""
    import sys
    quant = sys.modules.get("talyx_quant")
    return getattr(quant, "_entry_bar_stops", {}).get(identity) if quant is not None else None


def fill_model(costs, fill, prices, ledger, clock, through_ticks=0, guard=None, halted="keep"):
    """A Nautilus fill model for this run's cost settings; `clock()` is the simulation time in nanoseconds. A limit order fills when the bar touches
    its price, or (`through_ticks` above zero) only when the bar trades that many price steps through it; the order then stays open. `guard`
    says why an order cannot be paid for (the fee on top of the notional is more than the account has), which rejects it instead of letting
    the engine fail on a negative balance. `halted` is what happens to a position whose order finds its name halted: kept, or closed at the
    name's next price (the strategy sends the close, whose id is in `ledger.closing`, and it fills at the first bar the name has)."""
    from nautilus_trader.execution import FillModel
    from nautilus_trader.model import BookOrder, BookType, OrderBook, OrderSide, Quantity

    half_spread = float(costs.get("halfSpreadBps") or 0) / 1e4
    coefficient = float(costs.get("impact") if costs.get("impact") is not None else M.IMPACT_COEFFICIENT)
    cap = costs.get("volumeCapPct")

    class TalyxFill(FillModel):
        def is_limit_filled(self):
            return True

        def is_slipped(self):
            return False

        def fill_limit_inside_spread(self):
            return False

        def get_orderbook_for_fill_simulation(self, instrument, order, best_bid, best_ask):
            kind = str(order.order_type).rsplit(".", 1)[-1]
            if kind == "LIMIT" and through_ticks > 0:
                name, now = str(instrument.id), clock()
                index = prices.at(name, now)
                bars = prices.by.get(name)
                distance = through_ticks * float(instrument.price_increment)
                buy = order.side == OrderSide.BUY
                limit = float(order.price.as_double())
                if index is None or (bars["low"][index] > limit - distance + 1e-9 if buy else bars["high"][index] < limit + distance - 1e-9):
                    return OrderBook(instrument.id, BookType.L2_MBP)  # not traded through: no fill on this bar, the order stays
            name, now = str(instrument.id), clock()
            index = prices.at(name, now)
            identity = str(order.client_order_id)
            if kind != "MARKET" and kind not in TRIGGERED:
                if index is not None:
                    ledger.moments[(identity, now)] = int(prices.by[name]["opened"][index])  # filled inside the bar: stamped with its open
                return None  # a resting limit fills at its own price
            book = OrderBook(instrument.id, BookType.L2_MBP)
            expected = prices.next_moment(name, int(order.ts_init)) if kind == "MARKET" else None
            if index is not None and expected is not None and expected < now and prices.at(name, expected) is None and identity not in ledger.closing:
                index = None  # it could have filled when its peers traded; it had no bar then: halted
            if kind == "MARKET" and prices.after_last(name, int(order.ts_init)):
                # Sent on the run's last bar: the data ends before the bar it would fill on. Not a halt, and never filled on the bar it was sent at.
                ledger.at_end.add(identity)
                ledger.unfilled[identity] = "Sent on the run's last bar: the data ends before a bar it could fill on."
                return book
            if index is None:
                ledger.unfilled[identity] = "No bar: halted or not trading, " + ("the position is closed at the next price." if halted == "close" else "the position is kept.")
                return book  # an empty book: Nautilus rejects the order, nothing is filled
            bars = prices.by[name]
            buy = order.side == OrderSide.BUY
            stopped = _stopped_in_entry_bar(identity) if kind == "MARKET" else None
            if stopped is not None and prices.at(name, stopped[1]) is not None:
                # The stop of a position that went through it in the bar the position opened in: at the stop, in that bar.
                index = prices.at(name, stopped[1])
                base = stopped[0]
            elif kind == "MARKET":
                base = float(bars["open"][index] if fill == "nextOpen" else bars["close"][index])
            elif kind == "STOP_MARKET" and getattr(order, "trigger_price", None) is not None:
                # A stop fills at its trigger; when the bar opens beyond it the open is the price (a gap goes through the stop).
                trigger, opened = float(order.trigger_price.as_double()), float(bars["open"][index])
                base = max(trigger, opened) if buy else min(trigger, opened)
            else:
                base = float((best_ask if buy else best_bid).as_double()) if (best_ask if buy else best_bid) is not None else float(bars["close"][index])
            quantity = float(order.leaves_qty.as_double())
            participation = quantity * base / bars["dailyValue"][index] if bars["dailyValue"][index] > 0 else 0.0
            impact = coefficient * float(bars["volatility"][index]) * math.sqrt(max(participation, 0.0))
            move = half_spread + impact
            price = instrument.make_price(base * (1 + move) if buy else base * (1 - move))
            size = order.leaves_qty
            if guard is not None and (why := guard(instrument, order, price.as_double(), float(size.as_double()))):
                ledger.unfilled[identity] = why
                return book
            if cap is not None:
                limit = float(cap) / 100 * float(bars["volume"][index])
                if limit < quantity:
                    size = instrument.make_qty(max(limit, 0.0))
                    ledger.unfilled[identity] = f"Volume cap: at most {float(cap):g}% of the bar's volume ({size} of {order.leaves_qty}) filled."
                    if size.as_double() <= 0:
                        return book
            ledger.orders[identity] = {"base": base, "spread": half_spread, "impact": impact, "instrument": name, "time": now, "side": "BUY" if buy else "SELL"}
            # A fill's time is the moment of its price: a market order at the close it fills at, any other at the open of the bar it filled in.
            ledger.moments[(identity, now)] = int(bars["times"][index] if kind == "MARKET" and fill == "nextClose" else bars["opened"][index])
            book.add(BookOrder(OrderSide.SELL if buy else OrderSide.BUY, price, Quantity.from_str(str(size)), 1), 0, 0, now)
            return book

    return TalyxFill()


def funding_rates(rows):
    """{instrument: (times ns ascending, rates, mark prices or None)} from [{"instrument", "time" (ms), "rate", "mark"?}]."""
    found = defaultdict(list)
    for row in rows:
        mark = row.get("mark")
        found[row["instrument"]].append((int(row["time"]) * 1_000_000, float(row["rate"]), None if mark is None or mark != mark else float(mark)))
    return {name: tuple(list(column) for column in zip(*sorted(items))) for name, items in found.items()}


def simulation_module(costs, prices, ledger, rates, kinds):
    """Funding on perpetuals at each settlement Market gives (a long pays a positive rate), and borrow on short stock every day it is held.
    `kinds` maps instrument id to its type (perpetual, equity, spot)."""
    from nautilus_trader.backtest import SimulationModule
    from nautilus_trader.model import Money

    funding = bool(costs.get("funding", True))
    borrow = float(costs.get("borrowPct") if costs.get("borrowPct") is not None else M.DEFAULT_BORROW_PCT) / 100

    class TalyxCosts(SimulationModule):
        def __init__(self):
            super().__init__()
            self.last = None

        def pre_process(self, data):
            pass

        def process(self, ts_now, ctx):
            previous, self.last = self.last, ts_now
            if previous is None or ts_now <= previous:
                return None
            totals = defaultdict(float)
            for position in ctx.positions:
                name = str(position.instrument_id)
                signed = float(position.signed_qty)
                if not signed:
                    continue
                currency = position.settlement_currency
                if funding and kinds.get(name) == "perpetual" and name in rates:
                    times, values, marks = rates[name]
                    start = bisect.bisect_right(times, previous)
                    stop = bisect.bisect_right(times, ts_now)
                    for at, rate, mark in zip(times[start:stop], values[start:stop], marks[start:stop], strict=True):
                        # The venue charges funding on the mark price at the settlement; the last trade price where Market has none.
                        price = mark if mark is not None else prices.close(name, at)
                        if price is None:
                            continue
                        amount = signed * price * rate  # a cost when long and the rate is positive
                        totals[currency] -= amount
                        ledger.entries.append({"t": at, "instrument": name, "kind": "funding", "amount": amount, "currency": str(currency), "rate": rate})
                if borrow and signed < 0 and kinds.get(name) == "equity":
                    days = ts_now // NS_DAY - previous // NS_DAY
                    if days > 0:
                        price = prices.close(name, ts_now)
                        if price is not None:
                            amount = -signed * price * borrow * days / 365
                            totals[currency] -= amount
                            ledger.entries.append({"t": ts_now, "instrument": name, "kind": "borrow", "amount": amount, "currency": str(currency), "days": int(days)})
            if not totals:
                return None
            return [Money(Decimal(str(round(value, 8))), currency) for currency, value in totals.items() if value]

        def acknowledge(self, outcomes):
            for outcome in outcomes or ():
                if not outcome.applied:
                    ledger.entries.append({"t": self.last, "instrument": None, "kind": "rejected", "amount": 0.0, "currency": None, "error": outcome.error})

        def log_diagnostics(self):
            pass

        def reset(self):
            self.last = None

    return TalyxCosts()
