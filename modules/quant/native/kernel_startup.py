"""Run once in every notebook kernel, in its namespace: the variable list the side panel shows, the pip refusal, traceback folding.

Names here start with an underscore so the variable list never shows them.
"""
import json as _json
import sys as _sys
import types as _types

_PIP = ("%pip", "%conda", "!pip", "!uv pip", "!python -m pip", "!conda")
_HIDDEN = {"In", "Out", "exit", "quit", "get_ipython", "q", "open"}
_seen = {}
import time as _time

_STARTED = int(_time.time() * 1000)
_PIP_MESSAGE = "Install packages from the Packages page (⋯ → Packages)"


def _size(value):
    try:
        if hasattr(value, "memory_usage") and hasattr(value, "shape"):
            usage = value.memory_usage(deep=False)
            return int(usage.sum() if hasattr(usage, "sum") else usage)
        if hasattr(value, "nbytes"):
            return int(value.nbytes)
    except Exception:  # noqa: BLE001 - a size that cannot be read is shown as a dash
        pass
    return None


def _shape(value):
    try:
        shape = getattr(value, "shape", None)
        if shape is not None and not callable(shape):
            return " × ".join(f"{int(n):,}" for n in shape)
        if isinstance(value, (list, tuple, set, dict, str)):
            return f"{len(value):,}"
    except Exception:  # noqa: BLE001
        pass
    return None


def __talyx_vars__():
    namespace = get_ipython().user_ns  # noqa: F821
    found = []
    for name, value in list(namespace.items()):
        if name.startswith("_") or name in _HIDDEN or isinstance(value, (_types.ModuleType, _types.FunctionType, _types.BuiltinFunctionType, type)):
            continue
        if _seen.get(name, (None, None))[0] != id(value):
            _seen[name] = (id(value), get_ipython().execution_count - 1)  # noqa: F821
        found.append({"name": name, "type": type(value).__name__, "shape": _shape(value), "size": _size(value), "fromCell": _seen[name][1]})
    for name in [name for name in _seen if name not in namespace]:
        del _seen[name]
    q = _sys.modules.get("talyx_quant")
    return _json.dumps({"variables": found, "loaded": list(getattr(q, "_loaded", [])), "memory": _memory(), "startedAt": _STARTED})


def _memory():
    try:
        import psutil
        return int(psutil.Process().memory_info().rss)
    except Exception:  # noqa: BLE001 - no figure rather than a wrong one
        return None


def __talyx_table__(name):
    """One variable as the table explorer shows it, or None when it is not a table."""
    value = get_ipython().user_ns.get(name)  # noqa: F821
    try:
        import pandas as pd
        if isinstance(value, pd.Series):
            value = value.to_frame()
        if isinstance(value, pd.DataFrame):
            return _json.dumps(_table(value)["application/vnd.talyx.table+json"])
    except Exception:  # noqa: BLE001
        pass
    return _json.dumps(None)


def _refuse_pip(lines):
    for line in lines:
        if line.lstrip().startswith(_PIP):
            return [f"raise RuntimeError({_PIP_MESSAGE!r})\n"]
    return lines


def _table(frame, include=None, exclude=None):
    """A DataFrame as the table explorer shows it: the first 500 rows, each column's kind and missing count, and a 12-bar histogram of a number column.
    The usual HTML stays beside it for anything that cannot draw the explorer."""
    import numpy as np
    import pandas as pd
    original, shape = frame, [int(frame.shape[0]), int(frame.shape[1])]
    if not isinstance(frame.index, pd.RangeIndex) or any(name is not None for name in frame.index.names):
        # The index is shown first, as pandas prints it (the dates of q.data's closes); a plain 0, 1, 2 index is not.
        frame = frame.reset_index(allow_duplicates=True)
    head = frame.head(500)
    columns = []
    for position, name in enumerate(frame.columns):
        column = frame.iloc[:, position]  # by place: the index may carry a column's name
        kind = "bool" if pd.api.types.is_bool_dtype(column) else "int" if pd.api.types.is_integer_dtype(column) else "float" if pd.api.types.is_float_dtype(column) \
            else "date" if pd.api.types.is_datetime64_any_dtype(column) else "text"
        entry = {"name": str(name), "type": kind, "missing": int(column.isna().sum())}
        if kind in ("int", "float"):
            values = column.dropna().to_numpy(dtype=float)
            values = values[np.isfinite(values)]
            entry["hist"] = np.histogram(values, bins=12)[0].tolist() if len(values) else []
        columns.append(entry)
    rows = _json.loads(head.to_json(orient="values", date_format="iso", default_handler=str))
    return {"application/vnd.talyx.table+json": {"shape": shape, "columns": columns, "rows": rows, "size": _size(original)},
            "text/html": original._repr_html_()}


def _internal(path):
    path = path.replace("\\", "/")
    return "talyx_quant" in path or "talyx_call" in path or "/site-packages/" in path or "/native/" in path or "<frozen" in path


def _folded(shell, etype, evalue, tb, tb_offset=None):
    """An error's traceback with only the user's frames: the frames inside TALYX and libraries become one line saying how many and where."""
    import types
    frames = []
    while tb is not None:
        frames.append(tb)
        tb = tb.tb_next
    kept, hidden, names = [], 0, []
    for frame in frames:
        path = frame.tb_frame.f_code.co_filename.replace("\\", "/")
        if _internal(path):
            hidden += 1
            name = "talyx_quant" if "talyx_quant" in path else path.split("/site-packages/")[-1].split("/")[0] if "/site-packages/" in path else "TALYX"
            if name not in names:
                names.append(name)
        else:
            kept.append(frame)
    if not kept:
        _sys.last_type, _sys.last_value, _sys.last_traceback = etype, evalue, frames[0] if frames else None
        shell.showtraceback(exc_tuple=(etype, evalue, frames[0] if frames else None), tb_offset=tb_offset)
        return
    chain = None
    for frame in reversed(kept):
        chain = types.TracebackType(chain, frame.tb_frame, frame.tb_lasti, frame.tb_lineno)
    # %debug and breakpoints read the last traceback: hand it the user's frames, as IPython's own handler would have left it.
    _sys.last_type, _sys.last_value, _sys.last_traceback = etype, evalue, chain
    _sys.last_exc = evalue
    stb = shell.InteractiveTB.structured_traceback(etype, evalue, chain, tb_offset)
    if hidden:
        stb.insert(len(stb) - 1, f"… {hidden} frames inside {', '.join(names)}")
    shell._showtraceback(etype, evalue, stb)


def _install():
    shell = get_ipython()  # noqa: F821
    try:
        import talyx_quant as q
        shell.user_ns["q"] = q
        import tempfile
        # Qlib keeps its provider and experiment store under the run's output; a notebook has no run, so it gets a folder of its own.
        q._Run.invocation = {"output": tempfile.mkdtemp(prefix="talyx-notebook-")}
    except ImportError:
        pass
    shell.input_transformers_cleanup.append(_refuse_pip)
    shell.set_custom_exc((Exception,), _folded)
    try:
        import pandas as pd
        shell.display_formatter.mimebundle_formatter.for_type(pd.DataFrame, _table)
        shell.display_formatter.mimebundle_formatter.for_type(_sys.modules["talyx_quant"]._library_values_class(), _sys.modules["talyx_quant"]._library_block)
    except ImportError:
        pass


_install()
