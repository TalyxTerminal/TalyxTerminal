"""Sources behind a program on this computer: Interactive Brokers (IB Gateway or TWS) and Futu (OpenD).

Both connect to 127.0.0.1, never through the proxy, and serve realtime data and recent
history to people with an account there; neither is used for long history (``historyRole:
recent_only``). IB (``ib_async``, official API docs): bars of 30 seconds and less are limited
to 60 requests in ten minutes and nothing older than six months; one-minute and longer bars
are paced; ``reqHistoricalData`` with ``formatDate=2`` stamps intraday bars in UTC and daily
bars with their date. Futu (``futu-api``, OpenD docs): ``request_history_kline`` pages up to
1,000 rows with ``page_req_key``; codes are ``US.AAPL``, ``HK.00700``, ``SH.600519``;
``time_key`` is the exchange's local time; history is limited to 100 stocks in 7 days on the
base quota. When the program is not running the source says so (``local_program_off``).
Neither was tried against a real account: they follow the official interfaces and are
tested against the documented answers.
"""

from __future__ import annotations

from datetime import UTC, datetime

import numpy as np
from talyx_sdk.i18n import mark, t

from vocabulary.identity import timeframe_nanoseconds

from .. import bars, calendars
from ..common import NS, MarketError, now_ns
from ..routing import in_worker
from .base import History, Source, coverage, row

LOCAL_OFF = {"reason": "local_program_off"}


def local_off(source_id: str, program: str, exc: BaseException) -> MarketError:
    return MarketError("source_unavailable", t("{program} is not running on this computer ({exc})", program=program, exc=exc),
                       source_id=source_id, details=LOCAL_OFF)


def _stamp(value) -> int:
    if isinstance(value, datetime):
        moment = value if value.tzinfo else value.replace(tzinfo=UTC)
        return int(moment.timestamp() * 10**6) * 1000
    return int(datetime.fromisoformat(str(value)).replace(tzinfo=UTC).timestamp()) * NS


def _daily_columns(days: list, values: dict, calendar: str) -> bars.Columns:
    opens, closes = calendars.daily_times(calendar, np.asarray([np.datetime64(str(d)[:10], "D") for d in days]))
    return bars.sort_unique({"time": opens, "close_time": closes, **values})


def _intraday_columns(times: np.ndarray, values: dict, calendar: str, timeframe: str) -> bars.Columns:
    step = timeframe_nanoseconds(timeframe)
    if not len(times):
        return bars.empty()
    grid_open, grid_close = calendars.bar_grid(calendar, timeframe, int(times.min()), int(times.max()) + step)
    slot = np.searchsorted(grid_open, times)
    inside = (slot < len(grid_open)) & (grid_open[np.minimum(slot, max(len(grid_open) - 1, 0))] == times) \
        if len(grid_open) else np.zeros(len(times), bool)
    columns = {"time": times[inside], "close_time": grid_close[slot[inside]],
               **{name: array[inside] for name, array in values.items()}}
    columns = bars.sort_unique(columns)
    return bars.take(columns, columns["close_time"] <= now_ns())


# ── Interactive Brokers ────────────────────────────────────────────────────

_IB_BAR = {"1m": "1 min", "5m": "5 mins", "15m": "15 mins", "1h": "1 hour", "1d": "1 day"}


class IbSource(Source):
    id = "ib"
    descriptor = {"name": "Interactive Brokers (IB Gateway)", "sourceIds": ["ib"], "kinds": ["bars"],
                  "assetClasses": ["equity", "fund", "index"], "credentials": "none", "paid": False,
                  "supports": ["history", "configure"], "localGateway": True,
                  "settings": [{"name": "port", "label": mark("IB Gateway or TWS API port"), "type": "integer",
                                "secret": False, "required": True},
                               {"name": "clientId", "label": mark("API client id"), "type": "integer", "secret": False,
                                "required": False}]}

    def __init__(self, market):
        super().__init__(market)
        self._ib = None

    async def describe(self, source_id: str) -> dict:
        periods = [("1m", "P180D", 2000), ("5m", "P180D", 2000), ("15m", "P180D", 2000), ("1h", "P180D", 2000),
                   ("1d", "P365D", 365)]
        return coverage([row({"markets": ["US", "HK"]}, "bars", periods, history_role="recent_only")],
                        concurrency=1, rpm=6)

    def client(self):
        from ib_async import IB
        return IB()

    async def _connected(self):
        if self._ib is not None and self._ib.isConnected():
            return self._ib
        ib = self.client()
        port = int(self.settings.get("port") or 4001)
        try:
            await ib.connectAsync("127.0.0.1", port, clientId=int(self.settings.get("clientId") or 17), timeout=5)
        except (OSError, TimeoutError, ConnectionError) as exc:
            raise local_off(self.id, "IB Gateway", exc) from exc
        self._ib = ib
        return ib

    async def history(self, source_id: str, target, kind: str, timeframe: str | None, price_basis: str,
                      start: int, end: int,
                      archive: tuple[str, int] | None = None) -> History:
        if kind != "bars" or timeframe not in _IB_BAR:
            raise MarketError("unsupported", f"IB has no {kind} {timeframe or ''}".strip(), source_id=self.id)
        from ib_async import Stock
        ib = await self._connected()
        code = target.code_at()
        contract = Stock(code.split(".")[0].lstrip("0"), "SEHK", "HKD") if target.market == "HK" \
            else Stock(code.replace(".", " "), "SMART", "USD")
        stop = min(end, now_ns())
        days = max(1, -(-(stop - start) // (86_400 * NS)))
        duration = f"{min(days, 365)} D" if timeframe != "1d" or days < 365 else f"{-(-days // 365)} Y"
        bars_ = await ib.reqHistoricalDataAsync(
            contract, endDateTime=datetime.fromtimestamp(stop // NS, UTC), durationStr=duration,
            barSizeSetting=_IB_BAR[timeframe], whatToShow="TRADES", useRTH=True, formatDate=2)
        calendar = target.instrument["calendarId"]
        values = {name: np.asarray([float(getattr(b, name)) for b in bars_], dtype=np.float64)
                  for name in ("open", "high", "low", "close", "volume")}
        if timeframe == "1d":
            columns = _daily_columns([b.date for b in bars_], values, calendar) if bars_ else bars.empty()
        else:
            times = np.asarray([_stamp(b.date) for b in bars_], dtype=np.int64)
            columns = _intraday_columns(times, values, calendar, timeframe)
        columns = bars.window(columns, start, end)
        history = History(columns)
        if not bars.length(columns):
            history.empty_reason = "source_has_none"
        return history

    async def close(self) -> None:
        if self._ib is not None:
            self._ib.disconnect()


# ── Futu OpenD ─────────────────────────────────────────────────────────────

_FUTU_K = {"1m": "K_1M", "5m": "K_5M", "15m": "K_15M", "1h": "K_60M", "1d": "K_DAY"}


def futu_code(market: str, code: str) -> str:
    number, _dot, exchange = code.partition(".")
    if market == "HK":
        return f"HK.{number.zfill(5)}"
    if market == "CN":
        return f"{exchange}.{number}"
    return f"US.{code}"


class FutuSource(Source):
    id = "futu"
    descriptor = {"name": "Futu (OpenD)", "sourceIds": ["futu"], "kinds": ["bars"],
                  "assetClasses": ["equity", "fund", "index"], "credentials": "none", "paid": False,
                  "supports": ["history", "configure"], "localGateway": True,
                  "settings": [{"name": "port", "label": mark("OpenD port"), "type": "integer", "secret": False,
                                "required": True}]}

    async def describe(self, source_id: str) -> dict:
        periods = [(tf, "P365D", 1000) for tf in ("1m", "5m", "15m", "1h")] + [("1d", "P3650D", 1000)]
        return coverage([row({"markets": ["HK", "US", "CN"]}, "bars", periods, history_role="recent_only")],
                        concurrency=1, rpm=60)

    def context(self):
        """A quote context on OpenD (futu-api); the tests hand in the documented answers here."""
        try:
            from futu import OpenQuoteContext
        except ImportError as exc:
            raise local_off(self.id, "Futu OpenD (futu-api is not installed)", exc) from exc
        return OpenQuoteContext(host="127.0.0.1", port=int(self.settings.get("port") or 11111))

    async def history(self, source_id: str, target, kind: str, timeframe: str | None, price_basis: str,
                      start: int, end: int,
                      archive: tuple[str, int] | None = None) -> History:
        if kind != "bars" or timeframe not in _FUTU_K:
            raise MarketError("unsupported", f"Futu has no {kind} {timeframe or ''}".strip(), source_id=self.id)
        calendar = target.instrument["calendarId"]
        code = futu_code(target.market, target.code_at())
        first = str(calendars.trading_day(calendar, np.asarray([start], dtype=np.int64))[0])
        last = str(calendars.trading_day(calendar, np.asarray([min(end, now_ns()) - 1], dtype=np.int64))[0])

        def load():
            try:
                context = self.context()
            except MarketError:
                raise
            except Exception as exc:  # noqa: BLE001 - OpenD refusing the connection
                raise local_off(self.id, "Futu OpenD", exc) from exc
            try:
                frames, key = [], None
                while True:
                    ret, frame, key = context.request_history_kline(code, start=first, end=last,
                                                                    ktype=_FUTU_K[timeframe], autype="NONE",
                                                                    max_count=1000, page_req_key=key)
                    if ret != 0:
                        raise MarketError("source_unavailable", t("Futu: {frame}", frame=frame), source_id=self.id)
                    frames.append(frame)
                    if key is None:
                        return frames
            finally:
                context.close()

        frames = await in_worker(load)
        records = [item for frame in frames for item in frame.to_dict("records")]
        values = {name: np.asarray([float(item[name]) for item in records], dtype=np.float64)
                  for name in ("open", "high", "low", "close", "volume")}
        if timeframe == "1d":
            columns = _daily_columns([item["time_key"] for item in records], values, calendar) if records \
                else bars.empty()
        else:
            # time_key is the bar's end in exchange time.
            local = np.asarray([np.datetime64(item["time_key"].replace(" ", "T"), "ns") for item in records],
                               dtype="datetime64[ns]")
            ends = _local_to_utc(local, calendar)
            columns = _intraday_columns(ends - timeframe_nanoseconds(timeframe), values, calendar, timeframe)
        columns = bars.window(columns, start, end)
        history = History(columns)
        if not bars.length(columns):
            history.empty_reason = "source_has_none"
        return history


def _local_to_utc(local: np.ndarray, calendar: str) -> np.ndarray:
    import pandas as pd
    index = pd.DatetimeIndex(local).tz_localize(calendars.timezone(calendar))
    return index.asi8.astype(np.int64)

