"""The ready-made strategies: ordinary strategy code that reads a library item, shown read-only and copied to become one's own.

Each is a whole strategy file on its own (it imports only `talyx_quant`, the engine and the standard library), so a copy keeps working and
can be edited freely. The library item arrives as library data in `on_data`, each value at the moment it became available; a decision is made
there, never in `on_bar` at the same moment. A period that cannot be decided (for the ranked preset: fewer names with a value than it holds) is
skipped and written to the report with the reason.
"""

from .i18n import mark

RANKED = '''"""Hold the top names by a library signal, and optionally short the bottom ones, re-ranked when a new signal arrives.

Ready-made strategy: copy it to change the code. Choose the signal (a factor or a model's prediction in the library) in the parameters.
A signal that lacks a value for some symbol skips that period; the report says which symbols were missing.
Shorting needs perpetuals (a margin account); with it, the invested share is split between the long and the short names.
"""
import talyx_quant as q
from nautilus_trader.model import OrderSide


def fit(base, total, name_cap=None, group_of=None, group_cap=None):
    """Spread `total` over the names in proportion to `base`, no name above `name_cap`, no group above `group_cap` (a name without a group is
    not limited by it); what cannot be placed stays in cash. Weight freed by a limit goes to the names that still have room."""
    weights = dict.fromkeys(base, 0.0)
    active = {name for name in base if base[name] > 0}
    left = total
    groups = group_of or {}
    while active and left > 1e-12:
        mass = sum(base[name] for name in active)
        step = left / mass
        if name_cap:
            step = min(step, min((name_cap - weights[name]) / base[name] for name in active))
        if group_cap:
            for group in {groups.get(name) for name in active} - {None}:
                used = sum(weight for name, weight in weights.items() if groups.get(name) == group)
                members = sum(base[name] for name in active if groups.get(name) == group)
                step = min(step, (group_cap - used) / members)
        step = max(step, 0.0)
        for name in active:
            weights[name] += step * base[name]
        left -= step * mass
        for name in list(active):
            group = groups.get(name)
            if (name_cap and weights[name] >= name_cap - 1e-12) or (
                    group_cap and group is not None and sum(weight for other, weight in weights.items() if groups.get(other) == group) >= group_cap - 1e-12):
                active.discard(name)
        if step <= 1e-15:
            break
    return {name: weight for name, weight in weights.items() if weight > 0}


def choose(scores, *, top_n, buffer=0, held=(), max_weight=None, weighting="equal", volatility=None, min_dollar_volume=None, dollar_volume=None,
           sector_cap=None, sector_of=None, invest=0.95):
    """The long weights for one rebalance from `{name: signal}`: the top `top_n` names, plus any held name still within the top `top_n + buffer`
    (it is sold only when it falls below that); names thinner than `min_dollar_volume` are left out first. Weighted equally, by rank, by signal
    or by 1 / volatility; then limited by `max_weight` per name and `sector_cap` per sector (names without a sector are not limited by it)."""
    names = sorted(scores, key=lambda name: (-scores[name], name))
    if min_dollar_volume:
        names = [name for name in names if (dollar_volume or {}).get(name, 0.0) >= min_dollar_volume]
    chosen = names[:top_n]
    chosen += [name for name in names[top_n:top_n + buffer] if name in held]
    if not chosen:
        return {}
    if weighting == "rank":
        base = {name: len(chosen) - place for place, name in enumerate(chosen)}
    elif weighting == "signal":
        base = {name: max(0.0, scores[name]) for name in chosen}
        if not sum(base.values()):
            base = dict.fromkeys(chosen, 1.0)
    elif weighting == "inverse_vol":
        base = {name: 1.0 / volatility[name] if volatility and volatility.get(name) else 0.0 for name in chosen}
        if not sum(base.values()):
            base = dict.fromkeys(chosen, 1.0)
    else:
        base = dict.fromkeys(chosen, 1.0)
    return fit(base, invest, max_weight or None, sector_of, sector_cap or None)


class RankedPortfolio(q.Strategy):
    def __init__(self):
        super().__init__()
        self.signal = q.library_param("signal", "", label="Signal")
        self.chosen_field = q.param("signal_field", "", label="Signal field", help="Blank when the item has only one")
        self.top_n = q.param("top_n", 20, min=1, label="Hold the top")
        self.short_n = q.param("short_n", 0, min=0, label="Short the bottom", help="0 = long only")
        self.every = q.param("rebalance_every", 1, min=1, label="Rebalance every", help="This many signals between rebalances")
        self.weighting = q.choice("weighting", "equal", [("equal", "Equal"), ("rank", "By rank"), ("signal", "By signal"), ("inverse_vol", "Inverse volatility")], label="Weighting")
        self.invest = q.param("invest_fraction", 0.95, min=0.01, max=1.0, label="Invested", help="Share of equity invested")
        self.buffer = q.param("buffer", 0, min=0, label="Buffer", help="Keep a held name until it falls below the top + this")
        self.max_weight = q.param("max_weight", 0.0, min=0.0, max=1.0, label="Max weight", help="Largest weight of one name; 0 = no limit")
        self.min_dollar_volume = q.param("min_dollar_volume", 0.0, min=0.0, label="Min traded value", help="Smallest 20-day average traded value; 0 = no limit")
        self.sector_cap = q.param("sector_cap", 0.0, min=0.0, max=1.0, label="Sector cap", help="Largest share of one sector; 0 = no limit")
        if not self.signal:
            raise ValueError("Choose the signal this strategy ranks by (a library item) in its parameters.")
        self.field = self.chosen_field
        self.sections = {}  # the moment values became available -> {instrument id: value}
        self.signals_seen = 0
        self.waiting = []  # buys held back until the sells have settled
        self.rebalances, self.ranking = [], []

    def on_start(self):
        if self.top_n + self.short_n > len(self.instrument_ids):
            raise ValueError(f"Holding {self.top_n} and shorting {self.short_n} needs at least {self.top_n + self.short_n} symbols; the data settings have {len(self.instrument_ids)}.")
        if self.short_n and not all(q.can_short(self.cache.instrument(item)) for item in self.instrument_ids):
            raise ValueError("Shorting needs perpetuals, or stocks with shorting allowed in the run settings; a spot symbol cannot be shorted.")
        self.by_name = {str(item): item for item in self.instrument_ids}
        self.subscribe_data(q.library_data_type())
        for bar_type in self.bar_types:
            self.subscribe_bars(bar_type)

    def on_data(self, data):
        value = data.data
        if value.name != self.signal:
            return
        if not self.field:
            self.field = value.field
        elif value.field != self.field:
            if self.chosen_field:
                return
            raise ValueError(f"{self.signal} has more than one field ({self.field}, {value.field}); choose one in Signal field.")
        for moment in [moment for moment in self.sections if moment < value.ts_init]:
            self.settle(moment, self.sections.pop(moment))
        section = self.sections.setdefault(value.ts_init, {})
        section[value.instrument_id] = value
        if value.batch_end:
            self.settle(value.ts_init, self.sections.pop(value.ts_init))

    def settle(self, moment, section):
        """One signal moment: rank the names in the universe that have a value; skip only when fewer have one than are held."""
        eligible = {str(item) for item in self.universe(moment // 1_000_000)}
        present = {key: item for key, item in section.items() if key in eligible}
        needed = self.top_n + self.short_n
        if len(present) < needed:
            self.rebalances.append({"ts_available": moment, "state": "skipped", "version": next(iter(section.values())).version,
                                    "reason": f"Only {len(present)} of {len(eligible)} names have a value; {needed} are needed"})
            return
        self.signals_seen += 1
        if (self.signals_seen - 1) % self.every == 0:
            self.rebalance(moment, present)

    def spread(self, names, budget, section):
        """The weight of each name in one leg, summing to `budget`."""
        if self.weighting == "rank":
            shares = {name: len(names) - place for place, name in enumerate(names)}
        elif self.weighting == "signal":
            sign = 1 if budget > 0 else -1
            shares = {name: max(0.0, sign * section[name].value) for name in names}
            if not sum(shares.values()):
                shares = {name: 1 for name in names}
        else:
            shares = {name: 1 for name in names}
        total = sum(shares.values())
        return {name: budget * share / total for name, share in shares.items()}

    def rebalance(self, moment, section):
        ranked = sorted(section, key=lambda name: (-section[name].value, name))
        long_budget = self.invest / 2 if self.short_n else self.invest
        weights = {str(item): 0.0 for item in self.instrument_ids if self.is_tradable(item, moment // 1_000_000)}
        held = {str(item) for item in self.instrument_ids if self.position_size(item) > 0}
        scores = {name: section[name].value for name in ranked}
        weights.update(choose(scores, top_n=self.top_n, buffer=self.buffer, held=held, max_weight=self.max_weight, weighting=self.weighting,
                              volatility=self.volatilities(ranked) if self.weighting == "inverse_vol" else None, min_dollar_volume=self.min_dollar_volume,
                              dollar_volume=self.dollar_volumes(ranked) if self.min_dollar_volume else None, sector_cap=self.sector_cap,
                              sector_of=self.sectors(ranked) if self.sector_cap else None, invest=long_budget))
        if self.short_n:
            weights.update(self.spread(list(reversed(ranked))[:self.short_n], -long_budget, section))
        unpriced = sorted(name for name in ranked if self.last_close(self.by_name[name]) is None)
        if self.open_orders() or unpriced:
            reason = "Earlier orders are still open" if self.open_orders() else "No price yet for " + ", ".join(unpriced)
            self.rebalances.append({"ts_available": moment, "state": "skipped", "reason": reason, "version": section[ranked[0]].version})
            return
        self.rebalances.append({"ts_available": moment, "state": "rebalanced", "reason": None, "version": section[ranked[0]].version})
        self.ranking.extend({"ts_available": moment, "instrument": name, "rank": place + 1, "value": section[name].value, "weight": weights[name]}
                            for place, name in enumerate(ranked))
        self.trade_to(weights)

    def history(self, name, count=21):
        bars = self.cache.bars(next(item for item in self.bar_types if str(item.instrument_id) == name))[:count]
        return list(reversed(bars))

    def volatilities(self, names):
        """Standard deviation of the last 20 returns of each name."""
        out = {}
        for name in names:
            closes = [float(bar.close) for bar in self.history(name)]
            returns = [b / a - 1 for a, b in zip(closes, closes[1:], strict=False) if a]
            if len(returns) > 1:
                mean = sum(returns) / len(returns)
                out[name] = (sum((value - mean) ** 2 for value in returns) / (len(returns) - 1)) ** 0.5
        return out

    def dollar_volumes(self, names):
        """Average traded value (close times volume) over the last 20 bars."""
        out = {}
        for name in names:
            bars = self.history(name, 20)
            if bars:
                out[name] = sum(float(bar.close) * float(bar.volume) for bar in bars) / len(bars)
        return out

    def sectors(self, names):
        """The sector of each name from Market's industry series; a name Market has none for is left out (the cap does not limit it)."""
        out = {}
        for name in names:
            try:
                found = q.series("industry", self.by_name[name])
            except q.SeriesUnavailable:
                continue
            if len(found):
                out[name] = str(found.iloc[-1])
        return out

    def trade_to(self, weights):
        equity = self.equity()
        sells, buys = [], []
        for name, weight in weights.items():
            instrument = self.cache.instrument(self.by_name[name])
            change = weight * equity / self.last_close(instrument.id) - self.position_size(instrument.id)
            quantity = q.tradable_quantity(instrument, abs(change))
            if quantity is None:
                continue
            (buys if change > 0 else sells).append((instrument, quantity))
        for instrument, quantity in sells:
            self.submit_order(self.order_factory.market(instrument.id, OrderSide.SELL, quantity))
        self.waiting = buys
        self.release()

    def release(self):
        """Buys go out when no order of this strategy is still open, so the cash of the sells is there."""
        if self.waiting and not self.open_orders():
            waiting, self.waiting = self.waiting, []
            for instrument, quantity in waiting:
                self.submit_order(self.order_factory.market(instrument.id, OrderSide.BUY, quantity))

    def on_order_filled(self, event):
        self.release()

    def on_order_rejected(self, event):
        self.release()

    def on_order_denied(self, event):
        self.release()

    def on_order_canceled(self, event):
        self.release()

    def reports(self, engine, frame):
        import pandas as pd
        for moment, section in self.sections.items():
            self.skip(moment, section)
        return {"Signal rebalances": pd.DataFrame(self.rebalances, columns=["ts_available", "state", "reason", "version"]),
                "Signal ranking": pd.DataFrame(self.ranking, columns=["ts_available", "instrument", "rank", "value", "weight"])}
'''

REBALANCE = '''"""Hold the weights a library item publishes (an asset allocation), trading to them when a new set arrives.

Ready-made strategy: copy it to change the code. Choose the weights (an allocation in the library) in the parameters.
An allocation publishes every selected symbol, including explicit zero weights; a missing member skips that set. A weight below zero is a short,
which needs a perpetual (a margin account). Trades smaller than the tolerance are not made: when no symbol is further than that
from its target weight, the period is skipped.
"""
import talyx_quant as q
from nautilus_trader.model import OrderSide


class WeightRebalance(q.Strategy):
    def __init__(self):
        super().__init__()
        self.signal = q.library_param("signal", "", label="Weights (library item)")
        self.chosen_field = q.param("signal_field", "", label="Weights field", help="Blank when the item has only one")
        self.every = q.param("rebalance_every", 1, min=1, label="Rebalance every", help="This many weight sets between rebalances")
        self.tolerance = q.param("tolerance", 0.0, min=0.0, max=1.0, label="Tolerance", help="Trade only when a weight is off by more than this")
        self.invest = q.param("invest_fraction", 1.0, min=0.01, max=1.0, label="Invested", help="Share of the weights invested")
        if not self.signal:
            raise ValueError("Choose the weights this strategy follows (a library item) in its parameters.")
        self.field = self.chosen_field
        self.sections = {}
        self.sets_seen = 0
        self.waiting = []
        self.rebalances, self.targets = [], []

    def on_start(self):
        self.by_name = {str(item): item for item in self.instrument_ids}
        self.subscribe_data(q.library_data_type())
        for bar_type in self.bar_types:
            self.subscribe_bars(bar_type)

    def on_bar(self, bar):
        pass

    def on_data(self, data):
        value = data.data
        if value.name != self.signal:
            return
        if not self.field:
            self.field = value.field
        elif value.field != self.field:
            if self.chosen_field:
                return
            raise ValueError(f"{self.signal} has more than one field ({self.field}, {value.field}); choose one in Weights field.")
        self.sections.setdefault(value.ts_init, {})[value.instrument_id] = value
        if value.batch_end:
            self.sets_seen += 1
            section = self.sections.pop(value.ts_init)
            if (self.sets_seen - 1) % self.every == 0:
                self.rebalance(value.ts_init, section)

    def rebalance(self, moment, section):
        version = next(iter(section.values())).version
        eligible = {str(item) for item in self.universe(moment // 1_000_000)}
        missing = sorted(eligible - set(section))
        if missing:
            self.rebalances.append({"ts_available": moment, "state": "skipped", "reason": "No weight for " + ", ".join(missing), "version": version})
            return
        if not eligible:
            self.rebalances.append({"ts_available": moment, "state": "skipped", "reason": "No priced index members", "version": version})
            return
        weights = {name: 0.0 for name, item in self.by_name.items() if self.is_tradable(item, moment // 1_000_000)}
        weights.update({name: section[name].value for name in eligible})
        unpriced = sorted(name for name in weights if self.last_close(self.by_name[name]) is None)
        if self.open_orders() or unpriced:
            reason = "Earlier orders are still open" if self.open_orders() else "No price yet for " + ", ".join(unpriced)
            self.rebalances.append({"ts_available": moment, "state": "skipped", "reason": reason, "version": version})
            return
        instruments = {name: self.cache.instrument(self.by_name[name]) for name in weights}
        for name, instrument in instruments.items():
            if weights[name] < 0 and not q.can_short(instrument):
                raise ValueError(f"{name} has a negative weight, which is a short; shorting needs perpetuals or stocks with shorting allowed.")
        equity = self.equity()
        current = {name: self.position_size(instrument.id) * self.last_close(instrument.id) / equity for name, instrument in instruments.items()}
        targets = {name: weights[name] * self.invest for name in weights}
        if max(abs(targets[name] - current[name]) for name in weights) <= self.tolerance:
            self.rebalances.append({"ts_available": moment, "state": "skipped", "reason": "Every weight is within the tolerance of its target", "version": version})
            return
        self.rebalances.append({"ts_available": moment, "state": "rebalanced", "reason": None, "version": version})
        self.targets.extend({"ts_available": moment, "instrument": name, "target_weight": targets[name], "weight_before": current[name]} for name in weights)
        sells, buys = [], []
        for name, instrument in instruments.items():
            change = targets[name] * equity / self.last_close(instrument.id) - self.position_size(instrument.id)
            quantity = q.tradable_quantity(instrument, abs(change))
            if quantity is None:
                continue
            (buys if change > 0 else sells).append((instrument, quantity))
        for instrument, quantity in sells:
            self.submit_order(self.order_factory.market(instrument.id, OrderSide.SELL, quantity))
        self.waiting = buys
        self.release()

    def release(self):
        """Buys go out when no order of this strategy is still open, so the cash of the sells is there."""
        if self.waiting and not self.open_orders():
            waiting, self.waiting = self.waiting, []
            for instrument, quantity in waiting:
                self.submit_order(self.order_factory.market(instrument.id, OrderSide.BUY, quantity))

    def on_order_filled(self, event):
        self.release()

    def on_order_rejected(self, event):
        self.release()

    def on_order_denied(self, event):
        self.release()

    def on_order_canceled(self, event):
        self.release()

    def reports(self, engine, frame):
        import pandas as pd
        return {"Signal rebalances": pd.DataFrame(self.rebalances, columns=["ts_available", "state", "reason", "version"]),
                "Weight targets": pd.DataFrame(self.targets, columns=["ts_available", "instrument", "target_weight", "weight_before"])}
'''

SIGNAL = '''"""Trade one symbol on a library signal: buy when it rises above the buy level, sell everything when it falls below the sell level.

Ready-made strategy: copy it to change the code. Choose the signal (a factor or a model's prediction in the library) in the parameters.
Between the two levels the position is left as it is. The signal arrives when it became available, and the order goes out then.
"""
import talyx_quant as q
from nautilus_trader.model import OrderSide


class SignalTrader(q.Strategy):
    def __init__(self):
        super().__init__()
        self.signal = q.library_param("signal", "", label="Signal")
        self.chosen_field = q.param("signal_field", "", label="Signal field", help="Blank when the item has only one")
        self.buy_above = q.param("buy_above", 0.0, label="Buy above", help="Buy when the signal is above this")
        self.sell_below = q.param("sell_below", 0.0, label="Sell below", help="Sell when the signal is below this")
        self.stake = q.param("stake_fraction", 0.95, min=0.01, max=1.0, label="Share of equity per buy")
        if not self.signal:
            raise ValueError("Choose the signal this strategy trades on (a library item) in its parameters.")
        if self.sell_below > self.buy_above:
            raise ValueError("The sell level cannot be above the buy level.")
        self.field = self.chosen_field
        self.decisions = []

    def on_start(self):
        if len(self.instrument_ids) != 1:
            raise ValueError(f"This strategy trades one symbol; the data settings have {len(self.instrument_ids)}.")
        self.subscribe_data(q.library_data_type())
        self.subscribe_bars(self.bar_type)

    def on_data(self, data):
        value = data.data
        if value.name != self.signal:
            return
        if not self.field:
            self.field = value.field
        elif value.field != self.field:
            if self.chosen_field:
                return
            raise ValueError(f"{self.signal} has more than one field ({self.field}, {value.field}); choose one in Signal field.")
        instrument = self.cache.instrument(self.instrument_id)
        price = self.last_close(self.instrument_id)
        flat = not self.position_size(self.instrument_id)
        action = "none"
        if self.open_orders():
            action = "skipped: an earlier order is still open"
        elif price is None:
            action = "skipped: no price yet"
        elif value.value > self.buy_above and flat:
            amount = self.equity() * self.stake / price
            quantity = q.tradable_quantity(instrument, amount)
            if quantity is not None:
                self.submit_order(self.order_factory.market(self.instrument_id, OrderSide.BUY, quantity))
                action = "buy"
            else:
                action = "skipped: the stake is below one order step"
        elif value.value < self.sell_below and not flat:
            self.close_all_positions(self.instrument_id)
            action = "sell"
        self.decisions.append({"ts_available": value.ts_init, "ts_observed": value.ts_event, "value": value.value, "version": value.version, "action": action})

    def on_stop(self):
        self.cancel_all_orders(self.instrument_id)

    def reports(self, engine, frame):
        import pandas as pd
        return {"Signal decisions": pd.DataFrame(self.decisions, columns=["ts_available", "ts_observed", "value", "version", "action"])}
'''

# Each preset: what the list shows, the code, and the parameter that sells short (None when the strategy never does).
BREAKOUT = '''"""Breakout on the 1h close: enter when the close clears the last N bars; exit on an ATR stop, an ATR trailing stop or the
half-length channel.

Ready-made strategy: copy it to change the code. Risk per trade sizes the position from the stop distance, up to Max position and what the free balance pays for.
"""
from collections import deque

import talyx_quant as q
from nautilus_trader.model import OrderSide


class Breakout(q.Strategy):
    def __init__(self):
        super().__init__()
        self.lookback = q.param("lookback", 48, min=12, max=240, label="Lookback (bars)")
        self.stop_atr = q.param("stop_atr", 2.0, min=0.5, max=6.0, label="Stop (ATR)")
        self.trail_atr = q.param("trail_atr", 3.0, min=0.0, max=8.0, label="Trailing stop (ATR)")
        self.risk = q.param("risk", 1.0, min=0.1, max=5.0, label="Risk per trade (%)")
        self.shorts = q.param("shorts", True, label="Trade the short side")
        self.highs, self.lows = deque(maxlen=self.lookback), deque(maxlen=self.lookback)
        self.ranges = deque(maxlen=14)
        self.previous_close = None
        self.stop_level = None
        self.pending_stop = None

    def on_start(self):
        self.instrument = self.cache.instrument(self.instrument_id)
        self.subscribe_bars(self.bar_type)

    def on_bar(self, bar):
        high, low, close = float(bar.high), float(bar.low), float(bar.close)
        if self.previous_close is not None:
            self.ranges.append(max(high - low, abs(high - self.previous_close), abs(low - self.previous_close)))
        before_highs, before_lows = list(self.highs), list(self.lows)
        self.highs.append(high)
        self.lows.append(low)
        self.previous_close = close
        if len(self.ranges) < 14 or len(before_highs) < self.lookback:
            return
        atr = sum(self.ranges) / len(self.ranges)
        held = self.position_size(self.instrument_id)
        q.plot("ATR", atr, pane="own")
        if held:
            half = self.lookback // 2
            if held > 0 and close < min(before_lows[-half:]) or held < 0 and close > max(before_highs[-half:]):
                self.cancel_all_orders(self.instrument_id)
                self.stop_level = None
                self.close(self.instrument_id, reason="channel")
            elif self.trail_atr > 0:
                self.trail(held, close, atr)
            return
        if self.open_orders():
            return
        if close > max(before_highs):
            self.enter(OrderSide.BUY, close, atr, "breakout")
        elif self.shorts and close < min(before_lows):
            self.enter(OrderSide.SELL, close, atr, "breakdown")

    def enter(self, side, close, atr, why):
        size = min(q.risk_size(self.risk, self.equity(), self.stop_atr * atr, close, self.max_position), self.affordable(self.instrument_id, close))
        quantity = self.instrument.make_qty(size)
        if quantity.as_double() <= 0:
            return
        sign = 1 if side == OrderSide.BUY else -1
        self.pending_stop = close - sign * self.stop_atr * atr
        self.submit_order(self.order_factory.market(self.instrument_id, side, quantity, tags=["entry:" + why]))
        q.log(f"{why} at {close:.1f}, stop {self.pending_stop:.1f}", kind="Signal", instrument=self.instrument_id)

    def on_order_filled(self, event):
        if self.pending_stop is not None and self.position_size(self.instrument_id) and not self.stop_level:
            self.place_stop(self.pending_stop, "stop")
            self.pending_stop = None

    def place_stop(self, level, why):
        held = self.position_size(self.instrument_id)
        side = OrderSide.SELL if held > 0 else OrderSide.BUY
        quantity = self.instrument.make_qty(abs(held))
        self.stop_level = level
        self.submit_order(self.order_factory.stop_market(self.instrument_id, side, quantity, self.instrument.make_price(level),
                                                         trigger_type=self.stop_trigger, reduce_only=True, tags=["exit:" + why]))

    def trail(self, held, close, atr):
        """Move the stop only in the position's favour."""
        sign = 1 if held > 0 else -1
        level = close - sign * self.trail_atr * atr
        if self.stop_level is not None and sign * (level - self.stop_level) > 0:
            self.cancel_all_orders(self.instrument_id)
            self.place_stop(level, "trail")

    def on_order_rejected(self, event):
        """A stop that reaches the venue after the price has already gone through it is refused: close at market instead of holding on with no stop."""
        order = self.cache.order(event.client_order_id)
        why = next((str(tag)[5:] for tag in (order.tags if order else None) or [] if str(tag) in ("exit:stop", "exit:trail")), None)
        if why and self.position_size(self.instrument_id):
            self.stop_level = None
            self.close(self.instrument_id, reason=why)

    def on_position_closed(self, event):
        self.stop_level = None
'''


CROSSOVER = '''"""Moving-average crossover: long when the fast average crosses above the slow one, flat when it crosses below (and short, if allowed).

Ready-made strategy: copy it to change the code. The position is Max position times the account's equity, up to what the free
balance pays for with the fee included.
"""
from collections import deque

import talyx_quant as q
from nautilus_trader.model import OrderSide


class Crossover(q.Strategy):
    def __init__(self):
        super().__init__()
        self.fast = q.param("fast", 20, min=5, max=100, label="Fast window")
        self.slow = q.param("slow", 100, min=20, max=400, label="Slow window")
        self.shorts = q.param("shorts", False, label="Trade the short side")
        if self.fast >= self.slow:
            raise ValueError("The fast window must be shorter than the slow window")
        self.closes = deque(maxlen=self.slow)
        self.above = None

    def on_start(self):
        self.instrument = self.cache.instrument(self.instrument_id)
        self.subscribe_bars(self.bar_type)

    def on_bar(self, bar):
        self.closes.append(float(bar.close))
        if len(self.closes) < self.slow:
            return
        closes = list(self.closes)
        fast, slow = sum(closes[-self.fast:]) / self.fast, sum(closes) / self.slow
        q.plot("fast", fast)
        q.plot("slow", slow)
        above, before = fast > slow, self.above
        self.above = above
        if before is None or above == before:
            return
        held = self.position_size(self.instrument_id)
        if above:
            if held < 0:
                self.close(self.instrument_id, reason="cross_up")
            if held <= 0:
                self.open(OrderSide.BUY, float(bar.close), "cross_up")
        else:
            if held > 0:
                self.close(self.instrument_id, reason="cross_down")
            if self.shorts and held >= 0 and q.can_short(self.instrument_id):
                self.open(OrderSide.SELL, float(bar.close), "cross_down")

    def open(self, side, close, why):
        quantity = self.instrument.make_qty(min(self.max_position * self.equity() / close, self.affordable(self.instrument_id, close)))
        if quantity.as_double() > 0:
            self.submit_order(self.order_factory.market(self.instrument_id, side, quantity, tags=["entry:" + why]))
'''

PRESETS = {
    "ranked": {"label": mark("Top N by signal"), "description": mark("Holds the top names of a library signal, optionally shorting the bottom ones, re-ranked as new signals arrive."),
               "source": RANKED, "shortParameter": "short_n"},
    "rebalance": {"label": mark("Rebalance to weights"), "description": mark("Holds the weights an allocation in the library publishes, trading to each new set."),
                  "source": REBALANCE, "shortParameter": None},
    "signal": {"label": mark("One name on a signal"), "description": mark("Buys one name when a library signal is above a level and sells when it is below another."),
               "source": SIGNAL, "shortParameter": None},
    "breakout": {"label": mark("BTC breakout"), "description": mark("Enters on a close above the last N bars with an ATR stop, an ATR trailing stop and a channel exit."),
                 "source": BREAKOUT, "shortParameter": "shorts",
                 "data": {"symbols": [{"symbol": "okx:BTC/USDT:USDT"}], "timeframe": "1h", "years": 2}},
    "crossover": {"label": mark("Moving-average crossover"), "description": mark("Long when the fast average is above the slow one, flat otherwise."),
                  "source": CROSSOVER, "shortParameter": "shorts",
                  "data": {"symbols": [{"symbol": "US:SPY"}], "timeframe": "1d", "years": 10}},
}
