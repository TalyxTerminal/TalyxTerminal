"""HKEX's list of securities: every Hong Kong listing, updated each trading day.

``ListOfSecurities.xlsx`` has three title rows, then one row per security: stock code
(five digits), name, category, sub-category, board lot, ISIN, ..., trading currency and
the RMB counter's code. Market keeps the categories it serves (Equity, Exchange Traded
Products, Real Estate Investment Trusts); warrants, CBBCs and debt are left out. The list
names only today's listings and no listing dates: a code's period starts when Market
first sees it and moves back to the earliest bar a source returns (codes are reused:
00013 is HUTCHMED since 2021-06-30).
"""

from __future__ import annotations

import io
from datetime import datetime

from talyx_sdk.i18n import t

from ..common import MarketError
from ..routing import in_worker
from .base import Source, coverage, row

URL = "https://www.hkex.com.hk/eng/services/trading/securities/securitieslists/ListOfSecurities.xlsx"
_KEEP = {
    "Equity": ("equity", "common_stock"),
    "Exchange Traded Products": ("fund", "etf"),
    "Real Estate Investment Trusts": ("fund", "reit"),
}
_SUB = {"Depositary Receipts": ("equity", "depositary_receipt"), "Investment Companies": ("fund", "closed_end_fund"),
        "Leveraged and Inverse": ("fund", "leveraged_etf")}
_CURRENCY = {"RMB": "CNY"}


def parse(body: bytes) -> tuple[list[dict], dict]:
    import openpyxl

    try:
        book = openpyxl.load_workbook(io.BytesIO(body), read_only=True, data_only=True)
    except Exception as exc:  # noqa: BLE001 - any unreadable workbook is the source's problem
        raise MarketError("source_unavailable", t("HKEX list is unreadable: {exc}", exc=exc), source_id="hkex") from exc
    sheet = book.active
    sheet.reset_dimensions()  # the file declares a smaller range than it holds
    candidates, lots = [], {}
    rows = sheet.iter_rows(values_only=True)
    next(rows)
    updated = datetime.strptime(next(rows)[0].split()[-1], "%d/%m/%Y").date().isoformat()
    for values in rows:
        code, name, category, sub = (values + (None,) * 4)[:4]
        if not code or not str(code).strip().isdigit() or category not in _KEEP:
            continue
        asset, security = _SUB.get(sub) or _KEEP[category]
        currency = values[16] if len(values) > 16 and values[16] else "HKD"
        candidates.append({
            "type": "listing", "market": "HK", "code": f"{str(code).strip().zfill(5)}.HK",
            "name": str(name).strip() if name else None, "assetClass": asset, "securityType": security,
            "currency": _CURRENCY.get(str(currency).strip(), str(currency).strip()), "mic": "XHKG",
            "calendarId": "XHKG"})
        if values[4]:
            lots[candidates[-1]["code"]] = {"size": int(str(values[4]).replace(",", "")),
                                            "stampDuty": values[7] == "Y"}
    book.close()
    return candidates, {"date": updated, "listings": lots}


class HkexListings(Source):
    id = "hkex"
    descriptor = {"name": "HKEX list of securities", "sourceIds": ["hkex"], "kinds": ["listings"],
                  "assetClasses": ["equity", "fund"], "credentials": "none", "paid": False, "supports": ["catalog"]}

    async def describe(self, source_id: str) -> dict:
        return coverage([row({"markets": ["HK"]}, "listings")], concurrency=1, rpm=None)

    async def catalog(self, source_id: str, scope: dict, environment: str, cursor: str | None):
        body = await self.egress.bytes(self.id, URL, timeout=90)
        listings, lots = await in_worker(parse, body)
        await in_worker(self.market.terms.board_lots.write, "HK", lots)
        return listings, None
