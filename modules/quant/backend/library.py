"""The research library as the rest of Quant and its callers see it: items and versions, values a day at a time, who uses what, stopping,
archiving and comparing versions. What reads files or many rows (the list, an item's page, a day of values, a comparison) is synchronous
and the routes call it on a worker thread, never on the Host's loop; the store opens its own connection per call. What changes the library
runs on the loop, because it tells the rest of Quant (events, live series) right after."""
from __future__ import annotations

import asyncio
import contextlib
import difflib
import json
import shutil
import time
from datetime import UTC, datetime
from uuid import uuid4

import pandas as pd
from talyx_sdk import ContractError
from talyx_sdk.i18n import t

from . import library_figures, library_inputs, library_summary
from .library_loop import EXPIRES_AFTER
from .library_store import LibraryStore
from .materials import plain

PAGE = 5000
VALUES_PAGE = 50
DAY = 86_400_000


def day_text(milliseconds):
    return time.strftime("%b %d, %Y", time.gmtime(milliseconds / 1000)) if milliseconds is not None else None


class Library:
    def __init__(self, ctx, records, clock=None):
        self.ctx, self.records = ctx, records
        self.root = records.root / "library2"  # the old catalog in library/ is never read or deleted
        self.store = LibraryStore(self.root, **({"clock": clock} if clock else {}))
        self.reading = asyncio.Semaphore(4)
        self.on_change = None  # set by whoever follows changes: called with (item id, version id) after each one
        self.loop = None  # the production loop, once it runs: its queue and the next round show in the list

    def version_directory(self, version_id):
        return self.root / "versions" / version_id

    def publish_change(self, item_id=None, version_id=None):
        try:
            self.ctx.publish("library_changed", {"itemId": item_id, "versionId": version_id})
        except ContractError as error:
            if error.code != "provider_unloaded":
                raise
            return
        if self.on_change is not None:
            self.on_change(item_id, version_id)

    async def environment_state(self, environment):
        """The frozen research environment a version or run names, if the platform still has it."""
        state = plain(self.ctx.environments.get(environment["preparationId"]))
        if state["state"] != "completed":
            raise ContractError("unavailable", t("The environment this library version was made in is no longer available."), {"preparation": state})
        return {"interpreterPath": environment["interpreterPath"]}

    # --- the list ------------------------------------------------------------------------------------------------------
    def list(self, args):
        """The list: the stored summary of every item (problems first), how many need attention, the production queue and the next round."""
        items = self.store.summaries(args.get("query"))
        if args.get("kind"):
            items = [item for item in items if item["kind"] == args["kind"]]
        if args.get("state"):
            items = [item for item in items if item["state"] == args["state"] or args["state"] == "attention" and item["attention"]]
        archivable = [{"itemId": item["itemId"], "name": item["name"], "versionId": version["versionId"], "version": version["version"]}
                      for item in items for version in item["versions"] if version["state"] == "stopped" and not version["users"]]
        production = self.loop.view() if self.loop is not None else None
        return {"items": items, "needsAttention": sum(1 for item in items if item["attention"]), "archivable": archivable, "production": production,
                "nextRound": production["next"] if production else None}

    def read_item(self, item_id):
        versions = self.store.item_versions(item_id)
        item = self.store.item(item_id)
        return {"itemId": item_id, "name": item["name"], "kind": item["kind"], "versions": versions}

    def carries_values(self, version_id):
        """Whether a version holds values a reader may take: every factor, allocation and data item, and a model published by the training
        pipeline (its predictions). A model published as a fitted object only (q.report.model) holds none."""
        version = self.store.version(version_id)
        kind = self.store.summary_kind(version["item_id"])
        return kind != "model" or bool(version["meta"].get("pipeline")) or bool(version["meta"].get("fields"))

    def read_version(self, version_id):
        return self.version_view(self.store.version(version_id))

    def version_view(self, version):
        """`read_version` of a version already read: its record is parsed once per page, not once per part (a version published before the
        index summary carries 50-66 MB of membership, and the item page parsed it four or five times)."""
        with self.store.connect() as db:
            summary = self.store.version_summary(db, version)
        meta = version["meta"]
        return {**summary, "carriesValues": summary["kind"] != "model" or bool(meta.get("pipeline")) or bool(meta.get("fields")), "published": meta.get("published"), "after": version["after"], "parameters": meta.get("params", {}), "data": meta.get("data"),
                "settings": meta.get("settings"), "calendar": {key: meta["info"][key] for key in ("calendarId", "sessionScope", "timezone", "annualization")} if "info" in meta else None,
                "code": meta.get("code"), "formula": meta.get("formula"), "publication": meta.get("publication"), "measure": meta.get("measure"),
                "universe": meta.get("universe"), "inputs": meta.get("inputs", []), "notes": meta.get("notes", []), "settingsText": meta.get("settingsText"),
                "oneOff": bool(meta.get("oneOff")), "horizon": meta.get("horizon"), "publishedThrough": meta.get("publishedThrough"),
                "trainingCutoff": (meta.get("pipeline") or {}).get("trainedThrough") if summary["kind"] == "model" else None,
                "heldOutFrom": (meta.get("pipeline") or {}).get("heldOutFrom") if summary["kind"] == "model" else None}

    # --- one item's page ------------------------------------------------------------------------------------------------------
    def detail(self, item_id, version_id=None, metric="ic", span="since", figures=True):
        """The item page: its summary and state, what to do about it, each version with its figures and users, the chosen version's record after
        publication, its production periods, the newest failed tries, what feeds what, and its frozen files."""
        summary = self.store.summary_of(item_id)
        versions = self.store.item_versions(item_id)
        chosen = next((item for item in versions if item["versionId"] == version_id), None) if version_id else None
        chosen = chosen or next((item for item in versions if item["versionId"] == summary.get("shownVersionId")), versions[0])
        health = {item["versionId"]: item for item in summary["versions"]}
        records = {entry["versionId"]: self.store.version(entry["versionId"]) for entry in versions}
        out = []
        for entry in versions:
            full = self.version_view(records[entry["versionId"]])
            after = full["after"] or {}
            settled = after.get("settled", 0)
            if after.get("kind") == "allocation" and after.get("worst"):
                # The asset whose risk share moved most, by its name, with its share then and now (library.js: "Risk share, SOL").
                instruments = (records[entry["versionId"]]["meta"].get("info") or {}).get("instruments", [])
                asset, shares = after["worst"]["asset"], after.get("shares") or {}
                name = next((item.get("displaySymbol") or item.get("column") for item in instruments if item["instrumentId"] == asset), asset)
                after = {**after, "worstShare": {"name": name, "published": (shares.get("published") or {}).get(asset), "now": (shares.get("now") or {}).get(asset),
                                                 "delta": after["worst"]["delta"]}}
            out.append({"versionId": entry["versionId"], "version": entry["version"], "state": library_summary.public_state({"archived_at": entry["archivedAt"], "state": entry["state"]}),
                        "health": health.get(entry["versionId"], {}).get("health"), "count": health.get(entry["versionId"], {}).get("count", 0),
                        "auto": entry["auto"], "publishedAt": entry["createdAt"], "stoppedAt": entry["stoppedAt"], "archivedAt": entry["archivedAt"], "runId": entry["runId"],
                        "objectId": entry["objectId"], "origin": entry["origin"], "settingsText": full["settingsText"], "measure": full["measure"],
                        "pub": full["published"], "after": after or None, "collecting": library_summary.collecting(after), "settledDays": after.get("settledDays", 0),
                        "settled": settled, "pending": after.get("pending", 0), "users": self.users(entry["versionId"]), "cursor": entry["cursor"],
                        "lastDatetime": entry["lastDatetime"], "failure": entry["failure"], "inputs": full["inputs"], "rows": entry["rows"], "oneOff": full["oneOff"]})
        version = records[chosen["versionId"]]
        return {"summary": summary, "versions": out, "versionId": chosen["versionId"], "issue": self.issue(summary, version),
                # The charts only for the page: an AI caller reads the figures and periods, not Plotly (library_ai).
                "overview": self.overview(version) if figures else None, "figures": self.figures_of(chosen["versionId"], version, metric, span) if figures else None,
                "periods": self.store.periods(chosen["versionId"], limit=60), "feeds": self.feeds(item_id),
                "inputs": self.inputs(version), "nextDue": self.loop.next_due(chosen["versionId"]) if self.loop is not None else None,
                "failures": self.failures(chosen["versionId"]), "files": self.source_files(chosen["versionId"], version), "users": self.users(chosen["versionId"]),
                "oneOff": bool(version["meta"].get("oneOff")), "origin": version["meta"].get("origin")}

    def failures(self, version_id):
        """The newest five failed tries, each with what the author's code said (its traceback and the output before it)."""
        return [{"at": row["at"], "label": row["label"], "message": row["message"], "location": row["location"], "traceback": row["traceback"], "output": row["output"]}
                for row in self.store.failure_logs(version_id)[:5]]

    def inputs(self, version):
        """What a version reads (a model's factor versions), each with its state now."""
        entries = version["meta"].get("inputs") or []
        found = self.store.versions([entry["versionId"] for entry in entries])
        out = []
        for entry in entries:
            other = found.get(entry["versionId"])
            state = "archived" if other is None or other["archived_at"] else library_summary.public_state(other)
            out.append({**entry, "state": state, "health": None if other is None else library_summary.version_state(other, self.store.periods(other["id"], limit=20),
                                                                                                                  self.store.clock())[0],
                        "cursor": None if other is None else other["cursor"]})
        return out

    def feeds(self, item_id):
        """What reads this item: published versions holding one of its versions, and who in turn uses those (a chain of three: this, the model, its users)."""
        out = []
        for user in self.store.occupants(item_id):
            if not user["owner_id"].startswith("qlib:"):
                continue
            reader = self.store.version(user["owner_id"].removeprefix("qlib:"))
            item = self.store.item(reader["item_id"])
            out.append({"itemId": item["id"], "name": item["name"], "kind": item["kind"], "versionId": reader["id"], "version": reader["version"],
                        "reads": user["version_id"], "state": library_summary.public_state(reader), "users": self.users(reader["id"])})
        return out

    def issue(self, summary, version):
        """What there is to say about the shown version's state, only when there is something: what, since when, the rows that explain it."""
        state = summary["state"]
        if state not in library_summary.ATTENTION or summary.get("shownVersionId") != version["id"]:
            return None
        periods = self.store.periods(version["id"], limit=60)
        last = periods[-1] if periods else {}
        streak = [period for period in periods if period["result"] == last.get("result")][-max(1, summary["stateCount"] or 1):]
        since = streak[0]["due_at"] if streak else version["updated_at"]
        rows, links = [], []
        name = f"{summary['name']} v{version['version']}"
        downstream = self.feeds(version["item_id"])
        next_due = self.loop.next_due(version["id"]) if self.loop is not None else None
        if state == "fail":
            failure = version["failure"] or {}
            location = failure.get("location")
            where = f" in {location['file']} line {location['line']}" if location and location.get("file") else ""
            rows += [{"label": t("Reason"), "text": (failure.get("message") or last.get("why") or "The code failed").rstrip(".") + where + "."}]
            if where:
                links.append({"kind": "source", "file": location["file"], "line": location["line"], "versionId": version["id"]})
            tries = failure.get("tries") or 1
            rows.append({"label": t("Tried"), "text": f"{tries} {'time' if tries == 1 else 'times'} since {_clock_text(since)}; "
                         + (f"next automatic try {_clock_text(version['retry_after'])}" if version["retry_after"] else "no automatic try scheduled")})
        elif state == "miss":
            # library.js: "Missing 1 period", due when; the period, why, and the next one with the rule that stops it.
            rows += [{"label": t("Period"), "text": _date_text(last["label"]) if last.get("label") else "—"},
                     {"label": t("Reason"), "text": last.get("why") or t("The source has no bar")}]
            if version["state"] != "expired":  # five in a row stopped it: there is no next one
                rows.append({"label": t("Next settlement"), "text": (f"due {_clock_text(next_due['at'])}" if next_due and next_due.get("at") else "with the next bar")
                             + f"; {EXPIRES_AFTER} missing in a row stops it"})
        elif state == "up":
            # library.js: "Upstream missing N periods"; the input that has no value and why, how many others came on time, who goes without.
            inputs = self.inputs(version)
            newest = last.get("label") or 0
            short = [entry for entry in inputs if entry["cursor"] is None or entry["cursor"] < newest or entry["state"] != "producing"]
            days = (f"{_day_text(streak[0]['label'])} – {_day_text(streak[-1]['label'])}" if len(streak) > 1 else _day_text(streak[0]["label"])) if streak else ""
            reasons = {"fail": "it failed", "auto_off": "its auto-update is off", "miss": "its source has no bar", "up": "it is missing an input itself", "stopped": "it stopped",
                       "archived": "it was archived", "expired": "it stopped"}
            texts = []
            for entry in short:
                links.append({"kind": "item", "itemId": entry.get("itemId"), "versionId": entry["versionId"], "name": entry["name"]})
                why = reasons.get(entry["state"]) if entry["state"] != "producing" else reasons.get(entry["health"])
                texts.append(f"{entry['name']} v{entry['version']} has no value for {days}" + (f", {why}" if why else ""))
            rows.append({"label": t("Missing input"), "text": ("; ".join(texts) or (last.get("why") or "An input has no value").rstrip("."))
                         + "; an input with no new value for a period counts as missing"})
            if len(inputs) > len(short):
                rows.append({"label": t("Other inputs"), "text": t("{inputs} on time", inputs=len(inputs) - len(short))})
        elif state == "weak":
            published, after = version["meta"].get("published") or {}, version["after"] or {}
            spread = f" ± {after['se']:.4f}" if after.get("se") is not None else ""
            rows.append({"label": version["meta"].get("measure") or t("Rank IC"),
                         "text": t("published {value}, after {value2}{spread}: {value3}", value=_signed(published.get('value')), value2=_signed(after.get('value')), spread=spread, value3=library_summary.weak_how(published.get('value') or 0, after.get('value') or 0))})
        elif state == "drift":
            after = version["after"] or {}
            multiple = after.get("multiple")
            rows.append({"label": t("Volatility"), "text": f"realised 63-day {_percent(after.get('value'))}, published {_percent(after.get('published'))}"
                         + (f": {multiple:.2f}×" + (", over 1.5×" if multiple > 1.5 else "") if multiple else "")})
            worst = after.get("worst")
            if worst:
                shares = after.get("shares") or {}
                names = {item["instrumentId"]: item.get("displaySymbol") or item.get("column") for item in (version["meta"].get("info") or {}).get("instruments", [])}
                now, then = (shares.get("now") or {}).get(worst["asset"]), (shares.get("published") or {}).get(worst["asset"])
                rows.append({"label": t("Risk share"), "text": f"{names.get(worst['asset'], worst['asset'])} {_percent(now)}, published {_percent(then)}: {worst['delta'] * 100:+.1f} pt".replace("-", "−")
                             + (", over 10 pt" if abs(worst["delta"]) > 0.10 else "")})
        elif state == "late":
            why = last.get("why") or "Made after it was due"
            rows.append({"label": t("Why"), "text": why})
        affected = [f"{item['name']} v{item['version']}" for item in downstream]
        if state in ("fail", "up", "weak", "drift"):
            text = ", ".join(affected) or "nobody"
            if state == "up" and affected and last.get("label"):
                text += f" {'gets' if len(affected) == 1 else 'get'} no {_day_text(last['label'])} value"
            rows.append({"label": t("Downstream"), "text": text})
        count = len(streak)
        plural = f"{count} periods" if count > 1 else "1 period"
        title = {"fail": f"{name} failed", "miss": f"Missing {plural}", "up": f"Upstream missing {plural}", "weak": "Weaker since publication",
                 "drift": "Risk off its published shape", "late": f"{name} was late"}[state]
        note = None
        if state == "late" and last.get("ready_at") and last.get("due_at"):
            title = f"Late by {_duration(last['ready_at'] - last['due_at'])}"
            note = f"{_day_text(last['label'])} value at {_clock_text(last['ready_at'])}, due {_clock_text(last['due_at'])}"
        elif state == "miss" and last.get("due_at"):
            note = f"due {_clock_text(last['due_at'])}"
        elif state == "up" and streak:
            first, final = _day_text(streak[0]["label"]), _day_text(streak[-1]["label"])
            made = "predictions" if (version["meta"].get("publication") or {}).get("kind") == "model" else "values"
            note = f"{first} – {final}: no {made}" if count > 1 else f"{first}: no {made[:-1]}"
        elif state in ("weak", "drift"):
            # library.js: "329 settled days after Jun 30, 2025": counted from the last bar the publication held, a data day (no viewer's clock in it).
            daily = str(version["meta"].get("timeframe") or "1d").endswith("d")
            through = version["meta"].get("publishedThrough") or version["created_at"]
            settled = (version["after"] or {}).get("settled", 0)
            note = f"{settled} {'settled ' if state == 'weak' else ''}{('trading days' if state == 'drift' else 'days') if daily else 'periods'} after {_date_text(through)}"
        if state == "fail" and streak:
            # library.js: "Failed 2 periods in a row", then the days that have no values.
            title = f"Failed {count} periods in a row" if count > 1 else "Failed 1 period"
            first, final = _day_text(streak[0]["label"]), _day_text(streak[-1]["label"])
            note = f"{first} – {final} have no values" if count > 1 else f"{first} has no values"
        return {"kind": state, "title": title, "since": since, "note": note, "rows": rows, "links": links}

    def figures_of(self, version_id, version, metric, span):
        """The charts of the chosen version, drawn here (library_figures): only its own kind's, as Plotly."""
        shown = {**self.version_view(version), "publishedAt": version["created_at"], "info": version["meta"].get("info")}
        return library_figures.of_version(shown, self.store.after_points(version_id) or [], metric=metric, span=span)

    def overview(self, version):
        """The chosen version's record: daily rank IC and long–short summed up, before and after publication, each day settled or pending; for
        an allocation its realized volatility and risk shares against publication."""
        meta = version["meta"]
        points = self.store.after_points(version["id"]) or []
        published = meta.get("published") or {}
        if (meta.get("publication") or {}).get("kind") == "allocation":
            after = version["after"] or {}
            shares = after.get("shares") or {}
            assets = sorted(set(shares.get("published") or {}) | set(shares.get("now") or {}))
            return {"kind": "allocation", "volSincePublish": [{"date": point["date"], "realized63d": point["value"], "after": point["after"]} for point in points],
                    "publishedVol": published.get("value"), "publishedAt": version["created_at"], "publishedThrough": meta.get("publishedThrough"),
                    "riskShares": {"published": [{"asset": asset, "share": (shares.get("published") or {}).get(asset)} for asset in assets],
                                   "now": [{"asset": asset, "share": (shares.get("now") or {}).get(asset)} for asset in assets]},
                    "table": [{"what": "Volatility", "published": published.get("value"), "now": after.get("value")},
                              {"what": "Return vs benchmark", "published": published.get("excessReturn"), "now": None},
                              {"what": "Max drawdown", "published": published.get("maxDrawdown"), "now": after.get("maxDrawdown")}]}
        series = {"rank_ic": [], "long_short": []}
        totals = {"rank_ic": 0.0, "long_short": 0.0}
        for point in points:
            for metric, key in (("rank_ic", "ic"), ("long_short", "ls")):
                value = point.get(key)
                if value is not None and point["settled"]:  # the IC's sum; the long-short compounds
                    totals[metric] = totals[metric] + value if metric == "rank_ic" else (1 + totals[metric]) * (1 + value) - 1
                series[metric].append({"date": point["date"], "value": value, "cumulative": round(totals[metric], 6), "settled": point["settled"], "after": point["after"]})
        return {"kind": "signal", "cumulative": [{"metric": metric, "points": series[metric], "publishedAt": version["created_at"],
                                                  "publishedThrough": meta.get("publishedThrough"),
                                                  "publishedLevel": published.get("value") if metric == "rank_ic" else None if published.get("ls") is None else published["ls"] / meta["info"]["annualization"]} for metric in series],
                "pending": (version["after"] or {}).get("pending", 0)}

    @staticmethod
    def user_view(user, version=None):
        owner = user["owner_id"]
        kind = user.get("kind") or "research"
        how = user.get("how") or ""
        return {"ownerId": owner, "name": user.get("name") or owner, "kind": kind, "how": how,
                "objectId": owner.removeprefix("qobject:") if owner.startswith("qobject:") else None,
                "libraryVersionId": owner.removeprefix("qlib:") if owner.startswith("qlib:") else None, "version": version,
                "readsDaily": owner.startswith("qlib:"), "unattended": owner.startswith("qlib:")}

    def updates(self, args):
        """The newest updates of an item (all its versions) or of one version."""
        limit = args.get("limit", 50)
        if "versionId" in args:
            self.store.version(args["versionId"])
            return {"items": self.store.updates(version_id=args["versionId"], limit=limit)}
        self.store.item_versions(args["itemId"])
        return {"items": self.store.updates(item_id=args["itemId"], limit=limit)}

    def log(self, version_id, offset=0, limit=None):
        """The production log: every period newest first (or a page of them from `offset`), and the last five failed tries with their tracebacks."""
        self.store.version(version_id)
        periods = self.store.periods(version_id)[::-1]
        page = periods[offset:offset + limit] if limit is not None else periods[offset:]
        return {"periods": page, "total": len(periods), "failures": self.failures(version_id), "updates": self.store.updates(version_id=version_id, limit=50)}

    # --- frozen files ------------------------------------------------------------------------------------------------------------
    def report(self, version_id):
        path = self.version_directory(version_id) / "report" / "result-index.json"
        if not path.is_file():
            raise ContractError("not_found", t("This version has no report."))
        return json.loads(path.read_text(encoding="utf-8"))

    def report_file(self, version_id, name):
        directory = (self.version_directory(version_id) / "report").resolve()
        path = (directory / name).resolve()
        listed = any(item["name"] == name for item in self.report(version_id)["artifacts"])
        if not listed or not path.is_relative_to(directory) or not path.is_file():
            raise ContractError("not_found", t("This report file has not been published."))
        return path

    def source_file(self, version_id, name):
        if name == "formula":
            formula = self.store.version(version_id)["meta"].get("formula")
            if formula is not None:
                return {"name": "formula", "content": formula, "readOnly": True}
        directory = (self.version_directory(version_id) / "source").resolve()
        path = (directory / name).resolve()
        if not path.is_relative_to(directory) or not path.is_file():
            raise ContractError("not_found", t("This frozen source file is unavailable."))
        return {"name": name, "content": path.read_text(encoding="utf-8"), "readOnly": True}

    def source_files(self, version_id, version=None):
        directory = self.version_directory(version_id) / "source"
        if not directory.is_dir():
            return ["formula"] if (version or self.store.version(version_id))["meta"].get("formula") is not None else []
        return sorted(path.relative_to(directory).as_posix() for path in directory.rglob("*.py"))

    # --- values a day at a time -------------------------------------------------------------------------------------------
    def values_page(self, version_id, *, date=None, as_of=None, order="top", query=None, page=0, field=None):
        """One day's values of a version, a page at a time: rank, name, sector and the value with the day before; or the names without a value
        and why. Only that day's rows (and the day before's) are read."""
        version = self.store.version(version_id)
        if version["archived_at"] is not None:
            raise ContractError("not_found", t("This version was archived; its values were dropped."))
        meta = version["meta"]
        field = field or (meta.get("fields") or [None])[0]
        days = [label for label, _ in self.store.days(version_id)]
        if not days:
            return {"versionId": version_id, "date": None, "days": 0, "rows": [], "have": 0, "total": 0, "missing": 0, "matching": 0, "page": 0, "pages": 0}
        if date is None:
            date = days[-1]
        elif date not in days:
            date = max([day for day in days if day <= date] or [days[0]])
        index = days.index(date)
        def effective_day(label):
            frame = self.store.frame(version_id, datetime=label, field=field, as_of=as_of)
            return frame.sort_values(["availableAt", "recordedAt"], kind="stable").drop_duplicates("instrument", keep="last")

        today = effective_day(date)
        before = effective_day(days[index - 1]) if index > 0 else None
        names = {item["instrumentId"]: item for item in (meta.get("info") or {}).get("instruments", [])}
        sectors = meta.get("sectors") or {}
        previous = dict(zip(before["instrument"], before["value"], strict=True)) if before is not None and len(before) else {}
        frame = today.sort_values("value", ascending=False, kind="stable").reset_index(drop=True)
        ranks = {instrument: position + 1 for position, instrument in enumerate(frame["instrument"])}
        universe = (meta.get("info") or {}).get("index")
        if universe is not None:
            # An index's names are those that were members that day; the rest were not in it and have no place among the missing.
            members = {instrument for instrument, held in universe["spans"].items() if any(since <= date <= until for since, until in held)} | set(frame["instrument"])
            gaps = {item["instrumentId"]: item for item in self.store.exclusions(version_id) if item["instrumentId"] is not None and item["since"] <= date <= item["until"]}
        else:
            members, gaps = set(names) if names else set(frame["instrument"]), {}
        missing = sorted(members - set(frame["instrument"]))
        kind = (meta.get("publication") or {}).get("kind")
        maker = "the data has" if kind in (None, "data") else f"the {kind} gave"

        def label(instrument):
            return names[instrument].get("displaySymbol") or names[instrument]["column"] if instrument in names else instrument

        def why(instrument):
            if instrument in gaps:
                return f"No price that day: {gaps[instrument].get('sourceReason') or gaps[instrument]['reason']}"
            return f"A member with prices that day; {maker} no value for it" if universe is not None else f"{maker.capitalize()} no value for it that day"
        if order == "missing":
            rows = [{"instrumentId": instrument, "name": label(instrument), "sector": sectors.get(instrument), "why": why(instrument)} for instrument in missing]
        else:
            ordered = frame if order == "top" else frame.iloc[::-1]
            rows = [{"rank": ranks[row.instrument], "instrumentId": row.instrument, "name": label(row.instrument), "sector": sectors.get(row.instrument),
                     "value": float(row.value), "previous": previous.get(row.instrument)} for row in ordered.itertuples(index=False)]
        if query:
            rows = [row for row in rows if row["name"].casefold().startswith(query.casefold())]
        pages = max(1, -(-len(rows) // VALUES_PAGE))
        page = max(0, min(page, pages - 1))
        period = self.store.period(version_id, date)
        ready = int(today["availableAt"].max()) if len(today) else None
        out = {"versionId": version_id, "date": date, "previousDay": days[index - 1] if index > 0 else None, "nextDay": days[index + 1] if index + 1 < len(days) else None,
               "firstDay": days[0], "lastDay": days[-1], "days": len(days), "readyAt": period["ready_at"] if period else ready, "dueAt": period["due_at"] if period else None,
               "result": period["result"] if period else "history", "onTime": None if period is None else period["result"] == "ok",
               "have": len(frame), "total": len(members), "missing": len(missing), "rows": rows[page * VALUES_PAGE:(page + 1) * VALUES_PAGE], "matching": len(rows),
               "page": page, "pages": pages, "field": field}
        if (meta.get("publication") or {}).get("kind") == "allocation":
            through = meta.get("publishedThrough")
            last_published = max([day for day in days if through is not None and day <= through] or [days[0]])
            published = self.store.frame(version_id, datetime=last_published, field=field, columns=["instrument", "value"])
            out["weights"] = {"published": [{"instrumentId": row.instrument, "name": label(row.instrument), "weight": float(row.value)} for row in published.itertuples(index=False)],
                              "thisPeriod": [{"instrumentId": row.instrument, "name": label(row.instrument), "weight": float(row.value)} for row in today.itertuples(index=False)],
                              "publishedDate": last_published}
        return out

    def days(self, version_id):
        return [label for label, _ in self.store.days(version_id)]

    # --- comparing two versions ---------------------------------------------------------------------------------------------
    def compare(self, version_a, version_b):
        """Two versions of one item side by side: published and after figures; where both produced the same days how alike the cross-sections were
        (mean daily rank correlation) and which names only one had; the code difference by file and the settings difference."""
        a, b = self.read_version(version_a), self.read_version(version_b)
        if a["itemId"] != b["itemId"]:
            raise ContractError("invalid_input", t("Compare two versions of the same item."))
        older, newer = (a, b) if a["version"] < b["version"] else (b, a)
        field = (newer["fields"] or [None])[0]
        overlap = None
        if older["archivedAt"] is None and newer["archivedAt"] is None and field:
            start = max(older["firstDatetime"] or 0, newer["firstDatetime"] or 0)
            first = self.store.frame(older["versionId"], field=field, start=start, columns=["datetime", "instrument", "value"])
            second = self.store.frame(newer["versionId"], field=field, start=start, columns=["datetime", "instrument", "value"])
            if len(first) and len(second):
                left = first.pivot_table(index="datetime", columns="instrument", values="value")
                right = second.pivot_table(index="datetime", columns="instrument", values="value")
                days = left.index.intersection(right.index)
                if len(days):
                    left, right = left.loc[days], right.loc[days]
                    columns = left.columns.intersection(right.columns)
                    correlation = left[columns].rank(axis=1).corrwith(right[columns].rank(axis=1), axis=1).dropna()
                    overlap = {"from": int(days[0]), "to": int(days[-1]), "days": len(days), "rankCorrelation": float(correlation.mean()) if len(correlation) else None,
                               "namesA": float(left.notna().sum(axis=1).median()), "namesB": float(right.notna().sum(axis=1).median()),
                               "onlyA": len(set(left.columns) - set(right.columns)), "onlyB": len(set(right.columns) - set(left.columns))}
        result = {"a": self.figures(older), "b": self.figures(newer), "overlap": overlap, "code": self.code_diff(older, newer), "settings": self.settings_diff(older, newer)}
        if (newer.get("publication") or {}).get("kind") == "allocation":
            result["weights"] = self.weights_latest(older, newer)
        return result

    def weights_latest(self, older, newer):
        """Each asset's weight in the newest period of both versions and the change."""
        def latest(version):
            days = self.days(version["versionId"])
            if not days or version["archivedAt"] is not None:
                return {}
            frame = self.store.frame(version["versionId"], datetime=days[-1], columns=["instrument", "value"])
            return dict(zip(frame["instrument"], frame["value"].astype(float), strict=True))
        first, second = latest(older), latest(newer)
        return [{"instrumentId": asset, "a": first.get(asset), "b": second.get(asset), "change": None if first.get(asset) is None or second.get(asset) is None
                 else second[asset] - first[asset]} for asset in sorted(first.keys() | second.keys())]

    @staticmethod
    def figures(version):
        return {"versionId": version["versionId"], "version": version["version"], "runId": version["runId"], "origin": version["origin"],
                "published": version["published"], "after": version["after"], "measure": version["measure"], "state": version["state"],
                "archivedAt": version["archivedAt"], "settingsText": version["settingsText"]}

    def code_diff(self, older, newer):
        if older.get("formula") is not None or newer.get("formula") is not None:
            if older.get("formula") == newer.get("formula"):
                return []
            return [{"file": "formula", "status": "changed", "diff": f"- {older.get('formula') or ''}\n+ {newer.get('formula') or ''}"}]
        first, second = self.version_directory(older["versionId"]) / "source", self.version_directory(newer["versionId"]) / "source"
        out = []
        for name in sorted({*self.source_files(older["versionId"]), *self.source_files(newer["versionId"])}):
            old = (first / name).read_text(encoding="utf-8", errors="replace").splitlines() if (first / name).is_file() else []
            new = (second / name).read_text(encoding="utf-8", errors="replace").splitlines() if (second / name).is_file() else []
            if old == new:
                continue
            lines = list(difflib.unified_diff(old, new, f"v{older['version']} {name}", f"v{newer['version']} {name}", lineterm="", n=2))
            out.append({"file": name, "status": "added" if not old else "removed" if not new else "changed", "diff": "\n".join(lines[:400])})
        return out

    @staticmethod
    def settings_diff(older, newer):
        def flat(value, prefix=""):
            if isinstance(value, dict):
                return {key: item for name, part in value.items() for key, item in flat(part, f"{prefix}.{name}" if prefix else name).items()}
            return {prefix: value}

        def of(version):
            return flat({"run": version.get("settings") or {}, "data": {key: value for key, value in (version.get("data") or {}).items() if key != "symbols"},
                         "code": version.get("parameters") or {}})
        before, after = of(older), of(newer)
        return [{"what": key, "from": before.get(key), "to": after.get(key)} for key in sorted(before.keys() | after.keys()) if before.get(key) != after.get(key)]

    # --- names and values for code and other modules -------------------------------------------------------------------
    def resolve(self, name, version=None, kind=None):
        """The version a name means: its newest producing one unless `version` says another."""
        items = self.store.find_item(name, kind)
        if not items:
            raise ContractError("not_found", t("The library has nothing named {name}.", name=name))
        versions = self.store.item_versions(items[0]["id"])
        if version is None:
            return next((item for item in versions if item["state"] in ("updating", "failed", "fixed") and item["archivedAt"] is None), versions[0])
        found = next((item for item in versions if item["version"] == version), None)
        if found is None:
            raise ContractError("not_found", t("{name} has no version {version}.", name=name, version=version), {"versions": [item["version"] for item in versions]})
        return found

    def resolve_values(self, name, version=None):
        found = self.resolve(name, version)
        if not found["fields"]:
            raise ContractError("invalid_input", t("{name} holds no values to read.", name=name))
        return found

    async def values(self, args):
        """Rows of one version, page by page: `asOf` limits to what could be known then; `latest` gives each instrument's newest known value."""
        if not args.get("versionId") and not args.get("name"):
            raise ContractError("invalid_input", t("Choose a name or versionId from list_library."))
        if args.get("latest") and args.get("asOf") is None:
            raise ContractError("invalid_input", t("Give asOf in epoch milliseconds to read the latest values known at that moment."))
        version = args.get("versionId") or self.resolve_values(args["name"], args.get("version"))["versionId"]
        read = self.store.version(version)
        if read["archived_at"] is not None:
            raise ContractError("not_found", t("This library version was archived; its values were dropped."))
        async with self.reading:
            if args.get("latest"):
                rows = await asyncio.to_thread(self.store.latest_available, version, args["asOf"], field=args.get("field"))
            else:
                rows = await asyncio.to_thread(self.store.read_values, version, field=args.get("field"), instrument=args.get("instrument"), start=args.get("start"),
                                               end=args.get("end"), as_of=args.get("asOf"))
        offset = args.get("offset", 0)
        limit = args.get("limit", PAGE)
        return {"versionId": version, "state": read["state"], "version": read["version"], "stoppedAt": read["stopped_at"], "producedByRun": read["run_id"], "publishedAt": read["created_at"], "rows": rows[offset:offset + limit], "total": len(rows),
                "nextOffset": offset + limit if offset + limit < len(rows) else None}

    async def export_values(self, args):
        """The same rows as a parquet file the platform hands to the caller's code."""
        read = await self.values({**args, "limit": 10 ** 9})
        directory = self.root / "exports"
        directory.mkdir(exist_ok=True)
        identity = uuid4().hex
        path = directory / (identity + ".parquet")
        # Each row carries its name beside the exact instrument id (the version's own names, as the Values tab shows them), so code need not map them
        names = {item["instrumentId"]: item.get("displaySymbol") or item.get("column") for item in (self.store.version(read["versionId"])["meta"].get("info") or {}).get("instruments", [])}
        table = pd.DataFrame(read["rows"], columns=["datetime", "instrument", "field", "value", "availableAt", "recordedAt", "mode"])
        table.insert(2, "name", table["instrument"].map(lambda instrument: names.get(instrument, instrument)))
        await asyncio.to_thread(table.to_parquet, path, index=False)
        reference = await self.ctx.hand_over(path, "library-values.parquet", move=True)
        return {"values": reference, "versionId": read["versionId"], "version": read["version"], "state": read["state"], "total": read["total"],
                "producedByRun": read["producedByRun"], "publishedAt": read["publishedAt"], "note": self.read_note(read, "version" in args or "versionId" in args)}

    @staticmethod
    def read_note(read, chosen):
        """The line a notebook prints first: which version it got, and, for a retired one, when it stopped and where its values end."""
        def day(stamp):
            moment = datetime.fromtimestamp(stamp / 1000, UTC)
            return f"{moment:%b} {moment.day}"
        if read["state"] == "stopped":
            ends = max((row[0] for row in read["rows"]), default=None)
            return f"v{read['version']} is retired (stopped {day(read['stoppedAt'])})" + (f"; values end {day(ends)}" if ends is not None else "")
        return None if chosen else f"v{read['version']}, the newest live version"

    async def publish_values(self, args):
        """One-off data: rows of datetime, instrument, field and value, kept as they are and never updated."""
        rows = args["rows"]
        recorded = int(time.time() * 1000)
        version_id = "ver-" + uuid4().hex[:12]
        frame = pd.DataFrame(rows)
        if frame.empty or set(frame.columns) != {"datetime", "instrument", "field", "value"}:
            raise ContractError("invalid_input", t("Give rows with datetime (milliseconds), instrument, field and value."))
        origin = args.get("origin") or {}
        table = pd.DataFrame({"instrument": frame["instrument"].astype(str), "field": frame["field"].astype(str), "datetime": frame["datetime"].astype("int64"),
                              "value": frame["value"].astype(float), "availableAt": frame["datetime"].astype("int64"), "recordedAt": recorded, "mode": "history"})
        spec = {"name": args["name"], "kind": "data", "versionId": version_id, "state": "fixed", "auto": False,
                "meta": {"fields": sorted(set(table["field"])), "timeframe": None, "oneOff": True, "environment": None, "instrumentCount": int(table["instrument"].nunique()),
                         "origin": {"objectId": origin.get("objectId"), "runId": None, "objectName": origin.get("objectName"), "objectKind": origin.get("objectKind", "notebook")},
                         "settingsText": args.get("description") or "Put in from a notebook; it does not update.", "measure": None},
                "rows": table, "objectId": origin.get("objectId"), "runId": None, "workspaceId": self.ctx.workspace(),
                "cursor": int(table["datetime"].max()), "checks": {}}
        created = (await asyncio.to_thread(self.store.commit_versions, [spec]))[0]
        self.publish_change(created["itemId"], version_id)
        return created

    # --- use, stop, archive ----------------------------------------------------------------------------------------------------
    def acquire(self, version_id, owner_id, workspace_id=None, *, kind=None, name=None, how=None):
        """Something now relies on this version (a model, a strategy): it shows among the users."""
        self.store.acquire(version_id, owner_id, workspace_id, kind=kind, name=name, how=how)
        self.publish_change(self.store.version(version_id)["item_id"], version_id)

    def release(self, version_id, owner_id):
        self.store.release(version_id, owner_id)
        self.publish_change(self.store.version(version_id)["item_id"], version_id)

    def sync_strategy_users(self, target):
        from .changes import library_pins
        versions = library_inputs.saved_versions(target["settings"])
        with library_pins(self, target):
            self.pin("qobject:" + target["id"], target["workspaceId"], kind="strategy", name=target["name"],
                     how="research, pinned", version_ids=list(versions.values()))

    def reconcile_strategy_users(self, records):
        """The catalog and objects have separate databases. Restore missing or stale catalog entries from the saved objects on startup."""
        held = {}
        with self.store.connect() as db:
            for row in db.execute("SELECT owner_id, version_id FROM occupancy WHERE kind='strategy' AND owner_id LIKE 'qobject:%'"):
                held.setdefault(row["owner_id"], set()).add(row["version_id"])
        for obj in records:
            if obj["kind"] == "strategy" and not obj.get("deleted"):
                current = held.pop("qobject:" + obj["id"], set())
                if current != set(library_inputs.saved_versions(obj["settings"]).values()):
                    self.sync_strategy_users(obj)
        for owner in held:
            self.pin(owner, None, kind="strategy", name=None, how=None, version_ids=[])

    def pin(self, owner_id, workspace_id, *, kind, name, how, version_ids):
        """A research object's library versions, all at once: those it no longer names are released."""
        held = self.store.held_by(owner_id)
        for version_id in held - set(version_ids):
            self.release(version_id, owner_id)
        for version_id in set(version_ids):
            with contextlib.suppress(ContractError):
                self.acquire(version_id, owner_id, workspace_id, kind=kind, name=name, how=how)

    def users(self, version_id):
        number = self.store.version_number(version_id)
        return [self.user_view(user, number) for user in self.store.users(version_id)]

    def stop(self, version_id, *, dry_run=False):
        """Stop producing a version. With `dry_run` only what would happen: who uses it and what each of them will see."""
        version = self.store.version(version_id)
        users = self.users(version_id)
        others = [item for item in self.store.item_versions(version["item_id"]) if item["versionId"] != version_id and item["state"] == "updating" and item["archivedAt"] is None]
        result = {"versionId": version_id, "version": version["version"], "users": users,
                  "producing": [item["version"] for item in others], "lastDatetime": version["cursor"],
                  "consequence": (f"{', '.join(user['name'] for user in users)} keep v{version['version']}; its values end "
                                  f"{day_text(version['cursor'])} and they are told" + (f" that v{others[0]['version']} is producing" if others else "") + ".") if users else
                                 f"No values after {day_text(version['cursor'])}." if version["cursor"] else "It stops."}
        if dry_run:
            return result
        self.store.stop(version_id)
        self.publish_change(version["item_id"], version_id)
        for user in users:
            self.records.put("qlibevent", {"id": f"library_version_stopped:{version_id}:{user['ownerId']}", "type": "library_version_stopped", "workspaceId": None,
                                           "itemId": version["item_id"], "versionId": version_id, "owner": user["ownerId"],
                                           "newer": [item["versionId"] for item in others]})
        return {**result, "stopped": True}

    def resume(self, version_id):
        version = self.store.version(version_id)
        self.store.resume(version_id)
        self.publish_change(version["item_id"], version_id)
        return self.read_version(version_id)

    def set_auto(self, version_id, on):
        version = self.store.version(version_id)
        self.store.set_auto(version_id, on)
        self.publish_change(version["item_id"], version_id)
        return self.read_version(version_id)

    def retry(self, version_id):
        if self.loop is None:
            raise ContractError("unavailable", t("The library's production is not running."))
        result = self.loop.retry_now(version_id)
        self.publish_change(self.store.version(version_id)["item_id"], version_id)
        return result

    def archivable(self, version_id):
        """The version when it may be archived (stopped, not archived, nothing uses it), with the same refusals the archive gives."""
        version = self.store.version(version_id)
        if version["archived_at"] is not None:
            raise ContractError("conflict", t("This version is archived already."))
        if version["state"] != "delisted":
            raise ContractError("conflict", t("Stop this version first; a stopped version nobody uses can be archived."))
        users = self.store.users(version_id)
        if users:
            raise ContractError("conflict", t("Something still uses this version: {users}.", users=', '.join(row['name'] or row['owner_id'] for row in users)), {"users": users})
        return version

    async def archive(self, version_id):
        """Archive a stopped version nobody uses: the stored values, the window models and the frozen environment go; the record stays."""
        version = self.store.version(version_id)
        dropped = await asyncio.to_thread(self.store.archive, version_id)
        directory = self.version_directory(version_id)
        for name in ("windows", "models", "updates", "days.json", "after.json"):
            target = directory / name
            if target.is_dir():
                shutil.rmtree(target, ignore_errors=True)
            elif target.is_file():
                target.unlink(missing_ok=True)
        environment = dropped["meta"].get("environment")
        if environment is not None:
            with contextlib.suppress(ContractError):
                self.ctx.environments.release(environment["preparationId"], "library:" + version_id)
        self.publish_change(version["item_id"], version_id)
        return {"versionId": version_id, "rows": dropped["rows"], "archived": True}

    async def close(self):
        """Values handed over stay in the hand-over folder for 24 hours; nothing to let go."""


def _clock_text(milliseconds):
    """"Nov 4, 16:30 UTC" for any reader; the page shows it in the viewer's own time."""
    if not milliseconds:
        return "—"
    moment = time.gmtime(milliseconds / 1000)
    return f"{time.strftime('%b', moment)} {moment.tm_mday}, {time.strftime('%H:%M', moment)} UTC"


def _day_text(milliseconds):
    """A data day (UTC): "Nov 3"."""
    day = time.gmtime(milliseconds / 1000)
    return f"{time.strftime('%b', day)} {day.tm_mday}"


def _date_text(milliseconds):
    """A data day with its year: "Nov 3, 2026"."""
    day = time.gmtime(milliseconds / 1000)
    return f"{time.strftime('%b', day)} {day.tm_mday}, {day.tm_year}"


def _signed(value):
    """"+0.0153" or "−0.0094", as the page writes a signed figure."""
    return "—" if value is None else f"{value:+.4f}".replace("-", "−")


def _duration(milliseconds):
    """"8 min", "1 h 30 min", "2 d 3 h" (library.js dur)."""
    minutes = max(1, round(milliseconds / 60_000))
    if minutes < 60:
        return f"{minutes} min"
    if minutes < 1440:
        return f"{minutes // 60} h {minutes % 60:02d} min"
    return f"{minutes // 1440} d {minutes % 1440 // 60} h"


def _number(value):
    return "—" if value is None else f"{value:.4f}"


def _percent(value):
    return "—" if value is None else f"{value * 100:.1f}%"
