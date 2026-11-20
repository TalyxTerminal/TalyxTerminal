"""How an instrument is shown everywhere: a TradingView-style code and the exchange's name.

Identity stays the instrument id and ``symbol``; these two fields are presentation only and are
derived from the record each time it is read, so every module shows the same thing.
"""
from __future__ import annotations

from datetime import UTC, datetime

from .sources.crypto import VENUES

EXCHANGES = {  # listings by MIC
    "XNAS": "NASDAQ", "XNYS": "NYSE", "XASE": "AMEX", "ARCX": "NYSE Arca", "BATS": "Cboe", "IEXG": "IEX",
    "OTCM": "OTC", "XSHG": "SSE", "XSHE": "SZSE", "XBSE": "BSE", "BJSE": "BSE", "XHKG": "HKEX",
}
MARKETS = {"COIN": "Crypto", "FX": "Forex", "US": "US", "CN": "China", "HK": "HKEX"}
MONTHS = "FGHJKMNQUVXZ"  # futures month codes, January to December


def display_symbol(record: dict) -> str:
    """Spot BTCUSDT, perpetual BTCUSDT.P, dated future BTCUSDTZ2026, option BTCUSD261225C100000;
    listings (stocks, funds, indices) keep their code."""
    if record["identityVersion"] == 2 or not record.get("base") or not record.get("quote"):
        return record["symbol"]
    pair = f"{record['base']}{record['quote']}"
    kind = record["marketType"]
    if kind == "swap":
        return pair + ".P"
    if kind in ("future", "option") and record.get("expiry"):
        expiry = datetime.fromtimestamp(int(record["expiry"]) / 1e9, UTC)
        if kind == "future":
            return f"{pair}{MONTHS[expiry.month - 1]}{expiry.year}"
        side = "C" if record.get("optionSide") == "call" else "P"
        return f"{pair}{expiry:%y%m%d}{side}{record.get('strike') or ''}"
    return pair


def venue_name(record: dict) -> str:
    if record["identityVersion"] == 1:
        return place_name(record["venueId"])
    return EXCHANGES.get(record.get("mic") or "", place_name(record["venueId"]))


def place_name(venue: str) -> str:
    """A directory's venue or market as shown: OKX, China; a market a module's source adds keeps its code."""
    return VENUES[venue]["name"] if venue in VENUES else MARKETS.get(venue, venue)


def presented(record: dict) -> dict:
    record["displaySymbol"] = display_symbol(record)
    record["venueName"] = venue_name(record)
    return record


QUOTES = ("FDUSD", "USDT", "USDC", "BUSD", "TUSD", "USD", "EUR", "GBP", "JPY", "TRY", "BRL", "AUD", "KRW", "PLN",
          "DAI", "BTC", "ETH", "BNB")


def typed_codes(text: str) -> set[str]:
    """What a TradingView-style code someone typed is in Market's symbols (prefixes for futures):
    BTCUSDT → BTC/USDT, BTCUSDT.P → BTC/USDT:USDT (and the inverse BTC/USD:BTC), BTCUSDTZ2026 →
    BTC/USDT:USDT-2612…"""
    upper = text.strip().upper()
    kind, month = "spot", None
    if upper.endswith(".P"):
        kind, upper = "swap", upper[:-2]
    elif len(upper) > 6 and upper[-4:].isdigit() and upper[-5] in MONTHS:
        kind, month, upper = "future", f"{upper[-2:]}{MONTHS.index(upper[-5]) + 1:02d}", upper[:-5]
    found = set()
    for quote in QUOTES:
        if upper.endswith(quote) and len(upper) > len(quote):
            base = upper[:-len(quote)]
            pair = f"{base}/{quote}"
            if kind == "spot":
                found.add(pair)
            else:
                for settle in (quote, base):
                    contract = f"{pair}:{settle}"
                    found.add(contract if kind == "swap" else f"{contract}-{month}")
    return found
