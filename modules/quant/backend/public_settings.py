"""Translate editable public settings to the stored research record at the public action boundary."""

from __future__ import annotations

from datetime import UTC, datetime

from talyx_sdk import ContractError

from .allocation_prep import NUMBER_TEXT

PERCENT = {
    "long_cap": "longCapPct",
    "short_cap": "shortCapPct",
    "net": "netPct",
    "max_weight": "maxWeightPct",
    "min_weight": "minWeightPct",
    "threshold": "thresholdPct",
    "max_turnover": "maxTurnoverPct",
    "max_tracking": "maxTrackingPct",
    "risk_cap": "riskCapPct",
    "min_return": "minReturnPct",
    "confidence": "confidencePct",
}
SELECTIONS = ("symbols", "index", "list", "universe")


def timestamp(ms):
    return datetime.fromtimestamp(ms / 1000, UTC).isoformat().replace("+00:00", "Z")


def milliseconds(value, end=False):
    try:
        moment = datetime.fromisoformat(value)
        if len(value) == 10:
            moment = moment.replace(tzinfo=UTC)
        elif moment.tzinfo is None:
            raise ValueError
    except (ValueError, TypeError) as error:
        raise ContractError(
            "invalid_input", f"Expected a date or an ISO time with timezone, got {value!r}."
        ) from error
    return int(moment.timestamp() * 1000) + (86_399_999 if end and len(value) == 10 else 0)


def merge(original, patch):
    if not isinstance(patch, dict):
        return patch
    merged = dict(original) if isinstance(original, dict) else {}
    for key, value in patch.items():
        if value is None:
            merged.pop(key, None)
        else:
            merged[key] = merge(merged.get(key), value)
    return merged


def changed(before, after, root=""):
    found = []
    for key in sorted(before.keys() | after.keys()):
        path = f"{root}.{key}" if root else key
        left, right = before.get(key), after.get(key)
        if left == right:
            continue
        if isinstance(left, dict) and isinstance(right, dict):
            found.extend(changed(left, right, path))
        else:
            found.append({"path": path, "from": left, "to": right})
    return found


def edit_list(original, edit, path):
    if isinstance(edit, list):
        return list(edit)
    if not isinstance(edit, dict) or not edit or edit.keys() - {"add", "remove"}:
        raise ContractError("invalid_input", f"{path} takes a list, or add/remove lists.")
    for key, values in edit.items():
        if not isinstance(values, list):
            raise ContractError("invalid_input", f"{path}.{key} takes a list.")
    result = [value for value in original if value not in edit.get("remove", [])]
    for value in edit.get("add", []):
        if value not in result:
            result.append(value)
    return result


def run_out(record, run):
    """A model's trade cost is stored as cost, in basis points; callers see it as costBps."""
    if record["kind"] != "model" or "cost" not in run:
        return run
    return {("costBps" if key == "cost" else key): value for key, value in run.items()}


def run_in(record, run):
    if record["kind"] != "model":
        return run
    if "cost" in run:
        raise ContractError("invalid_input", "A model's trade cost is run.costBps, in basis points.")
    return {("cost" if key == "costBps" else key): value for key, value in run.items()}


def params_out(record, values):
    if record["kind"] != "allocation" or record["settings"].get("mode", "standard") != "standard":
        return dict(values)
    return {
        PERCENT.get(name, name): (float(value) if name in PERCENT and value != "" else value)
        for name, value in values.items()
    }


def params_in(record, values):
    if record["kind"] != "allocation" or record["settings"].get("mode", "standard") != "standard":
        return values
    inverse = {new: old for old, new in PERCENT.items()}
    result = {}
    for name, value in values.items():
        if name in PERCENT:
            raise ContractError("invalid_input", f"Use {PERCENT[name]} for a percentage.")
        if name in inverse:
            name = inverse[name]
            if type(value) not in (float, int) and value != "":
                raise ContractError("invalid_input", f"{PERCENT[name]} takes a percentage.")
            if name in NUMBER_TEXT:
                value = str(value)
        result[name] = value
    return result


async def settings(ctx, record):
    from . import market_inputs as market

    saved = record["settings"]
    raw_data = saved.get("data") or {}
    data = {
        k: v
        for k, v in raw_data.items()
        if k not in {"symbols", "index", "since", "until", "heldOutFrom", "adjustment"}
    }
    if "since" in raw_data:
        data["start"] = raw_data["since"]
    if raw_data.get("until") and raw_data["until"] != "latest":
        data["end"] = raw_data["until"]
    if raw_data.get("adjustment"):
        data["adjustment"] = {"adjusted": "split_dividend"}.get(
            raw_data["adjustment"], raw_data["adjustment"]
        )
    raw_items = [row["instrument"] for row in raw_data.get("symbols", [])]
    if raw_data.get("index"):
        raw_items.append(raw_data["index"]["instrument"])
    resolved = await market.saved_items(ctx, raw_items)
    labels = {item["symbol"]: item["instrument"] for item in resolved}
    if "symbols" in raw_data:
        data["symbols"] = [item["symbol"] for item in resolved[: len(raw_data["symbols"])]]
    if raw_data.get("index"):
        index = raw_data["index"]
        data["index"] = {"symbol": resolved[-1]["symbol"]}
        if index.get("at"):
            data["index"]["at"] = index["at"]
        if index.get("asOf") is not None:
            data["indexAsOf"] = timestamp(index["asOf"])
    run = dict(saved.get("run") or {})
    if raw_data.get("heldOutFrom"):
        run["heldOutFrom"] = raw_data["heldOutFrom"]
    account = saved.get("account") or {}
    if "capital" in account:
        run.setdefault("capital", account["capital"])
    if "leverage" in account:
        run["exchange"] = {"leverage": account["leverage"], **run.get("exchange", {})}
    if account.get("fee") or account.get("stockCommission"):
        costs = {}
        if account.get("fee"):
            costs = {
                "feePct": account["fee"]["taker"] * 100,
                "makerPct": account["fee"]["maker"] * 100,
            }
        if account.get("stockCommission"):
            costs["stockCommission"] = account["stockCommission"]
        run["costs"] = {**costs, **run.get("costs", {})}
    return {"data": data, "run": run_out(record, run), "params": params_out(record, saved.get("params", {}))}, labels


async def store_data(ctx, data, labels):
    from . import market_inputs as market

    raw = dict(data)
    index = raw.pop("index", None)
    if index is not None and (
        not isinstance(index, dict)
        or index.keys() - {"symbol", "at"}
        or not isinstance(index.get("symbol"), str)
    ):
        raise ContractError("invalid_input", "data.index holds symbol and optionally at.")
    names = raw.get("symbols", [])
    if not isinstance(names, list) or any(not isinstance(s, str) or not s for s in names):
        raise ContractError("invalid_input", "data.symbols must contain nonempty symbol strings.")
    missing = list(
        dict.fromkeys(s for s in [*names, *([index["symbol"]] if index else [])] if s not in labels)
    )
    if missing:
        for symbol, item in zip(missing, await market.symbol_items(ctx, missing), strict=True):
            labels[symbol] = item["instrument"]
    if "symbols" in raw:
        raw["symbols"] = [{"instrument": labels[s]} for s in names]
    if index:
        raw["index"] = {"instrument": labels[index["symbol"]]}
        if index.get("at"):
            raw["index"]["at"] = index["at"]
        if "indexAsOf" in raw:
            raw["index"]["asOf"] = milliseconds(raw.pop("indexAsOf"))
    if raw.get("adjustment") not in {None, "raw", "split", "split_dividend"}:
        raise ContractError("invalid_input", "data.adjustment is raw, split or split_dividend.")
    if "start" in raw:
        raw["since"] = raw.pop("start")
    raw["until"] = raw.pop("end", "latest")
    return raw


async def prepare_update(store, record, args):
    if record["kind"] == "notebook":
        invalid = set(args) & {"params", "run", "libraryInputs", "formula"}
        if invalid:
            raise ContractError(
                "invalid_input", f"A notebook cannot set {', '.join(sorted(invalid))}."
            )
    previous, labels = await settings(store.ctx, record)
    patch = {key: args[key] for key in ("data", "params", "run") if key in args}
    for area, array in (("data", "symbols"), ("params", "inputs")):
        if isinstance(patch.get(area), dict) and isinstance(patch[area].get(array), dict):
            patch[area] = {
                **patch[area],
                array: edit_list(
                    previous[area].get(array, []), patch[area][array], f"{area}.{array}"
                ),
            }
    if isinstance(patch.get("data"), dict):
        if "heldOutFrom" in patch["data"]:
            raise ContractError("invalid_input", "Set the held-out date in run.heldOutFrom.")
        keys = [s for s in SELECTIONS if patch["data"].get(s) is not None]
        if len(keys) > 1:
            raise ContractError(
                "invalid_input", "Choose one of data.symbols, index, list or universe."
            )
        if keys:
            patch["data"] = {**{s: None for s in SELECTIONS if s != keys[0]}, **patch["data"]}
            if keys[0] != "index":
                patch["data"]["indexAsOf"] = None
        old_names = {"since", "until"} & patch["data"].keys()
        if old_names:
            raise ContractError(
                "invalid_input", "Use data.start and data.end for the requested dates."
            )
    target = {
        k: merge(previous[k], patch[k]) or {} if k in patch else previous[k] for k in previous
    }
    updates = {}
    if (
        "data" in patch
        or "run" in patch
        and ("heldOutFrom" in (patch["run"] or {}) or not patch["run"])
    ):
        raw = await store_data(store.ctx, target["data"], labels)
        if record["kind"] != "strategy":
            raw["heldOutFrom"] = target["run"].get("heldOutFrom")
        updates["data"] = raw
    if "params" in patch:
        updates["params"] = params_in(record, target["params"])
    if "formula" in args:
        if record["kind"] != "factor" or store.source_path(record).is_file():
            raise ContractError("invalid_input", "formula applies to a formula factor.")
        updates["formula"] = args["formula"] or ""
    if "run" in patch:
        run = run_in(record, dict(target["run"]))
        if record["kind"] == "strategy":
            # The saved range and holdout change atomically. Validate their combination,
            # including the case where the new start predates the object's original range.
            held = run.pop("heldOutFrom", None)
            costs = dict(run.get("costs") or {})
            commission = costs.pop("stockCommission", None)
            updates["account"] = {"stockCommission": commission} if commission else {}
            if costs:
                run["costs"] = costs
            else:
                run.pop("costs", None)
        else:
            run.pop("heldOutFrom", None)
        if record["kind"] == "allocation" and not run:
            pass
        else:
            updates["run"] = run
    next_settings = await store.checked_settings(record, updates, code=args.get("code")) if updates or "code" in args else record["settings"]
    if "run" in patch and record["kind"] == "strategy":
        from .object_store import check_data

        data = dict(next_settings["data"])
        if held is not None:
            data["heldOutFrom"] = held
        else:
            data.pop("heldOutFrom", None)
        check_data(data, "strategy")
        next_settings = {**next_settings, "data": data}
    if "run" in patch and record["kind"] in {"factor", "model"}:
        # The validation also computes effective defaults for the private page.
        # Only the explicit patch keys belong in the saved public settings.
        next_settings = {
            **next_settings,
            "run": run_in(record, {k: v for k, v in target["run"].items() if k != "heldOutFrom"}),
        }
    return next_settings, changed(previous, target)
