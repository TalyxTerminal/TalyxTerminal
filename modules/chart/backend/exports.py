"""A chart's bars and indicator values as a file: CSV, JSON or Parquet.

Rows are the chart's closed bars in its current window (projected bars on Heikin Ashi and brick
types); after time, closeTime and OHLCV come one column per indicator output, named
``<indicator>: <output>``, empty where the indicator has no value for that bar.
"""
from __future__ import annotations

import base64
import csv
import hashlib
import io
import json
import re
import time
import uuid
from pathlib import Path

import pyarrow as pa
import pyarrow.parquet as pq
from talyx_sdk import ContractError
from talyx_sdk.i18n import t

from .flows import ChartFlow
from .pine.indicators import ALERT_CONDITION

BAR_COLUMNS = ("time", "closeTime", "open", "high", "low", "close", "volume")
MEDIA = {"csv": "text/csv", "json": "application/json", "parquet": "application/vnd.apache.parquet"}


def table(flow: ChartFlow) -> dict[str, list]:
    feed = flow.shown
    if not flow.current or feed is None or not feed.ready.is_set():
        raise ContractError("unavailable", t("The chart has no bars yet"))
    columns = {name: list(feed.columns[name]) for name in BAR_COLUMNS}
    row_of = {time: index for index, time in enumerate(columns["time"])}
    bindings = flow.replay_bindings if flow.replay else flow.bindings
    for binding in bindings.values():
        output = binding.instance.output if binding.instance is not None else None
        if output is None:
            continue
        label = binding.ref["id"].removesuffix(".py")
        for key, values in output["values"].items():
            if key.startswith(ALERT_CONDITION):
                continue
            name, suffix = f"{label}: {key}", 2
            while name in columns:
                name, suffix = f"{label} ({suffix}): {key}", suffix + 1
            column = [None] * len(columns["time"])
            for bar_time, value in zip(output["time"], values, strict=True):
                if bar_time in row_of:
                    column[row_of[bar_time]] = value
            columns[name] = column
    return columns


def encode(columns: dict[str, list], chart_format: str) -> bytes:
    if chart_format == "csv":
        text = io.StringIO()
        writer = csv.writer(text, lineterminator="\n")
        writer.writerow(columns)
        writer.writerows(["" if value is None else value for value in row] for row in zip(*columns.values()))
        data = text.getvalue().encode()
    elif chart_format == "json":
        data = json.dumps([dict(zip(columns, row)) for row in zip(*columns.values())], allow_nan=False).encode()
    else:
        sink = io.BytesIO()
        pq.write_table(pa.table({name: pa.array(values, type=pa.int64() if name in ("time", "closeTime")
                                                 else pa.float64()) for name, values in columns.items()}), sink)
        data = sink.getvalue()
    return data


def filename(flow: ChartFlow, chart_format: str) -> str:
    instrument = flow.feed.instrument
    stem = re.sub(r"[^A-Za-z0-9]+", "_", f"{instrument['displaySymbol']} {flow.feed.series['timeframe']}").strip("_")
    return f"{stem}.{chart_format}"


def export(flow: ChartFlow, chart_format: str) -> dict:
    columns = table(flow)
    data = encode(columns, chart_format)
    return {"filename": filename(flow, chart_format), "mediaType": MEDIA[chart_format],
            "data": base64.b64encode(data).decode(), "rows": len(columns["time"])}


def publish_file(folder: Path, name: str, data: bytes) -> dict:
    folder.mkdir(parents=True, exist_ok=True)
    cutoff = time.time() - 86400
    for old in folder.iterdir():
        try:
            if old.stat().st_mtime < cutoff:
                old.unlink(missing_ok=True)
        except FileNotFoundError:  # another export swept it
            continue
    path = folder / f"{uuid.uuid4().hex}-{name}"
    try:
        path.write_bytes(data)
    except OSError:
        path.unlink(missing_ok=True)
        raise
    return {"path": str(path.resolve()), "sha256": hashlib.sha256(data).hexdigest(), "bytes": len(data)}


def hand_over(folder: Path, name: str, chart_format: str, columns: dict[str, list]) -> dict:
    return {"filename": name, "mediaType": MEDIA[chart_format], "rows": len(columns["time"]),
            "file": publish_file(folder, name, encode(columns, chart_format))}


def research_snapshot(folder: Path, layer: dict, limit: int) -> dict:
    import orjson

    data = orjson.dumps(layer)
    return {"layer": layer if len(data) <= limit else publish_file(folder, "research-layer.json", data)}
