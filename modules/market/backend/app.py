"""The Market application: one owner of identities, storage, sources and serving."""

from __future__ import annotations

import asyncio
import dataclasses
from pathlib import Path

from talyx_sdk.i18n import t

from vocabulary.instruments import MarketInstrument

from .actions import Actions
from .admin import SourceAdmin
from .common import MarketError
from .data import Data
from .display import place_name
from .exports import Exports
from .handoff import Handoff
from .identity import IdentityStore
from .imports import WriteData
from .instruments import Instruments
from .jobs import Jobs
from .live import Sessions
from .network import Egress
from .panel import Panel
from .professional import Professional
from .public_actions import PublicActions
from .quotes import Quotes
from .research import Research
from .routing import Registry, in_worker
from .store import DocumentStore, SeriesStore
from .terms import Terms


class Market:
    def __init__(self, ctx, root: Path, *, egress: Egress | None = None, sources=None):
        self.ctx, self.root = ctx, root
        root.mkdir(parents=True, exist_ok=True)
        self.egress = egress or Egress()
        self.identity = IdentityStore(root / "identity.sqlite")
        self.store = SeriesStore(root)
        self.actions = Actions(DocumentStore(root, "actions"), self.store)
        self.registry = Registry(root)
        self.data = Data(self)
        self.instruments = Instruments(self)
        self.handoff = Handoff(self)
        self.sessions = Sessions(self)
        self.quotes = Quotes(self)
        self.research = Research(self)
        self.jobs = Jobs(self)
        self.workspace_watch = None
        self.exports = Exports(self)
        self.admin = SourceAdmin(self)
        self.terms = Terms(self)
        self.professional = Professional(self)
        self.write_data = WriteData(self)
        self.panel = Panel(self)
        self.public = PublicActions(self)
        self._tasks: set[asyncio.Task] = set()
        self._secrets: dict[str, str] = {}
        for source in sources if sources is not None else _official(self):
            self.registry.add(source)

    # ── lifecycle ───────────────────────────────────────────────────────────

    async def folder(self, text: str | None, workspace_id: str | None) -> Path:
        path = Path(text or ".")
        if path.is_absolute():
            return path.resolve()
        if path.anchor or not workspace_id:
            raise MarketError("invalid_input", t("Give an absolute path or a path relative to a bound workspace"))
        base = Path(await self.ctx.platform.workspace_folder(workspace_id)).resolve()
        target = (base / path).resolve()
        if not target.is_relative_to(base):
            raise MarketError("invalid_input", t("Relative path {text} leaves the workspace folder", text=text))
        return target

    async def start(self, *, load_catalogs: bool = True) -> None:
        seen = set()
        for source_id, entry in self.registry.entries.items():
            if id(entry.source) not in seen:
                seen.add(id(entry.source))
            await entry.source.load_secrets(source_id)
        await asyncio.gather(*(self.registry.refresh(sid) for sid in self.registry.entries))
        if load_catalogs:
            self.instruments.start_loading()
            self.track_task(asyncio.ensure_future(self._after_loading()))
        self.track_task(asyncio.ensure_future(self._housekeeping()))

    async def _after_loading(self) -> None:
        loads = [task for task in self.instruments._loads.values() if not task.done()]
        await asyncio.gather(*loads, return_exceptions=True)
        await self.load_code_history()

    async def _housekeeping(self) -> None:
        while True:
            await self.handoff.sweep()
            await asyncio.sleep(3600)

    async def load_code_history(self, *, force: bool = False) -> int:
        """Apply the renames sources know (Alpaca's name changes) to the listings' code periods, newest
        first; at most once a day per source unless forced (a key was just configured). Returns how many
        renames were applied."""
        from .common import now_ns
        states = {(c["venue"], c["family"]): c for c in await in_worker(self.identity.catalogs)}
        applied = 0
        for source_id, entry in list(self.registry.entries.items()):
            if not hasattr(entry.source, "code_history") or not self.registry.usable(entry):
                continue
            state = states.get((source_id, "code_history"))
            if not force and state and state["state"] == "ready" and state["loadedAt"] and \
                    now_ns() - state["loadedAt"] < 86_400 * 10**9:
                continue
            try:
                changes = await self.registry.call(source_id, "code_history")
            except MarketError as exc:
                await in_worker(self.identity.catalog_state, source_id, "code_history", "failed", exc.problem())
                continue
            for change in sorted(changes, key=lambda item: -int(item["at"])):
                result = await in_worker(self.identity.apply_rename, change["market"], change["old"],
                                         change["new"], int(change["at"]), source_id)
                if result["state"] == "applied":
                    applied += 1
                    if result.get("merged"):
                        await self.merge_listings(result["instrumentId"], result["merged"])
                    self.instrument_changed(result["instrumentId"])
            await in_worker(self.identity.catalog_state, source_id, "code_history", "ready")
        return applied

    async def close(self) -> None:
        if self.workspace_watch is not None:
            changes, watcher = self.workspace_watch
            watcher.cancel()
            await asyncio.gather(watcher, return_exceptions=True)  # failures are reported by its done callback
            await changes.close()
            self.workspace_watch = None
        await self.jobs.close()
        await self.sessions.close()
        await self.quotes.close()
        for task in list(self._tasks):
            task.cancel()
        await asyncio.gather(*self._tasks, return_exceptions=True)
        closed = set()
        for entry in self.registry.entries.values():
            if id(entry.source) not in closed:
                closed.add(id(entry.source))
                await entry.source.close()
        await self.egress.close()

    def track_task(self, task: asyncio.Task) -> asyncio.Task:
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)
        return task

    def extensions_changed(self, items: list[dict]) -> None:
        """Third-party data sources come and go with their modules: ``items`` are the enabled modules'
        implementations, ``{moduleId, id, descriptor}``."""
        from .sources.extension import ExtensionSource
        live = set()
        for item in items:
            if item["moduleId"] == "market":
                continue
            source = ExtensionSource(self, item)
            for source_id in item["descriptor"]["sourceIds"]:
                live.add(source_id)
                if source_id not in self.registry.entries:
                    self.registry.add(source, source_ids=[source_id])
                    self.track_task(asyncio.ensure_future(self.registry.refresh(source_id)))
        for source_id, entry in list(self.registry.entries.items()):
            if entry.source.__class__.__name__ == "ExtensionSource" and source_id not in live:
                del self.registry.entries[source_id]

    async def after_catalog(self, source_id: str, venue: str, family: str) -> None:
        """A source may check its fresh directory for listings that are one (A-share code changes)."""
        hook = getattr(self.registry.entry(source_id).source, "after_catalog", None)
        if hook is not None:
            await hook(source_id, venue, family)

    async def merge_listings(self, survivor_id: str, merged_id: str, *, market: str | None = None,
                             periods: list[tuple[str, int, int | None]] | None = None) -> None:
        """``merged_id`` is the same listing as ``survivor_id``: its data moves over (one copy of each kind
        stays: the current source's, else the longer), its codes and keys follow, and the old id redirects."""
        if self.identity.survivor(merged_id) == survivor_id:
            return
        await in_worker(self._move_data, survivor_id, merged_id)
        await in_worker(self.identity.merge, survivor_id, merged_id)
        if periods:
            await in_worker(self.identity.set_code_periods, survivor_id, market, periods)
        self.instrument_changed(survivor_id)

    def _move_data(self, survivor_id: str, merged_id: str) -> None:
        record = self.identity.record(survivor_id)
        for key in self.store.keys(merged_id, environment=None):
            target = dataclasses.replace(key, instrument_id=survivor_id)
            theirs, mine = self.store.pointer(key), self.store.pointer(target)
            if mine is None or self._better(record, key, theirs, mine):
                self.store.move(key, target)
            else:
                self.store.delete(key)
        merged_actions, own_actions = self.actions.documents.read(merged_id), self.actions.documents.read(survivor_id)
        if merged_actions is not None:
            if own_actions is None or len(merged_actions["rows"]) > len(own_actions["rows"]):
                self.actions.documents.write(survivor_id, merged_actions)
            self.actions.documents.delete(merged_id)
        for documents in self.research.documents.values():
            theirs = documents.read(merged_id)
            if theirs is None:
                continue
            mine = documents.read(survivor_id)
            if mine is None or len(theirs.get("rows", [])) > len(mine.get("rows", [])):
                documents.write(survivor_id, theirs)
            documents.delete(merged_id)

    def _better(self, record: dict | None, key, theirs: dict, mine: dict) -> bool:
        """True when the merged listing's copy of a series should be the one kept."""
        preferred = None
        if record is not None and key.capability == "ohlcv":
            ranked = self.registry.rank(record, "bars", key.timeframe)
            preferred = ranked[0][0] if ranked else None
        def score(pointer):
            first, last = pointer.get("first"), pointer.get("last")
            span = (int(last) - int(first)) if first is not None and last is not None else 0
            return (pointer.get("source") == preferred, span, pointer.get("rows") or 0)
        return score(theirs) > score(mine)

    # ── secrets (Market's own platform secrets for official sources) ────────

    async def secret_get(self, key: str) -> str | None:
        if self.ctx is None:
            return self._secrets.get(key)
        return await self.ctx.platform.secret(key)

    async def secrets_set(self, key: str, value: str | None) -> None:
        if self.ctx is None:
            if value is None:
                self._secrets.pop(key, None)
            else:
                self._secrets[key] = value
            return
        await self.ctx.platform.set_secret(key, value)

    async def secret_configured(self, key: str) -> bool:
        if self.ctx is None:
            return key in self._secrets
        return await self.ctx.platform.secret_configured(key)

    # ── identity helpers ────────────────────────────────────────────────────

    def instrument_record(self, value: dict) -> dict:
        """The current local record of an instrument handed in by a caller."""
        record = self.identity.record(value["instrumentId"])
        if record is not None:
            return record
        if set(value) == {"instrumentId"}:
            raise MarketError("not_found", t("Unknown instrument ID {instrumentId}", instrumentId=value['instrumentId']))
        outcome = self.instruments.accept_record(value)
        if outcome["kind"] == "missing":
            raise MarketError("not_found", t("Market does not know {symbol} ({instrumentId})", symbol=value['symbol'], instrumentId=value['instrumentId']),
                              details={"candidates": outcome["candidates"]})
        return self.identity.record(outcome["id"])

    def instrument(self, value: dict) -> MarketInstrument:
        return MarketInstrument.from_wire(self.instrument_record(value))

    def has_local(self, instrument_id: str, environment: str = "production") -> bool:
        return bool(self.store.keys(instrument_id, environment=environment))

    def extra_series(self, record: dict) -> list[dict]:
        found = []
        for key in self.store.keys(record["instrumentId"]):
            if key.import_id:
                from vocabulary.instruments import MarketSeries
                series = MarketSeries(instrumentId=record["instrumentId"], capability=key.capability,
                                      timeframe=key.timeframe, priceBasis=key.price_basis,
                                      sessionScope=key.session_scope,
                                      adjustment="raw" if record["adjustmentEligible"] else "not_applicable",
                                      importId=key.import_id)
                found.append({"series": series.to_dict(), "label": t("Imported {import_id}", import_id=key.import_id)})
        return found

    def instrument_changed(self, instrument_id: str) -> None:
        pass

    def next_revision(self, instrument_id: str) -> int:
        values = [self.actions.read(instrument_id)["revision"]]
        for key in self.store.keys(instrument_id, environment=None):
            pointer = self.store.pointer(key)
            if pointer:
                values.append(pointer["revision"])
        return max(values) + 1

    def source_ref(self, source_id: str | None) -> dict | None:
        if source_id is None:
            return None
        if source_id.startswith("import:"):
            return {"sourceId": source_id, "name": f"Imported {source_id[7:]}"}
        entry = self.registry.entries.get(source_id)
        name = entry.descriptor["name"] if entry else source_id
        if entry and entry.source.id == "ccxt":
            name = place_name(source_id)
        return {"sourceId": source_id, "name": name}


def _official(market: Market) -> list:
    from .sources.alpaca import AlpacaSource
    from .sources.baostock import BaostockSource
    from .sources.coingecko import CoinGecko
    from .sources.crypto import CryptoSource
    from .sources.databento import DatabentoSource
    from .sources.gateways import FutuSource, IbSource
    from .sources.hkex import HkexListings
    from .sources.indices import Sp500History
    from .sources.listings import NasdaqListings
    from .sources.sec import SecFilings
    from .sources.sharadar import SharadarSource
    from .sources.tushare import TushareSource
    from .sources.yahoo import YahooSource
    return [YahooSource(market), CryptoSource(market), NasdaqListings(market), Sp500History(market), SecFilings(market),
            CoinGecko(market), BaostockSource(market), TushareSource(market), HkexListings(market),
            AlpacaSource(market), SharadarSource(market), DatabentoSource(market), IbSource(market),
            FutuSource(market)]
