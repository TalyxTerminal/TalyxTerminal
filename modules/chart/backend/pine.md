# Bring over a TradingView Pine script

The user gives you a Pine script; you write it as a Chart user indicator (saving, checking and the
folder are in the `indicators` topic). Chart runs PyneCore 6.10.7: Pine's API in Python. This page
is only what goes wrong when you translate line by line.

## Before you write anything

- **No source, no conversion.** A protected or invite-only script shows no code: tell the user it
  cannot be brought over. Do not rebuild it from its description.
- **`strategy()`.** Chart runs indicators. Orders, positions and backtest results do not exist here
  (`strategy.entry` and the rest do nothing). Say so, and offer to bring over what it draws: write it
  with `@script.indicator`, keep its plots, turn its entry conditions into plots or
  `alertcondition`s.
- **`import user/Lib/1 as lib`.** Ask the user for the library's source and convert it too, as a
  module in the same indicator folder (a multi-file indicator: `main.py` does `import lib_name`).
  If the script uses only one or two of its functions, you may write those from the library's
  published documentation. If you cannot get the source, tell the user which library is missing;
  never write a guess of it.
- **A Pine function PyneCore lacks** (for example `math.clamp`): write the same thing in the script
  and say which one you added.
- **Different behaviour.** If the code fails here or gives other values where TradingView runs it
  (an index out of range on the first bars, a na that TradingView tolerates), tell the user that
  Chart may differ from TradingView here and what you saw. Do not quietly change the logic until
  the error goes away.

## Pine to PyneCore

| Pine | Here |
|---|---|
| `//@version=6` + `indicator("T", "S", overlay=true, format=format.volume, precision=2, max_lines_count=100)` | `"""@pyne"""` first, then `@script.indicator("T", shorttitle="S", overlay=True, ...)` on `def main(...)`; the same keyword names |
| `indicator(timeframe="W")` | not supported (the run fails); take the higher timeframe with `request.security` |
| `len = input.int(14, "Length")` | a parameter of `main`: `length: int = input.int(14, "Length")`; params are keyed by the parameter name |
| `input.color`, `input.enum`, `input.time`, `input.source` | the same calls; an enum is a Python `Enum` class; `input.time` takes UTC ms (0 shows as empty). Write colours with `input.color`, not `input(color.red)`: a changed value of the bare form comes back as text |
| `var x = 0` / `varip x = 0` | `x: Persistent[int] = 0` / `x: IBPersistent[int] = 0` (from `pynecore.types`) |
| `x[1]` on your own variable | declare it `x: Series[float] = ...` (built-ins and `ta.*` results need nothing) |
| `na`, `na(x)`, `nz(x, 0)` | `na(float)` for a typed na; `na(x)` to test, never `x is None`; `nz(x, 0)` |
| `c ? a : b`, `:=`, `else if` | `a if c else b`, `=`, `elif` |
| `for i = 0 to n` | `for i in range(n + 1)`: Pine's bound is inclusive; `for i = n to 0` is `range(n, -1, -1)` |
| block scope | Python's is the function: a name Pine declares again inside an `if` is a new variable there; rename it |
| last expression is the result; `[a, b] = f()` | `return a, b`; `a, b = f()`; `_` for an unused part |
| `type Pivot` + `Pivot.new(p, 0)` | `@udt class Pivot:` with typed fields and defaults (`from pynecore.core.pine_udt import udt`); `Pivot.new(p, 0)` works |
| `method touch(Pivot this, float v)` + `pivot.touch(v)` | a plain function `def touch(this: Pivot, v: float)` called as `touch(pivot, v)`; the dot form fails on your own types |
| arrays, `a.push(x)`, `array.from(1, 2)` | Python lists with the `array` functions: `array.push(a, x)`, `array.get(a, i)`, `array.from_items(1, 2)` |
| `map.new<string, float>()`, `matrix.new<float>(2, 2, 0)` | `map.new()` (a dict), `matrix.new(2, 2, 0.0)`, with the `map` and `matrix` functions |
| `str.tostring(x, "#.##")`, `str.format("{0}", x)` | `string.tostring(...)`, `string.format(...)` (the module is `string`) |
| `#FF000080`, `color.new(c, 50)` | `color.rgb(255, 0, 0, 50)` (the last is transparency), `color.new(c, 50)` |
| `line.new`, `label.new`, `box.new`, `table.new`, `polyline`, `linefill` | the same; keep what you draw in a `Persistent[list]` to move or delete it |
| `request.security("BINANCE:ETHUSDT", "D", close)` | the same call; a symbol is Market's `VENUE:CODE` (`"OKX:ETH/USDT"` or `"OKX:ETHUSDT"`). TradingView's prefixes are not all Market's venues (`NASDAQ:`, `CME:`): look the symbol up with `market.instruments.search`. `syminfo.tickerid` is the chart's own |
| `request.security(..., close[1], lookahead=barmerge.lookahead_on)` | the same call: every chart bar gets the previous period's close, including the forming bar. Without the offset, `lookahead_on` shows the containing period's final value early on history and follows its developing value in real time, as in TradingView. |
| `request.security_lower_tf(...)` | the same: a list per chart bar |
| `alertcondition(cond, "Title")` | `alertcondition(cond, title="Title")`; Chart's alerts trigger on it. `alert()` reaches no alert here |

Plot titles are the output ids users and alerts see: keep the script's. Every plot is called on
every bar, as in Pine.

## A whole script

```pine
//@version=6
indicator("Trend strength", overlay=false)
up = input.color(color.teal, "Up color")
dn = input.color(color.red, "Down color")
var int streak = 0
streak := close > close[1] ? streak + 1 : 0
[macdLine, signalLine, _] = ta.macd(close, 12, 26, 9)
yesterday = request.security(syminfo.tickerid, "D", close[1], lookahead = barmerge.lookahead_on)
spread = macdLine - signalLine
plot(spread, "Spread", spread >= spread[1] ? up : dn, style = plot.style_columns)
plot(streak, "Streak")
plot(100 * (close / yesterday - 1), "From yesterday %")
alertcondition(ta.crossover(macdLine, signalLine), "MACD cross")
```

```python
"""@pyne"""
from pynecore.lib import alertcondition, barmerge, close, color, input, plot, request, script, syminfo, ta
from pynecore.types import Persistent, Series


@script.indicator("Trend strength", overlay=False)
def main(up: color.Color = input.color(color.teal, "Up color"),
         dn: color.Color = input.color(color.red, "Down color")):
    streak: Persistent[int] = 0
    streak = streak + 1 if close > close[1] else 0
    macd_line, signal_line, _ = ta.macd(close, 12, 26, 9)
    yesterday = request.security(syminfo.tickerid, "D", close[1], lookahead=barmerge.lookahead_on)
    spread: Series[float] = macd_line - signal_line
    plot(spread, "Spread", color=up if spread >= spread[1] else dn, style=plot.style_columns)
    plot(streak, "Streak")
    plot(100 * (close / yesterday - 1), "From yesterday %")
    alertcondition(ta.crossover(macd_line, signal_line), title="MACD cross")
```

A type with a method, and a function PyneCore lacks:

```python
"""@pyne"""
from pynecore.core.pine_udt import udt
from pynecore.lib import close, high, math, plot, script, ta
from pynecore.types import Persistent


@udt
class Level:
    price: float = 0.0
    hits: int = 0


def touch(this: Level, value: float) -> Level:  # Pine: method touch(Level this, float value)
    if math.abs(value - this.price) <= this.price * 0.001:
        this.hits += 1
    return this


def clamp(value, low, high):  # Pine's math.clamp, missing in PyneCore
    return math.max(low, math.min(high, value))


@script.indicator("Level touches")
def main():
    level: Persistent[Level] = Level.new(ta.highest(high, 50), 0)
    if ta.change(ta.highest(high, 50)) != 0:
        level = Level.new(ta.highest(high, 50), 0)
    touch(level, close)
    plot(clamp(level.hits, 0, 10), "Touches")
```

A library brought over as a module of the indicator's folder. Save `main.py` first, then
`levels/bands.py`; `main.py`'s check reports the missing module until then, so save it again after:

```python
# levels/bands.py -- the converted library
"""@pyne"""
from pynecore.lib import ta


def envelope(source, length, width):
    basis = ta.sma(source, length)
    return basis * (1 + width), basis * (1 - width)
```

```python
# levels/main.py -- Pine: import someone/Bands/2 as bands
"""@pyne"""
from pynecore.lib import close, plot, script

import bands


@script.indicator("Envelope", overlay=True)
def main():
    upper, lower = bands.envelope(close, 20, 0.02)
    plot(upper, "Upper")
    plot(lower, "Lower")
```

## Check before you hand it over

1. Save it with `chart.write_indicator`; correct the line `check.error` names until it loads.
2. Put it on a chart and read `chart.read_data`: `indicators[].error` (with `line`) says what failed
   on real bars; look at a few values, not only that there is no error.
3. If it reads another timeframe or lower-timeframe bars (`request.security`, `lookahead`,
   `security_lower_tf`), ask the user to compare a few signals with TradingView: which bar they land
   on, and whether they move while the bar forms.
4. Tell the user what you changed from the original (functions you wrote, libraries converted,
   strategy parts left out) and anything that may behave differently from TradingView.
