"""SEC EDGAR company facts: US filers' XBRL financial statement values, as filed.

The SEC asks every automated client to name a contact e-mail in its User-Agent; the
user enters it in this source's settings (``contactEmail``). Without it the source
answers ``unsupported`` before any request. ``company_tickers.json`` maps a ticker to
its CIK; ``companyfacts/CIK##########.json`` holds every fact the company filed, each
with the form and the day it was filed. A row is one filed value: its knownAt is the
acceptance time (end of the New York filing day when only a date is supplied), so a later restatement is a new row and ``asOf`` keeps what was public then.
"""

from __future__ import annotations

import time
from datetime import date, datetime, timedelta
from datetime import time as clock_time
from functools import lru_cache
from zoneinfo import ZoneInfo

from ..common import NS, MarketError
from .base import Source, Target, coverage, row

TICKERS = "https://www.sec.gov/files/company_tickers.json"
FACTS = "https://data.sec.gov/api/xbrl/companyfacts/CIK{cik:010d}.json"
ANNUAL_FORMS = {"10-K", "10-K/A", "20-F", "20-F/A", "40-F", "40-F/A"}
QUARTER_FORMS = {"10-Q", "10-Q/A"}


class SecFilings(Source):
    id = "sec"
    descriptor = {"name": "SEC EDGAR company facts", "sourceIds": ["sec"], "kinds": ["financials"],
                  "assetClasses": ["equity"], "credentials": "none", "paid": False, "supports": ["research"],
                  "settings": [{"name": "contactEmail", "label": "Contact e-mail (sent to the SEC with each request)",
                                "type": "string", "secret": False, "required": True}]}

    def __init__(self, market):
        super().__init__(market)
        self._ciks: tuple[float, dict[str, int]] | None = None

    async def describe(self, source_id: str) -> dict:
        return coverage([row({"markets": ["US"], "assetClasses": ["equity"]}, "financials", point_in_time=True)],
                        concurrency=4, rpm=600)

    def _agent(self, source_id: str) -> dict:
        email = (self.settings.get("contactEmail") or "").strip()
        if "@" not in email:
            raise MarketError("unsupported", "SEC filings need a contact e-mail in the SEC source's settings",
                              source_id=source_id)
        return {"User-Agent": f"TALYX research {email}", "Accept-Encoding": "gzip, deflate"}

    async def _cik(self, source_id: str, code: str, headers: dict) -> int | None:
        if self._ciks is None or time.monotonic() - self._ciks[0] > 86400:
            data = await self.egress.json(self.id, TICKERS, headers=headers, timeout=60)
            table = {str(item["ticker"]).upper(): int(item["cik_str"]) for item in data.values()}
            self._ciks = (time.monotonic(), table)
        table = self._ciks[1]
        return table.get(code.upper()) or table.get(code.upper().replace(".", "-"))

    async def research(self, source_id: str, target: Target, kind: str) -> list[dict]:
        if kind != "financials":
            raise MarketError("unsupported", f"{source_id} gives financials only", source_id=source_id)
        headers = self._agent(source_id)
        cik = await self._cik(source_id, target.code_at(), headers)
        if cik is None:
            raise MarketError("no_data", f"The SEC has no filer for {target.code_at()}", source_id=source_id)
        data = await self.egress.json(self.id, FACTS.format(cik=cik), headers=headers, timeout=60)
        return facts_rows(data)


@lru_cache(maxsize=8192)
def _midnight(day: date) -> int:
    return int(datetime.combine(day, clock_time(), ZoneInfo("America/New_York")).timestamp()) * NS


@lru_cache(maxsize=8192)
def _known_at(accepted: str | None, filed: str) -> int:
    if accepted:
        moment = datetime.fromisoformat(accepted)
        if moment.tzinfo is None:
            moment = moment.replace(tzinfo=ZoneInfo("America/New_York"))
        return int(moment.timestamp()) * NS + moment.microsecond * 1000
    return _midnight(date.fromisoformat(filed) + timedelta(days=1)) - 1


def facts_rows(data: dict) -> list[dict]:
    """Filed values of every concept as research rows (FY and FQ periods only)."""
    out = []
    for taxonomy, concepts in (data.get("facts") or {}).items():
        for concept, body in concepts.items():
            for unit, facts in (body.get("units") or {}).items():
                seen: dict[tuple, int] = {}
                for fact in sorted(facts, key=lambda item: item.get("filed") or ""):
                    period = _period_kind(fact)
                    if period is None or not fact.get("filed") or fact.get("val") is None:
                        continue
                    key = (fact["end"], period)
                    revision = seen.get(key, 0)
                    seen[key] = revision + 1
                    known = _known_at(fact.get("acceptanceDateTime"), fact["filed"])
                    out.append({"metric": f"{taxonomy}:{concept}", "periodEnd": str(_midnight(date.fromisoformat(fact["end"]))),
                                "periodKind": period, "value": _decimal(fact["val"]), "unit": unit,
                                "currency": unit if len(unit) == 3 and unit.isupper() else None,
                                "knownAt": str(known), "revision": revision,
                                "form": fact.get("form")})
    return out


def _period_kind(fact: dict) -> str | None:
    form, fiscal = fact.get("form") or "", fact.get("fp") or ""
    days = None
    if fact.get("start") and fact.get("end"):
        days = (date.fromisoformat(fact["end"]) - date.fromisoformat(fact["start"])).days
    if form in ANNUAL_FORMS and fiscal == "FY" and (days is None or 340 <= days <= 380):
        return "FY"
    if form in QUARTER_FORMS and fiscal.startswith("Q") and (days is None or 80 <= days <= 100):
        return "FQ"
    return None


def _decimal(value) -> str:
    if isinstance(value, int):
        return str(value)
    text = format(value, "f").rstrip("0").rstrip(".")
    return text or "0"
