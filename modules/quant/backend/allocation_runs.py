"""An allocation's runs: the preparation, the trial of the newest period (the research process kept open), the whole walk-forward backtest in
a process of its own (in the shared run queue), the runs' list with what changed between them, and each run's Work and Results.

A run records every setting (the declared values, the pool as it was, the data settings with the day they end), keeps each finished period
as it goes (`periods.jsonl`) so a run stopped or interrupted keeps them, and ends in `work.json` and `results/<tab>.json`.
"""
from __future__ import annotations

import asyncio
import contextlib
import copy
import json
import shutil
import time
from datetime import UTC
from pathlib import Path
from uuid import uuid4

import pandas as pd
import pyarrow.parquet as pq
from talyx_sdk import ContractError
from talyx_sdk.i18n import t

from . import allocation_prep as P
from . import allocation_sweep as S
from .library_summary import reviewable
from .object_data import resolve_text, resolve_until
from .project_paths import MODULE

RUN = "qrun"
TERMINAL = {"completed", "failed", "cancelled", "interrupted"}
KINDS = {"backtest", "heldout"}
TABS = ("summary", "weights", "risk", "returns", "style", "robust", "settings")
GB = 1024 ** 3
WORKER = MODULE / "native" / "allocation_run.py"
READ_LIMIT = 300


def named_by_run(body, number):
    """The charts name the run's own line "Allocation"; the page calls it by its run number, as the compared run is, so the name is set here, where a run is read."""
    name = f"#{number}"
    if isinstance(body, list):
        return [named_by_run(item, number) for item in body]
    if not isinstance(body, dict):
        return body
    out = {key: named_by_run(value, number) for key, value in body.items()}
    if "type" in out and out.get("name") == "Allocation":
        out["name"] = name
        if isinstance(out.get("customdata"), list):
            out["customdata"] = [name if item == "Allocation" else item for item in out["customdata"]]
    return out


def summary(record):
    return {key: record.get(key) for key in ("id", "number", "runKind", "state", "stage", "createdAt", "startedAt", "endedAt", "seconds", "metrics", "changes", "starred", "note",
                                           "progress", "failure", "message", "of", "objectId", "heldOut", "version", "data", "queue", "stopped", "estimate", "notes", "sweep")}


def _words(value):
    return str(value).replace("_", " ").capitalize() if value else value


def day(milliseconds):
    from datetime import datetime
    return datetime.fromtimestamp(milliseconds / 1000, UTC).strftime("%Y-%m-%d") if milliseconds is not None else None


def pd_timestamp(text):
    import pandas as pd
    return pd.Timestamp(text)


def changes_of(before, after):
    """Every setting that differs between two runs' values (the whole declared set, not only the data)."""
    out = []
    for key in sorted(set(before) | set(after)):
        if key in before and before.get(key) != after.get(key):
            out.append({"name": key, "from": before.get(key), "to": after.get(key)})
    return out


DATA_NAMES = {"since": "start", "until": "end", "heldOutFrom": "held out from", "untilLatest": "up to the latest", "adjustment": "prices"}


def run_changes(before, after):
    """What differs between two runs: the dates and the rest of the data settings, the assets of the pool (by ticker, with what came and went), and every
    setting (only the settings were compared, so a new period or an added BTC read "No changes"). Each is {name, from, to}."""
    from .run_flow import changes as flow_changes
    out = [{"name": DATA_NAMES.get(item["what"], item["what"]), "from": "none" if item["from"] is None else item["from"], "to": "none" if item["to"] is None else item["to"]}
           for item in flow_changes(before.get("data") or {}, after.get("data") or {})]
    old, new = [row.get("ticker") for row in before.get("pool") or []], [row.get("ticker") for row in after.get("pool") or []]
    if old != new:
        came, went = [name for name in new if name not in old], [name for name in old if name not in new]
        moved = ", ".join([*(f"+{name}" for name in came), *(f"−{name}" for name in went)])
        out.append({"name": "assets", "from": str(len(old)), "to": f"{len(new)} ({moved})" if moved else f"{len(new)}, groups or order changed"})
    return out + changes_of(before.get("params") or {}, after.get("params") or {})


def held_from(rows):
    """The period the newest published weights belong to: the first day they are held from. Continued values repeat the held weights on every day until the
    next rebalance, so the newest row's day would move daily (and a review given for it would be lost the next day); the period is where those weights began."""
    by_day = {}
    for row in rows:
        by_day.setdefault(row["datetime"], {})[row["instrument"]] = round(row["value"], 12)
    days = sorted(by_day)
    if not days:
        return None
    at = len(days) - 1
    while at > 0 and by_day[days[at - 1]] == by_day[days[-1]]:
        at -= 1
    return days[at]



GROUP_CAP = "group_max:"   # a sweep axis "group_max:Stocks" is the Stocks row's "at most" of the group limits


def group_caps(params, changes):
    """A sweep cell's changes as settings: each `group_max:<group>` value becomes that group's "at most" in the group limits (a row is added for a group
    without one); the other changes are settings of their own name."""
    plain = {name: value for name, value in changes.items() if not name.startswith(GROUP_CAP)}
    caps = {name[len(GROUP_CAP):]: value for name, value in changes.items() if name.startswith(GROUP_CAP)}
    if not caps:
        return plain
    limits = [dict(row) for row in params.get("group_limits") or []]
    for group, value in caps.items():
        row = next((row for row in limits if row.get("group") == group), None)
        if row is None:
            limits.append({"group": group, "min": "", "max": str(value), "count": ""})
        else:
            row["max"] = str(value)
    return {**plain, "group_limits": limits}


async def with_pool_market(ctx, data, params, pool, prepare, info_of=lambda ready: ready["info"], code=False):
    """`prepare(data)` with what the pool's rows ask of Market added to the frozen bars: each row's proxy, the exchange rate of each currency that is not
    the allocation's, and the benchmark's symbols (in the pool or not) when the settings track it or the allocation is code (its estimator may). Returns (ready, extras): the maps `proxyColumns` and `fxColumns` the worker's
    `daily_returns` reads, keyed by the pool's own columns, and `benchmarkColumns` (column: percent) its `benchmark_daily` reads.
    A run, its publication and every update of the published version prepare their bars through this one function."""
    data = copy.deepcopy(data)
    base = P.money(params.get("currency") or "USD")
    symbols = data.get("symbols") or []
    wanted = {}   # key -> (instrument, invert for a rate or the weight for a benchmark symbol)
    market = [row for row in pool if row.get("kind") not in ("run", "library") and row.get("instrumentId")]
    for row in market:
        proxy = (row.get("proxy") or "").strip()
        if proxy:
            wanted[("proxy", row["instrumentId"])] = (await resolve_text(ctx, proxy), False)
    for currency in sorted({P.money(row.get("currency")) for row in market if row.get("currency") and P.money(row["currency"]) != base}):
        wanted[("fx", currency)] = (await resolve_text(ctx, f"{currency}{base}=X"), False)
    for symbol, weight in P.benchmark_parts(params["benchmark"]) if code or P.tracks(params) else ():
        wanted[("benchmark", symbol)] = (await resolve_text(ctx, symbol), weight)
    have = {item["instrument"].get("instrumentId") for item in symbols}
    for instrument, _ in wanted.values():
        if instrument["instrumentId"] not in have:
            symbols.append({"instrument": instrument})
            have.add(instrument["instrumentId"])
    data["symbols"] = symbols
    ready = await prepare(data)
    columns = {item.get("instrumentId"): item["column"] for item in info_of(ready).get("instruments", [])}
    extras = {"proxyColumns": {}, "fxColumns": {}, "benchmarkColumns": {}}
    for (kind, key), (instrument, extra) in wanted.items():
        if instrument["instrumentId"] not in columns:
            raise ContractError("invalid_input", t("Market has no prices for {displaySymbol}", displaySymbol=instrument.get('displaySymbol') or instrument.get('symbol')),
                                {"fields": [{"proxy": "pool", "fx": "currency", "benchmark": "benchmark"}[kind]]})
        column = columns[instrument["instrumentId"]]
        if kind == "benchmark":
            extras["benchmarkColumns"][column] = extra
        elif kind == "proxy":
            extras["proxyColumns"][columns.get(key, key)] = column
        else:
            for row in market:
                if P.money(row.get("currency")) == key and row["instrumentId"] in columns:
                    extras["fxColumns"][columns[row["instrumentId"]]] = {"column": column, "invert": extra}
    return ready, extras

class AllocationRuns:
    def __init__(self, ctx, records, store, factors, notifications):
        self.ctx, self.records, self.store, self.factors, self.notifications = ctx, records, store, factors, notifications
        self.data, self.engine, self.queue = factors.data, factors.engine, factors.queue
        self.tasks, self.stopping = {}, set()
        self.trials = {}
        self.recovered = set()  # workspaces whose runs from before this process were marked interrupted

    # ------------------------------------------------------------------------------------------------------- records
    def directory(self, identity):
        return self.records.root / "qallocations" / identity

    def read(self, identity):
        record = self.records.find(RUN, identity)
        if record is None or not record.get("allocation"):
            raise ContractError("not_found", t("This allocation run does not exist."))
        return record

    def owns(self, identity):
        record = self.records.find(RUN, identity)
        return bool(record and record.get("allocation"))

    def save(self, record, **changes):
        previous = record["state"]
        updated = self.records.put(RUN, {**record, **changes})
        if updated["state"] != previous or updated["stage"] != record["stage"] or {"progress", "starred", "checks", "queue", "sweep"} & set(changes):
            self.ctx.publish("object_run_changed", {"runId": updated["id"], "objectId": updated["objectId"], "workspaceId": updated["workspaceId"], "state": updated["state"]})
        return updated

    def update(self, identity, **changes):
        return self.save(self.read(identity), **changes)

    def of_object(self, object_id):
        rows = self.records.db.execute("SELECT content FROM records WHERE kind='qrun' AND json_extract(content,'$.objectId')=? AND json_extract(content,'$.allocation')=1 "
                                       "AND coalesce(json_extract(content,'$.removed'),0)=0 ORDER BY json_extract(content,'$.number') DESC LIMIT ?", (object_id, READ_LIMIT)).fetchall()
        return [json.loads(row[0]) for row in rows]

    def next_number(self, object_id):
        row = self.records.db.execute("SELECT MAX(json_extract(content,'$.number')) FROM records WHERE kind='qrun' AND json_extract(content,'$.objectId')=? "
                                      "AND json_extract(content,'$.allocation')=1", (object_id,)).fetchone()
        return int(row[0] or 0) + 1

    def allocation(self, object_id):
        obj = self.store.read(object_id)
        if obj["kind"] != "allocation":
            raise ContractError("invalid_input", t("Only an allocation runs here."))
        return obj

    # ------------------------------------------------------------------------------------------------------- strategy runs as assets (#21)
    async def link_candidates(self):
        """The strategy runs of this workspace that finished and can sit in the pool as an asset: the run's daily equity is its price."""
        rows = self.records.db.execute("SELECT content FROM records WHERE kind='qrun' AND json_extract(content,'$.objectKind')='strategy' AND json_extract(content,'$.state')='completed' "
                                       "AND json_extract(content,'$.runKind')='run' AND json_extract(content,'$.workspaceId')=? AND coalesce(json_extract(content,'$.removed'),0)=0 "
                                       "ORDER BY json_extract(content,'$.createdAt') DESC LIMIT 200", (self.store.workspace(),)).fetchall()
        out = []
        for row in rows:
            record = json.loads(row[0])
            series = await self.strategy_equity(record["id"]) if self.strategy_equity else []
            if series:
                out.append({"kind": "run", "runId": record["id"], "name": f"{record['name']} #{record.get('number') or ''}".strip(), "from": day(series[0][0]), "to": day(series[-1][0])})
        return out

    def library_candidates(self):
        """The allocations published in the library: each one's newest version, which can sit in the pool as an asset (its equity is what its weights earned on the prices they were frozen with)."""
        out = []
        for item in (self.library.list({"kind": "allocation"}) if self.library is not None else {"items": []})["items"]:
            for version in sorted(item["versions"], key=lambda entry: -entry["version"]):  # the newest version that still has what its equity is read from
                origin = (self.library.store.version(version["versionId"])["meta"].get("origin") or {})
                series = self.library_equity(origin.get("runId")) if origin.get("runId") else []
                if series:
                    out.append({"kind": "library", "runId": origin["runId"], "name": f"{item['name']} v{version['version']}", "from": day(series[0][0]), "to": day(series[-1][0])})
                    break
        return out

    def library_equity(self, run_id):
        """Read the net path earned by the source backtest, including its last actual bar."""
        path = self.directory(run_id) / "output" / "equity.parquet"
        if not path.is_file():
            return []
        series = pd.read_parquet(path)["equity"]
        return [[stamp.value // 1_000_000, value] for stamp, value in series.items()]

    async def with_links(self, ready, pool, directory):
        """`ready` with the pool's strategy runs added to the frozen bars as one more price column each (their daily equity); the frozen bars of the Market stay as they are."""
        links = [row for row in pool if row.get("kind") in ("run", "library")]
        if not links:
            return ready
        series = []
        for row in links:
            points = await asyncio.to_thread(self.library_equity, row["runId"]) if row["kind"] == "library" else await self.strategy_equity(row["runId"])
            if not points:
                raise ContractError("invalid_input", t("{name} has no daily equity to read; run it again.", name=row['name']), {"fields": ["pool"]})
            series.append((row, points))
        return await asyncio.to_thread(self.write_linked_bars, ready, series, directory)

    @staticmethod
    def write_linked_bars(ready, series, directory):
        import hashlib

        import pandas as pd
        bars = pd.read_parquet(ready["barsPath"], columns=["label", "column", "close"])
        added = []
        for row, points in series:
            frame = pd.DataFrame(points, columns=["label", "close"])
            frame["label"] = pd.to_datetime(frame["label"], unit="ms", utc=True).dt.normalize().dt.as_unit("ms").astype("int64")
            added.append(frame.drop_duplicates("label", keep="last").assign(column=row["ticker"]))
        stamp = hashlib.sha1("|".join([ready["barsPath"], *(f"{row['ticker']}:{row['runId']}" for row, _ in series)]).encode()).hexdigest()[:12]
        path = Path(directory) / f"bars-with-runs-{stamp}.parquet"
        path.parent.mkdir(parents=True, exist_ok=True)
        pd.concat([bars, *added], ignore_index=True).to_parquet(path)
        return {**ready, "barsPath": str(path)}

    async def prepare_data(self, data, params, pool, code):
        """`self.data.prepare(data)` with what the pool's rows ask of Market added to the frozen bars (`with_pool_market`)."""
        # An allocation's pool may span calendars (stocks and crypto, a rate on its own): each asset keeps its own.
        return await with_pool_market(self.ctx, data, params, pool, lambda wanted: self.data.prepare(wanted, calendars="many"), code=code)

    # ------------------------------------------------------------------------------------------------------- preparation
    async def prep(self, object_id):
        obj = self.allocation(object_id)
        declared = P.declarations(self.store, obj)
        params = P.values_of(self.store, obj) if not declared["errors"] else {}
        pool = P.pool_of(obj, params)
        if params:
            params = {**params, "costs": P.costs_of(params, pool)}
        return {"objectId": obj["id"], "name": obj["name"], "mode": obj["settings"].get("mode", "standard"), "groups": P.groups_view(declared), "values": params,
                "errors": declared["errors"], "pool": pool, "capabilities": P.capabilities(), "data": obj["settings"].get("data") or {}, "version": obj["version"],
                "limits": [], "running": {"running": len(self.tasks)}, "engineWarm": self.engine.lease is not None}

    # ------------------------------------------------------------------------------------------------------- trial
    async def trial(self, object_id, args=None):
        """The newest period under the settings (with `changes` on top), the same fit as every period of the backtest; the research process stays open.
        Only the newest of overlapping requests is answered."""
        args = args or {}
        obj = self.allocation(object_id)
        params = P.values_of(self.store, obj, args.get("changes"))
        pool = P.pool_of(obj, params)
        P.check_values_before_run(params, pool)
        ticket = self.trials[object_id] = self.trials.get(object_id, 0) + 1
        data = copy.deepcopy(obj["settings"].get("data") or {})
        data = await resolve_until(self.ctx, data, instruments=await self.data.calendar_items(data))
        ready, extras = await self.prepare_data(data, params, pool, obj["settings"].get("mode") == "code")
        columns = {item.get("instrumentId"): item["column"] for item in ready["info"].get("instruments", [])}
        pool = P.pool_of(obj, params, columns)
        output = self.directory(f"trial-{object_id}-{ticket}")
        ready = await self.with_links(ready, pool, output)
        job = {"output": str(output), "data": {key: ready[key] for key in ("key", "barsPath", "info", "extrasPath")},
               "settings": {**params, "pool": pool, "costs": P.costs_of(params, pool), **extras}, "pin": args.get("pin"), "heldOutFrom": data.get("heldOutFrom"),
               # only a maximum ratio fits over the rate; the other objectives' weights do not move with it, and the trial does not wait for it
               "riskFree": await P.risk_free(self.ctx, params, ready["dataFrom"], ready["dataTo"], f"qallocation-trial:{object_id}") if params["objective"] == "max_ratio" else {"basis": "zero"}}
        if args.get("published"):
            job["published"] = args["published"]
        if args.get("heldFrom") is not None:
            job["heldFrom"] = int(args["heldFrom"])  # the reviewed period: the trial is fitted at the close before its weights are held, as the version was
        elif args.get("runId"):
            job.update(self.compared_period(args["runId"]))
        started = time.perf_counter()
        try:
            if obj["settings"].get("mode") == "code":
                output.mkdir(parents=True, exist_ok=True)
                job["code"], _ = await self.capture_project(obj, output)
                answer = await self.engine.run_once(job, output / "engine", context={"objectId": object_id}, script=WORKER,
                                                    op="allocation_trial", prefix="qallocation-trial-")
            else:
                answer = await self.engine.call("allocation_trial", job, seconds=600)
        finally:
            await asyncio.to_thread(shutil.rmtree, output, ignore_errors=True)
        if self.trials.get(object_id) != ticket:
            return {"superseded": True}
        if not answer.get("ok"):
            raise ContractError("invalid_input", (answer.get("failure") or {}).get("message") or answer.get("message") or t("The trial failed."), {"code": answer.get("code"), "fields": answer.get("fields") or []})
        return {**answer, "computedAt": int(time.time() * 1000), "fullRun": False, "wall": round(time.perf_counter() - started, 3)}

    def compared_period(self, run_id):
        """The newest period of the run a trial is compared with: the close its weights were held from and what was held going into it (drifted), so the trial
        fits that same period from the same holding and only the settings differ."""
        run = self.read(run_id)
        if run["state"] != "completed" or run["runKind"] != "backtest":  # only a finished backtest has a newest period to compare with
            return {}
        work = json.loads((self.directory(run["id"]) / "output" / "work.json").read_text(encoding="utf-8"))
        held_from = pd.Timestamp(work["periodDates"][-1]).tz_localize("UTC")
        previous = {row["asset"]: row["drifted"] for row in work["thisPeriod"] if row.get("drifted") is not None}
        return {"heldFrom": int(held_from.value // 1_000_000), **({"previous": previous} if previous else {})}

    # ------------------------------------------------------------------------------------------------------- the held-out period
    async def held_status(self, object_id, run_id=None):
        """What the confirmation says before the held-out part is used: which run it is for, the part's dates and length, and whether it was seen already."""
        import pandas as pd
        runs = self.of_object(object_id)
        source = next((item for item in runs if (run_id is None or item["id"] == run_id) and item["runKind"] == "backtest" and item["state"] == "completed"), None)
        if source is None:
            raise ContractError("invalid_input", t("Run a backtest first; the held-out period scores a finished run."))
        data = source["settings"]["data"]
        start = data.get("heldOutFrom")
        if not start:
            raise ContractError("invalid_input", t("No held-out period is set on this run."))
        days = len(pd.bdate_range(start, data["until"]))
        seen = next((item for item in runs if item["runKind"] == "heldout" and item["state"] == "completed"), None)
        used = {"runId": seen["id"], "number": seen["number"], "same": seen["of"] == source["id"]} if seen else None
        return {"runId": source["id"], "number": source["number"], "from": start, "to": data["until"], "days": days, "short": days < 126, "used": used}

    # ------------------------------------------------------------------------------------------------------- publishing
    def plan_publication(self, run_id, name=None):
        """What publishing this run does, for the publish sheet: the next version, what changed from the last one, who uses it, the checks to look at. Nothing is computed."""
        run = self.read(run_id)
        if run["state"] != "completed" or run["runKind"] != "backtest":
            raise ContractError("conflict", t("Publish a completed backtest; a held-out check or a stopped run cannot be published."))
        store = self.library.store
        name = (name or run["name"]).strip()
        existing = store.find_item(name)
        # `field` says which row of the sheet the reason belongs to: the name, or the run as a whole
        blocked = {"reason": t("{name} is already a {kind} in the library; choose another name.", name=name, kind=existing[0]['kind']), "field": "name"} if existing and existing[0]["kind"] != "allocation" else None
        pool = run["settings"].get("pool") or []
        linked = [row for row in pool if row.get("kind") in ("run", "library")]
        if blocked is None and linked:
            # Research takes these pools; publishing them (and producing their periods) waits until they can be fed each period
            what = lambda row: f"{row.get('ticker')} ({'a strategy run' if row.get('kind') == 'run' else 'a library item'})"  # noqa: E731
            blocked = {"reason": t("{value}: a published allocation needs every asset to have new prices each period; take it out of the pool to publish", value=', '.join(what(row) for row in linked)), "field": "run"}
        versions = store.item_versions(existing[0]["id"]) if existing and blocked is None else []
        last = versions[0] if versions else None
        before = (store.version(last["versionId"])["meta"].get("published") or {}) if last else {}
        output = self.directory(run_id) / "output"
        work = json.loads((output / "work.json").read_text(encoding="utf-8"))
        now = run["metrics"]
        # (measure, previous, current, unit, signed, digits): a measure with no direction (volatility, turnover, a ratio) is written without a sign
        measures = [("CAGR", before.get("cagr"), now["cagr"], "pct", True, 2), ("Volatility", before.get("vol", before.get("value")), now["vol"], "pct", False, 2),
                    ("Sharpe", before.get("sharpe"), now["sharpe"], "ratio", False, 2), ("Max drawdown", before.get("maxDrawdown"), now["maxDrawdown"], "pct", True, 1),
                    ("vs 60/40", before.get("vs6040"), now.get("vs6040"), "pt", True, 2), ("vs equal weight", before.get("vsEqualWeight"), now.get("vsEqualWeight"), "pt", True, 2),
                    ("Turnover", before.get("turnover"), now["turnover"], "pct", False, 0)]
        if last:  # the settings that differ from the last version, as the draft writes them ("252d → 126d")
            was = store.version(last["versionId"])["meta"].get("allocation", {}).get("settings", {})
            for change in changes_of(was, run["settings"]["params"]):
                if change["name"] in ("pool",) or isinstance(change["from"], (list, dict)) or isinstance(change["to"], (list, dict)):
                    continue
                say = lambda value, name=change["name"]: f"{value}d" if name == "lookback" and isinstance(value, (int, float)) else _words(value) if isinstance(value, str) else value  # noqa: E731
                measures.append((_words(change["name"]).replace("Max weight", "Each asset, at most"), say(change["from"]), say(change["to"]), "text", False, 0))
        held = self.held_out_numbers(run)
        if held:  # the held-out part, once it was used for these settings
            measures += [("Held-out Sharpe", before.get("heldSharpe"), held["sharpe"], "ratio", False, 2), ("Held-out CAGR", before.get("heldCagr"), held["cagr"], "pct", True, 2)]
        else:
            measures.append(("Held-out", None, "not validated", "text", False, 0))
        compare = [{"measure": measure, "previous": was_, "current": current, "change": current - was_ if unit != "text" and isinstance(was_, (int, float)) and isinstance(current, (int, float)) else None,
                    "unit": unit, "signed": signed, "digits": digits}
                   for measure, was_, current, unit, signed, digits in measures if was_ is not None or current is not None]   # a first version lists its own
        import pandas as pd
        last_period = pd.read_parquet(output / "periods.parquet", columns=["date"])["date"].iloc[-1]  # the newest rebalance; the next period starts after the next one
        first = (pd_timestamp(last_period).to_period("M") + 1).to_timestamp("M")  # the end of the month after the newest rebalance
        return {"runId": run_id, "name": name, "version": (last["version"] + 1) if last else 1, "runNumber": run["number"],
                "firstPeriod": {"label": f"{(first + pd.Timedelta(days=1)):%b %Y} from the {first:%b} {first.day} close" + (f"; {first:%B} stays on v{last['version']}" if last else ""), "from": first.strftime("%Y-%m-%d")},
                "checks": [{"name": item["name"], "value": item["value"], "need": item["need"], "state": item["state"]} for item in work["checks"] if item["state"] == "fail"],
                "previous": {"version": last["version"], "publishedAt": last["createdAt"]} if last else None, "compare": compare,
                "users": [{"name": user["name"], "kind": user["kind"]} for user in store.users(last["versionId"])] if last else [], "blocked": blocked}

    def held_out_numbers(self, run):
        """The held-out check of this run's settings, if one was made: its CAGR and Sharpe. Nothing otherwise (the sheet says "not validated")."""
        seen = next((item for item in self.of_object(run["objectId"]) if item["runKind"] == "heldout" and item["state"] == "completed"
                     and item["of"] == run["id"]), None)
        return {"cagr": seen["metrics"]["cagr"], "sharpe": seen["metrics"]["sharpe"]} if seen and seen.get("metrics") else None

    @staticmethod
    def saved_environment(run):
        if not run.get("environment"):
            raise ContractError("unavailable", t("This backtest did not retain its environment. Run a new backtest to fix the method and environment together."))
        return run["environment"]

    async def freeze(self, run_id):
        """The backtest as a research run of the fixed project or standard flow: the saved files as its code, the settings as its
        values, the frozen bars, the research environment's lock, and the weights of every period as the one `weights` publication. The library then cuts the data at
        a few points and runs this code again to look for future data, and commits the version; nothing here writes to the library."""
        import hashlib
        plan = self.plan_publication(run_id)
        if plan["blocked"]:
            raise ContractError("conflict", plan["blocked"]["reason"])
        run = self.read(run_id)
        pool = run["settings"].get("pool") or []
        original = await asyncio.to_thread(self.saved_job, run_id)
        ready = original["data"]
        environment = self.saved_environment(run)
        directory = self.publisher.runs.directory(run_id)
        entry, digest, lock = await asyncio.to_thread(self.freeze_files, directory, ready, environment,
                                                         self.directory(run_id), run.get("allocationCode"))
        params = original["settings"]
        research = {"barsPath": str(directory / "data" / "bars.parquet"), "info": ready["info"]}
        invocation = {"runId": run_id, "title": run["name"], "workbench": "allocation", "entry": entry, "source": str(directory / "source"), "output": str(directory / "output"),
                      "common": str(directory / "common") if run.get("common") else None, "support": None, "mode": "run", "params": params,
                      "riskFree": original["riskFree"], "research": research}
        (directory / "invocation.json").write_text(json.dumps(invocation), encoding="utf-8")
        every = {"weekly": "week", "monthly": "month", "quarterly": "quarter"}.get(params.get("frequency", "monthly"), "month")
        held = self.held_out_numbers(run)
        metrics = run["metrics"]
        # Published with the run's own numbers and its newest period's risk shares (by instrument), what the library follows it on afterwards
        newest = pd.read_parquet(self.directory(run_id) / "output" / "periods.parquet").iloc[-1]
        ids = {row["ticker"]: row["instrumentId"] for row in pool}
        shares = {ids[column[2:]]: float(newest[column]) for column in newest.index if column.startswith("r:")}
        extra = {"value": metrics["vol"], "riskShares": shares, "cagr": metrics["cagr"], "vol": metrics["vol"], "sharpe": metrics["sharpe"], "maxDrawdown": metrics["maxDrawdown"], "turnover": metrics["turnover"], "vs6040": metrics.get("vs6040"), "vsEqualWeight": metrics.get("vsEqualWeight"), "objective": params.get("objective"), "heldCagr": held["cagr"] if held else None,
                 "heldSharpe": held["sharpe"] if held else None}
        return {**run, "runKind": "run", "mode": "run", "objectKind": "allocation", "code": {"entry": entry, "digest": digest, "snapshotPath": str(directory / "source")}, "common": run.get("common"),
                "environment": {key: environment[key] for key in ("state", "preparationId", "interpreterPath", "environmentPath", "pythonVersion") if key in environment},
                "recipe": {"pythonVersion": environment.get("pythonVersion"), "lockPath": str(directory / "execution.lock"), "lockDigest": hashlib.sha256(lock.encode()).hexdigest(), "engine": "research"},
                "params": params, "overrides": {}, "publishedExtra": extra,
                "libraryMeta": {"producer": "allocation", "net": float(params.get("net", 100)) / 100, "rebalance": {"every": every},
                                # The item's main measure is the volatility it publishes and is watched on (library.js: Volatility); the fit's risk measure is its own field.
                                "riskMeasure": params.get("risk_measure"),
                                "allocation": {"mode": run["settings"].get("mode", "standard"), "settings": {**run["settings"]["params"]}, "pool": pool}}}

    @staticmethod
    def freeze_files(directory, ready, environment, run_directory, code):
        import hashlib

        import pandas as pd
        shutil.rmtree(directory, ignore_errors=True)
        shutil.copytree(run_directory / "source", directory / "source")
        if (run_directory / "common").is_dir():
            shutil.copytree(run_directory / "common", directory / "common")
        (directory / "output").mkdir()
        (directory / "data").mkdir()
        entry = code["entry"] if code else "allocation_standard.py"
        digest = code["digest"] if code else hashlib.sha256((directory / "source" / entry).read_bytes()).hexdigest()
        lock = Path(environment["lockPath"]).read_text(encoding="utf-8")
        (directory / "execution.lock").write_text(lock, encoding="utf-8")
        shutil.copyfile(ready["barsPath"], directory / "data" / "bars.parquet")
        periods = pd.read_parquet(run_directory / "output" / "periods.parquet")
        assets = [column[2:] for column in periods.columns if column.startswith("w:")]
        rows = [(int(pd.Timestamp(period["heldFrom"]).tz_localize("UTC").value // 1_000_000), asset, field, float(period[f"{prefix}:{asset}"]))
                for _, period in periods.iterrows() for asset in assets for field, prefix in (("weight", "w"), ("riskShare", "r"))]
        frame = pd.DataFrame(rows, columns=["datetime", "instrument", "field", "value"])
        frame.to_parquet(directory / "output" / "weights.parquet", index=False)
        (directory / "output" / "result-index.json").write_text(json.dumps({"publications": [{"name": "weights", "kind": "allocation", "field": "weight", "path": "weights.parquet", "rows": len(frame)}]}), encoding="utf-8")
        return entry, digest, lock

    async def publish(self, run_id, name=None, auto=True):
        """Publish through the library's own publication (`publish_library_item`): the frozen code is cut at three points and run again, and the version is added in one
        transaction. Waits for it, and says why when it did not end in a version (future data, a name taken, stopped)."""
        plan = self.plan_publication(run_id, name)
        if plan["blocked"]:
            raise ContractError("conflict", plan["blocked"]["reason"])
        record = await self.publisher.start(run_id, {"name": plan["name"], "auto": bool(auto)})
        task = self.publisher.runs.tasks.get(record["id"])
        if task is not None:
            await asyncio.shield(task)
        ended = self.publisher.runs.read(record["id"])
        if ended["state"] != "completed" or not ended["publish"]["versions"]:
            raise ContractError("conflict", ended.get("message") or t("Publishing {name} {state}.", name=plan['name'], state=ended['state']))
        version = self.library.store.version(ended["publish"]["versions"][0])
        return {"versionId": version["id"], "version": version["version"], "name": plan["name"]}

    def published(self, object_id):
        """The newest published version of this allocation and what it holds for its newest period: the published weights by ticker, the review state and the drift."""
        store = self.library.store
        found = [version for version in store.all_versions() if version["object_id"] == object_id and version["archived_at"] is None and version["state"] != "delisted"
                 and store.summary_kind(version["item_id"]) == "allocation"]
        if not found:
            return None
        version = max(found, key=lambda item: item["version"])
        rows = store.latest_available(version["id"], as_of=int(store.clock()), field="weight")
        label = held_from(store.read_values(version["id"], field="weight", as_of=int(store.clock())))
        record = self.records.find(RUN, version["run_id"]) if version.get("run_id") else None
        names = {row.get("instrumentId") or row["ticker"]: row["ticker"] for row in ((record or {}).get("settings", {}).get("pool") or [])}
        meta = version["meta"]
        out = {"versionId": version["id"], "version": version["version"], "name": store.summary_name(version["id"]).rsplit(" v", 1)[0],
               # the weights held now: the newest day's, which are the ones held since `label`
               "weights": {names.get(row["instrument"], row["instrument"]): row["value"] for row in rows if row["datetime"] == max(item["datetime"] for item in rows)},
               "publishedAt": version["created_at"], "published": (meta.get("published") or {})}
        if reviewable(meta, label):
            out.update(period=label, review=(meta.get("reviews") or {}).get(str(label)))
        return out

    # ------------------------------------------------------------------------------------------------------- the monthly review
    async def review(self, args):
        """Accept a published period or replace every frozen asset's weight in one dated adjustment."""
        store = self.library.store
        version = store.version(args["versionId"])
        if store.summary_kind(version["item_id"]) != "allocation":
            raise ContractError("invalid_input", t("Only an allocation's period is reviewed."))
        if version["archived_at"] is not None or version["state"] == "delisted":
            raise ContractError("invalid_input", t("This version is retired; there is nothing to review."))
        label, at = int(args["period"]), int(store.clock())
        instruments = set(store.frame(version["id"], field="weight", datetime=label, columns=["instrument"])["instrument"])
        if not instruments:
            raise ContractError("invalid_input", t("This period has no computed weights."))
        if args["how"] == "accept":
            store.accept_period(version["id"], label, at)
        else:
            reason = (args.get("reason") or "").strip()
            if not reason:
                raise ContractError("invalid_input", t("Write why this period differs."), {"fields": ["reason"]})
            weights = self.adjusted_weights(version["meta"], instruments, args.get("weights") or {})
            store.add_adjustment(version["id"], label, weights, reason, at)
        return {"versionId": version["id"], "period": label, "review": store.version(version["id"])["meta"]["reviews"][str(label)]}

    @staticmethod
    def adjusted_weights(meta, instruments, weights):
        net = float(meta.get("net", 1.0))
        if not weights or abs(sum(float(value) for value in weights.values()) - net) > 1e-6:
            raise ContractError("invalid_input", t("The weights must add up to {net}%.", net=f'{net * 100:g}'), {"fields": ["weights"]})
        aliases = {}
        for rows, field in (((meta.get("info") or {}).get("instruments", []), "column"),
                            ((meta.get("allocation") or {}).get("pool", []), "ticker")):
            for row in rows:
                if row.get("instrumentId") in instruments:
                    aliases.setdefault(row[field], set()).add(row["instrumentId"])
        resolved = {}
        for asset, value in weights.items():
            identity = asset
            if asset not in instruments:
                matches = aliases.get(asset, set())
                if len(matches) != 1:
                    raise ContractError("invalid_input", t("Use a frozen pool instrument ID for {asset}.", asset=asset))
                identity, = matches
            if identity in resolved:
                raise ContractError("invalid_input", t("Duplicate weight for {asset}.", asset=asset))
            resolved[identity] = float(value)
        if resolved.keys() != instruments:
            raise ContractError("invalid_input", t("Give every pool asset a weight, including zero."))
        return resolved

    # ------------------------------------------------------------------------------------------------------- code
    async def write_as_code(self, object_id):
        """"Write my own code": the standard file is copied into the allocation's project; the settings stay in the same places and the run reads the code."""
        obj = self.allocation(object_id)
        if obj["settings"].get("mode") == "code":
            raise ContractError("conflict", t("This allocation is already written as code."))
        developer = await self.store.write_entry(obj, "allocation.py", P.STANDARD.read_text(encoding="utf-8"))
        record = self.store.save(obj, entry="allocation.py", settings={**obj["settings"], "mode": "code"}, developer=developer)
        return self.store.detail(record)

    async def capture_project(self, obj, directory):
        runs = self.factors.runs
        await runs.saved_source(obj)
        captured = await runs.capture_code(obj, directory)
        common = await self.factors.common.capture(self.records.root / "capture", directory / "common")
        roots = [str(directory / "source")] + ([str(directory / "common")] if common else [])
        return {"source": roots[0], "roots": roots, "entry": obj["entry"], "digest": captured["digest"]}, common

    async def start(self, object_id, args=None):
        args = args or {}
        if args.get("kind") == "heldout" and args.get("of"):
            previous = self.records.held_out_for(args["of"])
            if previous is not None:
                return summary(previous)
        obj = self.allocation(object_id)
        kind = args.get("kind", "backtest")
        if kind not in KINDS:
            raise ContractError("invalid_input", t("A run is a backtest or a held-out check."))
        if kind == "heldout":
            status = await self.held_status(object_id, args.get("of"))
            source = self.read(status["runId"])
            record = await self.create(obj, kind, source["settings"]["params"], source["settings"]["pool"], of=source["id"])
        else:
            params = P.values_of(self.store, obj, args.get("changes"))
            pool = P.pool_of(obj, params)
            P.check_values_before_run(params, pool)
            record = await self.create(obj, kind, params, pool)
        self._launch(record["id"])
        return summary(record)

    async def start_sweep(self, object_id, args):
        """A batch of backtests, one for each combination of the values given for one or two settings; each is an ordinary run in the queue, tagged with its cell.
        Nothing is picked as the best: the grid shows them all."""
        import itertools
        obj = self.allocation(object_id)
        axes = [{"name": {"@costMultiple": "cost_multiple"}.get(axis["name"], axis["name"]), "values": list(axis["values"])} for axis in args.get("axes") or []]
        if not 1 <= len(axes) <= 2 or any(len(axis["values"]) < 2 for axis in axes) or len({axis["name"] for axis in axes}) != len(axes):
            raise ContractError("invalid_input", t("A sweep gives one or two settings, each with at least two values."))
        combos = list(itertools.product(*[axis["values"] for axis in axes]))
        sweep_id = "sweep-" + uuid4().hex
        planned = []
        for index, combo in enumerate(combos):
            changes = {axis["name"]: value for axis, value in zip(axes, combo, strict=True)}
            params = P.values_of(self.store, obj, group_caps(P.values_of(self.store, obj, {}), changes))
            P.check_values_before_run(params, P.pool_of(obj, params))
            planned.append((index, changes, params))
        # the cells of a sweep over the lookback all start where the longest can, so they are compared over the same days
        warmup = max((int(float(value)) for axis in axes if axis["name"] == "lookback" for value in axis["values"]), default=None)
        out = []
        for index, changes, params in planned:
            record = await self.create(obj, "backtest", params, P.pool_of(obj, params), sweep={"id": sweep_id, "index": index, "of": len(combos), "axes": [axis["name"] for axis in axes],
                                                                                           "cell": changes, "warmup": warmup})
            out.append(record)

        launched = asyncio.get_running_loop().create_future()

        async def launch_batch():
            from .runtime import ACTIVITY
            try:
                for item in out:
                    self._launch(item["id"],activity=ACTIVITY.get())
            except BaseException as error:
                launched.set_exception(error)
                raise
            launched.set_result(None)

        self.ctx.start(launch_batch(), f"Allocation sweep · {obj['name']}", run_record=out[0])
        await launched
        return {"sweepId": sweep_id, "runs": [summary(r) for r in out]}

    async def create(self, obj, kind, params, pool, of=None, sweep=None):
        params = {**params, "costs": P.costs_of(params, pool)}   # recorded as charged: a row for each group of the pool
        identity = "run-" + uuid4().hex
        self.directory(identity).mkdir(parents=True, exist_ok=True)
        now = int(time.time() * 1000)
        mode = obj["settings"].get("mode", "standard")
        code, common = None, None
        if of:
            source = self.read(of)
            data = copy.deepcopy(source["settings"]["data"])
            mode = source["settings"].get("mode", "standard")
            code, common = source.get("allocationCode"), source.get("common")
            self.keep_code(identity, {"source": self.directory(of) / "source"})
            if common:
                shutil.copytree(self.directory(of) / "common", self.directory(identity) / "common")
            if code:
                roots = [str(self.directory(identity) / "source")] + ([str(self.directory(identity) / "common")] if common else [])
                code = {**code, "source": roots[0], "roots": roots}
        else:
            data = copy.deepcopy(obj["settings"].get("data") or {})
            data = await resolve_until(self.ctx, data, instruments=await self.data.calendar_items(data))
            if mode == "code":
                code, common = await self.capture_project(obj, self.directory(identity))
            else:
                self.keep_code(identity, None)
        # the run before is the object's last backtest of its own, not a cell of a sweep (a run was set beside one cell of a sweep)
        previous = next((item for item in self.of_object(obj["id"]) if item["runKind"] == "backtest" and not item.get("sweep")), None)
        record = {"id": identity, "objectId": obj["id"], "objectKind": "allocation", "allocation": True, "name": obj["name"], "runKind": kind, "number": self.next_number(obj["id"]),
                  "workspaceId": self.store.workspace(), "state": "queued", "stage": "queued", "createdAt": now, "startedAt": None, "endedAt": None, "seconds": None,
                  "allocationCode": code, "common": common,
                  "settings": {"mode": mode, "data": data, "params": copy.deepcopy(params), "pool": pool}, "progress": {"done": 0, "of": None, "stage": "queued"}, "metrics": None,
                  "changes": run_changes(previous["settings"], {"data": data, "pool": pool, "params": params}) if previous else [], "failure": None, "starred": False, "note": "",
                  "of": of, "heldOut": None, "version": obj["version"], "stopped": None, "removed": False, "notes": [], "message": None,
                  "data": None, "estimate": None, "queue": None, "sweep": sweep}
        record = self.records.put(RUN, record)
        self.ctx.publish("object_run_changed", {"runId": identity, "objectId": obj["id"], "workspaceId": record["workspaceId"], "state": "queued"})
        return record

    def _launch(self, identity, *, activity=None):
        self.tasks[identity] = self.ctx.start(self.execute(identity), run_record=self.read(identity),activity=activity)

    def saved_job(self, identity):
        path = self.directory(identity) / "engine" / "invocation.json"
        if not path.is_file():
            raise ContractError("unavailable", t("The frozen inputs of this allocation run are no longer available."))
        job = json.loads(path.read_text(encoding="utf-8"))["job"]
        if not Path(job["data"]["barsPath"]).is_file():
            raise ContractError("unavailable", t("The price data used by this allocation run are no longer available."))
        return job

    async def execute(self, identity):
        started = time.perf_counter()
        ending = {}
        try:
            record = self.read(identity)
            output = self.directory(identity) / "output"
            output.mkdir(parents=True, exist_ok=True)
            for name in ("progress.jsonl", "answer.json", "periods.jsonl"):
                (output / name).unlink(missing_ok=True)
            async with self.queue.slot(owner=identity, label=f"{record['name']} #{record['number']}", interactive=False, memory=2 * GB,
                                       on_wait=lambda position: self.update(identity, stage="queued", queue={"position": position})):
                self.update(identity, state="preparing", stage="data", startedAt=int(time.time() * 1000), queue=None)
                record = self.read(identity)
                if record["of"]:
                    original = await asyncio.to_thread(self.saved_job, record["of"])
                    job = {**original, "output": str(output), "kind": record["runKind"]}
                    pool = job["settings"]["pool"]
                else:
                    ready, extras = await self.prepare_data(record["settings"]["data"], record["settings"]["params"], record["settings"]["pool"], bool(record.get("allocationCode")))
                    columns = {item.get("instrumentId"): item["column"] for item in ready["info"].get("instruments", [])}
                    pool = [{**row, "ticker": columns.get(row.get("instrumentId"), row["ticker"])} for row in record["settings"]["pool"]]
                    ready = await self.with_links(ready, pool, output)
                    held = (record["settings"]["data"] or {}).get("heldOutFrom")
                    job = {"output": str(output), "kind": record["runKind"], "data": {key: ready[key] for key in ("key", "barsPath", "info", "extrasPath")},
                           "settings": {**record["settings"]["params"], "pool": pool, **extras}, "heldOutFrom": held, "warmup": (record.get("sweep") or {}).get("warmup"),
                           "riskFree": await P.risk_free(self.ctx, record["settings"]["params"], ready["dataFrom"], ready["dataTo"], f"qallocation-run:{identity}")}
                if record.get("allocationCode"):
                    job["code"] = record["allocationCode"]
                self.update(identity, stage="environment", info=job["data"]["info"], settings={**record["settings"], "pool": pool})
                if record["of"]:
                    environment = self.saved_environment(self.read(record["of"]))
                else:
                    environment = await self.packages.research_environment()
                lock = self.directory(identity) / "execution.lock"
                shutil.copyfile(environment["lockPath"], lock)
                environment = {**{key: environment[key] for key in ("state", "preparationId", "interpreterPath", "environmentPath", "pythonVersion")},
                               "lockPath": str(lock)}
                self.ctx.environments.hold(environment["preparationId"], "qrun:" + identity)
                self.update(identity, state="running", stage="backtest", environment=environment)
                follower = asyncio.create_task(self.follow(identity, output))
                try:
                    await self.engine.run_once(job, self.directory(identity) / "engine", context={"objectId": record["objectId"], "runId": identity},
                                               environment=environment, script=WORKER, prefix="qallocation-")
                finally:
                    follower.cancel()
            answer = json.loads((output / "answer.json").read_text(encoding="utf-8")) if (output / "answer.json").is_file() else {"ok": False, "failure": {"message": t("The process stopped before it answered.")}}
            if not answer.get("ok"):
                failure = answer.get("failure") or {"message": t("The run failed.")}
                ending = {"state": "failed", "message": failure["message"], "failure": {"stage": failure.get("stage", "compute"), "title": failure.get("title", "stopped"),
                                                                                         "message": failure["message"], "fields": failure.get("fields") or []}}
            else:
                work = json.loads((output / "work.json").read_text(encoding="utf-8"))
                ending = {"state": "completed", "stage": "done", "message": None, "metrics": work["metrics"], "notes": work["notes"], "failure": None,
                          "seconds": round(time.perf_counter() - started, 2), "data": {**(self.read(identity).get("data") or {}), "dataFrom": work["from"], "dataTo": work["to"],
                                                                   # What a run's answer reports as tested: the days of the equity curve the worker wrote and the assets of the pool.
                                                                   "bars": pq.read_metadata(output / "equity.parquet").num_rows if (output / "equity.parquet").is_file() else None,
                                                                   "names": len(pool)}}
        except asyncio.CancelledError:
            by_hand = identity in self.stopping
            done = len(self.periods_done(identity))
            ending = {"state": "cancelled" if by_hand else "interrupted", "message": t("Stopped at period {done}{identity}; the periods so far are kept.", done=done + 1, identity=self.of_periods(identity)) if by_hand
                      else t("Quant stopped; the periods so far are kept."), "stopped": {"period": done, "at": int(time.time() * 1000), "auto": not by_hand}}
        except ContractError as error:
            ending = {"state": "failed", "message": error.message, "failure": {"stage": "data", "title": t("stopped"), "message": error.message, "fields": []}}
        except Exception as error:  # noqa: BLE001 - a background run must end and say what broke
            import logging
            logging.getLogger(__name__).exception("Allocation run failed (%s)", identity)
            message = f"Quant hit an internal error ({type(error).__name__}) and stopped this run; the details are in Quant's log."
            ending = {"state": "failed", "message": message, "failure": {"stage": "compute", "title": t("stopped"), "message": message, "fields": []}}
        finally:
            self.tasks.pop(identity, None)
            self.stopping.discard(identity)
            if ending:
                record = self.read(identity)
                self.save(record, **{"seconds": round(time.perf_counter() - started, 2), **ending, "endedAt": int(time.time() * 1000)})
                if record.get("sweep") and ending.get("state") == "completed":
                    self.mark_weights_move(record["objectId"], record["sweep"]["id"])

    def mark_weights_move(self, object_id, sweep_id):
        """Put each finished cell's `move` (how far its target weights sit from its neighbours', pt a period) on the cell; it changes as neighbours finish."""
        cells = sorted((item for item in self.of_object(object_id) if (item.get("sweep") or {}).get("id") == sweep_id), key=lambda item: item["sweep"]["index"])
        if not cells:
            return
        axes = cells[0]["sweep"]["axes"]
        order = [list(dict.fromkeys(str(item["sweep"]["cell"][axis]) for item in cells)) for axis in axes]
        place = {item["id"]: (order[0].index(str(item["sweep"]["cell"][axes[0]])), order[1].index(str(item["sweep"]["cell"][axes[1]])) if len(axes) > 1 else 0) for item in cells}
        done = {place[item["id"]]: {row["date"]: row["weights"] for row in self.periods_done(item["id"])} for item in cells if item["state"] == "completed"}
        move = S.weights_move(done)
        for item in cells:
            if item["state"] == "completed":
                value = move.get(place[item["id"]])
                self.save(self.read(item["id"]), sweep={**item["sweep"], "move": None if value is None else round(value, 4)})

    async def follow(self, identity, output):
        seen = 0
        while True:
            await asyncio.sleep(1)
            path = output / "progress.jsonl"
            if path.is_file():
                lines = [line for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]
                if len(lines) > seen:
                    seen = len(lines)
                    last = json.loads(lines[-1])
                    self.update(identity, progress={"done": last["done"], "of": last["of"], "stage": last["stage"]})

    def periods_done(self, identity):
        path = self.directory(identity) / "output" / "periods.jsonl"
        return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()] if path.is_file() else []

    async def stop(self, identity):
        task = self.tasks.get(identity)
        record = self.read(identity)
        if record["state"] in TERMINAL or task is None:
            return summary(record)
        self.stopping.add(identity)
        self.save(record, state="cancel_requested")
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)
        return summary(self.read(identity))

    def recover(self, workspace_id=None):
        rows = self.records.db.execute("SELECT content FROM records WHERE kind='qrun' AND json_extract(content,'$.allocation')=1 AND json_extract(content,'$.state') IN "
                                       "('running','preparing','queued','cancel_requested')").fetchall()
        for row in rows:
            record = json.loads(row[0])
            if record["id"] not in self.tasks and (workspace_id is None or record["workspaceId"] == workspace_id):
                done = len(self.periods_done(record["id"]))
                self.save(record, state="interrupted", message=f"Interrupted; the {done} periods so far are kept.", stopped={"period": done, "at": int(time.time() * 1000), "auto": True})

    # ------------------------------------------------------------------------------------------------------- reading
    def list(self, object_id):
        self.allocation(object_id)
        # Once a process for each workspace: a run made a moment ago is in the records before its task is, and is not one this process left behind.
        with contextlib.suppress(Exception):
            workspace = self.store.workspace()
            if workspace not in self.recovered:
                self.recover(workspace)
                self.recovered.add(workspace)
        return {"items": [summary(record) for record in self.of_object(object_id)], "now": int(time.time() * 1000)}

    def of_periods(self, identity):
        """" of 113" from the run's own progress file, or nothing when it never wrote one."""
        path = self.directory(identity) / "output" / "progress.jsonl"
        try:
            lines = path.read_text(encoding="utf-8").strip().splitlines()
            return f" of {json.loads(lines[-1])['of']}" if lines else ""
        except (OSError, ValueError, KeyError):
            return ""

    def work(self, identity):
        """The run for the Work page: its summary and settings; when finished the key numbers, this period, checks and the every-period figure; when stopped,
        the periods so far."""
        record = self.read(identity)
        output = self.directory(identity) / "output"
        out = {"run": {**summary(record), "settings": record["settings"]}, "work": None, "periods": None}
        path = output / "work.json"
        if record["state"] == "completed" and path.is_file():
            out["work"] = named_by_run(json.loads(path.read_text(encoding="utf-8")), record["number"])
        elif record["state"] in ("cancelled", "interrupted"):
            out["periods"] = self.periods_done(identity)
        elif record["state"] == "completed":
            out["old"] = f"Run #{record['number']} was made by an earlier TALYX; run it again to see it here"
        return out

    def keep_code(self, identity, code):
        """The code this run ran, kept beside it for "Show code" (the project's files written as code, else the standard flow's one file): later edits or a newer
        standard flow do not change what an earlier run shows."""
        kept = self.directory(identity) / "source"
        shutil.rmtree(kept, ignore_errors=True)
        if code:
            shutil.copytree(code["source"], kept, ignore=shutil.ignore_patterns("__pycache__", "*.pyc"))
        else:
            kept.mkdir(parents=True)
            shutil.copyfile(MODULE / "native" / "allocation_standard.py", kept / "allocation_standard.py")

    def code(self, identity):
        """The code a run ran, read-only: its snapshot's files (the standard flow is the one file it copies in)."""
        self.read(identity)
        source = self.directory(identity) / "source"
        return {"files": [{"file": path.relative_to(source).as_posix(), "text": path.read_text(encoding="utf-8", errors="replace")} for path in sorted(source.rglob("*.py"))] if source.is_dir() else []}

    def results(self, identity, tab):
        if tab not in TABS:
            raise ContractError("invalid_input", t("A tab is one of {tabs}.", tabs=', '.join(TABS)))
        record = self.read(identity)
        path = self.directory(identity) / "output" / "results" / f"{tab}.json"
        if record["state"] != "completed" or not path.is_file():
            if tab == "style":
                return {"tab": tab, "style": None}
            raise ContractError("not_found", t("This run has no Results yet."))
        body = json.loads(path.read_text(encoding="utf-8"))
        if tab == "robust":
            body["heldOut"] = self.held_out_of(record)
        return {"tab": tab, **named_by_run(body, record["number"])}

    def held_out_of(self, record):
        """The held-out check made for this run's settings, for the Robustness tab: its dates, the numbers beside the in-sample ones and the growth lines. None until one was made."""
        seen = next((item for item in self.of_object(record["objectId"]) if item["runKind"] == "heldout" and item["state"] == "completed"
                     and item["of"] == record["id"]), None)
        path = self.directory(seen["id"]) / "output" / "work.json" if seen else None
        holdout = json.loads(path.read_text(encoding="utf-8")).get("holdout") if path and path.is_file() else None
        if not holdout:
            return None
        holdout = named_by_run(holdout, record["number"])
        figure = {**holdout["growth"], "layout": {**holdout["growth"]["layout"], "meta": {**holdout["growth"]["layout"].get("meta", {}), "key": "allocation.results.heldout"}}} if "layout" in holdout["growth"] else holdout["growth"]
        return {"runId": seen["id"], "number": seen["number"], "from": holdout["from"], "to": holdout["to"], "figure": figure, "inSample": record["metrics"], "held": holdout["rows"]["held"]}

    def period(self, identity, index):
        rows = self.periods_done(identity)
        if not 0 <= index < len(rows):
            raise ContractError("not_found", t("There is no such period."))
        return rows[index]

    def annotate(self, identity, args):
        record = self.read(identity)
        changes = {key: args[key] for key in ("note", "starred") if key in args}
        return summary(self.save(record, **changes))

    async def add_shock(self, identity, target, pct):
        """One more shock on a finished run's scenarios table, at once, from the covariance the run kept; the shock is also written to the allocation's settings so the next run has it.
        Returns the row."""
        from modules.quant.native.allocation_shock import shock_row
        record = self.read(identity)
        if record["state"] != "completed":
            raise ContractError("conflict", t("A shock is added to a finished run."))
        try:
            pct = float(pct)
        except (TypeError, ValueError):
            raise ContractError("invalid_input", t("A shock is a percent, like -20."), {"fields": ["pct"]}) from None
        if not -100 < pct or pct == 0:
            raise ContractError("invalid_input", t("A shock is a percent above -100 and not 0."), {"fields": ["pct"]})
        directory = self.directory(identity) / "output"
        basis_path, risk_path = directory / "shock-basis.json", directory / "results" / "risk.json"
        if not basis_path.is_file() or not risk_path.is_file():
            raise ContractError("not_found", t("This run has no scenarios to add to; run it again."))
        basis = json.loads(basis_path.read_text(encoding="utf-8"))
        row = shock_row(basis["names"], basis["weights"], basis["cov"], basis["groups"], target, pct)
        if row is None:
            raise ContractError("invalid_input", t("{target} is not an asset or a group of the pool.", target=target), {"fields": ["target"]})
        risk = json.loads(risk_path.read_text(encoding="utf-8"))
        scenarios = risk.get("scenarios")
        if isinstance(scenarios, list):
            if not any(item["scenario"] == row["scenario"] for item in scenarios):
                scenarios.append(row)
                risk_path.write_text(json.dumps(risk), encoding="utf-8")
        obj = self.store.read(record["objectId"])
        shocks = list(obj["settings"].get("params", {}).get("shocks") or [])
        if not any(item.get("target") == target and float(item.get("pct")) == pct for item in shocks):
            await self.store.update_settings(record["objectId"], {"params": {**obj["settings"].get("params", {}), "shocks": shocks + [{"target": target, "pct": pct}]}})
        return row

    async def apply(self, identity, expected_version=None):
        """Put a run's settings back on the allocation (the run list's "Use these settings")."""
        record = self.read(identity)
        from .run_restore import restore_files

        obj = self.allocation(record["objectId"])
        self.store.check_version(obj, expected_version)
        mode = record["settings"].get("mode", "standard")
        source = self.directory(identity) / "source" if mode == "code" else None
        report = await restore_files(self.ctx, obj, source, record.get("common"), self.factors.common.root)
        self.store.check_version(self.store.read(obj["id"]), obj["version"])
        settings = {**obj["settings"], "mode": mode, "params": record["settings"]["params"], "data": record["settings"]["data"]}
        return {**self.store.detail(self.store.save(obj, settings=settings)), "restore": report}

    async def close(self):
        for task in list(self.tasks.values()):
            task.cancel()
