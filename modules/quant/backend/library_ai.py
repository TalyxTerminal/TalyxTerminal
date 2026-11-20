"""What an AI caller reads of the library: the same reads, each cut to the Host's AI response budget and keeping what the AI acts on.

Names stand in for instrument objects and ids; the published and after-publication figures, states, versions, users and inputs stay; chart figures
go. A long list keeps its most relevant entries (problems first, the newest, the largest) with how many there are and how to read the rest (the
paginated reads). The page in the app reads the full answers (`library.py`). On realistic data (70 names) every full read was 30 to 530 KB and the
Agent could read nothing of the library: "Response exceeds AI budget"."""
from __future__ import annotations

from collections import Counter

from .ai_views import BUDGET, size

CAPS = (20, 12, 8, 5, 3)  # how many entries a long list keeps: the most that fits
VALUE_ROWS = 100  # values a page when the AI does not say how many


def fit(make):
    """The answer `make(cap)` gives with the longest lists that still fit the budget."""
    for cap in CAPS:
        out = make(cap)
        if size(out) <= BUDGET:
            return out
    return out


def text(value, limit):
    """A long text cut to its end (a traceback's last lines say what broke)."""
    if not isinstance(value, str) or len(value) <= limit:
        return value
    return "…" + value[-limit:]


def head(value, limit):
    if not isinstance(value, str) or len(value) <= limit:
        return value
    return value[:limit] + "…"


def rounded(value, digits=6):
    return round(value, digits) if isinstance(value, float) else value


# --- names -------------------------------------------------------------------------------------------------------------------
def symbol_of(entry):
    """The name of an instrument as the user reads it, from an instrument object, a data-settings symbol or a pool row."""
    if not isinstance(entry, dict):
        return str(entry)
    inner = entry.get("instrument") if isinstance(entry.get("instrument"), dict) else entry
    return inner.get("displaySymbol") or inner.get("ticker") or inner.get("symbol") or inner.get("column") or inner.get("name") or inner.get("instrumentId")


def names_of(meta):
    """Instrument id -> its name, for the ids a version's figures and values are keyed by."""
    out = {}
    for entry in ((meta or {}).get("info") or {}).get("instruments") or []:
        out[entry.get("instrumentId")] = entry.get("displaySymbol") or entry.get("column") or entry.get("instrumentId")
    for entry in ((meta or {}).get("allocation") or {}).get("pool") or []:
        out.setdefault(entry.get("instrumentId"), entry.get("ticker") or entry.get("name"))
    return out


def listed(names, cap):
    """Names: the first `cap` of them and how many there are."""
    names = list(names)
    return {"names": names[:cap], "count": len(names)}


def data_of(data, cap):
    """Data settings with the symbols as names (each symbol is a full instrument object in the stored settings)."""
    if not isinstance(data, dict):
        return data
    out = {key: value for key, value in data.items() if key != "symbols"}
    if isinstance(data.get("symbols"), list):
        out["symbols"] = listed([symbol_of(entry) for entry in data["symbols"]], cap)
    return out


def shorten(value, cap, depth=0):
    """Settings and parameters as the AI reads them: a long list keeps its first entries and its count; a pool of instruments its names."""
    if isinstance(value, dict):
        return {key: shorten(item, cap, depth + 1) for key, item in value.items()} if depth < 4 else value
    if isinstance(value, list):
        if value and all(isinstance(item, dict) and ("instrumentId" in item or "instrument" in item) for item in value):
            groups = Counter(item.get("group") for item in value if item.get("group"))
            return {**listed([symbol_of(item) for item in value], cap), **({"groups": dict(groups)} if groups else {})}
        if len(value) > cap:
            return {"first": [shorten(item, cap, depth + 1) for item in value[:cap]], "count": len(value)}
        return [shorten(item, cap, depth + 1) for item in value]
    if isinstance(value, str):
        return head(value, 400)
    return value


def shares_of(shares, names, cap):
    """Risk shares keyed by instrument id: the largest by name, and how many assets."""
    if not isinstance(shares, dict) or not shares:
        return shares
    largest = sorted(shares.items(), key=lambda pair: -abs(pair[1] or 0))[:cap]
    return {"largest": {names.get(key, key): rounded(value, 4) for key, value in largest}, "assets": len(shares)}


def figures_of(published, names, cap):
    """Published or after-publication figures, the per-asset parts by name and cut to the largest."""
    if not isinstance(published, dict):
        return published
    out = {key: rounded(value) for key, value in published.items() if key not in ("riskShares", "shares", "worst", "points")}
    if "riskShares" in published:
        out["riskShares"] = shares_of(published["riskShares"], names, cap)
    if isinstance(published.get("shares"), dict):
        then, now = published["shares"].get("published") or {}, published["shares"].get("now") or {}
        moved = sorted(set(then) | set(now), key=lambda key: -abs((now.get(key) or 0) - (then.get(key) or 0)))[:cap]
        # The assets that moved most, and the largest now (the Agent asked for the five largest and got only the movers).
        out["shares"] = {"moved": [{"name": names.get(key, key), "published": rounded(then.get(key), 4), "now": rounded(now.get(key), 4)} for key in moved],
                         "largestNow": shares_of(now, names, cap).get("largest") if now else {}, "assets": len(set(then) | set(now))}
    if isinstance(published.get("worst"), dict):
        out["worst"] = {**published["worst"], "asset": names.get(published["worst"].get("asset"), published["worst"].get("asset"))}
    return out


def users_of(users, cap):
    users = users or []
    out = [{key: user.get(key) for key in ("name", "kind", "how", "version", "objectId", "libraryVersionId", "unattended") if user.get(key) is not None}
           for user in users[:cap]]
    return out if len(users) <= cap else [*out, {"more": len(users) - cap}]


def origin_of(origin):
    return {key: origin.get(key) for key in ("objectId", "objectName", "objectKind", "runId", "runNumber") if origin.get(key) is not None} if isinstance(origin, dict) else origin


def period_of(period):
    out = {key: period.get(key) for key in ("label", "result", "due_at", "ready_at", "names", "tries") if period.get(key) is not None}
    return {**out, "why": head(period["why"], 240)} if period.get("why") else out


def periods_of(periods, cap):
    """Every period counted by result and by why (one reason said once, not on each of sixty periods); the newest `cap` listed."""
    periods = list(periods or [])
    reasons = Counter((period["why"], period["result"]) for period in periods if period.get("why"))
    return {"counts": dict(Counter(period["result"] for period in periods)), "total": len(periods),
            "reasons": [{"why": head(why, 240), "result": result, "periods": count} for (why, result), count in reasons.most_common(cap)],
            "newest": [{key: value for key, value in period_of(period).items() if key != "why"} for period in periods[::-1][:cap]]}


def failures_of(failures, cap):
    """The failed tries: each message and where; the newest one's traceback and output (their ends), the others' traceback shorter."""
    out = []
    for number, failure in enumerate(failures or []):
        limit = max((1500 if number == 0 else 300) * cap // CAPS[0], 120)
        out.append({"at": failure.get("at"), "label": failure.get("label"), "message": head(failure.get("message"), 400), "location": failure.get("location"),
                    "traceback": text(failure.get("traceback"), limit), "output": text(failure.get("output"), max(limit // 4, 60))})
    return out


def inputs_of(entries, cap, keys=("name", "version", "versionId", "state")):
    return [{key: entry.get(key) for key in keys if entry.get(key) is not None} for entry in entries or []][:cap]


# --- the reads -------------------------------------------------------------------------------------------------------------
def item_row(row, cap):
    """One row of the list: its state, versions, figures and the last 20 periods counted by result."""
    keep = ("itemId", "name", "kind", "state", "stateLabel", "stateCount", "attention", "shownVersion", "shownVersionId", "freq", "measure", "universe", "auto",
            "latestPeriod", "published", "after", "collecting", "collectingNeed", "settled", "pending", "users", "oneOff", "nextDue", "review")
    out = {key: rounded(row.get(key)) for key in keep if row.get(key) is not None}
    out["versions"] = [{key: version.get(key) for key in ("versionId", "version", "state", "health", "count", "auto", "users")} for version in row.get("versions") or []][:cap]
    last = row.get("last20") or []
    if last:
        newest = last[-1]
        out["last20"] = dict(Counter(period["result"] for period in last))
        out["newest"] = {"period": newest.get("period"), "result": newest.get("result"), **({"why": head(newest["why"], 200)} if newest.get("why") else {})}
    if row.get("inputs"):
        out["inputs"] = inputs_of(row["inputs"], cap)
    if row.get("origin"):
        out["origin"] = origin_of(row["origin"])
    return out


def list_library(library, args):
    full = library.list(args)

    def make(cap):
        items = full["items"]
        shown = min(len(items), cap * 2)
        production = full.get("production") or {}
        out = {"items": [item_row(row, cap) for row in items[:shown]], "count": len(items), "needsAttention": full["needsAttention"],
               "archivable": full["archivable"][:cap], "nextRound": full.get("nextRound"),
               "production": {key: production.get(key) for key in ("lastRound", "next", "producing", "concurrency")}
               | {key: (production.get(key) or [])[:cap] for key in ("running", "queued", "retries", "waiting")}}
        if shown < len(items):
            out["more"] = {"count": len(items) - shown, "read": "list_library with query (a name), kind or state for the rest; problems come first"}
        return out
    return fit(make)


def version_of(full, names, cap):
    skip = ("data", "parameters", "settings", "published", "after", "occupiedBy", "inputs", "notes", "formula", "origin", "failure")
    out = {key: value for key, value in full.items() if key not in skip}
    out.update(data=data_of(full.get("data"), cap), parameters=shorten(full.get("parameters"), cap), settings=shorten(full.get("settings"), cap),
               published=figures_of(full.get("published"), names, cap), after=figures_of(full.get("after"), names, cap),
               inputs=inputs_of(full.get("inputs"), cap, ("name", "version", "versionId", "parameter")), notes=(full.get("notes") or [])[:cap],
               formula=head(full.get("formula"), 2000), origin=origin_of(full.get("origin")))
    if isinstance(full.get("occupiedBy"), list):
        out["occupiedBy"] = users_of(full["occupiedBy"], cap)
    elif "occupiedBy" in full:
        out["occupiedBy"] = full["occupiedBy"]
    failure = full.get("failure")
    out["failure"] = {key: (text(value, 600) if isinstance(value, str) else value) for key, value in failure.items()} if isinstance(failure, dict) else failure
    return out


def read_version(library, version_id):
    full = library.read_version(version_id)
    names = names_of(library.store.version(version_id)["meta"])
    return fit(lambda cap: version_of(full, names, cap))


def detail(library, item_id, version_id=None):
    full = library.detail(item_id, version_id, figures=False)
    names = names_of(library.store.version(full["versionId"])["meta"])

    def version_row(entry, cap):
        keep = ("versionId", "version", "state", "health", "count", "auto", "publishedAt", "stoppedAt", "archivedAt", "runId", "objectId", "settingsText", "measure",
                "collecting", "settled", "pending", "cursor", "lastDatetime", "rows", "oneOff")
        out = {key: entry.get(key) for key in keep if entry.get(key) is not None}
        out.update(origin=origin_of(entry.get("origin")), pub=figures_of(entry.get("pub"), names, cap), after=figures_of(entry.get("after"), names, cap),
                   users=users_of(entry.get("users"), cap), inputs=inputs_of(entry.get("inputs"), cap))
        if entry.get("failure"):
            out["failure"] = head((entry["failure"] or {}).get("message"), 400)
        return out

    def make(cap):
        versions = full["versions"]
        out = {"item": item_row(full["summary"], cap), "versionId": full["versionId"], "issue": full.get("issue"),
               "versions": [version_row(entry, cap) for entry in versions[:cap]], "periods": periods_of(full.get("periods"), cap),
               "feeds": [{key: feed.get(key) for key in ("itemId", "name", "kind", "versionId", "version", "reads", "state")} | {"users": users_of(feed.get("users"), cap)}
                         for feed in (full.get("feeds") or [])[:cap]],
               "inputs": inputs_of(full.get("inputs"), cap, ("name", "version", "versionId", "state", "health", "cursor")), "nextDue": full.get("nextDue"),
               "failures": failures_of((full.get("failures") or [])[:2], cap), "failureCount": len(full.get("failures") or []),
               "files": listed(full.get("files") or [], cap), "users": users_of(full.get("users"), cap), "oneOff": full.get("oneOff"), "origin": origin_of(full.get("origin")),
               "read": "read_library_item with versionId for a version's settings and figures, read_library_log for every period and failed try, read_library_values with date for one day's values"}
        if len(versions) > cap:
            out["moreVersions"] = len(versions) - cap
        return out
    return fit(make)


def log(library, version_id, offset=0, limit=None):
    full = library.log(version_id)
    periods = full["periods"]

    def make(cap):
        page = periods[offset:offset + min(limit or cap * 2, cap * 3)]
        end = offset + len(page)
        out = {"counts": dict(Counter(period["result"] for period in periods)), "total": len(periods), "offset": offset,
               "periods": [period_of(period) for period in page], "nextOffset": end if end < len(periods) else None,
               "failures": failures_of(full.get("failures"), cap),
               "updates": [{key: update.get(key) for key in ("at", "version", "outcome", "values", "firstDatetime", "lastDatetime", "mode") if update.get(key) is not None}
                           | ({"message": head(update["message"], 240)} if update.get("message") else {}) for update in (full.get("updates") or [])[:cap]]}
        if out["nextOffset"] is not None:
            out["read"] = "read_library_log with offset for older periods (newest first)"
        return out
    return fit(make)


def values_day(library, version_id, **args):
    full = library.values_page(version_id, **args)

    def largest(rows, cap):
        rows = sorted(rows, key=lambda row: -abs(row["weight"]))
        return {"largest": {row["name"]: rounded(row["weight"], 4) for row in rows[:cap]}, "assets": len(rows)}

    def make(cap):
        out = {key: value for key, value in full.items() if key not in ("rows", "weights")}
        out["rows"] = [{key: rounded(value) for key, value in row.items() if key != "instrumentId" and value is not None} for row in full.get("rows") or []]
        if full.get("weights"):
            weights = full["weights"]
            out["weights"] = {"published": largest(weights.get("published") or [], cap), "thisPeriod": largest(weights.get("thisPeriod") or [], cap),
                              "publishedDate": weights.get("publishedDate")}
        return out
    return fit(make)


async def values(library, args):
    """Rows of one version as the AI reads them: the name for the instrument id; as many as fit, with the offset the rest starts at."""
    offset = args.get("offset", 0)
    full = await library.values({**args, "offset": offset, "limit": args.get("limit", VALUE_ROWS)})
    names = names_of(library.store.version(full["versionId"])["meta"])
    rows = [{"datetime": row.get("datetime"), "name": names.get(row.get("instrument"), row.get("instrument")), "field": row.get("field"), "value": rounded(row.get("value")),
             "availableAt": row.get("availableAt"), "recordedAt": row.get("recordedAt"), "mode": row.get("mode")} for row in full["rows"]]
    out = {key: value for key, value in full.items() if key not in ("rows", "nextOffset")}

    def make(count):
        end = offset + count
        return {**out, "rows": rows[:count], "offset": offset, "nextOffset": end if end < full["total"] else None}
    low, high = 0, len(rows)
    while low < high:  # the most rows that fit
        middle = (low + high + 1) // 2
        if size(make(middle)) <= BUDGET:
            low = middle
        else:
            high = middle - 1
    return make(low)


def compare(library, version_a, version_b):
    full = library.compare(version_a, version_b)
    names = names_of(library.store.version(version_b)["meta"]) | names_of(library.store.version(version_a)["meta"])

    def side(figures, cap):
        return {**{key: value for key, value in figures.items() if key not in ("published", "after", "origin")}, "origin": origin_of(figures.get("origin")),
                "published": figures_of(figures.get("published"), names, cap), "after": figures_of(figures.get("after"), names, cap)}

    def setting(value):
        return shorten(value, 5) if isinstance(value, (list, dict)) else head(value, 200) if isinstance(value, str) else value

    def make(cap):
        out = {"a": side(full["a"], cap), "b": side(full["b"], cap), "overlap": full.get("overlap")}
        out["code"] = [{"file": entry["file"], "status": entry["status"], "diff": "\n".join((entry.get("diff") or "").splitlines()[:cap * 4]),
                        "lines": len((entry.get("diff") or "").splitlines())} for entry in (full.get("code") or [])[:cap]]
        settings = full.get("settings") or []
        out["settings"] = [{"what": entry["what"], "from": setting(entry.get("from")), "to": setting(entry.get("to"))} for entry in settings[:cap * 2]]
        if len(settings) > cap * 2:
            out["moreSettings"] = len(settings) - cap * 2
        if full.get("weights") is not None:
            moved = sorted(full["weights"], key=lambda row: -abs(row["change"] if row["change"] is not None else (row["a"] or row["b"] or 0)))
            out["weights"] = {"moved": [{"name": names.get(row["instrumentId"], row["instrumentId"]), "a": rounded(row["a"], 4), "b": rounded(row["b"], 4),
                                         "change": rounded(row["change"], 4)} for row in moved[:cap]], "assets": len(moved)}
        return out
    return fit(make)
