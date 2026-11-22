"""Bars for code that asks for its own symbols (`q.data(symbols, timeframe, start, end)`): the same frozen research table a run's data settings give."""
from __future__ import annotations

import asyncio
import json
import shutil
from uuid import uuid4

from talyx_sdk import ContractError
from talyx_sdk.i18n import t

from . import research_data
from .research_lists import LIST
from .work_records import digest

KEPT = 20


class ResearchBars:
    def __init__(self, ctx, records):
        self.ctx, self.records = ctx, records
        self.root = records.root / "research-bars"
        self.locks = {}

    def list_named(self, name):
        found = [item for item in self.records.list(LIST) if not item.get("deleted") and item["name"].strip().casefold() == name.strip().casefold()]
        if not found:
            raise ContractError("not_found", t("There is no research list named {name}.", name=name))
        return [{"instrument": member} for member in found[0]["members"]]

    async def export(self, args, progress=None):
        from .public_settings import store_data
        given = [key for key in ("symbols", "index", "list") if args.get(key)]
        if len(given) != 1:
            raise ContractError("invalid_input", t("Choose one of symbols, index or list."))
        data = await store_data(self.ctx, {**args,"adjustment":args.get("adjustment","split_dividend")}, {})
        if "list" in data:
            data["symbols"] = self.list_named(data.pop("list"))
        if data.get("until") == "latest":
            from .object_data import resolve_until
            data = await resolve_until(self.ctx,data)
        directory = await self.frozen(data, uuid4().hex, progress)
        # The bars and their instrument information both go by file (FileRef, kept 24 hours): an index's information alone runs to tens of MB,
        # past what a call returns inline
        bars = await self.ctx.hand_over(directory / "bars.parquet", "research-bars.parquet")
        info = await self.ctx.hand_over(directory / "info.json", "research-bars-info.json")
        facts = json.loads(await asyncio.to_thread((directory / "info.json").read_text, encoding="utf-8"))
        return {"bars": bars, "info": info, "coverage": {"names": len(facts["instruments"]), "start": facts["since"], "end": facts["until"]}}

    async def frozen(self, data, identity, progress):
        """The folder of these bars frozen: the one frozen before for the same request while Market has every series at the revision frozen (one
        batch of revision reads; the same rule as a factor's data), otherwise frozen again. The same quarter of the S&P 500 asked twice went
        through Market and arranged its 483 names each time."""
        key = digest({"data": data})[:24]
        folder = self.root / "frozen" / key
        async with self.locks.setdefault(key, asyncio.Lock()):
            info_path = folder / "info.json"
            if info_path.is_file():
                info = json.loads(await asyncio.to_thread(info_path.read_text, encoding="utf-8"))
                if await research_data.unchanged(self.ctx, info):
                    await asyncio.to_thread(info_path.touch)
                    return folder
            staging = self.root / "frozen" / f".preparing-{identity}"
            try:
                staging.mkdir(parents=True)
                prepared = await research_data.prepare(self.ctx, data, "research-bars:" + identity, staging, progress=progress)
                await asyncio.to_thread((staging / "info.json").write_text, json.dumps(prepared["research"]["info"]), encoding="utf-8")
                if folder.exists():
                    await asyncio.to_thread(shutil.rmtree, folder)
                await asyncio.to_thread(staging.rename, folder)
            finally:
                if staging.exists():
                    await asyncio.to_thread(shutil.rmtree, staging)
        await asyncio.to_thread(self.prune, {key, *(name for name, lock in self.locks.items() if lock.locked())})
        return folder

    def prune(self, keep):
        """The last KEPT requests' freezes stay; what a read handed over lives on in Quant's hand-over folder."""
        folders = sorted((item for item in (self.root / "frozen").iterdir() if item.is_dir() and not item.name.startswith(".")
                          and item.name not in keep and (item / "info.json").is_file()),
                         key=lambda item: (item / "info.json").stat().st_mtime, reverse=True)
        for folder in folders[KEPT - 1:]:
            shutil.rmtree(folder)

    async def close(self):
        """What a read handed over lives on in the hand-over folder for 24 hours; nothing to let go."""
