"""What a data source implements (the ``market.data_source`` extension point), in Python.

Official sources are Python objects with these methods; third-party sources are
reached through the extension point and wrapped in ``ExtensionSource`` so Market's
planner, reads and live sessions treat both the same way. A source only answers:
history returns raw rows of one target, one kind, one base period in [start, end)
as one upstream request gives them, with ``next`` to continue; with ``archive`` it answers
from that one declared file (``History.missing`` when the file is not there). Market's
planner decides which file or API request answers what (``planner``).
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from dataclasses import dataclass, field
from typing import Any

import numpy as np

from .. import bars
from ..common import MarketError


@dataclass
class History:
    columns: bars.Columns
    next: int | None = None
    empty_reason: str | None = None
    # Rows of other kinds the same upstream answer carried (Yahoo events with bars).
    extra: dict[str, list[dict]] = field(default_factory=dict)
    # Spans inside the request the source says hold nothing (a venue's zero-trade filler rows,
    # the gap where a reused code changed hands, a hole in the exchange's own files).
    none: list[tuple[int, int]] = field(default_factory=list)
    # The file asked for (``archive``) is not there: late or never published. Says nothing about the span.
    missing: bool = False


@dataclass(frozen=True)
class Target:
    instrument: dict
    codes: list[dict]
    source_key: str | None = None
    key: str | None = None
    environment: str = "production"

    @property
    def market(self) -> str:
        return self.instrument["venueId"]

    def code_at(self, moment: int | None = None) -> str:
        """The code the listing used at ``moment`` (default: its current code)."""
        if moment is not None:
            for item in self.codes:
                start = int(item["from"]) if item.get("from") else None
                end = int(item["until"]) if item.get("until") else None
                if (start is None or start <= moment) and (end is None or moment < end):
                    return item["code"]
        return self.instrument["symbol"]

    def wire(self) -> dict:
        """The target as the extension point declares it (``SourceTarget``)."""
        codes = [{"code": item["code"], "from": item.get("from"), "until": item.get("until")} for item in self.codes]
        value = {"instrument": self.instrument, "codes": codes, "sourceKey": self.source_key,
                 "environment": self.environment}
        if self.key is not None:
            value["key"] = self.key
        return value


class Source:
    """Base for official sources. Subclasses set ``id``, ``descriptor`` and implement methods."""

    id: str = ""
    descriptor: dict[str, Any] = {}
    secret_settings: tuple[str, ...] = ()

    def __init__(self, market):
        self.market = market
        self.egress = market.egress
        self.settings: dict[str, Any] = {}
        self.key_configured = False

    @property
    def source_ids(self) -> list[str]:
        return list(self.descriptor["sourceIds"])

    def supports(self, method: str) -> bool:
        return method in self.descriptor["supports"]

    async def describe(self, source_id: str) -> dict:
        raise NotImplementedError

    async def search(self, source_id: str, query: str, scope: dict, limit: int) -> list[dict]:
        raise MarketError("unsupported", f"{source_id} has no search", source_id=source_id)

    async def catalog(self, source_id: str, scope: dict, environment: str, cursor: str | None
                      ) -> tuple[list[dict], str | None]:
        raise MarketError("unsupported", f"{source_id} has no catalog", source_id=source_id)

    async def history(self, source_id: str, target: Target, kind: str, timeframe: str | None, price_basis: str,
                      start: int, end: int,
                      archive: tuple[str, int] | None = None) -> History:
        raise MarketError("unsupported", f"{source_id} has no history", source_id=source_id)

    async def bulk(self, source_id: str, kind: str, timeframe: str | None, scope: dict, codes: list[str],
                   start: int, end: int) -> History:
        raise MarketError("unsupported", f"{source_id} has no bulk", source_id=source_id)

    async def bulk_artifact(self, value: dict) -> dict:
        raise MarketError("unsupported", f"{value['sourceId']} has no bulk files", source_id=value["sourceId"])

    def live(self, source_id: str, target: Target, kind: str, timeframe: str | None, price_basis: str = "last",
             depth: int | None = None) -> AsyncIterator[dict]:
        """Live events: subscribed, bar (row, closed), ticker (quote), gap. Market fills gaps by history."""
        raise MarketError("unsupported", f"{source_id} has no live data", source_id=source_id)

    async def configure(self, source_id: str, values: dict) -> dict:
        """Store settings; secrets go to Market's platform secrets, never to disk."""
        secrets = {name: values[name] for name in self.secret_settings if name in values}
        plain = {name: value for name, value in values.items() if name not in self.secret_settings}
        self.settings.update(plain)
        for name, value in secrets.items():
            await self.market.secrets_set(f"source:{source_id}:{name}", value)
        if secrets:
            self.key_configured = all([await self.market.secret_configured(f"source:{source_id}:{name}")
                                       for name in self.secret_settings])
        return {"keyConfigured": self.key_configured}

    async def secret(self, source_id: str, name: str) -> str | None:
        return await self.market.secret_get(f"source:{source_id}:{name}")

    async def load_secrets(self, source_id: str) -> None:
        if self.secret_settings:
            self.key_configured = all([await self.market.secret_configured(f"source:{source_id}:{name}")
                                       for name in self.secret_settings])

    async def research(self, source_id: str, target: Target, kind: str) -> list[dict]:
        """Research rows of one instrument (or, for members, one index); every row carries knownAt."""
        raise MarketError("unsupported", f"{source_id} has no {kind}", source_id=source_id)

    def note(self, source_id: str, kind: str) -> str | None:
        """A limitation of this source's rows in words (e.g. survivorship bias of a member list)."""
        return None

    def indices(self, source_id: str) -> list[dict]:
        """Indices whose member history this source gives: index record, start, end, pointInTime,
        survivorshipBias, note."""
        return []

    async def quotes(self, source_id: str, targets: list[Target], basis: str) -> dict[str, dict]:
        """Current quotes by instrument id: {"quote": Quote} or {"problem": Problem}."""
        raise MarketError("unsupported", f"{source_id} has no quotes", source_id=source_id)

    async def close(self) -> None:
        pass


def empty_history(reason: str) -> History:
    return History(bars.empty(), None, reason)


def coverage(rows: list[dict], *, concurrency: int, rpm: int | None, plan: str | None = None,
             per_day: int | None = None, per_connection: int | None = None) -> dict:
    return {"rows": rows, "concurrency": concurrency, "requestsPerMinute": rpm, "plan": plan,
            "requestsPerDay": per_day, "subscriptionsPerConnection": per_connection}


def row(scope: dict, kind: str, periods: list[tuple] = (), *, live: str = "none",
        live_limit: int | None = None, publish_delay: str | None = None, history_role: str = "bulk",
        delisted: bool = False, point_in_time: bool = False, live_periods: list[str] | None = None) -> dict:
    """A coverage row; each period is (timeframe, depth, request rows[, more period fields: archives, basisRows])."""
    value = {"scope": scope, "kind": kind,
             "periods": [{"timeframe": tf, "depth": depth, **({"requestRows": rows} if rows else {}), **dict(*more)}
                         for tf, depth, rows, *more in periods],
             "live": live, "liveLimit": live_limit, "publishDelay": publish_delay, "historyRole": history_role,
             "delisted": delisted, "pointInTime": point_in_time}
    if live_periods is not None:
        value["livePeriods"] = list(live_periods)
    return value


def archive(unit: str, start: str, publish_delay: str, *, day_zone: str = "+00:00", until: str | None = None,
            bases: tuple[str, ...] = ("last",)) -> dict:
    """One declared file layer of a period (see ``planner``)."""
    return {"unit": unit, "from": start, "until": until, "publishDelay": publish_delay, "dayZone": day_zone,
            "priceBases": list(bases)}


def float_column(values) -> np.ndarray:
    return np.asarray([np.nan if v is None else float(v) for v in values], dtype=np.float64)

