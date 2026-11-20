"""Answers for an AI caller kept inside the platform's response budget.

The platform refuses an answer over 20,000 characters (`RESPONSE_LIMIT`, measured as its JSON); the Agent's step then fails.
These helpers give an AI the part it can act on, with the total and how to read the rest, and leave the screen's answers alone.
"""
from __future__ import annotations

import json

import orjson
from talyx_sdk import ContractError

# Every Quant answer to an AI is cut to this one budget (library and strategy reads too, `library_ai`): the whole answer under the
# Host's 20,000 (`host/ai.py` RESPONSE_LIMIT), less room for the envelope the Host puts round it (call id, status, a task's state).
BUDGET = 18_000


def size(value):
    return len(orjson.dumps(value))


def fit(items, build, budget=BUDGET):
    """The leading items whose answer `build(kept)` fits the budget (at least one), and how many were kept."""
    kept = []
    for item in items:
        if kept and size(build([*kept, item])) > budget:
            break
        kept.append(item)
    return kept


def page(items, offset, build, budget=BUDGET, limit=None):
    """`build(kept, next_offset, total)` over the items from `offset` that fit (at most `limit`); the next offset is None at the end."""
    total = len(items)
    rest = items[offset:offset + limit if limit else None]
    kept = fit(rest, lambda part: build(part, offset + len(part), total), budget)
    stop = offset + len(kept)
    return build(kept, stop if stop < total else None, total)


def text(value, limit=12_000):
    """A long text cut at a line end: {text, chars (the whole length), truncated}."""
    if len(value) <= limit:
        return {"text": value, "chars": len(value), "truncated": False}
    cut = value[:limit]
    if "\n" in cut:
        cut = cut[:cut.rfind("\n")]
    return {"text": cut, "chars": len(value), "truncated": True}


def object_detail(detail):
    """Retain parameter names and values while omitting editor help and source locations."""
    parameters = terse(compact(detail["parameters"], drop=("file", "line", "showIf", "step", "help")))
    return {**detail, "parameters": parameters}


def notebook_text(raw, output_chars=400):
    """A notebook file as text for reading: each cell's source in full, its outputs as a short line each (images and other rich output are named, not carried)."""
    try:
        from .notebook_file import parse
        cells = parse(raw)["cells"]
    except ContractError:
        return raw
    parts = []
    for index, cell in enumerate(cells):
        source = cell.get("source", "")
        source = "".join(source) if isinstance(source, list) else str(source)
        parts.append(f"# [{index}] {cell['type']} cell {cell.get('id', '')}\n{source}")
        for output in cell.get("outputs") or []:
            data = output.get("data") or {}
            body = output.get("text") or data.get("text/plain") or ""
            body = "".join(body) if isinstance(body, list) else str(body)
            rich = [kind for kind in data if kind != "text/plain"]
            label = f"# output: {body[:output_chars]}{'...' if len(body) > output_chars else ''}" if body else "# output:"
            parts.append(label + (f" (+ {', '.join(rich)})" if rich else ""))
    return "\n".join(parts)


def trim_listing(result, budget=BUDGET):
    """A paged listing {items, total, offset, nextOffset} cut to the leading items that fit; nextOffset then points at the first one left out."""
    offset = result.get("offset", 0)
    kept = fit(result["items"], lambda part: {**result, "items": part}, budget)
    if len(kept) == len(result["items"]):
        return result
    return {**result, "items": kept, "nextOffset": offset + len(kept)}


def lean(value, names=40):
    """The answer with every data setting's `symbols` (the whole Market instrument for each name, some 700 characters apiece) written as the count and the first
    names' display symbols; the screen keeps the instruments."""
    if isinstance(value, dict):
        out = {}
        for key, item in value.items():
            if key == "symbols" and isinstance(item, list) and item and all(isinstance(entry, dict) and isinstance(entry.get("instrument"), dict) for entry in item):
                out[key] = {"count": len(item), "names": [entry["instrument"].get("displaySymbol") for entry in item[:names]]}
            else:
                out[key] = lean(item, names)
        return out
    if isinstance(value, list):
        return [lean(item, names) for item in value]
    return value


def compact(value, drop=("file", "line", "showIf", "step")):
    """A declaration without what only the screen uses: nulls, and where a setting is defined in the source (file, line, showIf, step)."""
    if isinstance(value, dict):
        return {key: compact(item, drop) for key, item in value.items() if item is not None and key not in drop}
    if isinstance(value, list):
        return [compact(item, drop) for item in value]
    return value


def thin(points, count=120):
    """At most `count` of the points, evenly spread and ending on the last one; the caller says how many there were."""
    if len(points) <= count:
        return points
    step = (len(points) - 1) / (count - 1)
    return [points[round(index * step)] for index in range(count)]


def short_lists(value, limit=20):
    """Every list of plain values longer than `limit` (a library item can name each of its hundreds of instruments by id) as {count, first}."""
    if isinstance(value, dict):
        return {key: short_lists(item, limit) for key, item in value.items()}
    if isinstance(value, list):
        if len(value) > limit and all(not isinstance(item, (dict, list)) for item in value):
            return {"count": len(value), "first": value[:5]}
        return [short_lists(item, limit) for item in value]
    return value


def chunk_files(files, offset=0, limit=12_000):
    """Source files cut into one run of at most `limit` characters starting `offset` characters into all of them laid end to end:
    {files: [{file, text, from}], chars (all of them), nextOffset (None at the end)}."""
    total, out, at = sum(len(item["text"]) for item in files), [], 0
    for item in files:
        begin, end = max(offset - at, 0), min(offset + limit - at, len(item["text"]))
        if begin < len(item["text"]) and end > begin:
            out.append({**item, "text": item["text"][begin:end], "from": begin})
        at += len(item["text"])
    stop = offset + limit
    return {"files": out, "chars": total, "nextOffset": stop if stop < total else None}


def terse(value):
    """Choices written as their values (`options: ["median", "mean"]`), the labels being the screen's."""
    if isinstance(value, dict):
        return {key: ([item["value"] for item in item_ if "value" in item] if key == "options" and isinstance(item_, list) and all(isinstance(item, dict) for item in item_) else terse(item_))
                for key, item_ in value.items()}
    if isinstance(value, list):
        return [terse(item) for item in value]
    return value


def model_prep(found, algorithm=None):
    """A model's preparation for the Agent: the settings in their groups with the source's file and line left out; the algorithms as a list, with the parameters and
    objectives of one (`algorithm`, else the model's own) and only that algorithm's settings in the Algorithm group."""
    found = terse(compact(found, drop=("file", "line", "showIf", "step", "help")))
    chosen = algorithm or (found.get("values") or {}).get("algorithm")
    own = {param["setting"] for item in found["algorithms"] if item["id"] == chosen for param in item.get("params", [])}
    every = {param["setting"] for item in found["algorithms"] for param in item.get("params", [])}
    groups = [{**group, "settings": [item for item in group["settings"] if item["name"] not in every or item["name"] in own]} if group["name"] == "Algorithm" else group
              for group in found["groups"]]
    algorithms = [item if item["id"] == chosen else {key: value for key, value in item.items() if key not in {"params", "objectives"}} for item in found["algorithms"]]
    return {**found, "groups": groups, "algorithms": algorithms, "algorithm": chosen}


def model_values(found, offset=0, limit=None, contributions=3):
    """One day of a model's values for the Agent: names in rank order, a page at a time, each with its strongest contributions; no figure."""
    rows = sorted(found["rows"], key=lambda row: (row["rank"] is None, row["rank"] or 0))
    rows = [{**row, "contributions": (row.get("contributions") or [])[:contributions]} for row in rows]
    rest = {key: value for key, value in found.items() if key not in {"rows", "figure"}}
    return page(rows, offset, lambda part, more, total: {**rest, "rows": part, "nextOffset": more, "listed": total}, limit=limit)


def table_page(found, section, offset=0, limit=None):
    """A results section's long `table` for the Agent, a page at a time (the rest of the section stays whole)."""
    block = (found.get("results") or {}).get(section)
    if not isinstance(block, dict) or not isinstance(block.get("table"), list):
        return found
    table = block["table"]

    def build(part, more, total):
        return {**found, "results": {**found["results"], section: {**block, "table": part, "tableTotal": total, "tableNextOffset": more}}}
    return page(table, offset, build, limit=limit)


def without_windows(value):
    """A model run for the Agent without its per-window rows (list of {k, test, ...}) and the per-window rows inside its baselines: they grow with the
    number of windows and are read, a page at a time, as the `windows` table of read_run_table."""
    if isinstance(value, dict):
        return {key: without_windows(item) for key, item in value.items()
                if not (key == "windows" and isinstance(item, list) and item and all(isinstance(row, dict) and "k" in row for row in item))}
    if isinstance(value, list):
        return [without_windows(item) for item in value]
    return value


def bound(found, budget=BUDGET, floor=4):
    """The answer cut to the budget by design: the longest list in it is shortened (never below `floor` entries) until the whole fits, and `cut` names each
    list that was shortened with how many entries it keeps of how many. What is cut is a page the Agent can read through the action's own offset."""
    if size(found) <= budget:
        return found
    found = json.loads(json.dumps(found))
    cut = {}

    def lists(value, path):
        if isinstance(value, dict):
            for key, item in value.items():
                yield from lists(item, [*path, key])
        elif isinstance(value, list):
            yield path, value
            for index, item in enumerate(value):
                yield from lists(item, [*path, index])

    while size({**found, "cut": cut}) > budget:
        candidates = [(size(items), path, items) for path, items in lists(found, []) if len(items) > floor]
        if not candidates:
            break
        _, path, items = max(candidates, key=lambda item: item[0])
        label = ".".join(str(part) for part in path)
        total = cut.get(label, {}).get("total", len(items))
        del items[max(floor, len(items) // 2):]
        cut[label] = {"shown": len(items), "total": total}
    return {**found, "cut": cut}


def model_results(found, section=None, offset=0, limit=None):
    """A model run's Results for the Agent. Without a section only the section names; with one, the run and settings without what repeats or grows with
    the windows (the windows section pages through them), the section's long table a page at a time, and whatever still passes the budget cut by design."""
    if not section:
        return {**without_windows(found), "results": {"sections": list(found["results"])}}
    found = {**without_windows(found), "settings": {key: value for key, value in found["settings"].items() if key != "params"}}
    return bound(table_page(found, section, offset, limit))
