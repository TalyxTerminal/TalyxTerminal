"""Research lists: named sets of exact Market instruments that every workspace can use in its data settings."""
from __future__ import annotations

import time
from uuid import uuid4

from talyx_sdk import ContractError

from . import market_inputs as market
from .object_data import resolve_text

LIST = "qlist"


def clean_name(value):
    if not isinstance(value, str) or not value.strip() or len(value.strip()) > 120:
        raise ContractError("invalid_input", "Choose a list name of 1 to 120 characters.")
    return value.strip()


class ResearchLists:
    def __init__(self, ctx, records):
        self.ctx, self.records = ctx, records
        self.used_by = None  # set by the services: list id -> the names of the objects whose data settings select it

    def read(self, identity):
        record = self.records.find(LIST, identity)
        if record is None or record.get("deleted"):
            raise ContractError("not_found", "This research list does not exist.")
        return record

    def summary(self, record):
        return {"id": record["id"], "name": record["name"], "count": len(record["members"]), "createdAt": record["createdAt"], "updatedAt": record["updatedAt"]}

    def detail(self, record):
        return {**self.summary(record), "members": [{"instrument": member, "displaySymbol": member["displaySymbol"], "venue": member["venueId"]} for member in record["members"]]}

    def list(self):
        return {"items": [self.summary(item) for item in sorted(self.records.list(LIST), key=lambda item: item["name"].casefold()) if not item.get("deleted")]}

    def members(self, identity):
        """The list's instruments in the data-settings shape."""
        return [{"instrument": member} for member in self.read(identity)["members"]]

    async def exact(self, symbols):
        """Each member as Market's exact instrument: copied from a Market search, or written in Market's standard symbol syntax."""
        members, seen = [], set()
        for item in symbols:
            instrument = item.get("instrument") if set(item) == {"instrument"} else (await resolve_text(self.ctx, item["symbol"]) if set(item) == {"symbol"} else None)
            if not isinstance(instrument, dict) or not instrument.get("instrumentId"):
                raise ContractError("invalid_input", "Each member is {\"instrument\": <the Market instrument>} copied from a Market search, or {\"symbol\": \"US:AAPL\"}.")
            instrument = await market.current(self.ctx, instrument)
            if instrument["instrumentId"] not in seen:
                seen.add(instrument["instrumentId"])
                members.append(instrument)
        return members

    def publish(self, record):
        self.ctx.publish("research_lists_changed", {"listId": record["id"]})

    async def create(self, args):
        now = int(time.time() * 1000)
        record = self.records.put(LIST, {"id": str(uuid4()), "name": clean_name(args["name"]), "members": await self.exact(args.get("members", [])), "createdAt": now})
        self.publish(record)
        return self.detail(record)

    async def update(self, identity, args):
        record = self.read(identity)
        changes = {}
        if "name" in args:
            changes["name"] = clean_name(args["name"])
        if "members" in args:
            changes["members"] = await self.exact(args["members"])
        record = self.records.put(LIST, {**record, **changes})
        self.publish(record)
        return self.detail(record)

    def deletable(self, identity):
        """The list when it may go: not while an object's data settings select it."""
        record = self.read(identity)
        users = self.used_by(identity)
        if users:
            raise ContractError("conflict", f"{record['name']} is selected in the data settings of {', '.join(users)}; choose other data there first.", {"objects": users})
        return record

    def delete(self, identity):
        record = self.deletable(identity)
        self.records.put(LIST, {**record, "deleted": True})
        self.publish(record)
        return {"listId": identity, "deleted": True}
