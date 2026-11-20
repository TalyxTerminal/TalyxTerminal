"""Quant's notifications: sent straight to the platform's toast and the user's channels, retried a few times, kept as records.

What Quant can tell is declared here once (`DECLARED`): each is a setting of Quant (``entry`` declares them), so the
settings window lists them and a workspace can have its own."""
from __future__ import annotations

import time

from talyx_sdk import ContractError

DECLARED = (
    {"id": "longRuns", "group": "Runs", "title": "Long runs finished", "description": "A run, sweep, held-out validation or publication that took over a minute finished or failed."},
    {"id": "libraryFailures", "group": "Library", "title": "Library failures", "description": "A published version's code failed on the new data."},
    {"id": "libraryMissing", "group": "Library", "title": "Library missing periods", "description": "The source gave no bar, or an input had no value, for a version's newest period."},
    {"id": "libraryWeakening", "group": "Library", "title": "Library weakening", "description": "A factor or model fell under a quarter of its published figure or flipped sign; an allocation drifted from its published risk."},
    {"id": "libraryRecovered", "group": "Library", "title": "Library recovered", "description": "A version that failed or missed periods is producing again."},
)
DEFAULTS = {item["id"]: True for item in DECLARED}
LONG_RUN_MS = 60_000
# Wait after each failed send; one more failure than there are waits gives the notice up.
RETRY_AFTER = (5_000, 30_000, 120_000)
NOTICE = "qnotice"


def now():
    return int(time.time() * 1000)


class Notifications:
    def __init__(self, ctx, records):
        self.ctx, self.records = ctx, records

    async def settings(self):
        """The switches as they apply to the workspace being served (global for work of no workspace)."""
        from .runtime import WORKSPACE
        values = await self.ctx.call("platform.settings.read", {"module": "quant", "keys": list(DEFAULTS),
                                                                 "workspaceId": WORKSPACE.get() or "global"})
        return {key: values["values"][key]["value"] for key in DEFAULTS}

    async def deliver(self, category, identity, title, body):
        """Send once for this identity: a repeated identity is the same notice and is not sent again."""
        if not (await self.settings())[category] or self.records.find(NOTICE, identity) is not None:
            return
        notice = self.records.put(NOTICE, {"id": identity, "title": title, "body": body, "toasted": False, "channelIds": None, "attempts": 0,
                                           "status": "sending", "nextAt": None, "error": None, "createdAt": now(), "category": category})
        await self.attempt(notice)

    async def long_run(self, identity, title, body, seconds):
        """A run that took over a minute: told when it ends, once."""
        if seconds is not None and seconds * 1000 >= LONG_RUN_MS:
            try:
                await self.deliver("longRuns", f"run-done:{identity}", title, body)
            except ContractError:
                return

    async def attempt(self, notice):
        # Each step that succeeded is remembered, so a retry repeats only what failed; the channels take the same notificationId.
        try:
            if not notice["toasted"]:
                await self.ctx.call("platform.notifications.toast", {"title": notice["title"], "message": notice["body"]})
                notice = self.records.put(NOTICE, {**notice, "toasted": True})
            if notice["channelIds"] is None:
                channels = await self.ctx.call("platform.notifications.channels", {})
                notice = self.records.put(NOTICE, {**notice, "channelIds": [item["id"] for item in channels if item["enabled"] and item["configured"]]})
            if notice["channelIds"]:
                await self.ctx.call("platform.notifications.send", {"notificationId": notice["id"], "channelIds": notice["channelIds"], "title": notice["title"], "body": notice["body"]})
        except ContractError as error:
            attempts = notice["attempts"] + 1
            failed = attempts > len(RETRY_AFTER)
            self.records.put(NOTICE, {**notice, "attempts": attempts, "error": error.message, "status": "failed" if failed else "retrying",
                                      "nextAt": None if failed else now() + RETRY_AFTER[attempts - 1]})
            return
        self.records.put(NOTICE, {**notice, "status": "sent", "nextAt": None, "error": None})

    def resume(self):
        """A notice the application stopped while sending is sent again at once; the channels take the same id and send it once."""
        for notice in self.records.list(NOTICE):
            if notice["status"] == "sending":
                self.records.put(NOTICE, {**notice, "status": "retrying", "nextAt": now()})

    async def resend(self):
        for notice in self.records.list(NOTICE):
            if notice["status"] == "retrying" and notice["nextAt"] <= now():
                await self.attempt(notice)
