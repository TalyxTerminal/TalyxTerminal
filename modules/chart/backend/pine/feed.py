"""Bars of another symbol or timeframe for ``request.security``, handed over by Chart.

PyneCore runs each (symbol, timeframe) a script asks for in a child process with a live
provider plugin. This one (entry point ``pyne.plugin: talyx``) only reads what the Pine worker
wrote: one JSON-lines file per (symbol, timeframe) in the worker's feed directory, its first
row ``["h", path, count]`` (the history file Chart wrote, ``history.py``), then rows
``["c", time, closeTime, open, high, low, close, volume]`` (closed) and ``["f", ...]`` (the
forming bar); a new file (smaller than what was read) is a replace. The worker writes the
whole history before an instance starts, so a child's warmup sees it; afterwards each chart
observation reads the rows known by then (``authoritative_observations``, TALYX's patch).
It never fetches anything itself. In the worker, PyneCore asks it for a context's symbol info
before starting the context's child: that is where the instance asks Chart for the bars and
waits for them (``Instance.need``), for constant and input-chosen symbols alike.
"""
from __future__ import annotations

import json
import math
import re
import threading
from dataclasses import dataclass
from datetime import UTC
from pathlib import Path

from pynecore.core.plugin.live_provider import LiveProviderConfig, LiveProviderPlugin
from pynecore.core.syminfo import SymInfo
from pynecore.types.ohlcv import OHLCV

from modules.chart.backend.pine import history


def feed_file(directory: str | Path, symbol: str, timeframe: str) -> Path:
    return Path(directory) / (re.sub(r"[^A-Za-z0-9]+", "_", f"{symbol}@{timeframe}") + ".jsonl")


def row_line(kind: str, bar: dict) -> list:
    volume = bar["volume"]
    return [kind, bar["time"], bar["closeTime"], bar["open"], bar["high"], bar["low"], bar["close"],
            None if volume is None else volume]


@dataclass
class TalyxFeedConfig(LiveProviderConfig):
    directory: str = ""
    history_through: int = 0  # close of the chart's last historical bar, ms


class TalyxFeed(LiveProviderPlugin[TalyxFeedConfig]):
    Config = TalyxFeedConfig
    feed_timeout_bars = None
    authoritative_observations = True
    history_ends_dataset = False  # no real-time bar follows the history: its last bar is barstate.islast

    def __init__(self, **kwargs):
        kwargs["ohlcv_dir"] = None  # bars stay in memory; nothing is written by the child
        super().__init__(**kwargs)
        self.closed: list[list] = []
        self.history: tuple[str, int] | None = None  # the history file the first rows of ``closed`` come from
        self.forming: list | None = None
        self.offset = 0
        self.connected = False
        self.warmup_start: int | None = None  # older rows were never part of this child's run
        self.sent = 0  # closed rows before this index were handed to the child already

    # ── reading the worker's file ──

    def read(self) -> None:
        path = feed_file(self.config.directory, self.symbol, self.timeframe)
        with path.open("rb") as stream:
            stream.seek(0, 2)
            if stream.tell() < self.offset:
                self.closed, self.forming, self.offset, self.sent = [], None, 0, 0
            stream.seek(self.offset)
            chunk = stream.read()
        end = chunk.rfind(b"\n") + 1  # a line still being written waits for the next read
        self.offset += end
        for line in chunk[:end].splitlines():
            row = json.loads(line)
            if row[0] == "h":
                self.history = (row[1], row[2])
                self.closed = list(history.read(row[1], row[2]))
            elif row[0] == "c":
                if self.closed and row[1] <= self.closed[-1][1]:
                    continue
                self.closed.append(row)
                if self.forming is not None and self.forming[1] <= row[1]:
                    self.forming = None
            else:
                self.forming = row

    @staticmethod
    def candle(row: list, closed: bool) -> OHLCV:
        _kind, time, _close_time, open_, high, low, close, volume = row
        return OHLCV(time, open_, high, low, close, math.nan if volume is None else volume, None, closed)

    # ── what the security child asks ──

    def observation_time_ms(self) -> int:
        return self.config.history_through

    def read_security_observation(self, target_time, round_tick):
        """Rows known at the chart's observation instant ``round_tick``: bars closed by then and
        not handed over yet (the child keeps what it was given), and the forming bar while the
        chart is live. The chart asks once per bar, so this stays proportional to what is new."""
        cutoff = int(round_tick) if round_tick else self.config.history_through
        historical = cutoff <= self.config.history_through
        if not historical:  # the whole history was in the file before the child started
            self.read()
        start = self.warmup_start or 0
        closed = []
        while self.sent < len(self.closed) and self.closed[self.sent][2] <= cutoff:
            row = self.closed[self.sent]
            self.sent += 1
            if start <= row[1]:
                closed.append(self.candle(row, True))
        developing = None
        if not historical:
            following = self.closed[self.sent] if self.sent < len(self.closed) else None  # closes after cutoff
            open_now = following if following is not None and following[1] < cutoff else None
            if open_now is None and self.forming is not None and self.forming[1] < cutoff:
                open_now = self.forming
            developing = self.candle(open_now, False) if open_now is not None else None
        return closed, developing, historical

    def download_ohlcv(self, time_from, time_to, on_progress=None, limit=None, with_extra=False):
        self.read()
        start = int(time_from.replace(tzinfo=UTC).timestamp() * 1000)
        end = int(time_to.replace(tzinfo=UTC).timestamp() * 1000)
        self.warmup_start = start if self.warmup_start is None else min(self.warmup_start, start)
        rows = [index for index, row in enumerate(self.closed) if start <= row[1] and row[2] <= end]
        self.save_ohlcv_data([self.candle(self.closed[index], True) for index in rows])
        if rows:
            self.sent = max(self.sent, rows[-1] + 1)

    def update_symbol_info(self) -> SymInfo:
        need = getattr(threading.current_thread(), "need", None)  # in the worker: an instance's thread
        if need is not None:
            need(self.symbol, self.timeframe)
        prefix, _, ticker = self.symbol.partition(":")
        self.syminfo = SymInfo(prefix=prefix, ticker=ticker, description=ticker, period=self.timeframe,
                               currency="", basecurrency=None, type="crypto", mintick=1e-8, pricescale=100_000_000,
                               pointvalue=1, mincontract=1, opening_hours=[], session_starts=[], session_ends=[],
                               timezone="UTC")
        return self.syminfo

    def get_list_of_symbols(self, *args, **kwargs):
        return [self.symbol]

    @classmethod
    def to_tradingview_timeframe(cls, timeframe):
        return timeframe

    @classmethod
    def to_exchange_timeframe(cls, timeframe):
        return timeframe

    # ── the live loop is driven by observations, not by this stream ──

    async def connect(self):
        self.connected = True

    async def disconnect(self):
        self.connected = False

    @property
    def is_connected(self):
        return self.connected

    async def watch_ohlcv(self, symbol, timeframe):
        raise NotImplementedError("TALYX secondary bars arrive per chart observation")

    async def backfill_closed_bars(self, symbol, timeframe, since_ms):
        self.read()
        return [self.candle(row, True) for row in self.closed if row[1] > since_ms]
