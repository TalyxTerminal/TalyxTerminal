---
name: Market data
description: Instruments and their data (candles, crypto series, quotes, corporate actions, index members, fundamentals), read locally and filled from the source.
---

Use symbols directly (AAPL, 600519.SH, 0700.HK, US:DASH, USDCNY=X, ^GSPC), crypto exchange:BASE/QUOTE or exchange:BASE/QUOTE:SETTLE, or instrumentIds. instruments.search finds names and former codes.

- market.read_data {symbols:["AAPL","MSFT"],timeframe:"1d",start:"2025-01-01"} gives one long table. Without start, get the latest rows. For crypto series set data:"funding_rate", "open_interest", "taker_volume", etc.
- Historical index return ranking: in one Python call request market.read_data once with {index:{symbol:"^GSPC"},start:"2025-11-19",end:"2026-11-18",timeframe:"1d",adjustment:"split",format:"file"}. Use that file.path throughout. Historical bars already cover only each member’s time in the index. Sort by time, group by instrumentId and membershipPeriod, divide last close by first, then multiply period factors per instrument and subtract 1. Rank by that return and display symbol. Report unmapped names in missing. index.at selects a single day's constituents for the entire read window.
- For large selections format:"file" returns Parquet with per-symbol coverage, source and units in footer metadata "meta". On fill_too_large use market.fetch with the same selection first.
- market.read_research {data,symbols|index}: actions, members (join/leave dates and names), classification, shares, financials, market_cap, earnings_dates. market.read_quotes gives current quotes. market.read_coverage shows stored data, gaps and source limits for the selected environment (production by default). Deleting source series uses write_data operation.environment.
- market.estimate_fetch takes fetch inputs and returns estimatedBytes and freeBytes. market.downloads {operation:{op:"list"}} lists this workspace’s jobs. Cancel or resume an interrupted download by jobId with operation.op "cancel" or "resume".
- market.export destination:{folder:"exports"} writes inside the calling workspace and returns absolute paths. market.scan_import {folder:"imports"} previews price files. market.write_data {operation:{op:"import",folder:"imports"}} imports them. Folder inputs also accept absolute paths. For crypto imports specify the exchange and an existing contract.
- Dates start and end are inclusive. Stock dates use exchange local time, crypto UTC. Default stock prices are split-adjusted; use split_dividend for total return or raw for traded prices. Volume is shares or base currency, prices in quote currency. Report actual coverage dates.
