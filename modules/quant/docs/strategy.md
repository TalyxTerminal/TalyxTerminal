# Writing a strategy in code

A strategy is a Quant object whose code is one Python file, `strategy.py`, holding exactly one
class based on `q.Strategy` (NautilusTrader's `Strategy`, run bar by bar). Its data settings
choose the instruments, timeframe and dates; its run settings the capital, costs, fill timing,
shorting and benchmark; the code only decides when to trade.

## Save, check, run

1. `quant.create_object {kind: "strategy", name, code, data: {symbols: ["binance:BTC/USDT"],
   timeframe: "1d", start: "2022-01-01"}}`: `code` is the whole file. Change it later with
   `quant.update_object {objectId, code}`. The object's `entryUri` identifies its saved source for Developer.
2. The answer's `codeErrors` is the check (syntax and the declared settings, each with `file`,
   `line` and `message`). Fix and update until it is empty. Errors that only show while running
   (a wrong attribute, a missing class) come back in the run's failure with the line.
   If a new declaration conflicts with a saved parameter, the update is refused before writing.
   Supply the corrected value or reset that parameter with null in the same update.
3. `quant.start_run {objectId}`: the result (key numbers, trades, the days tested) reaches you
   when the run ends. More: `quant.read_run`, `quant.read_run_table {table: "trades"}`.

Set execution assumptions alongside the code in the same create call. For a stock run,
`run.capital` is USD, `fill` is `nextOpen` or `nextClose`, and `costs.commissionBps` and
`costs.halfSpreadBps` are basis points. `costs.impact` is the market-impact coefficient.
The risk-free series uses `riskFree:{basis:"irx"}`, `{basis:"zero"}` or `{basis:"fixed",pct}`.

A ready-made strategy (preset) runs TALYX's code; to change its code, copy it first
(`create_object {kind: "strategy", copyOf}`).

## The class

```python
"""RSI mean reversion: buy when RSI falls below the entry level, sell when it rises above the exit level."""
from collections import deque

import talyx_quant as q
from nautilus_trader.model import OrderSide


class RsiReversion(q.Strategy):
    def __init__(self):
        super().__init__()
        self.period = q.param("period", 14, min=2, max=100, label="RSI period")
        self.entry = q.param("entry", 30.0, min=1, max=50, label="Buy below")
        self.exit = q.param("exit", 70.0, min=50, max=99, label="Sell above")
        self.closes = deque(maxlen=self.period + 1)

    def on_start(self):
        self.instrument = self.cache.instrument(self.instrument_id)
        self.subscribe_bars(self.bar_type)

    def on_bar(self, bar):
        close = float(bar.close)
        self.closes.append(close)
        if len(self.closes) <= self.period:
            return
        prices = list(self.closes)
        moves = [b - a for a, b in zip(prices, prices[1:])]
        gain = sum(m for m in moves if m > 0) / self.period
        loss = -sum(m for m in moves if m < 0) / self.period
        rsi = 100.0 if loss == 0 else 100 - 100 / (1 + gain / loss)
        q.plot("rsi", rsi, pane="own")
        held = self.position_size(self.instrument_id)
        if rsi < self.entry and held <= 0:
            size = min(self.max_position * self.equity() / close, self.affordable(self.instrument_id, close))
            quantity = self.instrument.make_qty(size)
            if quantity.as_double() > 0:
                self.submit_order(self.order_factory.market(self.instrument_id, OrderSide.BUY, quantity,
                                                            tags=["entry:rsi_low"]))
        elif rsi > self.exit and held > 0:
            self.close(self.instrument_id, reason="rsi_high")
```

- `q.param(name, default, min=, max=, step=, label=, help=)`, `q.choice(name, default,
  options)`: settings the user and `update_object {params}` change. Literal arguments only:
  TALYX reads them from the code without running it. Read them in `__init__`.
- `on_start` subscribes to bars; `on_bar(bar)` runs on each closed bar (`bar.open`, `bar.high`,
  `bar.low`, `bar.close`, `bar.volume`; `float(...)` for numbers). An order decided on a bar fills
  at the next bar's open (the run setting `fill` may say next close).
- Several instruments: `self.instrument_ids` and `self.bar_types` (in the data settings' order);
  `bar.bar_type.instrument_id` says whose bar it is. `self.instrument_id` and `self.bar_type` are
  the first.
- Keep your own state on `self` (a `deque` of closes, the last signal). The first bar is the
  data settings' start: a 14-bar RSI gives its first value on the 15th bar.

## Trading helpers on `self`

- `equity()`: the account's value; `position_size(id)`: held units (negative short);
  `last_close(id)`; `open_orders()`.
- `affordable(id, price)`: the largest new position the free balance pays for, fees and margin
  included; size entries with `min(self.max_position * self.equity() / price, affordable)` or the
  venue refuses them. `max_position` is the run setting (share of equity).
- `order_target_weight(id, weight)`: trade to a share of equity (negative is short; 0 closes).
  `rebalance_to({id: weight})`: several at once, sells first. `close(id, reason=)`: flat.
- Nautilus orders: `self.order_factory.market(id, OrderSide.BUY, self.instrument.make_qty(n),
  tags=["entry:why"])`, `limit(...)`, `stop_market(...)`, then `self.submit_order(order)`.
- `q.can_short(id)`: whether this run may short it (perpetuals, or stocks when allowed).
- `q.plot(name, value, pane="price" | "own")`, `q.stat(name, value)`, `q.log(text)`: shown with
  the run's results.
- `q.series("funding_rate")`: a Market series of the instrument up to now (read in the code as a
  literal name).

## Packages

The strategy environment has Python 3.13, `nautilus_trader`, `numpy`, `pandas`, `pyarrow` and the
standard library. Quant's common code folder (shared by every object, on the import path) and
extra packages (Quant's Packages page) are the user's to change.
