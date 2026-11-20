"""Keep original portfolio snapshots and derive explicitly scoped display statistics."""
from __future__ import annotations

import math
from array import array
from bisect import bisect_right
from decimal import Decimal

import numpy as np
import pandas as pd

from . import metrics as M

_CAPTURES = {}
_MARKS = {}
_DAYS = {}
NATIVE_SNAPSHOT_CAPACITY = 1_000_000
HOUR_NS = 3_600_000_000_000
DAY_NS = 24 * HOUR_NS


def attach(engine):
    if id(engine) not in _CAPTURES:
        _CAPTURES[id(engine)] = EquityCapture(engine)


def capture_reports(engine, sampling):
    captured = _CAPTURES.get(id(engine))
    if captured:
        return captured.reports(sampling)
    rows, possibly_truncated = [], False
    for venue in engine.list_venues():
        account = engine.cache.account_for_venue(venue)
        if account is None:
            continue
        snapshots = engine.portfolio.snapshots(account.id)
        possibly_truncated |= len(snapshots) >= NATIVE_SNAPSHOT_CAPACITY
        for snapshot in snapshots:
            rows.extend(snapshot_rows(snapshot))
    reports, summaries = equity_reports(priced(engine, rows), sampling)
    if possibly_truncated:
        for summary in summaries:
            summary.update(totalReturn=None, maximumDrawdown=None, dailySharpe=None,
                warning="Captured after execution; the native snapshot buffer may have omitted earlier observations.")
        reports["Account performance"] = pd.DataFrame(summaries)
        reports = {key: value for key, value in reports.items() if not key.startswith("Account daily returns")}
    return reports, summaries


def release(engine):
    _CAPTURES.pop(id(engine), None)
    _MARKS.pop(id(engine), None)
    _DAYS.pop(id(engine), None)


def record_marks(engine, data):
    """Keep every price the engine is fed; native snapshots hold currencies apart and carry no price."""
    marks, days = _MARKS.setdefault(id(engine), {}), _DAYS.setdefault(id(engine), set())
    for item in data:
        days.add(int(item.ts_init) // DAY_NS)
        kind = type(item).__name__
        if kind == "Bar":
            instrument, price = str(item.bar_type.instrument_id), item.close
        elif kind == "QuoteTick":
            instrument, price = str(item.instrument_id), item.bid_price
        elif kind == "TradeTick":
            instrument, price = str(item.instrument_id), item.price
        else:
            continue
        times, prices = marks.setdefault(instrument, (array("q"), array("d")))
        times.append(int(item.ts_init))
        prices.append(float(price))


def engine_day_sampling(engine, periods):
    """The days the engine was fed data, as the form path takes them from its source table."""
    dates = pd.DatetimeIndex(pd.to_datetime(sorted(_DAYS[id(engine)]), unit="D", utc=True))
    return {"dates": dates, "annualization": periods, "basis": "Observed market-data days (UTC)"}


def priced(engine, rows):
    marks = _MARKS.get(id(engine))
    if not marks:
        return rows
    pairs, precision, balances = {}, {}, {}
    for instrument in engine.cache.instruments():
        if type(instrument).__name__ == "CurrencyPair":
            pairs.setdefault(str(instrument.base_currency), []).append((str(instrument.quote_currency), str(instrument.id)))
            for currency in (instrument.base_currency, instrument.quote_currency):
                precision[str(currency)] = int(currency.precision)
    for venue in engine.list_venues():
        account = engine.cache.account_for_venue(venue)
        if account is not None:
            balances[str(account.id)] = [(int(event.ts_event), {str(balance.total.currency): balance.total.as_decimal() for balance in event.balances})
                                         for event in account.events]
    return revalue(rows, pairs, marks, balances, precision)


def conversion_routes(pairs, held):
    """Fold order and the one pair each base currency is valued through. A base with several pairs
    uses the one quoted in a currency the account started with; if that is not a single pair, or the
    pairs run in a circle, the currency cannot be valued."""
    routes = {}
    for base, options in pairs.items():
        chosen = options if len(options) == 1 else [option for option in options if option[0] in held]
        if len(chosen) == 1:
            routes[base] = chosen[0]
    order, remaining = [], dict(routes)
    while True:
        # A currency is folded only after every currency valued through it.
        ready = [base for base in remaining if all(quote != base for quote, _ in remaining.values())]
        if not ready:
            return order, routes, [base for base in pairs if base not in order]
        order.extend(ready)
        for base in ready:
            del remaining[base]


def revalue(rows, pairs, marks, balances, precision):
    """A cash account holds spot purchases as base-currency balances, which native total equity
    leaves apart from cash. Equity is the balances after every account event, with each base
    currency valued at the last observed price, and re-marked at most hourly between events.
    A held base currency that cannot be valued makes the valuation incomplete."""
    pairs = {base: [option for option in options if option[1] in marks] for base, options in pairs.items()}
    pairs = {base: options for base, options in pairs.items() if options}
    folded = {account: history for account, history in balances.items() if any(base in totals for _, totals in history for base in pairs)}
    if not folded:
        return rows
    series = {}
    for options in pairs.values():
        for _, instrument in options:
            times, prices = np.frombuffer(marks[instrument][0], dtype="int64"), np.frombuffer(marks[instrument][1], dtype="float64")
            order = np.argsort(times, kind="stable")
            series[instrument] = (times[order], prices[order])
    stamps = np.unique(np.concatenate([times for times, _ in series.values()]))
    hourly = stamps[np.append(stamps[1:] // HOUR_NS != stamps[:-1] // HOUR_NS, True)]
    output = [row for row in rows if row["account"] not in folded]
    for account, history in folded.items():
        order, routes, unvalued = conversion_routes(pairs, {currency for currency, amount in history[0][1].items() if amount})
        totals = {}
        for stamp, amounts in history:
            totals[stamp] = amounts
        taken = sorted(totals)
        for stamp in sorted({*(stamp for stamp in taken if stamp >= stamps[0]), *(int(stamp) for stamp in hourly)}):
            if stamp < taken[0]:
                continue
            amounts = dict(totals[taken[bisect_right(taken, stamp) - 1]])
            missing = []
            for base in order:
                if base not in amounts:
                    continue
                quote, instrument = routes[base]
                times, prices = series[instrument]
                index = int(np.searchsorted(times, stamp, side="right")) - 1
                if index < 0:
                    if amounts[base]:
                        missing.append(instrument)
                    continue
                amounts[quote] = amounts.get(quote, Decimal(0)) + amounts.pop(base) * Decimal(repr(float(prices[index])))
            missing.extend(base for base in unvalued if amounts.get(base))
            for currency, amount in amounts.items():
                amount = amount.quantize(Decimal(1).scaleb(-precision[currency]))
                output.append({"account": account, "currency": currency, "timestampNs": str(stamp), "availableAtNs": str(stamp),
                               "snapshotId": f"{account}@{stamp}", "equity": str(amount), "equityNumeric": float(amount),
                               "currencyPrecision": precision[currency], "valuation": "incomplete" if missing else "complete",
                               "missingInstruments": ", ".join(missing), "staleInstruments": "", "staleCurrencies": ""})
    return output


def snapshot_rows(snapshot):
    """No currency conversion or position-return reconstruction here."""
    def value(name):
        item = getattr(snapshot, name)
        return item() if callable(item) else item
    stale = bool(value("is_stale"))
    missing = [str(item) for item in value("unpriced_instruments")]
    rows = []
    for money in value("total_equity"):
        currency = str(money.currency)
        rows.append({"account": str(value("account_id")), "currency": currency,
                     "timestampNs": str(value("ts_event")), "availableAtNs": str(value("ts_init")),
                     "snapshotId": str(value("event_id")), "equity": str(money.as_decimal()),
                     "equityNumeric": float(money.as_decimal()), "currencyPrecision": int(money.currency.precision) if hasattr(money.currency, "precision") else None,
                     "valuation": "incomplete" if stale or missing else "complete",
                     "missingInstruments": ", ".join(missing),
                     "staleInstruments": ", ".join(map(str, value("stale_instruments"))),
                     "staleCurrencies": ", ".join(map(str, value("stale_currencies")))})
    return rows


def observed_day_sampling(config, frame, periods):
    """Only days with real primary-source observations enter the daily sample."""
    times = frame.ts_init.map(int)
    if config.get("startNs") is not None:
        times = times[times >= int(config["startNs"])]
    if config.get("endNs") is not None:
        times = times[times <= int(config["endNs"])]
    dates = pd.to_datetime(times.tolist(), unit="ns", utc=True).normalize().unique().sort_values()
    return {"dates": dates, "annualization": periods, "basis": "Observed market-data days (UTC)"}


def equity_reports(rows, sampling):
    """Keep all equity marks; restrict daily returns to observed source days."""
    if not rows:
        return {}, []
    frame = pd.DataFrame(rows).drop_duplicates(["snapshotId", "currency"], keep="last")
    frame = frame.sort_values("timestampNs", key=lambda values: values.map(int), kind="stable")
    frames, summaries, returns = [], [], {}
    for (account, currency), selected in frame.groupby(["account", "currency"], sort=False):
        selected = selected.copy()
        # At a shared clock time the final native observation is the valuation at that time.
        selected = selected.drop_duplicates("timestampNs", keep="last")
        amounts = selected.equity.map(Decimal)
        selected["equityNumeric"] = amounts.map(float)
        complete = selected.valuation.eq("complete").all()
        positive = bool((amounts > 0).all())
        falls = M.drawdown_path(amounts, amounts.iloc[0])
        selected["drawdown"] = [fall if row.valuation == "complete" else None for fall, row in zip(falls, selected.itertuples(), strict=True)]
        total_return = float(amounts.iloc[-1] / amounts.iloc[0] - 1) if complete and positive else None
        daily = pd.Series([float(value) for value in amounts], index=pd.to_datetime(selected.timestampNs.map(int).tolist(), unit="ns", utc=True))
        daily = daily.resample("1D").last()
        annualization, basis = sampling["annualization"], sampling["basis"]
        # Native timers produce weekend marks. Reindex to actual source
        # observation days, not a generic weekday calendar. Never forward
        # fill a missing valuation or remove observed zero-return days.
        daily = daily.reindex(sampling["dates"])
        missing_days = int(daily.isna().sum())
        daily = daily.pct_change(fill_method=None).dropna()
        if complete and positive and len(daily):
            returns[f"Account daily returns · {account} · {currency}"] = pd.DataFrame({
                "timestampNs": [str(time.value) for time in daily.index], "return": daily.to_numpy(),
                "account": account, "currency": currency})
        deviation = float(daily.std(ddof=1)) if len(daily) >= 2 else 0
        summaries.append({"account": account, "currency": currency, "initialEquity": str(amounts.iloc[0]),
                          "finalEquity": str(amounts.iloc[-1]), "equityChange": str(amounts.iloc[-1] - amounts.iloc[0]),
                          "initialEquityNumeric": float(amounts.iloc[0]), "finalEquityNumeric": float(amounts.iloc[-1]),
                          "equityChangeNumeric": float(amounts.iloc[-1] - amounts.iloc[0]),
                          "currencyPrecision": int(selected.currencyPrecision.iloc[0]) if "currencyPrecision" in selected and pd.notna(selected.currencyPrecision.iloc[0]) else None,
                          "totalReturn": total_return, "maximumDrawdown": min(selected.drawdown.dropna()) if complete and positive else None,
                          "dailySharpe": float(daily.mean()) / deviation * math.sqrt(annualization) if annualization and complete and positive and deviation > 0 else None,
                          "dailyObservations": len(daily), "snapshots": len(selected), "valuation": "complete" if complete else "incomplete",
                          "returnBasis": "Account equity change; no external cash-flow adjustment",
                          "annualization": f"{basis}, {annualization} observations/year; zero risk-free rate" if annualization else f"{basis}; not annualized because the topic has no market",
                          "annualizationPeriods": annualization, "samplingBasis": basis,
                          "warning": "Some account marks are missing or stale; performance is unavailable." if not complete else f"{missing_days} observed days have no daily equity valuation; returns across those gaps are excluded." if missing_days else ""})
        frames.append(selected)
    return {"Account equity": pd.concat(frames, ignore_index=True), "Account performance": pd.DataFrame(summaries), **returns}, summaries


class EquityCapture:
    """Retain native snapshots at existing event boundaries without adding timers."""
    def __init__(self, engine):
        from nautilus_trader.common import DataActor, DataActorConfig
        from nautilus_trader.model import ActorId
        capture = self
        self.engine = engine
        self.portfolio = engine.portfolio
        self.rows = []
        self.seen = set()
        self.last_capture = 0
        self.incomplete = False
        self.cache = engine.cache
        self.venues = engine.list_venues()

        class SnapshotReader(DataActor):
            def __init__(self):
                super().__init__(DataActorConfig(actor_id=ActorId("TALYX-EQUITY")))

            def on_start(self):
                for venue in capture.venues:
                    account = self.cache.account_for_venue(venue)
                    if account is None:
                        continue
                    for snapshot in capture.portfolio.snapshots(account.id):
                        capture.receive(snapshot)
                self.subscribe_topic("events.portfolio.*", capture.receive)
                for instrument in self.cache.instruments():
                    self.subscribe_quotes(instrument.id)
                    self.subscribe_trades(instrument.id)

            def on_quote(self, quote):
                capture.observe(quote.ts_init)

            def on_trade(self, trade):
                capture.observe(trade.ts_init)

            def on_reset(self):
                capture.rows.clear()
                capture.seen.clear()
                capture.last_capture = 0
                capture.incomplete = False

            def on_stop(self):
                pass

        self.actor = SnapshotReader()
        engine.add_actor(self.actor)

    def receive(self, snapshot):
        """Rows of a snapshot not read before: the ring is read every simulated hour and holds every snapshot so far, so one already
        seen is known by its event id before its rows are built."""
        event = snapshot.event_id() if callable(snapshot.event_id) else snapshot.event_id
        if str(event) in self.seen:
            return
        rows = snapshot_rows(snapshot)
        if rows:
            self.seen.add(rows[0]["snapshotId"])
            self.rows.extend(rows)

    def observe(self, timestamp):
        if int(timestamp) - self.last_capture >= 3_600_000_000_000:
            self.read_ring()
            self.last_capture = int(timestamp)

    def read_ring(self):
        for venue in self.venues:
            account = self.cache.account_for_venue(venue)
            if account is None:
                continue
            snapshots = self.portfolio.snapshots(account.id)
            if len(snapshots) >= NATIVE_SNAPSHOT_CAPACITY and not any(str(snapshot.event_id() if callable(snapshot.event_id) else snapshot.event_id) in self.seen for snapshot in snapshots):
                self.incomplete = True
            for snapshot in snapshots:
                self.receive(snapshot)

    def reports(self, sampling):
        self.read_ring()
        reports, summaries = equity_reports(priced(self.engine, self.rows), sampling)
        if self.incomplete:
            for summary in summaries:
                summary.update(totalReturn=None, maximumDrawdown=None, dailySharpe=None,
                    warning="The native snapshot buffer omitted observations. Use a coarser portfolio sampling interval and run again.")
            reports["Account performance"] = pd.DataFrame(summaries)
            reports = {key: value for key, value in reports.items() if not key.startswith("Account daily returns")}
        return reports, summaries
