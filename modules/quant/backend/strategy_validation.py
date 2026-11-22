"""Looking at the held-out part: the one run that reads the period every other run and check leaves out. It can be asked for more than once, but
the first look is the only untouched one, so the object keeps a record of every look (`heldOutUsed`: the run looked at, its code and values) and
says so before the next; a copy of the object carries the record along."""
from __future__ import annotations

import time
from datetime import date

from talyx_sdk import ContractError
from talyx_sdk.i18n import t

from .object_store import OBJECT

SHORT_DAYS = 60


class Validation:
    def __init__(self, runs, store):
        self.runs, self.store = runs, store

    def source(self, run_id):
        run = self.runs.read(run_id)
        if run["objectKind"] != "strategy":
            raise ContractError("invalid_input", t("Only a strategy run is validated on the held-out part."))
        return run

    async def status(self, run_id):
        """What the confirmation says: the days the held-out part covers (and how many trading days that is), and whether it was looked at before and with
        the same code and values."""
        run = self.source(run_id)
        obj = self.store.read(run["objectId"])
        data = run["settings"]["data"]
        held = data.get("heldOutFrom")
        if not held:
            raise ContractError("no_held_out_period", t("Set where the held-out part starts in the run settings first."))
        last = data["until"]
        if last == "latest":
            raise ContractError("unavailable", t("This run did not retain its resolved held-out end date."))
        calendar = (date.fromisoformat(last) - date.fromisoformat(held)).days + 1
        work = self.runs.directory(run["id"]) / "output" / "strategy" / "work.json"
        year = 365
        if work.is_file():
            import json
            year = json.loads(work.read_text(encoding="utf-8")).get("annualization") or 365
        days = max(0, round(calendar * year / 365))
        used = obj.get("heldOutUsed") or []
        digest = (run.get("code") or {}).get("digest")
        same = any(item["codeDigest"] == digest and item["params"] == run["params"] for item in used)
        latest = used[-1] if used else None
        number = None
        if latest:
            number = self.runs.records.find("qrun", latest["runId"])
            number = (number or {}).get("number")
        return {"runId": run_id, "number": run.get("number"), "from": held, "to": last, "days": days, "short": days < SHORT_DAYS,
                "used": {"runId": latest["runId"], "number": number, "same": same} if latest else None}

    async def held_out(self, run_id):
        """Run this run's code and values on the held-out part, tagged, and remember the look."""
        run = self.source(run_id)
        previous = self.runs.records.held_out_for(run_id)
        if previous is not None:
            return previous
        started = await self.runs.start(run["objectId"], {"kind": "heldout", "of": run_id})
        obj = self.store.read(run["objectId"])
        look = {"runId": started["id"], "of": run_id, "codeDigest": (run.get("code") or {}).get("digest"), "params": run["params"], "at": int(time.time() * 1000)}
        self.runs.records.put(OBJECT, {**obj, "heldOutUsed": [*(obj.get("heldOutUsed") or []), look]})
        return started
