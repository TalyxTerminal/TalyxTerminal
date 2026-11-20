"""The Market panel's private route (``market.panel``): what the panel shows and the user-only steps.

The library rows come from the same code as ``read_data(view=local)``, so both give the
same numbers. Folders the user names here may be anywhere on disk; a relative folder is
inside the workspace's folder.
"""

from __future__ import annotations

import shutil

import numpy as np

from vocabulary.instruments import MarketInstrument, MarketSeries, market_data_series, ohlcv_series

from . import bars, library
from .common import MarketError, iso_of_ns, now_ns
from .library import time_text
from .routing import in_worker
from .store import SeriesKey, Spans

LIBRARY_COLUMNS = ("instrumentId", "symbol", "venue", "kind", "timeframe", "rows", "first", "last", "gaps", "bytes",
                   "source", "status", "name", "delistedAt", "importId", "former")


class Panel:
    def __init__(self, market):
        self.market = market

    async def call(self, value: dict, workspace_id=None) -> dict:
        op = value["op"]
        handler = getattr(self, "_" + op, None)
        if handler is None:
            raise MarketError("invalid_input", f"Unknown panel operation {op}")
        return await handler(value, workspace_id)

    # ── library ─────────────────────────────────────────────────────────────

    async def _library(self, value: dict, _ctx) -> dict:
        query = {key: value[key] for key in ("market", "venue", "kind", "query", "status") if value.get(key)}
        # Thousands of rows each read their identity record and codes: off the event loop (the panel asks every
        # 2 s while a fetch runs, and on the loop that stalled live charts by up to half a second).
        return await in_worker(self.library_view, query)

    def library_view(self, query: dict) -> dict:
        """Every stored series as one row of LIBRARY_COLUMNS: what the list shows and filters on, nothing more.

        The answer goes over the bridge (2 MB a frame) every 2 s while a fetch runs; rows carrying whole identity
        records made 1,800 series 2.4 MB. Records and series ids for fetch and export come from ``selections``.
        """
        series = library.series_rows(self.market, query)
        identity = self.market.identity
        rows = []
        for row in series:
            record = row["_record"]
            former = [item for item in identity.codes(row["instrumentId"]) if item["code"] != row["symbol"]] \
                if record.get("identityVersion") == 2 else []
            values = {**row, "name": record.get("name"), "delistedAt": record.get("delistedAt"),
                      "importId": row["kind"][len("import:"):] if row["kind"].startswith("import:") else None,
                      "former": [{"code": item["code"], "until": item.get("until")} for item in former] or None}
            rows.append([values[name] for name in LIBRARY_COLUMNS])
        datasets = library.dataset_rows(series)
        for item in datasets:
            item.pop("_first", None)
            item.pop("_last", None)
        imports = [self.market.write_data.imports.read(i) for i in self.market.write_data.imports.ids()]
        return {"series": {"columns": list(LIBRARY_COLUMNS), "rows": rows}, "datasets": datasets,
                "imports": [i for i in imports if i], "now": str(now_ns())}

    async def _selections(self, value: dict, _ctx) -> dict:
        """The instrument records and series ids of library rows the user chose, as fetch and export take them."""
        def build():
            out = []
            for item in value["series"]:
                record = self.market.instrument_record({"instrumentId": item["instrumentId"]})
                kind = f"import:{item['importId']}" if item.get("importId") else item["kind"]
                out.append({"instrument": record, "series": _series_of(record, {"kind": kind, "timeframe": item["timeframe"]})})
            return out
        return {"selections": await in_worker(build)}

    async def _detail(self, value: dict, _ctx) -> dict:
        """One stored series: its record, stored spans and gaps, codes over time, actions and latest rows."""
        record = self.market.instrument_record({"instrumentId": value["instrumentId"]}) \
            if "instrumentId" in value else None
        if record is None:
            raise MarketError("not_found", "No such instrument")
        key = SeriesKey(record["instrumentId"], value.get("capability", "ohlcv"), value.get("priceBasis", "last"),
                        value.get("sessionScope", "continuous" if record["identityVersion"] == 1 else "regular"),
                        value.get("timeframe"), value.get("importId"))
        store = self.market.store
        pointer = store.pointer(key)
        if pointer is None:
            raise MarketError("not_found", "Nothing stored for this series")
        calendar = record.get("calendarId", "24_7")
        first, last = pointer["first"], pointer["last"]
        gaps = [{"from": time_text(calendar, key.timeframe, int(start)), "to": time_text(calendar, key.timeframe,
                                                                                       int(end) - 1),
                 "fillable": True} for start, end in Spans.missing(pointer["spans"], int(first), int(last) + 1)] \
            if first is not None else []
        rows = []
        if key.capability == "ohlcv":
            model = MarketInstrument.from_wire(record)
            adjustment = value.get("adjustment") if record.get("adjustmentEligible") and not key.import_id else None
            if key.import_id:
                series = MarketSeries(instrumentId=record["instrumentId"], capability="ohlcv", timeframe=key.timeframe,
                                      priceBasis=key.price_basis, sessionScope=key.session_scope,
                                      adjustment="raw" if record.get("adjustmentEligible") else "not_applicable",
                                      importId=key.import_id)
            else:
                series = ohlcv_series(model, key.timeframe or "1d", adjustment=adjustment or None)
            end = int(value["before"]) if value.get("before") else None
            page = await self.market.data.page(record, series, end=end, limit=int(value.get("limit", 40)),
                                               fill="none")
            columns = page["columns"]
            for index in range(len(columns["time"])):
                rows.append({"time": time_text(calendar, key.timeframe, int(columns["time"][index])),
                             **{name: columns[name][index] for name in ("open", "high", "low", "close", "volume")
                                if name in columns}})
        actions = []
        if record.get("adjustmentEligible") and not key.import_id:
            daily = SeriesKey(record["instrumentId"], "ohlcv", "last", key.session_scope, "1d")
            times, ratios, dividends = await in_worker(self.market.actions.factors, record["instrumentId"],
                                                       daily, calendar)
            doc = self.market.actions.read(record["instrumentId"])
            by_time = {}
            for row in doc["rows"]:
                if row["kind"] != "factor":
                    by_time.setdefault(int(row["exDate"]), []).append(row)
            for ex, ratio, dividend in zip(times.tolist(), ratios.tolist(), dividends.tolist(), strict=True):
                events = by_time.get(ex, [])
                actions.append({"date": time_text(calendar, "1d", ex), "events": [
                    {"kind": row["kind"], "ratio": row.get("ratio"), "amount": row.get("amount"),
                     "currency": row.get("currency") or record.get("currency")} for row in events],
                    "factor": bars.decimal_text(np.asarray([dividend / ratio]))[0]})
        codes = self.market.identity.codes(record["instrumentId"]) if record["identityVersion"] == 2 else []
        return {"instrument": record, "source": pointer.get("source"), "rows": pointer.get("rows"),
                "bytes": pointer.get("bytes"), "first": time_text(calendar, key.timeframe, first),
                "last": time_text(calendar, key.timeframe, last), "gaps": gaps, "actions": actions,
                "codes": [{"code": item["code"], "from": iso_of_ns(int(item["from"]))[:10] if item.get("from") else None,
                           "until": iso_of_ns(int(item["until"]))[:10] if item.get("until") else None,
                           "name": item.get("name")} for item in codes],
                "latest": rows}

    # ── sources ─────────────────────────────────────────────────────────────

    async def _sources(self, _value: dict, _ctx) -> dict:
        """read_source's list with what the panel needs to draw each row (key kind, local program, coverage)."""
        admin, out = self.market.admin, []
        for source_id in sorted(self.market.registry.entries):
            entry = self.market.registry.entry(source_id)
            details = admin.details(source_id)
            out.append({**admin.row(source_id), "credentials": entry.descriptor["credentials"],
                        "localGateway": bool(entry.descriptor.get("localGateway")),
                        "coverage": details["coverage"]["rows"], "settings": details["settings"],
                        "lastTest": details["lastTest"]})
        return {"sources": out, "selection": admin.selection()}

    async def _configure(self, value: dict, _ctx) -> dict:
        """Settings typed in the panel, keys included (the user's own hand; AI cannot set keys)."""
        await self.market.admin.configure(value["source"], value["settings"], allow_secrets=True)
        return {"changed": True}

    # ── jobs, disk, folders ─────────────────────────────────────────────────

    async def _jobs(self, _value: dict, workspace_id) -> dict:
        # The panel polls this every few seconds: a job's request names its first target and counts the rest
        # (an "update all" job listed a thousand instruments, 1.1 MB on every poll).
        return {"jobs": [self.market.jobs.summary(record) for record in self.market.jobs.list(workspace_id)[:50]]}

    async def _cancel(self, value: dict, workspace_id) -> dict:
        return await self.market.jobs.control("cancel", value["jobId"], workspace_id)

    async def _resume(self, value: dict, workspace_id) -> dict:
        return await self.market.jobs.control("resume", value["jobId"], workspace_id)

    async def _disk(self, value: dict, ctx) -> dict:
        folder = await self.market.folder(value.get("folder"), ctx) if value.get("folder") else self.market.root
        target = folder
        while not target.exists() and target.parent != target:
            target = target.parent
        usage = await in_worker(shutil.disk_usage, target)
        return {"drive": target.anchor or str(target), "free": usage.free, "folder": str(folder)}

    async def _folder(self, value: dict, ctx) -> dict:
        return {"folder": str(await self.market.folder(value.get("folder"), ctx))}

    async def _scan(self, value: dict, workspace_id) -> dict:
        folder = await self.market.folder(value.get("folder"), workspace_id)
        return await self.market.write_data.scan_folder(folder, value.get("spec") or {})

    async def _import(self, value: dict, workspace_id) -> dict:
        folder = await self.market.folder(value.get("folder"), workspace_id)
        return await self.market.write_data.import_folder(folder, value.get("spec") or {})


def _series_of(record: dict, row: dict) -> dict:
    """The series a library row stores, as callers name it (export, chart)."""
    model = MarketInstrument.from_wire(record)
    if row["kind"].startswith("import:"):
        return MarketSeries(instrumentId=record["instrumentId"], capability="ohlcv", timeframe=row["timeframe"],
                            sessionScope="continuous" if record["identityVersion"] == 1 else "regular",
                            adjustment="raw" if record.get("adjustmentEligible") else "not_applicable",
                            importId=row["kind"][len("import:"):]).to_dict()
    if row["kind"] == "ohlcv":
        return ohlcv_series(model, row["timeframe"] or "1d").to_dict()
    return market_data_series(model, row["kind"], timeframe=row["timeframe"]).to_dict()
