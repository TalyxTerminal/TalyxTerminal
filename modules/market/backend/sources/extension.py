"""A third-party module's data source, reached through ``market.data_source``."""

from __future__ import annotations

from collections.abc import AsyncIterator

from talyx_sdk import ContractError

from .. import bars
from ..common import SOURCE_CODES, MarketError
from .base import History, Source, Target


class ExtensionSource(Source):
    def __init__(self, market, item: dict):
        super().__init__(market)
        self.module_id, self.implementation = item["moduleId"], item["id"]
        self.id = f"{self.module_id}:{self.implementation}"
        self.descriptor = item["descriptor"]

    async def _call(self, source_id: str, method: str, value: dict):
        try:
            return await self.market.ctx.call_extension("data_source", self.module_id, self.implementation, method,
                                                        value)
        except ContractError as exc:
            code = exc.code if exc.code in SOURCE_CODES else "source_unavailable"
            details = exc.details if isinstance(exc.details, dict) else {}
            retry = details.get("retryAt")
            raise MarketError(code, exc.message, source_id=source_id, status=details.get("status"),
                              retry_at=int(retry) if retry else None) from exc

    async def describe(self, source_id: str) -> dict:
        return await self._call(source_id, "describe", {"sourceId": source_id})

    async def search(self, source_id: str, query: str, scope: dict, limit: int) -> list[dict]:
        result = await self._call(source_id, "search", {"sourceId": source_id, "query": query, "scope": scope,
                                                        "limit": limit})
        return result["candidates"]

    async def catalog(self, source_id: str, scope: dict, environment: str, cursor: str | None):
        result = await self._call(source_id, "catalog", {"sourceId": source_id, "scope": scope,
                                                         "environment": environment, "cursor": cursor})
        return result["candidates"], result["nextCursor"]

    async def history(self, source_id: str, target: Target, kind: str, timeframe: str | None, price_basis: str,
                      start: int, end: int,
                      archive: tuple[str, int] | None = None) -> History:
        value = {"sourceId": source_id, "target": target.wire(), "kind": kind, "timeframe": timeframe,
                 "start": str(start), "end": str(end)}
        if price_basis != "last":
            value["priceBasis"] = price_basis
        if archive is not None:
            value["archive"] = {"unit": archive[0], "start": str(archive[1])}
        result = await self._call(source_id, "history", value)
        columns = bars.from_wire(result["columns"]) if result["columns"] else bars.empty()
        missing = result["emptyReason"] == "missing"
        return History(columns, None if result["next"] is None else int(result["next"]),
                       None if missing else result["emptyReason"],
                       none=[(int(item["start"]), int(item["end"])) for item in result.get("none", [])],
                       missing=missing)

    async def configure(self, source_id: str, values: dict) -> dict:
        result = await self._call(source_id, "configure", {"sourceId": source_id, "values": values})
        self.key_configured = result["keyConfigured"]
        return result

    async def live(self, source_id: str, target: Target, kind: str, timeframe: str | None, price_basis: str = "last",
                   depth: int | None = None) -> AsyncIterator[dict]:
        from .official import from_wire_event

        params = {"sourceId": source_id, "target": target.wire(), "kind": kind, "timeframe": timeframe,
                  "priceBasis": price_basis}
        if depth:
            params["depth"] = depth
        subscription = await self.market.ctx.subscribe_extension("data_source", self.module_id, self.implementation,
                                                                 "live", params)
        try:
            async for item in subscription:
                event = from_wire_event(item)
                if event is not None:
                    yield event
        finally:
            await subscription.close()
