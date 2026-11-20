"""Private routes and public actions of the Quant object shell: navigation, settings, runs, history and Chart."""
from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Annotated, Literal

from fastapi import APIRouter, HTTPException, Query
from fastapi.responses import FileResponse
from pydantic import BaseModel, ConfigDict, Field
from talyx_sdk import ContractError

from . import object_data
from .allocation_runs import AllocationRuns
from .common_code import CommonCode
from .factor_runs import FactorRuns
from .library import Library
from .library_loop import LibraryLoop
from .library_produce import PipelineProducer
from .library_publish import Publisher
from .model_library import ModelLibrary
from .model_runs import ModelRuns
from .notifications import Notifications
from .object_history import RunHistory
from .object_runs import TERMINAL, ObjectRuns
from .object_store import KINDS, OBJECT, ObjectStore
from .object_sweeps import Sweeps
from .packages import UserPackages
from .public_inputs import TableQuery
from .quant_home import QuantHome
from .research_bars import ResearchBars
from .research_lists import ResearchLists
from .research_reads import log_segment
from .strategy_validation import Validation
from .strategy_views import SECTIONS, StrategyViews

Kind = Literal[KINDS]


class Input(BaseModel):
    model_config = ConfigDict(extra="forbid")

    def args(self, **path):
        return {**path, **self.model_dump(exclude_unset=True)}


class ObjectCreation(Input):
    kind: Kind
    name: str | None = None
    folder: str | None = None
    preset: str | None = None
    # A library version to start from: its data settings are copied and the version is pinned.
    fromLibrary: str | None = None


class ObjectCopy(Input):
    name: str | None = None


class ObjectUpdate(Input):
    name: str | None = None
    folder: str | None = None
    expectedVersion: int | None = None


class SettingsUpdate(Input):
    data: dict | None = None
    params: dict | None = None
    account: dict | None = None
    formula: str | None = None
    run: dict | None = None
    expectedVersion: int | None = None


class ParameterSource(Input):
    content: str | None = None


class RunStart(Input):
    params: dict | None = None
    quick: bool | None = None
    # A factor's run: what kind, the formula as typed, run settings changed for it, a sweep's axes, the run a held-out validation scores.
    kind: Literal["run", "sweep", "heldout"] | None = None
    formula: str | None = None
    settings: dict | None = None
    sweep: Annotated[list[dict], Field(max_length=4)] | None = None
    of: str | None = None


class FormulaCheck(Input):
    formula: str
    objectId: str | None = None


class FormulaToCode(Input):
    formula: str


class CreateFromRun(Input):
    kind: Literal["model", "strategy"]
    sweepIndex: Annotated[int, Field(ge=0)] | None = None


class ApplyFactorRun(Input):
    expectedVersion: int | None = None
    sweepIndex: Annotated[int, Field(ge=0)] | None = None


class SweepAxis(Input):
    # A parameter of the code, or a run setting written with "@" (@costMultiple, @fill, @capital, @period), and the values it takes.
    name: str = Field(min_length=1)
    values: Annotated[list[float | str | bool | dict[str, str]], Field(min_length=1, max_length=200)]


class SweepStart(Input):
    axes: Annotated[list[SweepAxis], Field(min_length=1, max_length=8)]
    metric: Literal["sharpe", "cagr", "totalReturn", "maxDrawdown", "trades", "profitFactor", "winRate"] | None = None
    # The run the sweep starts from (its values and settings): a rerun at other costs or capital reads the run it fills.
    of: str | None = None

    def args(self, **path):
        return {**path, "axes": [{"name": axis.name, "values": axis.values} for axis in self.axes], **({"metric": self.metric} if self.metric is not None else {}),
                **({"of": self.of} if self.of else {})}


class WalkStart(SweepStart):
    pickOn: Annotated[int, Field(ge=1, le=120)] = 12
    trade: Annotated[int, Field(ge=1, le=60)] = 3
    pickBy: Literal["sharpe", "cagr", "calmar"] = "sharpe"

    def args(self, **path):
        return {**super().args(**path), "pickOn": self.pickOn, "trade": self.trade, "pickBy": self.pickBy}


class SweepUse(Input):
    index: Annotated[int, Field(ge=0)]
    expectedVersion: int | None = None


class InputSwitch(Input):
    name: str = Field(min_length=1)
    versionId: str = Field(min_length=1)


class SweepSelect(Input):
    index: Annotated[int, Field(ge=0)]


class SweepView(Input):
    x: str | None = None
    y: str | None = None
    metric: str | None = None


class RunQuery(Input):
    sweepId: str | None = None
    state: str | None = None
    starredOnly: bool | None = None
    offset: Annotated[int, Field(ge=0)] | None = None
    limit: Annotated[int, Field(ge=1, le=500)] | None = None


class RunAnnotation(Input):
    note: str | None = None
    starred: bool | None = None


class ApplyRun(Input):
    expectedVersion: int | None = None


ResultTab = Literal["returns", "ic", "industry", "style", "turnover", "costs", "event"]


class FactorResultsBuild(Input):
    # A sweep builds Results for one combination, by its index in the sweep.
    cell: Annotated[int, Field(ge=0)] | None = None


class Services:
    def __init__(self, ctx, records):
        self.lists = ResearchLists(ctx, records)
        self.store = ObjectStore(ctx, records, self.lists)
        self.lists.used_by = self.store.names_using_list
        self.common = CommonCode(ctx, records)
        self.packages = UserPackages(ctx, records, self.common, self.store)
        self.library = Library(ctx, records)
        self.library.reconcile_strategy_users(records.list(OBJECT, all_workspaces=True))
        self.store.on_strategy_pins = self.library.sync_strategy_users
        self.runs = ObjectRuns(ctx, records, self.store, self.common, self.lists, self.packages, self.library)
        self.factors = FactorRuns(ctx, records, self.store, self.runs, self.lists, self.library, self.packages, self.common)
        self.history = RunHistory(records, self.runs)
        self.sweeps = Sweeps(self.runs, queue=self.factors.queue)
        self.runs.queue = self.factors.queue
        self.strategy = StrategyViews(self.runs, self.store, self.library)
        self.validation = Validation(self.runs, self.store)
        self.store.last_run = self.history.latest
        # A deleted object no longer uses the library versions it pinned: they stop listing it and a stopped one can be archived.
        self.store.on_delete = lambda identity: self.library.pin("qobject:" + identity, None, kind=None, name=None, how=None, version_ids=[])
        self.notifications = Notifications(ctx, records)
        self.factors.notifications = self.notifications
        self.publisher = Publisher(ctx, records, self.library.store, self.runs, self.library, self.factors, self.notifications)
        self.loop = LibraryLoop(ctx, records, self.library, self.notifications, factors=self.factors)
        self.research_bars = ResearchBars(ctx, records)
        # A model's training runs (S3) and their publication; they share the factor engine, the frozen data and the run queue.
        async def run_result(identity):
            from .public_runs import summary
            return await summary(self, self.runs.read(identity))
        ctx.run_result = run_result
        self.records = records
        self.models = ModelRuns(ctx, records, self.store, self.factors, self.library, self.notifications)
        self.allocation = AllocationRuns(ctx, records, self.store, self.factors, self.notifications)
        self.allocation.library = self.library
        self.allocation.packages, self.allocation.publisher = self.packages, self.publisher
        async def strategy_equity(run_id):
            shown = self.strategy.shown(self.runs.read(run_id))
            report = await asyncio.to_thread(self.strategy.report, shown, "work")
            return report["equity"] if report is not None else []
        self.allocation.strategy_equity = strategy_equity
        self.publisher.allocations = self.allocation
        from .allocation_producer import AllocationProducer
        self.loop.allocations = AllocationProducer(ctx, self.library, self.factors.engine)
        self.model_library = ModelLibrary(ctx, records, self.models, self.library)
        self.model_library.publisher = self.publisher  # a factor published with a model is made and checked the way a factor published alone is
        self.loop.pipelines = PipelineProducer(self.model_library)
        self.home = QuantHome(self)

    async def data_defaults(self, object_id):
        record = self.store.read(object_id)
        data = record["settings"]["data"]
        if data.get("universe") == "code":
            return {"universe": "code", "instruments": [], "account": None,
                    "message": "This object's code picks its own symbols, so the venue, fees and capital defaults are known only when it runs."}
        if data.get("list"):
            data = {**data, "symbols": self.lists.members(data["list"])}
        return await object_data.defaults(self.store.ctx, {**record["settings"], "data": data})

    def run_view(self, identity):
        """The run record with its published result index and the code's location for the report area."""
        record = self.runs.read(identity)
        return {**record, 'data': object_data.public_summary(record['data']), "result": self.runs.result(record), "summary": self.history.summary(record)}

    def result_file(self, identity, name):
        record = self.runs.read(identity)
        result = self.runs.result(record)
        listed = result is not None and any(item["name"] == name for item in result["artifacts"])
        directory = (self.runs.directory(identity) / "output").resolve()
        path = (directory / name).resolve()
        if not listed or not path.is_relative_to(directory) or not path.is_file():
            raise ContractError("not_found", "This result file has not been published.")
        return path


def mount(ctx, records, router: APIRouter, services: Services):
    store, runs, history, sweeps = services.store, services.runs, services.history, services.sweeps
    factors = services.factors

    @router.get("/objects")
    async def list_objects(kind: Kind | None = None, all: bool = False, folder: str | None = None, query: str | None = None,
                           scope: Literal["workspace", "all"] | None = None):
        return services.home.objects({key: value for key, value in {"kind": kind, "all": all or scope == "all", "folder": folder, "query": query}.items()
                                      if value is not None})

    @router.post("/objects")
    async def create_object(body: ObjectCreation):
        args = body.args()
        if "fromLibrary" in args:
            return await services.home.create_from_library(args.pop("fromLibrary"), args["kind"], args.get("name"))
        return await store.create(args)

    @router.get("/strategy-presets")
    async def list_presets():
        return {"items": store.presets()}

    @router.get("/objects/{object_id}")
    async def read_object(object_id: str):
        return store.detail(store.read(object_id))

    @router.get("/objects/{object_id}/code")
    async def read_code(object_id: str):
        """The object's code as read-only text: what a ready-made strategy runs, or an object's file."""
        record = store.read(object_id)
        return {"name": record["entry"], "content": store.code(record), "readOnly": record["preset"] is not None}

    @router.post("/objects/{object_id}/copy")
    async def copy_object(object_id: str, body: ObjectCopy):
        return await store.copy(object_id, body.args()["name"] if body.name is not None else None)

    @router.post("/objects/{object_id}/open")
    async def open_object(object_id: str):
        return await store.open(object_id)

    @router.patch("/objects/{object_id}")
    async def update_object(object_id: str, body: ObjectUpdate):
        return store.update(object_id, body.args())

    @router.put("/objects/{object_id}/settings")
    async def update_settings(object_id: str, body: SettingsUpdate):
        return await store.update_settings(object_id, body.args())

    @router.post("/objects/{object_id}/parameters")
    async def read_parameters(object_id: str, body: ParameterSource):
        record = store.read(object_id)
        content = body.content if body.content is not None else store.code(record)
        return store.parameters(content)

    @router.get("/objects/{object_id}/data-defaults")
    async def data_defaults(object_id: str):
        return await services.data_defaults(object_id)

    @router.post("/objects/{object_id}/runs")
    async def start_run(object_id: str, body: RunStart):
        if store.read(object_id)["kind"] == "factor":
            return await factors.start(object_id, body.args())
        if store.read(object_id)["kind"] == "allocation":
            return await services.allocation.start(object_id, {key: value for key, value in body.args().items() if key in ("kind", "changes", "of")})
        if store.read(object_id)["kind"] == "model":
            return await services.models.train(object_id, {key: value for key, value in body.args().items() if key == "params"})
        return history.summary(await runs.start(object_id, body.args()))

    # --- a factor's Work: the formula box, the runs, the selected run, a day's values
    @router.get("/objects/{object_id}/factor-fields")
    async def factor_fields(object_id: str):
        return await factors.fields(object_id)

    @router.post("/factor-formula/check")
    async def check_formula(body: FormulaCheck):
        fields = (await factors.fields(body.objectId))["fields"] if body.objectId else None
        return {"error": factors.check(body.formula, fields)}

    @router.post("/objects/{object_id}/factor-formula/code")
    async def formula_to_code(object_id: str, body: FormulaToCode):
        return await factors.to_code(object_id, body.formula)

    @router.get("/objects/{object_id}/factor-runs")
    async def list_factor_runs(object_id: str, since: int | None = None):
        return factors.list(object_id, since)

    @router.get("/factor-runs/{run_id}")
    async def read_factor_run(run_id: str, vs: str | None = None):
        return await asyncio.to_thread(factors.work, run_id, vs)

    # --- a factor run's Results: built in the background after Work's numbers; read one tab at a time
    @router.get("/factor-runs/{run_id}/results")
    async def read_factor_results(run_id: str, tab: ResultTab | None = None, vs: Annotated[list[str], Query(max_length=4)] = [],  # noqa: B006 - FastAPI query list
                                  cell: Annotated[int, Query(ge=0)] | None = None):
        return await factors.results(run_id, tab, tuple(vs), cell)

    @router.post("/factor-runs/{run_id}/results")
    async def build_factor_results(run_id: str, body: FactorResultsBuild):
        await factors.build_results(run_id, body.cell)
        return await factors.results(run_id, None, (), body.cell)

    @router.get("/factor-runs/{run_id}/fetch")
    async def read_factor_fetch(run_id: str):
        return factors.fetch(run_id)

    @router.get("/factor-runs/{run_id}/sweep")
    async def read_factor_sweep(run_id: str):
        return factors.sweep(run_id)

    @router.get("/factor-runs/{run_id}/cross-section")
    async def read_cross_section(run_id: str, date: int | None = None):
        return await asyncio.to_thread(factors.cross_section, run_id, date, factors.read(run_id))

    @router.post("/factor-runs/{run_id}/retry")
    async def retry_factor_run(run_id: str):
        return await factors.retry(run_id)

    @router.post("/factor-runs/{run_id}/held-out")
    async def validate_held_out(run_id: str):
        return await factors.start(factors.read(run_id)["objectId"], {"kind": "heldout", "of": run_id})

    @router.post("/factor-runs/{run_id}/apply")
    async def apply_factor_run(run_id: str, body: ApplyFactorRun):
        return await factors.apply(run_id, body.expectedVersion, body.sweepIndex)

    @router.post("/factor-runs/{run_id}/create")
    async def create_from_run(run_id: str, body: CreateFromRun):
        return await factors.create_from(run_id, body.kind, body.sweepIndex)

    @router.get("/objects/{object_id}/files")
    async def project_file_list(object_id: str):
        """The object's Python files, the entry first: the code bar's file tags."""
        from .factor_runs import project_files
        record = store.read(object_id)
        names = sorted(project_files(record["directory"]))
        return {"files": sorted(names, key=lambda name: name != record["entry"])}

    @router.get("/run-queue")
    async def read_run_queue():
        return factors.queue.view()

    @router.post("/objects/{object_id}/sweeps")
    async def start_sweep(object_id: str, body: SweepStart):
        return history.summary(await sweeps.start(object_id, body.args()))

    @router.post("/objects/{object_id}/sweeps/estimate")
    async def estimate_sweep(object_id: str, body: SweepStart):
        return await sweeps.estimate(object_id, body.args())

    @router.post("/objects/{object_id}/walk-forward/estimate")
    async def estimate_walk(object_id: str, body: WalkStart):
        return await sweeps.estimate(object_id, body.args(), kind="walk")

    @router.get("/object-runs/{run_id}/sweep")
    async def read_sweep(run_id: str, query: Annotated[SweepView, Query()]):
        return sweeps.detail(run_id, view=query.model_dump(exclude_none=True) or None)

    @router.post("/object-runs/{run_id}/sweep/resume")
    async def resume_sweep(run_id: str):
        return history.summary(await sweeps.resume(run_id))

    @router.post("/object-runs/{run_id}/sweep/use")
    async def use_sweep_values(run_id: str, body: SweepUse):
        return await sweeps.use_values(run_id, body.index, body.expectedVersion)

    @router.post("/object-runs/{run_id}/sweep/select")
    async def select_sweep_case(run_id: str, body: SweepSelect):
        return sweeps.select(run_id, body.index)

    # --- a strategy's Work and Results: one run, a section of its Results, its trades, the inputs it reads, the run settings
    strategy = services.strategy

    @router.post("/objects/{object_id}/walk-forward")
    async def start_walk(object_id: str, body: WalkStart):
        return history.summary(await sweeps.start(object_id, body.args(), kind="walk"))

    @router.get("/strategy-runs/{run_id}/held-out")
    async def read_held_out_status(run_id: str):
        return await services.validation.status(run_id)

    @router.post("/strategy-runs/{run_id}/held-out")
    async def validate_strategy_held_out(run_id: str):
        return history.summary(await services.validation.held_out(run_id))

    @router.get("/strategy-runs/{run_id}")
    async def read_strategy_run(run_id: str, vs: str | None = None):
        return await asyncio.to_thread(strategy.page_run, run_id, vs)

    @router.get("/strategy-runs/{run_id}/results")
    async def read_strategy_results(run_id: str, section: Literal[SECTIONS] | None = None, vs: str | None = None):
        return await asyncio.to_thread(strategy.results, run_id, section, vs)

    @router.get("/strategy-runs/{run_id}/trades")
    async def read_strategy_trades(run_id: str, offset: Annotated[int, Query(ge=0)] = 0, limit: Annotated[int, Query(ge=1, le=500)] = 500, side: str | None = None,
                                   exit: str | None = None, losing: bool | None = None, since: int | None = None, until: int | None = None, id: int | None = None):
        return await asyncio.to_thread(strategy.trades, run_id, offset, limit, side, exit, losing, since, until, id)

    @router.get("/strategy-runs/{run_id}/orders")
    async def read_strategy_orders(run_id: str, offset: Annotated[int, Query(ge=0)] = 0, limit: Annotated[int, Query(ge=1, le=500)] = 500, status: str | None = None):
        return await asyncio.to_thread(strategy.orders, run_id, offset, limit, status)

    @router.get("/strategy-runs/{run_id}/log")
    async def read_strategy_log(run_id: str, offset: Annotated[int, Query(ge=0)] = 0, limit: Annotated[int, Query(ge=1, le=1000)] = 1000, kind: str | None = None, trade: int | None = None, since: int | None = None):
        return await asyncio.to_thread(strategy.log, run_id, offset, limit, kind, trade, since)

    @router.get("/strategy-runs/{run_id}/rebalances")
    async def read_strategy_rebalances(run_id: str, filter: Literal["changed", "unfilled", "all"] = "changed", at: int | None = None,
                                       offset: Annotated[int, Query(ge=0)] = 0, limit: Annotated[int, Query(ge=1, le=200)] = 200,
                                       since: int | None = None, until: int | None = None):
        return await asyncio.to_thread(strategy.rebalances, run_id, filter, at, offset, limit, since, until)

    @router.get("/objects/{object_id}/strategy-runs")
    async def list_strategy_runs(object_id: str, since: int | None = None):
        return await asyncio.to_thread(strategy.runs, object_id, since)

    @router.get("/objects/{object_id}/inputs")
    async def read_inputs(object_id: str):
        return {"items": strategy.inputs(object_id)}

    @router.post("/objects/{object_id}/inputs/switch")
    async def switch_input(object_id: str, body: InputSwitch):
        return strategy.switch_input(object_id, body.name, body.versionId)

    @router.get("/objects/{object_id}/run-settings")
    async def read_run_settings(object_id: str):
        return await strategy.run_settings(object_id)

    @router.get("/objects/{object_id}/runs")
    async def list_runs(object_id: str, query: Annotated[RunQuery, Query()]):
        store.read(object_id)
        return await asyncio.to_thread(history.list, object_id, query.args())

    @router.get("/object-runs/{run_id}")
    async def read_run(run_id: str):
        return await asyncio.to_thread(services.run_view, run_id)

    @router.post("/object-runs/{run_id}/cancel")
    async def cancel_run(run_id: str):
        if runs.read(run_id).get("objectKind") == "factor":
            return await factors.cancel(run_id)
        if services.models.owns(run_id):
            return await services.models.stop(run_id)
        return history.summary(await runs.cancel(run_id))

    @router.patch("/object-runs/{run_id}")
    async def annotate_run(run_id: str, body: RunAnnotation):
        if runs.read(run_id).get("objectKind") == "factor":
            return factors.annotate(run_id, body.args())
        if services.models.owns(run_id):
            return services.models.annotate(run_id, body.args())
        return history.annotate(run_id, body.args())

    @router.get("/object-runs/{run_id}/compare/{other_id}")
    async def compare_runs(run_id: str, other_id: str):
        return history.compare(run_id, other_id)

    @router.post("/object-runs/{run_id}/apply")
    async def apply_run(run_id: str, body: ApplyRun):
        if runs.read(run_id).get("objectKind") == "factor":
            return await factors.apply(run_id, body.args().get("expectedVersion"))
        if runs.read(run_id).get("allocation"):
            return await services.allocation.apply(run_id, body.expectedVersion)
        if services.models.owns(run_id):
            return await services.models.apply(run_id, body.args().get("expectedVersion"))
        return await runs.apply(run_id, body.args().get("expectedVersion"))

    @router.get("/object-runs/{run_id}/source")
    async def read_source(run_id: str, name: str):
        record = runs.read(run_id)
        if record["code"] is None:
            raise ContractError("not_found", "This run never fixed its code.")
        root = Path(record["code"]["snapshotPath"]).resolve()
        path = (root / name).resolve()
        if not path.is_relative_to(root) or not path.is_file():
            raise ContractError("not_found", "This fixed source file is unavailable.")
        return {"name": name, "content": path.read_text(encoding="utf-8"), "readOnly": True, "digest": record["code"]["digest"]}

    @router.get("/object-runs/{run_id}/logs/{stream}")
    async def read_log(run_id: str, stream: Literal["stdout", "stderr", "environment_stdout", "environment_stderr"], offset: Annotated[int, Query(ge=0)] = 0):
        record = runs.read(run_id)
        if stream.startswith("environment_"):
            selected = (record["environment"] or {}).get("stdoutPath" if stream == "environment_stdout" else "stderrPath")
            path = Path(selected) if selected else None
        else:
            path = runs.directory(run_id) / "output" / (stream + ".log")
        if path is None:
            return {"text": "", "nextOffset": 0, "eof": True}
        return log_segment(path, offset, final=record["state"] in TERMINAL)

    @router.post("/object-runs/{run_id}/table")
    async def read_table(run_id: str, body: TableQuery):
        from .result_tables import read_result_table
        page = await asyncio.to_thread(read_result_table, services.result_file(run_id, body.name), body.args())
        return {**page, "assetLabels": runs.asset_labels(runs.read(run_id))}

    @router.get("/object-runs/{run_id}/files/{name:path}")
    async def report_file(run_id: str, name: str):
        try:
            return FileResponse(services.result_file(run_id, name))
        except ContractError as error:
            raise HTTPException(404, str(error)) from None



def with_progress(view, record, strategy):
    """A strategy run in flight says how far it has simulated and its equity so far (the notebook's backtest block draws it); anything else is the view as it is."""
    progress = strategy.progress(record) if record.get("objectKind") == "strategy" else None
    return {**view, "progress": progress} if progress is not None else view


def handle_actions(ctx, services: Services):
    from . import public_objects, public_runs
    from .public_pages import page
    from .public_settings import params_in, timestamp

    public_objects.handle_objects(ctx, services)
    store, runs, factors, models = services.store, services.runs, services.factors, services.models
    allocations, sweeps = services.allocation, services.sweeps

    async def list_runs(args, call):
        store.read(args["objectId"])
        workspace = store.workspace()
        rows = [r for r in runs.records.list("qrun") if r["objectId"] == args["objectId"]
                and (args.get("scope") == "all" or r["workspaceId"] == workspace)
                and (not args.get("runKind") or public_runs.run_kind(r) == args["runKind"])
                and (not args.get("state") or r["state"] == args["state"])
                and ("starred" not in args or bool(r.get("starred")) == args["starred"])
                and (not args.get("batch") or r.get("batch") == args["batch"])]
        rows.sort(key=lambda r:(r["createdAt"],r["id"]), reverse=True)
        return page([{"runId":r["id"],"number":r.get("number"),"workspaceId":r["workspaceId"],"state":r["state"],
                      "runKind":public_runs.run_kind(r),"createdAt":timestamp(r["createdAt"]),"numbers":public_runs.numbers(r),
                      "tested":public_runs.tested(r.get("data") or {}, (r.get("settings") or {}).get("data",{}).get("timeframe","1d")),
                      "values":r.get("values"),"batch":r.get("batch"),"note":r.get("note"),"starred":bool(r.get("starred"))}
                     for r in rows],args)

    async def start_run(args, call):
        identity = args["objectId"]
        kind = store.read(identity)["kind"]
        if kind == "model":
            record = (await models.train(identity))["runs"][0]
        elif kind == "allocation":
            record = await allocations.start(identity,{})
        elif kind == "factor":
            record = await factors.start(identity,{})
        elif kind == "strategy":
            record = await runs.start(identity,{})
        else:
            raise ContractError("invalid_input","A notebook runs one cell at a time.")
        return public_runs.receipt(record)

    async def list_presets(args, call):
        return {"items": store.presets()}

    async def check_parameters(args, call):
        store.read(args["objectId"])
        return store.parameters(args["code"])

    async def select_sweep_cell(args, call):
        detail = sweeps.select(args["runId"], args["cell"])
        return {"runId": args["runId"], "selected": detail["selected"]}

    async def promote_model_run(args, call):
        return public_runs.receipt(await models.run_next(args["runId"]))

    async def reset_model(args, call):
        obj = models.reset(args["objectId"])
        return await public_objects.summary(services, store.read(obj["id"]))

    def strategy_axes(axes):
        return [{**axis, "values": [{"since": value["start"], "until": value["end"]} if isinstance(value, dict)
                                     and value.keys() == {"start", "end"} else value for value in axis["values"]]}
                if axis["name"] == "@period" else axis for axis in axes]

    async def estimate_sweep(args, call):
        return await sweeps.estimate(args["objectId"], {**args, "axes": strategy_axes(args["axes"])}, args.get("kind", "sweep"))

    async def start_sweep(args, call):
        identity = args["objectId"]
        obj = store.read(identity)
        kind = obj["kind"]
        if kind != "strategy" and ("metric" in args or "of" in args):
            raise ContractError("invalid_input","metric and of apply to strategy sweeps.")
        axes = args["axes"]
        if kind == "strategy":
            # A period axis takes the public start and end; the sweep keeps the data settings' since and until.
            axes = strategy_axes(axes)
            record = await sweeps.start(identity,{**args, "axes": axes})
        elif kind == "factor":
            record = await factors.start(identity,{"kind":"sweep","sweep":axes})
        elif kind == "model":
            record = (await models.train(identity,{"vary":axes}))["runs"][0]
        elif kind == "allocation":
            normalized = []
            for axis in axes:
                values = [params_in(obj,{axis["name"]:v}) for v in axis["values"]]
                normalized.append({"name":next(iter(values[0])), "values":[next(iter(v.values())) for v in values]})
            record = (await allocations.start_sweep(identity,{"axes":normalized}))["runs"][0]
        else:
            raise ContractError("invalid_input","A notebook has no parameter sweep.")
        return public_runs.receipt(record)

    async def start_walk_forward(args, call):
        return public_runs.receipt(await sweeps.start(args["objectId"], {**args, "axes": strategy_axes(args["axes"])}, kind="walk"))

    async def plan_held_out(args, call):
        identity = args["runId"]
        record = runs.read(identity)
        kind = record["objectKind"]
        if kind == "strategy":
            found = await services.validation.status(identity)
        elif kind == "allocation":
            found = await allocations.held_status(record["objectId"], identity)
        elif kind == "model":
            found = models.held_out_plan(identity)
            found = {**found, "from": timestamp(found["from"])[:10], "to": timestamp(found["to"])[:10]}
        else:
            data = record["settings"]["data"]
            if not data.get("heldOutFrom"):
                raise ContractError("no_held_out_period", "Set run.heldOutFrom before running.")
            found = {"from": data["heldOutFrom"], "to": data.get("until"), "uses": factors.uses_of(record)}
        return {**{k: v for k, v in found.items() if k not in {"from", "to"}}, "start": found["from"], "end": found["to"]}

    # The first validation has to freeze or look up settings before it can save a run.
    # Simultaneous requests share that submission before subsequent requests can find the saved run.
    validating = {}

    async def validate_held_out(args, call):
        identity = args["runId"]
        if identity not in validating:
            task = asyncio.create_task(validate_once(identity))
            validating[identity] = task
            task.add_done_callback(lambda done: validating.pop(identity, None))
        return await asyncio.shield(validating[identity])

    async def validate_once(identity):
        record = runs.read(identity)
        earlier = runs.records.held_out_for(identity)
        if earlier is not None:
            return public_runs.receipt(earlier)
        kind = record["objectKind"]
        if kind == "strategy":
            found = await services.validation.held_out(identity)
        elif kind == "factor":
            found = await factors.start(record["objectId"],{"kind":"heldout","of":identity})
        elif kind == "allocation":
            found = await allocations.start(record["objectId"],{"kind":"heldout","of":identity})
        else:
            found = await models.held_out(identity)
        return public_runs.receipt(found)

    async def read_run(args, call):
        return await public_runs.read(services, args)

    async def read_run_table(args, call):
        return await public_runs.rows(services,ctx,args)

    async def resume_run(args, call):
        identity = args["runId"]
        if models.owns(identity):
            result = await models.resume(identity,args)
        else:
            if "epoch" in args or "override" in args:
                raise ContractError("invalid_input","epoch and override apply to training.")
            record = runs.read(identity)
            if record["objectKind"] == "factor":
                return public_runs.receipt(await factors.retry(identity))
            if record["objectKind"] != "strategy" or record["runKind"] not in ("sweep", "walk"):
                raise ContractError("invalid_input","resume_run continues a model run, or a strategy sweep or walk-forward.")
            result = await sweeps.resume(identity)
        return public_runs.receipt(result)

    async def cancel_run(args, call):
        identity = args["runId"]
        kind = runs.read(identity)["objectKind"]
        if services.model_library.owns(identity):
            await services.model_library.cancel(identity)
        elif kind == "factor":
            await factors.cancel(identity)
        elif kind == "model":
            await models.stop(identity)
        elif kind == "allocation":
            await allocations.stop(identity)
        else:
            await runs.cancel(identity)
        return public_runs.receipt(runs.read(identity))

    async def apply_run(args, call):
        identity, cell, version = args["runId"], args.get("cell"), args.get("expectedVersion")
        record = runs.read(identity)
        kind = record["objectKind"]
        if cell is not None and kind in {"model","allocation"}:
            raise ContractError("invalid_input","Choose the batch member's runId to apply its values.")
        if kind == "factor":
            detail = await factors.apply(identity,version,cell)
        elif kind == "allocation":
            detail = await allocations.apply(identity,version)
        elif kind == "model":
            detail = await models.apply(identity,version)
        elif cell is not None:
            detail = await sweeps.use_values(identity,cell,version)
        else:
            detail = await runs.apply(identity,version)
        result = await public_objects.summary(services,store.read(record["objectId"]))
        if detail.get("restore"):
            result["restore"] = detail["restore"]
        return result

    async def update_run(args, call):
        identity = args["runId"]
        kind = runs.read(identity)["objectKind"]
        changes = {k:v for k,v in args.items() if k != "runId"}
        if kind == "factor":
            return factors.annotate(identity,changes)
        if kind == "model":
            return models.annotate(identity,changes)
        return services.history.annotate(identity,changes)

    async def build_results(args, call):
        identity = args["runId"]
        record = runs.read(identity)
        if record["objectKind"] == "factor":
            await factors.build_results(identity,args.get("cell"))
        elif record["objectKind"] == "model" and "cell" not in args:
            await models.portfolio(identity)
        else:
            raise ContractError("invalid_input","Only a factor, a factor sweep cell or a model can build a report.")
        return public_runs.receipt(record)

    async def validate_factor_formula(args, call):
        fields = (await factors.fields(args["objectId"]))["fields"] if args.get("objectId") else None
        return {"error":factors.check(args["formula"],fields)}

    async def delete_object(args, call):
        return store.delete(args["objectId"])

    async def create_object_folder(args, call):
        return store.create_folder(args)

    async def rename_object_folder(args, call):
        return store.rename_folder(args)

    async def delete_object_folder(args, call):
        return store.delete_folder(args)

    for fn in (list_presets,check_parameters,select_sweep_cell,promote_model_run,reset_model,estimate_sweep,list_runs,start_run,start_sweep,start_walk_forward,plan_held_out,validate_held_out,read_run,read_run_table,
               resume_run,cancel_run,apply_run,update_run,build_results,validate_factor_formula,delete_object,
               create_object_folder,rename_object_folder,delete_object_folder):
        ctx.handlers[fn.__name__] = fn
