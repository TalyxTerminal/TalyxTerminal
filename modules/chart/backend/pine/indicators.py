"""Indicator definitions (built-in and the user's) and running them on feeds.

There is one kind of indicator: a Pine (PyneCore) script. Built-ins ship in ``builtin/``; a
user indicator is a ``.py`` file or a folder with ``main.py`` in Chart's indicator directory.
A definition's identity is the digest of its code. Indicators run in the Pine worker on a
feed's bars; ``PineLink`` hands one feed's bars to the engine once, however many indicators
and consumers read it.
"""
from __future__ import annotations

import ast
import asyncio
import hashlib
import shutil
from collections.abc import Callable
from pathlib import Path

from talyx_sdk import ContractError

from ..derived import PRICE_SUFFIXES, PointsFeed, derived_of
from ..feeds import Feed, Hub, ProjectedFeed
from ..marketdata import market_timeframe
from . import visible
from .engine import Definition, Instance, PineEngine
from .history import FIELDS as PINE_FIELDS

BUILTIN = Path(__file__).parents[1] / "builtin"
INTRABARS = 200_000  # bars a request.security feed holds at most: TradingView's lower-timeframe limit
ALERT_CONDITION = "alertcondition:"  # the worker's row key prefix for an alertcondition's value


def digest_of(paths: list[Path], root: Path) -> str:
    hasher = hashlib.sha256()
    for path in sorted(paths):
        hasher.update(path.relative_to(root).as_posix().encode() + b"\0" + path.read_bytes() + b"\0")
    return hasher.hexdigest()


def constants_of(path: Path) -> dict:
    """The script's ``DESCRIPTION`` and ``CATEGORY`` (TradingView-style: Trend, Oscillators, ...)
    and the titles of its ``alertcondition`` calls."""
    tree = ast.parse(path.read_text(encoding="utf-8"))
    found = {"description": "", "category": "Other", "alertConditions": []}
    for node in tree.body:
        name = getattr(node.targets[0], "id", None) if isinstance(node, ast.Assign) else None
        if name in ("DESCRIPTION", "CATEGORY") and isinstance(node.value, ast.Constant) and isinstance(node.value.value, str):
            found[name.lower()] = node.value.value
    for node in ast.walk(tree):
        if isinstance(node, ast.Call) and getattr(node.func, "id", getattr(node.func, "attr", None)) == "alertcondition":
            title = next((item.value for item in node.keywords if item.arg == "title"),
                         node.args[1] if len(node.args) > 1 else None)
            found["alertConditions"].append(title.value if isinstance(title, ast.Constant) else "Alert")
    return found


class Catalog:
    def __init__(self, engine: PineEngine, user_dir: Path):
        self.engine, self.user_dir = engine, user_dir
        user_dir.mkdir(parents=True, exist_ok=True)  # Chart declares it to Developer: it exists from the start
        self.described: dict[tuple[str, str, str], dict] = {}

    def definition(self, ref: dict) -> Definition:
        if ref["source"] == "builtin":
            path = BUILTIN / f"{ref['id']}.py"
            if not path.is_file() or path.parent != BUILTIN:
                raise ContractError("not_found", f"No built-in indicator {ref['id']}")
            return Definition(digest=digest_of([path], BUILTIN), path=path)
        path = (self.user_dir / ref["id"]).resolve()
        if not path.is_relative_to(self.user_dir.resolve()):
            raise ContractError("not_found", f"No indicator {ref['id']}")
        if path.is_dir():
            files = [item for item in path.rglob("*.py") if "__pycache__" not in item.parts]
            return Definition(digest=digest_of(files, path), path=path / "main.py", root=path)
        if path.suffix == ".py" and path.is_file():
            return Definition(digest=digest_of([path], path.parent), path=path)
        raise ContractError("not_found", f"No indicator {ref['id']}")

    @staticmethod
    def reads_view(definition: Definition) -> bool:
        """Whether the code reads the chart's visible range: it runs on charts only."""
        return visible.uses(definition.digest, definition.path, definition.root)

    def refs(self) -> list[dict]:
        builtin = [{"source": "builtin", "id": path.stem} for path in sorted(BUILTIN.glob("*.py"))]
        user = [{"source": "user", "id": path.name} for path in sorted(self.user_dir.iterdir())
                if path.suffix == ".py" or path.is_dir() and (path / "main.py").is_file()]
        return builtin + user

    async def describe(self, ref: dict) -> dict:
        definition = self.definition(ref)
        key = (ref["source"], ref["id"], definition.digest)
        cached = self.described.get(key)
        if cached is None:
            reply = await self.engine.describe(definition)
            base = {"source": ref["source"], "id": ref["id"], "digest": definition.digest}
            if "ok" in reply:   # code that failed to load may not even parse: only loaded code is read for these
                cached = {**base, "visibleRange": self.reads_view(definition), **constants_of(definition.path),
                          **reply["ok"], "error": None, "line": None}
            else:
                cached = {**base, "visibleRange": False, "description": "", "category": "Other", "alertConditions": [],
                          "title": ref["id"], "shortTitle": ref["id"], "overlay": False, "inputs": [],
                          "error": reply["error"], "line": reply["line"]}
            self.described[key] = cached
        return cached

    async def entries(self) -> list[dict]:
        return [await self.describe(ref) for ref in self.refs()]


def pine_bar(columns: dict, index: int) -> dict:
    return {name: columns[name][index] for name in PINE_FIELDS}


class PineLink:
    """Hands one feed's bars to the engine. On brick chart types the last confirmed brick runs
    as the forming bar (it can still grow); provisional bricks are not computed."""

    def __init__(self, feed: Feed | ProjectedFeed, engine: PineEngine):
        self.feed, self.engine = feed, engine
        self.users = 0
        self.fed = 0
        self.last: dict | None = None
        feed.listeners.append(self)
        if feed.ready.is_set():
            self.on_replaced(feed)

    def split(self) -> tuple[int, dict | None]:
        feed = self.feed
        if not feed.projected:
            return len(feed.columns["time"]), feed.forming
        if feed.kind == "heikin_ashi":
            closed = feed.confirmed
            forming = pine_bar(feed.columns, closed) if len(feed.columns["time"]) > closed else None
            return closed, forming
        closed = max(0, feed.confirmed - 1)
        return closed, pine_bar(feed.columns, closed) if feed.confirmed else None

    def on_replaced(self, feed) -> None:
        closed, forming = self.split()
        columns = {name: feed.columns[name][:closed] for name in PINE_FIELDS}
        self.engine.set_series(feed.key, feed.syminfo, columns, forming, whole=feed.raw.whole.is_set())
        self.fed = closed
        self.last = pine_bar(feed.columns, closed - 1) if closed else None

    def on_appended(self, feed, count: int) -> None:
        for index in range(len(feed.columns["time"]) - count, len(feed.columns["time"])):
            self.engine.bar(feed.key, pine_bar(feed.columns, index), True)
        self.fed = len(feed.columns["time"])

    def on_forming(self, feed) -> None:
        if not feed.projected and feed.forming is not None:
            self.engine.bar(feed.key, {name: feed.forming[name] for name in PINE_FIELDS}, False)

    def on_spliced(self, feed, start: int) -> None:
        closed, forming = self.split()
        if closed < self.fed or self.fed and pine_bar(feed.columns, self.fed - 1) != self.last:
            self.on_replaced(feed)
            return
        for index in range(self.fed, closed):
            self.engine.bar(feed.key, pine_bar(feed.columns, index), True)
        self.fed = closed
        self.last = pine_bar(feed.columns, closed - 1) if closed else None
        if forming is not None:
            self.engine.bar(feed.key, forming, False)

    def on_format(self, feed) -> None:
        pass  # syminfo.mintick follows the format the next time the series is set

    def on_status(self, feed) -> None:
        pass

    def close(self) -> None:
        self.feed.listeners.remove(self)
        self.engine.drop_series(self.feed.key)


class SecondaryLink:
    """Hands a feed a script reads with request.security to the engine: Market's newest page at once,
    then its whole window and the bars after it (a new history restarts the scripts reading it, as the
    chart's own window does)."""

    def __init__(self, engine: PineEngine, need: tuple[str, str], feed: Feed | PointsFeed):
        self.engine, self.need, self.feed = engine, need, feed
        feed.listeners.append(self)
        self.on_replaced(feed)

    @staticmethod
    def row(columns: dict, index: int) -> dict:
        return {name: columns[name][index] for name in PINE_FIELDS}

    def on_replaced(self, feed) -> None:
        self.engine.set_secondary(self.need, {name: list(feed.columns[name]) for name in PINE_FIELDS}, feed.forming)

    def on_appended(self, feed, count: int) -> None:
        for index in range(len(feed.columns["time"]) - count, len(feed.columns["time"])):
            self.engine.secondary_bar(self.need, self.row(feed.columns, index), True)

    def on_forming(self, feed) -> None:
        if feed.forming is not None:
            self.engine.secondary_bar(self.need, feed.forming, False)

    def on_spliced(self, feed, start: int) -> None:
        pass

    def on_status(self, feed) -> None:
        pass  # failing after its history: the scripts keep the bars they have

    def on_format(self, feed) -> None:
        pass

    def close(self) -> None:
        self.feed.listeners.remove(self)


class Pins:
    """The code an alert was made with: a copy per digest, so the alert keeps computing what it
    was made on after the indicator changes (and says it is outdated)."""

    def __init__(self, catalog: Catalog, directory: Path):
        self.catalog, self.directory = catalog, directory

    def pin(self, ref: dict) -> str:
        definition = self.catalog.definition(ref)
        if self.catalog.reads_view(definition):
            raise ContractError("unsupported", f"{ref['id']} reads the chart's visible range, so it runs on charts only")
        copy = self.directory / definition.digest
        if not copy.exists():
            sources = [definition.path] if definition.root is None else \
                [item for item in definition.root.rglob("*.py") if "__pycache__" not in item.parts]
            base = definition.path.parent if definition.root is None else definition.root
            staging = copy.with_name(copy.name + ".tmp")
            shutil.rmtree(staging, ignore_errors=True)
            for source in sources:
                target = staging / source.relative_to(base)
                target.parent.mkdir(parents=True, exist_ok=True)
                shutil.copyfile(source, target)
            staging.rename(copy)
        return definition.digest

    def definition(self, ref: dict, digest: str) -> tuple[Definition, bool]:
        """The code to run for this pin, and whether the indicator changed since."""
        try:
            current = self.catalog.definition(ref)
        except ContractError:  # the indicator is gone; its copy still runs
            current = None
        if current is not None and current.digest == digest:
            return current, False
        copy = self.directory / digest
        if ref["source"] == "user" and not ref["id"].endswith(".py"):  # a project folder
            return Definition(digest=digest, path=copy / "main.py", root=copy), True
        return Definition(digest=digest, path=copy / (ref["id"] if ref["source"] == "user" else f"{ref['id']}.py")), True


class Indicators:
    """Indicator instances on feeds for charts and alerts. Feeds a script reads with
    request.security ("OKX:ETHUSDT" on "60") come from Market through the same hub."""

    def __init__(self, engine: PineEngine, catalog: Catalog, hub: Hub):
        self.engine, self.catalog, self.hub = engine, catalog, hub
        self.links: dict[str, PineLink] = {}
        engine.supply = self.supply

    def supply(self, symbol: str, timeframe: str, start: int | None) -> Callable[[], None]:
        task = asyncio.ensure_future(self.follow_secondary(symbol, timeframe, start))
        return task.cancel

    async def follow_secondary(self, symbol: str, timeframe: str, start: int | None) -> None:
        """A feed a script reads, from ``start`` (its chart's first bar) on: intrabars of a long window
        go back that far, up to ``INTRABARS``."""
        need = (symbol, timeframe)
        market = self.hub.market

        def failed(message: str) -> None:
            self.engine.secondary_failed(need, f"request.security({symbol!r}, {timeframe!r}): {message}")
        derived = derived_of(symbol)
        points = derived is not None and derived[1] not in PRICE_SUFFIXES
        feed = None
        try:
            try:
                venue, _, code = (derived[0] if derived else symbol).partition(":")
                instrument, = await market.records([f"{venue.lower()}:{code}"])
                series_timeframe = market_timeframe(timeframe)
                if derived and derived[1] in PRICE_SUFFIXES and instrument["marketType"] not in ("swap", "future"):
                    raise ContractError("unsupported", "mark and index prices are for futures and perpetual contracts")
                if points:  # Market's open interest or funding as bars (derived.py); it fails through ``failed``
                    feed = PointsFeed(market, instrument, derived[1], series_timeframe, failed)
                    await feed.open()
                    await feed.whole.wait()
                else:  # bars: last price, or the contract's mark or index price
                    basis = PRICE_SUFFIXES[derived[1]] if derived else None
                    feed = await self.hub.acquire(instrument, market.default_series(instrument, series_timeframe,
                                                                                    price_basis=basis),
                                                  start=start, cap=INTRABARS)
            except ContractError as error:
                failed(error.message)
                return
            except ValueError as error:  # a timeframe Market has no name for
                failed(str(error))
                return
            link = SecondaryLink(self.engine, need, feed)
            try:
                if not points:  # a points feed is whole already (above)
                    try:
                        await feed.wait_whole()
                    except ContractError as error:  # Market failed the series before giving any bars
                        if not feed.columns["time"]:
                            failed(error.message)
                            return
                await asyncio.Event().wait()  # until no instance reads it any more
            finally:
                link.close()
        finally:
            if feed is not None:
                await (feed.close() if points else self.hub.release(feed))

    async def attach(self, feed: Feed | ProjectedFeed, ref: dict, params: dict,
                     listener: Callable[[dict], None], definition: Definition | None = None
                     ) -> Instance:
        """``definition``: exact code to run instead of the catalog's current one (an alert's pin)."""
        definition = definition or self.catalog.definition(ref)
        link = self.links.get(feed.key)
        if link is None:
            await feed.ready.wait()
            link = self.links.get(feed.key) or PineLink(feed, self.engine)
            self.links[feed.key] = link
        link.users += 1
        return await self.engine.attach(feed.key, definition, params, listener)

    def detach(self, feed: Feed | ProjectedFeed, instance: Instance, listener: Callable[[dict], None]) -> None:
        self.engine.detach(instance, listener)
        link = self.links[feed.key]
        link.users -= 1
        if link.users == 0:
            del self.links[feed.key]
            link.close()
