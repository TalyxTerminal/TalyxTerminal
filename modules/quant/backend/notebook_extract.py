"""Extracting ticked notebook cells into a new object: the code as written, the data settings the cells used, and what they lean on that was not taken.

Nothing here runs the cells. The data settings come from the literal arguments of `q.run`, `q.library.read` and `q.data` calls (ast), so a call whose
arguments are not literals contributes what it can. Tidying the code is the user's, in Developer.
"""
from __future__ import annotations

import ast
import builtins

KINDS = ("factor", "model", "allocation", "strategy")
BUILTINS = set(dir(builtins)) | {"q", "display", "get_ipython"}


def label(cell, index):
    count = cell.get("executionCount")
    return count if isinstance(count, int) else index + 1


def code_as_written(cells, order):
    """The chosen cells' source in notebook order, each under `# cell [n]` (n is the number the cell shows, or its place when it never ran)."""
    chosen = [(label(cell, index), cell["source"].rstrip("\n")) for index, cell in enumerate(order) if cell["id"] in {item["id"] for item in cells} and cell["type"] == "code"]
    return "\n\n".join(f"# cell [{number}]\n{source}" for number, source in chosen) + ("\n" if chosen else "")


def literal(node):
    try:
        return ast.literal_eval(node)
    except (ValueError, SyntaxError):
        return None


def q_call(node):
    """('run' | 'data' | 'library.read', call) for a call written q.run(...), q.data(...) or q.library.read(...), else None."""
    if not isinstance(node, ast.Call) or not isinstance(node.func, ast.Attribute):
        return None
    func = node.func
    if isinstance(func.value, ast.Name) and func.value.id == "q" and func.attr in {"run", "data", "backtest"}:
        return func.attr
    if isinstance(func.value, ast.Attribute) and func.value.attr == "library" and isinstance(func.value.value, ast.Name) and func.value.value.id == "q" and func.attr == "read":
        return "library.read"
    return None


def argument(call, position, name):
    if len(call.args) > position:
        return literal(call.args[position])
    for keyword in call.keywords:
        if keyword.arg == name:
            return literal(keyword.value)
    return None


def calls_in(source):
    try:
        tree = ast.parse(source)
    except SyntaxError:
        return []
    return [(q_call(node), node) for node in ast.walk(tree) if q_call(node) is not None]


def data_settings(cells, held_out_from=None):
    """What the cells read, first source winning: {runs, libraryInputs, universe, symbols, since, until, heldOutStudied, conflicts, found}."""
    runs, library, conflicts, data_calls = [], [], [], 0
    got = {}
    for cell in cells:
        for kind, call in sorted(calls_in(cell["source"]), key=lambda item: (item[1].lineno, item[1].col_offset)):
            if kind == "backtest":
                name = argument(call, 0, "strategy")
                if isinstance(name, str):
                    runs.append({"object": name})
            elif kind == "run":
                name, number = argument(call, 0, "run_id"), argument(call, 1, "number")
                if isinstance(name, str):
                    runs.append({"object": name, **({"number": number} if isinstance(number, int) else {})})
            elif kind == "library.read":
                name, version = argument(call, 0, "name"), argument(call, 99, "version")
                if isinstance(name, str):
                    library.append({"name": name, "version": version if isinstance(version, int) else None})
            else:
                data_calls += 1
                values = {"universe": argument(call, 99, "universe"), "list": argument(call, 99, "list"), "index": argument(call, 99, "index"), "symbols": argument(call, 0, "symbols"),
                          "timeframe": argument(call, 1, "timeframe"), "since": argument(call, 2, "start"), "until": argument(call, 3, "end")}
                for key, value in values.items():
                    if value is None:
                        continue
                    if key not in got:
                        got[key] = value
                    elif got[key] != value:
                        conflicts.append(f"{key}: {value} (kept {got[key]})")
    until = got.get("until")
    studied = held_out_from is not None and ((data_calls > 0 and (until is None or str(until) >= held_out_from)) or bool(library))
    return {"runs": runs, "libraryInputs": library, "universe": got.get("universe"), "list": got.get("list"), "index": got.get("index"), "symbols": got.get("symbols"), "timeframe": got.get("timeframe"), "since": got.get("since"), "until": until,
            "heldOutStudied": bool(studied), "conflicts": conflicts, "found": bool(runs or library or got)}


def names_bound(tree):
    bound = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Name) and isinstance(node.ctx, (ast.Store, ast.Del)):
            bound.add(node.id)
        elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            bound.add(node.name)
        elif isinstance(node, (ast.Import, ast.ImportFrom)):
            bound.update((alias.asname or alias.name).split(".")[0] for alias in node.names)
        elif isinstance(node, ast.arg):
            bound.add(node.arg)
        elif isinstance(node, ast.ExceptHandler) and node.name:
            bound.add(node.name)
    return bound


def names_used(tree):
    return {node.id for node in ast.walk(tree) if isinstance(node, ast.Name) and isinstance(node.ctx, ast.Load)}


def not_taken(chosen, order):
    """Lines for the sheet's "Not taken": the unticked earlier cells that define names the ticked ones use."""
    ids = {cell["id"] for cell in chosen}
    own, needed = set(), set()
    for cell in order:
        if cell["id"] in ids and cell["type"] == "code":
            try:
                tree = ast.parse(cell["source"])
            except SyntaxError:
                continue
            bound = names_bound(tree)
            needed |= names_used(tree) - own - bound - BUILTINS
            own |= bound
    # a name a taken cell defines before using it is not missing; this is checked cell by cell above
    lines = []
    definers = {}
    for index, cell in enumerate(order):
        if cell["id"] in ids or cell["type"] != "code":
            continue
        try:
            tree = ast.parse(cell["source"])
        except SyntaxError:
            continue
        for name in sorted(names_bound(tree) & needed):
            definers.setdefault(label(cell, index), []).append(name)
    if definers:
        numbers = sorted(definers)
        names = list(dict.fromkeys(name for number in numbers for name in definers[number]))
        join = lambda items, _tail: (f"{', '.join(map(str, items[:-1]))} and {items[-1]}" if len(items) > 1 else str(items[0]))
        cells_text = join([f"[{number}]" for number in numbers], "")
        lines.append(f"Cell{'s' if len(numbers) > 1 else ''} {cells_text} define{'' if len(numbers) > 1 else 's'} {join(names, '')} that these cells use")
    return lines


def preview(cells_ids, order, kind, held_out_from=None):
    if kind not in KINDS:
        raise ValueError(kind)
    chosen = [cell for cell in order if cell["id"] in set(cells_ids)]
    return {"code": code_as_written(chosen, order), "dataSettings": data_settings(chosen, held_out_from), "notTaken": not_taken(chosen, order)}
