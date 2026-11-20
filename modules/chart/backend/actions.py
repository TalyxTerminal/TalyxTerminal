"""Chart's public actions: what AI, other modules and running code call.

Each action calls the same backend function the Chart panel's private routes call; nothing here
keeps state of its own. Reads stay within 20,000 characters (``read_data`` within 18,000, as
Market's) and fail with ``too_large`` saying how many items fit instead of cutting the answer.
Times are ISO-8601 UTC.
"""
from __future__ import annotations

import asyncio
import base64
import json
import ntpath
import shutil
import uuid
from bisect import bisect_left
from datetime import UTC, datetime
from pathlib import Path

from pydantic import ValidationError
from talyx_sdk import ContractError

from vocabulary.python.types import FileRef

from . import exports, wire
from .document import COMMANDS, PRICE_PANE, Indicator, drawing_group
from .flows import PROJECTED
from .models import ResearchLayer
from .pine.indicators import ALERT_CONDITION

READ_LIMIT = 20_000
DATA_LIMIT = 18_000
READY_SECONDS = 60.0  # the series' whole window from Market
COMPUTE_SECONDS = 120.0  # an indicator's first output on it


# ── shared helpers ──


def workspace_of(call_ctx) -> str:
    workspace = call_ctx.workspace_id
    if not workspace:
        raise ContractError("invalid_input", "Chart's actions act in a workspace; this call names none")
    return workspace


def iso(ms: int | None) -> str | None:
    if ms is None:
        return None
    return datetime.fromtimestamp(ms / 1000, UTC).isoformat(timespec="milliseconds").replace("+00:00", "Z")


def ms_of(text: str, field: str) -> int:
    """An ISO-8601 date or time (UTC unless it says otherwise) as epoch milliseconds."""
    try:
        moment = datetime.fromisoformat(text.strip().replace("Z", "+00:00"))
    except ValueError as error:
        raise ContractError("invalid_input", f"{field}: not an ISO-8601 date or time: {text}") from error
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=UTC)
    return int(moment.timestamp() * 1000)


def encode_cursor(value: dict) -> str:
    return base64.urlsafe_b64encode(json.dumps(value).encode()).decode()


def decode_cursor(text: str | None) -> dict:
    if not text:
        return {}
    try:
        return json.loads(base64.urlsafe_b64decode(text.encode()))
    except ValueError as error:
        raise ContractError("invalid_input", "Unknown cursor") from error


def characters(value) -> int:
    return len(json.dumps(value, ensure_ascii=False))


def fitted(build, count: int, limit: int = READ_LIMIT) -> dict:
    """``build(count)`` when it fits in ``limit`` characters; otherwise ``too_large`` saying the
    largest count that does (asked again with that limit and the same cursor, it fits)."""
    answer = build(count)
    if characters(answer) <= limit:
        return answer
    low, high = 0, count
    while low < high:
        middle = (low + high + 1) // 2
        if characters(build(middle)) <= limit:
            low = middle
        else:
            high = middle - 1
    raise ContractError("too_large", f"The answer is over {limit} characters; ask again with limit={low} "
                                     f"(the same cursor continues from the same place)",
                        {"limitCharacters": limit, "fits": low}, execution="failed")


def paged(items: list, value: dict, default: int, maximum: int, make) -> dict:
    """``make(page, nextCursor)`` for the page of ``items`` the request's cursor and limit name."""
    count = min(int(value.get("limit", default)), maximum)
    offset = int(decode_cursor(value.get("cursor")).get("offset", 0))

    def build(size: int) -> dict:
        end = offset + size
        return make(items[offset:end], encode_cursor({"offset": end}) if end < len(items) else None)
    return fitted(build, count)


def symbol_of(instrument: dict) -> str:
    """Market's text for an instrument: venue:symbol for exchange contracts, the code for stocks."""
    return instrument["symbol"] if instrument["identityVersion"] == 2 else \
        f"{instrument['venueId']}:{instrument['symbol']}"


async def resolve(app, symbol: str) -> dict:
    record, = await app.market.records([symbol])
    return record


def new_id(prefix: str) -> str:
    return f"{prefix}-{uuid.uuid4().hex[:8]}"


def indicator_ref(item: dict, catalog) -> dict:
    """``{source, id}`` of an indicator an action is asked to run."""
    name = item["name"]
    for ref in catalog.refs():  # built-ins come first when the caller only gives a name
        if ref["id"] == name and (not item.get("source") or ref["source"] == item["source"]):
            return ref
    raise ContractError("not_found", f"No indicator {name} with that source; read_indicator lists them")


# ── read_chart ──


def drawing_view(drawing) -> dict:
    value = drawing.model_dump(mode="json", by_alias=True, exclude_defaults=True)
    value["points"] = [{"time": iso(point.time), "price": point.price} for point in drawing.points]
    value.setdefault("paneId", PRICE_PANE)
    return value


def chart_summary(app, entry) -> dict:
    document = entry.document
    feed = app.hub.feeds.get(document.series["seriesId"])
    status = feed.status if feed is not None else None
    return {"chartId": entry.id, "symbol": symbol_of(document.instrument),
            "instrumentId": document.instrument["instrumentId"], "timeframe": document.series["timeframe"],
            "priceBasis": document.series["priceBasis"], "adjustment": document.series["adjustment"],
            "chartType": document.chartType, "revision": entry.revision,
            "saved": entry.revision == entry.saved_revision,
            "status": None if status is None else {"state": status["state"], "problem": status["problem"]},
            "indicators": len(document.indicators), "drawings": len(document.drawings.get(drawing_group(document), [])),
            "comparisons": len(document.comparisons)}


async def read_chart(app, value: dict, call_ctx) -> dict:
    workspace = workspace_of(call_ctx)
    if value["view"] == "workspace":
        return {"alertDefaults": await app.read_alert_defaults(workspace), "health": app.health()}
    if value["view"] == "templates":
        if value.get("templateId"):
            return app.template(value["templateId"])
        items = [{key: item[key] for key in ("id", "name", "kind")} for item in app.templates()]
        return paged(items, value, 50, 200, lambda page, cursor: {"templates": page, "nextCursor": cursor})
    if value["view"] == "list":
        charts = [chart_summary(app, app.charts.entry(workspace, chart_id))
                  for chart_id in app.store.charts(workspace)]
        return fitted(lambda size: {"view": "list", "charts": charts}, len(charts))
    if value["view"] == "layer":
        if not value.get("chartId") or not value.get("layerId"):
            raise ContractError("invalid_input", "view=layer needs chartId and layerId")
        layer = await app.stored_layer(workspace, wire.LayerRead(chartId=value["chartId"], id=value["layerId"]))
        return await asyncio.to_thread(exports.research_snapshot, app.export_folder, layer, DATA_LIMIT)
    if not value.get("chartId"):
        raise ContractError("invalid_input", "view=one needs chartId")
    entry = app.charts.entry(workspace, value["chartId"])
    document = entry.document
    head = {"view": "one", "chart": chart_summary(app, entry),
              "display": document.display,
              "panes": [pane.model_dump(mode="json") for pane in document.panes],
              "indicators": [{"id": item.id, "name": item.definition.id, "source": item.definition.source,
                              "params": item.params, "paneId": item.paneId, "visible": item.visible,
                              "label": item.name, "styles": {k: v.model_dump() for k, v in item.styles.items()}}
                             for item in document.indicators],
              "layers": [{"id": item.id, "label": item.label, "instrumentId": item.instrumentId,
                          "adjustment": item.adjustment, "visible": item.visible} for item in document.layers],
              "comparisons": [{"id": item.id, "symbol": symbol_of(item.instrument), "scale": item.scale,
                               "visible": item.visible} for item in document.comparisons]}
    return paged([drawing_view(item) for item in document.drawings.get(drawing_group(document), [])], value, 50, 200,
                 lambda page, cursor: {**head, "drawings": page, "nextCursor": cursor})


# ── read_data ──


async def first_output(app, feed, ref: dict, params: dict) -> dict:
    """The indicator's output over the feed's window (shared with a chart or alert computing the same)."""
    done = asyncio.get_running_loop().create_future()

    def listener(message: dict) -> None:
        if not done.done() and message["op"] in ("replace", "error"):
            done.set_result(message)
    instance = await app.indicators.attach(feed, ref, params, listener)
    try:
        return await asyncio.wait_for(asyncio.shield(done), COMPUTE_SECONDS)
    except TimeoutError:
        return {"op": "error", "code": "timeout", "message": f"No output within {COMPUTE_SECONDS:.0f} seconds", "line": None}
    finally:
        app.indicators.detach(feed, instance, listener)


async def window_of(app, instrument: dict, series: dict, chart_type: str, projection: dict | None, latest_rows=None,
                    chart_window=None):
    """Raw recent reads can finish as soon as the requested page arrives."""
    try:
        return await asyncio.wait_for(app.hub.acquire(instrument, series, chart_type=chart_type, projection=projection,
                                                      whole=latest_rows is None, latest_rows=latest_rows,
                                                      window_of=chart_window), READY_SECONDS)
    except TimeoutError:
        raise ContractError("unavailable", f"Market sent no whole window for this series within {READY_SECONDS:.0f} "
                                           "seconds", execution="failed") from None


def indicator_failure(error: ContractError) -> dict:
    return {"op": "error", "code": error.code, "message": error.message, "line": None}


async def read_data(app, value: dict, call_ctx) -> dict:
    workspace = workspace_of(call_ctx)
    if value.get("chartId"):
        document = app.charts.entry(workspace, value["chartId"]).document
        instrument, series, chart_type = document.instrument, document.series, document.chartType
        projection = document.projection.model_dump() if chart_type in PROJECTED else None
        wanted = [(item.id, item.definition.model_dump(), item.params) for item in document.indicators]
    else:
        if not value.get("symbol") or not value.get("timeframe"):
            raise ContractError("invalid_input", "Give chartId, or symbol and timeframe")
        instrument = await resolve(app, value["symbol"])
        series = app.market.default_series(instrument, value["timeframe"])
        chart_type, projection = "candles", None
        wanted = []
        for index, item in enumerate(value.get("indicators", [])):
            ref = indicator_ref(item, app.catalog)
            wanted.append((item.get("id") or f"{item['name']}{index + 1}", ref, item.get("params", {})))
    outputs = {}
    for indicator_id, ref, params in wanted:
        try:
            visible = app.catalog.reads_view(app.catalog.definition(ref))
        except ContractError as error:
            outputs[indicator_id] = (ref, params, indicator_failure(error))
            continue
        if visible:
            if value.get("chartId"):
                continue  # a reading has no visible range
            raise ContractError("unsupported", f"{ref['id']} reads the chart's visible range, so it runs on charts only")
        outputs[indicator_id] = (ref, params, None)
    needs_history = any(message is None for _, _, message in outputs.values())
    limit = min(int(value.get("limit", 100)), 500)
    recent = limit if not needs_history and chart_type not in PROJECTED and not value.get("cursor") else None
    flows = app.flows.get((workspace, value.get("chartId")))
    flow = flows[-1] if flows and flows[-1].current else None
    replay = flow.replay if flow else None
    feed = replay.feed if replay else await window_of(app, instrument, series, chart_type, projection, recent,
                                                      flow.chart_id if flow and flow.extended else None)
    try:
        for indicator_id, (ref, params, message) in outputs.items():
            if message is None:
                try:
                    message = await first_output(app, feed, ref, params)
                except ContractError as error:  # a definition can disappear while waiting for the window
                    message = indicator_failure(error)
                outputs[indicator_id] = (ref, params, message)
        columns = {name: list(feed.columns[name]) for name in ("time", "open", "high", "low", "close", "volume")}
        status, forming, more = feed.status, feed.forming, feed.has_more_before
    finally:
        if replay is None:
            await app.hub.release(feed)
    times = columns["time"]
    described = []
    for indicator_id, (ref, params, message) in outputs.items():
        about = {"id": indicator_id, "name": ref["id"], "source": ref["source"], "params": params}
        if message["op"] == "error":
            about["error"] = {"code": message["code"], "message": message["message"], "line": message["line"]}
        else:
            index = {time: position for position, time in enumerate(message["time"])}
            titles = {meta["id"]: meta.get("title") for meta in message["meta"] if "id" in meta}
            about["outputs"] = {}
            for output, values in message["values"].items():
                if output.startswith(ALERT_CONDITION):
                    continue
                name = f"{indicator_id}.{output}"
                columns[name] = [values[index[time]] if time in index else None for time in times]
                about["outputs"][name] = titles.get(output) or output
        described.append(about)
    chosen = ["time", *[name for name in columns if name != "time"]]
    if value.get("columns"):
        unknown = [name for name in value["columns"] if name not in columns]
        if unknown:
            raise ContractError("invalid_input", f"Unknown columns {unknown}; this read has {chosen}")
        chosen = ["time", *[name for name in value["columns"] if name != "time"]]
    before = decode_cursor(value.get("cursor")).get("before")
    end = bisect_left(times, ms_of(before, "cursor")) if before else len(times)
    head = {"symbol": symbol_of(instrument), "instrumentId": instrument["instrumentId"],
            "timeframe": series["timeframe"], "chartId": value.get("chartId"), "chartType": chart_type,
            "status": None if status is None else {"state": status["state"], "problem": status["problem"]},
            "columns": chosen,
            "forming": None if forming is None else {**forming, "time": iso(forming["time"]),
                                                     "closeTime": iso(forming["closeTime"])},
            "window": {"bars": len(times), "first": iso(times[0]) if times else None, "marketHasOlder": more},
            "indicators": described}

    def build(size: int) -> dict:
        start = max(0, end - size)
        return {**head, "rows": [[iso(times[i]), *[columns[name][i] for name in chosen[1:]]] for i in range(start, end)],
                "nextCursor": encode_cursor({"before": iso(times[start])}) if start > 0 else None}
    return fitted(build, limit, DATA_LIMIT)


# ── write_chart ──


async def edit_commands(app, document, changes: list[dict]) -> tuple[list[dict], dict]:
    commands, added = [], {"indicators": [], "drawings": [], "comparisons": []}
    indicators = {item.id: item for item in document.indicators}
    drawings = {item.id for item in document.drawings.get(drawing_group(document), [])}
    comparisons = {item.id for item in document.comparisons}
    for change in changes:
        kind = change["kind"]
        if kind == "selection":
            command = {"type": "selection.set", **{key: change[key] for key in
                                                   ("timeframe", "priceBasis", "adjustment", "adjustmentAnchor")
                                                   if key in change}}
            if "symbol" in change:
                command["instrumentId"] = (await resolve(app, change["symbol"]))["instrumentId"]
            commands.append(command)
        elif kind == "chart_type":
            commands.append({"type": "chart_type.set", "chartType": change["chartType"],
                             **({"projection": change["projection"]} if "projection" in change else {})})
        elif kind == "indicator":
            patch = {key: change[key] for key in ("params", "visible") if key in change}
            if "label" in change:
                patch["name"] = change["label"]
            if "paneId" in change:
                patch["paneId"] = change["paneId"]
            if change.get("id") in indicators:
                if "name" in change and change["name"] != indicators[change["id"]].definition.id:
                    raise ContractError("invalid_input", f"Indicator {change['id']} is "
                                                         f"{indicators[change['id']].definition.id}; add a new one "
                                                         "for another indicator")
                commands.append({"type": "indicator.update", "id": change["id"], "patch": patch})
                continue
            if "name" not in change:
                raise ContractError("invalid_input", "A new indicator needs its name")
            ref = indicator_ref(change, app.catalog)
            indicator_id = change.get("id") or new_id("indicator")
            if "paneId" not in patch:
                overlay = (await app.catalog.describe(ref))["overlay"]
                patch["paneId"] = PRICE_PANE if overlay else f"pane-{indicator_id}"
            commands.append({"type": "indicator.add", "value": {"id": indicator_id, "definition": ref, **patch}})
            added["indicators"].append(indicator_id)
        elif kind == "layer":
            if "layer" not in change:
                raise ContractError("invalid_input", "kind=layer needs a research layer")
            commands.append({"type": "layer.set", "layer": change["layer"]})
        elif kind == "drawing":
            fields = {key: change[key] for key in ("text", "style", "settings", "locked", "visible", "paneId")
                      if key in change}
            if "label" in change:
                fields["name"] = change["label"]
            if "points" in change:
                fields["points"] = [{"time": ms_of(point["time"], "points.time"), "price": point["price"]}
                                    for point in change["points"]]
            if change.get("id") in drawings:
                commands.append({"type": "drawing.update", "id": change["id"], "patch": fields})
                continue
            if "tool" not in change:
                raise ContractError("invalid_input", "A new drawing needs its tool and points")
            drawing_id = change.get("id") or new_id("d")
            commands.append({"type": "drawing.add", "value": {"id": drawing_id, "tool": change["tool"], **fields}})
            added["drawings"].append(drawing_id)
        else:  # comparison
            fields = {key: change[key] for key in ("scale", "color") if key in change}
            if change.get("id") in comparisons:
                commands.append({"type": "comparison.update", "id": change["id"], **fields,
                                 **({"visible": change["visible"]} if "visible" in change else {})})
                continue
            if "symbol" not in change:
                raise ContractError("invalid_input", "A new comparison needs its symbol")
            comparison_id = change.get("id") or new_id("c")
            commands.append({"type": "comparison.add", "id": comparison_id,
                             "instrumentId": (await resolve(app, change["symbol"]))["instrumentId"], **fields})
            added["comparisons"].append(comparison_id)
    return commands, added


async def write_chart(app, value: dict, call_ctx) -> dict:
    workspace = workspace_of(call_ctx)
    operation = value["operation"]
    if operation["op"] == "create":
        if operation.get("copyOf") and any(key in operation for key in ("symbol", "timeframe", "importId", "indicators", "layers")):
            raise ContractError("invalid_input", "Give copyOf alone to copy a chart")
        record = await resolve(app, operation["symbol"]) if operation.get("symbol") else None
        indicators = []
        for item in operation.get("indicators", []):
            ref = indicator_ref(item, app.catalog)
            definition = await app.catalog.describe(ref)
            identity = new_id("indicator")
            try:
                indicators.append(Indicator(id=identity, definition=ref, params=item.get("params", {}),
                                            paneId=PRICE_PANE if definition["overlay"] else f"pane-{identity}"))
            except ValidationError as error:
                raise ContractError("invalid_input", str(error)) from error
        layers = [await app.research_file(FileRef.model_validate(item)) if "path" in item
                  else ResearchLayer.model_validate(item) for item in operation.get("layers", [])]
        if len({layer.id for layer in layers}) != len(layers):
            raise ContractError("invalid_input", "Research layer ids must be unique on a chart")
        instrument = layers[0].instrumentId if layers else record["instrumentId"] if record else None
        timeframe = layers[0].timeframe if layers else operation.get("timeframe")
        state = await app.charts.create(workspace, instrument, timeframe,
                                       operation.get("importId"), operation.get("copyOf"),
                                       indicators=indicators if "indicators" in operation or layers else None, layers=layers)
        return {"chartId": state["chartId"], "revision": state["revision"],
                **({"layerIds": [item["id"] for item in state["document"]["layers"]]} if layers else {}),
                "openTarget": {"module":"chart", "panelType":"main", "objectId":"chart:" + state["chartId"]}}
    entry = app.charts.entry(workspace, operation["chartId"])
    if operation["baseRevision"] != entry.revision:
        raise ContractError("conflict", "The chart changed since that revision; read it again",
                            {"revision": entry.revision})
    if "commands" in operation:
        if any(key in operation for key in ("changes", "indicators", "drawings", "comparisons", "layers")):
            raise ContractError("invalid_input", "Give commands as the entire edit")
        commands, added = operation["commands"], None
    elif operation["op"] == "edit":
        if not operation.get("changes"):
            raise ContractError("invalid_input", "Give changes or commands")
        commands, added = await edit_commands(app, entry.document, operation["changes"])
    else:
        added = None
        commands = [{"type": f"{kind}.delete", "id": item_id}
                    for kind, key in (("indicator", "indicators"), ("drawing", "drawings"),
                                      ("comparison", "comparisons"), ("layer", "layers"))
                    for item_id in operation.get(key, [])]
        if not commands:
            raise ContractError("invalid_input", "Name the indicators, drawings, comparisons or layers to remove")
    try:
        parsed = COMMANDS.validate_python(commands)
    except ValidationError as error:
        raise ContractError("invalid_input", "; ".join(
            f"{'.'.join(str(part) for part in item['loc'])}: {item['msg']}" for item in error.errors()[:5])) from None
    if operation["op"] == "edit" and any(command.type.endswith(".delete")
            or command.type in ("drawings.replace", "indicators.replace")
            or command.type == "drawing.bulk" and command.remove for command in parsed):
        raise ContractError("invalid_input", "Use op=remove for commands that delete or replace a group")
    state = await app.charts.commit(workspace, entry.id, operation["baseRevision"], parsed)
    return {"chartId": entry.id, "revision": state["revision"], "added": added}


async def write_template(app, op, call_ctx) -> dict:
    workspace = workspace_of(call_ctx)
    if op.op == "delete":
        return await app.delete_template(workspace, wire.TemplateId(templateId=op.templateId))
    document = app.charts.entry(workspace, op.chartId).document
    template = app.template(op.templateId) if op.templateId else None
    kind = op.kind if op.op == "save" else template["kind"]
    if kind == "drawing":
        group = document.drawings.get(drawing_group(document), [])
        drawing = next((item for item in group if item.id == op.drawingId), None)
        if drawing is None:
            raise ContractError("not_found", "Give the id of a drawing on this chart")
    if op.op == "save":
        if kind == "chart":
            content = {key: document.display[key] for key in ("appearance", "precision", "scales", "statusLine", "alertLine")
                       if key in document.display}
        elif kind == "drawing":
            content = {"tool": drawing.tool, "settings": drawing.settings, "style": drawing.style.model_dump(mode="json")}
        else:
            content = {"indicators": [item.model_dump(mode="json") for item in document.indicators],
                       "panes": [pane.model_dump() for pane in document.panes if pane.id != PRICE_PANE]}
        return await app.save_template(workspace, wire.TemplateSave(templateId=op.templateId, name=op.name, kind=kind,
                                                                    content=content))
    content = template["content"]
    if kind == "chart":
        command = {"type": "display.update", "patch": content}
    elif kind == "drawing":
        if drawing.tool != content["tool"]:
            raise ContractError("invalid_input", f"This template is for {content['tool']}")
        command = {"type": "drawing.update", "id": drawing.id,
                   "patch": {"style": content["style"], "settings": content["settings"]}}
    else:
        price = next(pane.model_dump() for pane in document.panes if pane.id == PRICE_PANE)
        command = {"type": "indicators.replace", "indicators": content["indicators"], "panes": [price, *content["panes"]]}
    state = await app.charts.commit(workspace, op.chartId, op.baseRevision, COMMANDS.validate_python([command]))
    return {"chartId": op.chartId, "revision": state["revision"]}


# ── read_indicator, write_indicator ──


async def read_indicator(app, value: dict, call_ctx) -> dict:
    if value["view"] == "list":
        entries = await app.catalog.entries()
        query = value.get("query", "").lower()
        listed = [{"name": item["id"], "source": item["source"], "title": item["title"],
                   "shortTitle": item["shortTitle"], "category": item["category"], "overlay": item["overlay"],
                   **({"error": item["error"]} if item.get("error") else {})}
                  for item in entries
                  if (not query or query in item["title"].lower() or query in item["shortTitle"].lower()
                      or query in item["id"].lower())
                  and (not value.get("category") or item["category"].lower() == value["category"].lower())
                  and (not value.get("source") or item["source"] == value["source"])]
        return paged(listed, value, 100, 300,
                     lambda page, cursor: {"view": "list", "indicators": page, "nextCursor": cursor})
    if "name" not in value:
        raise ContractError("invalid_input", "view=one needs the indicator's name")
    ref = indicator_ref(value, app.catalog)
    entry = await app.catalog.describe(ref)
    answer = {"view": "one", "name": entry["id"], "source": entry["source"], "title": entry["title"],
              "shortTitle": entry["shortTitle"], "description": entry["description"], "category": entry["category"],
              "overlay": entry["overlay"], "inputs": entry["inputs"], "alertConditions": entry["alertConditions"],
              "digest": entry["digest"], "error": entry["error"], "line": entry["line"]}
    if ref["source"] == "user":  # its code, to read before changing it with write_indicator
        answer["uri"] = app.catalog.definition(ref).path.as_uri()
    return fitted(lambda size: answer, 1)


async def save_document(ctx, project_id: str, path: Path, content: str, *, create: bool) -> None:
    """Write a file the way Chart's editor does: through Developer's shared document, so an editor showing it follows.
    Unsaved edits in an editor are not overwritten."""
    document = await ctx.call("developer.documents.open", {"source": {"kind": "file", "uri": path.as_uri(),
                                                                      "projectId": project_id}})
    if create and path.exists():
        raise ContractError("conflict", f"{path.name} already exists; update it")
    if not create and not path.is_file():
        raise ContractError("not_found", f"No file {path.name} in the indicators folder; create it")
    if document["dirty"] or document["conflict"]:
        raise ContractError("conflict", f"{path.name} has unsaved edits in an editor; save or discard them first")
    edited = await ctx.call("developer.documents.edit", {"documentId": document["documentId"],
                                                         "expectedVersion": document["version"], "content": content})
    saved = await ctx.call("developer.documents.save", {"documentId": edited["documentId"],
                                                        "expectedVersion": edited["version"]})
    if saved["status"] not in ("saved", "clean"):
        raise ContractError("conflict", f"{path.name} was not saved: {saved['message'] or saved['status']}")


async def write_indicator(app, value: dict, call_ctx) -> dict:
    operation = value["operation"]
    name, folder = operation["name"], app.catalog.user_dir
    if any(part in {".", ".."} or ntpath.isreserved(part) for part in name.split("/")):
        raise ContractError("invalid_input", "Use a file or folder name valid on Windows")
    if operation["op"] == "delete":
        indicator_ref({"source": "user", "name": name}, app.catalog)
        app.catalog.definition({"source": "user", "id": name})
        path = folder / name
        await asyncio.to_thread(shutil.rmtree if path.is_dir() else Path.unlink, path)
        app.workspaces.publish_all({"type": "catalog"})
        return {"name": name, "file": None, "check": None}
    path = (folder / name).resolve()
    if not path.is_relative_to(folder.resolve()):
        raise ContractError("invalid_input", "Choose an indicator file inside the indicators folder")
    if "/" in name and path.name != "main.py" and not (path.parent / "main.py").is_file():
        raise ContractError("invalid_input", f"Create {path.parent.name}/main.py before its helper files")
    project = await app.ctx.call("developer.projects.open", {"directory": str(folder.resolve()), "name": "Indicators"})
    await save_document(app.ctx, project["projectId"], path, operation["content"], create=operation["op"] == "create")
    entry = await app.catalog.describe({"source": "user", "id": name.partition("/")[0]})  # a file, or its folder
    app.workspaces.publish_all({"type": "catalog"})
    return {"name": entry["id"], "file": name,
            "check": {key: entry[key] for key in ("title", "shortTitle", "overlay", "inputs", "alertConditions",
                                                  "error", "line")}}


def handlers(app) -> dict:
    """Action name → handler(value as JSON, call), for the declarations in ``entry``."""
    from . import object_actions

    def bind(function):
        async def handler(value, call_ctx):
            return await function(app, value, call_ctx)
        return handler
    return {name: bind(function) for name, function in (
        ("read_chart", read_chart), ("read_data", read_data), ("write_chart", write_chart),
        ("read_indicator", read_indicator), ("write_indicator", write_indicator), *object_actions.ACTIONS)}
