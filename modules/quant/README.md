# Quant

See the [user guide](../../docs/user-guide.md#5-quant) for the current workflows and settings.

Quant is where professional quantitative research happens in TalyxTerminal: factors, models, asset allocation, strategy backtests and validation, a library of published results, and notebooks.

Every object has its own code (or the settings of a standard workflow), data settings and parameters. A run freezes and records the code, parameters, data and environment it used. Research output is published to the library, kept up to date on a schedule, and read by strategies, allocations and Chart.

Quant works at bar level. Factors, models and allocations run on daily or hourly data; strategies run mainly on daily and hourly bars. Stocks and crypto are designed for deep professional data, not narrowed to what the current data sources in [Market](../market/README.md) can supply. Where Market cannot provide something, the interface is still there, and Quant says once, at the point of use, what is missing.

Design references: WorldQuant BRAIN (factors), Qlib task configuration and the MLflow experiment table (models), QuantConnect and the TradingView strategy tester (strategies), Portfolio Visualizer and Bloomberg PORT (allocation), the MLflow model registry, feature stores and Numerai (library). Institutional workflow such as multi-person approval and compliance sign-off is out of scope.

Deployment, live trading and a Trade module (accounts, real orders, exchange reconciliation) are not part of this release; they are on the roadmap. Quant does research and backtesting only.

Contents: [Shared rules](#shared-rules) · [Factors](#factors) · [Models](#models) · [Strategy backtests](#strategy-backtests) · [Strategy validation](#strategy-validation) · [Asset allocation](#asset-allocation) · [Library](#library) · [Home and all objects](#home-and-all-objects) · [Notebooks](#notebooks) · [Deep learning](#deep-learning) · [Research code and data](#research-code-and-data) · [Historical index constituents](#historical-index-constituents) · [Stock trading terms](#stock-trading-terms) · [Models in strategies](#models-in-strategies) · [Packages](#packages) · [Environments and native computation](#environments-and-native-computation) · [Interfaces](#interfaces)

## Shared rules

### Work and Results

Factors, models, allocations and strategies share one frame with two sides:

- **Work** holds everything needed to make a decision: settings, the run button, the run list, the selected run's key numbers and checks, and its difference from the run it is compared with. Decision inputs such as pre-publish checks, similarity to the library and returns after costs live here.
- **Results** is the in-depth analysis of the selected run, on its own wider page.

All four follow the same loop: change, run, look, dig deeper. What goes inside the frame depends on each kind's job.

### Objects

Each object has an id, a kind (notebook, factor, model, allocation, strategy), an optional folder and a workspace. Lists default to the current workspace and can switch to "All".

An object's code is a multi-file [Developer](../developer/README.md) project; editing, saving and file history belong to Developer. The code area in a Quant panel is Developer's embedded project view. It can be closed and widened. It is open by default for objects you write in code, and collapsed for standard workflows (models, allocations) and one-line factor formulas. If Developer is not installed, Quant still works and the files are simply the files on disk.

New objects start empty:

- a factor is an empty formula box;
- a strategy is a minimal runnable skeleton (BTC perpetual, 1 hour, last two years by default);
- a model or allocation is the standard workflow with nothing selected.

TalyxTerminal ships presets. Creating from a preset gives you your own copy; later changes to the preset do not affect it. The standard model and allocation workflows are product features, not presets, so improvements apply automatically. To take full control, use **Write as code**: it copies the workflow's code into the project and keeps the same settings interface.

### Two layers of settings

**Layer one is declared statically in code**, and the interface is generated from the declarations:

- `q.param`: numbers (with optional `unit`, range and step), switches and text;
- `q.choice`: a drop-down; options can carry display names;
- `q.pick`: pick something from the library, or an unpublished research run; can be limited by kind and allow multiple values; the value is pinned to a version or to that run;
- `q.table`: a small table with fixed columns.

All accept `label`, `help`, `group` (items in one group share a section) and `show_if` (shown only for a given value of another item). Only literals are allowed. The backend reads declarations statically from every Python file in the project and never executes the code to do so. A mistake shows the line number and reason on the parameter row; a duplicate name shows both line numbers; a run with an invalid declaration fails immediately. Settings are shown by their labels, not variable names.

**Layer two is the run settings** the platform provides per kind: capital, costs, fill timing, shorting, benchmark, risk-free rate, held-out period and so on. Code can give defaults; values changed in the settings take precedence. Keeping these separate is what makes checks such as "does it still make money at double cost" possible. TradingView's Inputs and Properties are split the same way.

Option lists are generated from the engine's and Market's declarations, never hand-written. Remaining parameters of the selected algorithm or objective go in one **Engine parameters** line (`key=value`), validated against the engine class's parameter list.

### Data settings

One data-settings overlay is shared by all four kinds: an instrument list, a research list, an index's historical constituents, or "rule in code"; adjusted or raw prices; timeframe; date range.

- **End date.** The default is "Latest close": when a run starts, it takes the last bar each instrument's source has published (Market's publication cutoff, not just the close; A-shares, US stocks and crypto each follow their own calendar, not "the previous UTC day"). The run records the resolved date. You can also fix the end date. When two runs end on different days, comparisons say so; whether to rerun is your call.
- New objects use the full selected range.
- **Held-out period.** Set its start date in the run settings. The data settings show the date, and each run's `tested` field records the first and last dates actually tested.
- Only routes that Market marks as verified are listed for instruments and timeframes. If saved settings no longer point to a verified route, the run is refused before it starts and names the route, rather than failing halfway through fetching.
- Factor fields, model inputs and extra strategy series are generated from what Market declares it can supply for the selected instruments. If a formula or code uses something Market does not have, the pre-run check or the error says so once, at that spot.

### Research data storage

Market data is stored once, in Market. Quant never keeps a mutable copy.

- When a run is prepared, Quant goes through Market's public interface. Missing data is downloaded first (progress says which source it is waiting for and until when), exported from Market's local store, and frozen as the run's own immutable files.
- The version of each series (including corporate actions) is read before and after export; if it changed, the export is repeated. Later corporate actions and source revisions therefore never change an old run.
- A factor rerun with the same settings reuses Market's local data and only fetches missing date ranges. The previous frozen data is reused only when the series versions and research inputs are unchanged.
- For stocks, "adjusted" means split and dividend adjusted and "raw" means unadjusted; both are requested explicitly from Market (when Market does not specify, US and Hong Kong stocks are adjusted for splits only). Market cap and turnover use raw price times raw share count. Share counts apply from their announcement time; before the first announcement they are empty.
- Fetching is per instrument. If one instrument in a plain instrument list cannot be fetched, the whole run fails and names the instrument, Market's error code and message. Index members that cannot be fetched are dropped and listed (see [Historical index constituents](#historical-index-constituents)).
- In backtests, a bar's event time and availability time are both its actual close (Market only stores finalized bars past their publication cutoff). Daily labels are the exchange's local trading day.

### Runs

There is one run button. Its label follows the run kind ("Run backtest", "Sweep 30", "Walk-forward 7"). Setting a range on a parameter row turns it into a sweep.

A run:

1. saves all unsaved changes in the Developer project and fixes a code snapshot (if a file on disk was changed elsewhere while the editor also has unsaved changes, it stops and names the file);
2. freezes the data;
3. prepares the environment from its lock;
4. runs in a subprocess.

It records the code snapshot (including common code), the actual parameter and run-setting values, a data summary (range, gaps and end date per instrument), the environment lock, check results and duration.

- While running, a run shows the current step, elapsed time and a stop button, and finished parts can already be viewed.
- A failure points to the line of code (you can jump to it), with the original error, traceback and the local variables of the failing frame. Finished parts are kept. Data-stage errors quote Market's source, reason, instrument and period verbatim; Quant does not retry or switch sources.
- States: completed, failed, cancelled, interrupted by a TalyxTerminal restart (not rerun automatically). Every run that reaches a final state emits `research_completed`.
- Run history supports stars, notes, comparison (per-file code diff and the full run settings, not just data labels) and "Use this version", which writes that run's code snapshot back through Developer as a new version and restores its parameters and settings.

### Robustness and the held-out period

Once a held-out period is set in the run settings, ordinary runs of all four kinds stop before it.

- **Validate on held-out** is a separate run kind. It runs only after explicit confirmation in a centered dialog, and the run is marked afterwards. Before confirming, the dialog shows how many times the same formula or code has already been validated on the same held-out period. Validating the same run again returns the existing result and does not increase the count.
- Why: after choosing the best of dozens of variants, the test period has been used for choosing. Only the held-out period is clean, and once you have looked at it and changed something, it is no longer out of sample.
- Sharpe ratios are also deflated by the number of trials and shown next to the raw value. Strategies add a trade-order shuffle for drawdown and walk-forward validation.

Signal checks (factors, models, strategies) use the same thresholds everywhere:

| Check | Pass |
|---|---|
| Overlap-corrected t | ≥ 3 |
| Rank correlation with existing library items | < 0.7 |
| Names per group | at least 30 |
| Years with the same sign | at least 75% |
| Break-even cost | at least 2× the assumed cost |

Failed checks are marked; they do not block publishing. Allocations have their own set.

### Batches

All four kinds can run a batch (a sweep for strategies, a batch for models) with the same component. Each cell is an ordinary run that can be opened and compared; with two varying items you get a heat map.

TalyxTerminal does not choose the best combination for you. It marks which one is best by the selected metric; you choose what to show and what to keep. Picking the best combination is exactly the step that fits parameters to one stretch of history, so a person makes that choice, and every trial still counts toward the deflation. Random or Bayesian search is not offered: grids are enough, and automated tuning runs many more trials, which conflicts with trial-count deflation.

### Run queue and hardware

Quant has one run queue. Interactive work (checks, trials) goes first; background work (model batches, sweeps, library updates) is scheduled by CPU and memory. The queue is visible and can be stopped.

Parallelism, threads per job and the pre-run memory check are computed from the detected machine:

- CPU slots: half the number of cores;
- memory budget: half the machine's memory, leaving at least 4 GB for the system.

Jobs that do not fit are stopped before they start, showing the estimate and this machine's value.

The research process stays running (it exits after about 10 idle minutes, and restarts when data settings change or after an error). That is why a factor check returns in seconds and an allocation trial in about a second.

### Result charts

Native charts come first: charts and tables the engines provide are used as they are. Analyses the engines lack (style, held-out, costs, crisis periods, by year, baselines) are computed by the backend and drawn with the same template. All charts are produced by the backend as Plotly figures and shown through Quant's own chart layer.

Colours follow the series' role:

- the main series uses the text colour; benchmarks are blue, dark orange, then purple; the comparison run is grey; ordered groups go from cool to warm; heat maps and correlation matrices use one neutral hue;
- up/down colours are used only for gains and losses, long and short, and drawdowns (a light "down" colour). They follow your up/down colour setting, and visible charts are redrawn when it changes;
- orange is reserved for things that need action;
- `earlier` (the same thing at an earlier time, dark grey solid line, such as the risk shares at publication) and `watch` (amber: worth a look, no action yet, such as a risk share more than 10 points off target or a 1.5× volatility line) are coloured by the shared layer.

Hover readouts show + or − only for signed quantities and state the unit once. Each run draws one set of charts (per-period and per-window data is stored as numbers). If a chart cannot be produced, its section says why instead of silently leaving it out.

Price candles are always drawn by Chart's embedded view (`chart.layer_chart`), never with Plotly. Series such as IC or several equity curves are drawn by Quant.

### Research results on price

What a run looks like on price is a **research layer**, returned by `read_run(part="chartLayer")` (optionally with an instrument and `around`). The embedded chart, opening the run in Chart, and the Agent all read this same layer. To put a run on [Chart](../chart/README.md), the Agent reads it and calls `chart.write_chart` with `layers`.

- **Strategies.** Top pane: equity (plus benchmark and the run being compared). Bottom pane: candles, `q.plot(pane="price")` lines (single-instrument only: `q.plot` has no instrument argument) and the fills for that instrument (actual fill time, price and quantity). Each `q.plot(pane="own")` gets its own pane, also for multi-instrument strategies. Lines show only values the strategy code computed.
- **Factors and models.** Two panes: the raw value (prediction) and its percentile rank in that day's universe. Ranks are computed only among names with a value that day (missing values are not in the denominator), ties take the average rank, the range is 0 to 100, and a single name is 100. Values are drawn at the time they become available (a value computed after day D's close sits on day D's bar). Models are drawn from the first scored test day.
- Adjustment and timeframe follow the run's data settings. When a run has more than 200,000 bars, the most recent 200,000 are loaded by default; with `around`, the window is centered on it (within the run's range), and a secondary line in the title bar states the period shown. Backtests, reports and fill tables are not affected.

### Interface

A Quant panel shows one object at a time; several panels can be open. Panel state stores only `{objectId, runId, …}`, and other modules open things by `objectId`. Quant uses the SDK components, so it looks like the rest of the platform.

- Unbuilt features do not appear; there are no greyed-out items. Data that is unavailable is mentioned once, at the point of use. Something that should have appeared but failed explains itself in place. Hovers carry only definitions a professional needs.
- Areas can be resized and expanded to fill the panel; key numbers drill down to the data behind them.
- Run ids, code digests and file paths are never shown.
- Run state is pushed to the panel by the `object_run_changed` event; panels do not poll. Research pages read run events at most once per second: the first read is immediate, the last change is always read, and your own actions are read immediately. After the host reconnects, Quant rereads objects and runs; write requests are not replayed automatically.

## Factors

Decide whether an idea has something in it, what to change next, and whether to publish. The usual loop is: edit the formula, check (seconds), look at the numbers and groups, edit again. Modelled on BRAIN: an expression box, one run button, a few numbers, pass/fail checks and correlation with what you already have. BRAIN's composite Fitness score is not used; each number is shown as it is. Quant adds "values for one day": every name's value and raw inputs, so you can trace why.

- **Formula or code.** A one-line formula can use all public Qlib operators, plus cross-sectional operators added by TalyxTerminal: `CSRank`, `CSZScore`, `CSDemean`, `IndRank` and `Neutralize`. Formulas can only look back; look-ahead is prevented structurally. Errors are marked in the expression as you type: unknown function (with a suggested fix), unknown field, unbalanced parentheses, reading the future. Formulas can read library values (`$lib.<name>`). When a formula gets complex, **To code** converts it to code; both produce the same results page. One-line factors have no code settings and no code area.
- **Work.** The formula box sits at the top, with one row of key numbers directly below: Rank IC, overlap-corrected t, long-short annualized return after costs, turnover, coverage (names with a value per day), and correlation with the most similar library item, each with its difference from the comparison run (IC and t differences are not given when the two runs use different holding periods).
  - Main chart: cumulative return by group (group numbers at the line ends, plus a long-short line after costs). Beside it: IC decay for holding periods of 1 to 20 days, with the current holding period marked; clicking a day sets it and reruns.
  - Checks: the five shared checks, plus monotonic groups, look-ahead (each run recomputes after cutting prices beyond three dates), coverage collapse and suspicious data (a one-day 50% drop on normal volume, like an unadjusted split). Clicking a check goes to the place to fix.
  - Below: the run list (full formula per row), the most similar library items, a cross-section table for one day (raw inputs, value before and after processing, rank, group, subsequent return, reason for a missing value; click the main chart to change the date), and **Use this run to build a model / strategy** (no publishing needed; data settings carry over).
  - Selecting an older run switches the formula and settings to that run's, so the numbers always match the settings shown.
- **Run settings** (what a formula cannot declare, like BRAIN's Settings):
  - universe filters (suspended, newly listed, turnover); holding period; trade delay in days;
  - grouping: equal-count groups, fixed cut points, positive/negative split, within-industry groups (required for event-type factors and factors with a signed meaning);
  - outliers and standardization, and neutralization. The default each day is to clip at 3× median absolute deviation, standardize, and take residuals from a regression on industry dummies and log market cap. You can choose none or industry only. Asset classes without industry data from Market, such as crypto, default to none and industry-based options are hidden; if Market cannot supply industries for the universe, the run stops and names the setting;
  - return basis: Demeaned / Raw and Group neutral (Alphalens's `demeaned` and `group_adjust`). The Work numbers follow the same setting, so a page has one basis;
  - costs (default 5 bps per trade); held-out period.
  - There is no risk-free rate: factor numbers are self-financing long-short spreads or involve no money (IC, group means), so Sharpe does not subtract a rate. Unknown settings are rejected.
- **One run button.** One check computes everything on Work, including checks, library correlation and decay. With the research process running, results appear about 1–3 seconds after an edit. The full report continues in the background, outside the edit loop. Repeated sub-expressions are reused and style and library rankings are cached per dataset (invalidated when new data arrives), so a small edit does not recompute unchanged parts.
- **On price** (after Returns): pick a name (searchable, sorted by latest value, highest first) to see its candles with value and percentile-rank panes. See [Research results on price](#research-results-on-price).
- **Results** has eight pages: Returns, IC (by year and month), Industry (by industry, size and period), Style (correlation with styles; only styles that can be computed), Turnover (top/bottom turnover, rank autocorrelation), Costs (cost breakdown, net return by cost, by holding period), Event (event study), Settings (what ran, data and environment, reuse these settings). IC tables, group statistics, return tables, factor weighting, spreads, turnover and rank autocorrelation use Alphalens functions; cumulative groups, by-year, industry IC, events and costs are computed by Quant. The full Alphalens tear sheet (`create_full_tear_sheet`) is never called; all charts are Plotly from the backend.
- **Publishing**: see [Library](#library).
- **Not supported:** Chart indicators as a factor source (a single-instrument indicator does not form a cross-section; use Chart indicators in a strategy). BRAIN's separate decay and truncation settings (writing `Mean` or `WMA` in the formula is decay; weight truncation belongs to the strategy).

## Models

Decide which experiment in a batch to publish, or, if none qualifies, what to change in the next batch. One training takes tens of minutes to two hours, so the loop is: base a change on an earlier run, run the pre-run check, launch a batch, walk away, come back, sort the experiment table, open the top few to see whether they are stable and beat the baselines, then combine, validate on held-out, and publish or run another batch. Modelled on Qlib's task configuration (data, processing, label, split, rolling retrain, model, record map onto the preparation groups; the config file itself is not shown) and the MLflow / W&B experiment table (parameters and metrics as columns, sort, filter, tick rows to compare). The metric set is fixed so runs stay comparable.

- **One Work page.** Preparation on the left, expandable to full screen; the experiment table on the right, with comparison of ticked rows below it. Results is for a single run. New models open with preparation expanded and an empty table; processing, label and split have standard defaults, while universe, inputs and algorithm are empty.
- **Preparation** (groups follow the standard workflow's declaration):
  - **Data:** universe (index with daily constituents, filtering suspended, newly listed and illiquid names), date range, held-out period.
  - **Inputs:** a table, one row per input: a library factor (pinned to a version, or an unpublished run), the built-in Alpha158 feature set (filtered by constituents; columns needing VWAP are omitted because the price data has no VWAP), seven price styles, or your own. Each row shows source, version, start date and coverage; clicking a row shows distributions before and after processing and missing values per day.
  - **Processing:** set separately for inputs and label. Options are TalyxTerminal implementations with the same names and meaning as Qlib processors (robust standardization, standardization, min-max, cross-sectional standardization or rank, fill, drop missing, and others; cross-sectional rank is on the same scale as Qlib's CSRankNorm). Processing that fits statistics is fitted on the training segment only.
  - **Label:** written with the factor formula engine, so it can express targets other than returns (volatility, direction). The number of bars it looks ahead is derived from the expression and used for segment gaps and leakage checks. Shortcuts: 1, 5, 10, 20 bars × raw, cross-sectionally demeaned, cross-sectional rank, measured from the next bar's open to match strategy fills.
  - **Split:** lengths of training, validation and test; expanding or fixed-length rolling training window; retrain frequency; segment gap computed from the label length and shown; validation used for early stopping.
  - **Algorithm and parameters:** grouped by what the environment can run: trees (LightGBM, XGBoost, CatBoost), linear (including Lasso and non-negative least squares), ensembles (DoubleEnsemble, high-frequency LightGBM); neural networks are covered in [Deep learning](#deep-learning). Objective (regression, classification, ranking) and sample weighting appear only when the algorithm supports them. Random seed and a "reproducible" switch (multithreading with deterministic flags, so two runs match bit for bit).
- **Pre-run check** (no training, seconds): samples per window and names per day, input coverage, input pairs with correlation above 0.95, label distribution, look-ahead (an input available later than the label start), and memory estimate against available memory. Fitting statistics on training only and gaps by label length are guaranteed by the split, so they are not listed. Failures are marked but do not block, except insufficient memory (the run would fail anyway). A timeline across the top shows the range, held-out period, warm-up and each window's train / validation / test and gaps, with problems marked in place; with many windows, only the first few, the last and any with problems are drawn. Orange marks leakage and insufficient memory, amber too few samples; red and green mean only up and down.
- **Batch:** numeric parameters and options of the current algorithm can take several values (algorithm, inputs and label do not vary within a batch; launch separate batches, which land in the same table). Shown as a small "combination × changed item" table listing only differing cells, with estimated time and memory. Batches estimated to exceed ten minutes ask for confirmation. Parallelism is limited to what fits on this machine.
- **Experiment table:** one row per training, rows of one batch grouped, sorted by test Rank IC by default. Columns: state (rank in queue, current window and time left, failure with a one-line reason, resume for interrupted runs), star, note, published; what changed versus the reference run; inputs, label, algorithm; parameters that vary in the batch as automatic columns; test Rank IC, t, improvement over the strongest single input, long-short annualized return after costs, turnover, train-test gap, share of windows with positive test IC, checks passed, duration, data end date, whether held-out was touched. Filterable (including by published). The best combination is not chosen for you. Ticking several rows and choosing **Combine** averages them into a new row that can be published (averaging the same settings over several seeds is a common way to reduce randomness).
- **Selected row:** the same key numbers; the main chart shows per-window test IC bars (training and validation in lighter colours alongside) with cumulative test IC, against two baselines, "strongest single input" and "equal-weighted inputs". Seven checks: t, beats baselines, 75% of windows positive, test IC at least 10% of training IC (tree models naturally have high training IC, so the raw gap cannot tell good from bad; the table still shows it), correlation with inputs (does it just copy the strongest one), break-even cost, look-ahead. While running, finished windows are drawn and the run can be stopped. A failure names the window and the reason (for memory: roughly how much is needed and how much is available) and can resume from there. **Use this run to build a strategy.**
- **Results** (one run in depth): per-window table (IC in three segments, best iteration, samples, duration), learning curves, feature importance and its stability across windows, input contributions to each name's prediction on a given day, decay, group returns and costs, splits by year / industry / size, IC after removing styles, a portfolio test (top N versus average; N and costs in run settings), and all settings (reuse in one click). Comparing several runs shows settings differences, key-number differences and overlaid per-window IC.
- **Values** (a Results page: each name's prediction and input contributions on a given day). A run's inputs are built once, the first time you open that run (the page says the inputs are being built), stored next to the run and keyed by its data, settings, inputs and code; changing the day only reads them back. Only each window's test days and their look-back are kept, as float32, with no raw copy (a typical S&P 500 run is about 86 MB). One resident process per environment holds the run's inputs and window models. The first build takes about 2 minutes (107–135 seconds measured), with progress shown; changing the day then takes 0.3–2.1 seconds. Two simultaneous requests for the same day are computed once. Library inputs are exported to a table once per run. The page opens on the last test day whose subsequent return is already known, not the last test day (whose realized return and Rank IC are empty); other runs open on their own first day. Clicking a name jumps to **On price** (the page after Values) with that name selected.
- **Strategies from unpublished model runs.** "Use this run" builds a strategy that trades the run's out-of-sample test-window predictions. The signal carries the run's name and states which model run it came from; sweeps launched from that strategy keep the signal. The held-out confirmation lists the same windows the run actually trains (windows beyond the source run's are laid out by the schedule; if the source run's data has been cleared, the count is estimated as span divided by step).
- **Long trainings** need TalyxTerminal to stay open. After closing the workspace or restarting, training resumes from the last finished window; completion is reported through platform notifications.
- **Write as code:** copies the standard workflow code; settings still come from declarations. Code that uses `q.model_data`, `q.fit` and `q.record` gets the full per-window treatment. Code that does not call `q.record` gets only whole-period numbers in the table, and the main chart says there is no per-window data.
- **Publishing** produces one thing, a "model"; its predictions are the values it produces each period. Factors it uses that are not yet in the library are published with it in the same transaction, each with its own look-ahead check. Publishing is blocked if a factor version it uses has been stopped ("A factor version it uses has been stopped; retrain on the new version"), unless automatic updates are turned off. The published history is the run's per-window test predictions stitched together: every value comes from a model that never saw that period, not from the final model predicting backwards. The version records the training cutoff and the held-out start. On updates, between retrain dates the latest window's model predicts; on a retrain date, only the new window is trained. The look-ahead check at publication trains only the window at the cut point. Retraining the whole history every day would cost tens of minutes to two hours per model per day at professional scale.
- **Presets:** "LightGBM on Alpha158", "LSTM on Alpha360", "Ridge on the price styles".
- **Not supported:** Qlib online updating, DDG-DA, nested executors and reinforcement learning (daily predictions after publishing are handled by library updates); automated hyperparameter search (use a grid of parameter combinations); parallel-coordinate plots; order execution metrics.

## Strategy backtests

Decide whether a strategy is usable and what to change next. A strategy is Python in the object (`q.Strategy`, on the Nautilus engine), one strategy class per object. See [docs/strategy.md](docs/strategy.md) for writing one. Modelled on QuantConnect (one-line statistics, equity and drawdown against a benchmark, orders, logs) and the TradingView strategy tester (overview, list of trades, properties, Bar Magnifier). Those produce one result per backtest; Quant also has multi-combination runs, so the result area changes with the run kind (sweep, walk-forward and held-out each have their own view; see [Strategy validation](#strategy-validation)).

Strategies run only the Python strategy in the object, frozen Market bars and the extra series the code uses. Cash accounts do not borrow; short stock positions pay a borrow fee.

### Run settings

- **Capital:** stocks default to 100,000 USD, crypto to 10,000 USDT; presets size each trade as a fraction of capital. Multi-venue strategies have capital per venue.
- **Exchange conditions:** leverage, margin method, liquidation, stop triggers on last / mark / index price, one-way or hedge positions. Items that do not apply to the instrument are hidden (stocks have no mark-price trigger and no hedge mode). Selecting a mark-price or index-price trigger without that data selected is an explicit error.
- **Liquidation** follows the exchange's cross-margin rules, implemented by TalyxTerminal rather than the engine. Each perpetual position gets a reduce-only stop at its liquidation price, computed from the account balance, other positions' unrealized P&L and the maintenance margin of all positions. It fills intrabar when touched, or at the open on a gap. If at a bar's close the account equity is not above the total maintenance margin, all positions in that account are closed at the next price. Hedge mode uses only the second rule. These stops appear in the log as a separate category, "Liquidation stop", hidden by default.
- **Fill timing:** next bar's open (default) or next bar's close. "This bar's close" is not offered: computing a signal on a close and filling at that same close is impossible live.
- **Shorting.** **Benchmark.**
- **Risk-free rate:** US and Hong Kong stocks default to the 13-week US T-bill (^IRX), A shares to 4% per year, and crypto to 0. Rates were around 5% in 2022–2024; using 0 would inflate Sharpe.
- **Finer bars for matching**, which removes the "did the stop or the target hit first within the bar" approximation. The main chart still shows the signal timeframe.
- **Limit orders:** fill on touch, or require crossing by N ticks. These are deterministic rules rather than the engine's probabilistic fills, so results are reproducible.
- **Suspended instruments:** keep the position when it cannot trade, or close at the next price.
- **Held-out period.**
- Annualization follows the instrument's trading calendar (stocks 252, crypto 365). A page shows one Sharpe.

### Costs

One cost model: commission (from Market) + half the spread + impact (0.3 × daily volatility × √(participation in traded value), coefficient adjustable) + borrow fee (0.3% a year, adjustable) + perpetual funding (one entry per settlement time, from the position at that time and Market's historical funding rates).

- If funding history has a gap, that period is not charged, results are still produced, and the cost breakdown is marked "incomplete" with the missing period named. It is never silently treated as 0.
- Stocks can be shorted (as in QuantConnect: allowed by default, with an annual borrow fee). Free data has no "shares available to borrow", so the report says results are optimistic on this point.
- If Market cannot supply a venue's commission, or instruments on one venue have different rates, the run fails before starting and asks you to enter the rate in the run settings.
- Orders that would fail on insufficient funds for the commission are blocked before submission instead of crashing the backtest.

### Work

- **Top:** name, data range, run button and progress, stop.
- **Left:** both settings layers, with changed-but-not-yet-run items marked. Next to them, **Inputs used** lists library or research items the code reads (`q.model("name")` is not a parameter, so it is listed here), the pinned version or run, whether a newer version exists, and a switch to it (after switching, a note says results are not comparable with the previous run). Clicking an item opens the run it came from.
- **Key numbers:** annualized return, maximum drawdown, Sharpe, excess over benchmark (annualized minus benchmark annualized; alpha is in Results), annual cost drag, number of trades (round trips for multi-instrument), each with its difference from the comparison run. Total return is in the hover (it is not comparable when two runs cover different periods).
- **Main view:** after the run, the embedded research chart (equity on top, candles with buys and sells below; see [Research results on price](#research-results-on-price)); while running, a growing equity curve. For multi-instrument strategies, a list of instruments grouped by industry sits to the right (group total contribution; per row the symbol, contribution amount and share, number of fills and final weight; the largest absolute contributor is selected by default). Clicking a row switches the bottom pane; in narrow panels the list collapses into a selector in the chart title bar. Clicking a fill in the fills table moves the chart to it, reloading around it if needed.
- **Below:** rebalances, positions, contribution (multi-instrument) or the fills list (single instrument). Every equity curve shows a faint line for the comparison run. Beside it, a log ordered by simulated time that ties to each trade. A one-line run bar with a star.
- Strategies are not published: the library holds only time-updated data and trained models.
- **Failure:** the result area shows the line of code (click to jump), the date the backtest had reached, and the finished part with its key numbers. Single-instrument runs also show local variables at the failure; multi-instrument runs that fail on missing prices list the names with no price that day.
- **Time filters:** fills and rebalance lists are read through public actions by time (`since` / `until`), with the same semantics for the interface and the Agent. Long and short legs use up/down colours. Capacity and cost stress are rerun on demand (only when clicked, not on every backtest). The held-out view reads the held-out numbers, shown beside the pre-held-out numbers with differences.

### Results

One scrolling page with section navigation on the left; each section is as tall as its content:

summary (Nautilus native statistics, 14 more statistics and benchmark-relative statistics); monthly and yearly; rolling; drawdown list; long and short separately; P&L and exposure by instrument and industry; costs split into commission / spread / impact / borrow / funding; capacity (impact rerun at several capital levels); holding time and per-trade P&L distribution; crisis periods (only those the run covers; separate sets for stocks and crypto, including the 2024-08 yen carry unwind, the 2025 tariff shock and the 2025-10 crypto deleveraging); orders (including rejected, cancelled and expired with reasons, time in force, attached stops and targets); account (balance, margin used, leverage over time); the strategy's own statistics (`q.stat`, in the summary; `q.plot` lines are on the research chart); style exposure and attribution (single-instrument runs get only market beta); Instruments (also for single instruments); and all settings for this run (columns "layer / setting / that run / now", only changed items, "Use #N's settings").

Raw engine reports are cleaned up before display: readable event names and times, one instrument notation. The raw output stays in the run record.

### Library inputs in backtests

- Values reach the strategy at the time they actually became available, pinned to the selected version. When a new version appears, users are told; switching is their choice.
- Reading a model's published values (the ranking preset's Signal can select a model) works like reading a factor, and the start is not limited by the training cutoff. The part of the backtest before this version's held-out start is labelled "Used to pick <model name>": it was out of sample for training, but the model's settings were chosen while looking at it; only data after the held-out start is fully clean. Without a held-out period, the whole range is labelled.
- If a library signal stopped producing values more than a week before the run's end (later values only become known after that period, so the strategy keeps holding its last selection), the numbers do not show it, so Work and the walk-forward view state which item, which version, and the date its values end.
- Calling a frozen model directly (`q.model(...)` predictions) is only allowed after its training cutoff. A backtest starting earlier is refused, and the error suggests reading its published values instead.

### Other strategy rules

- **Contract-quoted perpetuals.** OKX and Gate perpetuals trade in contracts. When Market supplies the contract step and multiplier, the backtest works in base-currency quantities, with a minimum step of "contract step × multiplier" (one Gate BTC perpetual contract = 0.0001 BTC, so the step is 0.0001); strategies write quantities in the base currency as usual. If Market gives no contract step or the unit is unclear, the run fails with an explicit error rather than guessing. Bar volume keeps the source's unit.
- **Extra series** such as `q.series("funding_rate")`: literal names are read from the code before the run and frozen through Market; during the backtest they are cut at the simulated time, so they cannot read the future. Series Market does not have are reported once before the run.
- **Presets:**
  - Top N by signal: names with a value that period are ranked; a period is skipped only when fewer names have values than the number to hold. Rank buffer, per-name cap, equal / rank / signal / inverse-volatility weighting, turnover filter, industry cap, optional short of the bottom names.
  - Rebalance to target weights.
  - Single-instrument signal trading.
  - BTC breakout (channel breakout, ATR stop and trailing stop; runs out of the box).
  - Moving-average crossover (SPY daily, ten years).
- **`q.Strategy` helpers:** `equity()`, `position_size()`, `last_close()`, `open_orders()`, `order_target_weight()`, `rebalance_to()` (one call to reach target weights, selling before buying), `universe()`, `is_tradable()`.
- **Not supported:** tick, quote or order-book level backtests (needed for market making, high frequency and execution algorithms); latency models, queue position and liquidity consumption (meaningless with bars only); custom Actors, execution algorithms and several strategies in one engine (the engine supports them; not exposed yet); a visual strategy builder or an AI assistant inside Quant (people who do not write code use the Agent, which edits code through Developer's and Quant's public actions); option and futures analytics.

## Strategy validation

- **Sweeps.** Numeric and option parameters in code take a range or a list of values; run settings can also be swept: cost multiplier, fill timing, capital and date range (sweeping the date range reads 12 extra months of warm-up). Sweeps estimated to exceed ten minutes ask for confirmation. Each cell is an ordinary run with a light report (key numbers, equity, fills); clicking Run on one combination produces its full report. Results are a heat map (one neutral hue) plus a table; ranking metrics include maximum drawdown, number of trades and profit factor. Clicking a cell shows that combination; **Use these values** writes it back to the parameters. Only the best combination is marked; none is chosen for you. A stopped sweep can be resumed. Individual failed cells do not block the others, and the table shows why. If no cell produces a result, the sweep fails with the first failure reason.
- **Walk-forward** is a run kind built on sweeps. Each window picks the best combination over its selection months and then runs it over the following months (starting flat, with capital carried over from the previous window's end). The stitched out-of-sample equity is shown beside the "fixed parameters" line, with a per-window table. Total combinations are capped.
- **Held-out validation** works as described in [Shared rules](#robustness-and-the-held-out-period); the result shows in-sample and out-of-sample side by side. The held-out run's record carries the run it came from and its numbers (it is not just another ordinary run number); the model page's Results opens it with its own numbers.
- **Checks on Work** (each unrun check has a button that runs it):

  | Check | Pass |
  |---|---|
  | Held-out Sharpe | ≥ 50% of in-sample |
  | Walk-forward efficiency (mean out-of-sample Sharpe / mean selection Sharpe) | ≥ 0.50 |
  | Neighbouring parameters' mean Sharpe | ≥ 80% of this cell, and not on the grid edge |
  | Sharpe at double cost | ≥ 50% of the original |
  | 95th percentile drawdown over 1,000 trade-order shuffles | ≤ 2× the actual drawdown |
  | Best 5 trades (5 names for multi-instrument) | ≤ 50% of net profit |
  | Probability that deflated Sharpe > 0 | ≥ 95% |

  - A walk-forward window with no fills counts as Sharpe 0 and is not excluded, so a ten-window walk-forward where only one window trades fails.
  - The neighbour check reads sweeps launched from this run on the page (each sweep remembers the run it came from). When the current value is not in the middle of that sweep, "Sweep around these" is offered. Reruns by year, cost multiplier or capital do not count as neighbours.
  - Only sweep and walk-forward combinations count as trials for deflation. Rerunning the same strategy by year, cost multiplier or capital tries nothing new and does not count; otherwise each robustness check would deflate Sharpe again.
  - Checks use in-sample numbers only. When a rerun, sweep or walk-forward finishes, the checks and Results of the run on screen are reread.
- **Cost stress** (the cost multiplier and the double-cost check) scales rates: commission, spread, impact and borrow are multiplied; perpetual funding is neither scaled up nor down and follows Market's historical rates. Funding is a market-set transfer between longs and shorts, not trading friction; scaling it would invent a cost that does not exist, and strategies that collect funding would look more profitable. Scaling rates rather than totals keeps positions following equity. Cost and capacity reruns only run what is missing: the run itself and earlier sweeps of the same run at the same value count as existing. Capacity chart ticks are rounded to the digits the tick spacing needs.

## Asset allocation

Decide two things: whether to publish this method, and, at each monthly review, whether to follow this period's weights. The usual loop: change the method or constraints, see this period's weights change within a second, then run the full backtest when satisfied. Modelled on Portfolio Visualizer (fill in a form, get a report, compare portfolios side by side) and Bloomberg PORT (watch a managed portfolio: trial trades and risk shares).

- **Standard workflow.** A new allocation has no code; settings are generated from the standard workflow's declarations: asset pool (taken from the instruments in data settings; asset classes are set in the pool), objective, risk estimation, constraints, rebalancing, costs by asset class. One allocation has one objective; to compare objectives, run again or sweep.
  - **Objectives** follow skfolio: equal weight, inverse volatility, minimum variance, risk parity, risk budgeting (risk shares per asset class; risk parity is a special case), maximum diversification, hierarchical risk parity, HERC, nested clustering, maximum ratio (Sharpe and others), maximum utility, maximum return, benchmark tracking, Schur. Minimum risk can add "return at least X% a year". Objectives that use expected returns can choose historical, shrinkage or equilibrium estimates. All skfolio risk measures are listed; ten common covariance estimators are listed (the rest are available in code). Each row appears only when the objective accepts it.
  - **Constraints:** per-asset and per-class bounds, long-only or a long-short budget, maximum number of holdings (also per class), minimum weight if held, turnover cap, tracking-error cap, risk cap, free-form linear constraints, regularization, costs in the objective, robust optimization.
  - "Maximum number of holdings" and "minimum weight if held" exist only for mean-risk objectives (minimum risk, maximum ratio, maximum utility, maximum return, maximum diversification, benchmark tracking). They use the open-source solver SCIP (HiGHS for linear risk measures) with a node limit rather than a wall-clock limit, so results are the same under any load. skfolio does not raise when the solver hits its limit, so the solver status is read every period, and a non-optimal period is a warning.
- **Backtest method.** Each period fits on the look-back window before the rebalance date. Weights are held from the next trading day's close and drift daily; trades to the new weights are charged at per-class bps. Calendar rebalancing (weekly, monthly or quarterly). The turnover cap and costs-in-objective see holdings as of the rebalance date. Missing returns are not filled with 0 (that would treat pre-listing periods as flat and bias minimum variance towards them). Returns compound; run metrics are whole-period. Each period stores numbers only (weights, holdings, risk shares, turnover, costs, binding constraints, solver status, forecast volatility); charts are drawn once per run. Stopped or interrupted runs keep finished periods. The pool can span trading calendars (see below).
- **Work.** Most prominent is this period's weights table (target weight, published weight for this period, change needed, risk share; weights and risk shares as side-by-side bars, binding constraints marked in the cell). Beside it, a stacked chart of weights per period: weights that jump around every period are the most common allocation flaw, and a good equity curve can hide it; only this chart shows it. Clicking a period switches the table to it; with many assets, the stack is by class. Then key numbers (annualized return, volatility, Sharpe, maximum drawdown, difference versus 60/40 and equal weight, annual turnover), checks, this period's trade list and the run list. Changing a setting recomputes this period's weights within a second in the resident process (a trial, using the same function as the last period of the full backtest). Only the standard workflow supports second-level trials; for your own code, a trial is a full rerun.
- **Checks** (allocation's own set):
  - Sharpe after costs not below equal weight (listed first; equal weight is the hardest baseline to beat);
  - share of periods with a constraint at its bound ≤ 50%;
  - mean effective number of assets ≥ min(5, pool size ÷ 2) and largest weight ≤ 30% (or the per-asset cap if set lower);
  - zero periods where the solver failed or fell back;
  - changing the look-back length or covariance estimator by one step moves weights by ≤ 8 points per period on average;
  - no asset's history starts more than 1 year after the earliest one (any that do are named).
- **Results:** full-period equity against 60/40 and equal weight, drawdown, crisis periods, by year, per-period rebalances (weights, trades, binding constraints, solver status), Weights (target and actual holdings), Risk (risk shares by class or asset, forecast and realized volatility, efficient frontier with unsolved points counted, risk-measure table, VaR and CVaR), Style (exposure to the seven price styles, yearly contribution and specific part), attribution by class against 60/40, Robustness (sensitivity to look-back and risk estimator, costs at 0 / 1 / 2×, held-out), and all settings.
- **Sweeps and held-out** work as for the other kinds; the run settings include a cost multiplier that can be swept. A sweep shows as one row in the run list and expands to its combinations.
- **Cross-market pools.** Returns can be aligned weekly or monthly (last trading day of the week or month; an entire period without a close has no price change, and the reopening period includes the move since the last close. Published allocations follow the same rule), and look-back days are converted to rows accordingly. Assets in other currencies are converted to USD with the FX series Market freezes with the data; without an FX rate the run is refused, never assumed to be 1. An asset with short history can be given a proxy: the proxy's prices are scaled at the splice date to fill the period before listing. If daily returns of the two correlate below 0.8 over their common period, the proxy is refused and another must be chosen. The filled period is marked "·p" in the pool.
- **Capital** is a run setting; trade quantities in the rebalance list use it.
- **Scenario shocks.** Besides historical replay, you can shock an asset or class (`add_allocation_shock`, for example stocks −20%). Other assets move with it according to the latest period's risk estimate (conditional mean: Σ_og Σ_gg⁻¹ × shock). Shocks are stored in the allocation's settings; the Agent can add them too.
- **Drift line for review.** A "Since vN" row compares the published version's volatility at publication with the current target's, and how far each asset's risk share has moved. This also holds for weekly or monthly aligned pools, proxies and FX-converted assets.
- **Publishing** writes the full history of weights and per-period risk shares to the library through the same path as factors (code and settings of the standard workflow frozen, look-ahead check by rerunning at three cut points, one transaction). The publish sheet lists comparison with 60/40 and equal weight, changed settings, and held-out CAGR and Sharpe. After publication, the "at publication vs now" table also shows annual turnover (each new rebalance's weights against the previous drifted weights) and return against the benchmark (60/40, or the equal-weighted pool if there is none), plus post-publication Sharpe and number of rebalances. With "Produce every period automatically" on, the library extends it with the same fit at each new rebalance period, writing weights and risk shares together. Risk shares come from the same step that produces weights; the library does not recompute them from prices, because they depend on the method's risk estimate, and a different estimate would report a "risk drift" that is not there.
- **Monthly review.** Allocations in production get a review block for the current period: the published weights for this period against a trial. It compares the published version's period (a period starts on the day its weights start being held, not the latest daily row; the target is fitted on the close before that period's holding starts, not the run's last period; after the review, the version column still sits beside this period's method weights; if an asset fell back because it did not move during the look-back, it is named). Three options: accept as is; make a one-off adjustment for this period (with a reason, stored as a later set of weights for the same period, and the period is marked in the library); or change the method, which means going back to settings, backtesting and publishing a new version. Home shows a reminder when a review is due.
- **Weakening after publication** is judged by risk drift: realized volatility above 1.5× its level at publication, or any asset's risk share more than 10 points away from publication. PORT and Aladdin monitor risk against target; IC-style tracking is for signals.
- **Write as code** copies the standard file into the project. `q.allocate(settings, estimator=…)` accepts a skfolio optimization estimator or a Pipeline ending in one. Benchmark-tracking estimators use the tracking benchmark in allocation settings, which defaults to 60% SPY and 40% IEF. Estimators that need something the run cannot supply (such as factors) stop the run with the reason. Only a solver failure on one period's data falls back to equal weight.
- **Not supported:** Black-Litterman view inputs (views are fixed numbers written today; putting them in a full backtest tests the past with today's judgement; honest views would vary over time and come from signals. In code mode you can call skfolio's `BlackLitterman`, and likewise `EntropyPooling` and `OpinionPooling`); stacking optimization and distributionally robust CVaR; synthetic-data stress tests (scenarios are historical replay and asset shocks only); rebalancing advice against actual holdings.

## Library

The library is a catalogue plus post-publication performance, not an "auto-update every day" mechanism. A published version never changes. It records the run it came from, the code and data it used and who uses it, next to its performance since publication. Producing new values each period is a job attached to a version, and whether it is on time or failing is visible. Mature products keep these three apart: catalogue (MLflow model registry, BRAIN submitted alphas), production (feature-store scheduled computation, Numerai automated submission) and tracking (BRAIN out-of-sample scores, Numerai live scores). Multi-person roles (owners, approvals, stage promotion) are out of scope. The library is shared across the whole application. Updates need TalyxTerminal to be running, but not the workspace or panel the item was published from.

| Kind | Reads from the library | Writes to the library |
|---|---|---|
| Factor | Usually nothing (formulas can read `$lib.<name>`) | Factor values |
| Model | Library factors as inputs (unpublished research runs also work) | The model (predictions each period); factors it uses are published with it |
| Allocation | Usually nothing; works from prices | Target weights and risk shares |
| Strategy | Library factors, models or weights as signals | Nothing |
| Notebook | Anything, by name | One-off data |

- **Publishing** starts from a completed run (the publish sheet on the factor, model or allocation page). Versions are numbered automatically; the same name means a new version. Failed checks are listed but do not block. A new version is compared item by item with the previous one, and users of the previous version are listed (they stay on it). A factor's publish sheet also shows its most similar library item (rank correlation of 0.7 or more is orange). "Produce every period automatically" is optional (on by default, can be toggled any time; a version stored only up to its publication date has no post-publication performance, and its users get no new periods).
  Publishing is a platform task, also listed in run history, with step-by-step progress, and it can be cancelled. It takes the run's computed history, uses the Market calendar to compute each value's availability time, then truncates the data at three in-sample points and recomputes with the frozen code. Values before each cut must be identical; otherwise publishing is refused, naming the instrument and time whose value changed (it used future data). Cut points are in-sample only; the held-out period is never touched. Only if everything passes is it written in one transaction; a failure or cancellation writes nothing. Each version freezes the code snapshot, common code, lock, parameters, data settings (including list members at the time) and environment reference; later code changes or package installs only affect new runs.
- **Values and timing.** A long table (bar time, instrument, field, value). Each value carries its availability time (bar close for factors and predictions, bar open for allocation weights), the time it was actually computed, and its origin (backfill, on time, catch-up). On-time values are stamped on the bar boundary; late values are stamped at the time they were actually computed, so nothing reading the library could have used them earlier. Reading with `asOf` returns only values available by then.
- **Updates each period.** At each bar boundary of the version's calendar, new periods are computed with the frozen code in dependency order (factors, then the models and allocations that read them, then further downstream), each update queued in Quant's run queue. Each period ends as one of:
  - **On time**;
  - **Late** (TalyxTerminal was not running, or the source delivered late; waiting in the queue does not count as late);
  - **Missing** (the source has no bar for that period; asked again after 1, 2, 5 and 10 minutes, then every 10 minutes);
  - **Upstream missing** (an input has no value for that period; old values are never used to fill in);
  - **Failed** (a code error; retried after 10 minutes, or with "Retry now").

  After 5 consecutive missing periods, updates stop, history is kept, and the item shows as missing with the reason. A period's values, period log, post-publication metrics and summary are committed together, once per period; catching up several periods runs once. Publishing a new version does not stop the old one; it keeps producing values.
- **Post-publication performance** counts only settled periods (a prediction of the 5-day return is scored after 5 days); pending periods are listed separately. A model period is settled only when it has a label: when updating, labels for matured periods after the published version (same label definition as in training, at the time they became available) are written together with the new prediction, including matured periods filled during catch-up. Settlement is not inferred from the bars being present. Metrics show the number of periods and standard error; below 60 trading days they show "Collecting". The "At publication" column is the publishing run's number. After 60 trading days, an item is "Weaker" if its sign is unchanged but the metric is below a quarter of its publication value, or if the sign has flipped. Allocations use risk drift (see [Asset allocation](#asset-allocation)).
- **Production and stopping.** Versions are stopped only by a person. Versions used only by research objects are listed and can still be stopped. Research pinned to a stopped version keeps data up to that day and is told a newer version is available. Stopped versions with no users can be archived: the record, code and publication scores are kept; stored values, window models and the frozen environment are dropped, after one confirmation. "Users" include research objects pinned to a version; a published model holds the factor versions it uses.
- **Library page.** One table, problems first by default. The status column is the most prominent (On time, Late, Missing, Upstream missing, Failed, Weaker, Risk drift, Auto off, One-off, Stopped); other columns: kind, version in production, latest period, an on-time bar for the last 20 periods, main metric at publication versus since (with period count), users, and the automatic-update switch. The list reads a summary updated on publication and each update; it does not read every item in full when opened, and does not occupy the host's main loop. An item page has, at the top, status, reason and actions (Retry now, Stop, Resume, Archive, New from this, go to the source run). The main chart is cumulative post-publication performance (a line at the publication date, with the publication level as reference; allocations show realized volatility and risk shares against publication). Below: a per-period production strip (click a cell for that period); values by date and instrument, paged; the production log (the last 5 failures kept, with full reasons); versions and version comparison (code diff, settings diff, same-day cross-sectional rank correlation over the overlap, metric difference, coverage difference); and who uses which version.
- **New from this.** Build a model, strategy or allocation directly from a library item; data settings are copied from the item and the version is pinned, and you can go back to the source run. A strategy built this way is not an empty skeleton: it starts from Top N (from Rebalance for allocations), with this item as the signal and its version pinned by a parameter, the same as "Use this run" from a factor or model run. Switching versions moves the strategy's pin. Data items placed in the library from a notebook keep the row names as written in the notebook (for example `600642.XSHG`), and strategies read them by those names. When there are no values, the message says which names and periods the item does have values for, instead of "these instruments have no values".
- **Notifications** (the Quant section of the Notifications page, one switch each): long tasks finished, library failures, missing periods, weakening, recovery. The same failed period notifies once. Update failures fire the `library_update_failed` trigger for the publishing workspace and workspaces that use the item.
- **Other modules.** Library values are not shown on Chart yet; library items have no "Show in Chart". Strategy, factor and model runs reach Chart through research layers (see [Research results on price](#research-results-on-price)). The library records which version each research object uses.

## Home and all objects

- **Home.** Most prominent is **Needs attention**, holding only things to act on now: update failures, missing periods, allocations due for review, research pinned to a stopped version (all listed, archived items excluded, no time threshold). Each has an action, or a line of text if there is none. Weakening and late items only get a link to the library. Below: running and queued work (progress, stoppable; library updates can be cancelled), then recently touched objects with the key number of their representative run (factor Rank IC, model test IC, strategy annualized return or Sharpe, allocation Sharpe); publishing does not count as a run. New objects can be created empty or from a preset. Home reads stored summaries.
- **All objects.** Expanding the navigation shows a table of all objects by kind, including unpublished candidates. Each row shows the key numbers and checks of its representative run, is sortable, and can switch between this workspace and all. The representative run is the published run; otherwise the starred one; otherwise the latest successful run. It is not simply the latest run: after a batch, the latest may be a failed or the worst combination.

## Notebooks

Like JupyterLab: cells and output fill the page, with a collapsible side bar for the variable list and a library browser (clicking an item inserts a line that reads it). The top bar shows kernel status ("Python 3.12 · idle", followed by the device in the deep environment) and an environment drop-down; the page menu has package installation and **Open file in Developer**. In Developer, **Open in VS Code** opens the same folder; files saved externally are used on the next run. `input()`, `%debug` and `breakpoint()` work in cells, with an input box at the end of the output. See [docs/research.md](docs/research.md) for the `talyx_quant` API in notebooks.

- **Kernel environments:** standard (the research environment, Python 3.12) and deep (adds PyTorch). Switching asks for confirmation, clears variables and keeps outputs; if cells are running, the dialog says they will be stopped. If the deep environment is not installed, the dialog becomes an install confirmation stating the version (CUDA build with an NVIDIA GPU) and size; after installation the kernel restarts in it. A failed installation shows the reason and a retry, and the kernel stays in its original environment. Reading a neural network model with `q.model` in the standard environment says to switch to the deep environment.
- **`q.*` entry points:**
  - `q.run(run_id)` reads any run. Frozen data, settings, the tables behind reports, and model and portfolio objects are loaded lazily by reference, not copied. Failed or stopped runs return what exists. A run block ends with a line of key numbers. Run ids come from the run list or the start response.
  - `q.backtest(strategy, params, data=, signal=, every=)` runs a backtest in one line, with progress and stop. It starts a strategy run in the strategy environment, goes through the run queue and the pre-run memory check, and shows the reason in the cell if it fails or is blocked. It is recorded in the strategy's run list marked Notebook; it does not change the strategy and does not become its representative run. When finished, the cell shows a statistics block and **Open as a run of the strategy**; while running, a small equity-so-far chart. `data=` supplies your own table in place of the library input the strategy reads (dates as milliseconds or date strings); a table that does not match the library input is refused with a reason. Closing the panel stops the kernel, but the backtest finishes and becomes a strategy run; reopening the notebook shows "finished as run #n", or "was interrupted" after a host restart.
  - `q.data(...)`: a symbol list, `list=` a research list, or `index={"symbol":"^GSPC"}` an index, with an optional membership date.
  - `q.model(name, version)` reads a published library model. `q.qlib.init()` has a working directory in notebooks. `q.library.read(name, start=..., end=..., symbols=[...])` reads a slice by date and instrument; without a version it reads the latest production version. The cell shows item information; the return value is still a table.
- **Research bars.** `read_research_bars` returns a platform task id. The notebook keeps showing progress, and interrupting the cell cancels the task. When done, it returns two file references: the bars and a coverage note.
- **`q.tradable_quantity`** rounds to each instrument's own lot rules (stock board lots, perpetual contract steps); strategy presets use it for orders.
- **Autosave** after 4 seconds without typing, at most every 20 seconds, and before running a cell.
- **Interactive widgets:** ipywidgets sliders, drop-downs, buttons and `interact` work in output, as in JupyterLab.
- **Extract.** Tick cells and choose **Extract to new object** to create a new object from the selected cells and their data settings. The code is copied as written (with cell titles); tidying is up to you. Extracted models and allocations are always in code mode.
- **Open from a run or the library.** Every run page has a button to open it in a notebook (`open_run_in_notebook`): the top states the object, the run and the data end date, and the first cell is already loaded. Library items have "Open in a notebook" in their More menu.
- **Limits:** the kernel belongs to the workspace and notebook and stops when the panel closes; a single output larger than 2 MB is not saved; the Agent waits at most 120 seconds for a cell to run.

## Deep learning

Neural networks are a model algorithm and follow the same workflow as LightGBM: checks, batches, combining, held-out, publishing and updates. The "Neural networks" group lists the 18 networks of Qlib 0.9.8 plus "Your own network" (19 items), all selectable. TFT does not run on Windows or on CPU and is not listed.

- **Environment.** Networks train in a separate deep environment (the research environment plus PyTorch, with three locks, CPU, CUDA and XPU, chosen by the devices detected on this machine). When it is not installed, networks are still listed; selecting one shows an install block in the Algorithm group, and the pre-run check stops at the "Environment" line and points to installation. It is installed once and shared by all models. The Device drop-down lists only devices this machine has and the installed PyTorch can use: CPU, each NVIDIA GPU (model and memory), and integrated graphics for Transformer-type networks only. Every network can run on CPU.
- **Two training modes.** Twelve networks (LSTM, GRU, ALSTM, Transformer, Localformer, TCN, SFM, KRNN, Sandwich, MLP, TabNet, GATs) are trained epoch by epoch by TalyxTerminal, with per-epoch checkpoints, early stopping and resume from an epoch. IGMTF, ADD, ADARNN, TRA, TCTS and HIST use Qlib's own training for a whole window at once, with no per-epoch checkpoints; after a failure the window restarts, and finished windows are kept. Optimizer and "Early stop on" appear only for networks that declare them: ADARNN defaults to validation loss (IC also available); ADD, IGMTF and HIST default to validation IC; networks trained epoch by epoch by TalyxTerminal use validation loss. Other parameters go in Engine parameters.
- **Your own network.** Select it, then Write as code, and call `q.fit_network(NetClass, window, params, seed=, **net_kwargs)` with your network class. It trains with Adam and MSE in the same epoch loop (per-epoch progress, checkpoints, resume).
- **Start from.** GATs, IGMTF, ADD and HIST can start from a base model. The "Start from" drop-down in the Algorithm group lists finished LSTM or GRU runs of this object with the same inputs and look-back; the default is none (random initialization). If the base run's inputs, look-back, width or depth differ, the pre-run check stops at "Start from a run with the same inputs and look back" and says what differs.
- **HIST concepts.** HIST needs a concept matrix (names × concepts). The source is a library data item (names × concepts, non-zero means member, latest values), read through `read_object` (`concepts` under `part:"options"`); choose none when not used. Market has no concept data, so only data you import into the library works. With no source, or a table without these names or without concepts, the pre-run check stops.
- **TCTS auxiliary labels.** "Auxiliary labels" in the Label group take rows you can add and remove, each a formula; when empty, they default to the main label's form at +1 and +2 days. Formulas are checked for reading later bars, and the window gap and check gap use the furthest of the main and auxiliary labels.
- **Pre-run check per machine.** Memory, GPU memory (when a GPU is selected) and time are estimated; jobs that do not fit are stopped with a suggested fix (for example "GPU memory: about 14.1 GB; RTX 3060 has 11.2 GB free": reduce the batch size or switch to CPU). A batch size larger than a window's training rows is also stopped (the window would not train a single step). Default batch size is 800 on CPU; on a GPU, a suitable size is chosen from its memory.
- **Queueing.** Each GPU has its own memory budget (90% of total memory). Two jobs on the same GPU run together only if their estimates fit; otherwise one waits, and the table says which GPU it is waiting for. With several GPUs, jobs go to different GPUs; one network uses one GPU.
- **Out of memory.** The failure box names the window and epoch and offers resume options: from the last checkpointed epoch (epoch-by-epoch networks) or restart the window (Qlib-trained networks), resume with a smaller batch size, or, for GPU memory, resume on CPU.
- **Values.** All networks provide per-input contributions for a given day (integrated gradients), including the Qlib-trained IGMTF, ADD, ADARNN, TRA, TCTS and HIST. Permutation importance and per-epoch learning curves work as usual. The preparation area has narrow, wide and full-page layouts depending on panel width.

## Research code and data

**`talyx_quant`** (`import talyx_quant as q` in the research and strategy environments). Full reference: [docs/research.md](docs/research.md) and [docs/strategy.md](docs/strategy.md).

- Settings declarations: see [Two layers of settings](#two-layers-of-settings).
- `q.data()` returns the bars selected by the object's data settings (`.close`, `.open` and so on are time × instrument tables; `.annualization` is the number of bars per year on that calendar). `q.data(symbols, timeframe, start, end)` and `q.bars(...)` fetch other data through the code channel. `q.index_members(...)`.
- `q.report.factor` / `model` / `allocation` produce the standard report and write publishable values. In a notebook, `q.report.factor` uses the same method as the factor Work page: `settings` are the run settings, missing ones take the object page defaults, and it measures on `q.data()` or a passed `q.data(...)` result; with only a close-price table, settings that need volume must be turned off. Outside a run, these return a `ReportView` with `_repr_mimebundle_`.
- Models in code: `q.model_data`, `q.fit`, `q.record`, `q.predict`; `q.qlib` prepares Qlib data and rolling retraining. Allocations in code: `q.allocate`. Strategies: `q.Strategy`, `q.plot`, `q.stat`, `q.log`, `q.series`, `q.can_short`, `q.model`.
- `q.library.read(name)` reads a library item by name; `q.library.publish` publishes one-off data.
- matplotlib figures are kept only as raw output in a separate section of the report.

**Annualization** always follows Market's trading calendar: data preparation counts the bars of that calendar in the selected range and converts to a year (about 251–252 for US daily bars, about 8766 for crypto hourly bars). Strategy backtests likewise count days per year from the Market calendar (the longest when instruments have different calendars). Instruments of a factor, model or strategy must share one trading calendar (calendars with identical sessions, such as XNAS and XNYS, count as one); otherwise the run is refused, naming which instrument is on which calendar, and you can fetch them yourself with `q.data(...)` in code. Allocation pools can span calendars: each asset is fetched on its own calendar, returns use days when all assets have a close (or week / month ends), and observations per year are the common trading days (stocks plus crypto follow the stock calendar, about 252).

**Adjustment and stock backtests.** Research objects can use adjusted (default) or raw prices. Stock strategies freeze split- and dividend-adjusted prices exactly; other strategies use the source's raw basis. Stock backtests use simulated share counts and adjusted turnover. Reports keep the adjustment direction, anchor, source version and data limitations; the performance page states "Split and dividend adjusted prices", with a line below for the actual commission and the taxes in effect during the backtest, and free-data limitations (such as survivorship bias from missing delisted constituents). The list's members at run start are recorded in the run.

**History limits.** Data candidates list only Market's verified history routes for each timeframe. Settings and run preparation share Market's history boundaries; dates that are too early keep the same `earliestAt` and reason. Backtests use only finalized bars. Frozen raw tables keep the source's volume unit; Gate perpetual contracts are converted exactly to base-currency quantities before matching, while OKX quantities are already in base currency and are not converted again. When the data range starts before available history, the data settings show Market's reason and offer "Start on <date>" for the first complete day; a failed run's report shows the same sentence.

**Strategy data preparation.** Strategy bars are not refetched in full. For each name, Quant asks Market from ten bars before the last stored bar; if the source rewrote those ten bars, the whole name is refetched. Names already stored up to the run's end date are not requested, and data is then read from Market's own store without refreshing it again. Names with gaps in stored data are refreshed; if the source refuses, the run fails once with Market's reason. Unclosed bars (the instrument's session is still trading, by Market's calendar and close time) are not kept; the stored range ends before them, and that day is requested again next time, so the last bar is not mistaken for a source rewrite. One-minute matching bars are not copied into research storage; only their coverage is recorded (first, last and last ten bars per name), and the bars stay in Market. During the data stage, the run's first line shows the current step (Checking names, Fetching prices: n of 617, Exporting series: n of 1234, Reading trading terms, Checking coverage, Writing backtest inputs, reading library inputs, benchmark and risk-free rate, extra series; prices in batches of 50; names fetched and series exported are counted separately) and the names dropped so far.

**Dropped names.** When Market rejects an index member's price evidence (for example, dividends that do not match the adjusted prices), that name is dropped with Market's reason instead of failing the whole backtest. The run states how many names were set, how many traded, and which were dropped and why ("34 names set, META and TMO dropped for their dividend evidence"), so pages and reports never show an unexplained "34 set, 32 traded".

**Managed files** (imported files, library values, research archives) are located by id in Quant's own file store under its data directory, not by the absolute path recorded at import, so the data directory can be moved or copied. When a source prefix such as `yfinance:SPY` is missing, the error says how to write it.

**Research lists** are Quant's own records, shared across workspaces: create, rename, change members, delete (a list selected in some object's data settings cannot be deleted). Members are exact Market instruments. Importing Chart watchlists as research lists is not supported.

**Common code folder.** Under the user directory the platform gives Quant (`<user_dir>/module-files/quant/common`), shared across workspaces and added to the import path at run time. Each run keeps a copy as it was (`common` in the run directory), and publishing freezes another; later edits only affect new runs.

**Calling TalyxTerminal from running code.** When Quant starts a research, backtest or notebook kernel process, it requests a run token from the platform (with the run's context) and puts it in the process environment. `talyx_call` in the process uses it to call the host; calls are recorded with source code, Quant and the workspace. When the run ends, Quant reads back the run's calls (`platform.runs.calls`) and records them under the run history's data (`data.calls`). Running code can call any public action.

**Environment preparation.** Environments are built with the SDK's uv tool in a directory shared by all modules, named by lock, Python version and required imports; identical requirements share one environment. Environment keys follow how the lock is written in the repository, independent of where TalyxTerminal is checked out. Each user (run, kernel, library version) holds a record while using an environment and removes it when done; environments with holders are not cleaned up, and each library version holds the environment it was made with. A failed preparation is retried once by the next caller.

**Editor language support.** Quant contributes `source_language` to Developer, providing the research environment (plus Nautilus declarations for strategies) and the `talyx_quant` path for Quant object folders and the common code folder. Until the environment is ready, only the path is provided while it prepares in the background. When installing packages changes the effective lock (`packages.effective`), the editor environment switches without restarting the host.

## Historical index constituents

Research data settings can select an index that has membership history in Market with `index: {instrument, asOf?}` (currently only the S&P 500; Market does not have historical constituents for A-share or Hong Kong indices). This is mutually exclusive with instrument lists, research lists and selection in code.

- The run first has Market download the members and their prices, then reads membership as of `asOf` (default: the end date, UTC milliseconds). Market's membership end is exclusive; Quant converts it to an inclusive interval when freezing. Membership is known from its effective date; early announcements are not used.
- Membership switches at effective times; research fetches prices for the union of members over the period. Members that do not map to a Market instrument (each counted once) and members with an instrument but no prices are counted separately, and neither fails the run. Excluded members, periods and reasons are listed in the run's frozen data file (info.json next to the bars) and the report; the run record keeps only counts (names set, names traded). Per-name sources and data ranges are also in info.json, so the run record does not grow with the number of instruments (ten years of S&P 500 is about 5 MB, kept out of the record). Library versions likewise keep only counts and membership periods, with exclusion details next to the version's value folder.
- During a member's partial price gaps it is not tradable and is not ranked; it rejoins when prices resume. Prices are never invented or forward-filled. Reports note the survivorship bias. A symbol-list run reports the names left out for lack of prices and stops if none have prices.
- Membership intervals map to each bar's time; wide tables have empty prices for non-members, long tables keep `isMember`.
- `q.index_members(index, at, as_of=None)` reads constituents at a date or timezone-aware time and returns symbol, instrumentId, start, end and tradable. Notebooks read members at a given time through Market's `read_research`; research runs and strategy backtests use the run's frozen members. Periods with missing data or identity are kept in the table.
- Index stocks share one simulated cash balance per settlement currency. Members keep their own listing venue, trading calendar, taxes and trading conditions, and capital is not allocated twice across exchanges. At each new close, valid members and price gaps are checked before ranking and trading.
- When the source lacks the prices needed to verify the share basis around an ex-dividend date, the ex-dividend window is fetched first; members still without evidence are excluded from this run's tradable data, and the summary and report list the source's reason. Dividend amounts and share bases are never guessed.
- When restoring stock volume to actual shares, integers are restored only when the source provides the split factor and the difference from an integer is within one millionth of a share (SDK floating-point error); the original source value is kept. Larger fractions, volumes without a restoration basis and fractional positions are still rejected rather than rounded.
- In the data-settings overlay, Index lists indices with membership history in Market; selecting one defaults the timeframe to daily. The toolbar summary and run history read "<index name> members". The overview line of factor and backtest reports says how many members were excluded (hover for the list); details are in the price-limitations section of All outputs.

## Stock trading terms

Stock trading terms come from Market and are frozen with the run: minimum quantity, step, odd-lot selling, sellable trading days, price limits, and the mapping from raw to adjusted prices. Stock commissions and taxes are kept separately rather than as venue maker / taker fees; the commission assumption can be overridden, and statutory one-sided tax rates apply by trading day. If verifiable material is missing, preparation fails; it never falls back to raw prices or zero fees. Stock daily bar times follow Market's actual close and trading day, not "open plus 24 hours".

Stock backtests use Nautilus instrument trading terms: normal orders and amendments are checked for minimum quantity and step, and candidate fills are checked for limit-up buys, limit-down sells and T+1 frozen quantities. Minimum commission accumulates per original order; partial fills, one-sided taxes and funds checks use the same ledger. Stock backtests account in split- and dividend-adjusted prices with simulated share counts, so dividends and share additions are not applied a second time. Reports store the actual commission parameters and material versions.

## Models in strategies

A model artifact fixes its training cutoff (UTC, exclusive upper bound), file digests and runtime environment. The cutoff covers the data actually used to fit preprocessing, select the model and build training labels; the publication time is not used as a substitute. A backtest that calls a frozen model directly cannot start before the training cutoff (reading its published values is not limited; see [Library inputs in backtests](#library-inputs-in-backtests)), and warm-up cannot call a model before it was available. Long-term performance of rolling retraining reads the per-period predictions; historical backtests do not switch model versions by time.

Strategies declare the dependency with a literal `q.model("artifact name")` and call `predict(payload)`. Each run pins the artifact version and reuses the loaded session; runs do not share mutable model state. Custom models declare the same frozen material with `q.publish_model(name, artifacts, loader, predictor, training_cutoff)`.

For one model version, per-period predictions are obtained by reading values, and the frozen model itself is resolved through `q.model`. The model session process is called through a line-based pipe, with frozen dependencies and identity checks.

Training and update predictions take features from the factor values known at the time. Late-published values join predictions at closes after they became known; they are not backfilled as if known earlier, and not discarded permanently because they arrived after the original bar's close.

## Packages

Your package list is `requirements.txt` in the common code folder. The Packages page (from the "…" menu on notebook, research and strategy pages, and also offered when `%pip` is blocked) edits that file through Developer documents when you add or remove packages, so the file history and VS Code see the same file. Opened from a notebook, it opens in another Quant panel so the notebook and its kernel stay alive (switching pages keeps the kernel running, and closing the panel stops it).

- Installation resolves with uv separately against the research lock (Python 3.12) and the strategy lock (3.13); existing versions cannot change. If resolution fails, the message names the environment and the package (for example `pandas==2.2.0` conflicts with `pandas==2.3.3` in the lock); failure on the strategy side means the package cannot be used in strategies.
- The resolved lock goes to the SDK's uv tool to produce a new environment; installation is a background task. If one environment fails, it keeps its previous lock.
- After installation, new kernels, runs and backtests use the new lock; running kernels are told to restart. Published and running work refers to frozen environments and is unaffected. Each run records the lock file and digest it used.

## Environments and native computation

`native/` provides the native computation used by runs: factors (Alphalens, the vectorized Work numbers, the formula engine), models (Qlib data and processors, tree and linear models), allocation (skfolio, SCIP), styles, metrics, strategy backtests (Nautilus) and report publishing. Metrics (returns, volatility, Sharpe, drawdown, IC, t; annualized by the instrument's trading calendar) are defined in one place, `native/metrics.py`. They are computed once in the native process when a run finishes and stored in the run record; pages, comparisons, the Agent and notebooks read the stored numbers, and neither the host nor the frontend computes them. Native computation runs in subprocesses in the research or strategy environment, never in the host. Results are published as a result index plus tables, figures and files; figures are Plotly. Tables are read in pages and keep nanosecond timestamps, large numbers, decimals and missing values exactly. Reports and research features are built without modifying the engines.

Environments, locks, patches and wheels are described in [runtime/README.md](runtime/README.md):

- a research environment (Python 3.12, with Qlib, Alphalens, skfolio, SCIP, LightGBM, XGBoost, CatBoost, QuantLib, ORE, statsmodels, arch, linearmodels and ipykernel);
- a strategy environment (Python 3.13, Nautilus);
- a deep learning environment (see [Deep learning](#deep-learning)).

Environments are prepared from the locks with the SDK's uv tool and reused (see Environment preparation under [Research code and data](#research-code-and-data)); the repository's own virtual environment is not modified.

Native smoke tests are not part of the everyday suite. With `TALYX_NATIVE_SMOKE=1`, `pytest tests/native -s` installs the fixed environments from the bundled locks (reused while the locks are unchanged), runs a factor, model, portfolio and strategy once each, and prints a numeric summary of each. See [docs/development.md](../../docs/development.md).

Strategy editor help uses the actual execution environment. Where the type stubs shipped with Nautilus's PyO3 bindings differ from runtime signatures, editor-only material is generated from the installed stubs and runtime signatures; the original wheel, the execution search path and type diagnostics are left unchanged.

### Pricing, futures and hedging research

Quant has no dedicated pricing, futures or hedging pages. QuantLib and ORE are in the research environment; use them directly in notebooks. Lessons worth keeping in mind:

- An option's time to expiry must be exact to the moment, not the day, and the contract multiplier must be carried through to the Greeks. QuantLib's built-in SVI fit accepts only real-valued time; a fit on exact timestamps needs your own C binding.
- ORE can miss calculations even on a normal exit. Judge whether a calculation finished by checking that the expected trades and reports are complete, not by the exit code.
- Continuous futures series are for research only; P&L must be computed on the actual contract months.

## Interfaces

Quant exposes the panel `quant.workspace` and the result view `quant.result`. Public actions, events, settings and trigger streams are declared with decorators and pydantic models in [backend/entry.py](backend/entry.py); [public/contract.json](public/contract.json) is generated from them. Action names and parameter examples are in the [Quant GUIDE](GUIDE.md). For how modules connect through public contracts, see [docs/architecture.md](../../docs/architecture.md); for platform-wide limits, [docs/limits.md](../../docs/limits.md).

- **Process and workspaces.** Quant runs in its own process. Everything it does carries a workspace (the caller of an action, or a header on private routes). Background runs are registered under that workspace's activity and stop when the workspace closes. When the Quant process restarts, the subprocesses it started (training workers, notebook kernels) end with it, and notebooks show that the kernel restarted.
- **Panel and backend.** The panel talks to its own backend through private routes, mounted unchanged behind the host and validated with pydantic, and through the notebook kernel WebSocket. Deleting objects, and creating, renaming and deleting folders, are the exceptions: the panel calls the public actions directly, the same path the Agent uses.
- **Open targets.** Other modules open Quant items through the platform's open targets: `objectId` `research` (home), `object:<id>` (or the bare id) and `run:<id>`.
- **Public actions.** Scripts, the interface, other modules and the Agent use the same action contract. Lists default to the current workspace; `scope:"all"` shows everything. Objects in other workspaces can be read, changed or run by id; background activity belongs to the workspace that started the run. `create_object` creates an object with its data, parameters and run settings; `update_object` merges changes. `start_run` returns a run id, and the activity ends with the `read_run` summary. `read_run_table` reads fills, orders and daily data in pages, or as a whole table with `format:"file"`.
- **Editing and concurrency.** Public edits of a strategy object check new declarations against kept parameters. When the same version is edited concurrently, the conflicting request keeps the winner's source and version. Allocation reviews write by the frozen period's asset ids; a cross-section read by date has one row per asset, and `asOf` reads values from before the review.
- **Shared services.** Public actions and the panel share the run, library, model-queue and notebook services. Allocation runs can read their frozen source in pages; library versions can read reports, source, files and tables. Strategies can pin library versions with `libraryInputs` before their first run; with only a name, the version is pinned on first use. Library users follow the bindings saved on the object, and the run record keeps the versions actually read. The `run_cell` action saves the cell's output and execution count before it replies. Notebooks can be saved whole by document version, extraction can be previewed from selected cells, and the kernel and variable table can be controlled through actions.
- **Events:** `object_changed`, `object_run_changed`, `research_completed`, `library_changed`, `library_update_failed`, `packages_changed`, `research_lists_changed`.
- **Trigger streams:** `research_facts` and `library_facts`, read with a cursor.
- **Settings.** Notification switches are Quant settings (Settings → Quant → General) and can be set per workspace.
- **Dependencies.** Quant requires [Market](../market/README.md). [Chart](../chart/README.md) and [Developer](../developer/README.md) are optional.

Quant has no separate editor, experiment database, financial object store, training engine, or research service that keeps running after the application exits.
