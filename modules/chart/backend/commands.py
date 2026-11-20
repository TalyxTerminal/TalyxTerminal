"""Applying the command set to a chart document. Pure: Market lookups happen before (``resolve``
in charts.py turns ``selection.set`` and ``comparison.add`` into concrete fields)."""
from __future__ import annotations

import copy
import uuid

from vocabulary.identity import series_id

from .document import PRICE_PANE, ChartDocument, group_key


class CommandError(ValueError):
    pass


def _find(items: list[dict], item_id: str, kind: str) -> int:
    for index, item in enumerate(items):
        if item["id"] == item_id:
            return index
    raise CommandError(f"No {kind} {item_id} on this chart")


def _move(items: list, index: int, direction: int) -> None:
    target = index + direction
    if 0 <= target < len(items):
        items[index], items[target] = items[target], items[index]


def _patch(target: dict, patch: dict, *, nullable: tuple[str, ...] = ()) -> None:
    for key, value in patch.items():
        if value is None and key in nullable:
            target.pop(key, None)
        elif value is not None:
            target[key] = value


def _prune_panes(doc: dict) -> None:
    """Indicator panes live as long as an indicator sits on them; their drawings go with them."""
    used = {item["paneId"] for item in doc["indicators"]} | {PRICE_PANE}
    doc["panes"] = [pane for pane in doc["panes"] if pane["id"] in used]
    for group, drawings in doc["drawings"].items():
        doc["drawings"][group] = [item for item in drawings if item["paneId"] in used]


def _fresh(prefix: str) -> str:
    return f"{prefix}-{uuid.uuid4().hex[:10]}"


def apply(document: ChartDocument, commands: list[dict]) -> ChartDocument:
    doc = document.model_dump(mode="json", by_alias=True)
    for command in commands:
        _apply(doc, command)
    return ChartDocument.model_validate(doc)


def _apply(doc: dict, value: dict) -> None:
    kind = value["type"]
    group = group_key(doc["series"], doc["chartType"])
    drawings = doc["drawings"].setdefault(group, [])
    if kind == "drawing.add":
        if any(item["id"] == value["value"]["id"] for item in drawings):
            raise CommandError(f"Drawing {value['value']['id']} exists")
        drawings.append(value["value"])
    elif kind == "drawing.update":
        index = _find(drawings, value["id"], "drawing")
        _patch(drawings[index], value["patch"], nullable=("text", "name"))
    elif kind == "drawing.delete":
        drawings.pop(_find(drawings, value["id"], "drawing"))
    elif kind == "drawing.bulk":
        ids = set(value["ids"]) if value.get("ids") is not None else {item["id"] for item in drawings}
        if value.get("remove"):
            doc["drawings"][group] = [item for item in drawings if item["id"] not in ids]
        for item in drawings:
            if item["id"] in ids:
                for key in ("locked", "visible"):
                    if value.get(key) is not None:
                        item[key] = value[key]
    elif kind == "drawing.duplicate":
        for item_id in value["ids"]:
            copied = copy.deepcopy(drawings[_find(drawings, item_id, "drawing")])
            copied["id"] = _fresh("drawing")
            drawings.append(copied)
    elif kind == "drawing.reorder":
        _move(drawings, _find(drawings, value["id"], "drawing"), value["direction"])
    elif kind == "drawings.replace":
        doc["drawings"][group] = value["drawings"]
    elif kind == "indicator.add":
        item = value["value"]
        if any(existing["id"] == item["id"] for existing in doc["indicators"]):
            raise CommandError(f"Indicator {item['id']} exists")
        if item["paneId"] not in {pane["id"] for pane in doc["panes"]}:
            doc["panes"].append({"id": item["paneId"], "height": 1.0, "collapsed": False})
        doc["indicators"].append(item)
    elif kind == "indicator.update":
        index = _find(doc["indicators"], value["id"], "indicator")
        patch = value["patch"]
        if patch.get("paneId") and patch["paneId"] not in {pane["id"] for pane in doc["panes"]}:
            doc["panes"].append({"id": patch["paneId"], "height": 1.0, "collapsed": False})
        _patch(doc["indicators"][index], patch, nullable=("name",))
        _prune_panes(doc)
    elif kind == "indicator.delete":
        doc["indicators"].pop(_find(doc["indicators"], value["id"], "indicator"))
        _prune_panes(doc)
    elif kind == "indicator.duplicate":
        copied = copy.deepcopy(doc["indicators"][_find(doc["indicators"], value["id"], "indicator")])
        copied["id"] = _fresh("indicator")
        if copied["paneId"] != PRICE_PANE:
            copied["paneId"] = _fresh("pane")
            doc["panes"].append({"id": copied["paneId"], "height": 1.0, "collapsed": False})
        doc["indicators"].append(copied)
    elif kind == "indicator.reorder":
        _move(doc["indicators"], _find(doc["indicators"], value["id"], "indicator"), value["direction"])
    elif kind == "indicators.replace":
        doc["indicators"] = value["indicators"]
        doc["panes"] = value["panes"]
        _prune_panes(doc)
    elif kind == "pane.update":
        pane = doc["panes"][_find(doc["panes"], value["id"], "pane")]
        _patch(pane, {key: value.get(key) for key in ("height", "collapsed")})
    elif kind == "pane.move":
        _move(doc["panes"], _find(doc["panes"], value["id"], "pane"), value["direction"])
        if doc["panes"][0]["id"] != PRICE_PANE:
            raise CommandError("The price pane stays on top")
    elif kind == "comparison.add":
        if any(item["id"] == value["id"] for item in doc["comparisons"]):
            raise CommandError(f"Comparison {value['id']} exists")
        doc["comparisons"].append({key: value[key] for key in ("id", "instrument", "series", "scale")
                                   if key in value} | ({"color": value["color"]} if value.get("color") else {}))
    elif kind == "comparison.update":
        item = doc["comparisons"][_find(doc["comparisons"], value["id"], "comparison")]
        _patch(item, {key: value.get(key) for key in ("scale", "color", "visible")})
    elif kind == "comparison.delete":
        doc["comparisons"].pop(_find(doc["comparisons"], value["id"], "comparison"))
    elif kind == "layer.set":
        layer = value["layer"]
        items = doc["layers"]
        index = next((i for i, existing in enumerate(items) if existing["id"] == layer["id"]), len(items))
        items[index:index + 1] = [layer]
    elif kind == "layer.visible":
        doc["layers"][_find(doc["layers"], value["id"], "layer")]["visible"] = value["visible"]
    elif kind == "layer.delete":
        doc["layers"].pop(_find(doc["layers"], value["id"], "layer"))
    elif kind == "display.update":
        _patch(doc["display"], value["patch"], nullable=tuple(value["patch"]))
    elif kind == "display.replace":
        doc["display"] = value["display"]
    elif kind == "selection.set":
        doc["instrument"], doc["series"] = value["instrument"], value["series"]
        for item in doc["comparisons"]:  # same series on the new timeframe: its id follows
            series = {**item["series"], "timeframe": value["series"]["timeframe"]}
            item["series"] = {**series, "seriesId": series_id(series)}
    elif kind == "chart_type.set":
        doc["chartType"] = value["chartType"]
        if value.get("projection") is not None:
            doc["projection"] = value["projection"]
    else:
        raise CommandError(f"Unknown command {kind}")
    for name, items in list(doc["drawings"].items()):
        if not items:
            del doc["drawings"][name]
