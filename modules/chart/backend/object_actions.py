"""Chart's public actions on workspace objects: alerts, watchlists and screens.

The AI-facing shapes name instruments by Market's symbol text and indicators by name; they are
translated to the wire models of ``wire.py`` and handed to the same ``Alerts``, ``Watchlists``
and ``Screens`` methods the panel's private routes call.
"""
from __future__ import annotations

import asyncio

from pydantic import ValidationError
from talyx_sdk import ContractError

from .actions import (
    decode_cursor,
    encode_cursor,
    fitted,
    indicator_ref,
    iso,
    ms_of,
    paged,
    resolve,
    symbol_of,
    workspace_of,
)
from .wire import AlertSpec, ScreenSpec

QUOTES_AT_ONCE = 100  # Market's quotes.current takes at most 100 instruments


def validated(model, value: dict) -> dict:
    try:
        return model.model_validate(value).model_dump()
    except ValidationError as error:
        raise ContractError("invalid_input", "; ".join(
            f"{'.'.join(str(part) for part in item['loc'])}: {item['msg']}" for item in error.errors()[:5])) from None


# ── conditions (alerts and screens share them) ──


def operand_in(app, operand: dict | None, chart_id: str | None) -> dict | None:
    if operand is None:
        return None
    kind = operand["kind"]
    if kind == "price":
        return {"kind": "price", "field": operand.get("field", "close")}
    if kind == "value":
        return {"kind": "value", "value": operand["value"]}
    if kind == "drawing":
        chart = operand.get("chartId") or chart_id
        if chart is None:
            raise ContractError("invalid_input", "A drawing operand names its chartId")
        return {"kind": "drawing", "chartId": chart, "drawingId": operand["drawingId"]}
    ref = indicator_ref(operand, app.catalog)
    if kind == "alertcondition":
        return {"kind": "alertcondition", "definition": ref, "params": operand.get("params", {}),
                "condition": operand["condition"]}
    return {"kind": "indicator", "definition": ref, "params": operand.get("params", {}), "output": operand["output"]}


def operand_out(operand: dict | None) -> dict | None:
    if operand is None or operand["kind"] not in ("indicator", "alertcondition"):
        return operand
    out = {key: value for key, value in operand.items() if key != "definition"}
    return {**out, "name": operand["definition"]["id"], "source": operand["definition"]["source"]}


def conditions_in(app, conditions: list[dict], chart_id: str | None) -> list[dict]:
    return [{**{key: value for key, value in condition.items() if key not in ("left", "right", "right2")},
             **{side: operand_in(app, condition.get(side), chart_id)
                for side in ("left", "right", "right2") if condition.get(side) is not None}}
            for condition in conditions]


def conditions_out(conditions: list[dict]) -> list[dict]:
    return [{key: operand_out(value) if key in ("left", "right", "right2") else value
             for key, value in condition.items() if value is not None} for condition in conditions]


# ── alerts ──


async def target_in(app, workspace: str, target: dict) -> dict:
    if target.get("chartId"):
        document = app.charts.entry(workspace, target["chartId"]).document
        return {"kind": "series", "instrument": document.instrument, "series": document.series,
                "chartId": target["chartId"]}
    if target.get("watchlistId"):
        return {"kind": "watchlist", "watchlistId": target["watchlistId"], "timeframe": target["timeframe"]}
    instrument = await resolve(app, target["symbol"])
    return {"kind": "series", "instrument": instrument,
            "series": app.market.default_series(instrument, target["timeframe"])}


def target_out(target: dict) -> dict:
    if target["kind"] == "watchlist":
        return {"watchlistId": target["watchlistId"], "timeframe": target["timeframe"]}
    return {"symbol": symbol_of(target["instrument"]), "timeframe": target["series"]["timeframe"],
            **({"chartId": target["chartId"]} if target.get("chartId") else {})}


def alert_out(alert: dict) -> dict:
    return {"id": alert["id"], "name": alert.get("name"), "state": alert["state"], "reason": alert.get("reason"),
            "source": alert.get("source"), "outdated": alert.get("outdated", False), "revision": alert["revision"],
            "target": target_out(alert["target"]), "conditions": conditions_out(alert["conditions"]),
            "frequency": alert["frequency"], "expiresAt": iso(alert.get("expiresAt")), "message": alert["message"],
            "channels": alert["channels"], "createdAt": iso(alert["createdAt"]),
            "lastFiredAt": iso(alert.get("lastFiredAt")), "fireCount": alert.get("fireCount", 0)}


async def spec_in(app, workspace: str, value: dict) -> dict:
    target = await target_in(app, workspace, value["target"])
    spec = {"target": target, "conditions": conditions_in(app, value["conditions"], target.get("chartId")),
            **{key: value[key] for key in ("frequency", "message", "name", "channels") if key in value}}
    if value.get("expiresAt"):
        spec["expiresAt"] = ms_of(value["expiresAt"], "expiresAt")
    return validated(AlertSpec, spec)


async def read_alert(app, value: dict, call_ctx) -> dict:
    workspace = workspace_of(call_ctx)
    view = value["view"]
    if view == "list":
        alerts = sorted(app.alerts.all(workspace), key=lambda item: item["createdAt"])
        if value.get("state"):
            alerts = [item for item in alerts if item["state"] == value["state"]]
        return paged([alert_out(item) for item in alerts], value, 20, 100,
                     lambda page, cursor: {"view": "list", "alerts": page, "nextCursor": cursor})
    if view == "one":
        return {"view": "one", "alert": alert_out(app.alerts.get(workspace, value["alertId"]))}
    if value.get("alertId"):
        app.alerts.get(workspace, value["alertId"])
    before = decode_cursor(value.get("cursor")).get("before")
    log = app.alerts.log(workspace, value.get("alertId"), before, min(int(value.get("limit", 50)), 100))
    events = log["events"]

    def build(size: int) -> dict:
        page = events[:size]
        more = size < len(events) or log["next"] is not None
        return {"view": "log", "events": [{**event, "at": iso(event["at"]), "barTime": iso(event["barTime"])}
                                          for event in page],
                "nextCursor": encode_cursor({"before": page[-1]["at"]}) if more and page else None}
    return fitted(build, len(events))


async def write_alert(app, value: dict, call_ctx) -> dict:
    workspace = workspace_of(call_ctx)
    operation = value["operation"]
    op = operation["op"]
    if op == "create":
        return {"alert": alert_out(app.alerts.create(workspace, await spec_in(app, workspace, operation)))}
    if op == "update":
        current = app.alerts.get(workspace, operation["alertId"])
        spec = {key: current[key] for key in AlertSpec.model_fields if key in current}
        for key in AlertSpec.model_fields:
            if key not in operation:
                continue
            value = operation[key]
            if value is None:
                spec.pop(key, None)
            elif key == "target":
                previous = target_out(current["target"])
                if value.get("chartId"):
                    target = {"chartId": value["chartId"]}
                elif value.get("watchlistId"):
                    target = {"watchlistId": value["watchlistId"], "timeframe": value.get("timeframe", previous.get("timeframe"))}
                elif value.get("symbol") or "timeframe" in value:
                    if current["target"]["kind"] == "series":
                        previous["symbol"] = current["target"]["instrument"]["instrumentId"]
                    target = {**previous, **value}
                    target.pop("chartId", None)
                    if value.get("symbol"):
                        target.pop("watchlistId", None)
                else:
                    raise ContractError("invalid_input", "The alert target needs a chart, watchlist or symbol")
                if not target.get("chartId") and not (target.get("timeframe") and (target.get("symbol") or target.get("watchlistId"))):
                    raise ContractError("invalid_input", "The alert target needs a symbol or watchlist and timeframe")
                spec[key] = await target_in(app, workspace, target)
            elif key == "conditions":
                spec[key] = conditions_in(app, value, (spec.get("target") or {}).get("chartId"))
            elif key == "channels":
                spec[key] = {k: v for k, v in {**spec[key], **value}.items() if v is not None}
            else:
                spec[key] = ms_of(value, key) if key == "expiresAt" else value
        spec = validated(AlertSpec, spec)
        return {"alert": alert_out(app.alerts.update(workspace, operation["alertId"], operation["revision"], spec))}
    if op in ("pause", "resume"):
        state = "paused" if op == "pause" else "active"
        return {"alert": alert_out(app.alerts.set_state(workspace, operation["alertId"], operation["revision"], state))}
    app.alerts.delete(workspace, operation["alertId"])
    return {"alert": None}


# ── watchlists ──


def watchlist_out(watchlist: dict) -> dict:
    return {"id": watchlist["id"], "name": watchlist["name"], "revision": watchlist["revision"],
            "sections": [{"name": section["name"], "members": len(section["members"])}
                         for section in watchlist["sections"]]}


async def quotes_of(app, instruments: list[dict]) -> dict[str, dict | None]:
    client = app.market.client
    found: dict[str, dict | None] = {}
    for start in range(0, len(instruments), QUOTES_AT_ONCE):
        answer = await client.read_quotes({"symbols": [r["instrumentId"] for r in instruments[start:start + QUOTES_AT_ONCE]],
                                           "limit": QUOTES_AT_ONCE})
        for item in answer["items"]:
            quote = item["quote"]
            found[item["instrumentId"]] = None if quote is None else {
                "price": quote["price"], "change": quote["change"], "changePercent": quote["changePct"],
                "changeBasis": quote["changeBasis"], "delayed": quote["delayed"],
                "asOf": quote["asOf"]}
    return found


async def read_watchlist(app, value: dict, call_ctx) -> dict:
    workspace = workspace_of(call_ctx)
    if value["view"] == "list":
        return paged([watchlist_out(item) for item in app.watchlists.all(workspace)], value, 50, 200,
                     lambda page, cursor: {"view": "list", "watchlists": page, "nextCursor": cursor})
    watchlist = app.watchlists.get(workspace, value["watchlistId"])
    members = [(section["name"], member["instrument"]) for section in watchlist["sections"]
               for member in section["members"]]
    offset = int(decode_cursor(value.get("cursor")).get("offset", 0))
    shown = members[offset:offset + min(int(value.get("limit", 50)), 200)]
    quotes = await quotes_of(app, [instrument for _, instrument in shown]) if shown else {}
    rows = [{"symbol": symbol_of(instrument), "instrumentId": instrument["instrumentId"], "section": section,
             "name": instrument.get("name"), "quote": quotes.get(instrument["instrumentId"])}
            for section, instrument in members]
    return paged(rows, value, 50, 200, lambda page, cursor: {"view": "one", "watchlist": watchlist_out(watchlist),
                                                             "members": page, "nextCursor": cursor})


async def sections_in(app, sections: list[dict]) -> list[dict]:
    records = iter(await app.market.records([symbol for section in sections for symbol in section.get("symbols", [])]))
    return [{"id": f"s{index + 1}", "name": section.get("name", ""),
             "members": [{"instrument": next(records)} for _ in section.get("symbols", [])]}
            for index, section in enumerate(sections)]



def matches(instrument: dict, text: str) -> bool:
    return text in (symbol_of(instrument), instrument["instrumentId"])


async def write_watchlist(app, value: dict, call_ctx) -> dict:
    workspace = workspace_of(call_ctx)
    operation = value["operation"]
    op = operation["op"]
    if op == "create":
        created = app.watchlists.create(workspace, operation["name"], await sections_in(app, operation.get("sections", [])))
        return {"watchlist": watchlist_out(created)}
    if op == "delete":
        app.watchlists.delete(workspace, operation["watchlistId"])
        return {"watchlist": None}
    current = app.watchlists.get(workspace, operation["watchlistId"])
    sections = None
    if "sections" in operation:
        sections = await sections_in(app, operation["sections"])
    if operation.get("add") or operation.get("remove"):
        sections = [{**section, "members": list(section["members"])} for section in (sections or current["sections"])]
        for text in operation.get("remove", []):
            if not any(matches(member["instrument"], text) for section in sections for member in section["members"]):
                raise ContractError("not_found", f"{text} is not in this watchlist")
            for section in sections:
                section["members"] = [member for member in section["members"] if not matches(member["instrument"], text)]
        additions = operation.get("add", [])
        records = await app.market.records([item["symbol"] for item in additions])
        for item, record in zip(additions, records, strict=True):
            name = item.get("section", sections[-1]["name"] if sections else "")
            section = next((section for section in sections if section["name"] == name), None)
            if section is None:
                taken = {section["id"] for section in sections}
                section_id = next(f"s{index}" for index in range(1, len(sections) + 2) if f"s{index}" not in taken)
                section = {"id": section_id, "name": name, "members": []}
                sections.append(section)
            section["members"].append({"instrument": record})
    updated = app.watchlists.update(workspace, current["id"], operation["revision"], operation.get("name"), sections)
    return {"watchlist": watchlist_out(updated)}


# ── screens ──


def screen_out(screen: dict) -> dict:
    run = screen.get("lastRun")
    scope = screen["scope"]
    return {"id": screen["id"], "name": screen["name"], "revision": screen["revision"],
            "scope": {"watchlistId": scope["watchlistId"]} if scope["kind"] == "watchlist" else
            {"symbols": [symbol_of(item) for item in scope["instruments"]]},
            "timeframe": screen["timeframe"], "conditions": conditions_out(screen["conditions"]),
            "lastRun": None if run is None else {
                "state": run["state"], "startedAt": iso(run["startedAt"]), "finishedAt": iso(run["finishedAt"]),
                "done": run["done"], "total": run["total"], "matched": sum(1 for row in run["rows"] if row["matched"]),
                "error": run.get("error")}}


async def read_screen(app, value: dict, call_ctx) -> dict:
    workspace = workspace_of(call_ctx)
    if value["view"] == "list":
        return paged([screen_out(item) for item in app.screens.all(workspace)], value, 20, 100,
                     lambda page, cursor: {"view": "list", "screens": page, "nextCursor": cursor})
    screen = app.screens.get(workspace, value["screenId"])
    rows = (screen.get("lastRun") or {}).get("rows", [])
    if value.get("matchedOnly"):
        rows = [row for row in rows if row["matched"]]
    head = {"view": "one", "screen": screen_out(screen)}
    return paged([{"symbol": symbol_of(row["instrument"]), "matched": row["matched"], "values": row["values"],
                   "barTime": iso(row["barTime"]), "error": row.get("error")} for row in rows], value, 50, 200,
                 lambda page, cursor: {**head, "rows": page, "nextCursor": cursor})


async def write_screen(app, value: dict, call_ctx) -> dict:
    """A platform task for every op; cancelling a run's task cancels the run (``Screens.cancel``)."""
    workspace = workspace_of(call_ctx)
    operation = value["operation"]
    op = operation["op"]
    if op == "save":
        scope = operation["scope"]
        spec = {"name": operation["name"], "timeframe": operation["timeframe"],
                "scope": {"kind": "watchlist", "watchlistId": scope["watchlistId"]} if scope.get("watchlistId") else
                {"kind": "instruments", "instruments": await app.market.records(scope["symbols"])},
                "conditions": conditions_in(app, operation["conditions"], None)}
        saved = app.screens.save(workspace, operation.get("screenId"), operation.get("revision"),
                                 validated(ScreenSpec, spec))
        return {"screen": screen_out(saved)}
    if op == "delete":
        app.screens.delete(workspace, operation["screenId"])
        return {"screen": None}
    screen_id = operation["screenId"]
    app.screens.run(workspace, screen_id)
    running = app.screens.runs[screen_id]
    try:
        await asyncio.shield(running)
    except asyncio.CancelledError:
        if not running.done():
            app.screens.cancel(workspace, screen_id)
        raise
    screen = app.screens.get(workspace, screen_id)
    matched = [symbol_of(row["instrument"]) for row in screen["lastRun"]["rows"] if row["matched"]]
    return {"screen": screen_out(screen), "matched": matched[:50], "moreMatched": max(0, len(matched) - 50)}


ACTIONS = (("read_alert", read_alert), ("write_alert", write_alert), ("read_watchlist", read_watchlist),
           ("write_watchlist", write_watchlist), ("read_screen", read_screen), ("write_screen", write_screen))
