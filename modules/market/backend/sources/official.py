"""Official sources exposed through ``market.data_source`` exactly like third-party ones.

Market calls its own sources directly (in process); this adapter publishes the
same objects on the extension point, converting columns to vocabulary text.
"""

from __future__ import annotations

import numpy as np
from talyx_sdk.i18n import t

from .. import bars
from ..common import MarketError, time_range
from .base import Target


def to_wire_event(event: dict) -> dict:
    """Market's internal live event → the extension point's SourceLiveItem."""
    kind = event["type"]
    if kind == "bar":
        row = event["row"]
        bar = {"time": str(row["time"]), "closeTime": str(row["close_time"])}
        for name in ("open", "high", "low", "close", "volume"):
            bar[name] = bars.decimal_text(np.asarray([row[name]], dtype=float))[0]
        return {"type": "bar", "bar": bar, "closed": event["closed"]}
    if kind == "ticker":
        return {"type": "quote", "quote": event["quote"]}
    if kind == "subscribed":
        return {"type": "status", "state": "subscribed", "problem": None}
    if kind == "gap":
        return {"type": "status", "state": "reconnecting", "problem": event.get("problem")}
    return event


def from_wire_event(item: dict) -> dict | None:
    """The extension point's SourceLiveItem → Market's internal live event."""
    kind = item["type"]
    if kind == "bar":
        bar = item["bar"]
        row = {"time": int(bar["time"]), "close_time": int(bar.get("closeTime") or bar["time"])}
        for name in ("open", "high", "low", "close", "volume"):
            value = bar.get(name)
            row[name] = float("nan") if value in (None, "") else float(value)
        if not isinstance(item["closed"], bool):
            raise MarketError("invalid_output", t("A bar event requires a boolean closed flag"))
        return {"type": "bar", "row": row, "closed": item["closed"]}
    if kind == "quote":
        return {"type": "ticker", "quote": item["quote"]}
    if kind == "status":
        if item["state"] == "subscribed":
            return {"type": "subscribed", "mode": "live"}
        if item["state"] in ("reconnecting", "degraded"):
            return {"type": "gap", "problem": item.get("problem")}
        return None
    return {"type": kind, **{k: v for k, v in item.items() if k != "type"}}


def handlers(source) -> dict:
    async def describe(value, _ctx):
        return await source.describe(value["sourceId"])

    async def search(value, _ctx):
        return {"candidates": await source.search(value["sourceId"], value["query"], value.get("scope", {}),
                                                  value["limit"])}

    async def catalog(value, _ctx):
        candidates, cursor = await source.catalog(value["sourceId"], value["scope"],
                                                  value.get("environment", "production"), value["cursor"])
        return {"candidates": candidates, "nextCursor": cursor}

    async def history(value, _ctx):
        target = value["target"]
        result = await source.history(value["sourceId"], Target(target["instrument"], target["codes"],
                                                                target.get("sourceKey"), target.get("key"),
                                                                target.get("environment", "production")),
                                      value["kind"], value["timeframe"], value.get("priceBasis", "last"),
                                      int(value["start"]), int(value["end"]),
                                      (value["archive"]["unit"], int(value["archive"]["start"]))
                                      if "archive" in value else None)
        return {"columns": bars.to_wire(result.columns), "next": None if result.next is None else str(result.next),
                "emptyReason": "missing" if result.missing else result.empty_reason,
                "none": [time_range(a, b) for a, b in result.none]}

    async def bulk(value, _ctx):
        return await source.bulk_artifact(value)

    async def configure(value, _ctx):
        return await source.configure(value["sourceId"], value["values"])

    async def live(params, _subscription):
        target = params["target"]
        events = source.live(params["sourceId"], Target(target["instrument"], target["codes"], target.get("sourceKey"),
                                                        target.get("key"), target.get("environment", "production")),
                             params["kind"], params.get("timeframe"), params.get("priceBasis", "last"),
                             params.get("depth"))
        async for event in events:
            yield to_wire_event(event)

    actions = {"describe": describe, "search": search, "catalog": catalog, "history": history, "bulk": bulk,
               "configure": configure}
    return {"actions": actions, "streams": {"live": live} if "live" in source.descriptor["supports"] else {}}
