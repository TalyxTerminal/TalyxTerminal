"""Typing a coin's base shows its main pairs first, not its codes in alphabetical order."""

from modules.market.backend.identity import IdentityStore


def contract(symbol, *, market_type="spot", expiry=None, strike=None, side=None):
    base, rest = symbol.split("/")
    quote, _, settle = rest.partition(":")
    settle = settle.split("-")[0] or None
    return {"venueId": "", "symbol": symbol, "displaySymbol": symbol, "marketType": market_type, "assetClass": "crypto",
            "base": base, "quote": quote, "settle": settle,
            "contractOrientation": "none" if market_type == "spot" else "linear" if settle == quote else "inverse",
            "contractSize": None if market_type == "spot" else "1", "expiry": expiry, "strike": strike,
            "optionSide": side}


def put(store, venue, *records):
    store.upsert_contracts(venue, [{**record, "venueId": venue} for record in records])


def test_a_base_lists_spot_and_perpetuals_by_quote_and_venue_before_alphabetical_codes(tmp_path):
    store = IdentityStore(tmp_path / "identity.sqlite")
    put(store, "kraken", contract("BTC/AED"), contract("BTC/USDT"), contract("BTCST/USDT"))
    put(store, "okx", contract("BTC/USDT"), contract("BTC/USDT:USDT", market_type="swap"),
        contract("BTC/USD:BTC-261225", market_type="future", expiry="1798185600000000000"))
    put(store, "binance", contract("BTC/BRL"), contract("BTC/USDT"), contract("BTC/USDT:USDT", market_type="swap"))
    # Hundreds of dated options share the prefix; they must not crowd the pairs out of the candidates.
    put(store, "deribit", *(contract(f"BTC/USD:BTC-261225-{strike}-C", market_type="option",
                                     expiry="1798185600000000000", strike=str(strike), side="call")
                            for strike in range(10_000, 160_000, 500)))
    found = [(r["instrument"]["venueId"], r["instrument"]["symbol"]) for r in store.search("BTC", limit=12)]
    assert found[:10] == [
        ("binance", "BTC/USDT"), ("binance", "BTC/USDT:USDT"), ("okx", "BTC/USDT"), ("okx", "BTC/USDT:USDT"),
        ("kraken", "BTC/USDT"), ("binance", "BTC/BRL"), ("kraken", "BTC/AED"),
        ("okx", "BTC/USD:BTC-261225"), ("deribit", "BTC/USD:BTC-261225-10000-C"), ("deribit", "BTC/USD:BTC-261225-10500-C"),
    ]
    assert ("kraken", "BTCST/USDT") not in found


def test_delisting_current_code_retires_the_listing(tmp_path):
    from modules.market.backend.identity import IdentityStore

    store = IdentityStore(tmp_path / "identity.sqlite")
    original, = store.upsert_listings("US", [{"code": "REUSE", "codeFrom": "1"}], source="test")
    store.upsert_listings("US", [{"code": "REUSE", "codeUntil": "1000", "delistedAt": "1000"}], source="test")
    record = store.record(original)
    assert (record["active"], record["delistedAt"]) == (False, "1000")
