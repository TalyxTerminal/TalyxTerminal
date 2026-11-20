# Recorded baostock answers

Replayed by `tests/unit/test_market_cn.py` through `BaostockReplay` (at the library's single boundary, `baostock.util.socketutil.send_msg`); anonymous login, no credentials.

- `cn.json` (recorded 2026-11-10 from public-api.baostock.com:10030, direct connection): request text without header and checksum → answer text. `query_stock_basic` is the real list trimmed to the stocks listed on 1991-04-03, 1994-09-28, 1998-01-22, 2001-08-27 and 2012-01-16, plus 000001.SZ, 000002.SZ, 300750.SZ, 600036.SH, 600087.SH, the indices 000001.SH and 000300.SH and the ETF 510300.SH (one page). The rest is what Market asked for with that list: the code-change checks (daily rows before each delisted code ended), 000043/001914 daily for 2019-12, 600519 daily and adjustment factors with the 2024 dividends, the CSRC industry of the whole market at 2015-06-30 (2,853 rows in two pages), 2023 quarterly profit data for 600519, 000001 and 600036, and 000001 hourly bars on 2024-06-18.
