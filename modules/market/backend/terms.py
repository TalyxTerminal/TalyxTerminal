"""Trading terms per exact instrument (``instruments.terms``).

Crypto terms come from the venue's own market list through the source that
serves the contract (fees, size rules, quantity unit, multiplier, tick). Stocks
carry Market's dated rules for US and A-share listings (order size, settlement,
taxes, an overridable commission assumption). With series plus start/end, each
daily session's raw and adjusted prices are frozen together with the exact
coordinate between them: Market stores raw prices and adjusts on read from the
corporate actions it holds, so the scale of a session is the product of the
adjustment steps after it, not a fit. Whatever is not known stays null and
``unavailable`` says why; nothing is estimated.
"""

from __future__ import annotations

import hashlib
import json
from datetime import datetime
from decimal import Decimal
from zoneinfo import ZoneInfo

import numpy as np

from vocabulary.instruments import MarketInstrument, MarketSeries, ohlcv_series

from . import bars, calendars
from .common import MarketError, now_ns
from .data import MAX_PAGE, READ_REQUEST_LIMIT, _rebase
from .routing import in_worker
from .store import SeriesKey

RULES = "https://www.sse.com.cn/lawandrules/sselawsrules2025/fund/trading/c/c_20260424_10817739.shtml"
TAX = "https://shanxi.chinatax.gov.cn/web/detail/sx-11400-545-1780448"
COMMISSION = "https://www.interactivebrokers.com/en/pricing/commissions-stocks.php"
US_TICK_RELIEF = "https://www.sec.gov/files/rules/exorders/2026/34-105656.pdf"
VERSION = "equity-rules-2026-11-20-v5"
FIELDS = ("fees", "minQuantity", "quantityIncrement", "quantityUnit", "contractMultiplier", "priceIncrement")
HK_SPREADS = "https://www.hkex.com.hk/Services/Circulars-and-Notices/Participant-and-Members-Circulars/SEHK/2026/20260731_LSD_046_2026"
# Minimum price steps by price, the one place for them.
# US, Regulation NMS Rule 612: $0.01 at $1.00 and above, $0.0001 below; SEC release 34-105656 keeps this regime
# through the first business day of November 2027 (US_TICK_RELIEF).
# Hong Kong, HKEX spread table for stocks, REITs and equity warrants after phase 2 of the minimum spread reduction
# (2026-08-03, HKEX notice LSD 046 2026): "from 0.01 to 0.25", then "over" each bound "up to" the next, so a price
# on a bound takes the lower band's step. Exchange traded products use another table, not held here.
_HK_STEPS = ((Decimal("0.25"), Decimal("0.001")), (Decimal("10"), Decimal("0.005")), (Decimal("20"), Decimal("0.01")),
             (Decimal("50"), Decimal("0.02")), (Decimal("100"), Decimal("0.05")), (Decimal("200"), Decimal("0.1")),
             (Decimal("500"), Decimal("0.2")), (Decimal("1000"), Decimal("0.5")), (Decimal("2000"), Decimal("1")),
             (Decimal("5000"), Decimal("2")), (Decimal("9995"), Decimal("5")))


def tick(market: str, price: float, day: str) -> Decimal:
    """The minimum price step of a US or Hong Kong stock trading at ``price`` on ``day`` (ISO date)."""
    value = Decimal(_price(price))
    if market == "US":
        if day >= "2027-11-01":
            raise MarketError("unsupported", f"The US tick after November 2027 ({day}) is not verified",
                              details={"evidence": US_TICK_RELIEF})
        return Decimal("0.01") if value >= 1 else Decimal("0.0001")
    return next(step for bound, step in _HK_STEPS if value <= bound)


def _cn_tick(record: dict) -> str:
    return "0.001" if record["assetClass"] == "fund" else "0.01"


def static_terms(record: dict) -> dict:
    """Dated execution rules of a US or A-share (Shanghai, Shenzhen) listing."""
    market, code = record["venueId"], record["symbol"]
    china = market == "CN" and code.endswith((".SH", ".SZ"))
    us = market == "US"
    if not china and not us:
        raise MarketError("unsupported", f"Market has no verified stock trading rules for {code}")
    currency = record.get("currency") or ("CNY" if china else "USD")
    star = china and code.startswith("688")
    return {
        "version": VERSION, "currency": currency,
        "orderSize": {"minimum": 200 if star else 100 if china else 1, "increment": 1 if star or us else 100,
                      "oddLotSellOnly": china, "oddLotSellAll": china,
                      "effectiveFrom": "2019-07-22" if star else "1990-01-01", "evidence": RULES if china else COMMISSION},
        "settlement": {"sellAfterTradingDays": 1 if china else 0, "calendarId": record["calendarId"],
                       "evidence": RULES if china else
                       "US cash-share resale assumption; broker settlement restrictions excluded"},
        "commission": {"rate": "0.0003" if china else "0", "perShare": "0" if china else "0.005",
                       "minimum": "5" if china else "1", "maximumRate": None if china else "0.01",
                       "assumption": "Research commission assumption; override in account settings",
                       "evidence": "TALYX research assumption" if china else COMMISSION},
        "taxes": ([{"effectiveFrom": "2008-09-19", "effectiveUntil": "2023-08-27", "buyRate": "0", "sellRate": "0.001",
                    "evidence": TAX},
                   {"effectiveFrom": "2023-08-28", "effectiveUntil": None, "buyRate": "0", "sellRate": "0.0005",
                    "evidence": TAX}] if china and record["assetClass"] != "fund" else []),
        "limitations": ["Free source histories may omit delisted constituents (survivorship bias).",
                        "Adjusted backtests use simulated share quantities and adjusted notional for cash and fees."]
        + ([] if china else ["US default commission excludes separately billed regulatory/venue fees; "
                             "broker-specific fees require an account override."]),
        "sessions": [],
    }


def digest(value) -> str:
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False,
                                     allow_nan=False).encode()).hexdigest()


def _text(value: float) -> str:
    return np.format_float_positional(float(value), trim="-")


def _price(value: float) -> str:
    """A stored price as written by the source (float64 noise below 1e-9 dropped)."""
    return _text(round(float(value), 9))


class Terms:
    def __init__(self, market):
        self.market = market

    async def read(self, value: dict) -> dict:
        records = [self.market.instrument_record(item) for item in value["instruments"]]
        windowed = any(name in value for name in ("series", "start", "end"))
        if windowed and not all(name in value for name in ("series", "start", "end")):
            raise MarketError("invalid_input", "Stock sessions need series, start and end together")
        start, end = (int(value["start"]), int(value["end"])) if windowed else (0, 0)
        if windowed and start >= end:
            raise MarketError("invalid_input", "start must be before end")
        selected = {}
        for item in value.get("series", []):
            series = MarketSeries.from_wire(item)
            selected[series.instrumentId] = series
        items = []
        for record, given in zip(records, value["instruments"], strict=True):
            item = await self._terms(record)
            if windowed and record["identityVersion"] == 2 and record["assetClass"] in ("equity", "fund") \
                    and "stock" in item:
                series = selected.get(record["instrumentId"]) or selected.get(given["instrumentId"])
                if series is None:
                    raise MarketError("invalid_input", f"No series given for {record['symbol']}")
                if series.instrumentId != record["instrumentId"]:
                    series = _rebase(series, record)
                item["stock"] = await self._sessions(record, series, item["stock"], start, end,
                                                     bool(value.get("allowMissingPrices")))
            if record["identityVersion"] == 2 and record["venueId"] in ("US", "HK") \
                    and record["assetClass"] in ("equity", "fund"):
                # After the sessions: their window may hold the first stored close.
                await self._price_increment(record, item)
            items.append(item)
        return {"items": items, "asOf": str(now_ns())}

    async def _terms(self, record: dict) -> dict:
        item = {"instrumentId": record["instrumentId"], **{name: None for name in FIELDS}, "unavailable": {}}
        if record["identityVersion"] == 2:
            if record["assetClass"] not in ("equity", "fund"):
                reason = f"Market has no trading terms for {record['assetClass']} listings"
                item["unavailable"].update({name: reason for name in FIELDS})
                return item
            try:
                stock = static_terms(record)
            except MarketError as exc:
                item["unavailable"].update({name: exc.message for name in (*FIELDS, "stock")})
                return item
            item.update(stock=stock, minQuantity=str(stock["orderSize"]["minimum"]),
                        quantityIncrement=str(stock["orderSize"]["increment"]), quantityUnit="shares",
                        contractMultiplier="1", priceIncrement=_cn_tick(record) if record["venueId"] == "CN" else None)
            item["unavailable"]["fees"] = ("Stock commission, order minimum and directional taxes are in stock; "
                                           "maker/taker does not represent them")
            return item
        sources = [source_id for source_id, _row in self.market.registry.rank(record, "bars", history=False)
                   if hasattr(self.market.registry.entry(source_id).source, "terms")]
        if not sources:
            reason = f"No source gives trading terms for {record['venueId']} {record['marketType']} contracts"
            item["unavailable"].update({name: reason for name in FIELDS})
            return item
        target = self.market.data.target(record)
        found = await self.market.registry.call(sources[0], "terms", target)
        item.update({name: found.get(name) for name in FIELDS})
        item["unavailable"].update(found.get("unavailable") or {})
        return item

    async def _price_increment(self, record: dict, item: dict) -> None:
        """US and Hong Kong steps depend on the price: the step at the latest stored daily close."""
        item["priceIncrement"] = None
        if record["venueId"] == "HK" and record["assetClass"] != "equity":
            item["unavailable"]["priceIncrement"] = "Hong Kong exchange traded products use a spread table Market " \
                                                    "does not hold"
            return
        latest = await in_worker(self._latest_close, record)
        if latest is None:
            item["unavailable"]["priceIncrement"] = (f"{record['symbol']}'s step depends on its price and no daily "
                                                     "close is stored yet")
            return
        try:
            item["priceIncrement"] = str(tick(record["venueId"], *latest))
        except MarketError as exc:
            item["unavailable"]["priceIncrement"] = exc.message
            return
        item["unavailable"].pop("priceIncrement", None)

    def _latest_close(self, record: dict) -> tuple[float, str] | None:
        series = ohlcv_series(MarketInstrument.from_wire(record), "1d", adjustment="raw")
        key = self.market.data.plan(record, series, None, local_only=True).key
        pointer = self.market.store.pointer(key)
        if not pointer or pointer.get("last") is None:
            return None
        last = int(pointer["last"])
        rows = self.market.store.read(key, last, last + 1)
        day = calendars.trading_day(record["calendarId"], np.asarray([last], dtype=np.int64))[0]
        return float(rows["close"][-1]), str(day)

    # ── stock sessions ──────────────────────────────────────────────────────

    async def _sessions(self, record: dict, series: MarketSeries, stock: dict, start: int, end: int,
                        allow_missing: bool) -> dict:
        if series.capability != "ohlcv" or series.timeframe != "1d":
            raise MarketError("unsupported", "Stock sessions are daily: give a 1d price series")
        data = self.market.data
        instrument_id = record["instrumentId"]
        # Fill bars and actions for the window, then read both sides from the same stored rows.
        await data.page(record, series, start=start, end=end, limit=MAX_PAGE)
        raw_series = ohlcv_series(MarketInstrument.from_wire(record), "1d", session_scope=series.sessionScope,
                                  adjustment="raw")
        raw_plan = data.plan(record, raw_series, start, local_only=True)
        adjusted_plan = data.plan(record, series, start, local_only=True)
        raw = await in_worker(data.local_rows, raw_plan, start, end)
        if series.adjustment == "raw":
            await data.fill_actions(raw_plan, start, end, READ_REQUEST_LIMIT, "production")
            adjusted = raw
        else:
            adjusted = await in_worker(data.local_rows, adjusted_plan, start, end)
        if not raw or not bars.length(raw):
            raise MarketError("no_data", f"{record['symbol']}: no closed sessions in the window")
        if not np.array_equal(raw["time"], adjusted["time"]):
            raise MarketError("source_unavailable", f"{record['symbol']}: raw and adjusted sessions differ")
        closed_end = min(end, data.closed_until(record, "1d", data.published_through(raw_plan, now_ns())))
        expected = data.bars_between(record, "1d", start, closed_end, series.sessionScope)
        if not allow_missing and bars.length(raw) < expected:
            raise MarketError("no_data", f"{record['symbol']}: {expected - bars.length(raw)} closed sessions in the "
                                         "window have no prices", details={"missingPriceEvidence": True})
        daily = SeriesKey(instrument_id, "ohlcv", "last", raw_plan.key.session_scope, "1d")
        times, ratios, dividends = await in_worker(self.market.actions.factors, instrument_id,
                                                  daily if series.adjustment == "split_dividend" or record["venueId"] == "CN" else None,
                                                  record["calendarId"],
                                                  start - 1 if series.adjustmentAnchor != "earliest" else None,
                                                  end if series.adjustmentAnchor == "earliest" else None)
        actions = self._actions(record, raw, start, end, ZoneInfo(calendars.timezone(record["calendarId"])))
        source = self.market.store.pointer(raw_plan.key) or {}
        revision = data.revision(adjusted_plan)
        # Each session's Decimal arithmetic and text (some 2,500 a name over ten years) runs off the event loop: done on it, a
        # 500-name index held the whole Host for tens of seconds in 65 ms pieces (MK-09).
        return await in_worker(self._session_material, record, series, stock, raw, adjusted, times, ratios, dividends,
                               actions, source, revision)

    @staticmethod
    def _session_material(record, series, stock, raw, adjusted, times, ratios, dividends, actions, source, revision) -> dict:
        price_mult, _volume_mult = bars.multipliers(raw["time"], times, ratios, dividends, series.adjustment,
                                                    series.adjustmentAnchor)
        china = record["venueId"] == "CN"
        steps = dict(zip(times.tolist(), (dividends / ratios).tolist(), strict=True))
        days, session_opens, _, _, _ = calendars._schedule(record["calendarId"])
        dates = calendars.trading_day(record["calendarId"], raw["time"])
        positions = np.searchsorted(days, dates)
        if np.any(positions == len(days)) or np.any(days[positions] != dates):
            raise MarketError("no_data", "No exchange session for a stored trading date")
        sessions = []
        for index in range(bars.length(raw)):
            opened = int(raw["time"][index])
            day = str(dates[index])
            rv = [float(raw[name][index]) for name in ("open", "high", "low", "close")]
            av = [float(adjusted[name][index]) for name in ("open", "high", "low", "close")]
            scale = Decimal(_text(price_mult[index]))
            error = max(abs(Decimal(_text(a)) - Decimal(_price(r)) * scale) for r, a in zip(rv, av, strict=True))
            increment = Decimal(_cn_tick(record)) if china else tick("US", min(rv), day)
            reference = None
            if china and index:
                # The exchange's ex-rights reference: the previous close times the day's adjustment step.
                reference = Decimal(_price(raw["close"][index - 1] * steps.get(opened, 1.0))).quantize(increment)
            sessions.append({
                "sessionDate": day, "openAt": str(int(session_opens[positions[index]])), "closeAt": str(int(raw["close_time"][index])),
                "priceMap": {"scale": str(scale), "offset": "0", "maxError": str(error)},
                "rawOHLC": [_price(x) for x in rv], "adjustedOHLC": [_text(x) for x in av],
                "sourceOHLC": [_price(x) for x in rv], "rawRestorationFactor": "1",
                "rawVolume": _text(raw["volume"][index]), "adjustedVolume": _text(adjusted["volume"][index]),
                "rawReference": None if reference is None else str(reference), "rawLower": None, "rawUpper": None,
                "lower": None, "upper": None, "rawPriceIncrement": str(increment),
                "priceIncrement": str(increment * scale),
                "priceLimitEvidence": ("A-share daily limits need each day's ST status, which Market does not hold yet"
                                       if china else
                                       "US listed equities: daily fixed percentage limit not applicable; intraday "
                                       "LULD excluded"),
            })
        material = {**stock, "sessions": sessions, "corporateActions": actions, "series": series.to_dict(),
                    "sourcePriceBasis": "raw",
                    "sourceVersion": digest({"source": source.get("source"), "revision": revision,
                                             "sessions": [s["rawOHLC"] for s in sessions]}),
                    "acquiredAt": str(now_ns())}
        material["materialVersion"] = digest({key: value for key, value in material.items() if key != "acquiredAt"})
        return material

    def _actions(self, record: dict, raw: bars.Columns, start: int, end: int, zone: ZoneInfo) -> list[dict]:
        """Splits and cash dividends with ex-dates in the window, with the close each dividend step used."""
        doc = self.market.actions.read(record["instrumentId"])
        out = []
        for row in doc["rows"]:
            ex = int(row["exDate"])
            if not start <= ex < end or row["kind"] == "factor":
                continue
            day = datetime.fromtimestamp(ex / 1e9, zone).date().isoformat()
            dividend = row["kind"] == "cash_dividend"
            evidence = {key: str(value) for key, value in row.items() if value is not None}
            if dividend:
                index = int(np.searchsorted(raw["time"], ex, "left")) - 1
                if index >= 0 and row.get("amount"):
                    close = _price(raw["close"][index])
                    evidence.update(referenceClose=close, referenceDate=datetime.fromtimestamp(
                        int(raw["time"][index]) / 1e9, zone).date().isoformat(),
                        dividendFactor=str(1 - Decimal(str(row["amount"])) / Decimal(close)))
            item = {"factId": f"{record['instrumentId']}:{ex}:{row['kind']}", "rowVersion": digest(row),
                    "assetId": record["instrumentId"], "effectiveTime": str(ex),
                    "actionType": "dividend" if dividend else "split",
                    "currency": (row.get("currency") or record.get("currency")) if dividend else None,
                    "exDate": day, "recordDate": row.get("recordDate"), "paymentDate": row.get("payDate"),
                    "listingDate": None, "publishedAt": row.get("knownAt"), "sourceRecord": evidence}
            if dividend:
                item["amount"] = float(row["amount"]) if row.get("amount") else 0.0
            else:
                item["ratio"] = float(row["ratio"])
            out.append(item)
        return out
