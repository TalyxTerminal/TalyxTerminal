"""Read the settings code declares (`q.param`, `q.choice`, `q.pick`, `q.table`, `q.library_param`) from every Python file of a project, without running it.

Only literals are allowed, so the panel can show the settings before the code ever runs. A name declared twice is an error that gives both
places. The declarations are the code's own settings layer; the platform's run settings sit beside them.
"""
import ast

LITERAL_TYPES = (int, float, bool, str)
NAMES = {"param", "library_param", "choice", "pick", "table"}
COMMON = {"label", "help", "group", "show_if"}
KEYWORDS = {"param": {"min", "max", "step", "unit", *COMMON}, "library_param": {"label"}, "choice": COMMON, "pick": {"kinds", "multiple", "pinned", *COMMON},
            "table": {"columns", *COMMON}}
PICK_KINDS = {"factor", "model", "allocation", "strategy", "data", "prediction"}


def _declaration(call):
    """The declaring function's name when this call declares a setting, else None."""
    function = call.func
    if isinstance(function, ast.Attribute):
        return function.attr if function.attr in NAMES and isinstance(function.value, ast.Name) and function.value.id in {"q", "talyx_quant"} else None
    return function.id if isinstance(function, ast.Name) and function.id in NAMES else None


def _literal(node, line, what):
    try:
        return ast.literal_eval(node)
    except ValueError:
        raise ValueError(f"line {line}: {what} must be a literal") from None


def _options(raw, line):
    """Choices as `[value, …]` or `[(value, label), …]`; each value a number or text."""
    if not isinstance(raw, (list, tuple)) or not raw:
        raise ValueError(f"line {line}: options must be a list of values, or of (value, label) pairs")
    out = []
    for item in raw:
        value, label = (item[0], item[1]) if isinstance(item, (list, tuple)) and len(item) == 2 else (item, None)
        if type(value) not in (int, float, str) or label is not None and not isinstance(label, str):
            raise ValueError(f"line {line}: each option is a number or text, with an optional label")
        out.append({"value": value, "label": label if label is not None else str(value)})
    if len({repr(item["value"]) for item in out}) != len(out):
        raise ValueError(f"line {line}: options repeat a value")
    return out


def _columns(raw, line):
    if not isinstance(raw, (list, tuple)) or not raw or not all(isinstance(item, str) and item for item in raw):
        raise ValueError(f"line {line}: columns must be a list of column names")
    return list(raw)


def _declared(node, declared, file):
    line = node.lineno
    positional = 1 if declared == "pick" else 2
    if declared == "choice":
        positional = 3
    if any(isinstance(arg, ast.Starred) for arg in node.args) or not 1 <= len(node.args) <= positional or declared != "pick" and len(node.args) < positional:
        shape = {"param": "a name and a default", "library_param": "a name and a default", "choice": "a name, a default and the options",
                 "table": "a name and the default rows", "pick": "a name"}[declared]
        raise ValueError(f"line {line}: q.{declared} needs {shape} as its first arguments")
    name = _literal(node.args[0], line, "the setting's name")
    if not isinstance(name, str) or not name.isidentifier():
        raise ValueError(f"line {line}: the setting's name must be an identifier string")
    options = {}
    for keyword in node.keywords:
        if keyword.arg not in KEYWORDS[declared]:
            raise ValueError(f"line {line}: q.{declared} does not take {keyword.arg or '**'}")
        options[keyword.arg] = _literal(keyword.value, line, keyword.arg)
    for key in ("label", "help", "group"):
        if key in options and not isinstance(options[key], str):
            raise ValueError(f"line {line}: {key} must be text")
    show_if = options.get("show_if")
    if show_if is not None and (not isinstance(show_if, dict) or not show_if or not all(isinstance(key, str) for key in show_if)):
        raise ValueError(f"line {line}: show_if must be a dict of other settings' names and the values that show this one")
    item = {"name": name, "label": options.get("label") or name, "help": options.get("help"), "group": options.get("group"),
            "showIf": show_if, "file": file, "line": line, "min": None, "max": None}
    if "unit" in options:
        if not isinstance(options["unit"], str):
            raise ValueError(f"line {line}: unit must be text")
        item["unit"] = options["unit"]
    if declared in ("param", "library_param"):
        default = _literal(node.args[1], line, "the default")
        if type(default) not in LITERAL_TYPES or declared == "library_param" and type(default) is not str:
            raise ValueError(f"line {line}: the default must be " + ("the text of a library item's name" if declared == "library_param" else "a number, true/false or text"))
        for key in ("min", "max", "step"):
            if key in options and (type(options[key]) not in (int, float) or type(default) in (bool, str)):
                raise ValueError(f"line {line}: {key} applies to number settings")
        if "step" in options and options["step"] <= 0:
            raise ValueError(f"line {line}: step must be more than 0")
        kind = "library" if declared == "library_param" else {bool: "bool", int: "int", float: "float", str: "text"}[type(default)]
        item.update(type=kind, default=default, min=options.get("min"), max=options.get("max"), step=options.get("step"))
    elif declared == "choice":
        default = _literal(node.args[1], line, "the default")
        choices = _options(_literal(node.args[2], line, "the options"), line)
        if all(repr(default) != repr(entry["value"]) for entry in choices):
            raise ValueError(f"line {line}: the default {default!r} is not one of the options")
        item.update(type="choice", default=default, options=choices)
    elif declared == "pick":
        default = _literal(node.args[1], line, "the default") if len(node.args) > 1 else None
        kinds = options.get("kinds", ["factor"])
        kinds = [kinds] if isinstance(kinds, str) else list(kinds) if isinstance(kinds, (list, tuple)) else None
        if not kinds or any(entry not in PICK_KINDS for entry in kinds):
            raise ValueError(f"line {line}: kinds must be among {', '.join(sorted(PICK_KINDS))}")
        pinned = options.get("pinned", "version")
        if pinned not in ("version", "run"):
            raise ValueError(f"line {line}: pinned is \"version\" (a library version) or \"run\" (one run of an unpublished object)")
        if not isinstance(options.get("multiple", False), bool):
            raise ValueError(f"line {line}: multiple must be True or False")
        item.update(type="pick", default=default if default is not None else ([] if options.get("multiple") else None), kinds=kinds,
                    multiple=options.get("multiple", False), pinned=pinned)
    else:
        rows = _literal(node.args[1], line, "the default rows")
        columns = _columns(options.get("columns"), line) if "columns" in options else None
        if not isinstance(rows, (list, tuple)) or not all(isinstance(row, dict) for row in rows):
            raise ValueError(f"line {line}: a table's default is a list of rows, each a dict of column to value")
        columns = columns or (list(rows[0]) if rows else None)
        if not columns:
            raise ValueError(f"line {line}: a table needs columns")
        for row in rows:
            if set(row) != set(columns) or any(type(value) not in LITERAL_TYPES for value in row.values()):
                raise ValueError(f"line {line}: every row has exactly the columns {', '.join(columns)}, each a number, true/false or text")
        item.update(type="table", default=[dict(row) for row in rows], columns=columns)
    return item


def read_parameters(content, file="main.py"):
    """`{"parameters": [...], "errors": [{"file", "line", "message"}]}` for one source text."""
    return read_project({file: content})


def read_project(files):
    """The settings declared across a project's Python files (`{relative path: text}`), in file then line order; a name declared twice is
    an error naming both places, and the second is left out."""
    parameters, errors, first = [], [], {}
    for file in sorted(files):
        try:
            tree = ast.parse(files[file])
        except SyntaxError as error:
            errors.append({"file": file, "line": error.lineno or 1, "message": f"line {error.lineno}: {error.msg}"})
            continue
        found = []
        for node in ast.walk(tree):
            declared = _declaration(node) if isinstance(node, ast.Call) else None
            if declared is None:
                continue
            try:
                item = _declared(node, declared, file)
            except ValueError as error:
                errors.append({"file": file, "line": node.lineno, "message": str(error)})
                continue
            found.append(item)
        for item in sorted(found, key=lambda entry: entry["line"]):
            if item["name"] in first:
                where = first[item["name"]]
                errors.append({"file": file, "line": item["line"], "message": f"{item['name']!r} is declared twice: {where['file']} line {where['line']} and {file} line {item['line']}",
                               "also": {"file": where["file"], "line": where["line"]}})
                continue
            first[item["name"]] = item
            parameters.append(item)
    names = {item["name"] for item in parameters}
    for item in parameters:
        for other in item["showIf"] or {}:
            if other not in names:
                errors.append({"file": item["file"], "line": item["line"], "message": f"line {item['line']}: show_if names {other!r}, which is not declared"})
    return {"parameters": parameters, "errors": errors}


def _check_pick(item, value):
    entries = value if item["multiple"] else [value]
    if item["multiple"] and not isinstance(value, list):
        return "a list of picks"
    for entry in entries:
        if entry is None and not item["multiple"]:
            continue
        if not isinstance(entry, dict) or not isinstance(entry.get("name"), str):
            return "a pick: {name, version} of a library item or {name, runId} of an unpublished object's run"
        if item["pinned"] == "version" and "version" not in entry and "runId" not in entry:
            return "a pick fixed to a library version or to one run"
    return None


def check_values(definitions, values):
    """Validate panel values against the declared settings; raises ValueError naming the first mismatch."""
    known = {item["name"]: item for item in definitions}
    for name, value in values.items():
        item = known.get(name)
        if item is None:
            raise ValueError(f"The code has no setting {name!r}")
        kind = item["type"]
        need = None
        if kind == "bool":
            need = None if type(value) is bool else "true or false"
        elif kind in ("text", "library"):
            need = None if type(value) is str else "the name of a library item" if kind == "library" else "text"
        elif kind == "int":
            need = None if type(value) is int or type(value) is float and value == int(value) else "a whole number"
        elif kind == "float":
            need = None if type(value) in (int, float) else "a number"
        elif kind == "choice":
            need = None if any(repr(value) == repr(entry["value"]) or value == entry["value"] for entry in item["options"]) else \
                "one of " + ", ".join(str(entry["value"]) for entry in item["options"])
        elif kind == "pick":
            need = _check_pick(item, value)
        elif kind == "table":
            ok = isinstance(value, list) and all(isinstance(row, dict) and set(row) == set(item["columns"]) and all(type(cell) in LITERAL_TYPES for cell in row.values()) for row in value)
            need = None if ok else f"rows with the columns {', '.join(item['columns'])}"
        if need:
            raise ValueError(f"Setting {name!r} needs {need}")
        if kind in ("int", "float") and (item["min"] is not None and value < item["min"] or item["max"] is not None and value > item["max"]):
            raise ValueError(f"Setting {name!r} must be between {item['min']} and {item['max']}")
