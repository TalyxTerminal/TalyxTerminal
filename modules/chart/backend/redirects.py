"""Merged instruments: Market answers an old instrument id with a redirect; when a
workspace opens, every id its charts, watchlists and alerts keep is replaced by the new one in
one pass. A derived change: chart documents change in their current and saved revision alike
(nothing shows as unsaved)."""
from __future__ import annotations

import logging

from talyx_sdk import ContractError

from vocabulary.identity import series_id

LOG = logging.getLogger(__name__)


def moved_series(series: dict, instrument_id: str) -> dict:
    series = {**series, "instrumentId": instrument_id}
    return {**series, "seriesId": series_id({key: value for key, value in series.items() if key != "seriesId"})}


def move_document(document: dict, moved: dict[str, str], records: dict[str, dict]) -> bool:
    changed = False
    for holder in (document, *document.get("comparisons", [])):
        old = holder["instrument"]["instrumentId"]
        if old in moved:
            holder["instrument"] = records[moved[old]]
            holder["series"] = moved_series(holder["series"], moved[old])
            changed = True
    drawings = document.get("drawings", {})
    for key in list(drawings):
        old, _, rest = key.partition(":")
        if old in moved:
            target = drawings.setdefault(f"{moved[old]}:{rest}", [])
            existing = {item["id"] for item in target}
            target.extend(item for item in drawings.pop(key) if item["id"] not in existing)
            changed = True
    return changed


async def apply(workspace: str, app) -> None:
    store, watchlists, alerts = app.store, app.watchlists, app.alerts
    stored = {chart_id: store.chart(chart_id) for chart_id in store.charts(workspace)}
    documents = [document for item in stored.values() for document in (item["document"], item["savedDocument"])
                 if document]
    ids = {holder["instrument"]["instrumentId"] for document in documents
           for holder in (document, *document.get("comparisons", []))}
    ids.update(key.partition(":")[0] for document in documents for key in document["drawings"])
    ids |= {member["instrument"]["instrumentId"] for listed in watchlists.all(workspace)
            for section in listed["sections"] for member in section["members"]}
    ids |= {alert["target"]["instrument"]["instrumentId"] for alert in alerts.all(workspace)
            if alert["target"]["kind"] == "series"}
    if not ids:
        return
    try:
        matches = await app.market.matches(sorted(ids))
    except ContractError as error:  # the next opening tries again; nothing is lost meanwhile
        LOG.warning("Could not check merged instruments of workspace %s: %s", workspace, error.message)
        return
    moved = {item["previousId"]: item["instrument"]["instrumentId"] for item in matches if item["status"] == "redirect"}
    if not moved:
        return
    records = {item["instrument"]["instrumentId"]: item["instrument"] for item in matches if item["status"] == "redirect"}
    for chart_id, item in stored.items():
        changed = [move_document(document, moved, records) for document in (item["document"], item["savedDocument"])
                   if document]
        if any(changed):
            store.rewrite_chart(chart_id, item["document"], item["savedDocument"])
            app.charts.entries.pop(chart_id, None)
    for listed in watchlists.all(workspace):
        sections, seen, changed = [], set(), False
        for section in listed["sections"]:
            members = []
            for member in section["members"]:
                old = member["instrument"]["instrumentId"]
                instrument = records[moved[old]] if old in moved else member["instrument"]
                changed |= old in moved
                if instrument["instrumentId"] not in seen:  # the merged pair was listed twice
                    seen.add(instrument["instrumentId"])
                    members.append({"instrument": instrument})
            sections.append({**section, "members": members})
        if changed:
            watchlists.update(workspace, listed["id"], listed["revision"], None, sections)
    for alert in alerts.all(workspace):
        target = alert["target"]
        if target["kind"] == "series" and target["instrument"]["instrumentId"] in moved:
            new = moved[target["instrument"]["instrumentId"]]
            alerts.put(workspace, {**alert, "revision": alert["revision"] + 1, "target": {
                **target, "instrument": records[new], "series": moved_series(target["series"], new)}})
        fired = store.object("alert_fired_bar", alert["id"])
        if fired and any(key in moved for key in fired["bars"]):
            fired["bars"] = {moved.get(key, key): value for key, value in fired["bars"].items()}
            store.put_object("alert_fired_bar", fired, workspace)
