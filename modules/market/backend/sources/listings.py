"""Official listing directories: Nasdaq Trader (all US listings)."""

from __future__ import annotations

import re

from ..common import MarketError
from .base import Source, coverage, row

NASDAQ = "https://www.nasdaqtrader.com/dynamic/SymDir/"
_OTHER_MIC = {"N": "XNYS", "A": "XASE", "P": "ARCX", "Z": "BATS", "V": "IEXG"}
_SUFFIX = re.compile(r"\s+-\s+(Common Stock|Ordinary Shares|American Depositary Shares.*)$")


def _security(name: str, etf: bool) -> tuple[str, str]:
    lowered = name.lower()
    if etf:
        return "fund", "etf"
    if "warrant" in lowered:
        return "warrant", "warrant"
    if re.search(r"\bunits?\b", lowered):
        return "equity", "unit"
    if re.search(r"\brights?\b", lowered):
        return "equity", "rights"
    if "preferred" in lowered or "depositary shares, each representing" in lowered and "preferred" in lowered:
        return "equity", "preferred_stock"
    if "american depositary" in lowered:
        return "equity", "depositary_receipt"
    return "equity", "common_stock"


class NasdaqListings(Source):
    id = "nasdaqtrader"
    descriptor = {"name": "Nasdaq Trader symbol directory", "sourceIds": ["nasdaqtrader"], "kinds": ["listings"],
                  "assetClasses": ["equity", "fund", "warrant"], "credentials": "none", "paid": False,
                  "supports": ["catalog"]}

    async def describe(self, source_id: str) -> dict:
        return coverage([row({"markets": ["US"]}, "listings")], concurrency=2, rpm=None)

    async def catalog(self, source_id: str, scope: dict, environment: str, cursor: str | None):
        candidates = []
        listed = await self._directory("nasdaqlisted.txt", "Symbol|Security Name|")
        for line in listed.splitlines()[1:]:
            parts = line.split("|")
            if len(parts) < 8 or line.startswith("File Creation Time") or parts[3] == "Y":
                continue
            asset, security = _security(parts[1], parts[6] == "Y")
            candidates.append(_candidate(parts[0], parts[1], asset, security, "XNAS"))
        other = await self._directory("otherlisted.txt", "ACT Symbol|Security Name|")
        for line in other.splitlines()[1:]:
            parts = line.split("|")
            if len(parts) < 8 or line.startswith("File Creation Time") or parts[6] == "Y":
                continue
            asset, security = _security(parts[1], parts[4] == "Y")
            candidates.append(_candidate(parts[0], parts[1], asset, security, _OTHER_MIC.get(parts[2])))
        return candidates, None

    async def _directory(self, name: str, header: str) -> str:
        """One symbol directory file; anything else (an error page) is the source failing, not an empty market."""
        text = (await self.egress.bytes(self.id, NASDAQ + name, timeout=60)).decode("utf-8", "replace")
        if not text.startswith(header):
            raise MarketError("source_unavailable", f"nasdaqtrader returned no symbol directory for {name}",
                              source_id=self.id)
        return text


def _candidate(code: str, name: str, asset: str, security: str, mic: str | None) -> dict:
    return {"type": "listing", "market": "US", "code": code.strip(), "name": _SUFFIX.sub("", name.strip()),
            "assetClass": asset, "securityType": security, "currency": "USD", "mic": mic, "calendarId": "XNYS"}
