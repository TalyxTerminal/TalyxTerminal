"""Watchlists: a workspace's lists of instruments in sections.

A list holds at most 200 members, each instrument once, as Market's instrument record. Every
change goes out on the workspace stream. A workspace starts with one list of common symbols
(TradingView starts with one too), resolved through Market once; deleting it does not bring
it back.
"""
from __future__ import annotations

import asyncio
import logging
import uuid
from collections.abc import Callable

from talyx_sdk import ContractError

from .marketdata import Market
from .store import Store

LOG = logging.getLogger(__name__)
MAX_MEMBERS = 200
DEFAULT_LIST = ("Watchlist", (
    ("Crypto", ("okx:BTC/USDT", "okx:ETH/USDT", "okx:SOL/USDT")),
    ("Stocks", ("US:AAPL", "US:NVDA", "US:MSFT", "US:TSLA", "US:AMZN")),
))


def checked(sections: list[dict]) -> list[dict]:
    ids = [section["id"] for section in sections]
    if len(set(ids)) != len(ids):
        raise ContractError("invalid_input", "Section ids repeat")
    members = [member["instrument"].get("instrumentId") for section in sections for member in section["members"]]
    if None in members:
        raise ContractError("invalid_input", "A member's instrument has no instrumentId")
    if len(members) > MAX_MEMBERS:
        raise ContractError("invalid_input", f"A watchlist holds at most {MAX_MEMBERS} symbols")
    if len(set(members)) != len(members):
        raise ContractError("invalid_input", "A symbol is in the watchlist more than once")
    return sections


class Watchlists:
    def __init__(self, store: Store, market: Market, publish: Callable[[str, dict], None]):
        self.store, self.market, self.publish = store, market, publish
        self.seeding: dict[str, asyncio.Task] = {}
        self.changed: list[Callable[[str, str], None]] = []  # (workspace, watchlist id): alerts follow members

    def all(self, workspace: str) -> list[dict]:
        return [record["value"] for record in self.store.objects("watchlist", workspace)]

    def get(self, workspace: str, watchlist_id: str) -> dict:
        for value in self.all(workspace):
            if value["id"] == watchlist_id:
                return value
        raise ContractError("not_found", f"No watchlist {watchlist_id} in this workspace")

    def put(self, workspace: str, value: dict) -> dict:
        self.store.put_object("watchlist", {"id": value["id"], "value": value}, workspace)
        self.publish(workspace, {"type": "watchlist", "value": value})
        return value

    def create(self, workspace: str, name: str, sections: list[dict]) -> dict:
        return self.put(workspace, {"id": "wl-" + uuid.uuid4().hex[:12], "name": name, "revision": 1,
                                    "sections": checked(sections)})

    def update(self, workspace: str, watchlist_id: str, revision: int, name: str | None,
               sections: list[dict] | None) -> dict:
        current = self.get(workspace, watchlist_id)
        if revision != current["revision"]:
            raise ContractError("conflict", "The watchlist changed meanwhile", {"revision": current["revision"]})
        value = self.put(workspace, {**current, "revision": revision + 1,
                                     "name": current["name"] if name is None else name,
                                     "sections": current["sections"] if sections is None else checked(sections)})
        for listener in self.changed:
            listener(workspace, watchlist_id)
        return value

    def delete(self, workspace: str, watchlist_id: str) -> None:
        self.get(workspace, watchlist_id)
        self.store.delete_object("watchlist", watchlist_id)
        self.publish(workspace, {"type": "watchlist_removed", "id": watchlist_id})
        for listener in self.changed:
            listener(workspace, watchlist_id)

    # ── the first list of a new workspace ──

    def seed(self, workspace: str) -> None:
        """Called when the workspace stream opens; the list follows on the stream when ready."""
        if workspace in self.seeding or self.store.objects("seeded", workspace):
            return
        self.seeding[workspace] = asyncio.ensure_future(self.make_default(workspace))

    async def make_default(self, workspace: str) -> None:
        name, groups = DEFAULT_LIST
        sections = []
        for index, (title, texts) in enumerate(groups):
            members = []
            for match in await self.market.matches(texts):
                if match["status"] in ("found", "redirect"):
                    members.append({"instrument": match["instrument"]})
                else:
                    LOG.warning("Default watchlist leaves out %s: %s", match["symbol"], match["status"])
            sections.append({"id": f"s{index + 1}", "name": title, "members": members})
        self.create(workspace, name, sections)
        self.store.put_object("seeded", {"id": workspace}, workspace)
        del self.seeding[workspace]

    async def close(self) -> None:
        for task in self.seeding.values():
            task.cancel()
