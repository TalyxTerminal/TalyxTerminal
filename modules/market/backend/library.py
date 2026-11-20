"""The local data library: what Market has stored, per series or per dataset.

A dataset is one market or venue × data kind × period (or one imported dataset).
Used by ``read_coverage`` and the Market panel's private route, so both
give the same numbers.
"""

from __future__ import annotations

from functools import lru_cache

import numpy as np

from vocabulary.identity import timeframe_nanoseconds

from . import calendars
from .common import DAY_NS, iso_of_ns, now_ns
from .data import kind_of
from .store import Spans


@lru_cache(maxsize=65536)
def time_text(calendar_id: str, timeframe: str | None, value: int | None) -> str | None:
    if value is None:
        return None
    step = timeframe_nanoseconds(timeframe) if timeframe else None
    if calendars.canonical(calendar_id) != calendars.CONTINUOUS and (step is None or step >= DAY_NS):
        day = calendars.trading_day(calendar_id, np.asarray([value], dtype=np.int64))[0]
        return str(np.datetime64(day, "D"))
    return iso_of_ns(int(value))


def series_rows(market, value: dict) -> list[dict]:
    """Every stored series as a row (``_record`` is its identity record, for callers that show more of it)."""
    now = now_ns()
    rows = []
    latest: dict[tuple[str, str], int] = {}   # the newest closed bar's open, once per calendar and period
    want_market = (value.get("market") or "").upper()
    want_venue = (value.get("venue") or "").lower()
    query = (value.get("query") or "").upper()
    for key in market.store.keys(environment=value.get("environment", "production")):
        pointer = market.store.pointer(key)
        if not pointer or not pointer.get("rows"):
            continue
        record = market.identity.record(key.instrument_id) or {}
        venue = record.get("venueId", "?")
        if want_market and venue.upper() != want_market:
            continue
        if want_venue and venue.lower() != want_venue:
            continue
        kind = f"import:{key.import_id}" if key.import_id else key.capability
        if value.get("timeframe") and value["timeframe"] != key.timeframe:
            continue
        if value.get("kind") and value["kind"] not in (kind, key.capability):
            continue
        symbol = record.get("symbol", key.instrument_id)
        if query and query not in symbol.upper() and query not in (record.get("name") or "").upper():
            continue
        calendar = record.get("calendarId", "24_7")
        step = (timeframe_nanoseconds(key.timeframe) or DAY_NS) if key.timeframe else 1
        first, last = int(pointer["first"]), int(pointer["last"])
        gaps = Spans.missing(pointer["spans"], first, last + step)
        if record.get("active") is False or record.get("delistedAt") or record.get("retiredAt"):
            status = "delisted"
        elif key.import_id:
            status = "current"
        else:
            period = (calendar, key.timeframe or "1d", pointer["source"])
            if record and period not in latest:
                # The newest bar published by the series' source (its publish cutoff), once per calendar, period, source.
                delay = market.data.publish_delay(pointer["source"], record, kind_of(key.capability), key.timeframe)
                latest[period] = market.data.last_closed_open(calendar, period[1], now - delay)
            status = "behind" if record and last < latest[period] else "current"
        if value.get("status") and status != value["status"]:
            continue
        rows.append({
            "instrumentId": key.instrument_id, "symbol": symbol, "venue": venue, "kind": kind,
            "timeframe": key.timeframe, "rows": int(pointer["rows"]), "first": time_text(calendar, key.timeframe, first),
            "last": time_text(calendar, key.timeframe, last), "gaps": len(gaps), "bytes": int(pointer["bytes"]),
            "source": pointer.get("source"), "status": status, "_first": first, "_last": last, "_record": record,
        })
    rows.sort(key=lambda r: (r["venue"], r["kind"], r["timeframe"] or "", r["symbol"]))
    return rows


def dataset_rows(series: list[dict]) -> list[dict]:
    groups: dict[tuple, dict] = {}
    for row in series:
        key = (row["venue"], row["kind"], row["timeframe"])
        group = groups.get(key)
        if group is None:
            group = groups[key] = {"dataset": row["venue"], "kind": row["kind"], "timeframe": row["timeframe"],
                                   "instruments": 0, "rows": 0, "first": None, "last": None, "fillableGaps": 0,
                                   "bytes": 0, "sources": [], "statuses": {}, "_first": None, "_last": None}
        group["instruments"] += 1
        group["rows"] += row["rows"]
        group["bytes"] += row["bytes"]
        group["fillableGaps"] += row["gaps"]
        if row["source"] and row["source"] not in group["sources"]:
            group["sources"].append(row["source"])
        group["statuses"][row["status"]] = group["statuses"].get(row["status"], 0) + 1
        if group["_first"] is None or row["_first"] < group["_first"]:
            group["_first"], group["first"] = row["_first"], row["first"]
        if group["_last"] is None or row["_last"] > group["_last"]:
            group["_last"], group["last"] = row["_last"], row["last"]
    return list(groups.values())


def library(market, value: dict) -> list[dict]:
    series = series_rows(market, value)
    return series if value.get("group") == "instrument" else dataset_rows(series)
