# Chart

Chart is the TalyxTerminal module for charting and watching markets, modelled on TradingView: charts, indicators, drawings, comparison symbols, watchlists, alerts, screens, templates, replay and export. Instruments, history, live data and calendars all come from [Market](../market/README.md); Chart stores no market data and keeps no calendar or connection state of its own. The editor for writing indicators belongs to [Developer](../developer/README.md).

The public interface is defined by [public/contract.json](public/contract.json). The chart engine is TradingView Lightweight Charts (Copyright TradingView, Inc., https://www.tradingview.com/).

## What you see

- **Layouts and charts.** One Chart panel is one layout holding 1–8 charts, with at most 8 charts per workspace in total. A layout can sync symbol, timeframe, crosshair, time and date range. A new panel with no target opens OKX BTC/USDT spot on the daily timeframe; a chart added to a layout copies the current chart's symbol and timeframe.
- **Chart documents.** Each chart is one document containing:
  - the full series identity: instrument, timeframe, price basis (last, mark, index), adjustment and anchor, import set;
  - the chart type: candles, hollow candles, bars (OHLC), line, line with markers, step line, area, HLC area, baseline, columns, high-low, Heikin Ashi, and five brick types: Renko, Kagi, P&F, Line break and Range;
    - When Renko, Kagi, P&F or Range has no size set, the size is fixed once from ATR(14) when the chart opens. Renko and P&F boxes equal the ATR (TradingView's default); a Range bar's height equals the Renko brick; Kagi converts by its reversal count (box = ATR ÷ reversal count, so the reversal amount is exactly the ATR). Renko and P&F boxes start from the first price of the first bar in the window (its close when using closes, its open when using OHLC) and are not aligned to multiples of the box size. TradingView's documentation does not say how it chooses the starting point.
    - Each Range bar spans exactly one height from low to high: an up move closes at low plus height, a down move closes at high minus height, and the part that first moved the other way remains as a wick (TradingView's method).
    - Kagi is drawn as one continuous line, as in TradingView: vertical lines are joined by horizontal shoulders and waists; rising above the previous shoulder turns the line thick (yang, up colour), falling below the previous waist turns it thin (yin, down colour).
    - P&F is drawn as X and O columns, as in TradingView: one mark per box from the column's open to its close, up columns as X in the up colour, down columns as O in the down colour.
  - panes, indicator instances, drawings, comparison symbols and display settings.
- **Edits, undo and saving.** The panel, the Agent and other modules all change charts through the same command set, carrying a `baseRevision`; a revision mismatch returns `conflict`. Each chart keeps 100 undo steps in memory, lost when TalyxTerminal closes; Agent edits go onto the same undo stack. Saving the workspace records the saved revision. Closing a workspace without saving reverts charts to their saved revision and deletes charts that were never saved.
- **Data window.** A chart first loads the latest 10,000 bars (intraday and daily alike). Scrolling back loads earlier bars in segments, up to 30,000 bars per chart. Jumping to a date outside the window loads back to that date; beyond the limit, the jump is refused with an explanation.
- **Indicators.** There is one kind of indicator: a Python script run by PyneCore (Pine semantics, but not the Pine language). 44 indicators are built in, in `backend/builtin/`; your own live in the user directory (see below). If an indicator cannot compute, only its own legend shows the reason; everything else carries on.
  - Volume is an indicator too. New charts come with the built-in Volume (TradingView's approach); it can be hidden, reconfigured or removed, and added back from the indicator list. It declares `overlay=true, scale=scale.none`: it sits in the bottom fifth of the main pane on its own hidden axis and takes no ticks from the price axis.
- **Drawings.** Anchors store real time (UTC milliseconds) plus price, never pixels or indices. Drawings are stored per instrument and price basis: switching symbols switches to that symbol's set; switching adjustment does not. Drawings on brick charts are stored by brick index and belong only to that chart and chart type.
- **Comparison symbols.** Values are taken at the main series' bar times (the last bar at or before that moment); no times are inserted into the main time axis. They can share one percentage axis or go on a new axis on the left or right.
- **Watchlists.** Each workspace can have several lists, with groups inside a list, up to 200 symbols per list. Quotes are subscribed from Market directly by the frontend. A new workspace starts with one list of common symbols; once deleted it does not come back.
- **Alerts and screens.** See [Alerts](#alerts). A screen checks whether each member's latest closed bar meets all conditions; members are a set of instruments or a watchlist, with at most 5 conditions. Saving a screen pins the indicator code it uses.
- **Templates.** Indicator templates, chart-settings templates and drawing-style templates. Templates are global, shared by all workspaces.
- **Research layers.** Another module (currently [Quant](../quant/README.md)) hands Chart a layer snapshot of research results: instrument, timeframe, adjustment, period, panes other than the price pane (above or below it), series (lines, steps and areas on price or in panes, coloured by role: main series in the text colour, benchmark as a thin grey line, comparison as a grey dashed line, the rest in the data colour order) and buy/sell markers (buys in the up colour, sells in the down colour, following the up/down colour setting; colours set by the chart itself take precedence). Chart draws only the values it is given and never recomputes them with its own indicators: a slight difference in parameters or algorithm would not match, and nobody could tell.
  - Timing: each point is drawn on the bar where "bar start ≤ point time < next bar start". When the chart's timeframe is coarser than the layer's, the last series point in a bar is used; all buy/sell markers are drawn.
  - Layers are saved with the chart. When the chart's instrument or adjustment differs from the layer's, the layer is not drawn, and its row in the object list says which instrument and adjustment it belongs to; switching back to the layer's instrument also restores its adjustment and the layer reappears. When a layer is newly added, the chart jumps to its period, loading earlier bars the same way as jumping to a date.
  - A layer is a snapshot and does not follow its source. After the source reruns, open it in Chart again to update.
- **Embedded research chart.** Other modules embed the read-only `layer_chart` view to show one layer, using Chart's engine and Market's bars. The price pane and other panes share a time axis with a linked crosshair. It can zoom and pan, but cannot draw and is not saved; it opens covering the layer's period and loads nothing beyond it. **Open in Chart** (top right) creates a chart with this layer and opens it; if the workspace already has a chart with a layer of the same id, that chart switches to the layer's instrument, timeframe and adjustment and the layer is replaced, instead of creating a new chart.
- **Replay and export.** Replay steps forward bar by bar from the selected bar, with its own data and indicator instances; the live chart and alerts keep running. Stopping returns to the live state. Export writes the closed bars in the current window and every indicator output to CSV, JSON or Parquet.

## Data and live updates

- Each series has one feed: one Market `session` subscription plus the window of closed bars it builds. Charts, alerts and indicators on the same series and window share a feed. A chart that has been scrolled back gets its own feed with a longer window. Heikin Ashi and brick types are projections over the raw feed.
- Each chart has one data stream (private WebSocket `/streams/chart/<chartId>`). The first item is the document; after that, in order of occurrence:
  - `bars`: `replace`, `append`, or `splice` for projected chart types;
  - `update`: the forming bar, with `closeTime`;
  - `status`;
  - indicator rows and shapes, and `indicator_error`.

  When a forming update has not been sent yet, a newer one replaces it.
- **What counts as live.** Only `append` and `update` arriving after Market reports `live` or `delayed` are live. The initial segment and bars filled in during recovery only build state and never trigger alerts. Whether a bar is closed depends only on Market's `closeTime`; future times come from Market's `calendar.bar_times`.
- **Status.** Chart uses Market's states as they are (connecting, live, delayed, history only, closed, recovering, failed), with the reason. While connecting (before the first connection) the legend shows nothing; a reason appears only after an attempt fails. Recovering only happens after a disconnect. A `replace` swaps the whole window: indicator and comparison rows stay at their bars' positions in the new window (so there is no flicker when Market sends the latest page first and then the full window) until their own `replace` arrives. After Market sets `failed`, it stops retrying; charts and alerts show **Retry**, which resubscribes. Chart never polls on its own.

## Running indicators

- Indicators run in long-lived worker processes; user code never enters the host. By default there is a pool of 2–4 processes, and a new instance goes to the least busy one. Instances that read the visible range get a separate process (started when the first one appears), so constant recomputation while panning does not slow other indicators. Compiled worker code (including PyneCore-converted scripts) is cached in `pycache` in Chart's data directory, so recomputation does not convert scripts again.
- An instance = one definition (identified by a digest of its code) + one set of parameters + one feed window. A chart and an alert using the same instance share it. The host keeps each instance's current output; a late consumer first receives a replace.
- **Computation.** When the window changes (opening a chart, changing parameters, a `replace`, scrolling back), the whole window is computed once. After that, PyneCore's live stepping is used: the forming bar updates on every tick and is confirmed at close (TradingView semantics). Live stepping gives exactly the same values as a full recomputation.
- **Hangs and crashes.** If an instance does not answer in time (120 seconds for warm-up, then 10 seconds per step), its process is killed. The process restarts, its instances warm up again, and the legend explains why.
- **Health.** The workspace stream's snapshot and `health` item carry two things: the state of the indicator process pool (ok, starting, restarting, down, with a reason) and the write state of Chart's store (failing with the error after a write fails; back to ok after the next successful write). The public action `restart_indicators` (also called by **Restart** on the status item) restarts the whole pool.
- **`request.security`.** Other timeframes and other instruments are supported, including `security_lower_tf` and instruments and timeframes chosen through inputs.
  - Chart subscribes to Market for each (instrument, timeframe) pair and passes the data to PyneCore's subprocess through TalyxTerminal's PyneCore data plugin (`pine/feed.py`); the plugin fetches nothing itself.
  - When `security_lower_tf` only reads raw price fields (`open`, `high`, `low`, `close`, `volume`, `hl2` and so on), no subprocess is started; the worker reads the same data directly (`pine/intrabars.py`). For 10,000 15-minute bars with 1-minute intrabars, the subprocess takes 3 seconds; this takes about 0.15 seconds.
  - At most 40 pairs per script (TradingView's limit). Feeds no longer read by anyone are released after 5 seconds.
  - Live, a higher timeframe's value follows its forming bar; a full recomputation shows the closed bar. The two match once that timeframe's bar closes.
- **Visible range.** `chart.left_visible_bar_time` and `chart.right_visible_bar_time` are the chart's real visible range (`pine/visible.py`). The chart sends the range to the backend (`/charts/view`, as two bar times; when the right edge is at the latest bar it is "latest N bars", so the range follows new bars without recomputation). Indicators that read it (found by scanning the code; reading it in any file of the project counts) get one instance per chart; when the range changes, the running computation stops and restarts with the latest range. Such indicators cannot be used where there is no chart (alerts, screens, reads): alerts and screens do not list them as sources, `read_data` skips them when reading a chart and returns `unsupported` when asked for one by name. `last_bar_index` and `last_bar_time` on historical bars are the last bar of the whole window (Pine semantics).
- **Market's derived data** uses TradingView's convention: a suffix after the symbol, read with `request.security`. The suffix table lives only in `derived.py`:

  | Suffix | What | How it becomes bars |
  |---|---|---|
  | `_OI` | Open interest | Points stored by Market are placed on Market's bar grid for the script's timeframe; the forming bar adds the latest observation |
  | `_FR` | Funding rate | Settled rates only, as steps at settlement times; predicted rates are not used |
  | `_MARK` | Mark price | The contract's mark-basis bars |
  | `_INDEX` | Index price | The contract's index-basis bars |

  - `_MARK` and `_INDEX` work only on futures and perpetuals. When the source does not provide them, the indicator fails with Market's reason.
  - Closed bars use only stored points, so a bar closed live equals a later recomputation. Exception: when a source publishes 5-minute points late, the last one or two bars closed live on timeframes below 5 minutes may differ from a recomputation.

## Alerts

- **An alert** consists of:
  - target: one series, or a whole watchlist on a timeframe;
  - conditions: 1–5, all must hold to trigger;
  - frequency: once only, once per bar, once per bar close, once per minute;
  - expiry, message and channels: platform notification desktop pop-up, sound, phone.
- **Each side of a condition** can be a price, an indicator output, a drawing (a copy of its geometry, kept in sync with edits on the chart), a constant, or a Pine `alertcondition`. Operators: crossing, crossing up, crossing down, greater than, less than, entering / exiting a channel, inside / outside a channel, moving more than an amount within N bars (value or percent), and `triggered` (with `alertcondition`).
- **Alerts are watched only while their workspace is open**, including when minimized to the tray. Each watched series is a feed shared with charts, evaluated on events, never by polling.
- **No false alarms.**
  - Only live bars are evaluated. The initial segment, recovery and filled-in bars only build the baseline.
  - Crossings need a previous value as a baseline. A `replace` silently rebuilds the baseline without firing.
  - "This bar has triggered" is written to the store before the notification is sent, so the same bar never notifies again after a restart.
  - Periods when TalyxTerminal was not running are not evaluated afterwards.
- **When alerts pause.** While Market is recovering, alerts show the reason and keep waiting. On failure, alerts pause with the reason and resume when you click Retry. If an attached drawing is gone, the alert pauses with "The drawing was deleted".
- **Indicator code is pinned by content digest.** Changing an indicator on the chart leaves existing alerts unchanged, and they are marked "Uses an older version".
- Alerts take effect immediately and are not reverted by workspace saving. Trigger history is kept for 90 days.
- Alerts cannot be created on back-adjusted series (anchored at the earliest bar): the meaning of a price level would silently change with corporate actions.

## Public interface

- **Actions.** Write actions declare side-effect tags, and people and the Agent use the same set. Whether to ask the user is decided by the Agent from the tags; neither the platform nor Chart blocks. When the panel does the same thing, it calls the same backend function.

  | Action | What it does |
  |---|---|
  | `read_chart` | `list`: charts in the workspace; `one`: a chart's display settings, panes, indicators, drawings and comparison symbols; `templates`: list and read templates; `workspace`: alert defaults and runtime state |
  | `read_data` | Bars and indicator outputs of a chart, or of an instrument plus indicators (computed without opening a chart) |
  | `write_chart` | `create` makes a chart, optionally from an import set or as a copy of a chart in the same workspace, optionally with research layers (`layers`; a layer with the same id reuses the existing chart). `edit` and `remove` submit panel commands in batches against `baseRevision`, covering display, panes, drawings, indicators, comparison lines and layers (`kind=layer` adds or replaces by id); deletions and whole-group replacement use `remove` |
  | `backfill_chart` | Extends an open chart backwards by bar count or date; `read_data` can read the extended window |
  | `replay_chart` | Starts replay on an open chart, steps a given number of bars, returns to live |
  | `export_chart` | Exports the whole window's closed bars and indicator columns as CSV, JSON or Parquet; returns the row count and a file reference, kept for at least 24 hours |
  | `write_template` | Saves indicator, chart-settings and drawing templates from a chart, applies them by revision, or deletes global templates |
  | `write_indicator` | Creates, updates, checks or deletes user indicators |
  | `read_indicator` | The indicator catalogue, and one definition's inputs, outputs and source location |
  | `read_alert`, `write_alert` | Alerts and trigger history; `create`, `update`, `pause`, `resume`, `delete` |
  | `read_watchlist`, `write_watchlist` | Watchlists and quotes; `create`, `update`, `delete` |
  | `read_screen`, `write_screen` | Screens and results; `save`, `run`, `delete`, all run as platform tasks; cancelling the task cancels the run |

  Also `restart_indicators`: restarts the indicator process pool; Chart's status item **Restart** calls it (platform status items can only call public actions).

  - Read results are at most 20,000 characters (`read_data`: 18,000). Larger results return `too_large` with a count that fits; nothing is truncated. Times are ISO-8601 UTC; instruments use Market's text form.
- **View.** `layer_chart`: props `{ layer, focusTime? }`; with `focusTime`, that bar is centred and marked with the crosshair.
- **Research layer limits.** At most 200,000 bars of the layer's timeframe in its period, at most 200,000 points per series and at most 20,000 buy/sell markers. Beyond that, `too_large` states which limit was exceeded and by how much; nothing is truncated, and cutting the window is up to the module providing the layer. Large layers are delivered as a file reference with length and sha256.
- **Open targets.** There is no separate action for opening a chart; Market, other modules and the Agent use the platform's open targets (the Agent uses `platform.panels.open_target`). The objectId is Chart's public convention: `instrument:<id>[/<timeframe>][@YYYY-MM-DD][;import=<import set>]`.
  - It opens a new chart of that instrument; the timeframe defaults to daily.
  - With a date, the chart scrolls to that day; with an import set, it opens the imported series.
  - Market's panel uses this for **Open in Chart**.
- **Extensions.**
  - Implements Developer's `developer.document_source` (`indicator-docs`, read-only: the indicator authoring guide `backend/authoring.md` and the built-in indicators' code) and `developer.development_support` (`indicators`: no editing features; it only lists the authoring guide and three built-in examples in Developer's Help).
  - Implements the platform's `platform.status_item` (indicator process health, with **Restart**).
- **Settings.** Two, per workspace (following the global value unless the workspace sets its own), in Settings → Chart → General: pop-up for new alerts by default (`alertPopup`) and sound by default (`alertSound`). Each alert can override them. Sound is played by the Chart process itself.
- **Copying a workspace.** The copy gets copies of charts and watchlists (with new ids); alerts and screens stay in the original workspace. A copied panel switches to its own chart copy the first time it opens (through `/charts/adopt`); copying an unsaved copy again still lets the new copy's panels find their own charts.
- **Process.** Chart runs in its own process, declared with decorators and pydantic in [backend/entry.py](backend/entry.py); the contract is generated by a script. Each Pine indicator process is wrapped in an SDK Job, and the whole process tree stops when Chart stops. After Market restarts, Chart's `session` subscriptions resume from their cursors, so alerts never stop silently.
- **Private routes.** The frontend talks to the backend over private routes, shaped by `ROUTES` and the pydantic models in [backend/wire.py](backend/wire.py); the host supplies the workspace in a request header. There are also two WebSockets: `/streams/chart/<chartId>` (one chart's data stream) and `/streams/workspace` (changes to alerts, watchlists, screens and templates in the workspace, plus health). Each message is one item; the last is `{"end": null}` or carries an error. The frontend calls Market through Market's generated client, not through Chart's backend.

For how modules connect through public contracts, see [docs/architecture.md](../../docs/architecture.md).

## User directory

User indicators live in `indicators/` under the user directory the platform gives Chart: `<user_dir>/module-files/chart/indicators/`. When `TALYX_DATA_DIR` is set, `user_dir` is `user/` inside it; otherwise it is the repository's `user/`.

- An indicator is one `.py` file, or a folder with `main.py` (a multi-file project). Its identity is the content digest of all its code.
- Chart declares the directory to Developer in its manifest as an author directory; it is protected.
- Code pinned by alerts and screens is copied by digest into `pinned_code/` in Chart's data directory. If the original indicator is changed or deleted, the pinned copy still runs.

## Limits

- At most 8 charts per workspace; at most 30,000 bars per chart window. Alerts and screens always use the latest 10,000 bars.
  - While a chart has not been scrolled back, it shares instances with alerts and the values match.
  - After scrolling back, the chart gets its own instances. Cumulative values such as OBV or `ta.cum` depend on the starting point and may differ from those in alerts (TradingView behaves the same way).
- At most 200 symbols per watchlist; at most 50 members for a watchlist alert with indicator conditions. At most 5 conditions per alert or screen. At most 40 `request.security` pairs per script.
- Brick charts have no future time and do not support drawing alerts. Future time beyond Market's calendar coverage is neither drawn nor evaluated.
- On a forming bar, Market merges updates and delivers only the latest tick, so a fast round trip within a few seconds can be missed.
- Only Quant uses research layers and the embedded chart so far. Agent-specific undo, chart links and alert trigger sources are not supported yet.

See [docs/limits.md](../../docs/limits.md) for platform-wide limits.
