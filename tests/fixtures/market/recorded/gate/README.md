# Recorded gate responses

Replayed by the Market tests (`tests/unit/test_market_*.py`); no credentials.

- `files.json`: download.gatedata.org `futures_usdt` `candlesticks_1h` and `funding_applies` for BTC_USDT, 2024-01 (gzip bodies in base64).
- `testnet.json`: Gate testnet (api-testnet.gateapi.io) `load_markets` via CCXT, trimmed to BTC_USDT/ETH_USDT spot and perpetuals and the BTC_USDT options expiring 2026-11-10 (40 contracts), plus that expiry's option contracts and tickers (option chain); recorded 2026-11-10.
