"""Run history of one object: summaries, comparison, notes and stars, and what a failed run reports."""
from __future__ import annotations

import json
from pathlib import Path

from talyx_sdk import ContractError

ACCOUNT_FIELDS = ("totalReturn", "maximumDrawdown", "dailySharpe", "initialEquityNumeric", "finalEquityNumeric", "equityChangeNumeric")
NAMES = {"initialEquityNumeric": "initialEquity", "finalEquityNumeric": "finalEquity", "equityChangeNumeric": "equityChange"}


def metrics_of(result):
    """Headline numbers of a finished run: a strategy's are the ones its report stored (one definition each, the same numbers its pages show);
    a backtest without a report gives counts and, per account, return, drawdown, Sharpe and equity; research what its report names."""
    if result["metrics"]:
        return result["metrics"]
    report = (result.get("strategyReport") or {}).get("stats")
    if report is not None:
        return {key: value for key, value in report.items() if value is not None}
    headline = {}
    for unit in result["units"]:
        native = unit.get("nativeStatistics")
        if native is None:
            continue
        accounts = native["accountPerformance"]
        values = {"orders": native["total_orders"], "positions": native["total_positions"]}
        for account in accounts:
            prefix = "" if len(accounts) == 1 else f"{account['account']}.{account['currency']}."
            values.update({prefix + NAMES.get(key, key): account[key] for key in ACCOUNT_FIELDS})
            pnl = native["stats_pnls"][account["currency"]]
            values[prefix + "winRate"] = pnl["Win Rate"]
        return {**values, **headline}
    return headline


def describe_failure(result, object_directory, stderr, code):
    """Where in the author's file a failed run broke; falls back to the process output when there is no report."""
    failure = result.get("failure") if result else None
    if failure is None:
        tail = stderr.strip()[-1500:]
        return {"code": "process_failed", "message": f"The backtest process exited with code {code}." + (f"\n{tail}" if tail else ""),
                "file": None, "line": None, "frames": []}
    # The author's files are opened from the object's folder, not from the run's fixed copy of it.
    frames = [{"root": item["root"], "file": item["file"], "path": (Path(object_directory) / item["file"]).as_posix() if item["root"] == "source" else item["path"],
               "line": item["line"], "function": item["function"], "text": item["text"]} for item in failure["frames"]]
    inner = next((item for item in reversed(frames) if item["root"] in {"source", "library"}), None)
    return {"code": "strategy_error", "type": failure["type"], "message": f"{failure['type']}: {failure['message']}",
            "file": inner["file"] if inner else None, "line": inner["line"] if inner else None,
            "frames": frames, "traceback": failure["traceback"]}


def flat(value, prefix=""):
    if isinstance(value, dict):
        return {key: item for name, part in value.items() for key, item in flat(part, f"{prefix}.{name}" if prefix else str(name)).items()}
    return {prefix: value}


def parameter_diff(current, previous):
    return {name: {"before": previous.get(name), "after": current.get(name)}
            for name in sorted(current.keys() | previous.keys()) if current.get(name) != previous.get(name)}


def data_label(settings):
    data = settings["data"]
    return {"symbols": [item["instrument"]["displaySymbol"] for item in data.get("symbols", [])], "timeframe": data.get("timeframe"),
            "since": data.get("since"), "until": data.get("until"), "list": data.get("listName"), "index": data.get("indexName"), "universe": data.get("universe")}


def sweep_summary(record):
    """What the history shows of a sweep (how its combinations stand and which one the report shows) and of one of its combinations (which sweep, which of them)."""
    if record["runKind"] == "case":
        return record["sweep"]
    sweep = record["sweep"]
    cases = sweep["cases"]
    selected = next((case for case in cases if case["index"] == sweep["selected"]), None)
    return {"metric": sweep["metric"], "parameters": [item["name"] for item in sweep.get("axes", sweep.get("ranges", []))], "total": len(cases),
            "completed": sum(case["state"] == "completed" for case in cases), "failed": sum(case["state"] == "failed" for case in cases),
            "running": sum(case["state"] in {"queued", "running"} for case in cases), "selected": sweep["selected"],
            "selectedRunId": selected["runId"] if selected else None}


class RunHistory:
    """The history lists one entry per run and per sweep; a sweep's combinations are runs of their own, listed under the sweep."""

    def __init__(self, records, runs):
        self.records, self.runs = records, runs

    def before(self, record):
        """The entry before this one: what its parameters are compared with. A combination of a sweep is compared with the sweep's own parameters."""
        if record["runKind"] == "case":
            return self.runs.read(record["sweep"]["sweepId"])
        row = self.records.db.execute("""SELECT content FROM records WHERE kind='qrun' AND json_extract(content,'$.objectId')=? AND json_extract(content,'$.runKind')!='case'
            AND (json_extract(content,'$.createdAt'), id) < (?,?) ORDER BY json_extract(content,'$.createdAt') DESC, id DESC LIMIT 1""",
            (record["objectId"], record["createdAt"], record["id"])).fetchone()
        return json.loads(row[0]) if row else None

    def summary(self, record):
        if record.get("objectKind") == "factor":
            from .factor_runs import summary
            return summary(record)
        if record.get("allocation"):
            from .allocation_runs import summary
            return summary(record)
        if record.get("modelRun") or record.get("pipeline") is True:
            from .model_runs import summary
            return summary(record)
        if record.get("modelPublish"):
            from .model_library import ModelLibrary
            return ModelLibrary.view(record)
        previous = self.before(record)
        error = record["error"]
        return {"id": record["id"], "objectId": record["objectId"], "name": record["name"], "runKind": record["runKind"], "mode": record["mode"],
                "publish": record["publish"] if record["runKind"] == "publish" else None, "sweep": sweep_summary(record) if record["runKind"] in {"sweep", "walk", "case"} else None,
                "state": record["state"], "stage": record["stage"], "createdAt": record["createdAt"], "endedAt": record["endedAt"], "message": record["message"],
                "error": {key: error.get(key) for key in ("stage", "code", "message", "file", "line", "details", "request")} if error else None,
                "params": record["params"], "parameterDiff": parameter_diff(record["params"], previous["params"]) if previous and "params" in previous else {},
                "data": data_label(record["settings"]), "metrics": record["metrics"], "starred": record["starred"],
                "note": record["note"], "codeDigest": record["code"]["digest"] if record["code"] else None}

    def latest(self, object_id):
        # A publication is not a run of the object.
        row = self.records.db.execute("""SELECT content FROM records WHERE kind='qrun' AND json_extract(content,'$.objectId')=? AND json_extract(content,'$.runKind') NOT IN ('case','publish')
            ORDER BY json_extract(content,'$.createdAt') DESC, id DESC LIMIT 1""", (object_id,)).fetchone()
        return self.summary(json.loads(row[0])) if row else None

    def list(self, object_id, args):
        clauses, values = ["kind='qrun'", "json_extract(content,'$.objectId')=?"], [object_id]
        if "sweepId" in args:
            clauses.append("json_extract(content,'$.sweep.sweepId')=?")
            values.append(args["sweepId"])
        else:
            clauses.append("json_extract(content,'$.runKind')!='case'")
        if args.get("state"):
            clauses.append("json_extract(content,'$.state')=?")
            values.append(args["state"])
        if args.get("starredOnly"):
            clauses.append("json_extract(content,'$.starred')=1")
        offset, limit = args.get("offset", 0), args.get("limit", 100)
        where = " AND ".join(clauses)
        total = self.records.db.execute("SELECT count(*) FROM records WHERE " + where, values).fetchone()[0]
        rows = self.records.db.execute("SELECT content FROM records WHERE " + where +
            " ORDER BY json_extract(content,'$.createdAt') DESC, id DESC LIMIT ? OFFSET ?", [*values, limit, offset])
        items = [self.summary(json.loads(row[0])) for row in rows]
        return {"items": items, "total": total, "offset": offset, "nextOffset": offset + limit if offset + limit < total else None}

    def annotate(self, identity, args):
        record = self.runs.read(identity)
        changes = {key: args[key] for key in ("note", "starred") if key in args}
        return self.summary(self.runs.save(record, **changes))

    def compare(self, identity, other):
        current, previous = self.runs.read(identity), self.runs.read(other)
        if current["objectId"] != previous["objectId"]:
            raise ContractError("invalid_input", "Compare runs of the same object.")
        left, right = current["metrics"], previous["metrics"]
        metrics = {}
        for key in sorted(left.keys() | right.keys()):
            a, b = left.get(key), right.get(key)
            metrics[key] = {"current": a, "previous": b, "difference": a - b if isinstance(a, (int, float)) and isinstance(b, (int, float)) else None}
        return {"current": self.summary(current), "previous": self.summary(previous), "metrics": metrics,
                "parameterDiff": parameter_diff(current["params"], previous["params"]),
                "codeChanged": (current.get("code") or current.get("codeSnapshot") or {}).get("digest")
                != (previous.get("code") or previous.get("codeSnapshot") or {}).get("digest"),
                "dataChanged": flat(data_label(current["settings"])) != flat(data_label(previous["settings"])),
                "dataDiff": {"current": data_label(current["settings"]), "previous": data_label(previous["settings"])}}
