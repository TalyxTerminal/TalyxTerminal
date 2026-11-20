"""Sources as the user and AI see them: state, selection, settings, tests and switching.

``read_source`` lists sources (state, key configured or not, never the key), which source
ranks first for each market and data kind and why, and one source's details.
``write_source`` turns sources on and off, sets non-secret settings, tests sources and
reconnects them. Whether an AI may do so is decided by the AI side from the action's declared
effects; Market does not ask.

A test runs the same checks on any source (official or third party): the declaration is
well formed; a sample history answer has unique increasing nanosecond times inside the
asked window; a second page starts where the first ended (no repeated or missing rows); an
unknown code fails with a Market error code. "Test all" sends one sample request to every
source at once, disabled ones included, and reports latency or why it did not get through.

Switching: when the best source for stored series changes (a key configured, a source
turned on or off, a plan rejected), Market fetches the same spans from the new best source
in the background and swaps each series whole once its rows are in; until then the old rows
serve. A series never holds two sources' rows.
"""

from __future__ import annotations

import asyncio
import json
import time
from typing import Any

from vocabulary.identity import timeframe_nanoseconds

from . import bars
from .common import DAY_NS, MarketError, now_ns
from .routing import JOBS, PRIORITY, depth_start, in_worker, period_start
from .sources.base import Target
from .store import Spans

PAGE = 40
TEST_SECONDS = 10.0
SAMPLES = {"US": ("AAPL", "XNYS"), "HK": ("00700.HK", "XHKG"), "CN": ("600519.SH", "XSHG"), "FX": ("EURUSD=X", "24_7")}
MARKET_KINDS = ("bars", "actions", "classification", "shares", "financials", "members", "listings")


def sample_target(source_entry, sample: str | None = None, identity=None) -> Target | None:
    """A sample instrument inside a source's declared scope (on a venue, its main BTC pair when its list is in)."""
    coverage = source_entry.coverage or source_entry.descriptor.get("withKey") or {"rows": []}
    for item in coverage["rows"]:
        scope = item["scope"]
        for market in scope.get("markets", []):
            if market in SAMPLES:
                code, calendar = SAMPLES[market]
                code = sample or code
                return Target({"instrumentId": "vi2_" + "0" * 32, "identityVersion": 2, "venueId": market,
                               "symbol": code, "displaySymbol": code, "assetClass": "equity", "marketType": "spot",
                               "calendarId": calendar, "adjustmentEligible": True}, [])
        for venue in scope.get("venues", []):
            listed = identity.search(sample or "BTC", venues=[venue], limit=1) if identity is not None else []
            if listed:
                return Target(listed[0]["instrument"], [])
            symbol = sample or "BTC/USDT"
            return Target({"instrumentId": "vi1_sample", "identityVersion": 1, "venueId": venue, "symbol": symbol,
                           "displaySymbol": symbol, "nativeId": symbol.replace("/", ""), "assetClass": "crypto",
                           "marketType": "spot", "base": symbol.split("/")[0], "quote": symbol.split("/")[-1],
                           "calendarId": "24_7"}, [])
    return None


def unreachable_of(exc: BaseException) -> tuple[str | None, int | None]:
    if isinstance(exc, (asyncio.TimeoutError, TimeoutError)):
        return "timeout", None
    if not isinstance(exc, MarketError):
        return None, None
    details = exc.details or {}
    if details.get("reason") == "local_program_off":
        return "local_program_off", None
    if exc.code == "unauthorized":
        return "key_rejected", exc.status
    if exc.code == "rate_limited":
        return "rate_limited", exc.status
    if exc.code == "network_unreachable":
        return "network_unreachable", None
    if exc.status is not None:
        return "http_error", exc.status
    return None, None


TEST_PROBLEMS = {
    "timeout": ("source_unavailable", "No answer within 10 s"),
    "http_error": ("source_unavailable", "The source refused the request"),
    "key_rejected": ("unauthorized", "Key rejected"),
    "local_program_off": ("source_unavailable", "The program on this computer is not running"),
    "network_unreachable": ("network_unreachable", "No network"),
}


class SourceAdmin:
    def __init__(self, market):
        self.market = market
        self.registry = market.registry
        self.last_tests: dict[str, dict] = {}
        self.switching: asyncio.Task | None = None
        self.switched: list[dict] = []

    # ── reading ─────────────────────────────────────────────────────────────

    def state(self, source_id: str) -> tuple[str, dict | None]:
        entry = self.registry.entry(source_id)
        if not self.registry.enabled(source_id):
            return "off", None
        if entry.rejected == "unsubscribed":
            return "unsubscribed", entry.problem
        if entry.descriptor["credentials"] == "required" and not entry.source.key_configured:
            return "not_configured", None
        if entry.rejected == "unauthorized" or entry.coverage is None:
            return "failing", entry.problem
        if entry.gate.cool_until > now_ns():
            return "rate_limited", {"code": "rate_limited", "message": entry.gate.cool_reason or "Rate limited",
                                    "sourceId": source_id, "status": None, "retryAt": str(entry.gate.cool_until)}
        # A last test that did not get through stands until the next test.
        last = (self.last_tests.get(source_id) or {"tests": [{}]})["tests"][0]
        if last.get("unreachable") in TEST_PROBLEMS:
            code, message = TEST_PROBLEMS[last["unreachable"]]
            status = last["httpStatus"]
            if status in (403, 451):
                code, message = "forbidden", "Blocked from your region"
            return "failing", {"code": code, "message": f"{message} (HTTP {status})" if status else message,
                               "sourceId": source_id, "status": status, "retryAt": None}
        return "working", None

    def row(self, source_id: str) -> dict:
        entry = self.registry.entry(source_id)
        state, problem = self.state(source_id)
        key = entry.source.key_configured if entry.descriptor["credentials"] != "none" else None
        return {"source": self.market.source_ref(source_id), "state": state, "problem": problem,
                "keyConfigured": key, "paid": bool(entry.descriptor.get("paid")),
                "enabled": self.registry.enabled(source_id), "openTarget": f"source:{source_id}"}

    def selection(self, market: str | None = None) -> list[dict]:
        markets = [market] if market else ["US", "CN", "HK"]
        out = []
        for name in markets:
            probe = {"identityVersion": 2, "venueId": name, "assetClass": "equity", "marketType": "spot"}
            for kind in MARKET_KINDS:
                spans = (("daily", "1d"), ("intraday", "1m")) if kind == "bars" else ((None, None),)
                for label, timeframe in spans:
                    ranked = self.registry.rank(probe, kind, timeframe, history=kind != "listings")
                    if not ranked and kind != "bars":
                        continue
                    item = {"market": name, "kind": kind, "source": None, "reason": "No enabled source covers it"}
                    if ranked:
                        item.update(source=self.market.source_ref(ranked[0][0]),
                                    reason=self.reason(ranked, probe, kind, timeframe))
                    if label:
                        item["timeframes"] = label
                    out.append(item)
        return out[:120]

    def reason(self, ranked: list, probe: dict, kind: str, timeframe: str | None) -> str:
        first_id, first = ranked[0]
        if len(ranked) == 1:
            return "The only enabled source that covers it"
        second_id, second = ranked[1]
        now = now_ns()

        def earliest(item):
            return min((period_start(p, "last", now) for p in item.get("periods", [])), default=now)

        if (first.get("delisted") or first.get("pointInTime")) and not (second.get("delisted")
                                                                         or second.get("pointInTime")):
            return f"Includes delisted listings (unlike {self.market.source_ref(second_id)['name']})"
        if earliest(first) < earliest(second):
            return f"Deeper history than {self.market.source_ref(second_id)['name']}"
        if first.get("live") != second.get("live") and first.get("live") == "realtime":
            return "Realtime"
        if "bulk" in self.registry.entry(first_id).descriptor["supports"]:
            return "Answers many instruments per request"
        return f"Ranks before {self.market.source_ref(second_id)['name']}"

    async def read(self, value: dict) -> dict:
        view = value["view"]
        result = {"sources": [], "selection": [], "details": None, "nextCursor": None}
        if view == "list":
            ids = sorted(self.registry.entries)
            offset = int(value.get("cursor") or 0)
            result["sources"] = [self.row(source_id) for source_id in ids[offset:offset + PAGE]]
            result["nextCursor"] = str(offset + PAGE) if offset + PAGE < len(ids) else None
        elif view == "selection":
            result["selection"] = self.selection(value.get("market"))
        else:
            source_id = value["source"]
            if source_id not in self.registry.entries:
                raise MarketError("not_found", f"No data source {source_id}")
            result["sources"] = [self.row(source_id)]
            result["details"] = self.details(source_id)
        return result

    def details(self, source_id: str) -> dict:
        entry = self.registry.entry(source_id)
        declared = entry.descriptor.get("settings") or []
        secret = {item["name"] for item in declared if item["secret"]}
        settings = {"schema": declared,
                    "values": {k: v for k, v in entry.source.settings.items() if k not in secret}}
        coverage = entry.coverage or entry.descriptor.get("withKey") or {
            "rows": [], "concurrency": 1, "requestsPerMinute": None, "plan": None, "requestsPerDay": None,
            "subscriptionsPerConnection": None}
        local = bool(entry.descriptor.get("localGateway"))
        return {"coverage": coverage, "settings": settings, "lastTest": self.last_tests.get(source_id),
                "route": {"via": "this computer (127.0.0.1)" if local else "system proxy when set, else direct"},
                "wouldUse": [], "adds": []}

    # ── writing ─────────────────────────────────────────────────────────────

    async def write(self, value: dict, call_ctx=None) -> dict:
        operation = value["operation"]
        op = operation["op"]
        if op in ("enable", "disable"):
            self.registry.entry(operation["source"])
            self.registry.set_enabled(operation["source"], op == "enable", call_ctx)
            await self.registry.refresh(operation["source"])
            self.start_switch()
            return {"tests": [], "changed": True}
        if op == "configure":
            await self.configure(operation["source"], operation["settings"], allow_secrets=False, call=call_ctx)
            return {"tests": [], "changed": True}
        if op == "test":
            tests = [await self.test(source_id, operation.get("sample")) for source_id in operation["sources"]]
            return {"tests": tests, "changed": False}
        if op == "test_all":
            return {"tests": await self.test_all(), "changed": False}
        if op == "reconnect":
            entry = self.registry.entry(operation["source"])
            await entry.source.close()
            entry.rejected = None
            await self.registry.refresh(operation["source"])
            return {"tests": [], "changed": True}
        raise MarketError("invalid_input", f"Unknown operation {op}")

    async def configure(self, source_id: str, values: dict, *, allow_secrets: bool, call=None) -> dict:
        """Settings checked against the source's declaration; secret fields only from the Market panel."""
        entry = self.registry.entry(source_id)
        declared = {item["name"]: item for item in entry.descriptor.get("settings") or []}
        for name, item in values.items():
            spec = declared.get(name)
            if spec is None:
                raise MarketError("invalid_input", f"{source_id} has no setting {name}")
            if spec["secret"] and not allow_secrets:
                raise MarketError("invalid_input", f"{name} is secret: enter it in the Market panel")
            kind = spec["type"]
            if item is not None and ((kind == "integer" and not isinstance(item, int))
                                     or (kind == "boolean" and not isinstance(item, bool))
                                     or (kind in ("string", "choice") and not isinstance(item, str))
                                     or (kind == "choice" and item not in spec.get("choices", []))):
                raise MarketError("invalid_input", f"{source_id}.{name} must be a {kind}")
        result = await entry.source.configure(source_id, values)
        plain = {k: v for k, v in values.items() if not declared[k]["secret"]}
        if plain:
            self.registry.set_settings(source_id, plain, call)
        entry.rejected = None
        await self.registry.refresh(source_id)
        if hasattr(entry.source, "code_history"):
            self.market.track_task(asyncio.ensure_future(self.market.load_code_history(force=True)))
        self.start_switch()
        return result

    async def revert(self, call_id, direction):
        source, section, fields, values = self.registry.preferences.change(call_id, direction)
        entry = self.registry.entry(source)
        if section == "settings":
            await entry.source.configure(source, {key: values.get(key) for key in fields})
            for key in fields - values.keys():
                entry.source.settings.pop(key, None)
        self.registry.preferences.write(source, section, fields, values)
        entry.rejected = None
        await self.registry.refresh(source)
        self.start_switch()

    # ── tests ───────────────────────────────────────────────────────────────

    async def probe(self, source_id: str) -> dict:
        """One sample request: latency or why it did not get through."""
        entry = self.registry.entry(source_id)
        result = {"source": source_id, "latencyMs": None, "unreachable": None, "httpStatus": None, "passed": 0,
                  "failed": []}
        if entry.descriptor["credentials"] == "required" and not entry.source.key_configured:
            result["unreachable"] = "key_missing"
            return result
        started = time.perf_counter()
        try:
            await asyncio.wait_for(self._sample(source_id, entry), TEST_SECONDS)
        except (TimeoutError, MarketError) as exc:
            reason, status = unreachable_of(exc)
            result.update(unreachable=reason, httpStatus=status)
            if reason is None:
                result["failed"].append({"check": "sample", "detail": str(getattr(exc, "message", exc))[:300]})
            else:
                result["latencyMs"] = None
            return result
        result["latencyMs"] = int((time.perf_counter() - started) * 1000)
        result["passed"] = 1
        return result

    async def _sample(self, source_id: str, entry) -> Any:
        source = entry.source
        custom = getattr(source, "probe", None)
        if custom is not None:
            return await custom(source_id)
        if "history" in entry.descriptor["supports"]:
            target = sample_target(entry, identity=self.market.identity)
            coverage = entry.coverage or await source.describe(source_id)
            row = next((r for r in coverage["rows"] if r.get("periods")), None)
            if target is not None and row is not None:
                timeframe = row["periods"][-1]["timeframe"]
                now = now_ns()
                return await source.history(source_id, target, row["kind"], timeframe, "last", now - 10 * DAY_NS, now)
        if "catalog" in entry.descriptor["supports"]:
            scope = (entry.coverage or {"rows": [{"scope": {}}]})["rows"][0]["scope"]
            return await source.catalog(source_id, scope, "production", None)
        return await source.describe(source_id)

    async def test_all(self) -> list[dict]:
        ids = sorted(self.registry.entries)
        results = await asyncio.gather(*(self.probe(source_id) for source_id in ids))
        for item in results:
            self.last_tests[item["source"]] = {"tests": [item], "changed": False}
        return list(results)[:100]

    async def test(self, source_id: str, sample: str | None = None) -> dict:
        """The conformance checks every source gets (see the module docstring); the result stands until the next
        test, whether it got through or not."""
        result = await self._checks(source_id, sample)
        self.last_tests[source_id] = {"tests": [result], "changed": False}
        return result

    async def _checks(self, source_id: str, sample: str | None) -> dict:
        entry = self.registry.entry(source_id)
        source = entry.source
        result = {"source": source_id, "latencyMs": None, "unreachable": None, "httpStatus": None, "passed": 0,
                  "failed": []}
        failed = result["failed"]

        def check(name: str, ok: bool, detail: str = "") -> None:
            if ok:
                result["passed"] += 1
            elif len(failed) < 20:
                failed.append({"check": name, "detail": detail[:300]})

        if entry.descriptor["credentials"] == "required" and not source.key_configured:
            result["unreachable"] = "key_missing"
            return result
        latencies = []
        try:
            started = time.perf_counter()
            coverage = await asyncio.wait_for(source.describe(source_id), TEST_SECONDS)
            latencies.append(time.perf_counter() - started)
        except (TimeoutError, MarketError) as exc:
            reason, status = unreachable_of(exc)
            result.update(unreachable=reason or "http_error", httpStatus=status)
            return result
        rows = coverage.get("rows") or []
        check("describe", bool(rows) and all("scope" in r and "kind" in r and "live" in r for r in rows),
              "describe() must give rows with scope, kind and live")
        periods = [(r["kind"], p) for r in rows for p in r.get("periods", []) if r["kind"] == "bars"]
        check("periods", all(timeframe_nanoseconds(p["timeframe"]) for _k, p in periods),
              "every period needs a known timeframe")
        target = sample_target(entry, sample, self.market.identity)
        if target is None or not periods or "history" not in entry.descriptor["supports"]:
            result["latencyMs"] = int(1000 * sorted(latencies)[len(latencies) // 2]) if latencies else None
            return result
        kind, period = min(periods, key=lambda item: timeframe_nanoseconds(item[1]["timeframe"]))
        timeframe = period["timeframe"]
        step = timeframe_nanoseconds(timeframe)
        now = now_ns()
        rows_per_request = int(period.get("requestRows") or 1000)
        span = min(2 * rows_per_request * step + 3 * DAY_NS, 30 * DAY_NS)
        start = max(now - span, depth_start(period["depth"], now))
        try:
            started = time.perf_counter()
            first = await asyncio.wait_for(source.history(source_id, target, kind, timeframe, "last", start, now),
                                           TEST_SECONDS)
            latencies.append(time.perf_counter() - started)
        except (TimeoutError, MarketError) as exc:
            reason, status = unreachable_of(exc)
            result.update(unreachable=reason, httpStatus=status)
            if reason is None:
                check("history", False, f"sample history failed: {getattr(exc, 'message', exc)}")
            return result
        columns = first.columns
        times = columns.get("time") if columns else None
        has_rows = times is not None and len(times) > 0
        check("columns", all(name in columns for name in ("time", "close_time", "open", "high", "low", "close",
                                                          "volume")), "history must give time, close_time, OHLCV")
        if has_rows:
            values = [int(t) for t in times]
            check("order", all(a < b for a, b in zip(values, values[1:], strict=False)),
                  "times must increase without repeats")
            nanoseconds = min(values) > 10**17
            check("time_unit", nanoseconds and int(columns["close_time"][0]) > values[0],
                  f"times must be UTC nanoseconds (first {values[0]}); closeTime after time")
            if nanoseconds:  # a window check on misread times would only repeat the unit failure
                check("window", min(values) >= start and max(values) < now, "rows must lie inside the asked window")
        if first.next is not None:
            try:
                second = await asyncio.wait_for(
                    source.history(source_id, target, kind, timeframe, "last", int(first.next), now), TEST_SECONDS)
                later = [int(t) for t in second.columns.get("time", [])]
                earlier = [int(t) for t in times] if has_rows else []
                overlap = bool(later and earlier and min(later) <= max(earlier))
                check("paging", not overlap and (not later or min(later) >= int(first.next)),
                      "the next page repeats rows of the previous one" if overlap
                      else "the next page starts before the continuation point")
            except (TimeoutError, MarketError) as exc:
                check("paging", False, f"the next page failed: {getattr(exc, 'message', exc)}")
        unknown = Target({**target.instrument, "symbol": "ZZZZQ.NOSUCH" if target.market != "CN" else "699999.SH",
                          "displaySymbol": "ZZZZQ"}, [])
        try:
            empty = await asyncio.wait_for(source.history(source_id, unknown, kind, timeframe, "last", start, now),
                                           TEST_SECONDS)
            check("errors", not bars.length(empty.columns), "an unknown code must give no rows or not_found")
        except MarketError:
            check("errors", True)
        except TimeoutError:
            check("errors", False, "an unknown code did not answer in time")
        except Exception as exc:  # noqa: BLE001 - a source must answer with a Market error code
            check("errors", False, f"an unknown code raised {type(exc).__name__} instead of a Market error code")
        result["latencyMs"] = int(1000 * sorted(latencies)[len(latencies) // 2])
        return result

    # ── switching ───────────────────────────────────────────────────────────

    def start_switch(self) -> asyncio.Task:
        """Start (or restart after the current one) moving stored series to their best source."""
        previous = self.switching

        async def run():
            if previous is not None and not previous.done():
                await asyncio.gather(previous, return_exceptions=True)
            return await self.switch()

        self.switching = self.market.track_task(asyncio.ensure_future(run()))
        return self.switching

    def moves(self) -> list[tuple]:
        moves = []
        store, identity = self.market.store, self.market.identity
        for key in store.keys(environment=None):
            if key.import_id or key.capability != "ohlcv":
                continue
            pointer = store.pointer(key)
            if not pointer or not pointer.get("source") or not pointer["parts"]:
                continue
            record = identity.record(key.instrument_id)
            if record is None:
                continue
            ranked = self.registry.rank(record, "bars", key.timeframe)
            best = next((sid for sid, row in ranked
                         if any(p["timeframe"] == key.timeframe for p in row.get("periods", []))), None)
            if best is not None and best != pointer["source"]:
                moves.append((key, record, pointer["source"], best))
        return moves

    async def switch(self) -> list[dict]:
        done = []
        token = PRIORITY.set(JOBS)
        try:
            for key, record, old, new in await in_worker(self.moves):
                pointer = self.market.store.pointer(key)
                spans = [list(s) for s in pointer["spans"] if s[2] == "have"]
                if not spans:
                    continue
                target = self.market.data.target(record, key.environment)
                collected, fetched = [], []
                try:
                    for span_start, span_end, _state in spans:
                        cursor = span_start
                        while cursor is not None and cursor < span_end:
                            history = await self.registry.call(new, "history", target, "bars", key.timeframe,
                                                               key.price_basis, cursor, span_end)
                            if bars.length(history.columns):
                                collected.append(bars.window(history.columns, cursor, span_end))
                            cursor = history.next
                        fetched.append([span_start, span_end, "have"])
                except MarketError as exc:
                    done.append({"instrumentId": key.instrument_id, "from": old, "to": new, "problem": exc.problem()})
                    continue
                columns = bars.empty()
                for part in collected:
                    columns = bars.merge(columns, part) if bars.length(columns) else part
                merged: list = []
                for span in fetched:
                    merged = Spans.add(merged, span[0], span[1], "have")
                await in_worker(self.market.store.replace, key, columns, merged, new)
                self.market.sessions.series_replaced(key)
                done.append({"instrumentId": key.instrument_id, "from": old, "to": new, "problem": None})
        finally:
            PRIORITY.reset(token)
        self.switched = done
        return done


def dumps(value: Any) -> str:
    return json.dumps(value, separators=(",", ":"))
