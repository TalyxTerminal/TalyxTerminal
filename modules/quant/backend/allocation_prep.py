"""An allocation's preparation: the settings TALYX's standard file declares, the pool from the data settings, the values with their checks, what
each objective accepts (read from `native/allocation_capabilities.json`, which a test keeps equal to skfolio), and the checks before a run.

The Host never imports skfolio: the choice lists are literals in `native/allocation_standard.py` and the capabilities a file.
"""
from __future__ import annotations

import copy
import json

from talyx_sdk import ContractError

from .object_params import check_values, read_parameters
from .project_paths import MODULE

STANDARD = MODULE / "native" / "allocation_standard.py"
CAPABILITIES = MODULE / "native" / "allocation_capabilities.json"
# Market's asset classes (an ETF is a `fund`; Market does not say whether a fund holds stocks or bonds, so it gets a group of its own the user splits)
GROUP_BY_CLASS = {"equity": "Stocks", "fund": "Funds", "crypto": "Crypto", "commodity": "Commodities", "fx": "Currencies", "index": "Indices"}
BENCHMARK = ("SPY", "IEF")   # the 60/40 the tracking limit, the tracking objective and the comparisons are against
NUMBER_TEXT = ("max_weight", "min_weight", "cardinality", "threshold", "max_turnover", "max_tracking", "risk_cap", "min_return")
ENGINE_TEXT = ("objective_parameters", "covariance_parameters")
MIN_PERIODS = 12


def money(code):
    """A currency as the pool compares it: the dollar stablecoins a crypto pair is quoted in count as dollars."""
    code = (code or "").strip().upper()
    return "USD" if code in ("USDT", "USDC", "BUSD") else code


def standard_declarations():
    return read_parameters(STANDARD.read_text(encoding="utf-8"), "allocation_standard.py")


def capabilities():
    return json.loads(CAPABILITIES.read_text(encoding="utf-8"))


def declarations(store, obj):
    if obj["settings"].get("mode", "standard") == "standard":
        return standard_declarations()
    return store.project_parameters(obj)


def values_of(store, obj, changes=None):
    """Every declared setting's value (the object's, else the default; `changes` on top), checked against the declarations."""
    declared = declarations(store, obj)
    if declared["errors"]:
        raise ContractError("invalid_input", declared["errors"][0]["message"], {"errors": declared["errors"]})
    params = {item["name"]: copy.deepcopy(obj["settings"].get("params", {}).get(item["name"], item["default"])) for item in declared["parameters"]}
    params.update(changes or {})
    for name in NUMBER_TEXT:  # a limit is text so blank can mean none; a number given for one (an AI's 30) is the same limit
        if isinstance(params.get(name), (int, float)) and not isinstance(params.get(name), bool):
            params[name] = f"{params[name]:g}"
    if isinstance(params.get("pool"), list):  # a pool saved before rows said what they are
        params["pool"] = [{"kind": "", "runId": "", **row} if isinstance(row, dict) else row for row in params["pool"]]
    try:
        check_values(declared["parameters"], params)
    except ValueError as error:
        raise ContractError("invalid_input", str(error)) from error
    if "risk_free" not in obj["settings"].get("params", {}) and "risk_free" not in (changes or {}):
        params["risk_free"] = default_risk_free(obj)   # also for code that declares no rate: a run's Sharpe is always over one
    return params


def default_risk_free(obj):
    """The risk-free rate an allocation starts with: zero for a pool of crypto alone, else the 13-week T-bill (the cash of a dollar pool of stocks and funds)."""
    classes = [(item.get("instrument") or {}).get("assetClass") for item in obj["settings"].get("data", {}).get("symbols", [])]
    return "zero" if classes and all(kind == "crypto" for kind in classes) else "tbill"


async def risk_free(ctx, params, since, until, owner):
    """The run's rate as the worker reads it (`allocation_work.risk_free_per_bar`): {basis: zero}, or the ^IRX closes from Market between `since` and `until`
    (ms) with `unavailable` saying why there are none."""
    from .strategy_inputs import risk_free as fetch
    return await fetch(ctx, {"basis": "irx" if params["risk_free"] == "tbill" else "zero"}, since, until, owner)


def pool_of(obj, params, columns=None):
    """The pool rows: one for each symbol of the data settings, with the group the user gave (else by asset class). `columns` maps an instrument
    id to the column the frozen bars use, once there are bars."""
    given = {row.get("ticker"): row for row in params.get("pool") or []}
    rows = []
    for item in obj["settings"].get("data", {}).get("symbols", []):
        instrument = item.get("instrument") or {}
        ticker = (columns or {}).get(instrument.get("instrumentId")) or instrument.get("symbol") or instrument.get("displaySymbol")
        if not ticker:
            continue
        row = given.get(ticker) or given.get(instrument.get("displaySymbol")) or {}
        rows.append({"ticker": ticker, "name": instrument.get("name") or ticker, "instrumentId": instrument.get("instrumentId"),
                     "group": row.get("group") or GROUP_BY_CLASS.get(instrument.get("assetClass"), "Other"), "currency": instrument.get("currency") or instrument.get("quote"),
                     "from": row.get("from", ""), "proxy": row.get("proxy", "")})
    # Rows that are not Market symbols: a strategy run (its daily equity is the asset's price) or a library item; they stay in the pool as the user put them.
    rows += [{"ticker": row["ticker"], "name": row.get("name") or row["ticker"], "kind": row["kind"], "runId": row.get("runId"), "group": row.get("group") or "Strategies",
              "currency": row.get("currency") or "", "from": row.get("from", ""), "proxy": ""} for row in params.get("pool") or [] if row.get("kind") in ("run", "library") and row.get("ticker")]
    return rows


def number(value, name):
    if value in (None, ""):
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        raise ContractError("invalid_input", f"{name} needs a number or nothing.", {"fields": [name]}) from None


def costs_of(params, pool):
    """The costs as charged: one row for each group of the pool, its own bps or else the Other row's (0 without one), so the record says what the run charges."""
    table = {row["group"]: float(row.get("bps") or 0) for row in params.get("costs") or []}
    groups = list(dict.fromkeys(row["group"] for row in pool))
    return [{"group": group, "bps": table.get(group, table.get("Other", 0.0))} for group in groups]


def check_values_before_run(params, pool):
    """What can be known without data: the pool, the limits, the numbers, the risk budget. Raises ContractError with the fields to mark."""
    if not pool:
        raise ContractError("invalid_input", "The pool is empty", {"fields": ["pool"]})
    for name in NUMBER_TEXT:
        number(params.get(name), name)
    cap = number(params.get("max_weight"), "max_weight")
    short = float(params.get("short_cap") or 0)
    if cap is not None and short == 0 and cap * len(pool) < 100 - 1e-9:
        raise ContractError("invalid_input", f"Limits can't add up to 100%: {len(pool)} assets x {cap:g}% = {cap * len(pool):g}%", {"fields": ["max_weight"]})
    maxima = {row["group"]: number(row.get("max"), "group_limits") for row in params.get("group_limits") or [] if row.get("max") not in (None, "")}
    if maxima and short == 0:
        groups = {}
        for row in pool:
            groups[row["group"]] = groups.get(row["group"], 0) + 1
        each = {group: min(maxima.get(group, 100.0), (cap if cap is not None else 100.0) * count) for group, count in groups.items()}
        room = sum(each.values())
        if room < 100 - 1e-9:  # name each group's room, so the one to raise can be found
            parts = ", ".join(f"{group} {value:g}%" for group, value in sorted(each.items(), key=lambda item: -item[1]))
            raise ContractError("invalid_input", f"Limits can't add up to 100%: the group limits allow {room:g}% ({parts})", {"fields": ["max_weight", "group_limits"]})
    missing = [name for name in BENCHMARK if name not in {row["ticker"] for row in pool}]
    if missing and params.get("objective") == "benchmark_tracking":
        raise ContractError("invalid_input", f"Benchmark tracking follows the 60/40 of SPY and IEF; add {' and '.join(missing)} to the pool", {"fields": ["objective"]})
    if missing and params.get("max_tracking") not in (None, ""):
        raise ContractError("invalid_input", f"The tracking error is against the 60/40 of SPY and IEF; add {' and '.join(missing)} to the pool", {"fields": ["max_tracking"]})
    if params.get("objective") == "risk_budget":
        total = sum(float(row.get("share") or 0) for row in params.get("budget") or [])
        if abs(total - 100) > 1e-6:
            raise ContractError("invalid_input", f"Shares add up to {total:g}%", {"fields": ["budget"]})
    return True


def groups_view(declared):
    groups = []
    for item in declared["parameters"]:
        group = item.get("group") or "Settings"
        if not groups or groups[-1]["name"] != group:
            groups.append({"name": group, "settings": []})
        groups[-1]["settings"].append(item)
    return groups
