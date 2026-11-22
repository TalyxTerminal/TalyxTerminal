"""Index member history: the S&P 500 compiled by fja05680 (github.com/fja05680/sp500).

``sp500_ticker_start_end.csv`` gives one row per membership period: ticker, the day it
joined, the day it left (empty while a member). Dates are effective dates; the file
does not say when a change was announced, so a row's knownAt is its start. Tickers are
the codes in use while the company was a member, so Market maps them to instruments
by (US, code, date) when read.
"""

from __future__ import annotations

import csv
import io

import numpy as np
from talyx_sdk.i18n import mark, t

from .. import calendars
from ..common import MarketError, ns_of_date
from .base import Source, Target, coverage, row

URL = "https://raw.githubusercontent.com/fja05680/sp500/master/sp500_ticker_start_end.csv"
INDEX = "^GSPC"
NOTE = mark("S&P 500 members by effective date from fja05680's public compilation of S&P announcements; "
        "it includes companies that later left the index (no survivorship bias), but the announcement "
        "dates are not recorded, so knownAt is the effective date.")


CN_INDICES = {"000300.SH": ("query_hs300_stocks", "399300.SZ"),
              "000905.SH": ("query_zz500_stocks", "000905.SH"),
              "000016.SH": ("query_sz50_stocks", "000016.SH")}


def cn_indices(identity):
    result = []
    for code in CN_INDICES:
        listing = identity.find_code("CN", code)
        if listing:
            result.append({"index": identity.record(listing), "start": None, "end": None,
                           "pointInTime": True, "survivorshipBias": False, "note": None})
    return result


def localize_sp500_dates(rows):
    """The CSV dates and the original cached rows encode New York effective dates at UTC midnight."""
    dates = sorted({int(row[field]) for row in rows for field in ("from", "until", "knownAt")
                    if row[field] is not None})
    days = np.asarray(dates, dtype="datetime64[ns]").astype("datetime64[D]")
    midnights = dict(zip(map(str, dates), map(str, calendars.local_midnight("XNYS", days)), strict=True))
    for member in rows:
        for field in ("from", "until", "knownAt"):
            if member[field] is not None:
                member[field] = midnights[member[field]]


class Sp500History(Source):
    id = "sp500_history"
    descriptor = {"name": "S&P 500 member history (fja05680)", "sourceIds": ["sp500_history"], "kinds": ["members"],
                  "assetClasses": ["index"], "credentials": "none", "paid": False, "supports": ["research"]}

    async def describe(self, source_id: str) -> dict:
        return coverage([row({"markets": ["US"], "assetClasses": ["index"]}, "members", point_in_time=True)],
                        concurrency=1, rpm=30)

    async def research(self, source_id: str, target: Target, kind: str) -> list[dict]:
        if kind != "members" or target.instrument["symbol"] != INDEX:
            raise MarketError("unsupported", t("{source_id} gives S&P 500 ({INDEX}) members only", source_id=source_id, INDEX=INDEX), source_id=source_id)
        text = (await self.egress.bytes(self.id, URL, timeout=60)).decode("utf-8", "replace")
        rows = []
        for item in csv.DictReader(io.StringIO(text)):
            code, start, end = (item.get("ticker") or "").strip(), (item.get("start_date") or "").strip(), \
                (item.get("end_date") or "").strip()
            if not code or not start:
                continue
            begin = ns_of_date(start)
            rows.append({"code": code, "from": str(begin), "until": str(ns_of_date(end)) if end else None,
                         "knownAt": str(begin)})
        if not rows:
            raise MarketError("source_unavailable", t("{source_id} returned no members", source_id=source_id), source_id=source_id)
        localize_sp500_dates(rows)
        return rows

    def note(self, source_id: str, kind: str) -> str | None:
        return t(NOTE) if kind == "members" else None

    def indices(self, source_id: str) -> list[dict]:
        found = self.market.identity.find_code("US", INDEX, None)
        if found is None:
            return []
        record = self.market.identity.record(self.market.identity.survivor(found) or found)
        start = calendars.local_midnight(record["calendarId"], np.array(["1996-01-02"], dtype="datetime64[D]"))[0]
        return [{"index": record, "start": str(start), "end": None, "pointInTime": True,
                 "survivorshipBias": False, "note": t(NOTE)}]
