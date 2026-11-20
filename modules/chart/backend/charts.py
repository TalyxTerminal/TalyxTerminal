"""Chart documents: the only way a chart changes.

Every change is a commit of commands at a base revision; the UI, AI and other modules commit
the same way. Each chart keeps 100 undo steps in memory (gone when TALYX closes). A chart has
its current revision and the revision the workspace was last saved with; closing a workspace
without saving returns its charts to the saved revision, and charts never saved go away.
"""
from __future__ import annotations

import uuid
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field

from talyx_sdk import ContractError

from . import research
from .commands import CommandError, apply
from .document import (
    MAX_UNDO,
    PRICE_PANE,
    ChartDocument,
    ComparisonAdd,
    DrawingAdd,
    DrawingsReplace,
    Indicator,
    IndicatorAdd,
    IndicatorsReplace,
    LayerSet,
    Pane,
    SavedLayer,
    SelectionSet,
    drawing_group,
)
from .marketdata import Market
from .models import ResearchLayer
from .store import Store

DEFAULT_INSTRUMENT = "okx:BTC/USDT"  # Market's exact form for a spot pair: VENUE:BASE/QUOTE


@dataclass
class Entry:
    id: str
    workspace: str
    revision: int
    document: ChartDocument
    saved_revision: int | None
    undo: list[ChartDocument] = field(default_factory=list)
    redo: list[ChartDocument] = field(default_factory=list)


class Charts:
    def __init__(self, store: Store, market: Market, publish: Callable[[str, dict], None]):
        self.store, self.market, self.publish = store, market, publish
        self.entries: dict[str, Entry] = {}
        self.listeners: dict[str, list[Callable[[Entry, ChartDocument], None]]] = {}
        self.changed: list[Callable[[str, str, ChartDocument | None], None]] = []  # alerts follow drawings

    def entry(self, workspace: str, chart_id: str) -> Entry:
        entry = self.entries.get(chart_id)
        if entry is None:
            stored = self.store.chart(chart_id)
            if stored is None:
                raise ContractError("not_found", f"No chart {chart_id}")
            entry = self.entries[chart_id] = Entry(chart_id, stored["workspace"], stored["revision"],
                                                   ChartDocument.model_validate(stored["document"]),
                                                   stored["savedRevision"])
        if entry.workspace != workspace:
            raise ContractError("not_found", f"No chart {chart_id} in this workspace")
        return entry

    @staticmethod
    def state(entry: Entry) -> dict:
        return {"chartId": entry.id, "revision": entry.revision,
                "document": entry.document.for_view(),
                "drawingGroup": drawing_group(entry.document), "canUndo": bool(entry.undo), "canRedo": bool(entry.redo)}

    async def default_instrument(self) -> dict:
        """A new chart without a symbol: OKX BTC/USDT spot (live around the clock, no account)."""
        record, = await self.market.records([DEFAULT_INSTRUMENT])
        return record

    async def create(self, workspace: str, instrument_id: str | None, timeframe: str | None,
                     import_id: str | None, copy_of: str | None, *, indicators: list[Indicator] | None = None,
                     layers: Sequence[ResearchLayer] = ()) -> dict:
        chart_id = "chart-" + uuid.uuid4().hex[:12]
        if copy_of is not None:
            document = self.entry(workspace, copy_of).document.model_copy(update={"id": chart_id}, deep=True)
        else:
            instrument = (await self.market.instrument(instrument_id))[0] if instrument_id else \
                await self.default_instrument()
            # An indicator drawn below the price (RSI) comes with its own pane, as when added to a chart.
            panes = [Pane(id=PRICE_PANE, height=3.0), *(Pane(id=item.paneId) for item in indicators or [] if item.paneId != PRICE_PANE)]
            saved_layers = [await self.prepare_layer(layer, instrument) for layer in layers]
            series = self.market.default_series(instrument, timeframe or "1d", import_id=import_id,
                                                **({"adjustment": layers[0].adjustment} if layers else {}))
            if layers:
                ids = {layer.id for layer in layers}
                for old_id in self.store.charts(workspace):
                    old = self.entry(workspace, old_id)
                    if any(item.id in ids for item in old.document.layers):
                        return self.commit_resolved(old, [{"type": "selection.set", "instrument": instrument, "series": series},
                            *({"type": "layer.set", "layer": layer.model_dump(by_alias=True)} for layer in saved_layers)])
            document = ChartDocument(id=chart_id, instrument=instrument, panes=panes,
                                     layers=saved_layers,
                                     series=series,
                                     # TradingView's: a new chart comes with Volume, an indicator like any other.
                                     indicators=indicators if indicators is not None else [Indicator.model_validate(
                                         {"id": "volume", "definition": {"source": "builtin", "id": "volume"}})])
        entry = self.entries[chart_id] = Entry(chart_id, workspace, 1, document, None)
        self.store.put_chart(chart_id, workspace, 1, document.model_dump(mode="json", by_alias=True))
        self.published(entry)
        return self.state(entry)

    async def prepare_layer(self, layer: ResearchLayer, instrument: dict) -> SavedLayer:
        start, end, count_bars = research.check_layer(layer)
        if instrument["instrumentId"] != layer.instrumentId:
            instrument, _ = await self.market.instrument(layer.instrumentId)
        if count_bars:
            series = self.market.default_series(instrument, layer.timeframe, adjustment=layer.adjustment)
            await research.history(self.market, instrument, series, start, end)
        return SavedLayer(**layer.model_dump(by_alias=True), symbol=instrument["displaySymbol"], version=uuid.uuid4().hex)

    async def resolve(self, document: ChartDocument, commands: list) -> list[dict]:
        """The commands as values; ``selection.set`` and ``comparison.add`` with Market's instrument
        and default series."""
        resolved = []
        selected_instrument, selected_series = document.instrument, document.series
        for command in commands:
            whole = isinstance(command, (DrawingAdd, IndicatorAdd, DrawingsReplace, IndicatorsReplace))
            value = command.model_dump(mode="json", by_alias=True, exclude_unset=not whole)
            if isinstance(command, SelectionSet):
                instrument = selected_instrument
                if command.instrumentId and command.instrumentId != instrument["instrumentId"]:
                    instrument = (await self.market.instrument(command.instrumentId))[0]
                    # Back on a research layer's instrument, the chart takes the layer's adjustment so the layer shows.
                    choice = next(({"adjustment": layer.adjustment} for layer in document.layers
                                   if layer.instrumentId == instrument["instrumentId"]), {})
                else:
                    current = selected_series
                    choice = {"price_basis": current["priceBasis"], "adjustment": current["adjustment"],
                              "adjustment_anchor": current.get("adjustmentAnchor"),
                              "import_id": current.get("importId")}
                choice.update({key: value for key, value in (
                    ("price_basis", command.priceBasis), ("adjustment", command.adjustment),
                    ("adjustment_anchor", command.adjustmentAnchor)) if value is not None})
                if choice.get("adjustment_anchor") == "latest":
                    choice["adjustment_anchor"] = None
                series = self.market.default_series(instrument, command.timeframe or selected_series["timeframe"],
                                                    **choice)
                selected_instrument, selected_series = instrument, series
                value = {"type": "selection.set", "instrument": instrument, "series": series}
            elif isinstance(command, LayerSet):
                layer = await self.prepare_layer(command.layer, selected_instrument)
                value["layer"] = layer.model_dump(by_alias=True)
            elif isinstance(command, ComparisonAdd):
                instrument = (await self.market.instrument(command.instrumentId))[0]
                series = self.market.default_series(instrument, selected_series["timeframe"])
                value = {**value, "instrument": instrument, "series": series}
            resolved.append(value)
        return resolved

    async def commit(self, workspace: str, chart_id: str, base_revision: int, commands: list) -> dict:
        entry = self.entry(workspace, chart_id)
        if base_revision != entry.revision:
            raise ContractError("conflict", "The chart changed meanwhile", {"revision": entry.revision})
        resolved = await self.resolve(entry.document, commands)
        if base_revision != entry.revision:
            raise ContractError("conflict", "The chart changed meanwhile", {"revision": entry.revision})
        return self.commit_resolved(entry, resolved)

    def commit_resolved(self, entry: Entry, resolved: list[dict]) -> dict:
        try:
            document = apply(entry.document, resolved)
        except (CommandError, ValueError) as error:
            raise ContractError("invalid_input", str(error)) from error
        entry.undo = [*entry.undo, entry.document][-MAX_UNDO:]
        entry.redo = []
        self.set(entry, document)
        return self.state(entry)

    def step(self, workspace: str, chart_id: str, back: bool) -> dict:
        entry = self.entry(workspace, chart_id)
        source, target = (entry.undo, entry.redo) if back else (entry.redo, entry.undo)
        if not source:
            raise ContractError("invalid_input", "Nothing to " + ("undo" if back else "redo"))
        target.append(entry.document)
        self.set(entry, source.pop())
        return self.state(entry)

    def set(self, entry: Entry, document: ChartDocument) -> None:
        previous = entry.document
        entry.document, entry.revision = document, entry.revision + 1
        self.store.put_chart(entry.id, entry.workspace, entry.revision, document.model_dump(mode="json", by_alias=True))
        self.published(entry)
        if previous.drawings != document.drawings:
            self.drawings_changed(entry.workspace, entry.id, document)
        for listener in self.listeners.get(entry.id, ()):
            listener(entry, previous)

    def drawings_changed(self, workspace: str, chart_id: str, document: ChartDocument | None) -> None:
        for listener in self.changed:
            listener(workspace, chart_id, document)

    def published(self, entry: Entry) -> None:
        self.publish(entry.workspace, {"type": "chart", "chartId": entry.id, "revision": entry.revision,
                                       "saved": entry.revision == entry.saved_revision})

    def save(self, workspace: str, charts: list[dict]) -> None:
        kept = {}
        for item in charts:
            entry = self.entry(workspace, item["chartId"])
            if item["revision"] != entry.revision:
                raise ContractError("conflict", "The chart changed meanwhile", {"revision": entry.revision})
            kept[entry.id] = entry
        removed = self.store.charts(workspace, saved_only=True) - kept.keys()
        with self.store.transaction():
            for chart_id in removed:
                self.store.delete_chart(chart_id)
            for chart_id in kept:
                self.store.save_chart(chart_id)
        for chart_id in removed:
            self.entries.pop(chart_id, None)
            self.drawings_changed(workspace, chart_id, None)
        for entry in kept.values():
            entry.saved_revision = entry.revision
            self.published(entry)

    def workspace_closed(self, workspace: str) -> None:
        """Back to the saved revision; never-saved charts go away. Undo history goes too."""
        for chart_id in self.store.charts(workspace):
            stored = self.store.chart(chart_id)
            self.entries.pop(chart_id, None)
            if stored["savedRevision"] is None:
                self.store.delete_chart(chart_id)
                self.drawings_changed(workspace, chart_id, None)
            elif stored["savedRevision"] != stored["revision"]:
                self.store.revert_chart(chart_id)
                self.drawings_changed(workspace, chart_id, ChartDocument.model_validate(stored["savedDocument"]))

    def listen(self, chart_id: str, listener: Callable[[Entry, ChartDocument], None]) -> Callable[[], None]:
        self.listeners.setdefault(chart_id, []).append(listener)
        return lambda: self.listeners[chart_id].remove(listener)
