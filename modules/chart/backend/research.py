"""Immutable overlays and the exact Market bar window shared by an embed and its saved chart."""
from __future__ import annotations

import hashlib
from datetime import UTC, datetime
from pathlib import Path

from pydantic import ValidationError
from talyx_sdk import ContractError
from talyx_sdk.i18n import t

from vocabulary.identity import timeframe_nanoseconds
from vocabulary.python.types import FileRef

from . import bars
from .models import ResearchLayer

BAR_LIMIT = 200_000
MARKER_LIMIT = 20_000
PAGE_SIZE = 10_000  # Market.read_series maximum


def milliseconds(text: str) -> int:
    try:
        instant = datetime.fromisoformat(text)
        return int((instant if instant.tzinfo else instant.replace(tzinfo=UTC)).timestamp() * 1000)
    except (ValueError, OverflowError) as error:
        raise ContractError("invalid_input", t("Invalid ISO-8601 time: {text}", text=text)) from error


def limit(item: str, count: int, maximum: int) -> None:
    if count > maximum:
        raise ContractError("too_large", t("{item} contains {count} entries, {value} over the {maximum} limit", item=item, count=f'{count:,}', value=f'{count - maximum:,}', maximum=f'{maximum:,}'))


def load_reference(ref: FileRef, module_data: Path) -> ResearchLayer:
    path = Path(ref.path).resolve()
    if not path.is_relative_to(module_data):
        raise ContractError("invalid_input", t("A research snapshot must come from module storage"))
    try:
        data = path.read_bytes()
    except OSError as error:
        raise ContractError("not_found", t("The research snapshot file is unavailable")) from error
    if len(data) != ref.bytes or hashlib.sha256(data).hexdigest() != ref.sha256:
        raise ContractError("invalid_input", t("The research snapshot differs from its file reference"))
    try:
        return ResearchLayer.model_validate_json(data)
    except ValidationError as error:
        raise ContractError("invalid_input", "Invalid ResearchLayer file: " + str(error.errors(include_input=False)[:1])) from error


def check_layer(layer: ResearchLayer) -> tuple[int, int, bool]:
    start, end = milliseconds(layer.window.start), milliseconds(layer.window.to)
    if end < start:
        raise ContractError("invalid_input", t("Research window ends before it starts"))
    try:
        step = timeframe_nanoseconds(layer.timeframe)
    except ValueError as error:
        raise ContractError("invalid_input", str(error)) from error
    panes = [pane.key for pane in layer.panes]
    if "price" in panes or len(panes) != len(set(panes)):
        raise ContractError("invalid_input", t("Research panes need unique keys distinct from price"))
    for series in layer.series:
        limit(series.name, len(series.points), BAR_LIMIT)
        if series.pane != "price" and series.pane not in panes:
            raise ContractError("invalid_input", t("{name} refers to unknown pane {pane}", name=series.name, pane=series.pane))
        for point in series.points:
            milliseconds(point.time)
    limit("Trade markers", len(layer.markers), MARKER_LIMIT)
    for marker in layer.markers:
        milliseconds(marker.time)
    # Calendar months/years and exchange closures only reduce the possible count. Exchange daily opens
    # shift with DST, so only sub-day steps use this fast upper bound.
    safe_step = min(step or 86_400_000_000_000, 23 * 3_600_000_000_000)
    return start, end, (end - start) * 1_000_000 // safe_step >= BAR_LIMIT


async def history(market, instrument, series, start: int, end: int) -> dict:
    """Market reads the oldest page when start and end are supplied. Both bounds are inclusive."""
    columns = {key: [] for key in bars.FIELDS}
    count = 0
    cursor = start * 1_000_000
    while cursor <= end * 1_000_000:
        page = await market.client.read_series({"instrumentId": instrument["instrumentId"], "series": series,
                    "start": str(cursor), "end": str(end * 1_000_000), "limit": PAGE_SIZE, "fill": "await"})
        rows = page["columns"]
        count += len(rows["time"])
        if count <= BAR_LIMIT:
            for key, values in bars.columns(rows).items():
                columns[key].extend(values)
        if len(rows["time"]) < PAGE_SIZE:
            break
        cursor = int(rows["time"][-1]) + 1
    limit("Window bars", count, BAR_LIMIT)
    return columns
