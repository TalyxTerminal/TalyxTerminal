"""A parameter sweep: one backtest per combination, over the data and environment one preparation fixed, as one entry of the run history.

A combination's axes are the code's number, switch and choice parameters and the run settings that can be swept (`@costMultiple`, `@fill`, `@capital`, `@period`).
How many run at once follows this machine: every combination takes its place in Quant's one queue (`run_queue`) with its expected memory, so the
CPU slots and the memory budget decide, and what a person is waiting for goes first. A combination runs a light report (key numbers, equity,
trades); the full report is made when a person runs that combination.

A walk-forward validation is a sweep scored window by window: each window's best combination (on the picking months, from the equity of the whole
run) is run again on the months after it, from flat, with the capital the previous window ended with, and the windows are stitched."""
from __future__ import annotations

import asyncio
import contextlib
import copy
import itertools
import json
import math
import time
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path
from uuid import uuid4

import pandas as pd
from talyx_sdk import ContractError
from talyx_sdk.i18n import t

from ..native import metrics as M
from . import run_flow, strategy_checks, strategy_settings, sweep_figures
from .object_data import day_ms
from .object_params import check_values
from .object_runs import RUN, finish_copy, new_run_directory
from .run_queue import RunQueue

MAX_AXES = 8
FORMAT = 2
# What a combination can be ranked by; all of them are "more is better" (a drawdown is stored as a negative number).
METRICS = ("sharpe", "cagr", "totalReturn", "maxDrawdown", "trades", "profitFactor", "winRate")
PICK_BY = ("sharpe", "cagr", "calmar")
# Run settings a sweep can vary beside the code's parameters: every cost counted this many times, the fill timing, the capital, the period.
SETTINGS = {"costMultiple": "Costs ×", "fill": "Fill", "capital": "Capital", "period": "Period"}
COST_KEYS = ("feePct", "makerPct", "commissionBps", "halfSpreadBps", "impact", "borrowPct")
# A swept period is run after this many months of data it only reads to warm its indicators up.
WARM_UP_MONTHS = 12
TERMINAL = {"completed", "failed", "cancelled", "interrupted"}
# How long one combination takes when nothing is known about the code (an earlier run of it is the better guess).
GUESS_SECONDS = 60
DAY = 86_400_000


def label_of(definition):
    """The name the code gives a setting, without a trailing unit in brackets ("Lookback (bars)" is Lookback)."""
    import re
    return re.sub(r"\s*\([^()=;,]{1,10}\)$", "", definition.get("label") or definition["name"]) or definition["name"]


def months_later(ms, months):
    moment = datetime.fromtimestamp(ms / 1000, UTC)
    month = moment.month - 1 + months
    year = moment.year + month // 12
    return int(moment.replace(year=year, month=month % 12 + 1, day=min(moment.day, 28)).timestamp() * 1000)


# ---------------------------------------------------------------------------------------------------------------- the combinations
def axis_values(name, values, definition):
    """The values of one axis, checked: a code parameter's against what the code declares, a run setting's against what it accepts."""
    if not isinstance(values, list) or not values:
        raise ContractError("invalid_input", t("{name} needs a list of values.", name=name), {"axis": name})
    if len(set(json.dumps(item, sort_keys=True) for item in values)) != len(values):
        raise ContractError("invalid_input", t("{name} lists a value twice.", name=name), {"axis": name})
    if name.startswith("@"):
        return setting_values(name[1:], values)
    if definition is None:
        raise ContractError("invalid_input", t("The code has no parameter {name}.", name=repr(name)), {"parameter": name})
    if definition["type"] == "bool":
        # a switch is swept over both of its sides.
        if any(not isinstance(item, bool) for item in values):
            raise ContractError("invalid_input", t("{definition} is swept over on and off (true, false).", definition=label_of(definition)), {"parameter": name})
        return sorted(values)
    if definition["type"] == "choice":
        # a choice is swept over options the code declares, in the code's order.
        options = [option["value"] for option in definition["options"]]
        for item in values:
            if item not in options:
                raise ContractError("value_outside_declared", t("{definition} has no option {item}", definition=label_of(definition), item=repr(item)), {"parameter": name, "value": item})
        return [option for option in options if option in values]
    if definition["type"] not in ("int", "float"):
        raise ContractError("invalid_input", t("{definition} is a {type}; numbers, switches and choices can be swept.", definition=label_of(definition), type=definition['type']), {"parameter": name})
    for item in values:
        if type(item) not in (int, float) or isinstance(item, bool) or not math.isfinite(item):
            raise ContractError("invalid_input", t("{definition} is swept over numbers.", definition=label_of(definition)), {"parameter": name})
        if definition["type"] == "int" and item != int(item):
            raise ContractError("value_outside_declared", t("{definition} {item} is not a whole number", definition=label_of(definition), item=f'{item:g}'), {"parameter": name, "value": item})
        low, high = definition.get("min"), definition.get("max")
        if low is not None and item < low or high is not None and item > high:
            raise ContractError("value_outside_declared", t("{definition} {item} is outside {low}–{high}", definition=label_of(definition), item=f'{item:g}', low=f'{low:g}', high=f'{high:g}') if low is not None and high is not None
                                else t("{definition} {item} is outside what the code allows", definition=label_of(definition), item=f'{item:g}'), {"parameter": name, "value": item, "min": low, "max": high})
    return sorted(int(item) if definition["type"] == "int" else float(item) for item in values)


def setting_values(name, values):
    if name not in SETTINGS:
        raise ContractError("invalid_input", t("A sweep can vary these run settings: {settings}.", settings=', '.join('@' + key for key in SETTINGS)), {"setting": name})
    if name == "costMultiple" and any(type(item) not in (int, float) or not 0 <= item <= 10 for item in values):
        raise ContractError("invalid_input", t("@costMultiple values are numbers from 0 to 10."))
    if name == "capital" and any(type(item) not in (int, float) or item <= 0 for item in values):
        raise ContractError("invalid_input", t("@capital values are numbers above zero."))
    if name == "fill" and any(item not in ("nextOpen", "nextClose") for item in values):
        raise ContractError("invalid_input", t("@fill values are nextOpen and nextClose."))
    if name == "period":
        for item in values:
            if not isinstance(item, dict) or set(item) != {"since", "until"}:
                raise ContractError("invalid_input", t("@period values are {start, end} days."))
            try:
                if datetime.fromisoformat(item["until"]) < datetime.fromisoformat(item["since"]):
                    raise ValueError
            except (TypeError, ValueError):
                raise ContractError("invalid_input", t("@period values are {start, end} days, end not before start.")) from None
    return list(values)


def expand(axes, definitions):
    """The axes as stored (name, type, values, and a label for a run setting) and every combination, each a dict from axis name to value."""
    if not isinstance(axes, list) or not 1 <= len(axes) <= MAX_AXES:
        raise ContractError("invalid_input", t("Choose 1 to {MAX_AXES} parameters to sweep.", MAX_AXES=MAX_AXES))
    known = {item["name"]: item for item in definitions}
    found = []
    seen = set()
    for axis in axes:
        name = axis["name"]
        if name in seen:
            raise ContractError("invalid_input", t("{name} is swept twice.", name=name), {"axis": name})
        seen.add(name)
        values = axis_values(name, axis["values"], known.get(name))
        found.append({"name": name, "type": "setting" if name.startswith("@") else known[name]["type"], "values": values,
                      **({"label": SETTINGS[name[1:]]} if name.startswith("@") else {"label": label_of(known[name])})})
    names = [axis["name"] for axis in found]
    return found, [dict(zip(names, combination, strict=True)) for combination in itertools.product(*[axis["values"] for axis in found])]


def split(combination):
    """A combination's code parameters and its run-setting values."""
    return ({key: value for key, value in combination.items() if not key.startswith("@")},
            {key[1:]: value for key, value in combination.items() if key.startswith("@")})


def case_settings(run, overrides):
    """The run settings of one combination: costs multiplied, the fill timing and the capital changed."""
    run = copy.deepcopy(run)
    if "fill" in overrides:
        run["fill"] = overrides["fill"]
    if "capital" in overrides:
        run["capital"] = overrides["capital"]
    if "costMultiple" in overrides:
        for key in COST_KEYS:
            if run["costs"].get(key) is not None:
                run["costs"][key] = run["costs"][key] * overrides["costMultiple"]
    return run


def scaled(amount, factor):
    number, unit = amount.split()
    text = format(Decimal(number) * Decimal(str(factor)), "f")
    if "." in text:
        text = text.rstrip("0").rstrip(".")
    return f"{text} {unit}"


def case_prepared(prepared, overrides, scale=None):
    """The prepared run of one combination: the venue fees follow the cost multiple, the accounts follow the capital (`scale`, a window's capital over
    the run's); everything else is the sweep's."""
    factor = overrides.get("costMultiple")
    capital = overrides.get("capital")
    if capital is not None and "runSettings" in prepared and prepared["runSettings"]["capital"]:
        scale = capital / prepared["runSettings"]["capital"]
    if factor is None and scale is None:
        return prepared
    venues = []
    for venue in prepared["venues"]:
        fees = dict(venue["fees"])
        if factor is not None and fees.get("type") == "maker_taker":
            multiple = Decimal(str(factor))
            fees.update(maker=format(Decimal(str(fees["maker"])) * multiple, "f"), taker=format(Decimal(str(fees["taker"])) * multiple, "f"))
        venues.append({**venue, "fees": fees, **({"balances": [scaled(amount, scale) for amount in venue["balances"]]} if scale is not None else {})})
    instruments = []
    for item in prepared["instruments"]:
        item = dict(item)
        if factor is not None:
            multiple = Decimal(str(factor))
            for key in ("makerFee", "takerFee"):
                if key in item:
                    item[key] = format(Decimal(str(item[key])) * multiple, "f")
            if isinstance(item.get("stockCommission"), dict):
                commission = dict(item["stockCommission"])
                for key in ("rate", "perShare", "minimum"):
                    if commission.get(key) is not None:
                        commission[key] = format(Decimal(str(commission[key])) * multiple, "f")
                item["stockCommission"] = commission
        instruments.append(item)
    return {**prepared, "venues": venues, "instruments": instruments}


def period_extra(period):
    """What a combination over a period needs the worker to know: feed bars from `WARM_UP_MONTHS` before it, place orders only from its start."""
    start, end = day_ms(period["since"], False), day_ms(period["until"], True)
    return {"window": {"from": months_later(start, -WARM_UP_MONTHS), "to": end, "warmUpMonths": WARM_UP_MONTHS}, "tradeFrom": start}


# ---------------------------------------------------------------------------------------------------------------- how many at once
def concurrency(queue, per_case):
    """How many combinations the machine runs at once: the CPU slots, or the memory budget over one combination's expected memory when that is fewer."""
    by_memory = max(1, queue.budget // max(int(per_case), 1))
    return {"atOnce": max(1, min(queue.cpus, by_memory)), "limit": "memory" if by_memory < queue.cpus else "cores"}


def duration(total, per_case, at_once):
    return int(math.ceil(total / max(at_once, 1)) * per_case)


# ---------------------------------------------------------------------------------------------------------------- walk-forward
def window_spans(first, last, pick, trade):
    """The windows of `pick` months to choose on and the `trade` months after each to trade, moving on by `trade`: [(start, picked_end, traded_end)].
    Every window trades its whole `trade` months, so each counts alike in the efficiency's mean: the tail after the last whole window, shorter than
    that, is no window (a window of one day made no trade and pulled the mean to zero). Data whose last day ends where a window ends covers it (`last`
    may be that day's end or a daily point stamped at its start)."""
    spans, start = [], first
    while True:
        picked_end = months_later(start, pick)
        whole = months_later(picked_end, trade)
        if whole - last > DAY:
            return spans
        spans.append((start, picked_end, min(whole, last)))
        start = months_later(start, trade)


def window_stats(points, start, end, annualization, risk_free):
    """Sharpe, CAGR, deepest fall, Calmar and simple return of the daily equity points [[ms, value]] between start and end, by the definitions a
    whole run uses (metrics), against the run's own risk-free rate a day ([[ms, rate]]; None: unavailable, and so is the Sharpe)."""
    inside = [(at, value) for at, value in points if start <= at <= end]
    if len(inside) < 3:
        return None
    equity = [value for _, value in inside]
    returns = M.returns_of(equity)
    rate = None
    if risk_free is not None:
        rates = dict((at, value) for at, value in risk_free)
        rate = [rates.get(at, math.nan) for at, _ in inside[1:]]
    grow, fall = M.cagr(returns, annualization), M.equity_drawdown(equity)
    return {"sharpe": M.sharpe(returns, annualization, rate) if rate is not None else None, "cagr": grow, "maxDrawdown": fall,
            "calmar": M.calmar(grow, fall), "total": equity[-1] / equity[0] - 1}


def traded_between(entries, start, end):
    return sum(1 for at in entries if at is not None and start <= at <= end)


def pick_window(curves, entries, span, pick_by):
    """The combination best on a window's picking months by `pick_by`, among those that traded in it: (index, its stats there), or None when none did."""
    start, picked_end, _ = span
    best = None
    for index, (points, annualization, risk_free) in curves.items():
        if not traded_between(entries.get(index, ()), start, picked_end):
            continue
        found = window_stats(points, start, picked_end, annualization, risk_free)
        if found is None or found[pick_by] is None:
            continue
        if best is None or found[pick_by] > best[1][pick_by]:
            best = (index, found)
    return best


efficiency = strategy_checks.walk_efficiency


def stitch(parts):
    """The out-of-sample equity as a level from 1: each window's equity points [[ms, value]] (each starting at the capital the window got) joined end to
    end; the end of the whole is the product of the windows' returns."""
    level, out = 1.0, []
    for points in parts:
        if len(points) < 2:
            continue
        base = points[0][1]
        for at, value in points[(1 if out else 0):]:
            out.append([at, round(level * value / base, 12)])
        level = level * points[-1][1] / base
    return out


class Sweeps:
    def __init__(self, runs, queue=None):
        self.runs = runs
        self.queue = queue

    @property
    def machine(self):
        return self.queue or RunQueue()

    # --- starting ------------------------------------------------------------------------------------------------------------------
    async def plan(self, object_id, args, kind="sweep"):
        """Everything a sweep needs checked before it exists: the axes, the values, the combinations, what one takes and how many run at once."""
        runs = self.runs
        if runs.closed:
            raise ContractError("unavailable", t("Quant is closing."))
        obj = runs.store.read(object_id)
        if obj["kind"] != "strategy":
            raise ContractError("invalid_input", t("Only a strategy can be swept."))
        metric = args.get("metric") or ("sharpe" if kind == "walk" else "sharpe")
        if metric not in METRICS:
            raise ContractError("invalid_input", t("Rank the combinations by one of: {metrics}.", metrics=', '.join(METRICS)), {"metrics": list(METRICS)})
        source = runs.read(args["of"]) if args.get("of") else None
        if source is not None and (source["objectId"] != obj["id"] or source["state"] != "completed" or source["runKind"] not in ("run", "case", "heldout")):
            raise ContractError("invalid_input", t("Start from a completed backtest of this strategy."))
        if source:
            obj = runs.source_object(obj, source)
            saved = (Path(obj["directory"]) / obj["entry"]).read_text(encoding="utf-8")
        else:
            saved = await runs.saved_source(obj)
        definitions = runs.store.parameters(saved)["parameters"]
        values = dict(source["params"]) if source else runs.store.effective_parameters(obj, saved)
        signal_run = (source or {}).get("signalRun") or obj["settings"].get("signalRun")  # a strategy that trades a model run's predictions keeps trading them
        if signal_run and not values.get(signal_run["param"]):
            values[signal_run["param"]] = f"{signal_run['model']} #{signal_run['number']}"
        axes, combinations = expand(args.get("axes"), definitions)
        for combination in combinations:
            try:
                check_values(definitions, split(combination)[0])
            except ValueError as error:
                raise ContractError("invalid_input", str(error)) from error
        settings = copy.deepcopy(obj["settings"]) if source else await runs.settings_for(obj)
        if settings["data"].get("until") == "latest":
            from .object_data import resolve_until as resolve
            settings["data"] = await resolve(runs.ctx, settings["data"])  # the record says which day "latest" was when it started
        for combination in combinations:
            runs.store.check_short(obj, {**values, **split(combination)[0]}, settings["data"])
        walk = None
        if kind == "walk":
            walk = await self.walk_plan(settings, args)
        newest = source or self.latest_run(obj["id"])
        per_case = await self.case_memory(obj, settings, newest)
        machine = self.machine  # a combination over the budget is not refused: it runs alone (run_queue)
        seconds = self.seconds_of(newest)
        total = len(combinations) + (len(walk["spans"]) if walk else 0)
        at_once = concurrency(machine, per_case)
        return {"signalRun": signal_run, "obj": obj, "metric": metric, "axes": axes, "combinations": combinations, "values": values, "settings": settings, "walk": walk, "source": source,
                "perCase": per_case, "total": total, "estimate": {"seconds": duration(len(combinations), seconds, at_once["atOnce"]) + (len(walk["spans"]) * seconds if walk else 0),
                                                                  "perCase": seconds, **at_once, "combinations": len(combinations)},
                "machine": {"memory": machine.total, "budget": machine.budget, "cores": machine.cpus}}

    async def walk_plan(self, settings, args):
        pick, trade = args.get("pickOn", 12), args.get("trade", 3)
        if type(pick) is not int or type(trade) is not int or not 1 <= pick <= 120 or not 1 <= trade <= 60:
            raise ContractError("invalid_input", t("pickOn and trade are whole numbers of months (pick on 1 to 120, trade 1 to 60)."))
        pick_by = args.get("pickBy") or "sharpe"
        if pick_by not in PICK_BY:
            raise ContractError("invalid_input", t("Pick by one of: {criteria}.", criteria=', '.join(PICK_BY)))
        data = strategy_settings.cut_data(settings["data"], "walk")
        from .object_data import resolve_until
        resolved = await resolve_until(self.runs.ctx, data)
        if not resolved.get("since") or not resolved.get("until"):
            raise ContractError("invalid_input", t("Walk-forward needs a start and an end of the data."))
        spans = window_spans(day_ms(resolved["since"], False), day_ms(resolved["until"], True), pick, trade)
        if len(spans) < 3:
            raise ContractError("too_few_windows", t("Pick on {pick} months, trade {trade}: {spans} window fit in the period before the held-out part; at least 3 needed.",
                                                     pick=pick, trade=trade, spans=len(spans))
                                                   if len(spans) == 1 else t("Pick on {pick} months, trade {trade}: {spans} windows fit in the period before the held-out part; at least 3 needed.",
                                                     pick=pick, trade=trade, spans=len(spans)),
                                {"windows": len(spans), "pickOn": pick, "trade": trade})
        return {"pickOn": pick, "trade": trade, "pickBy": pick_by, "spans": spans}

    def latest_run(self, object_id):
        row = self.runs.records.db.execute("SELECT content FROM records WHERE kind='qrun' AND json_extract(content,'$.objectId')=? AND json_extract(content,'$.runKind')='run' "
                                           "AND json_extract(content,'$.state')='completed' ORDER BY json_extract(content,'$.createdAt') DESC LIMIT 1", (object_id,)).fetchone()
        return json.loads(row[0]) if row else None

    async def case_memory(self, obj, settings, newest):
        """What one combination is expected to need: the peak a run of this strategy measured (a little over), else the estimate from the bars it
        prepared, else from names and bars of the settings."""
        if newest and newest.get("peakMemory"):
            return int(newest["peakMemory"] * 1.15)
        if newest and (newest.get("data") or {}).get("rows"):
            return strategy_settings.memory_estimate(newest["data"]["traded"], newest["data"]["rows"])
        found = await self.runs.memory_need(settings)
        return found or strategy_settings.memory_estimate(1, 1000)

    @staticmethod
    def seconds_of(run):
        if run and run.get("endedAt"):
            return max(5, int((run["endedAt"] - run["createdAt"]) / 1000))
        return GUESS_SECONDS

    async def estimate(self, object_id, args, kind="sweep"):
        found = await self.plan(object_id, args, kind)
        return {"combinations": len(found["combinations"]), "total": found["total"], **found["estimate"], "machine": found["machine"]}

    async def start(self, object_id, args, *, kind="sweep"):
        runs = self.runs
        plan = await self.plan(object_id, args, kind)
        obj, axes, combinations = plan["obj"], plan["axes"], plan["combinations"]
        identity = "run-" + uuid4().hex
        walk = None
        if plan["walk"]:
            walk = {key: plan["walk"][key] for key in ("pickOn", "trade", "pickBy")} | {"windows": [], "stitched": [], "efficiency": None, "changed": 0}
        sweep = {"format": FORMAT, "axes": axes, "metric": plan["metric"], "selected": None, "bestIndex": None, "perCase": plan["perCase"], "source": plan["source"]["id"] if plan["source"] else None,
                 "atOnce": plan["estimate"]["atOnce"], "limit": plan["estimate"]["limit"], "estimate": plan["estimate"],
                 "cases": [{"index": number, "params": combination, "runId": None, "state": "queued", "metrics": None, "message": None} for number, combination in enumerate(combinations)]}
        with new_run_directory(runs.directory(identity)) as directory:
            code, common = await finish_copy(runs.fix_project(obj, directory, plan["source"]))
            record = runs.records.put(RUN, {"id": identity, "objectId": obj["id"], "objectKind": obj["kind"], "name": obj["name"], "runKind": kind, "mode": "sweep",
                "number": runs.next_number(obj["id"]), "workspaceId": runs.store.workspace(), "state": "preparing", "stage": "code", "createdAt": int(time.time() * 1000), "params": plan["values"],
                "overrides": {}, "settings": plan["settings"], "message": None, "error": None, "code": code, "common": common, "data": None, "environment": None,
                "recipe": None, "metrics": {}, "starred": False, "note": "", "exitCode": None, "endedAt": None, "sweep": sweep, **({"walk": walk} if walk else {}),
                **({"of": plan["source"]["id"]} if plan["source"] else {}), **({"signalRun": plan["signalRun"]} if plan["signalRun"] else {}),
                **({"signalOverride": plan["source"]["signalOverride"]} if (plan["source"] or {}).get("signalOverride") else {})})
        runs.ctx.publish("object_run_changed", {"runId": identity, "objectId": obj["id"], "workspaceId": record["workspaceId"], "state": "preparing"})
        runs.tasks[identity] = runs.ctx.start(self.execute(identity, obj), run_record=record)
        return record

    def changed(self, identity):
        record = self.runs.read(identity)
        self.runs.ctx.publish("object_run_changed", {"runId": identity, "objectId": record["objectId"], "workspaceId": record["workspaceId"], "state": record["state"]})

    def update_case(self, identity, index, **changes):
        record = self.runs.read(identity)
        cases = [{**case, **changes} if case["index"] == index else case for case in record["sweep"]["cases"]]
        self.runs.save(record, sweep={**record["sweep"], "cases": cases})
        self.changed(identity)

    # --- running -----------------------------------------------------------------------------------------------------------------
    async def prepare(self, identity, obj, directory):
        """The sweep's one preparation: the code fixed, the data frozen, the environment ready. Kept in the folder so a stopped sweep can go on."""
        runs = self.runs
        directory.mkdir(parents=True, exist_ok=True)
        captured = runs.read(identity)["code"]
        runs.update(identity, stage="data")
        prepared = await runs.prepare_data(identity, obj, directory)
        await finish_copy(asyncio.to_thread(lambda: (directory / "prepared.json").write_text(json.dumps(prepared, default=str), encoding="utf-8")))
        runs.update(identity, data=prepared["summary"], stage="environment")
        state = await runs.environment(identity, obj, captured, directory)
        return prepared, state

    async def reprepare(self, identity, obj, directory):
        """What `prepare` made, read back for a sweep that goes on: the data and code stay as they were; the environment is made ready again."""
        record = self.runs.read(identity)
        prepared = await asyncio.to_thread(lambda: json.loads((directory / "prepared.json").read_text(encoding="utf-8")))
        state = await self.runs.environment(identity, obj, record["code"], directory)
        return prepared, state

    async def execute(self, identity, obj, resume=False):
        runs = self.runs
        ending = {}
        directory = runs.directory(identity)
        try:
            prepared, state = await (self.reprepare(identity, obj, directory) if resume and runs.read(identity)["data"] is not None else self.prepare(identity, obj, directory))
            runs.update(identity, stage="sweep", state="running", endedAt=None, message=None)
            await self.run_cases(identity, obj, directory, prepared, state)
            if runs.read(identity)["runKind"] == "walk":
                runs.update(identity, stage="walk")
                await self.walk(identity, obj, directory, prepared, state)
            ending = self.conclude(identity)
        except asyncio.CancelledError:
            ending = {"state": "interrupted" if runs.closed else "cancelled", "message": t("Stopped before every combination had run.")}
        except ContractError as error:
            ending = {"state": "interrupted" if runs.closed else "cancelled", "message": t("Stopped before every combination had run.")} if error.code == "cancelled" else {
                "state": "failed", "message": error.message, "error": runs.failure(identity, error)}
        except Exception as error:  # a sweep must reach an end state whatever breaks
            failure = ContractError("internal_error", f"{type(error).__name__}: {error}", {"exceptionType": type(error).__name__})
            ending = {"state": "failed", "message": failure.message, "error": runs.failure(identity, failure)}
        finally:
            runs.tasks.pop(identity, None)
            if ending:
                record = runs.read(identity)
                # What had not finished when the sweep stopped is marked with it; combinations that ran keep their results.
                runs.update(identity, **run_flow.terminal_state(record, **ending), endedAt=int(time.time() * 1000))

    async def run_cases(self, identity, obj, directory, prepared, state):
        """Every combination that has not completed (a failed one again, when a stopped sweep goes on), each in its place in the queue."""
        record = self.runs.read(identity)
        todo = []
        for case in record["sweep"]["cases"]:
            if case["state"] == "completed":
                continue
            found = self.reusable(record, case, prepared)
            if found is None:
                todo.append(case)
            else:
                self.update_case(identity, case["index"], runId=found[0], state="completed", metrics=found[1], message=found[2])
        tasks = [asyncio.create_task(self.case(identity, obj, directory, prepared, state, case)) for case in todo]
        try:
            await asyncio.gather(*tasks)
        except BaseException:
            for task in tasks:
                task.cancel()
            await asyncio.gather(*tasks, return_exceptions=True)
            raise

    def reusable(self, record, case, prepared):
        """The result a rerun of the costs or of the capital already has: the run the sweep starts from when the value is the one it ran with, or a
        completed case of an earlier sweep from the same run with the same value. (run id, metrics, what to say) or None: only what is missing runs."""
        names = [axis["name"] for axis in record["sweep"]["axes"]]
        if record["runKind"] != "sweep" or not record.get("of") or len(names) != 1 or names[0] not in ("@costMultiple", "@capital"):
            return None
        name, value = names[0], case["params"][names[0]]
        source = self.runs.read(record["of"])
        as_set = 1 if name == "@costMultiple" else (prepared.get("runSettings") or {}).get("capital")
        if as_set is not None and float(value) == float(as_set) and source.get("metrics"):
            return source["id"], source["metrics"], f"The run itself (#{source['number']})"
        rows = self.runs.records.db.execute("SELECT content FROM records WHERE kind='qrun' AND json_extract(content,'$.objectId')=? AND json_extract(content,'$.runKind')='sweep' "
                                            "AND json_extract(content,'$.of')=? AND json_extract(content,'$.state')='completed' AND json_extract(content,'$.id')!=?",
                                            (record["objectId"], record["of"], record["id"])).fetchall()
        for row in rows:
            earlier = json.loads(row[0])
            if earlier.get("code") != source.get("code") or earlier.get("common") != source.get("common"):
                continue
            sweep = earlier.get("sweep") or {}
            if [axis["name"] for axis in sweep.get("axes") or []] != [name]:
                continue
            for other in sweep.get("cases") or []:
                if other["state"] == "completed" and other.get("runId") and other.get("metrics") and float(other["params"].get(name, "nan")) == float(value):
                    return other["runId"], other["metrics"], f"Already ran in sweep #{earlier.get('number')}"
        return None

    @contextlib.asynccontextmanager
    async def place(self, identity, label, memory):
        """A place in Quant's one queue as background work: it waits for a CPU slot and for the memory budget, behind whatever a person is waiting for."""
        async with self.machine.slot(owner=identity, label=label, interactive=False, memory=memory):
            yield

    async def case(self, sweep_id, obj, directory, prepared, state, case):
        """One combination."""
        record = self.runs.read(sweep_id)
        identity = "run-" + uuid4().hex
        async with self.place(identity, f"{obj['name']} sweep #{case['index'] + 1}", record["sweep"]["perCase"]):
            params, overrides = split(case["params"])
            extra = period_extra(overrides["period"]) if "period" in overrides else {}
            finished = await self.run_case(sweep_id, obj, directory, prepared, state, identity=identity, params={**record["params"], **params}, overrides=overrides,
                                           link={"sweepId": sweep_id, "index": case["index"]}, extra=extra,
                                           on_start=lambda: self.update_case(sweep_id, case["index"], runId=identity, state="running"))
            self.update_case(sweep_id, case["index"], runId=finished["id"], state=finished["state"], metrics=finished["metrics"] or None, message=finished["message"])

    async def run_case(self, sweep_id, obj, directory, prepared, state, *, identity, params, overrides, link, extra, scale=None, on_start=None):
        """One backtest process for a combination (or a walk-forward window): its own run record and output; the code, data and environment are the
        sweep's, and it writes the light report. Returns its record when it has ended."""
        runs = self.runs
        record = runs.read(sweep_id)
        case_directory = runs.directory(identity)
        case_directory.mkdir(parents=True)
        own = case_settings(prepared["runSettings"], overrides) if overrides and "runSettings" in prepared else None
        base = {key: value for key, value in record.items() if key not in ("of", "walk")}
        runs.records.put(RUN, {**base, "id": identity, "runKind": "case", "mode": "run", "state": "preparing", "stage": "backtest", "createdAt": int(time.time() * 1000), "number": None,
                               "params": params, "overrides": {**params, **{"@" + key: value for key, value in overrides.items()}}, "message": None, "error": None, "metrics": {},
                               "starred": False, "note": "", "exitCode": None, "endedAt": None, "sweep": link, **({"runSettings": own} if own else {})})
        runs.ctx.environments.hold(record["environment"]["preparationId"], "qrun:" + identity)
        if on_start:
            on_start()
        process, ending = None, {}
        try:
            process = await runs.launch(identity, obj, case_directory, case_prepared(prepared, overrides, scale), state, snapshot=directory, extra={"report": "light", **extra})
            await runs.finish(identity, process, case_directory)
            ending = runs.ending(identity, case_directory)
        except asyncio.CancelledError:
            ending = {"state": "interrupted" if runs.closed else "cancelled", "message": t("Stopped before the run finished.")}
            raise
        except ContractError as error:
            ending = {"state": "failed", "message": error.message, "error": {"stage": "backtest", **error.to_dict()}}
        finally:
            await runs.close_models(identity)
            if process:
                with contextlib.suppress(ContractError):
                    await asyncio.shield(runs.ctx.processes.cancel(process))
                with contextlib.suppress(ContractError):
                    await asyncio.shield(runs.ctx.processes.close(process))
            runs.processes.pop(identity, None)
            if ending:
                runs.update(identity, **ending, endedAt=int(time.time() * 1000))
        return runs.read(identity)

    # --- ending ----------------------------------------------------------------------------------------------------------------
    def ranked(self, record):
        metric = record["sweep"]["metric"]
        return [case for case in record["sweep"]["cases"] if case["state"] == "completed" and isinstance((case["metrics"] or {}).get(metric), (int, float))]

    def best(self, record):
        """The completed combination that ranks first by the sweep's metric (described, never chosen), or None."""
        metric = record["sweep"]["metric"]
        return max(self.ranked(record), key=lambda case: case["metrics"][metric], default=None)

    def conclude(self, identity):
        """The end of a sweep: failed when no combination completed (saying the first failure), else completed; the best one is described, not chosen."""
        record = self.runs.read(identity)
        cases = record["sweep"]["cases"]
        if not any(case["state"] == "completed" for case in cases):
            failed = next((case for case in cases if case["state"] == "failed"), None)
            if failed is None:
                return {"state": "cancelled", "message": t("Stopped before any combination ran.")}
            return {"state": "failed", "message": t("All {cases} combinations failed; the first one: {message}", cases=len(cases), message=failed['message']),
                    "error": {"stage": "sweep", "code": "no_result", "message": t("No combination completed."), "file": None, "line": None, "details": {"cases": len(cases), "first": failed["index"]}}}
        best = self.best(record)
        self.runs.update(identity, sweep={**record["sweep"], "bestIndex": best["index"] if best else None})
        return {"state": "completed", "message": None}

    # --- walk-forward ------------------------------------------------------------------------------------------------------------
    def work_of(self, run_id):
        path = self.runs.directory(run_id) / "output" / "strategy" / "work.json"
        return json.loads(path.read_text(encoding="utf-8")) if path.is_file() else None

    def entries_of(self, run_id):
        path = self.runs.directory(run_id) / "output" / "strategy" / "trades.parquet"
        if not path.is_file():
            return []
        table = pd.read_parquet(path)
        return [] if table.empty or "entry" not in table else [int(item) for item in table["entry"].dropna()]

    async def walk(self, identity, obj, directory, prepared, state):
        """Pick on each window from the combinations' equity, then run the picked values on the months after it, one window after another. Each
        window starts from the capital the one before ended with, so a window that fails stops the walk, naming the window and why; going on
        (`resume`) keeps the windows that ran and starts again at the first one that did not."""
        runs = self.runs
        record = runs.read(identity)
        walk = record["walk"]
        curves, entries = {}, {}
        for case in record["sweep"]["cases"]:
            if case["state"] == "completed" and case["runId"]:
                work = self.work_of(case["runId"])
                if work and work.get("equity"):
                    curves[case["index"]] = (work["equity"], work["annualization"], work["dailyRiskFree"])
                    entries[case["index"]] = self.entries_of(case["runId"])
        if not curves:
            return
        first = min(points[0][0] for points, _, _ in curves.values())
        last = max(points[-1][0] for points, _, _ in curves.values())
        cases = {case["index"]: case for case in record["sweep"]["cases"]}
        capital = prepared["runSettings"]["capital"]
        windows = [row for row in walk["windows"] if row["trades"] is not None]  # the windows that ran before the walk stopped
        windows = windows[:next((n for n, row in enumerate(windows) if row["n"] != n + 1), len(windows))]
        parts = [self.work_of(row["runId"])["equity"] for row in windows]
        previous = {"n": windows[-1]["n"], "index": windows[-1]["index"], "values": windows[-1]["values"]} if windows else None
        for number, span in enumerate(window_spans(first, last, walk["pickOn"], walk["trade"]), 1):
            if number <= len(windows):
                continue
            start, picked_end, traded_end = span
            found = await asyncio.to_thread(pick_window, curves, entries, span, walk["pickBy"])  # every combination's window statistics, off the loop
            if found is not None:
                index, stats = found
                values, kept, sharpe_picked = cases[index]["params"], None, stats["sharpe"]
            elif previous is not None:
                index, values, kept, sharpe_picked = previous["index"], previous["values"], previous["n"], None
            else:
                index, values, kept, sharpe_picked = None, {key: value for key, value in record["params"].items() if key in {item["name"] for item in record["sweep"]["axes"]}}, 0, None
            code_params, setting_overrides = split(values)
            params = {**record["params"], **code_params}
            level = parts[-1][-1][1] if parts else capital
            window_id = "run-" + uuid4().hex
            row = {"n": number, "pickedOn": [start, picked_end], "traded": [picked_end, traded_end], "index": index, "values": values, "sharpePicked": sharpe_picked,
                   "sharpeTraded": None, "returnTraded": None, "trades": None, "runId": window_id, **({"kept": kept} if kept is not None else {})}
            windows.append(row)
            runs.update(identity, walk={**walk, "windows": windows})
            extra = {"window": {"from": start, "to": traded_end, "warmUpMonths": walk["pickOn"]}, "tradeFrom": picked_end}
            async with self.place(window_id, f"{obj['name']} walk-forward #{number}", record["sweep"]["perCase"]):
                finished = await self.run_case(identity, obj, directory, prepared, state, identity=window_id, params=params, overrides=setting_overrides,
                                               link={"sweepId": identity, "window": number}, extra=extra, scale=level / capital)
            if finished["state"] != "completed":
                row.update(failed=finished["message"])
                runs.update(identity, walk={**walk, "windows": windows})
                when = "–".join(datetime.fromtimestamp(at / 1000, UTC).date().isoformat() for at in (picked_end, traded_end))
                raise ContractError("failed", t("Window {number} ({when}) did not run: {message}", number=number, when=when, message=finished['message']), {"window": number})
            work = self.work_of(window_id)
            points = work["equity"]
            stats = await asyncio.to_thread(window_stats, points, 0, 10 ** 18, work["annualization"], work["dailyRiskFree"])
            row.update(sharpeTraded=stats["sharpe"] if stats else None, returnTraded=points[-1][1] / points[0][1] - 1 if len(points) > 1 else None, trades=work["tradesTotal"])
            parts.append(points)
            previous = {"n": number, "index": index, "values": values}
            walk = {**walk, "windows": windows, "stitched": stitch(parts)}
            runs.update(identity, walk=walk)
        values_seen = [json.dumps(item["values"], sort_keys=True) for item in windows]
        runs.update(identity, walk={**walk, "windows": windows, "stitched": stitch(parts), "efficiency": efficiency(windows),
                                    "changed": sum(1 for a, b in zip(values_seen, values_seen[1:], strict=False) if a != b)})

    # --- stopped and going on --------------------------------------------------------------------------------------------------
    async def resume(self, identity):
        """Go on with a stopped sweep: every combination that did not complete is run, the failed ones again (a failure from a machine that ran
        short, or a window cut off, is not the combination's), over the code and data it already fixed. A
        walk-forward that stopped, or failed in a window, goes on from its first window that did not run."""
        runs = self.runs
        record = self.read(identity)
        if runs.closed:
            raise ContractError("unavailable", t("Quant is closing."))
        window = record["runKind"] == "walk" and record["state"] == "failed" and ((record.get("error") or {}).get("details") or {}).get("window")
        if identity in runs.tasks or (record["state"] not in ("cancelled", "interrupted") and not window):
            raise ContractError("conflict", t("Only a stopped sweep, or a walk-forward a window of which failed, can be resumed."), {"state": record["state"]})
        if record["runKind"] != "walk" and not any(case["state"] in ("cancelled", "interrupted", "queued", "running", "failed") for case in record["sweep"]["cases"]):
            raise ContractError("conflict", t("Every combination of this sweep already ran."))
        obj = {**runs.store.read(record["objectId"]), "settings": record["settings"], "entry": record["code"]["entry"]}
        from .runtime import call_attribution
        sweep = record["sweep"]
        cases = [case if case["state"] == "completed" else
                 {**case, "state": "queued", "message": None, "runId": None, "metrics": None} for case in sweep["cases"]]
        record = runs.update(identity, state="preparing", stage="environment" if record["data"] is not None else "data",
                             sweep={**sweep, "cases": cases}, endedAt=None, error=None, message=None, **call_attribution())
        self.changed(identity)
        runs.tasks[identity] = runs.ctx.start(self.execute(identity, obj, resume=True), run_record=record)
        return runs.read(identity)

    # --- reading and choosing ------------------------------------------------------------------------------------------------------
    def read(self, identity):
        record = self.runs.read(identity)
        if record["runKind"] not in ("sweep", "walk"):
            raise ContractError("invalid_input", t("This run is not a parameter sweep."))
        return record

    def is_old(self, record):
        return (record.get("sweep") or {}).get("format") != FORMAT

    def detail(self, identity, *, view=None, held=None):
        """Every combination with its parameters, state and headline numbers, the swept axes, which one is being looked at, what the machine does with
        the rest, and the pictures (a heat map or a bar per year, the cell's equity, the stitched walk-forward)."""
        record = self.read(identity)
        if self.is_old(record):
            return {"runId": identity, "objectId": record["objectId"], "state": record["state"], "old": True, "number": record.get("number")}
        sweep = record["sweep"]
        axes = sweep["axes"]
        cases = []
        for case in sweep["cases"]:
            ratio = strategy_checks.neighbour_ratio(axes, sweep["cases"], case["index"])
            cases.append({**case, "neighbours": ratio["mean"], "neighbourCount": ratio["ran"], "edge": strategy_checks.edges(axes, case["params"])})
        selected = sweep["selected"]
        best = self.best(record)
        done = sum(case["state"] in TERMINAL for case in sweep["cases"])
        machine = self.machine
        out = {"runId": identity, "objectId": record["objectId"], "state": record["state"], "metric": sweep["metric"], "axes": axes, "cases": cases, "selected": selected,
               "bestIndex": best["index"] if best else None, "done": done, "total": len(cases), "failed": sum(case["state"] == "failed" for case in cases),
               "concurrency": {"atOnce": sweep.get("atOnce"), "limit": sweep.get("limit")}, "machine": {"memory": machine.total, "budget": machine.budget, "cores": machine.cpus},
               "estimate": sweep.get("estimate"), "metrics": list(METRICS), "number": record.get("number"), "of": record.get("of"), "message": record.get("message"),
               "figures": {}}
        if record["runKind"] == "walk":
            out["walk"] = record["walk"]
        if selected is None and held is None and not any(axis["name"].startswith("@") for axis in axes):
            current = strategy_checks.cell(axes, sweep["cases"], {axis["name"]: record["params"].get(axis["name"]) for axis in axes})
            held = current["index"] if current and current["state"] == "completed" else None
        shown = selected if selected is not None else held
        out["viewed"] = shown
        if any(axis["name"] == "@period" for axis in axes):
            out["figures"]["years"] = sweep_figures.years(cases, axes)
        elif view is None or view.get("x"):
            out["figures"]["heat"] = sweep_figures.heat(cases, axes, view or {}, sweep["metric"], shown)
        if shown is not None:
            found = next((case for case in cases if case["index"] == shown), None)
            if found is not None and found["state"] == "completed" and found["runId"]:
                preview = sweep_figures.preview(self.work_of(found["runId"]), self.source_work(record), found, record.get("of"), self.number_of(record.get("of")))
                if preview:
                    out["figures"]["preview"] = preview
        if record["runKind"] == "walk" and record["walk"]["stitched"]:
            out["figures"]["walk"] = sweep_figures.walk(record["walk"], self.source_work(record), record.get("of"), self.number_of(record.get("of")), self.number_of(identity))
        return out

    def number_of(self, identity):
        if not identity:
            return None
        try:
            return self.runs.read(identity).get("number")
        except ContractError:
            return None

    def source_work(self, record):
        source = (record.get("sweep") or {}).get("source") or record.get("of")
        return self.work_of(source) if source else None

    def select(self, identity, index):
        """The combination the page shows below the heat map: its equity, by its own run. This does not pick it for the strategy."""
        record = self.read(identity)
        case = next((item for item in record["sweep"]["cases"] if item["index"] == index), None)
        if case is None:
            raise ContractError("not_found", t("This sweep has no such combination."), {"index": index})
        if case["state"] != "completed":
            raise ContractError("conflict", t("This combination did not complete ({state}); there is no result to show.", state=case['state']), {"index": index, "state": case["state"]})
        self.runs.save(record, sweep={**record["sweep"], "selected": index})
        return self.detail(identity)

    async def use_values(self, identity, index, expected_version=None):
        """Write one combination's values back as the strategy's own: the code's parameters, and the run settings the sweep varied. The sweep stays as it was."""
        record = self.read(identity)
        case = next((item for item in record["sweep"]["cases"] if item["index"] == index), None)
        if case is None:
            raise ContractError("not_found", t("This sweep has no such combination."), {"index": index})
        obj = self.runs.store.read(record["objectId"])
        saved = await self.runs.saved_source(obj)
        declared = {item["name"] for item in self.runs.store.parameters(saved)["parameters"]}
        code, overrides = split(case["params"])
        gone = [name for name in code if name not in declared]
        if gone:
            raise ContractError("conflict", t("The code no longer declares {gone}; the values cannot be written back.", gone=', '.join(gone)), {"parameters": gone})
        args = {"params": {**obj["settings"]["params"], **code}}
        run = {key: overrides[key] for key in ("fill", "capital") if key in overrides}
        if run:
            args["run"] = {**(obj["settings"].get("run") or {}), **run}
        if expected_version is not None:
            args["expectedVersion"] = expected_version
        return await self.runs.store.update_settings(obj["id"], args)
