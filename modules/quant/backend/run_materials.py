"""What a notebook may read of a finished run: the paths of the files the run left, by reference (nothing is copied), and the settings it ran with.

The run's folder already holds its tables as parquet; the kernel reads them lazily by path. A run made before this version of TALYX (no `data` in its
record) is not opened.
"""
from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

from talyx_sdk import ContractError

SKIP_TOP = {"source", "common"}


def files_of(directory):
    """Every parquet file and the result index under the run's output folder: {path relative to the run folder, absolute path}."""
    root = Path(directory)
    found = {}
    for path in sorted(root.rglob("*")):
        if path.is_file() and path.suffix in {".parquet", ".json"} and path.relative_to(root).parts[0] not in SKIP_TOP:
            found[path.relative_to(root).as_posix()] = str(path)
    return found


def day_of(value):
    """The date a run's data ends: stored as UTC milliseconds or already as text."""
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        return datetime.fromtimestamp(value / 1000, tz=UTC).date().isoformat()
    return value


def milliseconds_of(value):
    """A run record's date as UTC milliseconds, whichever way the kind that made it stored it: milliseconds already, or a day written 2017-06-01 (or a full ISO time). Anything else is None."""
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return int(value)
    if isinstance(value, str):
        try:
            moment = datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError:
            return None
        return int((moment if moment.tzinfo else moment.replace(tzinfo=UTC)).timestamp() * 1000)
    return None


def failure_of(record):
    return record.get("failure") if isinstance(record.get("failure"), dict) else None


def _day(milliseconds):
    """A creation time in ms as Nov 7, 2026 (empty when there is none)."""
    if not isinstance(milliseconds, (int, float)) or isinstance(milliseconds, bool):
        return ""
    from datetime import datetime
    moment = datetime.fromtimestamp(milliseconds / 1000, tz=UTC)
    return f"{moment:%b} {moment.day}, {moment.year}"


def run_directory(runs, record):
    """Where a run keeps its files: research and strategy runs, model runs and allocation runs each have their own folder."""
    root = getattr(runs.records, "root", None)
    if root is not None:
        if record.get("allocation"):
            return root / "qallocations" / record["id"]
        if record.get("modelRun") or record.get("pipeline") is True:
            return root / "qmodels" / record["id"]
    return runs.directory(record["id"])


class RunMaterials:
    def __init__(self, store, runs, frozen_root=None):
        self.store, self.runs, self.frozen_root = store, runs, frozen_root

    def frozen_data(self, record, directory):
        """Where the prices a run read are kept (its frozen data version, or the copy an allocation run keeps) and their instrument information: {barsPath, infoPath} or None."""
        key = ((record.get("data") or {}).get("key") or "").split("@")[0]
        folders = [self.frozen_root / key] if self.frozen_root is not None and key else []
        folders.append(Path(directory) / "data")
        for folder in folders:
            bars, info = folder / "bars.parquet", folder / "info.json"
            if bars.is_file() and info.is_file():
                return {"barsPath": str(bars), "infoPath": str(info)}
        return None

    def objects_named(self, name, kind=None, by_run=True, number=None):
        """The one object called `name` (of `kind`). When several are, the error says what tells them apart: the kind when the kinds differ,
        otherwise each one's run to pass by id (the run `number`, else its latest) with the day it was made and how many runs it has."""
        found = [item for item in self.store.records.list("qobject") if not item.get("deleted") and item["name"] == name and (kind is None or item["kind"] == kind)]
        if len(found) > 1:
            kinds = sorted(item["kind"] for item in found)
            if len(set(kinds)) == len(kinds):
                raise ContractError("invalid_input", f"{len(found)} objects are named {name} ({', '.join(kinds)}); pass kind=… or the run id", {"kinds": kinds})
            if not by_run:
                raise ContractError("invalid_input", f"{len(found)} objects are named {name}; give them different names", {"kinds": kinds})
            choices = [self._choice(item, number) for item in sorted(found, key=lambda item: item.get("createdAt", 0))]
            plural = f"{kinds[0]}s" if len(set(kinds)) == 1 else "objects"
            raise ContractError("invalid_input", f"{len(found)} {plural} are named {name}; choose one by its run: " + "; ".join(choices), {"kinds": kinds})
        if not found:
            # Say which objects there are (a library item is read with q.library.read, by its own name), so the next call can be right
            names = sorted({item["name"] for item in self.store.records.list("qobject") if not item.get("deleted") and (kind is None or item["kind"] == kind)})
            close = [other for other in names if name.lower() in other.lower() or other.lower() in name.lower()] or names[:12]
            raise ContractError("not_found", f"There is no {kind or 'object'} named {name}" + (f"; there are: {', '.join(close)}" if close else "")
                                + ". A library item is read with q.library.read(name).", {"names": close})
        return found[0]

    def _choice(self, item, number):
        """One of several objects with the same name, as the call that reads its run and what tells it apart."""
        ordered = self.ordered(item["id"])
        made = _day(item.get("createdAt"))
        runs = f"{len(ordered)} run{'' if len(ordered) == 1 else 's'}"
        if number is not None:
            chosen = next((run for run in ordered if run.get("number") == number), None) or (ordered[number - 1] if 1 <= number <= len(ordered) else None)
        else:
            chosen = ordered[-1] if ordered else None
        tag = f"{item['kind']}, made {made}, {runs}" if made else f"{item['kind']}, {runs}"
        if chosen is None:
            return f"the {tag}{f' has no run #{number}' if number is not None and ordered else ''}"
        return f"q.run(run_id={chosen['id']!r}) for the {tag}"

    def ordered(self, object_id):
        return sorted((item for item in self.runs.records.list("qrun") if item["objectId"] == object_id), key=lambda item: item.get("createdAt", 0))

    def number_of(self, record):
        """The number a run goes by: a strategy's own, else its place among the object's runs."""
        if record.get("number"):
            return record["number"]
        return 1 + [item["id"] for item in self.ordered(record["objectId"])].index(record["id"])

    def run_of(self, args):
        if args.get("runId"):
            record = self.runs.records.find("qrun", args["runId"])
            if record is None:
                raise ContractError("not_found", "This run no longer exists.", {"runId": args["runId"]})
            return record
        obj = self.objects_named(args["object"], args.get("kind"), number=args.get("number"))
        ordered = self.ordered(obj["id"])
        number = args.get("number")
        if number is None:
            if not ordered:
                raise ContractError("not_found", f"{obj['name']} has no runs yet.")
            return ordered[-1]
        found = next((item for item in ordered if item.get("number") == number), None)
        if found is None and 1 <= number <= len(ordered):
            found = ordered[number - 1]
        if found is None:
            raise ContractError("not_found", f"Run #{number} of {obj['name']} no longer exists")
        return found

    def read(self, args, brief=False):
        """`brief` is the answer for an AI caller: the paths and the numbers, with the instrument table and the index exclusions left in info.json (its path is given)."""
        record = self.run_of(args)
        if "data" not in record:
            raise ContractError("unavailable", f"Run #{record.get('number') or '?'} was made before this TALYX version; open it in a new run")
        directory = run_directory(self.runs, record)
        state = record["state"]
        data = self.frozen_data(record, directory)
        settings = record.get("settings")
        if brief:   # an AI caller reads the counts here; a notebook reads the whole information from infoPath (tens of MB for an index)
            from .ai_views import lean
            settings = lean(settings)
            data = self.brief_data(data) if data is not None else None
        return {"kind": record["objectKind"], "object": record["name"], "runId": record["id"], "number": record.get("number"), "state": state,
                "settings": settings, "metrics": record.get("metrics") or {}, "dataTo": day_of((record.get("data") or {}).get("until") or (record.get("data") or {}).get("dataTo")),
                "failure": failure_of(record) if state == "failed" else None, "paths": files_of(directory), "data": data}

    @staticmethod
    def brief_data(data):
        """The frozen data for an AI caller: instruments as a count with the first few, the index's exclusions as a count; the full lists stay in info.json beside the bars."""
        import json
        info = json.loads(Path(data["infoPath"]).read_text(encoding="utf-8"))
        if isinstance(info.get("instruments"), list):
            info["instruments"] = {"count": len(info["instruments"]), "first": info["instruments"][:5]}
        if isinstance(info.get("index"), dict):
            info["index"] = {key: ({"count": len(value)} if isinstance(value, list) else value) for key, value in info["index"].items()}
        return {"barsPath": data["barsPath"], "infoPath": data["infoPath"], "info": info}
