"""Public Market requests adapted to the same services used by the panel."""

from __future__ import annotations

from datetime import date

from . import calendars, library
from .common import MarketError
from .public_data import PublicData, columns, iso, local_day, make_series
from .public_identity import fits, instant, offset
from .research import KINDS
from .routing import in_worker


def clocks(value, fields):
    if isinstance(value, list):
        return [clocks(item, fields) for item in value]
    if isinstance(value, dict):
        if value.get("bounds") == "[)" and value.get("unit") == "ns":
            return {"start": iso(value["start"]), "end": iso(int(value["end"]) - 1), "bounds": "[]"}
        return {
            key: iso(item) if key in fields and item is not None else clocks(item, fields)
            for key, item in value.items()
        }
    return value


def table_page(entries, request):
    start = offset(request)
    names = list(dict.fromkeys(key for row in entries for key in row if not key.startswith("_")))
    result = {
        "columns": columns(names),
        "meta": [],
        "total": len(entries),
        "rows": [],
        "nextCursor": None,
    }
    for entry in entries[start : start + request.get("limit", len(entries))]:
        result["rows"].append([entry.get(name) for name in names])
        # Measured with the cursor a cut page would carry, so setting it afterwards cannot push the page over.
        if not fits({**result, "nextCursor": str(start + len(result["rows"]))}):
            result["rows"].pop()
            if not result["rows"]:
                raise MarketError("too_large", "The first row does not fit a page")
            break
    end = start + len(result["rows"])
    result["nextCursor"] = str(end) if end < len(entries) else None
    return result


class PublicActions(PublicData):
    async def sessions(self, request):
        page = await in_worker(
            calendars.sessions,
            request["calendarId"],
            date.fromisoformat(request["startDate"]),
            date.fromisoformat(request["endDate"]),
        )
        start = offset(request)
        rows = page.pop("sessions")
        end = min(start + request.get("limit", 24), len(rows))
        sessions = []
        for row in rows[start:end]:
            sessions.append(
                {
                    **row,
                    "open": iso(row["open"]),
                    "close": iso(row["close"]),
                    "breaks": [
                        {"start": iso(b["start"]), "end": iso(b["end"])} for b in row["breaks"]
                    ],
                }
            )
        return {**page, "sessions": sessions, "nextCursor": str(end) if end < len(rows) else None}

    async def series(self, request):
        params = {"limit": 48, **request}
        identity = params.pop("instrumentId")
        if params["series"]["instrumentId"] != identity:
            raise MarketError("invalid_input", "series.instrumentId must match instrumentId")
        params["instrument"] = {"instrumentId": identity}
        if params.get("end") is not None:
            params["end"] = str(int(params["end"]) + 1)
        return await self.market.data.read(params, encoded=True)

    async def terms(self, request):
        entries = request.get("series") or [
            {"instrumentId": symbol} for symbol in request["symbols"]
        ]
        start = offset(request)
        items = []
        stamp = None
        for entry in entries[start : start + request.get("limit", 24)]:
            record = await self.record(entry["instrumentId"])
            value = {
                "instruments": [record],
                "allowMissingPrices": request.get("allowMissingPrices", False),
            }
            if request.get("start") or request.get("end"):
                if not (request.get("start") and request.get("end")):
                    raise MarketError(
                        "invalid_input", "Stock execution materials require start and end"
                    )
                value.update(
                    series=[entry.get("series") or make_series(record, {}).to_dict()],
                    start=str(instant(request["start"], record["calendarId"])),
                    end=str(instant(request["end"], record["calendarId"], inclusive_end=True)),
                )
            page = await self.market.terms.read(value)
            stamp = page["asOf"]
            item = clocks(
                page["items"][0],
                {"acquiredAt", "openAt", "closeAt", "effectiveTime", "publishedAt"},
            )
            item["symbol"] = await in_worker(self.spelling, record)
            items.append(item)
        end = start + len(items)
        return {
            "items": items,
            "asOf": iso(stamp),
            "nextCursor": str(end) if end < len(entries) else None,
        }

    async def coverage(self, request):
        entries = []
        data = request.get("data")
        if request.get("symbols"):
            for symbol in request["symbols"]:
                record = await self.record(symbol, environment=request.get("environment", "production"))
                label = await in_worker(self.spelling, record)
                if data is None or data not in KINDS:
                    params = {"instrument": record, "environment": request.get("environment", "production")}
                    if data:
                        params["capabilities"] = ["ohlcv" if data == "bars" else data]
                    response = await self.market.data.coverage(params)
                    for item in response["items"]:
                        if request.get("timeframe") and request["timeframe"] != item["timeframe"]:
                            continue
                        kind = "bars" if item["capability"] == "ohlcv" else item["capability"]
                        entries.append(
                            {
                                "symbol": label,
                                "instrumentId": record["instrumentId"],
                                "data": kind,
                                **clocks(
                                    item,
                                    {"start", "end", "earliest", "publishedThrough", "retryAt"},
                                ),
                            }
                        )
                if data is None or data in KINDS:
                    info = await self.market.research.describe(
                        {"instruments": [record], "kinds": [data] if data else None}
                    )
                    for row in info["kinds"]:
                        entries.append(
                            {
                                "symbol": label,
                                "instrumentId": record["instrumentId"],
                                "data": row["kind"],
                                **clocks(row, {"start", "end"}),
                            }
                        )
        elif data == "members":
            info = await self.market.research.describe({"kinds": ["members"]})
            for index in info["indices"]:
                record = index["index"]
                entries.append(
                    {
                        "data": "members",
                        "instrumentId": record["instrumentId"],
                        "symbol": self.spelling(record),
                        "name": record.get("name"),
                        "source": index["source"],
                        "start": local_day(index["start"], "24_7"),
                        "end": local_day(index["end"], "24_7"),
                        "pointInTime": index["pointInTime"],
                        "note": index["note"],
                    }
                )
        else:
            entries = await in_worker(
                library.library,
                self.market,
                {**request, "kind": "ohlcv" if data == "bars" else data},
            )
        return table_page(entries, request)

    async def research(self, request):
        params = {
            k: request[k] for k in ("market", "metrics", "periodKinds", "fill") if k in request
        }
        params["kind"] = request["data"]
        if ("metrics" in request or "periodKinds" in request) and request["data"] != "financials":
            raise MarketError("invalid_input", "metrics and periodKinds apply only to financials")
        records = [await self.record(s) for s in request.get("symbols", [])]
        if request.get("index"):
            index = await self.record(request["index"]["symbol"])
            if request["data"] == "members":
                params["index"] = index
            else:
                records = [record for record, _, _ in await self.selections(request)]
            calendar = index["calendarId"]
        else:
            from .codes import MARKET_CALENDARS

            calendar = records[0]["calendarId"] if records else MARKET_CALENDARS[request["market"]]
        params["instruments"] = records
        for key in ("start", "end", "at", "asOf"):
            text = request.get(key) or (
                (request.get("index") or {}).get("at") if key == "at" else None
            )
            if text:
                params[key] = str(instant(text, calendar, inclusive_end=key == "end"))
        if records:
            params["instrumentWindows"] = {
                record["instrumentId"]: {
                    key: instant(request[key], record["calendarId"], inclusive_end=key == "end")
                    for key in ("start", "end", "at", "asOf")
                    if request.get(key)
                }
                for record in records
            }
        params.update(limit=request.get("limit", 1000), cursor=request.get("cursor"))
        page = await self.market.research.read(params)
        lookup = {r["instrumentId"]: r for r in records}
        rows = []
        names = list(page["columns"])
        for values in zip(*page["columns"].values(), strict=True):
            row = dict(zip(names, values, strict=True))
            identity = row.get("instrumentId")
            if identity and identity not in lookup:
                lookup[identity] = self.identity.record(identity)
            record = lookup.get(identity)
            # A member no instrument maps to is named by the code it was listed under.
            row["symbol"] = self.spelling(record) if record else row["code"] if request["data"] == "members" else None
            if request["data"] == "members":
                row["name"] = record.get("name") if record else None
            zone = record["calendarId"] if record else calendar
            for key in ("exDate", "recordDate", "payDate", "periodEnd", "date", "from", "until"):
                if row.get(key) and str(row[key]).isdigit():
                    if request["data"] == "members":
                        row[key] = local_day(int(row[key]) - int(key == "until"), "24_7")
                    else:
                        row[key] = local_day(row[key], zone)
            for key in ("from", "until"):
                if key in row:
                    row["start" if key == "from" else "end"] = row.pop(key)
            rows.append(clocks(row, {"knownAt"}))
        fields = (
            list(rows[0])
            if rows
            else [("start" if k == "from" else "end" if k == "until" else k) for k in names]
            + ["symbol"]
        )
        result = {
            "data": request["data"],
            "columns": columns(fields),
            "rows": [],
            "nextCursor": page["nextCursor"],
            "total": page["total"],
            "unmapped": page["unmapped"],
            "meta": clocks(page["coverage"], {"start", "end", "retryAt"}),
            "note": page["note"],
        }
        # A row limit is an upper bound. Advance the cursor only past rows actually delivered.
        start = offset(request)
        for row in rows:
            result["rows"].append([row[k] for k in fields])
            if not fits({**result, "nextCursor": str(start + len(result["rows"]))}):
                result["rows"].pop()
                result["nextCursor"] = str(start + len(result["rows"]))
                break
        if not fits(result) or rows and not result["rows"]:
            raise MarketError(
                "too_large", "Research metadata is too large for a page; narrow the selection"
            )
        return result

    async def quotes(self, request):
        start = offset(request)
        symbols = request["symbols"][start : start + request.get("limit", 24)]
        records = [
            await self.record(s, environment=request.get("environment", "production"))
            for s in symbols
        ]
        page = await self.market.quotes.current(
            {
                "instruments": records,
                **{k: request[k] for k in ("basis", "environment") if k in request},
            }
        )
        for item, record in zip(page["items"], records, strict=True):
            item["symbol"] = self.spelling(record)
            quote = item["quote"]
            if quote is not None:
                quote = item["quote"] = dict(quote)
                quote["changePct"] = quote.pop("changePercent")
                quote["fieldAsOf"] = {key: iso(t) for key, t in quote["fieldAsOf"].items()}
        end = start + len(records)
        return {
            **clocks(page, {"receivedAt", "asOf", "retryAt"}),
            "nextCursor": str(end) if end < len(request["symbols"]) else None,
        }

    async def option_chain(self, request):
        record = await self.record(
            request["symbol"], environment=request.get("environment", "production")
        )
        result = await self.market.quotes.option_chain(
            {
                "venueId": record["venueId"],
                "underlying": record.get("base") or record["symbol"],
                "environment": request.get("environment", "production"),
                "expiry": str(instant(request["expiry"], record["calendarId"]))
                if request.get("expiry")
                else None,
                "limit": request.get("limit", 24),
                "cursor": request.get("cursor"),
            }
        )
        for row in result["rows"]:
            instrument = row["instrument"]
            row.update(
                symbol=self.spelling(instrument),
                instrumentId=instrument["instrumentId"],
                asOf={key: iso(stamp) for key, stamp in row["asOf"].items()},
            )
        return {
            **result,
            "asOf": iso(result["asOf"]),
            "expiries": [iso(x) for x in result["expiries"]],
        }

    async def professional(self, request):
        params = {"limit": 24, **request}
        params["after"] = params.pop("cursor", None)
        if "symbol" in params:
            params["instrument"] = await self.record(params.pop("symbol"))
        if "materialId" in params:
            if not params.get("version"):
                raise MarketError("invalid_input", "Reading a material requires version")
            if "kind" in params or "sourceId" in params:
                raise MarketError("invalid_input", "kind and sourceId filter the material list")
            page = await self.market.professional.read(params)
        else:
            if "version" in params or "quoteType" in params:
                raise MarketError("invalid_input", "version and quoteType require a materialId")
            page = await self.market.professional.list(params)
        page["nextCursor"] = page.pop("nextAfter")
        del page["hasMore"]
        return page

    async def fetch_request(self, request):
        data = request.get("data")
        if request.get("series"):
            if data is not None:
                raise MarketError("invalid_input", "Exact series already specify the data to fetch")
            targets = []
            data = []
            for entry in request["series"]:
                record = await self.record(entry["instrumentId"])
                series = entry["series"]
                kind = {
                    "kind": "bars" if series["capability"] == "ohlcv" else series["capability"],
                    "timeframe": series["timeframe"],
                    "priceBasis": series["priceBasis"],
                }
                targets.append({"instrument": record, "kinds": [kind]})
                if kind not in data:
                    data.append(kind)
        elif "index" in request:
            index = request["index"]
            record = await self.record(index["symbol"])
            targets = [{"index": record}]
            if index.get("at"):
                at = instant(index["at"], record["calendarId"])
                targets[0].update(membersFrom=str(at), membersUntil=str(at + 1))
        else:
            targets = [
                {
                    "instrument": await self.record(
                        s, environment=request.get("environment", "production")
                    )
                }
                for s in request["symbols"]
            ]
        if not data:
            raise MarketError("invalid_input", "Supply data to download, or exact series")
        params = {
            "targets": targets,
            "kinds": data,
            "environment": request.get("environment", "production"),
        }
        for k in ("start", "end"):
            if request.get(k):
                text = request[k]
                params[k] = (
                    text
                    if len(text) == 10
                    else str(instant(text, "24_7", inclusive_end=k == "end"))
                )
        return params

    async def fetch(self, request, call):
        params = await self.fetch_request(request)
        return await self.market.jobs.fetch(params, workspace_id=call.workspace_id, progress=call.progress)

    async def estimate_fetch(self, request, call):
        params = await self.fetch_request(request)
        jobs = self.market.jobs
        job = await jobs.prepare(params, jobs.key(params), workspace_id=call.workspace_id)
        return {**await in_worker(jobs.space, job), "folder": str(self.market.root), "items": len(job.record["items"]),
                "unmappedMembers": job.record["unmappedMembers"], "failures": job.record["expansionFailures"][:20]}

    async def downloads(self, request, workspace_id):
        op = request["operation"]
        jobs = self.market.jobs
        if op["op"] == "list":
            records = jobs.list(workspace_id)
            start = offset(op)
            summaries = []
            for record in records[start:start + op.get("limit", 24)]:
                row = jobs.summary(record)
                if not fits({"jobs": [*summaries, row], "nextCursor": str(len(records))}):
                    break
                summaries.append(row)
            if not summaries and start < len(records):
                raise MarketError("too_large", "Download summary exceeds the page size")
            end = start + len(summaries)
            return {"jobs": summaries, "nextCursor": str(end) if end < len(records) else None}
        return await jobs.control(op["op"], op["jobId"], workspace_id)

    async def export(self, request, call):
        selections = []
        if request.get("series"):
            if request.get("data") or request.get("timeframe"):
                raise MarketError(
                    "invalid_input", "Exact series already specify the data and timeframe"
                )
            for entry in request["series"]:
                record = await self.record(entry["instrumentId"])
                calendar = record["calendarId"]
                selections.append(
                    {
                        "instrument": record,
                        "series": entry["series"],
                        "start": instant(request.get("start"), calendar),
                        "end": instant(request.get("end"), calendar, inclusive_end=True),
                    }
                )
        elif request.get("data") in KINDS:
            instruments = [await self.record(symbol) for symbol in request.get("symbols", [])]
            if request.get("index") and request["data"] == "members":
                record = await self.record(request["index"]["symbol"])
                selections.append({"research": "members", "index": record})
            else:
                records = instruments or [r for r, _, _ in await self.selections(request)]
                selections.append({"research": request["data"], "instruments": records})
        else:
            for record, lo, hi in await self.selections(request):
                selections.append(
                    {
                        "instrument": record,
                        "series": make_series(record, request).to_dict(),
                        "start": lo,
                        "end": hi,
                    }
                )
        params = {
            key: request[key]
            for key in ("format", "fill", "allowEmpty", "destination", "environment", "adjustment")
            if key in request
        }
        params["selections"] = selections
        if request.get("data") in KINDS and selections:
            selected = selections[0]
            record = selected.get("index") or selected["instruments"][0]
            for key in ("start", "end"):
                if request.get(key):
                    params[key] = str(
                        instant(request[key], record["calendarId"], inclusive_end=key == "end")
                    )
        return await self.market.exports.export(
            params, workspace_id=call.workspace_id, progress=call.progress
        )
