"""Reading series: local first, fill only the missing part, adjust and aggregate on read.

A read picks the series' source (the one already holding its rows, else the best
ranked) and the base period: daily and longer from daily bars; intraday from the
coarsest base period that divides the request and reaches back far enough. The
missing spans of the window are estimated in source requests; more than the read
bound fails with ``fill_too_large`` before any request, except for a page before a time,
which fills the newest part the bound allows and returns fewer rows (more before them). A failed fill still returns
local rows with ``fill.problem``.
"""

from __future__ import annotations

import asyncio
import math
from dataclasses import dataclass

import numpy as np
from talyx_sdk.i18n import t

from vocabulary.identity import timeframe_nanoseconds
from vocabulary.instruments import MarketInstrument, MarketSeries, ohlcv_series

from . import bars, calendars
from .common import DAY_NS, MarketError, iso_of_ns, now_ns, problem_of, time_range
from .planner import RETRY_REFUSED, Layers, Unit
from .routing import duration_ns, in_worker, period_start
from .sources.base import Target
from .store import SeriesKey, Spans

READ_REQUEST_LIMIT = 50
# A live session's window (up to 10,000 rows, 30,000 scrolled back) is filled in the background with its own bound:
# 10,000 rows at OKX's 100 rows a page for mark and index prices is 100 requests.
WINDOW_FILL_LIMIT = 400
MAX_PAGE = 10_000
PRICE_KINDS = {"ohlcv": "bars"}
SERIES_CAPABILITIES = {"funding_rate", "open_interest", "long_short_account_ratio", "top_trader_account_ratio",
                       "top_trader_position_ratio", "taker_volume", "option_metrics"}


@dataclass
class Plan:
    instrument: dict
    series: MarketSeries
    kind: str
    key: SeriesKey
    base: str | None
    source_id: str | None
    period: dict | None


@dataclass
class Fill:
    state: str
    problem: dict | None = None
    requests: int = 0

    def wire(self) -> dict:
        return {"state": self.state, "problem": self.problem}


def kind_of(capability: str) -> str:
    return PRICE_KINDS.get(capability, capability)


class Data:
    def __init__(self, market):
        self.market = market
        self.store = market.store
        self._locks: dict[SeriesKey, asyncio.Lock] = {}

    # ── planning ────────────────────────────────────────────────────────────

    def source_for(self, instrument: dict, kind: str, timeframe: str | None, key: SeriesKey | None) -> str | None:
        """The series keeps the source holding its rows; otherwise the best ranked one."""
        if key is not None:
            pointer = self.store.pointer(key)
            if pointer and pointer.get("source") and pointer["parts"]:
                return pointer["source"]
        ranked = self.market.registry.rank(instrument, kind, timeframe)
        return ranked[0][0] if ranked else None

    def plan(self, instrument: dict, series: MarketSeries, start: int | None, *, local_only: bool = False,
             environment: str = "production") -> Plan:
        kind = kind_of(series.capability)
        target_tf = series.timeframe
        if series.importId:
            key = SeriesKey(instrument["instrumentId"], series.capability, series.priceBasis, series.sessionScope,
                            target_tf, series.importId, environment)
            return Plan(instrument, series, kind, key, target_tf, f"import:{series.importId}", None)
        if kind != "bars":
            key = self._key(instrument, series, target_tf, environment)
            source = self.source_for(instrument, kind, target_tf, key)
            period = None
            if source:
                period = next(iter(self.market.registry.periods(source, instrument, kind)), None)
            # Observations (funding, open interest) keep the series' timeframe; requests are sized by the
            # source's period.
            base = target_tf or (period["timeframe"] if period else None)
            return Plan(instrument, series, kind, key, base, source, period)
        if not bars.is_intraday(target_tf):
            key = self._key(instrument, series, "1d", environment)
            source = self.source_for(instrument, kind, "1d", key)
            if source is not None or calendars.canonical(instrument["calendarId"]) != calendars.CONTINUOUS:
                period = self._period(source, instrument, "1d")
                return Plan(instrument, series, kind, key, "1d", source, period)
            # A venue that cuts no UTC days (HTX, Deribit) gives no daily bars: its days come from intraday bars.
        # Intraday: coarsest dividing base period that is local (local_only) or reaches back to start.
        local = []
        for candidate in self._local_bases(instrument, series, environment):
            if bars.divides(candidate, target_tf, instrument["calendarId"]):
                local.append(candidate)
        if local_only and local:
            local.sort(key=lambda tf: timeframe_nanoseconds(tf), reverse=True)
            if start is not None:
                for base in local:
                    pointer = self.store.pointer(self._key(instrument, series, base, environment))
                    if pointer and pointer["first"] is not None and pointer["first"] <= start:
                        return self._plan_base(instrument, series, base, environment)
            return self._plan_base(instrument, series, local[0], environment)
        # Ranking only tells daily from intraday periods: any intraday period stands for them all.
        source = self.source_for(instrument, kind, target_tf if bars.is_intraday(target_tf) else "1m", None)
        if source is None:
            if local:
                return self._plan_base(instrument, series, max(local, key=timeframe_nanoseconds), environment)
            return Plan(instrument, series, kind, self._key(instrument, series, target_tf, environment), target_tf, None, None)
        periods = [p for p in self.market.registry.periods(source, instrument, kind)
                   if bars.is_intraday(p["timeframe"]) and bars.divides(p["timeframe"], target_tf, instrument["calendarId"])]
        if not periods:
            raise MarketError("unsupported", t("No source gives {target_tf} bars for {symbol}", target_tf=target_tf, symbol=instrument['symbol']))
        now = now_ns()
        periods.sort(key=lambda p: timeframe_nanoseconds(p["timeframe"]), reverse=True)
        basis = series.priceBasis
        chosen = next((p for p in periods if start is None or period_start(p, basis, now) <= start), None)
        if chosen is None:
            chosen = min(periods, key=lambda p: period_start(p, basis, now))
        key = self._key(instrument, series, chosen["timeframe"], environment)
        pointer = self.store.pointer(key)
        if pointer and pointer.get("source") and pointer["parts"]:
            source = pointer["source"]
        return Plan(instrument, series, kind, key, chosen["timeframe"], source, chosen)

    def _plan_base(self, instrument, series, base, environment):
        key = self._key(instrument, series, base, environment)
        pointer = self.store.pointer(key)
        source = pointer.get("source") if pointer else None
        return Plan(instrument, series, "bars", key, base, source, self._period(source, instrument, base))

    def _period(self, source: str | None, instrument: dict, timeframe: str) -> dict | None:
        if source is None or source.startswith("import:") or source not in self.market.registry.entries:
            return None
        return next((p for p in self.market.registry.periods(source, instrument, "bars")
                     if p["timeframe"] == timeframe), None)

    def _key(self, instrument: dict, series: MarketSeries, timeframe: str | None, environment: str) -> SeriesKey:
        return SeriesKey(instrument["instrumentId"], series.capability, series.priceBasis, series.sessionScope,
                         timeframe, series.importId, environment)

    def _local_bases(self, instrument: dict, series: MarketSeries, environment: str) -> list[str]:
        found = []
        for key in self.store.keys(instrument["instrumentId"], environment=environment):
            if (key.capability == series.capability and key.price_basis == series.priceBasis
                    and key.session_scope == series.sessionScope and key.import_id == series.importId
                    and key.timeframe and bars.is_intraday(key.timeframe)):
                found.append(key.timeframe)
        return found

    # ── estimating ──────────────────────────────────────────────────────────

    def bars_between(self, instrument: dict, timeframe: str, start: int, end: int, scope: str) -> int:
        step = timeframe_nanoseconds(timeframe)
        calendar = instrument["calendarId"]
        if end <= start:
            return 0
        if calendars.canonical(calendar) == calendars.CONTINUOUS and step is not None:
            return math.ceil((end - start) / step)
        if step is not None and step >= DAY_NS or step is None:
            opens, _closes = calendars.bar_grid(calendar, "1d", start, end)
            return len(opens)
        # Intraday on an exchange calendar: count sessions times bars per session.
        opens, _closes = calendars.bar_grid(calendar, "1d", start, end)
        per_day = max(1, math.ceil(6.5 * 3600 * 10**9 / step))
        return len(opens) * per_day

    def publish_delay(self, source_id: str | None, instrument: dict, kind: str, timeframe: str | None) -> int:
        """How long after a period closes the source publishes it as final (its declaration for that period)."""
        if source_id is None or source_id not in self.market.registry.entries:
            return 0
        row = self.market.registry.row(source_id, instrument, kind, timeframe)
        return duration_ns(row["publishDelay"]) if row else 0

    def published_through(self, plan: Plan, now: int) -> int:
        """The series' one publish cutoff: bars that closed at or before this instant are final at the source. Reads,
        fetches, live rows, coverage and exports (and Quant's "Latest close", through coverage) all go by it."""
        return now - self.publish_delay(plan.source_id, plan.instrument, plan.kind, plan.base)

    def closed_until(self, instrument: dict, timeframe: str, moment: int) -> int:
        """Rows before the returned instant had closed at ``moment`` (pass the publish cutoff for "published")."""
        step = timeframe_nanoseconds(timeframe)
        calendar = instrument["calendarId"]
        if calendars.canonical(calendar) == calendars.CONTINUOUS and step is not None:
            return moment - moment % step if step < DAY_NS else moment - moment % DAY_NS
        opens, closes = calendars.bar_grid(calendar, timeframe if bars.is_intraday(timeframe) else "1d",
                                           moment - 10 * DAY_NS, moment + DAY_NS)
        forming = np.flatnonzero((opens <= moment) & (closes > moment))
        return int(opens[forming[0]]) if len(forming) else moment

    def last_closed_open(self, calendar: str, timeframe: str, moment: int) -> int:
        """When the newest bar of this calendar and period closed by ``moment`` opened (outside trading hours: the last
        session's, not the one before ``moment``)."""
        step = timeframe_nanoseconds(timeframe)
        if calendars.canonical(calendar) == calendars.CONTINUOUS and step is not None:
            grid = step if step < DAY_NS else DAY_NS
            return moment - moment % grid - grid
        opens, closes = calendars.bar_grid(calendar, timeframe if bars.is_intraday(timeframe) else "1d",
                                           moment - 10 * DAY_NS, moment + DAY_NS)
        done = opens[closes <= moment]
        return int(done[-1]) if len(done) else moment - 10 * DAY_NS

    def reach_end(self, plan: Plan, end: int, now: int) -> int:
        """Where [.., end) stops for the source: bars count once their period has closed and passed the publish
        cutoff; funding and open interest are points at their instant."""
        cutoff = self.published_through(plan, now)
        return min(end, self.closed_until(plan.instrument, plan.base or "1d", cutoff) if plan.kind == "bars"
                   else cutoff + 1)

    def layers(self, plan: Plan, now: int) -> Layers | None:
        """The source's request layers for this series (its files, then its API); None when it declares none."""
        if plan.period is None:
            return None
        step = timeframe_nanoseconds(plan.base) if plan.base else None
        continuous = calendars.canonical(plan.instrument["calendarId"]) == calendars.CONTINUOUS
        entry = self.market.registry.entries.get(plan.source_id)
        pieces = entry.coverage["concurrency"] if entry is not None and entry.coverage else 1
        return Layers(plan.period, plan.key.price_basis, now, pieces,
                      lambda a, b: self.bars_between(plan.instrument, plan.base or "1d", a, b, plan.key.session_scope),
                      step if continuous else None)

    @staticmethod
    def base_end(plan: Plan, start: int, end: int) -> int:
        """Include the entire last target bucket whose open is in the requested window."""
        if plan.series is None or plan.kind != "bars" or plan.base == plan.series.timeframe or end <= start:
            return end
        target = plan.series.timeframe
        calendar = plan.instrument["calendarId"]
        if not bars.is_intraday(target):
            opened = int(calendars.period_starts(calendar, target, np.array([end - 1]))[0])
            return max(end, calendars.period_end(calendar, target, opened)) if opened >= start else end
        step = timeframe_nanoseconds(target)
        _, closes = calendars.bar_grid(calendar, target, max(start, end - step), end, plan.series.sessionScope)
        return max(end, int(closes[-1])) if len(closes) else end

    def missing(self, plan: Plan, start: int, end: int, retry_unavailable: bool = False,
                *, now: int | None = None) -> list[tuple[int, int]]:
        """What to ask the source for in [start, end): not stored, within what some layer of the source reaches, and
        not recorded unavailable with its retry time still ahead (unless ``retry_unavailable``: a fetch the user or
        AI asked for)."""
        pointer = self.store.pointer(plan.key)
        if pointer and not pointer["parts"] and pointer["source"] != plan.source_id:
            pointer = None  # an empty response from another source says nothing about this source
        spans = pointer["spans"] if pointer else []
        if now is None:
            now = now_ns()
        end = self.reach_end(plan, self.base_end(plan, start, end), now)
        # Vocabulary times are non-negative nanoseconds: nothing before 1970 is asked for or kept (IBM's history
        # from 1962 starts at 1970 here).
        start = max(start, 0)
        if end <= start:
            return []
        layers = self.layers(plan, now)
        reach = layers.reach() if layers is not None else [(start, end)]
        waiting = [] if retry_unavailable or pointer is None else \
            [[a, b, "have"] for a, b, retry, _problem in pointer.get("unavailable", []) if int(retry) > now]
        held = sorted(spans + waiting) if waiting else spans
        return [(max(a, lo), min(b, hi)) for lo, hi in reach for a, b in Spans.missing(held, start, end)
                if a < hi and lo < b]

    # ── filling ─────────────────────────────────────────────────────────────

    def target(self, instrument: dict, environment: str, source_id: str) -> Target:
        codes = self.market.identity.codes(instrument["instrumentId"]) if instrument["identityVersion"] == 2 else []
        keys = self.market.identity.external_ids(instrument["instrumentId"])
        return Target(instrument, codes, keys.get(source_id), environment=environment)

    def lock(self, key: SeriesKey) -> asyncio.Lock:
        return self._locks.setdefault(key, asyncio.Lock())

    async def fill(self, plan: Plan, start: int, end: int, *, limit: int | None = READ_REQUEST_LIMIT,
                   progress=None, retry_unavailable: bool = False) -> Fill:
        """Ask the source for what [start, end) is missing, unit by unit at once (``planner``), committing every
        answer as it arrives. A file that is missing or cannot be had falls to the layers below it. A span no layer
        could give, or the source refused, is recorded unavailable with when to ask again; one another failure left
        is the fill's problem and asked for again by the next fill."""
        if plan.source_id is None or plan.source_id.startswith("import:"):
            return Fill("not_needed")
        async with self.lock(plan.key):
            pointer = self.store.pointer(plan.key)
            if plan.kind == "bars" and pointer and pointer["parts"] and pointer["source"] != plan.source_id:
                # A switch may have finished while this fill waited for the series lock.
                plan.source_id = pointer["source"]
                plan.period = self._period(plan.source_id, plan.instrument, plan.base)
            # Pointer, actions and identity reads are files and SQLite: in a worker, never on the event loop.
            now = now_ns()
            gaps = await in_worker(self.missing, plan, start, end, retry_unavailable, now=now)
            if not gaps:
                return Fill("not_needed")
            layers = self.layers(plan, now)
            units = layers.units(gaps) if layers is not None else [Unit(a, b) for a, b in gaps]
            estimate = layers.requests(units) if layers is not None else len(units)
            if limit is not None and estimate > limit:
                raise MarketError("fill_too_large",
                                  t("Filling this read takes about {estimate} requests to {source_id}; download it with the fetch action first", estimate=estimate, source_id=plan.source_id),
                                  source_id=plan.source_id, details={"estimatedRequests": estimate})
            target = await in_worker(self.target, plan.instrument, plan.key.environment, plan.source_id)
            requests, problems = [0], []

            async def ask(cursor: int, stop: int, archive):
                history = await self.market.registry.call(plan.source_id, "history", target, plan.kind, plan.base,
                                                          plan.series.priceBasis, cursor, stop, archive)
                requests[0] += 1
                if plan.kind == "bars" and bars.inconsistent(history.columns).any():
                    # Bars whose high and low do not contain open and close are asked for once more; what is still
                    # inconsistent then is contained when stored (commit).
                    history = await self.market.registry.call(plan.source_id, "history", target, plan.kind,
                                                              plan.base, plan.series.priceBasis, cursor, stop, archive)
                    requests[0] += 1
                if progress:
                    progress(requests[0])
                return history

            async def failed(a: int, b: int, exc: MarketError) -> None:
                if exc.code == "forbidden":  # a refusal (HTTP 451, 403): not asked again for a while
                    await self.unavailable(plan, a, b, now_ns() + RETRY_REFUSED, exc.problem())
                problems.append(exc.problem())

            async def run(unit: Unit, cause: MarketError | None = None) -> None:
                """``cause``: why the file above this unit could not be had; a part no layer below reaches fails
                with it (it is not a file still to come)."""
                if unit.retry_at is not None:
                    if cause is not None:
                        await failed(unit.start, unit.end, cause)
                        return
                    problem = MarketError("no_data", t("{source_id} has no file for this span yet and its API reaches back to {api_from}", source_id=plan.source_id, api_from=iso_of_ns(layers.api_from)[:10]),
                                          source_id=plan.source_id).problem()
                    await self.unavailable(plan, unit.start, unit.end, unit.retry_at, problem)
                    problems.append(problem)
                    return
                cursor = unit.start
                try:
                    while cursor is not None and cursor < unit.end:
                        try:
                            history, lost = await ask(cursor, unit.end, unit.archive), None
                        except MarketError as exc:
                            if exc.status in (403, 451) or unit.archive is None:
                                raise
                            # A file that cannot be had (its host unreachable or refusing, the file unreadable)
                            # falls to the layers below it, as a missing one does.
                            history, lost = None, exc
                        if lost is not None or history.missing:
                            await asyncio.gather(*(run(item, lost) for item in layers.below(Unit(cursor, unit.end,
                                                                                                 unit.archive))))
                            return
                        stop = history.next if history.next is not None else unit.end
                        await self.commit(plan, history, cursor, stop)
                        if unit.archive is not None and history.empty_reason is None:
                            await asyncio.gather(*(run(item) for item in layers.unanswered(
                                Unit(cursor, min(stop, unit.end), unit.archive),
                                history.columns["time"] if bars.length(history.columns) else [], history.none)))
                        cursor = history.next
                except MarketError as exc:
                    await failed(cursor, unit.end, exc)

            await asyncio.gather(*(run(unit) for unit in units))
            if problems:
                return Fill("partial" if requests[0] else "failed", problems[0], requests[0])
            return Fill("done", None, requests[0])

    async def unavailable(self, plan: Plan, start: int, end: int, retry_at: int, problem: dict) -> None:
        await in_worker(self.store.write, plan.key, None, unavailable=[(start, end, retry_at, problem)],
                        source=plan.source_id)

    def held(self, plan: Plan, start: int, end: int) -> tuple[Fill, int]:
        """A live session's opening page, without asking the source: where the newest rows held without a break start,
        and ``Fill("filling")`` when anything of [start, end) is missing (the newest published bars, or older parts),
        for the session to fill in the background."""
        now = now_ns()
        gaps = self.missing(plan, start, end, now=now)
        if not gaps:
            return Fill("not_needed"), start
        # Gaps ending before ``newest`` are older parts of the window; one reaching it is the newest bars still to come.
        newest = self.reach_end(plan, end, now)
        older = [gap for gap in gaps if gap[1] < newest]
        return Fill("filling"), max(start, older[-1][1]) if older else start

    async def fill_newest(self, plan: Plan, start: int, end: int, environment: str) -> Fill:
        """The newest source page of [start, end) when the newest published bar is missing (a live session fills it
        first, before the rest of its window): the rows one API request gives back from the newest bar, within its
        depth. ``Fill("not_needed")`` when the newest bar is held."""
        now = now_ns()
        gaps = await in_worker(self.missing, plan, start, end, now=now)
        if not gaps or gaps[-1][1] < self.reach_end(plan, end, now):
            return Fill("not_needed")
        layers = self.layers(plan, now)
        gap_start = gaps[-1][0] if layers is None else max(gaps[-1][0], min(layers.api_from, gaps[-1][1] - 1))
        page_start = gap_start if layers is None or layers.rows is None else \
            max(gap_start, self.window_start(plan.instrument, plan.base or "1d", gaps[-1][1], layers.rows))
        result = await self.fill(plan, page_start, end, limit=None)
        if plan.series.adjustment in ("split", "split_dividend") and plan.kind == "bars":
            await self.fill_actions(plan, page_start, end, READ_REQUEST_LIMIT, environment)
        return result

    async def commit(self, plan: Plan, history, start: int, end: int) -> None:
        identity = self.market.identity
        instrument_id = plan.instrument["instrumentId"]
        revision = await in_worker(self.market.next_revision, instrument_id)
        actions = history.extra.get("actions")
        if actions is not None:
            # Actions are known for the span fetched, like the rows.
            actions = [row for row in actions if start <= int(row["exDate"]) < end]
            await in_worker(self.market.actions.write, instrument_id, actions, start=start, end=end,
                            source=plan.source_id, revision=revision)
        columns = await in_worker(self._store_answer, plan, history, start, end, revision)
        if bars.length(columns) and plan.instrument["identityVersion"] == 2 and plan.kind == "bars":
            if await in_worker(identity.observe_first_bar, instrument_id, int(columns["time"][0])):
                self.market.instrument_changed(instrument_id)

    def _store_answer(self, plan: Plan, history, start: int, end: int, revision: int) -> bars.Columns:
        """One source answer for [start, end) into the series.

        Only rows inside the span asked for are kept (the span ends at the publish cutoff, so nothing unpublished
        is stored as final). A tail the source is late with stays unfetched (see ``late_from``). Bars whose high and
        low do not contain open and close keep the source's open and close, are widened just enough, and their
        original values are kept in the series' record.
        """
        columns = history.columns
        if bars.length(columns):
            times = columns["time"]
            columns = bars.take(columns, (times >= max(start, 0)) & (times < end))  # nothing before 1970
        none = [(start, end)] if history.empty_reason else \
            [(max(a, start), min(b, end)) for a, b in history.none if b > start and a < end]
        fetched = [] if history.empty_reason else [(start, end)]
        repaired = []
        late = self.late_from(plan, columns, start, end) if plan.kind == "bars" else \
            self.late_point_from(plan, columns, start, end)
        if late is not None:
            fetched = [(start, late)] if late > start else []
            none = [(a, min(b, late)) for a, b in none if a < late]
        if plan.kind == "bars":
            if bars.length(columns):
                bad = bars.inconsistent(columns)
                if bad.any():
                    repaired = [{"time": str(int(columns["time"][i])),
                                 **{name: bars.decimal_text(columns[name][i:i + 1])[0]
                                    for name in ("open", "high", "low", "close")}} for i in np.flatnonzero(bad)]
                    columns = bars.contain(columns)
        self.store.write(plan.key, columns, fetched=fetched, none=none, source=plan.source_id, revision=revision,
                         repaired=repaired)
        return columns

    def late_from(self, plan: Plan, columns: bars.Columns, start: int, end: int) -> int | None:
        """Where an answer about [start, end) stops counting, when the source is late with the newest published bar.

        The series (with this answer) holds the bar before the newest published one but not that one: the source has
        not put it out yet, so from just after the last bar held the span stays unfetched and is asked for again,
        never recorded as "the source has none". Only the newest bar: older silence is a real gap (a delisted name is
        not asked again forever). None when the answer covers what it should.
        """
        calendar = plan.instrument["calendarId"]
        newest = self.last_closed_open(calendar, plan.base, self.published_through(plan, now_ns()))
        if not start <= newest < end:
            return None
        last = self.last_held(plan, columns)
        before_newest = self.last_closed_open(calendar, plan.base, newest)
        if last is None or not before_newest <= last < newest:
            return None
        return max(start, last + 1)

    def last_held(self, plan: Plan, columns: bars.Columns) -> int | None:
        """The newest row the series holds with this answer: the answer's last row or the stored pointer."""
        pointer = self.store.pointer(plan.key)
        held = [int(columns["time"][-1])] if bars.length(columns) else []
        if pointer and pointer["last"] is not None:
            held.append(int(pointer["last"]))
        return max(held, default=None)

    def late_point_from(self, plan: Plan, columns: bars.Columns, start: int, end: int) -> int | None:
        """``late_from`` for points (funding, open interest): when an answer reaching the publish cutoff ends less
        than two periods after the series' newest point, the source may not have put the next one out yet, so the
        span after that point stays unfetched and is asked for again. Older silence is a real gap."""
        step = timeframe_nanoseconds(plan.base)
        if end + step <= self.published_through(plan, now_ns()):
            return None
        last = self.last_held(plan, columns)
        if last is None or last >= end or end - last >= 2 * step:
            return None
        return max(start, last + 1)

    # ── reading ─────────────────────────────────────────────────────────────

    async def read(self, value: dict, *, encoded: bool = False) -> dict:
        """One page; ``encoded``: its columns as ``TextColumn`` (the public answer), else lists."""
        instrument = await in_worker(self.market.instrument_record, value["instrument"])
        series = MarketSeries.from_wire(value["series"])
        if series.instrumentId != value["instrument"]["instrumentId"]:
            raise MarketError("invalid_input", t("series.instrumentId does not name the instrument"))
        if instrument["instrumentId"] != series.instrumentId:
            series = _rebase(series, instrument)
        limit = min(int(value.get("limit", 1000)), MAX_PAGE)
        fill_mode = value.get("fill", "await")
        environment = value.get("environment", "production")
        start = int(value["start"]) if value.get("start") else None
        end = int(value["end"]) if value.get("end") else None
        before = int(value["before"]) if value.get("before") else None
        page = await self.page(instrument, series, start=start, end=end, before=before, limit=limit,
                               fill=fill_mode, environment=environment, encoded=encoded)
        return page

    async def page(self, instrument: dict, series: MarketSeries, *, start=None, end=None, before=None, limit=1000,
                   fill="await", environment="production", request_limit=READ_REQUEST_LIMIT, extra=None,
                   encoded=False) -> dict:
        return await self._page(instrument, series, start, end, before, limit, fill, environment, request_limit,
                                extra, encoded)

    def window(self, instrument: dict, series: MarketSeries, start, end, before, limit) -> tuple[int, int, bool]:
        """[start, end) a page reads and whether it is the latest page (no start given)."""
        now = now_ns()
        window_end = end if end is not None else (before if before is not None else now + DAY_NS)
        if start is not None:
            return start, window_end, False
        return self.window_start(instrument, series.timeframe or "1d", min(window_end, now), limit), window_end, True

    async def _page(self, instrument, series, start, end, before, limit, fill_mode, environment, request_limit,
                    extra=None, encoded=False):
        """``fill_mode``: "await" fills the window first; "none" reads what is stored; "filling" (a live session's
        replaces until its window is filled) answers the newest rows held without a break without asking the source,
        with ``fill.state`` "filling" when anything of the window is missing (the session fills it in the background)."""
        target_tf = series.timeframe
        start, window_end, latest = self.window(instrument, series, start, end, before, limit)
        # Planning lists the instrument's stored series and reads their pointers: in a worker.
        plan = await in_worker(self.plan, instrument, series, start, local_only=(fill_mode == "none"), environment=environment)
        if latest and fill_mode == "none":
            # Without filling, the latest page is the latest stored rows, however old.
            pointer = await in_worker(self.store.pointer, plan.key)
            if pointer and pointer.get("last") is not None and int(pointer["last"]) < start:
                start = self.window_start(instrument, target_tf or "1d", min(window_end, int(pointer["last"]) + 1),
                                          limit)
        result = Fill("skipped" if fill_mode == "none" else "not_needed")
        if fill_mode == "await" and plan.source_id is not None:
            while True:
                try:
                    result = await self.fill(plan, start, window_end, limit=request_limit)
                    break
                except MarketError as exc:
                    # A page before a time (scrolling back, a chart's intrabars reaching back) fills what the read
                    # bound allows, newest first, and says there is more before it: the next page asks for the rest.
                    if exc.code != "fill_too_large" or before is None or window_end - start < 2:
                        raise
                    share = request_limit / exc.details["estimatedRequests"]
                    start = window_end - max(1, int((window_end - start) * share * 0.9))
            if series.adjustment in ("split", "split_dividend") and plan.kind == "bars":
                await self.fill_actions(plan, start, window_end, request_limit, environment)
        elif fill_mode == "filling" and plan.source_id is not None:
            # Pointer and bar-grid reads: in a worker.
            result, start = await in_worker(self.held, plan, start, window_end)
        unwritten = extra(plan) if callable(extra) else None

        def assemble():  # stored rows, the pointer's files and up to 10,000 rows of text: off the event loop, in one go
            nonlocal result
            columns = self.local_rows(plan, start, window_end, unwritten, include_forming=True)
            forming = None
            if plan.kind == "bars" and plan.base != series.timeframe and columns and bars.length(columns):
                closed = columns["close_time"] <= now_ns()
                if not closed[-1]:
                    last = bars.to_wire(bars.take(columns, slice(-1, None)))
                    forming = {key: values[0] for key, values in last.items()}
                    columns = bars.take(columns, closed)
            has_rows = bool(columns) and bars.length(columns) > 0
            if result.problem is None and result.state != "filling" and \
                    (waiting := self.waiting_problem(plan, start, window_end)) is not None:
                result = Fill("partial" if has_rows else "failed", waiting)  # not asked again yet: say why it is missing
            if not has_rows and result.problem is not None and result.state != "filling":
                raise _problem_error(result.problem)
            rows = columns if columns else bars.empty(_columns_for(series))
            rows = bars.take(rows, slice(max(bars.length(rows) - limit, 0), None)) if end is None else \
                bars.take(rows, slice(0, limit))
            first = int(rows["time"][0]) if bars.length(rows) else None
            return (rows, first, forming, self.store.pointer(plan.key), self.revision(plan),
                    self.has_more_before(plan, first if first is not None else start),
                    self.gaps(plan, start, window_end), bars.to_wire(rows, encoded=encoded))
        rows, first, forming, pointer, revision, has_more, gaps, wire = await in_worker(assemble)
        page = {
            "instrumentId": instrument["instrumentId"], "seriesId": series.seriesId,
            "columns": wire,
            "forming": (self.market.sessions.forming(series, environment) if self.market.sessions else None) or forming,
            "range": time_range(first, int(rows["time"][-1]) + 1) if first is not None else None,
            "gaps": gaps[:500], "hasMoreBefore": has_more,
            "nextBefore": str(first) if has_more and first is not None else None,
            "source": self.market.source_ref(pointer["source"] if pointer and pointer.get("source") else plan.source_id),
            "fill": result.wire(), "revision": revision,
        }
        return page

    # ── coverage ────────────────────────────────────────────────────────────

    async def coverage(self, value: dict) -> dict:
        """What exists for one instrument, per capability and period (data.coverage)."""
        instrument = self.market.instrument_record(value["instrument"])
        wanted = set(value.get("capabilities") or [])
        return {"instrumentId": instrument["instrumentId"],
                "items": await in_worker(self.coverage_items, instrument, wanted, value.get("environment", "production"))}

    def coverage_items(self, instrument: dict, wanted: set[str] | None = None, environment="production") -> list[dict]:
        now = now_ns()
        stored = {}
        for key in self.store.keys(instrument["instrumentId"], environment=environment):
            if key.import_id is None:
                stored.setdefault(key.capability, {})[key.timeframe] = key
        items = []
        kinds = {"ohlcv": "bars"}
        for entry in self.market.registry.entries.values():
            for row in (entry.coverage or {}).get("rows", []):
                if row["kind"] in SERIES_CAPABILITIES:
                    kinds.setdefault(row["kind"], row["kind"])
        for capability, kind in kinds.items():
            if wanted and capability not in wanted:
                continue
            ranked = self.market.registry.rank(instrument, kind, "1d" if kind == "bars" else None)
            intraday = self.market.registry.rank(instrument, kind, "1m") if kind == "bars" else []
            sources = {}
            for source_id, row in ranked + intraday:
                for period in row.get("periods", []):
                    sources.setdefault(period["timeframe"], (source_id, row, period))
            timeframes = set(sources) | set(stored.get(capability, {}))
            for timeframe in sorted(timeframes, key=lambda tf: timeframe_nanoseconds(tf) if tf is not None else 0):  # a series without a bar size (funding) has None
                key = stored.get(capability, {}).get(timeframe)
                pointer = self.store.pointer(key) if key else None
                source_id, row, period = sources.get(timeframe, (None, None, None))
                if pointer and pointer.get("source"):
                    source_id = pointer["source"]
                    row = self.market.registry.row(source_id, instrument, kind, timeframe) \
                        if source_id in self.market.registry.entries else row
                cutoff = now - self.publish_delay(source_id, instrument, kind, timeframe)
                local = None
                if pointer and pointer["rows"]:
                    first, last = int(pointer["first"]), int(pointer["last"])
                    step = (timeframe_nanoseconds(timeframe) if timeframe is not None else None) or DAY_NS
                    plan = Plan(instrument, None, kind, key, timeframe, source_id, period)
                    gaps = self.gaps(plan, first, last + step) if period is not None and timeframe is not None else []
                    local = {"range": time_range(first, last + step), "rows": pointer["rows"],
                             "bytes": pointer["bytes"], "gaps": gaps[:200],
                             "repaired": pointer.get("repaired", [])[:200]}
                # The first stored row once the source is known to have nothing earlier; until then what the
                # source declares, from 1970 as for fetches (a fetch from 2016 doesn't mean history starts in 2016).
                earliest = None
                if local is not None and not self.has_more_before(plan, first):
                    earliest = local["range"]["start"]
                elif period is not None:
                    earliest = str(max(period_start(period, "last", now), 0))
                if instrument.get("active") is False or instrument.get("delistedAt"):
                    status = "delisted"
                elif local is None:
                    status = "not_stored"
                else:
                    lock = self._locks.get(key)
                    # A series without a bar size (funding settlements) has no bar grid: behind when nothing came in the last day.
                    newest = self.last_closed_open(instrument["calendarId"], timeframe, cutoff) if timeframe is not None else cutoff - DAY_NS
                    status = "updating" if lock is not None and lock.locked() else \
                        "behind" if int(pointer["last"]) < newest else "current"
                revision = pointer["revision"] if pointer else 0
                items.append({
                    "capability": capability, "timeframe": timeframe, "source": self.market.source_ref(source_id),
                    "local": local,
                    "available": {"periods": [p["timeframe"] for p in (row or {}).get("periods", [])],
                                  "earliest": earliest, "live": (row or {}).get("live", "none")},
                    "status": status, "problem": None, "revision": revision,
                    "publishDelay": (row or {}).get("publishDelay"), "publishedThrough": str(max(cutoff, 0)),
                })
        return items

    def window_start(self, instrument: dict, timeframe: str, end: int, count: int) -> int:
        """Where ``count`` bars before ``end`` start, at the earliest 1970: times are non-negative on the wire, and a
        chart's 10,000 weekly or monthly bars would reach back centuries (past what an int64 holds, for months)."""
        step = timeframe_nanoseconds(timeframe)
        calendar = instrument["calendarId"]
        if calendars.canonical(calendar) == calendars.CONTINUOUS and step is not None:
            aligned = end - end % step if step < DAY_NS else end - end % DAY_NS
            return max(0, aligned - count * step)
        if step is None or step >= DAY_NS:
            days = count * (step // DAY_NS if step else 31)
            return max(0, end - int(days * 1.5 + 10) * DAY_NS)
        per_day = max(1, int(6.5 * 3600 * 10**9 // step))
        return max(0, end - int((count / per_day) * 1.5 + 5) * DAY_NS)

    def local_rows(self, plan: Plan, start: int, end: int, extra: bars.Columns | None = None,
                   *, include_forming: bool = False) -> bars.Columns:
        """Stored rows of the plan's base (plus ``extra`` unwritten live rows), adjusted and aggregated."""
        series = plan.series
        if plan.kind != "bars":
            return self.store.read(plan.key, start, end)
        target = series.timeframe
        read_end = self.base_end(plan, start, end)
        raw = self.store.read(plan.key, start, read_end)
        if extra is not None and bars.length(extra):
            raw = bars.merge(raw, bars.window(extra, start, read_end))
        if not raw or not bars.length(raw):
            return {}
        if bars.inconsistent(raw).any():
            # A bar stored before the rule (MK-07) is read by the same rule a fetched one is stored by: open and close as given,
            # high and low widened just enough; the stored values stay as they are.
            raw = bars.contain(raw)
        rows = bars.window(self.render(plan, raw), start, end)
        if plan.base == target or not bars.length(rows):
            return rows
        closed = rows["close_time"] <= now_ns()
        keep = np.ones(len(closed), dtype=bool) if include_forming else closed.copy()
        # A local-only read can have just the first few days of a historical week. An unqueried
        # base slot makes that bucket incomplete. A source-confirmed no-trade span is known.
        pointer = self.store.pointer(plan.key)
        spans = pointer["spans"] if pointer else []
        for lo, hi in Spans.missing(spans, int(rows["time"][0]), int(rows["close_time"][-1])):
            for i in np.flatnonzero(closed & keep & (rows["time"] < hi) & (rows["close_time"] > lo)):
                expected, _ = calendars.bar_grid(plan.instrument["calendarId"], plan.base,
                    max(lo, int(rows["time"][i])), min(hi, int(rows["close_time"][i])), plan.key.session_scope)
                if len(expected) and not np.isin(expected, raw["time"], assume_unique=True).all():
                    keep[i] = False
        return bars.take(rows, keep)

    def render(self, plan: Plan, raw: bars.Columns) -> bars.Columns:
        """Raw base rows → the series' rows: adjust (at read time), then aggregate."""
        series = plan.series
        if bars.length(raw) and series.adjustment in ("split", "split_dividend"):
            daily = SeriesKey(plan.key.instrument_id, "ohlcv", "last", plan.key.session_scope, "1d")
            times, ratios, dividends = self.market.actions.factors(plan.key.instrument_id,
                daily if series.adjustment == "split_dividend" else None, plan.instrument["calendarId"],
                int(raw["time"][0]) if series.adjustmentAnchor != "earliest" else None,
                int(raw["time"][-1]) + 1 if series.adjustmentAnchor == "earliest" else None)
            raw = bars.adjust(raw, times, ratios, dividends, series.adjustment, series.adjustmentAnchor)
        return bars.aggregate(raw, plan.base or series.timeframe, series.timeframe, plan.instrument["calendarId"],
                              series.sessionScope)

    def action_gaps(self, plan, start, end):
        end = self.base_end(plan, start, end)
        ranked = self.market.registry.rank(plan.instrument, "actions")
        if not ranked:
            return None, []
        published = self.closed_until(plan.instrument, plan.base or "1d", self.published_through(plan, now_ns()))
        gaps = self.market.actions.missing(plan.instrument["instrumentId"], max(start, 0), min(end, published))
        return ranked[0][0], gaps

    async def fill_actions(self, plan: Plan, start: int, end: int, request_limit, environment) -> None:
        instrument = plan.instrument
        source, gaps = await in_worker(self.action_gaps, plan, start, end)
        if gaps:
            target = await in_worker(self.target, instrument, environment, source)
        for gap_start, gap_end in gaps[:request_limit]:
            history = await self.market.registry.call(source, "history", target, "actions", None, "last",
                                                      gap_start, gap_end)
            revision = await in_worker(self.market.next_revision, instrument["instrumentId"])
            rows = [row for row in history.extra.get("actions", []) if int(row["exDate"]) >= 0]
            await in_worker(self.market.actions.write, instrument["instrumentId"], rows, start=gap_start,
                            end=gap_end, source=source, revision=revision)
        if plan.series.adjustment == "split_dividend":
            await self.fill_reference_closes(plan, request_limit, start, self.base_end(plan, start, end))

    async def fill_reference_closes(self, plan, request_limit, start=None, end=None):
        from .actions import reference_close

        def missing():
            rows = self.market.actions.read(plan.key.instrument_id)["rows"]
            rows = self.market.actions.events(rows, start if plan.series.adjustmentAnchor != "earliest" else None,
                                              end if plan.series.adjustmentAnchor == "earliest" else None)
            days = self.market.actions.reference_days(rows, plan.instrument["calendarId"])
            if not days:
                return None, []
            series = ohlcv_series(MarketInstrument.from_wire(plan.instrument), "1d", adjustment="raw",
                                  session_scope=plan.series.sessionScope)
            daily = self.plan(plan.instrument, series, None, environment=plan.key.environment)
            pointer = self.store.pointer(daily.key)
            prices = self.store.read(daily.key) if pointer else {}
            spans = pointer["spans"] if pointer else []
            return daily, [day for day in sorted(set(days.values())) if reference_close(prices, day, spans) is None]

        daily, days = await in_worker(missing)
        if request_limit is not None and len(days) > request_limit:
            raise MarketError("fill_too_large", t("Fetch daily bars for the cash dividend reference dates first"))
        for day in days:
            result = await self.fill(daily, day, day + DAY_NS, limit=request_limit)
            if result.problem:
                raise _problem_error(result.problem)


    def revision(self, plan: Plan) -> int:
        pointer = self.store.pointer(plan.key)
        value = pointer["revision"] if pointer else 0
        if plan.series.adjustment in ("split", "split_dividend"):
            value = max(value, self.market.actions.read(plan.key.instrument_id)["revision"])
        return value

    def has_more_before(self, plan: Plan, first: int | None) -> bool:
        if first is None:
            return False
        pointer = self.store.pointer(plan.key)
        if pointer and pointer["first"] is not None and pointer["first"] < first:
            return True
        layers = self.layers(plan, now_ns())
        if layers is None:
            return False
        # Known to be empty before (or out of every layer's reach): the source has nothing earlier to give.
        return bool(self.missing(plan, layers.reach()[0][0], first))

    def gaps(self, plan: Plan, start: int, end: int) -> list[dict]:
        """Why rows are absent in [start, end): the source confirmed none, not asked yet, or unavailable (asked and
        no layer could give it, with when it is asked again; or no layer of the source reaches it at all)."""
        end = self.base_end(plan, start, end)
        pointer = self.store.pointer(plan.key)
        spans = pointer["spans"] if pointer else []
        unavailable = pointer.get("unavailable", []) if pointer else []
        out = []
        for a, b, state in spans:
            if b <= start or a >= end:
                continue
            if state == "none":
                out.append({"start": str(max(a, start)), "end": str(min(b, end)), "reason": "source_has_none"})
        for a, b, retry, problem in unavailable:
            if a < end and start < b:
                out.append({"start": str(max(a, start)), "end": str(min(b, end)), "reason": "unavailable",
                            "retryAt": str(retry), "problem": problem})
        now = now_ns()
        layers = self.layers(plan, now)
        reach = layers.reach() if layers is not None else [(0, 2**62)]
        held = sorted(spans + [[a, b, "unavailable"] for a, b, _retry, _problem in unavailable])
        # Between the layers the source keeps (after its last file, before its API's depth) nothing can be asked.
        holes = [(reach[i][1], reach[i + 1][0]) for i in range(len(reach) - 1)]
        for a, b in Spans.missing(held, start, min(end, now)):
            for lo, hi in reach:
                if a < hi and lo < b:
                    out.append({"start": str(max(a, lo)), "end": str(min(b, hi)), "reason": "not_fetched"})
            for lo, hi in holes:
                if a < hi and lo < b:
                    out.append({"start": str(max(a, lo)), "end": str(min(b, hi)), "reason": "unavailable",
                                "retryAt": None, "problem": MarketError(
                                    "no_data", t("{source_id} keeps no file for this span and its API reaches back to {api_from}", source_id=plan.source_id, api_from=iso_of_ns(layers.api_from)[:10]), source_id=plan.source_id).problem()})
        out.sort(key=lambda g: int(g["start"]))
        return out

    def waiting_problem(self, plan: Plan, start: int, end: int) -> dict | None:
        """The reason of a span in [start, end) recorded unavailable that is asked again later, if any."""
        pointer = self.store.pointer(plan.key)
        return next((problem for a, b, retry, problem in (pointer.get("unavailable", []) if pointer else [])
                     if a < end and start < b and int(retry) > now_ns()), None)


def _columns_for(series: MarketSeries) -> tuple[str, ...]:
    if series.capability == "ohlcv":
        return bars.BAR_COLUMNS
    return ("time",)


def _rebase(series: MarketSeries, instrument: dict) -> MarketSeries:
    values = series.to_dict()
    values.pop("seriesId")
    values["instrumentId"] = instrument["instrumentId"]
    return MarketSeries.from_wire(values)


def _problem_error(problem: dict) -> MarketError:
    return MarketError(problem["code"], problem["message"], source_id=problem.get("sourceId"),
                       status=problem.get("status"), retry_at=int(problem["retryAt"]) if problem.get("retryAt") else None)


__all__ = ["Data", "Plan", "Fill", "problem_of", "MarketInstrument"]
