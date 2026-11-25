"""An allocation's preparation: the settings TALYX's standard file declares, the pool from the data settings, the values with their checks, what
each objective accepts (read from `native/allocation_capabilities.json`, which a test keeps equal to skfolio), and the checks before a run.

The Host never imports skfolio: the choice lists are literals in `native/allocation_standard.py` and the capabilities a file.
"""
from __future__ import annotations

import copy
import json

from talyx_sdk import ContractError
from talyx_sdk.i18n import t

from .object_params import check_values, read_parameters
from .project_paths import MODULE

STANDARD = MODULE / "native" / "allocation_standard.py"
CAPABILITIES = MODULE / "native" / "allocation_capabilities.json"
# Market's asset classes (an ETF is a `fund`; Market does not say whether a fund holds stocks or bonds, so it gets a group of its own the user splits)
GROUP_BY_CLASS = {"equity": "Stocks", "fund": "Funds", "crypto": "Crypto", "commodity": "Commodities", "fx": "Currencies", "index": "Indices"}
DEFAULT_BENCHMARK = "SPY 60%, IEF 40%"   # what the tracking objective and the tracking limit follow unless the allocation names another
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
    params.setdefault("risk_free_pct", 4.0)   # code written before the fixed rate declares none
    params.setdefault("benchmark", DEFAULT_BENCHMARK)   # nor a benchmark
    return params


def default_risk_free(obj):
    """The risk-free rate an allocation starts with: zero for a pool of crypto alone, a fixed rate (4% a year, JoinQuant's) for A shares alone, else the
    13-week T-bill (the cash of a dollar pool of stocks and funds)."""
    instruments = [item.get("instrument") or {} for item in obj["settings"].get("data", {}).get("symbols", [])]
    if instruments and all(item.get("assetClass") == "crypto" for item in instruments):
        return "zero"
    return "fixed" if instruments and all(item.get("venueId") == "CN" for item in instruments) else "tbill"


async def risk_free(ctx, params, since, until, owner):
    """The run's rate as the worker reads it (`allocation_prices.risk_free_per_bar`): {basis: zero}, {basis: fixed, pct}, or the ^IRX closes from Market between
    `since` and `until` (ms) with `unavailable` saying why there are none."""
    from .strategy_inputs import risk_free as fetch
    basis = params["risk_free"]
    spec = {"basis": "fixed", "pct": float(params["risk_free_pct"])} if basis == "fixed" else {"basis": {"tbill": "irx", "zero": "zero"}[basis]}
    return await fetch(ctx, spec, since, until, owner)


def tracks(params):
    """Whether the settings follow the benchmark: the benchmark-tracking objective, or a tracking-error limit."""
    return params.get("objective") == "benchmark_tracking" or params.get("max_tracking") not in (None, "")


def benchmark_parts(text):
    """The benchmark as written, "SPY 60%, IEF 40%" or one symbol ("QQQ", all of it): [(Market symbol, percent)] adding up to 100. Raises ContractError on the
    benchmark field otherwise."""
    parts = []
    for piece in (text or "").split(","):
        words = piece.split()
        if not words:
            continue
        if len(words) > 2 or (len(words) == 2 and not words[1].endswith("%")):
            raise ContractError("invalid_input", t("Benchmark: write each symbol with its weight, such as SPY 60%, not {strip}", strip=piece.strip()), {"fields": ["benchmark"]})
        try:
            weight = float(words[1][:-1]) if len(words) == 2 else None
        except ValueError:
            raise ContractError("invalid_input", t("Benchmark: {words} is not a percent", words=words[1]), {"fields": ["benchmark"]}) from None
        parts.append((words[0], weight))
    if not parts:
        raise ContractError("invalid_input", t("Benchmark: name a Market symbol, such as SPY"), {"fields": ["benchmark"]})
    if len(parts) == 1 and parts[0][1] is None:
        return [(parts[0][0], 100.0)]
    if any(weight is None for _, weight in parts) or abs(sum(weight for _, weight in parts) - 100) > 1e-6:
        raise ContractError("invalid_input", t("Benchmark: the weights add up to 100%, such as SPY 60%, IEF 40%"), {"fields": ["benchmark"]})
    return parts


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
        raise ContractError("invalid_input", t("{name} needs a number or nothing.", name=name), {"fields": [name]}) from None


def costs_of(params, pool):
    """The costs as charged: one row for each group of the pool, its own bps or else the Other row's (0 without one), so the record says what the run charges."""
    table = {row["group"]: float(row.get("bps") or 0) for row in params.get("costs") or []}
    groups = list(dict.fromkeys(row["group"] for row in pool))
    return [{"group": group, "bps": table.get(group, table.get("Other", 0.0))} for group in groups]


def check_values_before_run(params, pool):
    """What can be known without data: the pool, the limits, the numbers, the risk budget. Raises ContractError with the fields to mark."""
    if not pool:
        raise ContractError("invalid_input", t("The pool is empty"), {"fields": ["pool"]})
    for name in NUMBER_TEXT:
        number(params.get(name), name)
    cap = number(params.get("max_weight"), "max_weight")
    short = float(params.get("short_cap") or 0)
    if cap is not None and short == 0 and cap * len(pool) < 100 - 1e-9:
        raise ContractError("invalid_input", t("Limits can't add up to 100%: {pool} assets x {cap}% = {cap2}%", pool=len(pool), cap=f'{cap:g}', cap2=f'{cap * len(pool):g}'), {"fields": ["max_weight"]})
    maxima = {row["group"]: number(row.get("max"), "group_limits") for row in params.get("group_limits") or [] if row.get("max") not in (None, "")}
    if maxima and short == 0:
        groups = {}
        for row in pool:
            groups[row["group"]] = groups.get(row["group"], 0) + 1
        each = {group: min(maxima.get(group, 100.0), (cap if cap is not None else 100.0) * count) for group, count in groups.items()}
        room = sum(each.values())
        if room < 100 - 1e-9:  # name each group's room, so the one to raise can be found
            parts = ", ".join(f"{group} {value:g}%" for group, value in sorted(each.items(), key=lambda item: -item[1]))
            raise ContractError("invalid_input", t("Limits can't add up to 100%: the group limits allow {room}% ({parts})", room=f'{room:g}', parts=parts), {"fields": ["max_weight", "group_limits"]})
    if tracks(params):
        benchmark_parts(params["benchmark"])
    if params.get("objective") == "risk_budget":
        total = sum(float(row.get("share") or 0) for row in params.get("budget") or [])
        if abs(total - 100) > 1e-6:
            raise ContractError("invalid_input", t("Shares add up to {total}%", total=f'{total:g}'), {"fields": ["budget"]})
    return True


def groups_view(declared):
    groups = []
    for item in declared["parameters"]:
        group = item.get("group") or "Settings"
        if not groups or groups[-1]["name"] != group:
            groups.append({"name": group, "settings": []})
        groups[-1]["settings"].append(item)
    return groups
