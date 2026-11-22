"""The research process for factor Work runs: it keeps the run's data in memory, so a changed formula or setting is answered in seconds.

Two ways to start it. Open (`resident`): the Host sends one JSON line per job on stdin and reads one JSON line per answer on stdout;
the author's prints go to stderr so they can never be taken for an answer. Once (`once`): one job from the invocation file, in a process
of its own, with the answer written to the run's output folder. Both run the same job.

A job writes into its output folder: `progress.jsonl` (one line per step, read by the Host every second), `work.json` (numbers, checks,
figures), `ls.json` (the long–short path, to draw beside other runs) and `cross/` (every day's values, read one day at a time).
"""
from __future__ import annotations

import json
import os
import runpy
import sys
import time
import traceback
from pathlib import Path

native_directory = Path(__file__).resolve().parent
sys.path[:] = [path for path in sys.path if Path(path or os.getcwd()).resolve() != native_directory]
sys.path.insert(0, str(native_directory.parent))

import numpy as np  # noqa: E402
import pandas as pd  # noqa: E402
from native import factor_work as W  # noqa: E402
from native import formula as F  # noqa: E402
from native import i18n

PANELS = {}  # data key -> Panel; the newest two are kept
LIMIT_PANELS = 2


class JobError(Exception):
    """A job that cannot go on: `failure` is what the run shows (stage, title, message, and for code the file and line)."""

    def __init__(self, failure):
        super().__init__(failure["message"])
        self.failure = failure


def progress(output, stage, **values):
    with open(Path(output) / "progress.jsonl", "a", encoding="utf-8") as stream:
        stream.write(json.dumps({"at": int(time.time() * 1000), "stage": stage, **values}) + "\n")


# ---------------------------------------------------------------------------------------------------------------- data
def load_panel(spec):
    """The panel of a data key: from memory when this process loaded it before, else from the files the Host froze."""
    info = spec["info"]
    key = (spec["key"], info.get("fingerprint"), tuple(sorted(info.get("revisions", {}).items())))
    if key in PANELS:
        PANELS[key] = PANELS.pop(key)
        return PANELS[key]
    bars = pd.read_parquet(spec["barsPath"])
    extras = json.loads(Path(spec["extrasPath"]).read_text(encoding="utf-8")) if spec.get("extrasPath") else {}
    columns = [item["column"] for item in info["instruments"]]
    labels = np.sort(bars["label"].unique())
    industry = W.industry_table(labels, columns, extras["industry"]) if extras.get("industry") else None
    shares = None
    clocks = bars.pivot(index="label", columns="column", values="knownAt").reindex(index=labels, columns=columns).to_numpy(dtype=float) \
        if extras.get("shares") or spec.get("libraryPath") else None
    if extras.get("shares"):
        shares = W.shares_table(labels, columns, extras["shares"], clocks=clocks)
    library = {}
    if spec.get("libraryPath"):
        values = pd.read_parquet(spec["libraryPath"])
        for (name, version), rows in values.groupby(["name", "version"]):
            library[name] = {"version": version, "values": W.library_table(labels, columns, rows, clocks), "usedBy": []}
        for name, used in (extras.get("usedBy") or {}).items():
            if name in library:
                library[name]["usedBy"] = used
    panel = W.Panel.from_bars(bars, info, industry=industry, shares=shares, library=library, notes=extras.get("notes"))
    PANELS[key] = panel
    while len(PANELS) > LIMIT_PANELS:
        PANELS.pop(next(iter(PANELS)))
    return panel


# ---------------------------------------------------------------------------------------------------------------- values
def formula_values(text, panel):
    """The formula's raw values and its inputs as arrays on the panel."""
    fields = {name: {} for name in panel.tables()}
    fields["$industry"] = {} if panel.industry is not None else {"unavailable": "Market gives no industry for these instruments"}
    try:
        tree = F.parse(text, fields)
    except F.FormulaError as error:
        raise JobError({"stage": "code", "title": i18n.t("The formula has a mistake"), "message": error.message, "formula": error.to_dict()}) from error
    context = F.Context(panel.tables(), panel.industry)
    try:
        values, parts = F.evaluate(text, context, F.inputs(tree, text) or ("$close",))
        values = values.to_numpy(dtype=float)
        inputs = {name: table.to_numpy(dtype=float) for name, table in parts.items()}
    except F.FormulaError as error:
        raise JobError({"stage": "code", "title": i18n.t("The formula cannot be computed on this data"), "message": error.message, "formula": error.to_dict()}) from error
    return values, inputs


def _purge(roots):
    """Forget the author's modules from an earlier run, so an edited helper file is read again."""
    for name, module in list(sys.modules.items()):
        path = getattr(module, "__file__", None)
        if path and any(Path(path).resolve().is_relative_to(root) for root in roots):
            del sys.modules[name]


def code_values(code, panel, roots):
    """Run the author's factor code on the panel; returns the raw values and the inputs it handed to q.report.factor."""
    import talyx_quant as q
    from native.object_worker import failure
    source = Path(code["source"]).resolve()
    _purge(roots.values())
    for root in roots.values():
        if str(root) not in sys.path:
            sys.path.insert(0, str(root))
    q._bind_parameters(code.get("params") or {})
    q._bind_work(panel, {"mode": "work", "research": {"info": panel.info}})
    previous = os.getcwd()
    os.chdir(source)
    sys.argv = [code["entry"]]
    try:
        runpy.run_path(str(source / code["entry"]), run_name="__main__")
    except Exception as error:  # the author's code: where it broke is the result
        found = failure(error, roots)
        inner = next((item for item in reversed(found["frames"]) if item["root"] in {"source", "library"}), None)
        raise JobError({"stage": "code", "title": f"stopped at line {inner['line']}" if inner else "stopped in the code", "message": f"{found['type']}: {found['message']}",
                        "file": inner["file"] if inner else None, "line": inner["line"] if inner else None,
                        "traceback": found["traceback"], "frames": found["frames"]}) from error
    finally:
        os.chdir(previous)
    work = q._Run.work
    if work is None:
        raise JobError({"stage": "code", "title": i18n.t("No factor values"), "message": i18n.t("The code finished without q.report.factor(factor): hand the factor's table to it.")})
    return _wide(work["factor"], panel, i18n.t("The factor")), {name: _wide(table, panel, name) for name, table in work["inputs"].items()}


def _wide(value, panel, label):
    from native import reports
    if isinstance(value, pd.Series):
        value = reports.index_pairs(value, "factor").unstack("asset")
    if not isinstance(value, pd.DataFrame):
        raise JobError({"stage": "code", "title": i18n.t("Not a table"), "message": i18n.t("{label} must be a table of dates by instruments, such as one built from q.data().close.", label=label)})
    index = pd.DatetimeIndex(value.index)
    index = index.tz_localize("UTC") if index.tz is None else index.tz_convert("UTC")
    value = value.copy()
    value.index = index
    aligned = value.reindex(index=panel.dates, columns=panel.columns)
    return aligned.apply(pd.to_numeric, errors="coerce").to_numpy(dtype=float)


def values_of(job, panel, roots):
    if job.get("formula") is not None:
        return formula_values(job["formula"], panel)
    return code_values(job["code"], panel, roots)


def future_check(job, panel, roots, values):
    """For code: the values at three dates recomputed with the data cut just after each; any difference means the code read later prices."""
    count = len(panel.labels)
    positions = sorted({int(count * share) for share in (0.5, 0.7, 0.9) if 30 <= int(count * share) < count - 1})
    for position in positions:
        cut = panel.truncate(int(panel.labels[position]) + 1)
        recomputed, _ = code_values(job["code"], cut, roots)
        rows = slice(max(0, position - 4), position + 1)
        a, b = values[rows], recomputed[rows]
        same = (np.isnan(a) & np.isnan(b)) | (np.abs(a - b) <= 1e-9 * np.maximum(1, np.abs(a)))
        if not same.all():
            row, col = np.argwhere(~same)[0]
            day = panel.dates[rows.start + row]
            return {"value": "changed", "passed": False, "line": None,
                    "title": i18n.t("{symbol} on {month} {day}, {year} changes when later prices are removed: the code reads later days", symbol=panel.columns[col], month=day.strftime(i18n.t("%b")), day=day.day, year=day.year)}
    return {"value": "none", "passed": True, "line": None, "title": i18n.t("{dates} dates recomputed without later prices: the values stay the same", dates=len(positions))}


# ---------------------------------------------------------------------------------------------------------------- jobs
def write_json(path, value):
    Path(path).write_text(json.dumps(value, allow_nan=False, default=_plain), encoding="utf-8")


def _plain(value):
    if isinstance(value, (np.integer,)):
        return int(value)
    if isinstance(value, (np.floating,)):
        return None if not np.isfinite(value) else float(value)
    if isinstance(value, np.ndarray):
        return value.tolist()
    raise TypeError(type(value).__name__)


def _clean(value):
    """Numbers made JSON-safe: NaN and infinities become null."""
    if isinstance(value, float):
        return value if np.isfinite(value) else None
    if isinstance(value, dict):
        return {key: _clean(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_clean(item) for item in value]
    if isinstance(value, np.floating):
        return float(value) if np.isfinite(value) else None
    if isinstance(value, np.integer):
        return int(value)
    return value


def enough_names(panel, settings):
    """A factor is scored by ranking names against each other each day: one name, or fewer names than equal groups, cannot be scored."""
    names = len(panel.columns)
    shown = ", ".join(panel.columns[:3]) + ("…" if names > 3 else "")
    if names < 2:
        raise JobError({"stage": "compute", "title": i18n.t("needs more than one name"),
                        "message": i18n.t("A factor is scored by ranking names against each other each day, and these data have one name ({shown}). Choose at least two: several symbols, a research list with more members, or an index.", shown=shown)})
    count = settings["grouping"]["count"]
    if settings["grouping"]["method"] == "equal" and count > names:
        raise JobError({"stage": "compute", "title": i18n.t("has fewer names than groups"),
                        "message": i18n.t("Groups is {count} but these data have {names} names ({shown}). Lower Groups to {names} or fewer, or add names.", count=count, names=names, shown=shown)})


def industry_settings(panel, settings):
    """a setting that needs an industry stops the run before anything is computed when Market gives none for these names, naming the
    setting and what to set instead (not the engine's internal error, and not done some other way)."""
    if panel.industry is not None:
        return
    neutral = settings["neutralize"] != "none"
    grouped = settings["grouping"].get("withinIndustry")
    if neutral or grouped:
        mode = i18n.t("Industry + size") if settings["neutralize"] == "industry+size" else i18n.t("Industry")
        if neutral and grouped:
            message = i18n.t("Neutralize is {mode}; Group within each industry is on, which needs an industry for each name, and Market gives none for these names. Set Neutralize to None and turn it off.", mode=mode)
        elif neutral:
            message = i18n.t("Neutralize is {mode}, which needs an industry for each name, and Market gives none for these names. Set Neutralize to None.", mode=mode)
        else:
            message = i18n.t("Group within each industry is on, which needs an industry for each name, and Market gives none for these names. Turn it off.")
        raise JobError({"stage": "compute", "title": i18n.t("needs an industry"), "message": message})


def run_job(job):
    """One Work run, sweep or held-out validation; returns its summary and writes its files."""
    started = time.perf_counter()
    output = Path(job["output"])
    output.mkdir(parents=True, exist_ok=True)
    progress(output, "compute", state="running")
    roots = {}
    if job.get("code"):
        roots["source"] = Path(job["code"]["source"]).resolve()
        if job["code"].get("common"):
            roots["library"] = Path(job["code"]["common"]).resolve()
    full = load_panel(job["data"])
    settings = W.settings_of(job["settings"])
    kind = job.get("kind", "run")
    if kind == "values":
        result = library_values(job, full, settings, roots, output)
        result["seconds"] = round(time.perf_counter() - started, 2)
        return result, (None, None, None, None)
    enough_names(full, settings)
    industry_settings(full, settings)
    held = job.get("heldOutFrom")
    panel = full if kind == "heldout" or held is None else full.truncate(held)
    formula = job.get("formula") is not None
    raw, inputs = values_of(job, panel, roots)
    if kind != "heldout":
        # The raw values, for the Results built afterwards in their own process (removed once they are built).
        np.save(output / "values.npy", np.asarray(raw, dtype=float))
    progress(output, "compute", state="done", seconds=round(time.perf_counter() - started, 2))
    progress(output, "numbers", state="running")
    if kind == "sweep":
        result = sweep(job, panel, raw, settings, roots, output)
    elif kind == "heldout":
        result = held_out(job, panel, raw, settings, held, output)
    else:
        evaluation = W.Evaluation(panel, raw, settings, formula=formula, future=None)
        evaluation.store(output / "cross", inputs)
        figures = evaluation.figures()
        write_json(output / "ls.json", evaluation.ls_path())
        evaluation.store_daily(output / "daily.npz")
        result = {**_clean(evaluation.summary()), "figures": _clean(figures), "inputs": list(inputs), "notes": panel.notes,
                  "dataTo": int(panel.labels[-1]), "dataFrom": int(panel.labels[0])}
        result["_evaluation"] = evaluation
    seconds = round(time.perf_counter() - started, 2)
    evaluation = result.pop("_evaluation", None)
    result["seconds"] = seconds
    write_json(output / "work.json", result)
    progress(output, "numbers", state="done", seconds=seconds)
    return result, (evaluation, raw, panel, roots)


def processed(job, panel, settings, roots):
    """The factor's values as the run tested them: computed, then left out, clipped, scaled and neutralized day by day."""
    raw, _ = values_of(job, panel, roots)
    raw = np.asarray(raw, dtype=float)
    raw[~np.isfinite(raw)] = np.nan
    reason = W.eligibility(panel, settings["leaveOut"])
    after, _ = W.preprocess(raw, panel, settings, reason)
    return after


def library_values(job, panel, settings, roots, output):
    """What the library keeps of a factor: its values from `from` on, in `values.parquet` (datetime, instrument column, value); and for a
    publication, the same values recomputed with the data cut just after each date in `cutoffs`: any value up to the cut that changes means the
    factor reads later data, and the first one is named."""
    after = processed(job, panel, settings, roots)
    labels = np.asarray(panel.labels, dtype="int64")
    start = job.get("from")
    rows = np.ones(len(labels), dtype=bool) if start is None else labels >= int(start)
    position, column = np.nonzero(~np.isnan(after) & rows[:, None])
    table = pd.DataFrame({"datetime": labels[position], "instrument": np.asarray(panel.columns, dtype=object)[column], "value": after[position, column]})
    table.to_parquet(output / "values.parquet", index=False)
    cutoffs = [int(value) for value in job.get("cutoffs") or []]
    found = None
    for number, cutoff in enumerate(cutoffs):
        progress(output, "future", state="running", done=number, total=len(cutoffs))
        index = int(np.searchsorted(labels, cutoff, side="right"))
        if index >= len(labels):
            continue
        cut = panel.truncate(int(labels[index]))
        again = processed(job, cut, settings, roots)
        a, b = after[:index], again[:index]
        same = (np.isnan(a) & np.isnan(b)) | (np.abs(a - b) <= 1e-9 * np.maximum(1, np.abs(np.nan_to_num(a))))
        if not same.all():
            changed = np.argwhere(~same)
            row, col = changed[0]
            found = {"cutoff": cutoff, "datetime": int(labels[row]), "instrument": panel.columns[col],
                     "withAll": None if np.isnan(a[row, col]) else float(a[row, col]), "whenCut": None if np.isnan(b[row, col]) else float(b[row, col]),
                     "changed": int(len(changed))}
            break
    progress(output, "future", state="done", done=len(cutoffs), total=len(cutoffs))
    return {"kind": "values", "rows": int(len(table)), "dataTo": int(labels[-1]), "dataFrom": int(labels[0]), "futureFound": found, "checked": len(cutoffs)}


def finish_job(job, state):
    """After the numbers: the code's future-data check, which reruns the code; the check in work.json is replaced by its result."""
    evaluation, raw, panel, roots = state
    if evaluation is None or not job.get("code"):
        return None
    output = Path(job["output"])
    progress(output, "future", state="running")
    try:
        future = future_check(job, panel, roots, raw)
    except JobError as error:
        future = {"value": "not checked", "passed": None, "line": None, "title": "Recomputing without later prices failed: " + error.failure["message"]}
    work = json.loads((output / "work.json").read_text(encoding="utf-8"))
    work["checks"] = [item if item["id"] != "future_data" else {**item, "value": future["value"],
                      "state": "notrun" if future["passed"] is None else "ok" if future["passed"] else "watch", "title": future["title"]} for item in work["checks"]]
    write_json(output / "work.json", work)
    progress(output, "future", state="done")
    return future


def sweep(job, panel, raw, settings, roots, output):
    """Every combination of the sweep's axes: the run settings change the test, a code setting changes the values (the code runs again)."""
    cells = W.sweep_axes(settings, job["sweep"])
    out, reuse, similar = [], {}, None
    for number, (combo, cell) in enumerate(cells):
        values = raw
        if cell.get("params") and job.get("code"):
            code = {**job["code"], "params": {**(job["code"].get("params") or {}), **cell["params"]}}
            values, _ = code_values(code, panel, roots)
        # Holding, groups and costs do not change the processed values; the code's own settings do.
        key = (json.dumps(cell.get("params"), sort_keys=True), cell["delay"], cell["extremes"], cell["scale"], cell["neutralize"], json.dumps(cell["leaveOut"], sort_keys=True))
        evaluation = W.Evaluation(panel, values, cell, formula=job.get("formula") is not None, step=5, with_similar=similar is None or bool(cell.get("params")),
                                  shared=reuse.get(key))
        reuse.setdefault(key, evaluation.shared())
        if similar is None:
            similar = evaluation.similar
        elif not cell.get("params"):
            evaluation.similar = similar
        review = sum(1 for item in evaluation.checks if item["state"] == "watch")
        out.append({"combo": combo, "settings": cell, "numbers": _clean(evaluation.numbers), "checks": _clean(evaluation.checks), "review": review,
                    "figures": _clean(evaluation.figures())})
        progress(output, "numbers", state="running", done=number + 1, total=len(cells))
    return {"kind": "sweep", "axes": job["sweep"], "cells": out, "dataTo": int(panel.labels[-1]), "dataFrom": int(panel.labels[0]), "notes": panel.notes}


def held_out(job, panel, raw, settings, start, output):
    """The run scored once on the held-out period; the in-sample path comes from the run it validates, the held-out one continues it."""
    evaluation = W.held_out(panel, raw, settings, start, formula=job.get("formula") is not None)
    first = int(np.searchsorted(panel.labels, start))
    dates = panel.dates[first:]
    net = evaluation.returns["net"][first:]
    path = np.cumprod(1 + np.nan_to_num(net)) - 1
    inside = job.get("inSample") or {}
    base = inside.get("ls") or {"x": [], "y": []}
    offset = base["y"][-1] if base["y"] else 0.0
    held = [(1 + offset) * (1 + value) - 1 for value in path]
    x = [stamp.strftime("%Y-%m-%d") for stamp in dates]
    figure = {"data": [{"type": "scatter", "mode": "lines", "x": base["x"], "y": base["y"], "name": "In-sample", "showlegend": False,
                        "meta": {"role": "benchmark", "key": "ls-net:in-sample"}, "hovertemplate": "In-sample %{y:+.1%}<extra></extra>"},
                       {"type": "scatter", "mode": "lines", "x": ([base["x"][-1]] if base["x"] else []) + x, "y": ([offset] if base["x"] else []) + [round(v, 6) for v in held],
                        "name": "Held out", "showlegend": False, "meta": {"role": "main", "key": "ls-net:held-out"}, "hovertemplate": "Held out %{y:+.1%}<extra></extra>"}],
              "layout": {"meta": {"key": "factor.heldout"}, "showlegend": False, "hovermode": "x unified", "margin": {"l": 8, "r": 44, "t": 18, "b": 22},
                         "yaxis": {"tickformat": ".0%", "side": "right"}, "xaxis": {"type": "date"},
                         "shapes": [{"type": "rect", "xref": "x", "yref": "paper", "x0": x[0] if x else None, "x1": x[-1] if x else None, "y0": 0, "y1": 1,
                                     "line": {"width": 0}, "meta": {"role": "group"}}] if x else [],
                         "annotations": [{"x": x[0], "y": 1, "yref": "paper", "text": i18n.t("held out"), "showarrow": False, "xanchor": "left", "yanchor": "top", "name": "held-out"}] if x else []}}
    numbers = evaluation.numbers
    years = [item for item in evaluation.details["years"] if item["year"] >= dates[0].year] if len(dates) else []
    last, begin = panel.dates[-1], dates[0] if len(dates) else None
    for item in years:
        late_start = begin is not None and item["year"] == begin.year and (begin.month > 1 or begin.day > 7)
        early_end = item["year"] == last.year and (last.month < 12 or last.day < 24)
        item["partial"] = late_start or early_end
        item["from"] = begin.strftime("%b") if late_start else None
        item["to"] = last.strftime("%b") if early_end else None
    return {"kind": "heldout", "numbers": _clean(numbers), "years": _clean(years), "figures": {"heldout": _clean(figure)},
            "from": int(start), "to": int(panel.labels[-1]), "dataTo": int(panel.labels[-1]), "dataFrom": int(panel.labels[0]), "notes": panel.notes}


def engine_failure(error):
    """What a run shows when the engine itself breaks: what broke, not the folders or ids in the error (the traceback goes to the log)."""
    return {"stage": "compute", "title": i18n.t("The factor engine failed"),
            "message": i18n.t("The factor engine hit an internal error ({error_type}) and stopped; the details are in Quant's log.", error_type=type(error).__name__)}


def handle(message):
    op = message.get("op")
    if op == "ping":
        return {"ok": True, "panels": list(PANELS)}
    if op == "run":
        job = message["job"]
        try:
            result, state = run_job(job)
        except JobError as error:
            progress(job["output"], "failed", failure=error.failure)
            return {"ok": False, "failure": error.failure}
        reply = {"ok": True, "seconds": result["seconds"], "kind": result.get("kind", "run")}
        if reply["kind"] == "values":
            reply.update(result)
        return reply, (job, state)
    if op == "model_values":  # a model run's day of values: the run's inputs and models kept here for the next day (a process of its own, `warm`)
        from native import model_run
        try:
            return model_run.values({**message["job"], "warm": True})
        except model_run.RunFailure as error:
            return {"ok": False, "failure": error.failure}
    if op in ("model_check", "model_inspect"):  # a model's check before training, on the panel kept here
        from native import model_check
        return model_check.handle(op, message["job"], load_panel)
    if op == "allocation_trial":  # an allocation's latest period only (the same fit as every period of its backtest), on the returns kept here
        from native import allocation_run
        return allocation_run.trial(message["job"])
    if op == "allocation_values":  # what a published allocation makes for the bars after its cursor
        from native import allocation_run
        return allocation_run.values(message["job"])
    if op == "formula_values":  # a published formula factor's newest values (label, column, value), later than `after`, for the names that are members that day
        job = message["job"]
        panel = load_panel(job["data"])
        try:
            values, _ = formula_values(job["formula"], panel)
        except JobError as error:
            return {"ok": False, "failure": error.failure}
        rows = [(int(panel.labels[t]), panel.columns[i], float(values[t, i])) for t in range(len(panel.labels)) if int(panel.labels[t]) > int(job.get("after") or 0)
                for i in range(len(panel.columns)) if np.isfinite(values[t, i]) and bool(panel.member[t, i])]
        path = Path(job["output"]) / "values.parquet"
        pd.DataFrame(rows, columns=["label", "column", "value"]).to_parquet(path, index=False)
        return {"ok": True, "rows": len(rows), "path": str(path)}
    raise ValueError(f"Unknown job {op!r}")


def resident():
    """Answer jobs from stdin until it closes; the Host ends the process after it has been idle a while."""
    answers = sys.stdout
    sys.stdout = sys.stderr  # the author's prints must never look like an answer
    print(json.dumps({"talyx": "factor", "ready": True}), file=answers, flush=True)
    for line in sys.stdin:
        if not line.strip():
            continue
        message = json.loads(line)
        identity = message.get("id")
        later = None
        try:
            answer = handle(message)
            if isinstance(answer, tuple):
                answer, later = answer
        except Exception as error:  # the process must stay up for the next job; the run shows what broke here
            traceback.print_exc()
            answer = {"ok": False, "failure": engine_failure(error)}
            if (message.get("job") or {}).get("output"):
                progress(message["job"]["output"], "failed", failure=answer["failure"])
        print(json.dumps({"talyx": "factor", "id": identity, **answer}, default=_plain), file=answers, flush=True)
        if later is not None:
            try:
                future = finish_job(*later)
            except Exception as error:  # noqa: BLE001 - reported on the run, the process goes on
                traceback.print_exc()
                future = {"value": "not checked", "passed": None, "title": engine_failure(error)["message"]}
            if future is not None:
                print(json.dumps({"talyx": "factor", "id": identity, "future": future}, default=_plain), file=answers, flush=True)


def once(invocation):
    """One job on its own: the same work, the answer in `answer.json` beside its files."""
    job = invocation["job"]
    output = Path(job["output"])
    output.mkdir(parents=True, exist_ok=True)
    try:
        answer = handle({"op": invocation.get("op", "run"), "job": job})
        if isinstance(answer, tuple):
            answer, later = answer
            answer["future"] = finish_job(*later)
    except Exception as error:  # noqa: BLE001
        traceback.print_exc()
        answer = {"ok": False, "failure": engine_failure(error)}
        progress(output, "failed", failure=answer["failure"])
    write_json(output / "answer.json", answer)
    return 0 if answer.get("ok") else 1


def main():
    invocation = json.loads(Path(sys.argv[1]).read_text(encoding="utf-8"))
    if invocation.get("support"):
        sys.path.insert(0, invocation["support"])
    os.environ.setdefault("MPLBACKEND", "Agg")
    if invocation["mode"] == "once":
        return once(invocation)
    resident()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
