"""Market's canonical codes and each source's spelling of them."""

from __future__ import annotations

import re

from talyx_sdk.i18n import t

from .common import MarketError

MARKET_CALENDARS = {"US": "XNYS", "CN": "XSHG", "HK": "XHKG", "FX": "24_7", "COIN": "24_7"}
MARKET_CURRENCIES = {"US": "USD", "CN": "CNY", "HK": "HKD"}


def yahoo_symbol(market: str, code: str) -> str:
    """Market code → Yahoo symbol (BRK.B → BRK-B, ABR$D → ABR-PD, 600519.SH → 600519.SS, 00700.HK → 0700.HK)."""
    if market == "US":
        if code.startswith("^"):
            return code
        match = re.fullmatch(r"([A-Z0-9]+)\$([A-Z]?)", code)
        if match:
            return f"{match.group(1)}-P{match.group(2)}"
        for suffix, yahoo in ((".U", "-UN"), (".W", "-WT"), (".R", "-RI")):
            if code.endswith(suffix):
                return code[: -len(suffix)] + yahoo
        return code.replace(".", "-")
    if market == "CN":
        number, exchange = code.split(".")
        if exchange == "SH":
            return f"{number}.SS"
        if exchange == "SZ":
            return f"{number}.SZ"
        raise MarketError("unsupported", t("Yahoo has no Beijing listings ({code})", code=code), source_id="yfinance")
    if market == "HK":
        number = code.split(".")[0]
        return (number[1:] if number.startswith("0") else number) + ".HK"
    return code


def market_from_yahoo(symbol: str, exchange: str | None = None) -> tuple[str, str] | None:
    """Yahoo symbol → (market, code), for searches of sources Market has no list for."""
    if symbol.endswith(".SS"):
        return "CN", symbol[:-3] + ".SH"
    if symbol.endswith(".SZ"):
        return "CN", symbol
    if symbol.endswith(".HK"):
        return "HK", symbol[:-3].zfill(5) + ".HK"
    if symbol.endswith("=X"):
        return "FX", symbol
    if "." in symbol or exchange in {"LSE", "TOR", "GER", "PAR"}:
        return None
    if symbol.startswith("^") or symbol.endswith("=F"):
        return "US", symbol
    match = re.fullmatch(r"([A-Z0-9]+)-P([A-Z]?)", symbol)
    if match:
        return "US", f"{match.group(1)}${match.group(2)}"
    for yahoo, suffix in (("-UN", ".U"), ("-WT", ".W"), ("-RI", ".R")):
        if symbol.endswith(yahoo):
            return "US", symbol[: -len(yahoo)] + suffix
    return "US", symbol.replace("-", ".")


def cn_code(number: str) -> str:
    """Six-digit A-share number → Market code by exchange prefix (funds: 5 Shanghai, 1 Shenzhen)."""
    if number.startswith(("4", "8", "92")):
        return f"{number}.BJ"
    if number.startswith(("6", "9", "5")):
        return f"{number}.SH"
    if number.startswith(("0", "2", "3", "1")):
        return f"{number}.SZ"
    raise MarketError("invalid_input", t("Unknown A-share number {number}", number=number))


def baostock_code(code: str) -> str:
    number, exchange = code.split(".")
    return f"{exchange.lower()}.{number}"


def tushare_code(code: str) -> str:
    return code


def alpaca_symbol(code: str) -> str:
    # Alpaca writes class shares with a dot (BRK.B), as Market does.
    return code


def parse_typed(text: str) -> tuple[str | None, str]:
    """User text → (market or None, code). Handles MARKET:CODE and exchange suffixes."""
    value = text.strip().upper()
    if ":" in value:
        prefix, rest = value.split(":", 1)
        if prefix in MARKET_CALENDARS:
            return prefix, rest
    if value.endswith("=X"):
        return "FX", value
    match = re.fullmatch(r"(\d{6})\.(SH|SZ|BJ|SS)", value)
    if match:
        exchange = "SH" if match.group(2) == "SS" else match.group(2)
        return "CN", f"{match.group(1)}.{exchange}"
    match = re.fullmatch(r"(\d{1,5})\.HK", value)
    if match:
        return "HK", match.group(1).zfill(5) + ".HK"
    if re.fullmatch(r"\d{6}", value):
        return "CN", cn_code(value)
    return None, value
