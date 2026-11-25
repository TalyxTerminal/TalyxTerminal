# TALYX user guide

[简体中文](user-guide.zh-CN.md)

TALYX is a desktop application for market data, charts, quantitative research and AI-assisted work. It runs on your computer: data, code, runs and conversations are stored locally, and data is fetched directly from the sources you choose. It is built from modules (Market, Chart, Quant, Agent, Developer) that you arrange in workspaces, and you can add modules written by others or by yourself.

Interface labels are in **bold**; menus are written **Menu › Item**.

## Contents

1. [Install and start](#1-install-and-start)
2. [Workspaces, panels and settings](#2-workspaces-panels-and-settings)
3. [Market: data, sources and instruments](#3-market-data-sources-and-instruments)
4. [Chart](#4-chart)
5. [Quant](#5-quant)
6. [Agent](#6-agent)
7. [Developer: writing your own modules](#7-developer-writing-your-own-modules)
8. [Data and privacy](#8-data-and-privacy)
9. [Getting help](#9-getting-help)

## 1. Install and start

**Requirements.** 64-bit Windows and an internet connection. Plan disk space for Quant's research environment (about 1.1 GB), the optional deep-learning environment (1.5–4 GB) and the market data you download.

**Install.** Download `TALYX-Setup-x64.exe` from the Releases page of `github.com/TalyxTerminal/TalyxTerminal` and run it. It installs for your Windows user only, without administrator rights, and adds Microsoft Edge WebView2 if it is missing (this needs an internet connection). Open TALYX from the Start menu or the desktop shortcut.

**First start.** TALYX opens on **Home**: a world clock, your most recent workspace (or **New workspace**) and a **Community** card. Click **New workspace** to start with an empty layout, then add panels.

**Quant's first run** downloads and prepares its Python environments: about 1.1 GB, roughly six minutes. Later runs reuse them.

**The tray.** Closing the window sends TALYX to the system tray; work in open workspaces keeps running. Quit with **Settings › Exit TALYX** or from the tray menu. While TALYX is in the tray, reopen it from the tray icon: starting it again from the Start menu does not bring the window back in this version.

**Updates.** With **Settings › General › Tell me when a new version is out** on (the default), TALYX asks GitHub for the latest release at start and once a day; nothing about you is sent. A newer version shows a mark in the top bar. **Update** downloads the installer, verifies its SHA-256 checksum, closes TALYX, installs and reopens it, keeping your workspaces, data and settings. The first start of a new version shows its release notes once.

**Uninstall** from **Installed apps** in Windows Settings. Your data stays (see [Data and privacy](#8-data-and-privacy)).

## 2. Workspaces, panels and settings

### Workspaces

A workspace is a layout of panels and everything in it: charts, watchlists, alerts, Quant objects, conversations. Open workspaces are tabs in the top bar; **+** opens a new or saved one. Click the current workspace's tab for its menu:

- **Add module…** adds a panel.
- **Save** (Ctrl+S). TALYX asks before closing a workspace with unsaved changes; if you close without saving, charts return to their saved state and charts never saved are deleted.
- **Rename…**, **Folder…** (each workspace has a folder for its files; you can point it to an existing folder), **Open in new window**, **Close workspace**.
- **Copy AI connection address** and **Stop AI work** concern AI apps outside TALYX ([Agent](#6-agent)).

Closing a workspace stops its background work (downloads, runs, notebook kernels, alerts, bots); the confirmation lists what will stop. Market downloads resume when you reopen it and model training continues from its finished windows; other runs are not restarted.

**Home › Workspace** lists saved workspaces with **Open**, **Rename**, **Duplicate**, **Show folder** and **Delete**. A duplicate gets its own copies of charts, watchlists and conversations; alerts and screens stay with the original.

### Panels

Each module opens as a panel. Drag a panel's tab to dock it beside another, split the area or join a tab group. The tab menu has **Close**, **Close others**, **Maximize** and **Restore**; **Add module here** adds a panel to that group (a workspace with a single panel has no tab row, so use **Add module…**). Ctrl+Tab steps through a group's panels; Ctrl+K opens the command palette.

### Settings

The gear in the top bar opens **Settings**. **Scope** switches between **Global** and **This workspace**: a workspace follows the global value until you change it there, and **Reset to global** undoes that. Settings tied to the machine or shared data (language, start-up, data sources and keys, notification channels) are global only.

- **General**: **Language** (English or 简体中文; the window reloads and keeps unsaved work), **Up color** (**Green up** or **Red up**, for prices, P&L, buy and sell everywhere), **Start TALYX when you sign in**, **Keep the computer awake while work is running**, version and updates.
- **Notifications**: **Phone channels** for Telegram, Feishu or Discord, with **Send test**. Chart alerts and Agent can send to them.
- **External AI**: the calls made by AI apps outside TALYX.
- Below these, each module's own pages, such as **Market data › Sources** or **Chart › General**.

When a module fails to start, an orange dot appears on the TALYX mark and the gear, and **Needs attention** in Settings gives the reason and **Retry**.

### Community

**Community** has **Discover** and **Installed**. **Installed** lists each module's panels, AI actions and use, with **Enable**, **Disable…**, **Reload**, **Uninstall…** and **Install from file…** (a folder or zip). A module others depend on cannot be disabled; a disabled module's panels stay in place and say so. Online sharing of modules is not available yet.

## 3. Market: data, sources and instruments

Market owns all market data; Chart, Quant and Agent read from it. Data is stored once on your computer and read from there, and the network is used only for what is missing, so stored data can be viewed offline. The Market panel has two pages, **Library** and **Sources**.

### Sources and keys

**Sources** lists each source with its status and a **Use** switch. Click one to set its key and settings, **Test** it, see what it provides (**Gives**) and what Market uses it for, or delete its local data (this happens at once, without confirmation). **Test all** tests every source in about ten seconds. **Settings › Market data › Sources** edits the same global settings. A saved key is never shown again.

Free sources:

- **Yahoo Finance**: US and Hong Kong stocks, ETFs, indices, FX, commodities, rates; corporate actions, current industry, share counts.
- **Baostock**: A-share daily bars including delisted stocks, corporate actions, industry by date, share counts, and CSI 300, CSI 500 and SSE 50 members over time.
- **Crypto exchanges**: Binance, Bitfinex, Bitget, Bybit, Coinbase, Crypto.com, Deribit, Gate, HTX, Hyperliquid, Kraken, KuCoin, MEXC, OKX; bars, funding, open interest and more where offered.
- Listings and research data: Nasdaq Trader, the HKEX list of securities (with board lots), S&P 500 member history, SEC EDGAR financials (needs a contact e-mail, sent to the SEC with each request) and CoinGecko.

Sources that need your key:

- **Alpaca** (free account): US intraday bars back to 2016, and code changes so a renamed company stays one instrument.
- **Sharadar (Nasdaq Data Link)**, paid: US daily prices and delisted US stocks, so past S&P 500 members that were delisted or acquired have prices.
- **Tushare Pro**, paid: A-share daily bars, monthly index weights, share counts.
- **Databento**, paid per use: US equity bars from the chosen dataset (**XNAS.ITCH**, **DBEQ.BASIC**, **EQUS.MINI**, **EQUS.SUMMARY**).

For these four, set **Requests per minute** to your plan's limit; Market paces its requests accordingly. **Interactive Brokers (IB Gateway)** and **Futu (OpenD)** can serve history through their local apps.

For each market and kind of data, Market ranks the sources (mostly by history depth) and takes a series from one source only. If a paid source stops working, it falls back to the next.

### Library

**Library** groups stored data by market and kind of data, with counts, date range, gaps, source and size. Running downloads are at the top with **Cancel**; interrupted ones have **Resume**. Open a group to see its series; select a series for its coverage timeline, fillable gaps, corporate actions, **Codes over time**, the latest rows and **Open in Chart**.

- **Get data**: pick an index's members (**S&P 500 members**, **CSI 300 members**, **CSI 500 members**, **SSE 50 members**) or search instruments; tick kinds of data (**Daily**, **1 hour**, **1 minute**, **Corporate actions**, **Funding**, **Open interest**); choose a **Period**. You see the estimated size against free disk space before starting. Only missing data is fetched; a failed instrument is reported with the source's message while the rest continue; when a source rate-limits, the job says which one and waits.
- **Update** / **Update all** brings series up to the latest bar (a **Behind** mark disappears).
- **Import** reads a folder of CSV or Parquet files with a time column and open, high, low, close (volume optional). The code comes from a code column or the file name (`AAPL_1d_2024.csv` → AAPL), the interval from the name or the data. **Read** shows each file as matched to an instrument or **Not matched**; unreadable rows are refused one by one with the reason. Imported data is a separate dataset beside the source's data, never replacing it.
- **Export** writes the selected series as Parquet or CSV, with **Raw**, **Splits** or **Splits + dividends** for stocks.
- **Delete** removes stored rows but keeps the instruments; downloaded data is fetched again when needed.

### Instruments and symbols

| You type | Meaning |
|---|---|
| `AAPL`, `600519.SH`, `0700.HK` | Local listing code |
| `US:DASH` | A code in a named market |
| `USDCNY=X` | FX rate |
| `binance:BTC/USDT`, `okx:BTC/USDT:USDT` | Spot or perpetual on an exchange |
| `BTCUSDT.P`, `OKX:BTCUSDT.P` | Chart-style crypto codes |

Search matches current and former codes, prefixes and names (Chinese names too). An instrument keeps its identity through renames and code changes; a code reused by another company is kept apart by date.

Market stores unadjusted prices plus corporate actions and adjusts when reading, so splits and dividends never rewrite stored data or exported files. Charts show US and Hong Kong stocks split-adjusted and A shares forward-adjusted with dividends, as common charting software does.

### Limits of free data

- **Terms of use.** Data belongs to its sources. For example, Binance's historical files are CC BY-NC-SA 4.0 (non-commercial only), and OKX data is for your own non-commercial trading. TALYX fetches on your computer and redistributes nothing.
- **History depth.** Without Alpaca, US hourly bars reach back about two years and one-minute bars about 30 days; Hong Kong hourly bars about 60 days; some exchanges keep only days of one-minute bars. What no source has is empty in charts and backtests.
- **The first fetch takes time** because of page sizes and rate limits; after that data is read locally.
- **Real time.** Instruments beyond a free real-time quota are marked delayed; stock intraday data has the source's publication delay.
- **Not available**: order-book history, liquidations, option implied volatility and Greeks history.
- **Regions.** Some exchanges refuse some regions (HTTP 403 or 451); Market shows the refusal and does not switch sources. Requests follow your Windows proxy settings.

## 4. Chart

Chart follows TradingView. A Chart panel is one layout of 1 to 8 charts, and a workspace holds at most 8 charts. A new panel opens OKX BTC/USDT spot, daily.

- **Top toolbar**: symbol search, **Compare or add symbol**, timeframe, chart type, **Indicators**, **Indicator templates**, **Alert**, **Replay**, **Undo** (Ctrl+Z), **Redo** (Ctrl+Y), **Layout** (number of charts and **Sync in layout** for symbol, interval, crosshair, time and date range), **Settings**, **Snapshot and export**.
- **Bottom bar**: date-range presets, **Go to date**, **Time zone**, **ADJ** (dividend adjustment), log and auto scale, and the **Screener** and **Editor** tabs.
- **Right rail**: **Watchlist**, **Alerts**, **Object tree and data window**. **Drawing tools** are on the left; drawings are anchored to time and price.

Chart types include candles, bars, lines, areas, baseline, Heikin Ashi, and Renko, Kagi, Point & figure, Line break and Range. A chart loads the latest 10,000 bars and up to 30,000 as you scroll back. The legend shows the feed state (live, delayed, history only, market closed, recovering, or failed with **Retry**).

**Indicators.** **Built-in** has 43; there are also **Favorites** and **My indicators**. Volume is an indicator on every new chart. A failing indicator shows the reason in its own legend; the rest keep working. Indicators can read other symbols and timeframes (`request.security`) and Market's derived series by suffix: `_OI`, `_FR` (funding rate), `_MARK`, `_INDEX`.

**Your own indicators.** In the **Editor** tab, **New indicator…**, write it, **Add to chart**. Indicators are Python scripts run by PyneCore: Pine semantics, but not Pine, so TradingView Pine scripts cannot be pasted in. Developer's help has "Writing Chart indicators".

**Watchlists**: several per workspace, with sections, up to 200 instruments each.

**Alerts.** **Alert**, or the **+** by the price scale (**Add alert at this price**). The target is one series or a whole watchlist. Add 1–5 conditions comparing price, indicator outputs, drawings, constants or a script's `alertcondition` (crossing, greater or less than, entering or leaving a channel, moving by an amount or percent within N bars), how often it fires (**Only once**, **Once per bar**, **Once per bar close**, **Once per minute**), an expiration and a message, and **Show popup**, **Play sound** or **Send to phone**. Defaults are in **Settings › Chart › General**.

Alerts take effect immediately, independent of saving. They are watched only while their workspace is open (also in the tray) and only on live bars; time when TALYX was closed is not checked afterwards. An alert keeps the indicator code from when it was created (**Uses an older version** after you change it). History is kept for 90 days.

**Screener**: **New screen** checks symbols or a watchlist against up to 5 conditions on each member's latest closed bar.

**Templates** (indicators, chart settings, drawing styles) are shared by all workspaces. **Replay** steps forward from a bar you pick while live data and alerts continue. **Snapshot and export** copies or downloads an image, or exports the closed bars in view with indicator outputs as CSV, JSON or Parquet.

Chart changes are saved with the workspace; each chart keeps 100 undo steps in memory, including the Agent's changes. Quant runs appear on charts as research layers; a layer is a snapshot, so after a rerun open it in Chart again.

## 5. Quant

Quant covers factors, models, asset allocation, strategies and backtests, notebooks, and a library of published results. It is research only: TALYX does not place real orders in this version.

### How Quant is organised

**Home** shows **To handle** (what needs action now), **Running and queued**, **New** (blank or ready-made objects of each kind) and **Recently changed**. **All objects** lists every object with its representative run (published, else starred, else latest successful); **Library** opens the library. Objects belong to the workspace they were made in; lists switch between this workspace and all.

Factors, models, allocations and strategies share one layout:

- **Code**: a Developer project in a code area you can show, hide or resize; **Open in Developer** opens the full editor.
- **Data settings** (**Set data**): the **Universe** (**Symbols**, a **Research list**, an **Index** with its members over time, or a **Rule in the code**), adjusted or raw prices, interval and range. The end defaults to **Latest close**, the last bar the source has published when the run starts.
- **Code settings** come from the code (`q.param`, `q.choice`, `q.pick`, `q.table`); **Run settings** hold capital, costs, fills, benchmark, held-out period and the like. Settings changed since the last run are marked.
- **Work** has what you need to decide: settings, the run, key numbers, checks and the difference from a compared run. **Results** is the deep analysis of the selected run. Ctrl+Enter runs.

**Runs.** A run saves your code, then freezes code, parameters, data and environment. While it runs you see the step, elapsed time and finished part, and can stop it. A failure points to the line in your code, with traceback and local variables. In **Runs** you can **Star**, add notes, **Compare** two runs (code and settings differences) and **Use this version** to restore a run's code and settings. A run cut short by a restart is marked interrupted and not rerun.

**Queue.** One queue for everything: interactive work first, batches and library updates by available CPU and memory, sized to your machine. A run estimated over the memory budget waits and runs alone; a run that truly runs out fails, stating what it used and what the machine has. Sweeps and batches have no size cap; one estimated at over ten minutes asks first.

**Checks** use the same thresholds everywhere (for example overlap-adjusted t ≥ 3, rank correlation with library items below 0.7, break-even cost at least twice the assumed cost). They flag; they do not block.

### Factors

Write a formula with Qlib's operators plus `CSRank`, `CSZScore`, `CSDemean`, `IndRank` and `Neutralize`, on fields such as `$close`, and library values as `$lib.name`. Errors are marked in the formula; references to future data are refused. **To code** turns it into Python.

A run takes 1–3 seconds once warm. **Work** shows Rank IC, overlap-adjusted t, long-short return after costs, turnover, coverage and correlation with the closest library item; group returns; IC decay over 1–20 holding days (click a day to use it); checks; and one day's cross-section with each name's inputs, value, rank and later return. **Results** has **Returns**, **IC**, **Industry**, **Style**, **Turnover**, **Costs**, **Event** and **Settings**, plus **On price** and **Values**. Run settings cover universe filters, holding days, trade delay, grouping, outliers, neutralisation (industry and log size by default), cost (5 bps by default) and the held-out period. **Use this run** builds a model or strategy from a run without publishing, or opens it in a notebook; **Publish** puts it in the library.

### Models

Ready-made: **LightGBM on Alpha158**, **LSTM on Alpha360**, **Ridge on the price styles**. The standard pipeline groups data, inputs (library factors, Alpha158, seven price styles, your own), processing, the label (a formula), the split (train, validation and test windows, expanding or rolling, retraining) and the algorithm (LightGBM, XGBoost, CatBoost, linear, DoubleEnsemble and others; 18 neural networks).

**Check only** checks in seconds: samples per window, coverage, correlated inputs, look-ahead and memory, on a timeline of the windows. Give any setting several values to train a batch. The experiment table lists every training, by test Rank IC by default; select rows and **Combine** to average them. Training can take hours and needs TALYX open; after a restart it continues from the finished windows. Neural networks need the deep-learning environment (PyTorch for CPU, NVIDIA CUDA or Intel XPU), offered for install when you pick one.

### Allocations

The standard pipeline takes an asset pool, an objective (equal weight, inverse volatility, minimum variance, risk parity, risk budgeting, hierarchical risk parity, maximum ratio, benchmark tracking and more), risk estimates, constraints, a rebalancing calendar and costs by asset class. **Work** shows this period's weights and risk shares against the published ones, the weights of every period stacked over time, key numbers against 60/40 and equal weight, and this period's trades. A changed setting recomputes this period in about a second; **Run backtest** runs the whole history. Pools can mix markets: returns align weekly or monthly, other currencies convert to US dollars, and a young asset can borrow a proxy's earlier prices. A published allocation's new period can be reviewed: **Accept as computed**, **Apply as this period's one-off adjustment**, or change the method and publish again.

### Strategies and backtests

A strategy is Python using `q.Strategy` on the NautilusTrader engine. Ready-made: **Top N by signal**, **Rebalance to weights**, **One name on a signal**, **BTC breakout**, **Moving-average crossover**.

Run settings: capital, exchange conditions (leverage, margin, liquidation, stop triggers, position mode, where they apply), costs, **Fill at** (**Next bar open** by default, or **Next bar close**), shorting, benchmark, risk-free rate, finer bars for fills, limit-order fills and halted names. The default benchmark is SPY for US, CSI 300 for A shares, 02800.HK for Hong Kong, and buy-and-hold of the traded instrument for crypto; any symbol works. The risk-free rate defaults to the 13-week T-bill for US and Hong Kong, 4% a year for A shares and zero for crypto.

Costs are fees, half the spread, market impact, borrow fees and perpetual funding. Fees come from Market per symbol; where an exchange lists none, its standard tier is used and marked assumed. Orders from your code round down to whole lots: 100 shares for A shares (a final odd lot is sold with the last whole lot), each stock's board lot for Hong Kong.

**Work** shows annual return, maximum drawdown, Sharpe, excess return, yearly cost and trades; a chart of equity and price with trades (with several names, pick one from the contributors); rebalances, holdings, trades and the log. **Results** covers statistics, monthly and yearly returns, drawdowns, long and short sides, P&L by name and industry, costs, capacity, crisis periods, orders, account and style.

**Sweep** runs a grid over parameters or run settings, shown as a heatmap; **Use these values** writes a cell back. TALYX marks the best cell but leaves the choice to you. **Walk-forward** picks the best combination per window and runs it on the following months. Checks include held-out Sharpe, walk-forward efficiency, neighbouring parameters, doubled costs, shuffled trades, the best five trades' share and a deflated Sharpe that accounts for the combinations tried.

### Held-out data

In **Run settings**, set where the held-out period starts. All normal runs then stop before it. **Validate on held-out…** is a separate run: the confirmation says how often this code and period were validated before, and the result appears beside the in-sample numbers. Once you have looked at the held-out period and changed things, it is no longer out of sample.

### Notebooks

Notebooks work like JupyterLab, with a side panel for variables and the library, `input()`, `%debug` and ipywidgets, on the research environment (Python 3.12) or the deep-learning one. Main entry points: `q.data(...)` (bars for symbols, a research list or an index; split- and dividend-adjusted by default), `q.run(run_id)`, `q.backtest(...)` (recorded as a run of that strategy), `q.library.read(name)`, `q.model(name)`. Notebooks save a few seconds after you stop typing. **Extract to new object** turns selected cells into an object, and every run can be opened in a notebook. The kernel keeps running when you switch pages in the panel; closing the panel or workspace stops it, asking first if cells were running. Outputs over 2 MB are not saved.

**Packages** (in the **…** menu) installs Python packages into the research (3.12) and strategy (3.13) environments without changing the versions TALYX ships, naming any conflict. Restart kernels to use them. Code shared by all objects goes in the common code folder (**Open common code**).

### The library

The library holds published factors, models and allocations, and one-off data from notebooks. Publishing starts from a finished run; versions are numbered, failed checks listed, and the values are recomputed at three earlier cut-off points: if any changes, the code used future data and publishing is refused, naming the instrument and date. With **Auto-update**, a version produces a value every period while TALYX runs. The library lists each item's state (such as on time, late, missing, failed, stopped), its last 20 periods, performance at and after publication, and who uses which version. **New from this** builds an object pinned to that version. Strategies read values as of when each was known; a new version never replaces the old one for its users without your choice.

### How Quant treats data

- Prices are split- and dividend-adjusted by default, anchored to the latest day, so early prices are not the prices traded then. For absolute price levels ("price below 5") use **Raw**.
- A stock whose prices stop (delisted, or no later data) exits at its last close; the run's data notes name it.
- In factor research a halted stock is left out of that day's cross-section.
- Names without prices are left out with a note ("3 of 300 names have no prices"); a run fails only if none is left.
- Index members follow their dates: S&P 500, and CSI 300, CSI 500 and SSE 50 from Baostock's weekly lists.
- A-share industry is the classification known on each date (read quarterly, so changes show up to a quarter late). US and Hong Kong stocks have only today's classification, applied to the whole period.
- Survivorship: the free US list has current stocks only; a Sharadar key adds delisted ones. Baostock's A-share list includes delisted stocks.
- Short results are optimistic: free data does not say which stocks could be borrowed.
- Factors, models and strategies need instruments on one trading calendar; allocations can mix.

## 6. Agent

Agent is an AI assistant that works through TALYX: it sees what you are looking at and uses the same actions as the panels, in every module. It can read market data, change charts and alerts, create and run Quant objects, write code and files, and search the web. Its panel has **Chats** and **Bots**.

**Connect a model** in **Settings › Agent › Models**: a provider with your own key (OpenAI, Anthropic, Google, xAI, DeepSeek, Kimi, Zhipu), a model on this computer (Ollama or LM Studio), or another OpenAI- or Anthropic-compatible service. Test the connection; models that cannot call tools cannot be used. Enter prices to see costs under **Usage**.

**Chats.** Type in the composer (**@** for files, **/** for commands), attach files or images, choose the model and thinking. **Permissions**:

- **Read only**: asks before changing, deleting, running code or sending anything.
- **Auto**: asks before deleting something you own.
- **Full access**: never asks.

**Plan mode** makes it propose a plan for you to approve first. Approvals appear as cards; the side pane shows subagents, tasks and changes. A turn's changes to files and objects can be undone (**Undo**) and redone. **Edit from here** rewrites one of your messages, optionally undoing later changes; **Fork from here** branches a conversation. Agent's Python runs as your Windows user with your permissions and is not sandboxed.

**Bots** have their own role, model, permissions, budget and history. They run on schedules (presets include **Pre-market brief** and **Weekly review**), wake on events such as Chart alerts, and can work in groups. Chats can be scheduled too (**Scheduled**). Schedules run while the workspace is open; those missed while it was closed run once when it reopens. Unanswered background approvals expire as not done.

**Settings › Agent** also has **General**, **Chats**, **Bots**, **Personalization** (instructions, memory, knowledge folders with read-only or read-write access), **Search** (the model's own, Exa, or Tavily with a key), **Skills**, **Knowledge**, **Usage** and **Spending limit** (a monthly cap that stops new paid requests).

**AI apps outside TALYX** that support MCP, such as Claude Code or Codex, use the same actions. **Copy AI connection address** in the workspace menu gives an address bound to that workspace; for Claude Code, `claude mcp add --transport http talyx <address>`. The outside app's own approvals apply. **Settings › External AI** lists their calls.

## 7. Developer: writing your own modules

Developer is TALYX's editor: a project file tree and a Monaco editor; Python files of Quant objects get completion, go to definition and error marks. The project switcher has **New TALYX module**, **Open folder**, **Remove from list** and **Move to Recycle Bin**. For cross-file search, a terminal and breakpoints, **Open in VS Code** (needs VS Code with `code` on PATH and its Python extension); saves in VS Code show up within a second. Unsaved edits survive restarts (**Recovery**), **File history** keeps earlier versions, and a file changed on disk while you have edits is marked **Changed on disk** for you to choose.

**Writing a module:**

1. **New TALYX module** asks for a name and creates a working module in your modules folder, with one action (`greet`) and a panel that calls it. The frontend is plain JavaScript; there is no build step.
2. **Use in TALYX** enables it: its panel appears in **Add module** and its actions become available to Agent and outside AI apps.
3. After edits, **Reload** saves, regenerates the module's contract and restarts only this module.
4. **Debug in VS Code** writes a launch configuration; start it in **Run and Debug** to attach to the module's process. Click again after the module restarts.

A module declares its actions, data streams, settings and panels in Python; TALYX reads these declarations before running the code, and they are what other modules and AI see. Developer's help lists the SDK references. An installed module runs in its own process, which isolates crashes but is not a security sandbox: install only modules you trust.

## 8. Data and privacy

There is no TALYX account. The installed application keeps everything in `%LOCALAPPDATA%\TALYX\Data`:

| What | Folder under `Data` |
|---|---|
| Workspace folders | `workspaces\` |
| Module data: market data, Quant runs, conversations | `modules\<module>\` |
| Chart indicators | `module-files\chart\indicators\` |
| Quant common code, `requirements.txt` | `module-files\quant\common\` |
| Agent skills | `module-files\agent\skills\` |
| Modules you installed or wrote (their code; their data is in `modules\<module>\` above, and uninstalling a module removes only its code) | `installed-modules\` |

**Keys** for data sources, AI providers, search and notification channels are kept in Windows Credential Manager, not in files; TALYX only shows whether one is set.

**What leaves your computer**: requests to the data sources you enable (through your Windows proxy settings); the update check to GitHub, if on; your messages and what Agent reads to answer them, to the model provider you chose, and searches to the search service; phone notifications; the contact e-mail sent to the SEC.

**Upgrades** convert your data to the new format and keep it. An older version does not open data written by a newer one; it says so and leaves the data untouched. Uninstalling keeps `Data`; to remove everything, delete that folder and TALYX's entries in Credential Manager.

## 9. Getting help

- Errors say what failed and why. Where a dialog has **Copy technical details**, include that text in reports.
- Modules that could not start are under **Needs attention** in Settings and **Installed** in Community, with the reason and **Retry**.
- Questions and bug reports: the Issues page of `github.com/TalyxTerminal/TalyxTerminal`. Include the TALYX version (**Settings › General**), what you did and what you expected.
