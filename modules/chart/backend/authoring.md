# Writing Chart indicators

A Chart indicator is a Python script run by PyneCore, which follows TradingView's indicator API
(`ta`, `plot`, `input`, `request.security`, ...).

## Put a new indicator on a new chart

1. Write the script using the examples below. Save the whole file in one call:
   `chart.write_indicator {"operation":{"op":"create","name":"new_high.py","content":"<Python source>"}}`.
   Its reply includes the name and the load check. If `check.error` has a message, correct that line
   and save again with `"op":"update"`.
2. Use that name when creating the chart:
   `chart.write_chart {"operation":{"op":"create","symbol":"AAPL","timeframe":"1d","indicators":[{"name":"new_high.py"}]}}`.
3. Show the chart with `platform.panels.open_target` using the returned `openTarget` object itself;
   in the same step, read `chart.read_data {"chartId":"<returned id>","limit":30}` to check its values.

## Where your indicators live

Chart resolves the relative name in `write_indicator` inside its indicator library
(`module-files/chart/indicators/` in the personal files directory). That name is all the action
needs to locate the file. Each indicator is either:

- one `.py` file (its name is the file name, `chan.py`), or
- a folder with a `main.py` (a multi-file project, named after the folder, `chan`). `main.py`
  imports its other modules by name (`import helpers`).

Chart lists everything in that folder next to the built-ins. An indicator's identity is the
content of its code, so a change you save is a new version.

Create a multi-file indicator's `main.py` first, then save its helpers by relative name.
For an existing chart use `{"kind":"indicator","name":"chan.py"}` in an edit. Runtime errors
come from `chart.read_data` (`indicators[].error`, with `line`). To change one of the user's indicators,
`chart.read_indicator {"view": "one", "name": ...}` gives its code's `uri`; read it with
`developer.documents.open {"source": {"kind": "file", "uri": ...}}` and `developer.documents.read`.

## A script

```python
"""@pyne"""
from pynecore.lib import close, color, input, plot, script, ta

DESCRIPTION = "Distance of the close from its moving average, in percent."
CATEGORY = "Trend"

@script.indicator("Distance from MA", shorttitle="Dist", overlay=False)
def main(length: int = input.int(50, "Length", minval=1)):
    average = ta.sma(close, length)
    plot(100 * (close - average) / average, "Distance", color=color.teal)
```

- The file starts with the `"""@pyne"""` docstring. A helper module that uses series values
  (`close[1]`, `ta.*`) needs it too; plain Python helpers do not.
- `@script.indicator(title, shorttitle=..., overlay=...)` declares it. `overlay=True` draws on
  the price pane, `False` in its own pane. With `overlay=True`, `scale=scale.none` puts the plots
  on a hidden scale of their own in the pane's bottom fifth, like the built-in Volume.
- Parameters of `main` with `input.*` defaults are the indicator's settings.
- `DESCRIPTION` and `CATEGORY` are optional. Chart shows them in the indicator list. Without a
  category, the indicator goes under "Other".
- `alertcondition(condition, title="...")` gives alerts a condition to trigger on.

## State across bars

`main` runs once per bar, oldest first, like a Pine script. Its local variables start again on
every bar, except:

- `Persistent[T]` (from `pynecore.types`, Pine's `var`): `count: Persistent[int] = 0` is set
  once and keeps its value from bar to bar; a list kept this way
  (`points: Persistent[list] = []`) holds whatever the script collects, such as pivots.
- `Series[T]` (from `pynecore.types`) and the price series index back in time: `close[1]` is
  the previous bar's close, `value[2]` two bars ago. `bar_index` numbers the bars from 0.
- `na` is a missing value; `na(x)` tests for it. `ta.pivothigh(high, left, right)` and
  `ta.pivotlow(low, left, right)` give a pivot `right` bars after it, at the bar it happened.

A stateful script can keep a typed scalar and publish a named value only on event bars:

```python
"""@pyne"""
from pynecore.lib import color, high, na, plot, script
from pynecore.types import Persistent

@script.indicator("New high", overlay=True)
def main():
    highest: Persistent[float] = na(float)
    fresh = na(highest) or high > highest
    if fresh:
        highest = high
    plot(highest if fresh else na(float), "New high", color=color.green, style=plot.style_circles, linewidth=2)
```

Each plot title names its output. Calling `plot` on every bar keeps its column aligned; a missing
value leaves that bar empty.

Drawings follow the bar indexes: `line.new(x1, y1, x2, y2, color=..., width=2)` and
`label.new(x, y, text, style=label.style_label_down)` with `x` a `bar_index`; keep what you draw
in a `Persistent` list to move or delete it later (`line.delete(item)`).

## What a script can use

- `pynecore.lib`: price series, `ta`, `math`, `input`, plots (`plot`, `plotshape`,
  `plotcandle`, `hline`, `fill`, `bgcolor`, ...), drawings (`line`, `label`, `box`, `table`,
  `polyline`, `linefill`) and `alertcondition`.
- The Python standard library, and the other modules of its own folder.
- `request.security` and `request.security_lower_tf` for other symbols and timeframes. A symbol
  is written `VENUE:CODE`, for example `"OKX:ETH/USDT"`; `syminfo.tickerid` is the chart's own.
  A lower timeframe of raw price fields only (`request.security_lower_tf(syminfo.tickerid, "1",
  (high, low, volume))`) is the fastest: it is read directly, with no script run per intrabar.
- `chart.left_visible_bar_time` and `chart.right_visible_bar_time`: the chart's visible range.
  The script runs again when the chart is scrolled or zoomed, so keep it quick; it runs on charts
  only, not in alerts or screens.

### Market's derived data

Add a suffix to a symbol to read Market's derived series as bars with `request.security`:

| Suffix | Series |
|---|---|
| `_OI` | Open interest |
| `_FR` | Funding rate (settled rates only) |
| `_MARK` | Mark price |
| `_INDEX` | Index price |

`_MARK` and `_INDEX` only work for futures and perpetual contracts. When the source does not
provide a series, the indicator fails and says why.

```python
oi = request.security(syminfo.tickerid + "_OI", timeframe.period, close)
```

## Examples

- Relative Strength Index (`rsi`): inputs, a plot, bands and a fill.
- Open Interest (`open_interest`): `_OI` as candles.
- Derivatives Basis (`derivatives_basis`): `_MARK` and `_INDEX`.
- Cumulative Volume Delta (`cumulative_volume_delta`): `Persistent` values.
- Fixed Range Volume Profile (`fixed_range_volume_profile`): boxes and lines kept in
  `Persistent` lists.

Every built-in's code reads the same way: `developer.documents.read_source {"source": {"kind":
"contribution", "providerId": "chart:indicator-docs", "sourceKey": "builtin/rsi"}}`.

## Limits

- An indicator runs on the chart's window: the latest 10,000 bars, up to 30,000 when you scroll
  back. Alerts and screens always use the latest 10,000.
- A script can request at most 40 symbol and timeframe pairs (TradingView's limit).
- A script that takes longer than 120 seconds for its history, or 10 seconds for one new bar,
  is stopped and its legend says so.
- If a script fails, only its own legend shows the error, with the line number when known.
  Other indicators keep running.
- Alerts and screens keep the code they were made with. After you change an indicator, an
  existing alert keeps the old version and is marked as outdated.
