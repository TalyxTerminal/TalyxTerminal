"""What the library list, the home page and the notifications read of an item: one state, computed in one place from its versions.

A version is on time, late (today), failed, missing, upstream missing (an input had no value), risk drift (an allocation whose realized
volatility or risk shares moved away from publication), weakening (60 settled periods after publication, the same sign as published but
under a quarter of it, or the opposite sign), no period yet, auto off, one-off or stopped; the item takes its worst version's state, and problems sort first. Figures after
publication count settled periods only; until they span 60 trading days (days with a settled period, whatever the bar) they are still collecting.
"""
from __future__ import annotations

from talyx_sdk.i18n import mark, t

MIN_AFTER = 60  # trading days with a settled period (`settledDays` of the figures after publication)
RANK = {"fail": 0, "miss": 1, "up": 2, "drift": 3, "weak": 4, "late": 5, "ok": 6, "first": 7, "auto_off": 8, "one_off": 9, "stopped": 10}
ATTENTION = ("fail", "miss", "up", "drift", "weak", "late")
FIX = ("fail", "miss", "up")
LABEL = {"fail": mark("Failed"), "miss": mark("Missing"), "up": mark("Upstream missing"), "drift": mark("Risk drift"), "weak": mark("Weaker"), "late": mark("Late"), "ok": mark("On time"), "first": mark("No period yet"),
         "auto_off": mark("Auto off"), "one_off": mark("One-off"), "stopped": mark("Stopped")}
FREQ = {"d": "Daily", "h": "Hourly", "m": "Intraday", "w": "Weekly"}
REBALANCED = {"week": "Weekly", "month": "Monthly", "quarter": "Quarterly"}  # an allocation's new weights come at its rebalance, not with each bar
DAY = 86_400_000
# A failure's code when the version was published before a change it cannot be produced under: a retry fails the same way, so none is offered or scheduled;
# publishing again from a new run mends it.
REPUBLISH = "republish"


def freq_of(timeframe):
    if not timeframe:
        return "One-off"
    if timeframe == "1d":
        return "Daily"
    return FREQ.get(timeframe[-1], timeframe)


def run_of(periods, result):
    """How many of the newest periods in a row have this result."""
    count = 0
    for period in reversed(periods):
        if period["result"] != result:
            break
        count += 1
    return count


def collecting(after):
    """Whether a signal's figures after publication are still collecting: their settled periods span fewer than 60 trading days."""
    return after.get("kind") != "allocation" and (after.get("settledDays") or 0) < MIN_AFTER


def weak(published, after):
    if not published or not after or published.get("value") is None or after.get("value") is None or after.get("kind") == "allocation":
        return False
    if collecting(after):
        return False
    pub, now = published["value"], after["value"]
    return pub != 0 and (pub * now < 0 or abs(now) < abs(pub) / 4)


def weak_how(published, after):
    """Why a version is weaker, in the words its issue and notification use."""
    return "opposite sign" if published * after < 0 else "same sign, under a quarter"


def version_state(version, periods, now):
    """One version's state and, for a streak, how many periods it has lasted."""
    if version["archived_at"] is not None or version["state"] == "delisted":
        return "stopped", 0
    if version["state"] == "fixed":
        return "one_off", 0
    if version["state"] == "expired":
        return "miss", max(version["misses"], run_of(periods, "miss"))
    if not version["auto"]:
        return "auto_off", 0
    last = periods[-1] if periods else None
    if last is not None:
        for result in ("fail", "miss", "up"):
            if last["result"] == result:
                return result, run_of(periods, result)
    if version["state"] == "failed":
        return "fail", max(1, run_of(periods, "fail"))
    after = version["after"] or {}
    if after.get("drift"):
        return "drift", 0
    if weak(published_of(version), after):
        return "weak", 0
    if last is not None and last["result"] == "late" and last["ready_at"] and now - last["ready_at"] < DAY:
        return "late", 0
    if last is None:   # published, nothing produced since: not yet on time
        return "first", 0
    return "ok", 0


def published_of(version):
    """The publication's main figure, from the run it was published from."""
    published = version["meta"].get("published") or {}
    return published if "value" in published else None


def reviewable(meta, period):
    """An allocation's period waits for the monthly review only when the library produced it after publication: the published period is the run's own,
    and set beside the run it would differ by nothing (the review came up at publication, for a period two years back)."""
    through = meta.get("publishedThrough")
    return period is not None and (through is None or int(period) > int(through))


def republish(version):
    """Whether the version failed in a way only publishing again mends (no retry is offered for it)."""
    return version["failure"] is not None and version["failure"]["code"] == REPUBLISH


def public_state(version):
    if version["archived_at"] is not None:
        return "archived"
    return {"delisted": "stopped", "fixed": "fixed"}.get(version["state"], "producing")


def item_summary(item, versions, now):
    """The item's row: `versions` is a list of {version, periods (newest 60, oldest first), users, lastDatetime, lastNames (names with a value on it), inputs}."""
    states = []
    for entry in versions:
        version = entry["version"]
        state, count = version_state(version, entry["periods"], now)
        states.append((state, count, entry))
    order = sorted(states, key=lambda found: (RANK[found[0]], -found[2]["version"]["version"]))
    worst = order[0] if order else ("stopped", 0, None)
    producing = [entry for _, _, entry in states if public_state(entry["version"]) == "producing"]
    if worst[0] in ATTENTION:
        shown = worst[2]
    else:
        shown = next((entry for entry in producing if entry["periods"]), producing[0] if producing else versions[0] if versions else None)
    users = {}
    for _, _, entry in states:
        for user in entry["users"]:
            users[user["owner_id"]] = {**user, "version": entry["version"]["version"]}
    out = {"itemId": item["id"], "name": item["name"], "kind": item["kind"], "createdAt": item["created_at"], "updatedAt": now,
           "state": worst[0], "stateLabel": LABEL[worst[0]], "stateCount": worst[1], "attention": worst[0] in ATTENTION,
           "versions": [{"versionId": entry["version"]["id"], "version": entry["version"]["version"], "state": public_state(entry["version"]),
                         "health": state, "count": count, "auto": entry["version"]["auto"], "users": len(entry["users"]),
                         "publishedAt": entry["version"]["created_at"], "stoppedAt": entry["version"]["stopped_at"], "archivedAt": entry["version"]["archived_at"]}
                        for state, count, entry in sorted(states, key=lambda found: -found[2]["version"]["version"])],
           "producing": [entry["version"]["version"] for entry in producing], "users": len(users),
           "usersList": [{"name": user["name"], "kind": user["kind"], "version": user["version"]} for user in users.values()]}
    if shown is None:
        return {**out, "shownVersion": None, "shownVersionId": None, "last20": [], "latestPeriod": None, "latestNames": None}
    version = shown["version"]
    meta = version["meta"]
    published = published_of(version)
    after = version["after"] or {}
    periods = shown["periods"]
    produced = [period for period in periods if period["result"] in ("ok", "late")]
    latest = produced[-1]["label"] if produced else shown["lastDatetime"]
    out.update({
        "shownVersion": version["version"], "shownVersionId": version["id"], "freq": REBALANCED[meta["rebalance"]["every"]] if item["kind"] == "allocation" else freq_of(meta.get("timeframe")), "measure": meta.get("measure"),
        "universe": meta.get("universe"), "auto": version["auto"], "latestPeriod": latest, "latestNames": shown["lastNames"],
        "last20": [{"period": period["label"], "result": period["result"], "readyAt": period["ready_at"], "dueAt": period["due_at"], "why": period["why"]}
                   for period in periods[-20:]],
        "published": None if published is None else published["value"],
        "after": after.get("value") if not collecting(after) else None,
        "collecting": collecting(after) and not meta.get("oneOff"), "collectingNeed": MIN_AFTER,
        "settled": after.get("settled", 0), "settledDays": after.get("settledDays", 0), "pending": after.get("pending", 0),
        "origin": meta.get("origin"), "oneOff": bool(meta.get("oneOff")),
        "inputs": [{"name": entry["name"], "version": entry["version"], "versionId": entry["versionId"], "state": public_state({"archived_at": None, "state": entry["state"]})
                    if entry["state"] != "archived" else "archived"} for entry in shown.get("inputs", [])],
        "nextDue": (version.get("pending") or {}).get("nextAt"),
        "republish": republish(version),
        "expired": version["state"] == "expired",   # five missing periods stopped it: checking the source now cannot restart it
        # An allocation's newest period waits for the monthly review until it is accepted or adjusted.
        "review": {"period": latest, "versionId": version["id"], "done": str(latest) in (meta.get("reviews") or {}), "objectId": (meta.get("origin") or {}).get("objectId")}
                  if item["kind"] == "allocation" and reviewable(meta, latest) and public_state(version) == "producing" else None,
    })
    return out


def ordered(rows):
    """Problems first, then the newest published."""
    return sorted(rows, key=lambda row: (RANK.get(row["state"], 9), -max((item["publishedAt"] for item in row["versions"]), default=0)))


def fix_action(row):
    """The one action of a row to fix: retry, check the source, open the input; a version that failed in a way only publishing again mends opens the object
    it was published from (or, with that gone, the library item), since a retry can only fail again."""
    if row["state"] == "fail" and row["republish"]:
        origin = row["origin"]
        if origin is not None and origin["objectId"]:
            return {"id": "republish", "label": t("Open the {kind}", kind=row['kind']), "objectId": origin["objectId"]}
        return {"id": "republish", "label": t("Open in the library")}
    if row["state"] == "miss" and row.get("expired"):
        return {"id": "open_version", "label": t("Open in the library")}
    return {"id": {"fail": "retry", "miss": "check_source", "up": "open_input"}[row["state"]],
            "label": {"fail": "Retry now", "miss": "Check the source now", "up": "Open the input"}[row["state"]]}


def attention(rows):
    """The library's part of the home page's To handle: failed, missing and upstream missing (an upstream that failed itself is listed alone);
    weakening, risk drift and late only count toward the link to the library."""
    failing = {row["name"] for row in rows if row["state"] in FIX}
    out, watch = [], 0
    for row in rows:
        if row["state"] in ("weak", "drift", "late"):
            watch += 1
            continue
        if row["state"] not in FIX:
            continue
        last = row["last20"][-1] if row.get("last20") else None
        if row["state"] == "up" and any(entry["name"] in failing for entry in row.get("inputs", [])):
            continue
        out.append({"tone": "fix", "label": LABEL[row["state"]], "state": row["state"], "count": row["stateCount"],
                    "subject": {"kind": "library", "id": row["itemId"], "name": row["name"], "version": row["shownVersion"], "versionId": row.get("shownVersionId"),
                                "itemKind": row["kind"]},
                    "why": (last or {}).get("why") or {"fail": "The last update failed", "miss": "The source gave no new bar", "up": "An input has no value"}[row["state"]],
                    "since": (last or {}).get("dueAt") or row["updatedAt"], "action": fix_action(row)})
    for row in rows:
        review = row.get("review")
        if review and not review["done"] and row["state"] not in FIX:
            out.append({"tone": "fix", "label": t("Review this period"), "state": "review", "count": 1,
                        "subject": {"kind": "library", "id": row["itemId"], "name": row["name"], "version": row["shownVersion"], "versionId": review["versionId"], "itemKind": row["kind"]},
                        "why": f"The newest period of {row['name']} waits for your review", "since": row["updatedAt"],
                        # the review is done in the allocation's Work, beside the trial of that period
                        "action": {"id": "review_period", "period": review["period"], "label": t("Review this period"), **({"objectId": review["objectId"]} if review.get("objectId") else {})}})
    return out, watch
