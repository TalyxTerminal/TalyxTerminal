# Recorded yfinance responses

Replayed by the Market tests (`tests/unit/test_market_*.py`); no credentials or e-mail addresses.

- `chart-daily.json`: Yahoo v8 chart, interval 1d with events: AAPL 2020-06..2021-01, NVDA 2024-05..06, 0700.HK 2024-10/11, ^GSPC 2024-12..2025-01, AAPL 2015-11..2016-01, BRK-B 2024-06; v1 search for ^GSPC (query2.finance.yahoo.com, recorded through a US network endpoint).
- `research.json`: Yahoo v8 chart 1d with events for 20 US stocks from 2024-01 (trimmed to 2025-01-10, splits kept) and for the 2015-12 S&P members (trimmed to 2016-02-01, splits kept); v1 search for codes not in the directory; quoteSummary assetProfile and fundamentals-timeseries shares for AAPL (query2.finance.yahoo.com, fc.yahoo.com; recorded through a US network endpoint).
- `hk.json`: Yahoo v8 chart 1d with events for 0013.HK from 2014-01-01 (Yahoo's rows start 2021-06-30), trimmed to 2021-08-01 (recorded 2026-11-10).
- `renames.json`: Yahoo v8 chart 1d for AAPL from 2018-09-01 (trimmed to 2019-01-05) and for today's FB (an ETF listed 2025-06-26) from 2025-06-01 (trimmed to 2025-07-31) (recorded 2026-11-10).
- `intraday.json`: Yahoo v8 chart without pre/post market: 0700.HK 1m, 15m and 60m and AAPL 1m for 2026-11-13 (the Hong Kong closing auction row at 16:08, the 60m afternoon starting at 12:30; the AAPL zero-volume 16:00 row), and AAPL 60m for the 2025-11-28 half day (an empty 12:30 row, and the last half hour under 13:00 with volume 0); meta tradingPeriods dropped (recorded 2026-11-14).
