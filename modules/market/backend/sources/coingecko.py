"""CoinGecko: coins (not exchange contracts), their market capitalisation history and categories.

Coins are listings in the ``COIN`` market; the code is CoinGecko's coin id (``bitcoin``),
since ticker symbols repeat across coins, and the name carries the symbol
(``Bitcoin (BTC)``) so a search for BTC finds it. The directory is the 1,000 largest
coins by market cap with a Demo key (four pages of 250) and the 250 largest without one (the
keyless API allows only a few requests a minute). Without a key the public API serves a year
of daily history; a Demo key (free, optional) raises the limits.
Data shown from it must credit "Data provided by CoinGecko".
"""

from __future__ import annotations

from ..common import DAY_NS, MarketError, now_ns
from .base import Source, Target, coverage, row

BASE = "https://api.coingecko.com/api/v3"
PAGES = 4
ATTRIBUTION = "Data provided by CoinGecko"


class CoinGecko(Source):
    id = "coingecko"
    descriptor = {"name": "CoinGecko", "sourceIds": ["coingecko"], "kinds": ["listings", "market_cap", "categories"],
                  "assetClasses": ["crypto"], "credentials": "optional", "paid": False,
                  "supports": ["catalog", "research"], "attribution": ATTRIBUTION,
                  "settings": [{"name": "apiKey", "label": "Demo API key (optional)", "type": "string",
                                "secret": True, "required": False}]}
    secret_settings = ("apiKey",)

    async def describe(self, source_id: str) -> dict:
        scope = {"markets": ["COIN"]}
        return coverage([row(scope, "listings"), row(scope, "market_cap", [("1d", "P365D", 365)]),
                         row(scope, "categories")], concurrency=1, rpm=25 if not self.key_configured else 90)

    async def _headers(self, source_id: str) -> dict:
        key = await self.secret(source_id, "apiKey") if self.key_configured else None
        return {"x-cg-demo-api-key": key} if key else {}

    async def catalog(self, source_id: str, scope: dict, environment: str, cursor: str | None):
        headers = await self._headers(source_id)
        candidates = []
        for page in range(1, (PAGES if headers else 1) + 1):
            items = await self.egress.json(self.id, f"{BASE}/coins/markets", headers=headers, timeout=30,
                                           params={"vs_currency": "usd", "order": "market_cap_desc", "per_page": 250,
                                                   "page": page, "sparkline": "false"})
            for item in items:
                symbol = str(item.get("symbol") or "").upper()
                candidates.append({"type": "listing", "market": "COIN", "code": item["id"],
                                   "name": f"{item.get('name') or item['id']} ({symbol})" if symbol else item["id"],
                                   "assetClass": "crypto", "securityType": None, "currency": "USD", "mic": None,
                                   "calendarId": "24_7", "sourceKey": item["id"]})
            if len(items) < 250:
                break
        return candidates, None

    def _coin(self, target: Target) -> str:
        if target.instrument["venueId"] != "COIN":
            raise MarketError("unsupported", "CoinGecko answers for coins (market COIN) only", source_id=self.id)
        return target.source_key or target.instrument["symbol"]

    async def research(self, source_id: str, target: Target, kind: str) -> list[dict]:
        coin = self._coin(target)
        headers = await self._headers(source_id)
        if kind == "market_cap":
            data = await self.egress.json(self.id, f"{BASE}/coins/{coin}/market_chart", headers=headers, timeout=30,
                                          params={"vs_currency": "usd", "days": 365, "interval": "daily"})
            rows = {}
            for stamp, value in data.get("market_caps") or []:
                if value is None:
                    continue
                moment = int(stamp) * 1_000_000
                # A value stamped at 00:00 UTC is the previous day's close; the last one is the current value.
                day = moment // DAY_NS * DAY_NS - (DAY_NS if moment % DAY_NS == 0 else 0)
                rows[day] = {"date": str(day), "value": _decimal(value), "currency": "USD", "knownAt": str(moment)}
            return [rows[day] for day in sorted(rows)]
        if kind == "categories":
            data = await self.egress.json(self.id, f"{BASE}/coins/{coin}", headers=headers, timeout=30,
                                          params={"localization": "false", "tickers": "false", "market_data": "false",
                                                  "community_data": "false", "developer_data": "false"})
            known = str(now_ns())
            return [{"category": _slug(name), "name": name, "knownAt": known} for name in data.get("categories") or []
                    if name]
        raise MarketError("unsupported", f"CoinGecko gives market_cap and categories, not {kind}", source_id=self.id)

    def note(self, source_id: str, kind: str) -> str | None:
        return ATTRIBUTION


def _decimal(value) -> str:
    number = float(value)
    return str(int(number)) if number.is_integer() else repr(number)


def _slug(name: str) -> str:
    return "-".join("".join(ch.lower() if ch.isalnum() else " " for ch in name).split())
