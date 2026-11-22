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

**US stocks.** Nasdaq’s free list shows stocks listed today. If you use it to pick stocks for a past period, you miss companies that have since delisted. A paid Sharadar key adds delisted US stocks when Sharadar has both a listing (TICKERS) and daily prices (SEP). TALYX uses the day after the last recorded price as an estimate of the delisting date.

**China A shares.** Baostock’s free data includes delisted stocks. Its weekly lists show which stocks belonged to the CSI 300, CSI 500 and SSE 50 at the time. Paid Tushare data gives each stock’s weight in the index once a month. Backtests use those weights from the date on each list. Until Tushare reports a new member’s weight, it stays blank.

**Industry history.** For China A shares, TALYX reads the CSRC industry classification from Baostock every quarter. A backtest uses the latest classification available on the date it is testing, so a change can take up to a quarter to appear. For US and Hong Kong stocks, TALYX uses today’s industry throughout the backtest.

**Hong Kong order sizes.** Each stock has a board lot, the number of shares in one standard order. TALYX uses the latest HKEX list for every date in a backtest. It does not model past changes in lot size or the prices you might get when trading less than a full lot.

A few details affect how to read a stock backtest.

- **When prices stop.** If a stock’s price history ends during a backtest, the backtest closes the position at the last recorded close. After a delisting or takeover, the amount an investor actually received may have been different.
- **Price screens.** Adjusted prices use the latest price as their reference. To find stocks that actually traded below $5 on a past date, use raw prices.
- **Short selling.** Free data does not tell you which stocks you could borrow on a given day. The backtest assumes you can borrow any stock you short.

Stock price coverage also varies by source.

- **Hong Kong hourly bars** go back about 60 days. Yahoo's 60-minute bars do not line up with HKEX sessions, so hourly bars are built from 15-minute bars, which Yahoo keeps for about 60 days.
- **Databento US equities.** XNAS.ITCH covers Nasdaq trades and EQUS.MINI aggregates its included venues. EQUS.SUMMARY provides daily bars with extended-hours volume. Hourly bars follow trading sessions.
- **Intraday stock data** carries the source's publication delay.

## Fetching and real time

- **The first fetch takes time.** Data not yet on your machine is fetched page by page, within each source's page size and rate limits. After that it is read locally.
- **Real-time quotas.** Instruments beyond a source's free real-time quota are shown as delayed.
- **Not available from free sources yet:** order-book history (real time only), liquidation records, and option implied-volatility and Greeks series. Indicators that need them are not available yet.

## Other

- **Indicators are Python.** Indicators run as Python scripts on PyneCore. They follow Pine's semantics but are not the Pine language, so TradingView Pine scripts cannot be pasted in and run.
- **Charts per workspace.** A workspace holds at most eight charts.
- **Windows x64 only** for now.
