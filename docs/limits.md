# Data and limitations

TalyxTerminal fetches market data directly from each source, on your own computer. Nothing passes through our servers, and data fetched once is stored locally and read from there afterwards. This page lists what that means in practice: what the free sources cover, where they stop, and how to get more when you need it.

## Terms of use

Data belongs to its sources, and each source sets its own terms. A few examples:

- **Binance** publishes its historical data files (data.binance.vision) under CC BY-NC-SA 4.0: non-commercial use, including research, open-source education and personal backtesting.
- **OKX** data, from both its API and its history files, is for your own personal, non-commercial trading and account management under the OKX API Agreement. It may not be resold, redistributed or shown to third parties.
- **Bitget** has no documented download files; TalyxTerminal uses its API only.

TalyxTerminal never redistributes or hosts data: every request goes from your computer to the source.

## Crypto

Exchange data comes from the official APIs and history files: minute bars, funding rates, open interest, long/short ratios and taker volume, with full history where the exchange provides it.

- **History depth differs by exchange.** Some exchanges serve minute bars for only the last few days through their API, and OKX keeps about five days of open-interest history. Where neither the API nor the files cover a period, that period is empty in charts and backtests.
- **Some regions are blocked.** A few exchanges refuse connections from some regions (Bybit, for example, returns 403 in some places); data from that exchange is then unavailable.

## Stocks

Prices, financial statements, industries and share counts for US, China A-share and Hong Kong stocks come from free public sources. They are enough for everyday research. Three things are worth knowing:

- **Listed companies only.** Free sources cover companies that trade today, so companies delisted during a backtest period are not in the sample. Long backtests over broad universes are where this matters most.
- **Today's industries.** Industry classifications are current ones, applied across the whole history.
- **No securities-lending data.** Short positions assume the shares can always be borrowed.

When you need delisted companies, point-in-time industries or other institutional data, add a key for a paid source such as Sharadar, Alpaca, Tushare or Databento in Market's sources. Market switches to the better source automatically and the other modules need no change.

Two more details:

- **Hong Kong hourly bars** go back about 60 days. Yahoo's 60-minute bars do not line up with HKEX sessions, so hourly bars are built from 15-minute bars, which Yahoo keeps for about 60 days.
- **Intraday stock data** carries the source's publication delay.

## Fetching and real time

- **The first fetch takes time.** Data not yet on your machine is fetched page by page, within each source's page size and rate limits. After that it is read locally.
- **Real-time quotas.** Instruments beyond a source's free real-time quota are shown as delayed.
- **Not available from free sources yet:** order-book history (real time only), liquidation records, and option implied-volatility and Greeks series. Indicators that need them are not available yet.

## Other

- **Indicators are Python.** Indicators run as Python scripts on PyneCore. They follow Pine's semantics but are not the Pine language, so TradingView Pine scripts cannot be pasted in and run.
- **Charts per workspace.** A workspace holds at most eight charts.
- **Windows x64 only** for now.
