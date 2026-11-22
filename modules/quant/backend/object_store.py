"""Quant objects: one thing, one id; its kind, folder, workspace, data settings and parameter values."""
from __future__ import annotations

import copy
import math
import re
import shutil
import time
from datetime import date
from pathlib import Path
from uuid import uuid4

from talyx_sdk import ContractError
from talyx_sdk.i18n import mark, t, tc

from ..native import styles
from ..native.object_templates import SOURCES
from ..native.strategy_presets import PRESETS
from . import object_data
from .materials import plain
from .object_params import check_values, read_parameters, read_project

KINDS = ("notebook", "factor", "model", "allocation", "strategy", "library")
CREATABLE = ("notebook", "strategy", "factor", "model", "allocation")
DEFAULT_NAMES = {"notebook": "Untitled", "strategy": "Untitled strategy", "factor": "Untitled factor", "model": "New model", "allocation": "Minimum variance"}
MODEL_RUN = {"cost": 5.0, "topN": 50}  # a model's run settings: cost a trade in bps, the N of the top-N test (no rate: every return it measures pays for itself)
DEFAULT_STRATEGY_DATA = {"symbols": [{"symbol": "okx:BTC/USDT:USDT"}], "timeframe": "1h", "years": 2}
TIMEFRAMES = ("1m", "5m", "15m", "30m", "1h", "4h", "1d")
OBJECT = "qobject"
NO_PARAMETERS = {"parameters": [], "errors": []}  # a notebook is not run as a whole, so it declares none
FOLDER = "qfolder"


def clean_name(value, label="name"):
    if not isinstance(value, str) or not value.strip() or len(value.strip()) > 120:
        raise ContractError("invalid_input", t("Choose a {label} of 1 to 120 characters.", label=label))
    return value.strip()


def parse_day(value, label):
    if not isinstance(value, str) or not re.fullmatch(r"\d{4}-\d{2}-\d{2}", value):
        raise ContractError("invalid_input", t("{label} needs a date written YYYY-MM-DD.", label=label))
    try:
        return date.fromisoformat(value)
    except ValueError as error:
        raise ContractError("invalid_input", t("{label} is not a real date.", label=label)) from error


def check_data(data, kind):
    """Data settings: exact Market instruments, a research list or the code's own rule for the instruments, one bar period and one UTC date range."""
    if set(data) - {"symbols", "list", "universe", "index", "timeframe", "since", "until", "adjustment", "referencePrices", "heldOutFrom", "fillBars"}:
        raise ContractError("invalid_input", t("Data settings hold symbols, list, universe, index, timeframe, since, until, adjustment, fillBars and heldOutFrom."))
    if data.get("fillBars") is not None:
        # Finer bars the venue matches orders on (stops and targets inside a signal bar); the chart and the signals stay on `timeframe`.
        if kind != "strategy" or data["fillBars"] not in {"1m", "5m"}:
            raise ContractError("invalid_input", t("fillBars is 1m or 5m (a strategy's fine bars for matching), or null for the signal bars."))
        if data.get("timeframe") in {"1m", "5m"} and TIMEFRAMES.index(data["fillBars"]) >= TIMEFRAMES.index(data["timeframe"]):
            raise ContractError("invalid_input", t("Fine bars must be shorter than the signal bars."))
    if 'index' in data:
        from .index_universe import check
        check(data['index'])
    if 'referencePrices' in data:
        bases = data['referencePrices']
        if kind != 'strategy' or not isinstance(bases, list) or any(value not in {'mark', 'index'} for value in bases) or len(set(bases)) != len(bases):
            raise ContractError('invalid_input', t("Strategy referencePrices is a list containing mark and/or index, without duplicates."))
    for symbol in data.get("symbols", []):
        instrument = symbol.get("instrument") if isinstance(symbol, dict) else None
        if set(symbol) != {"instrument"} or not isinstance(instrument, dict) or not instrument.get("instrumentId"):
            raise ContractError("invalid_input", t("Each symbol is {\"instrument\": <the Market instrument>} copied from a Market search, or {\"symbol\": \"Market symbol notation\"}."))
    chosen = [key for key in ("symbols", "list", "universe", "index") if data.get(key)]
    if len(chosen) > 1:
        raise ContractError("invalid_input", t("Choose the symbols one way: a list of symbols, a research list or the code's own rule."), {"chosen": chosen})
    if data.get("universe") not in {None, "code"}:
        raise ContractError("invalid_input", t("The only rule is \"code\": the code picks its own symbols."))
    if data.get("universe") and kind == "strategy":
        raise ContractError("invalid_input", t("A strategy backtests the symbols chosen here; the code cannot pick its own."))
    if data.get("timeframe") not in {None, *TIMEFRAMES}:
        raise ContractError("invalid_input", t("Choose a bar period: {periods}.", periods=', '.join(TIMEFRAMES)))
    allowed = {None, "raw"} if kind == "strategy" else {None, "raw", "adjusted", "split", "split_dividend"}
    if data.get("adjustment") not in allowed:
        raise ContractError("invalid_input", t("Strategy prices are fixed: splits and dividends for stocks, raw for other instruments.") if kind == "strategy" else t("Prices are raw or adjusted for splits and dividends."))
    since = parse_day(data["since"], "The start date") if data.get("since") else None
    until = parse_day(data["until"], "The end date") if data.get("until") and data["until"] != "latest" else None  # "latest" is the last closed bar when a run starts
    if since and until and since > until:
        raise ContractError("invalid_input", t("The end date must not be before the start date."))
    object_data.check_range(data)
    if data.get("heldOutFrom") is not None:
        # The end of the period kept out of every run and every check, until a held-out validation scores one run on it once.
        held = parse_day(data["heldOutFrom"], "The held-out start")
        if since and held <= since or until and held > until:
            raise ContractError("invalid_input", t("The held-out period starts inside the data period, after its first day."))


def check_account(account):
    if set(account) - {"capital", "fee", "leverage", "stockCommission"}:
        raise ContractError("invalid_input", t("Account settings hold capital, fee, leverage and stockCommission."))
    for key in ("capital", "leverage"):
        if key in account and (type(account[key]) not in (int, float) or account[key] <= 0):
            raise ContractError("invalid_input", t("{key} needs a positive number.", key=key))
    fee = account.get("fee")
    if fee is not None and (not isinstance(fee, dict) or set(fee) != {"maker", "taker"}
                            or any(type(fee[key]) not in (int, float) or fee[key] < 0 for key in fee)):
        raise ContractError("invalid_input", t("fee is {\"maker\": rate, \"taker\": rate} with rates of 0 or more."))
    commission = account.get('stockCommission')
    if commission is not None:
        if not isinstance(commission, dict) or set(commission) != {'rate', 'perShare', 'minimum', 'maximumRate'}:
            raise ContractError('invalid_input', t("stockCommission requires rate, perShare, minimum and maximumRate (null for uncapped)."))
        if any(value is not None and (type(value) not in (int, float) or not math.isfinite(value) or value < 0) for value in commission.values()) or any(commission[key] is None for key in ('rate', 'perShare', 'minimum')):
            raise ContractError('invalid_input', t("Stock commission values must be finite nonnegative numbers."))


# Display metadata read from the read-only strategy presets. Their executable source stays in English.
PRESET_WORDS = {
    mark("0 = long only"),
    mark("Blank when the item has only one"),
    mark("Buffer"),
    mark("Buy above"),
    mark("Buy when the signal is above this"),
    mark("By rank"),
    mark("By signal"),
    mark("Equal"),
    mark("Fast window"),
    mark("Hold the top"),
    mark("Inverse volatility"),
    mark("Invested"),
    mark("Keep a held name until it falls below the top + this"),
    mark("Largest share of one sector; 0 = no limit"),
    mark("Largest weight of one name; 0 = no limit"),
    mark("Lookback (bars)"),
    mark("Max weight"),
    mark("Min traded value"),
    mark("Rebalance every"),
    mark("Risk per trade (%)"),
    mark("Sector cap"),
    mark("Sell below"),
    mark("Sell when the signal is below this"),
    mark("Share of equity invested"),
    mark("Share of equity per buy"),
    mark("Share of the weights invested"),
    mark("Short the bottom"),
    mark("Signal"),
    mark("Signal field"),
    mark("Slow window"),
    mark("Smallest 20-day average traded value; 0 = no limit"),
    mark("Stop (ATR)"),
    mark("This many signals between rebalances"),
    mark("This many weight sets between rebalances"),
    mark("Tolerance"),
    mark("Trade only when a weight is off by more than this"),
    mark("Trade the short side"),
    mark("Trailing stop (ATR)"),
    mark("Weighting"),
    mark("Weights (library item)"),
    mark("Weights field"),
}


# A model's presets: the standard pipeline with some settings filled in (the universe and the period stay empty: the data settings are the object's).
MODEL_PRESETS = {
    "model:lgb-alpha158": {"label": mark("LightGBM on Alpha158"), "description": mark("Qlib's benchmark settings"),
                           "params": {"inputs": [{"source": "builtin", "name": "Alpha158", "ref": "alpha158"}], "algorithm": "lightgbm", "lgb_lr": 0.2, "lgb_leaves": 210, "lgb_depth": 8,
                                      "lgb_ff": 0.8879, "lgb_bf": 0.8789, "lgb_l1": 205.6999, "lgb_l2": 580.9768, "label": "Ref($open, -2) / Ref($open, -1) - 1"}},  # i18n: ignore
    "model:lstm-alpha360": {"label": mark("LSTM on Alpha360"), "description": mark("Qlib's benchmark settings"),
                            "params": {"inputs": [{"source": "builtin", "name": "Alpha360", "ref": "alpha360"}], "algorithm": "lstm", "nn_width": 64, "nn_layers": 2, "nn_dropout": 0.0,
                                       "nn_epochs": 200, "nn_lr": 0.001, "nn_stop": 20, "nn_batch": 800, "every_months": 6, "label": "Ref($open, -2) / Ref($open, -1) - 1"}},  # i18n: ignore
    "model:ridge-styles": {"label": mark("Ridge on the price styles"), "description": mark("The seven price styles as inputs, a regularised linear model"),
                           "params": {"inputs": [{"source": "builtin", "name": "Styles", "ref": "styles"}], "algorithm": "ridge"}},
}


class ObjectStore:
    def __init__(self, ctx, records, lists):
        self.ctx, self.records, self.lists = ctx, records, lists
        self.root = records.root / "objects"
        self.writing = set()
        self.last_run = None  # set by the services: object id -> its newest run's summary, or None
        self.on_strategy_pins = None  # update the library users from the saved strategy bindings
        self.on_delete = None  # set by the services: what lets go of a deleted object (the library versions it pins)

    def workspace(self):
        return self.ctx.workspace()

    def publish(self, record, event="object_changed"):
        self.ctx.publish(event, {"objectId": record["id"], "kind": record["kind"],
                                        "workspaceId": record["workspaceId"], "version": record["version"]})

    def read(self, identity):
        record = self.records.find(OBJECT, identity)
        if record is None or record.get("deleted"):
            raise ContractError("not_found", t("This Quant object does not exist."))
        return record

    def check_writer(self, identity):
        if identity in self.writing:
            raise ContractError("conflict", t("The object is being edited."), {"objectId": identity})

    def save(self, record, **changes):
        self.check_writer(record["id"])
        updated = self.records.put(OBJECT, {**record, **changes, "version": record["version"] + 1})
        if updated["kind"] == "strategy" and self.on_strategy_pins and (updated["settings"].get("inputs") != record["settings"].get("inputs")
                or updated["name"] != record["name"] and updated["settings"].get("inputs")):
            self.on_strategy_pins(updated)
        self.publish(updated)
        return updated

    def check_version(self, record, expected):
        if expected is not None and record["version"] != expected:
            raise ContractError("conflict", t("Read the current object before changing it."),
                                {"objectId": record["id"], "version": record["version"]})

    @staticmethod
    def preset(record):
        """What the list shows about a ready-made strategy, or None for anything else."""
        found = PRESETS[record["preset"]] if record["preset"] else None
        return {"id": record["preset"], "label": t(found["label"]), "description": t(found["description"]), "shortParameter": found["shortParameter"]} if found else None

    @staticmethod
    def presets():
        ready = [{"id": key, "kind": "strategy", "label": t(item["label"]), "description": t(item["description"]), "shortParameter": item["shortParameter"]} for key, item in PRESETS.items()]
        # A factor's presets are the price styles, written as formulas (one definition: native/styles.py).
        ready += [{"id": key, "kind": "model", "label": t(item["label"]), "description": t(item["description"]), "shortParameter": None} for key, item in MODEL_PRESETS.items()]
        style_labels = {"Momentum": t("Momentum"), "Reversal": t("Reversal"), "Volatility": t("Volatility"),
                        "Residual volatility": t("Residual volatility"), "Beta": t("Beta"),
                        "Liquidity": t("Liquidity"), "Size": tc("factor", "Size")}
        return ready + [{"id": "factor:" + item["style"], "kind": "factor", "label": style_labels[item["style"]], "description": t(item["measuredAs"]), "shortParameter": None,
                         "formula": item["formula"]} for item in styles.preset_formulas()]

    def summary(self, record, last=True):
        return {"id": record["id"], "kind": record["kind"], "name": record["name"], "folder": record["folder"], "preset": self.preset(record),
                "workspaceId": record["workspaceId"], "version": record["version"], "createdAt": record["createdAt"],
                "updatedAt": record["updatedAt"], "lastRun": self.last_run(record["id"]) if last else None}

    def detail(self, record):
        source = self.source_path(record)
        settings = record["settings"]
        if record["kind"] == "factor":
            from ..native.factor_work import known_settings, settings_of
            run = settings_of(known_settings(settings.get("run") or {}))  # a key an older version saved is left out, so the object still opens
            run.pop("heldOutFrom", None)
            settings = {**settings, "mode": settings.get("mode", "code" if source.is_file() else "formula"), "formula": settings.get("formula", ""), "run": run}
        elif record["kind"] == "model":
            settings = {**settings, "mode": settings.get("mode", "code" if source.is_file() else "standard"), "run": {**MODEL_RUN, **(settings.get("run") or {})}}
        elif record["kind"] == "allocation":
            settings = {**settings, "mode": settings.get("mode", "code" if source.is_file() else "standard")}
        return {**self.summary(record), "directory": record["directory"], "entry": record["entry"],
                "entryUri": source.as_uri(), "settings": settings, "hasCode": source.is_file(),
                **(NO_PARAMETERS if record["kind"] == "notebook" else self.project_parameters(record)),
                "developer": record.get("developer")}

    # navigation
    def list(self, args):
        """Objects by kind, default this workspace; `all` lifts the workspace limit. Counts cover every kind."""
        scope = None if args.get("all") else self.workspace()
        every = [item for item in self.records.list(OBJECT) if not item.get("deleted")
                 and (scope is None or item["workspaceId"] == scope)]
        folders = {(item["kind"], item["name"]) for item in self.records.list(FOLDER) if not item.get("deleted")}
        selected = [item for item in every if (args.get("kind") is None or item["kind"] == args["kind"])
                    and (args.get("folder") is None or item["folder"] == args["folder"])
                    and (not args.get("query") or args["query"].casefold() in item["name"].casefold())]
        kind_folders = {(item["kind"], item["folder"]) for item in every if item["folder"]} | folders
        return {"kinds": [{"kind": kind, "count": sum(1 for item in every if item["kind"] == kind),
                           "creatable": kind in CREATABLE} for kind in KINDS],
                "folders": [{"kind": kind, "name": name, "count": sum(1 for item in every if item["kind"] == kind and item["folder"] == name)}
                            for kind, name in sorted(kind_folders)],
                "items": [self.summary(item, last=args.get("lastRun", True)) for item in selected]}

    def register_folder(self, kind, name):
        if (self.records.find(FOLDER, f"{kind}:{name}") or {"deleted": True})["deleted"]:
            self.records.put(FOLDER, {"id": f"{kind}:{name}", "kind": kind, "name": name, "deleted": False})

    def create_folder(self, args):
        name = clean_name(args["name"], "folder name")
        if args["kind"] not in KINDS:
            raise ContractError("invalid_input", t("Choose one of the Quant kinds: {kinds}.", kinds=', '.join(KINDS)))
        self.register_folder(args["kind"], name)
        record = self.records.find(FOLDER, f"{args['kind']}:{name}")
        self.ctx.publish("object_changed", {"objectId": "", "kind": args["kind"], "workspaceId": self.workspace(), "version": 0})
        return {"kind": record["kind"], "name": record["name"]}

    def deletable_folder(self, args):
        """The folder's record when it may go: only an empty folder does; objects of any workspace count, since the folder is shared by them all."""
        held = self.folder_objects(args["kind"], args["name"])
        if held:
            raise ContractError("conflict", t("The folder {name} still holds {held} object(s); move or delete them first.", name=args['name'], held=len(held)),
                                {"kind": args["kind"], "name": args["name"], "objects": [item["name"] for item in held]})
        record = self.records.find(FOLDER, f"{args['kind']}:{args['name']}")
        if record is None or record["deleted"]:
            raise ContractError("not_found", t("There is no folder {name} in {kind}.", name=args['name'], kind=args['kind']))
        return record

    def delete_folder(self, args):
        record = self.deletable_folder(args)
        self.records.put(FOLDER, {**record, "deleted": True})
        self.ctx.publish("object_changed", {"objectId": "", "kind": args["kind"], "workspaceId": self.workspace(), "version": 0})
        return {"kind": args["kind"], "name": args["name"], "deleted": True}

    def rename_folder(self, args):
        name = clean_name(args["name"], "folder name")
        for item in self.folder_objects(args["kind"], args["from"]):
            self.save(item, folder=name)
        old = self.records.find(FOLDER, f"{args['kind']}:{args['from']}")
        if old is not None:
            self.records.put(FOLDER, {**old, "deleted": True})
        return self.create_folder({"kind": args["kind"], "name": name})

    def folder_objects(self, kind, name):
        items = [r for r in self.records.list(OBJECT, all_workspaces=True)
                 if not r.get("deleted") and r["kind"] == kind and r["folder"] == name]
        return items

    # lifecycle
    async def create(self, args, *, default_universe=True):
        kind, preset = args["kind"], args["preset"] if "preset" in args else None
        if kind not in CREATABLE:
            raise ContractError("unavailable", t("A {kind} cannot be created yet.", kind=kind), {"creatable": list(CREATABLE)})
        factor_presets = {"factor:" + item["style"]: item for item in styles.preset_formulas()}
        if kind == "model" and preset is not None and preset not in MODEL_PRESETS:
            raise ContractError("invalid_input", t("A model preset is one of: {presets}.", presets=", ".join(MODEL_PRESETS)), {"presets": list(MODEL_PRESETS)})
        if preset is not None and kind == "factor" and preset not in factor_presets and preset not in PRESETS:
            raise ContractError("invalid_input", t("A factor preset is one of: {presets}.", presets=", ".join(factor_presets)), {"presets": list(factor_presets)})
        if preset is not None and kind != "model" and (kind != "factor" or preset in PRESETS) and (kind != "strategy" or preset not in PRESETS):
            raise ContractError("invalid_input", t("A ready-made strategy is one of: {presets}.", presets=", ".join(PRESETS)), {"presets": list(PRESETS)})
        name = clean_name(args.get("name") or (factor_presets[preset]["style"] if kind == "factor" and preset else MODEL_PRESETS[preset]["label"] if kind == "model" and preset
                                               else PRESETS[preset]["label"] if preset else DEFAULT_NAMES[kind]))
        folder = clean_name(args["folder"], "folder name") if args.get("folder") else None
        if kind == "notebook":
            return await self.make(kind, name, folder, None, SOURCES[kind][1], {"data": {}, "params": {}, "account": {}})
        if kind == "factor":
            # A new factor is an empty formula box: no code file until the formula is turned into code (or code is chosen).
            # From a preset it is the style's own formula, the user's own factor from then on (no link back to the preset).
            return await self.make(kind, name, folder, None, None, {"data": await self.default_data(default_universe=default_universe), "params": {}, "account": {}, "mode": "formula",
                                                                    "formula": factor_presets[preset]["formula"] if preset else ""})
        if kind == "allocation":
            # A new allocation is TALYX's standard flow with an empty pool: daily bars, the last ten years to the latest close.
            today = date.today()
            since = today.replace(year=today.year - 10, day=min(today.day, 28))
            return await self.make(kind, name, folder, None, None, {"data": {"symbols": [], "timeframe": "1d", "since": since.isoformat(), "until": "latest"},
                                                                    "params": {}, "account": {}, "mode": "standard", "run": {}})
        if kind == "model":
            # A new model is TALYX's standard training pipeline with nothing chosen: no code file until it is written as code.
            # From a preset it is the same pipeline with those settings filled in: the user's own model from then on (no link back to the preset).
            return await self.make(kind, name, folder, None, None, {"data": {}, "params": copy.deepcopy(MODEL_PRESETS[preset]["params"]) if preset else {}, "account": {},
                                                                    "mode": "standard", "run": {}})
        # A strategy starts as the user's own copy: the empty skeleton, or a preset's code, with the data the preset (or a new strategy) comes with.
        spec = PRESETS[preset].get("data") if preset is not None else DEFAULT_STRATEGY_DATA
        data = await self.strategy_data(spec, default_universe=default_universe) if spec else {}
        return await self.make(kind, name, folder, None, SOURCES[kind][1] if preset is None else PRESETS[preset]["source"], {"data": data, "params": {}, "account": {}},
                               preset_of=preset)

    async def strategy_data(self, spec, *, default_universe=True):
        """The preset's Market identities, bar size and last `years` years to the latest close."""
        data = await self.exact_symbols({"symbols": spec["symbols"] if default_universe else []})
        today = date.today()
        try:
            since = today.replace(year=today.year - spec["years"])
        except ValueError:
            since = today.replace(year=today.year - spec["years"], day=28)
        return {"symbols": data["symbols"], "timeframe": spec["timeframe"], "since": since.isoformat(), "until": "latest"}

    async def default_data(self, *, default_universe=True):
        """A new factor starts with Market's S&P 500 identity, never a copied current member list."""
        data = {"timeframe": "1d", "since": "2016-01-01", "until": "latest"}
        if default_universe:
            data["index"] = {"instrument": await object_data.resolve_text(self.ctx, "^GSPC")}
        return data

    def taken_names(self, kind, but=None):
        return {item["name"] for item in self.records.list(OBJECT, all_workspaces=True) if not item.get("deleted") and item["kind"] == kind and item["id"] != but}

    def free_name(self, kind, name):
        """a name says which object it is in the lists and the navigation, so one kind holds each name once: a new one under a name in
        use takes the next number ("Top N by signal 2")."""
        taken = self.taken_names(kind)
        return next(candidate for candidate in (name, *(f"{name} {number}" for number in range(2, 10_000))) if candidate not in taken)

    async def make(self, kind, name, folder, preset, source, settings, preset_of=None):
        """A new object with its code file. A ready-made strategy's code is never edited: it is not opened in Developer, and each run takes it from the module."""
        identity = str(uuid4())
        name = self.free_name(kind, name)
        directory = self.root / identity
        directory.mkdir(parents=True)
        entry = SOURCES[kind][0]
        if source is not None:
            (directory / entry).write_text(source, encoding="utf-8")
        if folder:
            self.register_folder(kind, folder)
        now = int(time.time() * 1000)
        record = self.records.put(OBJECT, {"id": identity, "kind": kind, "name": name, "folder": folder, "preset": preset,
            "workspaceId": self.workspace(), "directory": str(directory), "entry": entry, "version": 1, "createdAt": now, **({"presetOf": preset_of} if preset_of else {}),
            "settings": settings, "developer": None})
        if preset is None and source is not None:
            record = self.records.put(OBJECT, {**record, "developer": await self.open_in_developer(record)})
        if kind == "strategy" and settings.get("inputs") and self.on_strategy_pins:
            self.on_strategy_pins(record)
        self.publish(record)
        return self.detail(record)

    async def copy(self, identity, name=None):
        """One's own strategy from a ready-made one (or any strategy): the code as it is now and the same settings, free to edit."""
        record = self.read(identity)
        if record["kind"] != "strategy":
            raise ContractError("invalid_input", t("Only a strategy can be copied."))
        created = await self.make("strategy", clean_name(name or record["name"] + " (copy)"), record["folder"], None, self.code(record), copy.deepcopy(record["settings"]))
        if record.get("heldOutUsed"):
            # The held-out period was looked at by the one it was copied from: the copy carries that, so looking again is still told.
            self.records.put(OBJECT, {**self.read(created["id"]), "heldOutUsed": copy.deepcopy(record["heldOutUsed"])})
        return created

    async def open_in_developer(self, record):
        opened = plain(await self.ctx.developer("developer.projects.open", {"directory": record["directory"], "name": record["name"]}))
        return None if opened is None else {"projectId": opened["projectId"], "contextId": opened["contextId"]}

    async def write_entry(self, record, relative, content):
        """Write generated source through the project's editor; Quant owns the file when no editor is installed."""
        developer = await self.open_in_developer(record)
        identity = record["id"]
        self.check_writer(identity)
        self.check_version(self.read(identity), record["version"])
        self.writing.add(identity)
        try:
            await self.write_source(record, relative, content, developer)
        finally:
            self.writing.remove(identity)
        return developer

    async def write_source(self, record, relative, content, developer):
        path = Path(record["directory"]) / relative
        if developer is None:
            from .changes import write_local
            write_local(self.records, record, path, content)
        else:
            doc = await self.ctx.call("developer.documents.open", {"source": {
                "kind":"file", "projectId":developer["projectId"], "uri":path.as_uri()}})
            if doc["conflict"] or doc["dirty"]:
                raise ContractError("conflict", t("Save or resolve the open edits in {relative} before generating source.", relative=relative))
            args = {"documentId":doc["documentId"], "expectedVersion":doc["version"], "content":content}
            edited = await self.ctx.call("developer.documents.edit", args)
            from .run_restore import save
            await save(self.ctx, edited, relative)

    def code_target(self, record):
        """The object as it will be checked and saved when its entry file is replaced."""
        if record["kind"] == "notebook":
            raise ContractError("invalid_input", t("A notebook's code is its cells: change them with write_cell."))
        if record["preset"]:
            raise ContractError("invalid_input", t("A ready-made strategy runs TALYX's code: copy it (create_object copyOf) to write your own."))
        if record["kind"] == "strategy":
            return record
        return {**record, "settings": {**record["settings"], "mode": "code"}}

    async def open(self, identity):
        record = self.read(identity)
        if record["preset"] or not self.source_path(record).is_file():
            return self.detail(record)
        developer = await self.open_in_developer(record)
        record = self.read(identity)
        if developer != record["developer"]:
            record = self.records.put(OBJECT, {**record, "developer": developer})
        return self.detail(record)

    def update(self, identity, args):
        record = self.read(identity)
        self.check_version(record, args.get("expectedVersion"))
        changes = self.metadata(record,args)
        if changes.get("folder"):
            self.register_folder(record["kind"],changes["folder"])
        return self.summary(self.save(record,**changes))

    def metadata(self,record,args):
        changes = {}
        if "name" in args:
            changes["name"] = clean_name(args["name"])
            if changes["name"] in self.taken_names(record["kind"], but=record["id"]):
                raise ContractError("conflict", t("Another {kind} is named {name}; choose a different name.", kind=record['kind'], name=changes['name']), {"name": changes["name"]})
        if "folder" in args:
            changes["folder"] = clean_name(args["folder"], "folder name") if args["folder"] else None
        return changes

    def delete(self, identity):
        self.check_writer(identity)
        record = self.read(identity)
        self.records.put(OBJECT, {**record, "deleted": True})
        if self.on_delete is not None:
            self.on_delete(identity)
        shutil.rmtree(record["directory"], ignore_errors=True)
        self.ctx.publish("object_changed", {"objectId": identity, "kind": record["kind"],
                                                   "workspaceId": record["workspaceId"], "version": record["version"] + 1})
        return {"objectId": identity, "deleted": True}

    # settings and parameters
    def names_using_list(self, list_id):
        return [item["name"] for item in self.records.list(OBJECT, all_workspaces=True)
                if not item.get("deleted") and item["settings"]["data"].get("list") == list_id]

    def source_path(self, record):
        return Path(record["directory"]) / record["entry"]

    def code(self, record):
        """The object's code: its file, except a ready-made strategy, whose code is the module's current one (the file is refreshed to match)."""
        path = self.source_path(record)
        if record["preset"]:
            source = PRESETS[record["preset"]]["source"]
            if path.read_text(encoding="utf-8") != source:
                path.write_text(source, encoding="utf-8")
            return source
        # A factor written as a formula has no code file.
        return path.read_text(encoding="utf-8") if path.is_file() else ""

    def check_short(self, record, values, data):
        """A ready-made strategy that shorts needs every symbol to be shortable: perpetuals, or stocks on a margin account (shorting allowed in the
        run settings, borrow charged a year); crypto spot cannot go short."""
        which = record["preset"] or record.get("presetOf")  # a copy of a preset keeps the preset's short parameter
        parameter = PRESETS[which]["shortParameter"] if which else None
        if parameter is None or not values[parameter]:
            return
        off = (record["settings"].get("run") or {}).get("shorting") == "off"
        if data.get('index'):
            if off:
                raise ContractError('invalid_input', t("Shorting is off in the run settings; set {parameter} to 0 or allow shorting.", parameter=parameter))
            return
        blocked = [item["instrument"]["displaySymbol"] for item in data["symbols"]
                   if item["instrument"]["marketType"] != "swap" and (off or item["instrument"]["assetClass"] not in {"equity", "fund"})]
        if blocked:
            raise ContractError("invalid_input", t("{blocked} cannot be shorted: shorting needs perpetuals or stocks with shorting allowed. Set {parameter} to 0, or choose other symbols.", blocked=', '.join(blocked), parameter=parameter),
                                {"symbols": blocked, "parameter": parameter})

    def parameters(self, content):
        try:
            return read_parameters(content)
        except SyntaxError as error:
            return {"parameters": [], "errors": [{"line": error.lineno, "message": t("line {lineno}: {msg}", lineno=error.lineno, msg=error.msg)}]}

    def project_declarations(self, record, code=None):
        """The settings declared across every Python file of the object's project (research objects); a strategy reads its entry file."""
        if record["kind"] == "strategy" or record["preset"]:
            source = self.code(record) if code is None else code
            declared = read_parameters(source, record["entry"])
            preset = record["preset"] or record.get("presetOf")
            if preset and source == PRESETS[preset]["source"]:
                for setting in declared["parameters"]:
                    for field in ("label", "help"):
                        if setting[field] in PRESET_WORDS:
                            setting[field] = t(setting[field])
                    for option in setting.get("options", []):
                        if option["label"] in PRESET_WORDS:
                            option["label"] = t(option["label"])
            return declared
        if record["kind"] == "model" and record["settings"].get("mode", "standard") == "standard":
            from .model_prep import standard_declarations
            return standard_declarations()  # the standard pipeline is TALYX's current file
        if record["kind"] == "allocation" and record["settings"].get("mode", "standard") == "standard":
            from .allocation_prep import standard_declarations
            return standard_declarations()  # the standard allocation is TALYX's current file
        from .factor_runs import project_files
        files = project_files(record["directory"])
        if code is not None:
            files[record["entry"]] = code
        return read_project(files)

    def project_parameters(self, record, code=None):
        """Check both the declaration and the value a run would actually use at that declaration."""
        declared = self.project_declarations(record, code)
        for item in declared["parameters"]:
            name = item["name"]
            value = record["settings"]["params"].get(name, item["default"])
            try:
                check_values([item], {name: value})
            except ValueError as error:
                declared["errors"].append({"file": item["file"], "line": item["line"], "message": str(error)})
        return declared

    @staticmethod
    def parameter_values(record, parameters):
        return {item["name"]: record["settings"]["params"].get(item["name"], item["default"]) for item in parameters}

    async def exact_symbols(self, data):
        """A symbol written as Market symbol notation becomes the exact Market instrument; Market owns that resolution."""
        symbols = []
        for item in data.get("symbols", []):
            if set(item) == {"symbol"} and isinstance(item["symbol"], str) and item["symbol"]:
                item = {"instrument": await object_data.resolve_text(self.ctx, item["symbol"])}
            symbols.append(item)
        return {**data, "symbols": symbols} if "symbols" in data else data

    def strategy_bindings(self, record, settings, versions, code=None, *, resolved=None):
        """Bind named versions to the parameters and model declarations the backtest actually reads."""
        from . import library_inputs, model_inputs
        from .factor_runs import project_files

        declarations = self.project_declarations(record, code)["parameters"]
        values = self.parameter_values({**record, "settings": settings}, declarations)
        named = dict(library_inputs.named_items(declarations, values))
        files = project_files(record["directory"])
        if code is not None:
            files[record["entry"]] = code
        models = {name: name for source in files.values() for name in model_inputs.declarations(source)}
        unknown = versions.keys() - set(named.values()) - models.keys()
        if unknown:
            raise ContractError("invalid_input", t("This strategy does not read {unknown}.", unknown=', '.join(sorted(unknown))))
        old = settings.get("inputs") or {}
        legacy = {pin["name"]: pin["versionId"] for pin in old if pin.get("name") and pin.get("versionId")} if isinstance(old, list) else {}
        pins = {}
        for group, sources in (("library", named), ("models", models)):
            previous = old.get(group, {}) if isinstance(old, dict) else {}
            pins[group] = {}
            for key, name in sources.items():
                prior = previous.get(key, {})
                version = versions.get(name) or (prior.get("versionId") if prior.get("name") == name else legacy.get(name))
                if version is None and resolved:
                    frozen = resolved[group].get(key)
                    if frozen and frozen["name"] == name:
                        version = frozen["versionId"]
                if version:
                    pins[group][key] = {"name": name, "versionId": version}
        if isinstance(old, dict) and "switched" in old:
            pins["switched"] = {name: history for name, history in old["switched"].items() if name in named.values() or name in models}
        return pins

    async def update_settings(self, identity, args):
        record = self.read(identity)
        self.check_version(record, args.get("expectedVersion"))
        settings = await self.checked_settings(record,args)
        self.check_version(self.read(identity),record["version"])
        return self.detail(self.save(record,settings=settings))

    async def checked_settings(self,record,args, *, code=None):
        settings = {**record["settings"]}
        if record["kind"] == "model" and "run" in args:
            run = args.pop("run")
            if not isinstance(run, dict) or set(run) - {"cost", "topN"}:
                raise ContractError("invalid_input", t("A model's run settings hold cost (bps a trade) and topN."))
            for key in ("cost", "topN"):
                if key in run and (type(run[key]) not in (int, float) or run[key] < 0 or key == "topN" and (run[key] < 1 or run[key] != int(run[key]))):
                    raise ContractError("invalid_input", (t("{key} needs a number of 0 or more (a whole number of 1 or more).", key=key)
                            if key == 'topN' else t("{key} needs a number of 0 or more.", key=key)))
            settings["run"] = {**MODEL_RUN, **(settings.get("run") or {}), **run}
        if "run" in args and record["kind"] == "strategy":
            from . import strategy_settings
            run = strategy_settings.check(args["run"])
            if "heldOutFrom" in args["run"]:
                # The held-out end is set with the run settings and kept with the data range it cuts (one place).
                held = run.pop("heldOutFrom", None)
                data = {**settings["data"], "heldOutFrom": held}
                check_data(data, "strategy")
                settings["data"] = data
            settings["run"] = run
            args = {key: value for key, value in args.items() if key != "run"}
        if "formula" in args or "run" in args:
            if record["kind"] != "factor":
                raise ContractError("invalid_input", t("Only a factor has a formula and factor run settings."))
            if "formula" in args:
                if not isinstance(args["formula"], str) or len(args["formula"]) > 4000:
                    raise ContractError("invalid_input", t("A formula is text of at most 4000 characters."))
                settings["formula"] = args["formula"]
            if "run" in args:
                from ..native.factor_work import settings_of
                try:
                    run = settings_of(args["run"])
                except (ValueError, TypeError, KeyError) as error:
                    raise ContractError("invalid_input", str(error)) from error
                run.pop("heldOutFrom", None)
                run.pop("params", None)
                settings["run"] = run
        if "data" in args:
            data = await self.exact_symbols(args["data"])
            if record["kind"] == "strategy":
                # The held-out start is set with the run settings only; the data settings show it and keep it.
                if "heldOutFrom" in data and data["heldOutFrom"] != settings["data"].get("heldOutFrom"):
                    raise ContractError("invalid_input", t("Set the held-out start in the run settings; the data settings only show it."))
                data = {key: value for key, value in data.items() if key != "heldOutFrom"}
                if "heldOutFrom" in settings["data"]:
                    # Once set (the default, the user's choice, or cleared) it stays as it is, while it still lies inside the new range;
                    # a start outside the new range is cleared.
                    held = settings["data"]["heldOutFrom"]
                    last = data.get("until") if data.get("until") != "latest" else object_data.yesterday()
                    if held is None or data.get("since") and last and data["since"] < held <= last:
                        data["heldOutFrom"] = held
            check_data(data, record["kind"])
            if data.get("list"):
                self.lists.read(data["list"])
            settings["data"] = data
        if "account" in args:
            check_account(args["account"])
            settings["account"] = args["account"]
        if "params" in args or code is not None and settings["params"]:
            declared = self.project_declarations(record, code)
            if "params" in args or not declared["errors"]:
                try:
                    check_values(declared["parameters"], args.get("params", settings["params"]))
                except ValueError as error:
                    raise ContractError("invalid_input", str(error)) from error
        if "params" in args:
            settings["params"] = args["params"]
            held = settings.get("signalRun")
            if held and args["params"].get(held["param"]):
                settings.pop("signalRun")  # a library signal chosen in its place
        if record["kind"] == "strategy" and settings.get("inputs") and ("params" in args or code is not None):
            settings["inputs"] = self.strategy_bindings(record, settings, {}, code)
        return settings

    async def start_from(self, kind, name, *, data, preset=None, run=None, params=None, keep=None):
        """The target block's entry for a model or strategy started from another block (a factor run, a model run, a library version): it goes
        through the checks the object's own settings go through (the data range, the held-out start, the run settings, the code's settings),
        so nothing the source chose is dropped on the way and nothing the target does not take is slipped in. `data` holds the held-out start
        the source used (None when it held nothing out); `keep` is what the target keeps about where it came from (a strategy's signal run or
        library pins, the factor run a model reads)."""
        created = await self.create({"kind": kind, "name": name, **({"preset": preset} if preset else {})})
        identity = created["id"]
        held = data.get("heldOutFrom")
        if kind == "strategy":
            data = {key: value for key, value in data.items() if key != "adjustment"}
            # A strategy's held-out start is set with its run settings, after the range it cuts.
            await self.update_settings(identity, {"data": {key: value for key, value in data.items() if key != "heldOutFrom"}})
            await self.update_settings(identity, {"run": {**(run or {}), "heldOutFrom": held}})
        else:
            await self.update_settings(identity, {"data": {**data, "heldOutFrom": held}})
            if run:
                await self.update_settings(identity, {"run": run})
        if params:
            record = self.read(identity)
            await self.update_settings(identity, {"params": {**(record["settings"].get("params") or {}), **params}})
        if keep:
            record = self.read(identity)
            self.save(record, settings={**record["settings"], **keep})
        return self.detail(self.read(identity))

    def effective_parameters(self, record, content=None):
        """The values a run will use: the panel's value where set, the code's default otherwise."""
        definitions = self.project_parameters(record, content)
        if definitions["errors"]:
            raise ContractError("invalid_input", definitions["errors"][0]["message"], {"errors": definitions["errors"]})
        return self.parameter_values(record, definitions["parameters"])
