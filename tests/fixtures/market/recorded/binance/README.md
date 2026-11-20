# Recorded binance responses

Replayed by the Market tests (`tests/unit/test_market_*.py`); no credentials or e-mail addresses. Recorded 2026-11-10 through a US network endpoint.

- `markets.json`: Binance `load_markets` via CCXT 4.5.77 (spot exchangeInfo from data-api.binance.vision, USD-M/COIN-M exchangeInfo from www.binance.com), trimmed to 20 spot USDT pairs and a few perpetuals (TLMUSDT, ICPUSDT, FTTUSDT with their onboardDate).
- `files.json`: data.binance.vision files as Market requested them (zip bodies in base64): USD-M 1d monthly files of TLMUSDT (every month), LUNAUSDT, FTTUSDT, ICPUSDT and BTCUSDT around their onboard dates; LUNAUSDT 2022-02 daily files for the days the monthly file misses; BTCUSDT fundingRate 2024-11; S3 listings (s3-ap-northeast-1.amazonaws.com/data.binance.vision, trimmed to the codes above); spot LUNAUSDT 1d klines from data-api.binance.vision (all pages).
- `history.json`: CCXT spot klines for LUNA/USDT 2022-05 (data-api.binance.vision).
