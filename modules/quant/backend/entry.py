"""Quant's declarations: its public actions (typed by ``models``), events, settings, trigger streams, what it
implements for Developer, the panel's private routes (unchanged: the routers in ``*_routes``), and starting up.
The contract is generated from this file.

Every action, route and stream says which workspace it serves (``runtime.WORKSPACE``) before Quant's code runs;
Quant's services read it from there, and their background work keeps it."""
from __future__ import annotations

import asyncio
import contextlib
import importlib
from collections.abc import AsyncIterator
from typing import Any

from talyx_sdk import ContractError
from talyx_sdk.declare import Module
from talyx_sdk.i18n import t
from talyx_sdk.process import Call, Context

from vocabulary.python.types import TableFile, TablePage

from . import changes, data_formats
from . import models as m
from . import public_inputs as i
from . import public_results as r
from . import results as out
from .notifications import DECLARED
from .runtime import CALL, WORKSPACE, Quant

module = Module("quant")
module.data_format(2, {2: data_formats.benchmarks_chosen})
# Tasks that run as long as the process; held here so they are not collected.
BACKGROUND: set[asyncio.Task] = set()
quant: Quant | None = None      # the platform as Quant uses it, set when the process starts
state: dict = {}                # the running services: shell, notebooks, triggers, language


for item in DECLARED:
    module.setting(item["id"], bool, True, title=item["title"], group="Notifications: " + item["group"],
                   description=item["description"])


async def serve(name: str, input, call: Call) -> Any:
    """Run Quant's handler for a public action, for the caller's workspace."""
    if quant is None or name not in quant.handlers:
        raise ContractError("unavailable", t("Quant is starting"))
    WORKSPACE.set(call.workspace_id)
    CALL.set(call)
    with changes.recording(state["records"], call, name):
        return await quant.handlers[name](input.model_dump(mode="json", exclude_unset=True, by_alias=True), call)


@module.changes
class QuantChanges:
    @staticmethod
    async def list(call_ids, run_id):
        return await changes.listed(state["shell"], call_ids, run_id)

    @staticmethod
    async def revert(call_id, direction):
        await changes.revert(state["shell"], call_id, direction)


# ── what Developer's editor gets ────────────────────────────────────────────────────────────────────────────────────
async def _language(request, call):
    return await state["language"].invoke(request, call)

module.implement("developer.development_support", "source_language", actions={"invoke": _language})


# ── trigger streams: research and library facts of a workspace, from a cursor ──────────────────────────────────────
@module.stream(cursor=True, trigger=True)
async def research_facts(params: m.Empty, call: Call, cursor=None) -> AsyncIterator[dict[str, Any]]:
    """Research facts of the caller's workspace (a run completed, failed or was published), from a cursor."""
    async for item in state["triggers"].follow("research", call.workspace_id, cursor):
        yield item


@module.stream(cursor=True, trigger=True)
async def library_facts(params: m.Empty, call: Call, cursor=None) -> AsyncIterator[dict[str, Any]]:
    """Library facts of the caller's workspace (a version failed, missed periods, weakened or recovered), from a
    cursor."""
    async for item in state["triggers"].follow("library", call.workspace_id, cursor):
        yield item


@module.on_start
async def start(ctx: Context) -> None:
    global quant
    from pathlib import Path

    # Importing Quant's services (pandas, the web framework's models) takes most of a second: a worker thread does it,
    # so the event loop never stops for it; the imports below then find them loaded.
    await asyncio.to_thread(_import_services)
    from .allocation_routes import handle_allocation_actions
    from .development_support import install as install_development_support
    from .library_routes import handle_library_actions
    from .model_routes import handle_model_actions
    from .notebook_routes import NotebookServices, handle_notebook_actions
    from .object_routes import Services, handle_actions
    from .triggers import TriggerFeed
    from .work_records import WorkRecords

    quant = Quant(ctx, await ctx.platform.storage())
    records = WorkRecords(Path(quant.data_dir))
    shell = Services(quant, records)
    notebooks = NotebookServices(quant, records, shell.store, shell.common, shell.packages)
    notebooks.runs = shell.runs
    shell.notebooks = notebooks
    state.update(shell=shell, notebooks=notebooks, records=records, triggers=TriggerFeed(quant, records),
                 language=install_development_support(quant, records, shell.store, shell.common, shell.packages))
    handle_model_actions(quant, shell)
    handle_allocation_actions(quant, shell)
    handle_actions(quant, shell)
    handle_library_actions(quant, shell)
    handle_notebook_actions(quant, notebooks)
    shell.loop.start()
    # Runs Quant stopped while they trained continue from their finished windows, in their workspaces once open.
    BACKGROUND.add(asyncio.create_task(follow_workspaces(shell.models, await open_workspaces(ctx, shell.models))))

    async def sweep():
        while True:
            with contextlib.suppress(OSError):
                quant.sweep_handoff()
            await asyncio.sleep(3600)
    BACKGROUND.add(asyncio.create_task(sweep()))


def _import_services() -> None:
    for name in ("allocation_routes", "development_support", "library_routes", "model_routes", "notebook_routes",
                 "object_routes", "triggers", "work_records"):
        importlib.import_module(f".{name}", __package__)


async def open_workspaces(ctx: Context, models):
    """Training a restart stopped belongs to the workspace it was started in: the runs of each open workspace resume
    now, as its background work; the others' when their workspace opens. The changes are subscribed before the open
    workspaces are listed, so one opening in between is not missed; they are returned for ``follow_workspaces``."""
    changes = await ctx.subscribe("platform.workspace.changes")
    for workspace in await ctx.platform.open_workspaces():
        models.recover(workspace)
    return changes


async def follow_workspaces(models, changes) -> None:
    async for change in changes:
        if change["kind"] == "opened" and change["workspaceId"] not in models.recovered:
            models.recover(change["workspaceId"])


@module.on_copy_workspace
async def copy_workspace(source: str, copy: str) -> None:
    from . import workspace
    await asyncio.to_thread(workspace.copy_workspace, state["records"], state["shell"].library, source, copy)


@module.on_delete_workspace
async def delete_workspace(workspace_id: str, *, preview: bool) -> list[str]:
    from . import workspace
    return await asyncio.to_thread(workspace.delete_workspace, state["records"], state["shell"].library,
                                   workspace_id, preview)


@module.private_routes
def routes(ctx: Context):
    """The Quant panel's own requests: the routers Quant had (162 routes) and the notebook kernel's WebSocket,
    unchanged; each request serves the workspace the Host names."""
    from fastapi import APIRouter, FastAPI
    from fastapi.responses import JSONResponse

    from .allocation_routes import mount_allocations
    from .library_routes import mount_library
    from .model_routes import mount_models
    from .notebook_routes import mount_notebooks
    from .object_routes import mount

    shell, notebooks = state["shell"], state["notebooks"]
    router = APIRouter()
    @router.post("/workspace/adopt")
    async def adopt(value: dict):
        from .workspace import translate
        ids = dict(state["records"].db.execute("SELECT original,copy FROM workspace_copies WHERE workspace=?",
                                               (quant.workspace(),)))
        return translate(value, ids)
    mount(quant, state["records"], router, shell)
    mount_models(router, shell)
    mount_allocations(router, shell)
    mount_library(router, shell)
    mount_notebooks(router, notebooks)
    app = FastAPI()
    app.include_router(router)

    @app.exception_handler(ContractError)
    async def failed(_request, error: ContractError):
        status = {"not_found": 404, "conflict": 409, "invalid_input": 400, "permission_denied": 403}.get(error.code, 500)
        return JSONResponse({"error": error.to_dict()}, status_code=status)

    async def workspace_of(scope, receive, send):
        if scope["type"] in ("http", "websocket"):
            headers = dict(scope.get("headers") or [])
            WORKSPACE.set((headers.get(b"x-talyx-workspace") or b"").decode() or None)
            CALL.set(None)
        await app(scope, receive, send)
    return workspace_of


module.event("research_completed", m.ResearchCompletedEvent, "research_completed")
module.event("object_changed", m.ObjectChangedEvent, "A Quant object or folder changed.")
module.event("object_run_changed", m.ObjectRunChangedEvent, "A Quant object run changed state or stage.")
module.event("library_changed", m.LibraryChangedEvent, "A library item or version was added, updated, changed state, used or delisted.")
module.event("packages_changed", m.PackagesChangedEvent, "A package installation ended: the environments notebooks and strategies run in may have changed, so a running kernel may need a restart.")
module.event("research_lists_changed", m.ResearchListsChangedEvent, "A research list was created, changed or deleted.")
module.event("library_update_failed", m.LibraryUpdateFailedEvent, "library_update_failed")


@module.action(effects=['read'],
               examples=[{'kind': 'strategy'},
                         {'scope': 'all', 'query': 'momentum'}])
async def list_objects(input: i.ListObjects, call: Call) -> r.Objects:
    """List research objects (strategies, factors, models, allocations, notebooks), each with its representative run
    (published, else starred, else the newest successful) and that run's numbers as fractions. Lists this workspace;
    scope "all" lists every workspace. Narrow by kind, folder or a name query (case-insensitive substring)."""
    return await serve("list_objects", input, call)


@module.action(effects=['read'],
               errors=['not_found'],
               examples=[{'objectId': '<objectId>'},
                         {'objectId': '<objectId>', 'part': 'options'},
                         {'objectId': '<objectId>', 'part': 'code'}])
async def read_object(input: i.ReadObject, call: Call) -> r.ObjectSummary | r.ObjectOptions | r.CodePage | r.Fields:
    """Read one object by objectId, from any workspace. part "summary" (default): name, version, the saved settings
    {data, params, run} in the shape update_object takes (only what was set), the effective values the next run uses,
    codeErrors (what the code's check finds: file, line, message), pinned library inputs, the representative and last
    run. part "options": every settable item with type, range, default and unit, the code's errors, and what the kind
    offers (strategy run settings and library inputs, model algorithms and inputs, allocation objectives, factor
    fields and functions). part code reads paged source. part notebook reads the whole model and document version.
    part dataDefaults previews venue, fees and account defaults."""
    return await serve("read_object", input, call)


@module.action(effects=['write'],
               errors=['invalid_input'],
               examples=[{'kind': 'strategy', 'name': 'BTC 20/100 crossover', 'preset': 'crossover',
                          'data': {'symbols': ['binance:BTC/USDT'], 'timeframe': '1d', 'start': '2020-01-01'},
                          'params': {'fast': 20, 'slow': 100}},
                         {'kind': 'factor', 'name': '5-day reversal', 'formula': '-($close/Ref($close,5)-1)',
                          'data': {'index': {'symbol': '^GSPC'}, 'timeframe': '1d', 'start': '2018-01-01'},
                          'run': {'holding': 5}},
                         {'kind': 'allocation', 'name': 'Risk parity',
                          'data': {'symbols': ['SPY', 'TLT', 'GLD'], 'timeframe': '1d', 'start': '2016-01-01'},
                          'params': {'objective': 'risk_parity', 'maxWeightPct': 40}},
                         {'kind': 'strategy', 'copyOf': '<objectId>', 'name': 'Crossover with shorts',
                          'params': {'shorts': True}},
                         {'kind': 'model', 'fromRun': '<runId>'},
                         {'kind': 'strategy', 'name': 'RSI reversion', 'code': '<the whole strategy.py>',
                          'data': {'symbols': ['binance:BTC/USDT'], 'timeframe': '1d', 'start': '2022-01-01'}}])
async def create_object(input: i.NewObject, call: Call) -> r.ObjectSummary:
    """Create a strategy, factor, model, allocation or notebook and, in the same call, set its code, data, params, run,
    formula and libraryInputs (checked exactly as update_object checks them; nothing is created if they are refused).
    Start empty, from a preset, or from one source: copyOf (a strategy), fromRun, fromVersion or fromNotebook.
    Creating does not run it: start_run {objectId} next. Returns the object as read_object shows it; from a notebook
    also notTaken (what the cells use that the object does not take)."""
    return await serve("create_object", input, call)


@module.action(effects=['write'],
               errors=['conflict', 'invalid_input', 'not_found'],
               examples=[{'objectId': '<objectId>', 'params': {'slow': 150}},
                         {'objectId': '<objectId>', 'data': {'symbols': {'add': ['binance:ETH/USDT'], 'remove': ['binance:BTC/USDT']}}},
                         {'objectId': '<objectId>', 'data': {'start': '2018-01-01', 'end': None}},
                         {'objectId': '<objectId>', 'run': {'heldOutFrom': '2024-11-01', 'costs': {'feePct': 0.05}}},
                         {'objectId': '<objectId>', 'formula': '-($close/Ref($close,10)-1)'},
                         {'objectId': '<objectId>', 'libraryInputs': {'Momentum': '<versionId>'}},
                         {'objectId': '<objectId>', 'code': '<the whole file>'}])
async def update_object(input: i.EditObject, call: Call) -> r.ObjectSummary:
    """Change an object's name, folder, settings or code. Changes merge: given keys replace, null resets a key to its
    default (params null resets every param), absent keys stay. A given array replaces the old one; data.symbols and
    params.inputs also take {add, remove}. Giving one of data.symbols, index, list or universe clears the other three.
    code replaces the code file's whole text and is checked without running: codeErrors lists each error with its
    file and line; fix them before start_run. A key the object's kind does not take is refused with invalid_input
    naming it. Returns the object as read_object shows it, plus changed [{path, from, to}]. Nothing runs: start_run
    after."""
    return await serve("update_object", input, call)


@module.action(effects=['delete', 'write'],
               target='objectId',
               errors=['not_found'],
               examples=[{'objectId': '<objectId>'}])
async def delete_object(input: m.DeleteObjectInput, call: Call) -> out.ObjectDeleted:
    """Delete a Quant object (a notebook, factor, model, allocation or strategy): its code and settings go, and the
    library versions it pinned no longer list it among their users."""
    return await serve("delete_object", input, call)


@module.action(effects=['write'],
               errors=['not_found'],
               examples=[{'objectId': '<objectId>'},
                         {'objectId': '<objectId>', 'formula': 'Mean($close, 5) / Mean($close, 20) - 1'}])
async def write_as_code(input: i.WriteAsCode, call: Call) -> r.ObjectSummary:
    """Turn a formula factor, a standard model or a standard allocation into editable Python code in the object's
    project; from then on the object runs that code. For a factor, formula converts that formula instead of the saved
    one. Other objects are refused."""
    return await serve("write_as_code", input, call)


@module.action(effects=['write'],
               errors=['invalid_input'],
               examples=[{'kind': 'factor', 'name': 'Momentum'}])
async def create_object_folder(input: m.CreateObjectFolderInput, call: Call) -> out.Folder:
    """Create a folder for one kind of object; folders are shared by every workspace. Put an object in it with
    update_object {objectId, folder}."""
    return await serve("create_object_folder", input, call)


@module.action(effects=['write'],
               errors=['conflict', 'invalid_input'],
               examples=[{'kind': 'factor', 'from': 'Momentum', 'name': 'Trend'}])
async def rename_object_folder(input: m.RenameObjectFolderInput, call: Call) -> out.Folder:
    """Rename a folder of one kind; its objects, in every workspace, move with it."""
    return await serve("rename_object_folder", input, call)


@module.action(effects=['delete', 'write'],
               errors=['conflict', 'not_found'],
               examples=[{'kind': 'factor', 'name': 'Momentum'}])
async def delete_object_folder(input: m.DeleteObjectFolderInput, call: Call) -> out.FolderDeleted:
    """Delete an empty folder; one that still holds objects of any workspace is refused with conflict."""
    return await serve("delete_object_folder", input, call)


@module.action(effects=['read'],
               errors=['not_found'],
               examples=[{'formula': '-($close/Ref($close,5)-1)'},
                         {'formula': 'Mean($close, 5) / Mean($close, 20) - 1', 'objectId': '<objectId>'}])
async def validate_factor_formula(input: m.ValidateFactorFormulaInput, call: Call) -> out.FormulaCheck:
    """Check a factor formula without running it, in milliseconds: the first mistake with its kind (function, field,
    brackets, future, syntax), at and length (character offsets), a message and a fix for a misspelt function; null
    when it reads. With objectId the fields are checked against that factor's data."""
    return await serve("validate_factor_formula", input, call)


@module.action(effects=['read'],
               errors=['invalid_input', 'not_found'],
               examples=[{'objectId': '<objectId>'},
                         {'objectId': '<objectId>', 'vary': [{'name': 'lgb_lr', 'values': [0.05, 0.1, 0.2]}]}])
async def check_model(input: i.CheckModel, call: Call) -> out.ModelCheck:
    """Check a model's saved settings in seconds, without training: training rows per window, each input's coverage
    and start, inputs nearly the same (correlation above 0.95), the label, look-ahead, gaps, the windows' time axis,
    and each group's memory against what is free. vary checks the batch start_sweep would train (its time and
    memory)."""
    return await serve("check_model", input, call)


@module.action(effects=['read'],
               errors=['invalid_input', 'not_found'],
               examples=[{'objectId': '<objectId>', 'input': 'i1'},
                         {'objectId': '<objectId>', 'input': 'i1', 'feature': 'KMID'}])
async def inspect_model_input(input: m.InspectModelInputInput, call: Call) -> out.InspectedInput:
    """One model input's distribution before and after processing and its missing share each week, on the windows'
    training segments only, with mean, std, skew and the share beyond 3 MAD."""
    return await serve("inspect_model_input", input, call)


@module.action(effects=['code'],
               errors=['invalid_input', 'not_found'],
               examples=[{'objectId': '<objectId>'},
                         {'objectId': '<objectId>', 'changes': {'lookback': 126, 'maxWeightPct': 30}},
                         {'objectId': '<objectId>', 'runId': '<runId>', 'pin': {'asset': 'GLD', 'weight': 0.1}}])
async def trial_allocation(input: m.TrialAllocationInput, call: Call) -> out.AllocationTrial | out.SupersededTrial:
    """Fit an allocation's newest rebalance under its saved settings, with changes on top, and return each asset's
    weight, risk share and the limit that binds, in about a second once the research process is up. Nothing is saved
    and no run is made; to keep the changes use update_object. Of overlapping requests only the newest is answered
    (the others say superseded)."""
    return await serve("trial_allocation", input, call)


@module.action(effects=['code', 'write'],
               errors=['invalid_input', 'not_found', 'unavailable'],
               examples=[{'objectId': '<objectId>'}])
async def start_run(input: i.ObjectId, call: Call) -> r.RunReceipt:
    """Run an object exactly as saved (code, data, params, run settings): a strategy backtest, a factor test, one model
    training or an allocation backtest. Returns runId at once; the result (read_run's summary: key numbers, tested
    start and end, symbols used and dropped) reaches you when the run ends, so do not poll. To try another value,
    update_object then start_run; to compare several values without changing the object, start_sweep. A notebook runs
    cell by cell (run_cell); code in a notebook backtests a strategy with start_notebook_backtest."""
    return await serve("start_run", input, call)


@module.action(effects=['code', 'write'],
               errors=['invalid_input', 'not_found', 'value_outside_declared'],
               examples=[{'objectId': '<objectId>', 'axes': [{'name': 'slow', 'values': [100, 150, 200]}]},
                         {'objectId': '<objectId>', 'axes': [{'name': 'fast', 'values': [10, 20]}, {'name': '@costMultiple', 'values': [1, 2]}], 'metric': 'cagr'},
                         {'objectId': '<objectId>', 'axes': [{'name': 'holding', 'values': [1, 5, 20]}]}])
async def start_sweep(input: i.Sweep, call: Call) -> r.RunReceipt:
    """Run one ordinary run per combination of the axes' values, without changing the object. Returns one runId at
    once; the result (each cell's state and numbers, the top 20 by the kind's main number, the best cell) comes when
    every cell has ended. Axes are the declared params (read_object part options); a strategy also takes @costMultiple
    (0-10), @fill (nextOpen or nextClose), @capital and @period ({start, end}); a factor holding, groups, costBps and
    delay; an allocation one or two axes of two or more values. Any number of combinations: estimate_sweep says how long a
    strategy's take.
    Strategy only: metric ranks the cells (sharpe default, cagr, totalReturn, maxDrawdown, trades, profitFactor,
    winRate); of starts from a completed backtest's code and values. Keep a cell with apply_run."""
    return await serve("start_sweep", input, call)


@module.action(effects=['code', 'write'],
               errors=['not_found', 'too_few_windows', 'value_outside_declared'],
               examples=[{'objectId': '<objectId>', 'axes': [{'name': 'lookback', 'values': [24, 48, 96]}], 'pickOn': 12, 'trade': 3}])
async def start_walk_forward(input: m.StartStrategyWalkForwardInput, call: Call) -> r.RunReceipt:
    """Walk-forward validation of a strategy: each window picks the best combination of the axes over pickOn months
    (by pickBy, default sharpe) and trades it for the next trade months (defaults 12 and 3); at least 3 windows must
    fit before the held-out start. Axes as in start_sweep. Returns runId at once; the
    result (stitched out-of-sample numbers against fixed values, efficiency, the windows) comes when it ends."""
    return await serve("start_walk_forward", input, call)


@module.action(effects=['read'],
               errors=['invalid_input', 'not_found'],
               examples=[{'runId': '<runId>'}])
async def plan_held_out(input: i.RunId, call: Call) -> r.Fields:
    """What validating a run on its held-out span involves, before doing it: the span (start, end), what is computed
    again, and how often that span was already looked at. Reads only. The span starts at run.heldOutFrom, set with
    update_object before tuning."""
    return await serve("plan_held_out", input, call)


@module.action(effects=['code', 'write'],
               errors=['no_held_out_period', 'not_found'],
               examples=[{'runId': '<runId>'}])
async def validate_held_out(input: i.RunId, call: Call) -> r.RunReceipt:
    """Score a completed run on its held-out span (from run.heldOutFrom), once, at the end of the research: a held-out
    run whose result puts the in-sample and held-out numbers side by side. Returns runId at once; the result comes
    when it ends. The same run validated again returns its earlier held-out run, not a new one."""
    return await serve("validate_held_out", input, call)


@module.action(effects=['code', 'write'],
               errors=['conflict', 'not_found'],
               examples=[{'runId': '<runId>'},
                         {'runId': '<runId>', 'override': {'nn_batch': 400}}])
async def resume_run(input: i.ResumeRun, call: Call) -> r.RunReceipt:
    """Continue a cancelled, interrupted or failed run: a model training from its finished windows (override sets params
    for the windows left, such as nn_batch; epoch continues a neural network from its saved checkpoint), a strategy sweep from the combinations that did not complete, a walk-forward from its first window
    that did not run, or a factor that failed during data preparation. Returns runId; the result comes when it ends."""
    return await serve("resume_run", input, call)


@module.action(effects=['code', 'write'],
               errors=['invalid_input', 'not_found'],
               examples=[{'runIds': ['<runId>', '<runId>']}])
async def combine_model_runs(input: m.CombineModelRunsInput, call: Call) -> r.RunReceipt:
    """A new model experiment: the mean of these runs' predictions, each ranked within its day (same data and
    windows); it can be published. Returns runId; the result comes when it ends."""
    return await serve("combine_model_runs", input, call)


@module.action(effects=['code', 'write'],
               errors=['not_found'],
               examples=[{'runId': '<runId>'},
                         {'runId': '<runId>', 'cell': 3}])
async def build_results(input: i.CellOfRun, call: Call) -> r.RunReceipt:
    """Build the detailed report of a run when read_run says it is not built: a factor run's Results (an older run, or
    one sweep cell with cell) or a model run's portfolio backtest. Then read its parts with read_run."""
    return await serve("build_results", input, call)


@module.action(effects=['write'],
               errors=['not_found'],
               examples=[{'runId': '<runId>'}])
async def cancel_run(input: i.RunId, call: Call) -> r.RunReceipt:
    """Stop a running or queued run, batch or publication; what finished stays and the state becomes cancelled.
    Model trainings, strategy sweeps and walk-forwards can go on with resume_run."""
    return await serve("cancel_run", input, call)


@module.action(effects=['read'],
               errors=['not_found'],
               examples=[{'objectId': '<objectId>'},
                         {'objectId': '<objectId>', 'runKind': 'sweep', 'starred': True}])
async def list_runs(input: i.ListRuns, call: Call) -> r.Page:
    """List an object's runs, newest first: runId, number, runKind, state, numbers (fractions), tested start and end,
    the values a batch cell used, note and star. Runs started in this workspace; scope "all" lists every workspace.
    Narrow by runKind, state, starred or batch. One run in depth: read_run."""
    return await serve("list_runs", input, call)


@module.action(effects=['read'],
               errors=['not_found', 'invalid_input', 'too_large'],
               examples=[{'runId': '<runId>'},
                         {'runId': '<runId>', 'part': 'statistics'},
                         {'runId': '<runId>', 'compareTo': '<runId>'}])
async def read_run(input: i.ReadRun, call: Call) -> r.RunSummary | r.RunChartSection | r.RunSection:
    """Read one run, one part at a time. part "summary" (default, and what a finished start_* delivers): state, key
    numbers, tested start and end (the days actually tested), the requested span, heldOutFrom, symbols set, used and
    dropped, the settings it ran with, what changed since the previous run, and its parts and tables. Other parts:
    strategy statistics, equity, periods, rolling, longShort, tradeStats, orderStats, account, sectors,
    instruments, style, costs, crisis; factor returns, ic, industry, style, turnover, costs, event; model returns,
    information, turnover, inputs, robustness; allocation weights, risk, returns, style, robust; each also settings,
    chartLayer for strategy, factor and model (pass data.layer to chart.write_chart create layers),
    and code (not allocations; paged by cursor). Sweeps and walk-forwards have summary and settings; held-out and
    publish runs summary. compareTo (a run of the same kind) adds what changed in settings, data and code and each
    number's difference. Rows and daily series: read_run_table."""
    return await serve("read_run", input, call)


@module.action(effects=['read'],
               errors=['not_found'],
               examples=[{'runId': '<runId>', 'table': 'trades', 'start': '2022-01-01', 'end': '2022-06-30'},
                         {'runId': '<runId>', 'table': 'crossSection', 'date': '2024-03-01'},
                         {'runId': '<runId>', 'table': 'daily', 'format': 'file'}])
async def read_run_table(input: i.ReadRunTable, call: Call) -> TablePage | TableFile:
    """A run's rows as a table (columns with units, rows, total, nextCursor), or the whole table as a Parquet file with
    format "file". Tables: strategy trades, orders, rebalances, daily (equity, benchmark, drawdown, return, riskFree);
    factor crossSection, daily; model windows, values, daily; allocation periods, daily; a sweep cells; every run log.
    start and end (inclusive) select daily, trades, orders, rebalances and periods; date picks one day of
    crossSection, values or rebalances. filter: trades {side, exit, losing}, orders {status}, rebalances {only:
    changed (default), unfilled or all}, cells {state}, log {stream: stderr (default), stdout, environment_stdout,
    environment_stderr}. The run's parts: read_run."""
    return await serve("read_run_table", input, call)


@module.action(effects=['read'],
               errors=['not_found'],
               examples=[{'runId': '<runId>'}])
async def read_run_materials(input: i.RunId, call: Call) -> out.Materials:
    """The files a run froze, for code to read: the bars and instrument information (paths), the tables behind its
    report, model files, with the run's settings and numbers."""
    return await serve("read_run_materials", input, call)


@module.action(effects=['write'],
               errors=['not_found'],
               examples=[{'runId': '<runId>', 'starred': True},
                         {'runId': '<runId>', 'note': 'Costs doubled: still positive'}])  # i18n: ignore
async def update_run(input: m.AnnotateObjectRunInput, call: Call) -> out.RunAnnotation:
    """Star a run or write its note; only the given fields change. A starred run represents its object when none is
    published."""
    return await serve("update_run", input, call)


@module.action(effects=['write'],
               errors=['conflict', 'invalid_input', 'not_found'],
               examples=[{'runId': '<runId>'},
                         {'runId': '<runId>', 'cell': 4}])
async def apply_run(input: i.ApplyRun, call: Call) -> r.ObjectSummary:
    """Put an object back to what a run used: without cell, the run's code and all its settings; with cell (a cell of a
    strategy or factor sweep), only that combination's values. For a model or allocation batch, give the member run's
    runId. Returns the object as read_object shows it."""
    return await serve("apply_run", input, call)


@module.action(effects=['write'],
               errors=['conflict', 'invalid_input', 'not_found'],
               examples=[{'runId': '<runId>', 'target': 'SPY', 'pct': -20}])
async def add_allocation_shock(input: m.AddAllocationShockInput, call: Call) -> out.Shock:
    """Add a shock to a finished allocation run's Scenarios table: an asset or group moves pct percent (-20 is a 20%
    fall) and the portfolio's move follows by the covariance."""
    return await serve("add_allocation_shock", input, call)


@module.action(effects=['read'],
               examples=[{}])
async def read_queue(input: i.Page, call: Call) -> out.Queue:
    """Running and queued Quant work in every workspace: each run's object, workspace, stage, progress and what it
    waits for (memory, GPU), and the library's production. Paged."""
    return await serve("read_queue", input, call)


@module.action(effects=['write'],
               errors=['invalid_input'],
               examples=[{'count': 2}])
async def set_model_concurrency(input: m.SetModelConcurrencyInput, call: Call) -> out.Concurrency:
    """How many model runs train at once; each gets the cores divided among them, all within the memory budget."""
    return await serve("set_model_concurrency", input, call)


@module.action(effects=['read'],
               errors=['invalid_input', 'not_found'],
               examples=[{'runId': '<runId>'},
                         {'runId': '<runId>', 'name': 'S&P 500 reversal'}])
async def plan_publication(input: i.PlanPublication, call: Call) -> out.PublicationPlan:
    """Before publish_run: the next version, checks not passed, the comparison with the previous version, who uses
    the item, the most similar library items, and whether it would be refused. For a completed factor, model or
    allocation run (strategies are not published); name defaults to the run's object name."""
    return await serve("plan_publication", input, call)


@module.action(effects=['code', 'write'],
               errors=['conflict', 'invalid_input', 'not_found'],
               examples=[{'runId': '<runId>', 'name': 'S&P 500 reversal'},
                         {'runId': '<runId>', 'name': 'S&P 500 LightGBM', 'autoUpdate': False}])
async def publish_run(input: i.PublishRun, call: Call) -> r.RunReceipt:
    """Publish a completed factor, model or allocation run to the library as a version of the item called name; a
    model's publication first checks for look-ahead by training again. autoUpdate (default true) makes the version
    add new values each period. Returns runId at once; the result comes when it ends."""
    return await serve("publish_run", input, call)


@module.action(effects=['read'],
               examples=[{'state': 'attention'},
                         {'kind': 'factor', 'query': 'momentum'}])
async def list_library(input: i.ListLibrary, call: Call) -> r.LibraryCatalog:
    """The library: one row per item (factor, model, allocation, data) with its versions, problems first. Narrow by
    kind, a name query or state ("attention" for items that need a hand). Paged."""
    return await serve("list_library", input, call)


@module.action(effects=['read'],
               errors=['not_found'],
               examples=[{'name': 'S&P 500 reversal'},
                         {'itemId': '<itemId>', 'versionId': '<versionId>', 'compareTo': '<versionId>'}])
async def read_library_item(input: i.LibraryItem, call: Call) -> out.LibraryItem:
    """One library item by itemId or name: its state and what to do, each version's numbers at publication and since,
    the selected version (versionId, default the newest producing one) with its settings, data, inputs and users,
    recent periods and failures. compareTo (another versionId) adds the two versions' comparison."""
    return await serve("read_library_item", input, call)


@module.action(effects=['read'],
               errors=['not_found'],
               examples=[{'versionId': '<versionId>'}])
async def read_library_log(input: i.LibraryLog, call: Call) -> TablePage:
    """One library version's production record, a row per period (result, times, failed tries). Paged."""
    return await serve("read_library_log", input, call)


@module.action(effects=['read'],
               errors=['invalid_input', 'not_found'],
               examples=[{'name': 'Momentum', 'start': '2024-01-01', 'end': '2024-06-30', 'symbols': ['AAPL', 'MSFT']},
                         {'name': 'Momentum', 'date': '2024-06-28', 'order': 'top', 'limit': 20},
                         {'versionId': '<versionId>', 'format': 'file'}])
async def read_library_values(input: i.LibraryValues, call: Call) -> r.LibraryPage | r.LibraryFile:
    """Values a library version produced: name (its newest version, or version n) or versionId. History: start and end
    (inclusive) give a long table of date, symbol, instrumentId, field, value, availableAt, recordedAt; with end the
    values are those known by the end of that day unless asOf (an ISO time) sets another cutoff. Cross section: date
    gives one day ranked by order (top, bottom, missing), not combined with start, end or asOf. symbols and field
    narrow either; format "file" returns every row as Parquet."""
    return await serve("read_library_values", input, call)


@module.action(effects=['read'],
               errors=['not_found', 'model_not_callable'],
               examples=[{'name': 'S&P 500 LightGBM'},
                         {'name': 'S&P 500 LightGBM', 'version': 2}])
async def read_library_model(input: m.ReadLibraryModelInput, call: Call) -> out.PublishedModel:
    """A published model's frozen files and entries, for code to load."""
    return await serve("read_library_model", input, call)


@module.action(effects=['write'],
               errors=['invalid_input'],
               examples=[{'name': 'EPS estimate', 'kind': 'data', 'rows': [{'date': '2025-01-02', 'symbol': 'AAPL', 'field': 'eps', 'value': 2.35},
                                                                              {'date': '2025-01-02', 'symbol': 'MSFT', 'field': 'eps', 'value': 3.1}]}])
async def publish_library_values(input: i.PublishValues, call: Call) -> out.PublishedValues:
    """Publish your own dated per-instrument values as a library data item called name, which strategies and models
    then read like any library item. Each row has date (YYYY-MM-DD) or time (ISO with timezone), symbol, field and
    value."""
    return await serve("publish_library_values", input, call)


@module.action(effects=['write'],
               errors=['conflict', 'not_found'],
               examples=[{'versionId': '<versionId>', 'producing': False},
                         {'versionId': '<versionId>', 'producing': False, 'dryRun': True},
                         {'versionId': '<versionId>', 'autoUpdate': True}])
async def update_library_version(input: i.EditVersion, call: Call) -> out.StopVersion:
    """Stop or resume a library version's production (producing) and turn its automatic update each period on or off
    (autoUpdate); only the given fields change. dryRun with producing false shows what stopping would affect,
    without stopping."""
    return await serve("update_library_version", input, call)


@module.action(effects=['code', 'write'],
               errors=['not_found'],
               examples=[{'versionId': '<versionId>'}])
async def retry_library_version(input: m.RetryLibraryVersionInput, call: Call) -> out.RetryVersion:
    """Try a failed, missing or waiting library version now, ahead of the queue."""
    return await serve("retry_library_version", input, call)


@module.action(effects=['write'],
               errors=['conflict'],
               examples=[{'versionId': '<versionId>'}])
async def cancel_library_update(input: m.CancelLibraryUpdateInput, call: Call) -> out.CancelUpdate:
    """Stop the update of this library version that runs now; nothing is added, it is tried again at the next
    check."""
    return await serve("cancel_library_update", input, call)


@module.action(effects=['delete', 'write'],
               errors=['conflict', 'not_found'],
               examples=[{'versionId': '<versionId>'}])
async def archive_library_version(input: m.ArchiveLibraryVersionInput, call: Call) -> out.ArchivedVersion:
    """Archive a stopped library version that nothing uses: its stored values, window models and frozen environment
    are deleted, which cannot be undone; its record, code and published figures stay."""
    return await serve("archive_library_version", input, call)


@module.action(effects=['write'],
               errors=['not_found'],
               examples=[{'versionId': '<versionId>', 'period': '2025-04-01', 'how': 'accept'},
                         {'versionId': '<versionId>', 'period': '2025-04-01', 'how': 'one_off',
                          'weights': {'SPY': 0.5, 'TLT': 0.3, 'GLD': 0.2}, 'reason': 'Rate decision this week'}])
async def review_allocation_period(input: m.ReviewAllocationPeriodInput, call: Call) -> out.ReviewedPeriod:
    """The monthly review of a published allocation's newest period: accept it as computed, or apply a one-off
    adjustment (every asset's weight and a reason) that applies to that period only."""
    return await serve("review_allocation_period", input, call)


@module.action(effects=['read'],
               examples=[{}])
async def read_quant_attention(input: i.Page, call: Call) -> out.Attention:
    """What needs a hand now, across workspaces: library versions that failed, miss periods or lack an input (an input
    that failed is listed alone), allocation periods to review, research objects on a stopped version. Each row names
    the Quant action to call and its complete input. Paged."""
    return await serve("read_quant_attention", input, call)


@module.action(effects=['read'],
               examples=[{}])
async def list_research_lists(input: i.Page, call: Call) -> r.Page:
    """List the research lists: named sets of instruments every workspace can use; an object reads one with
    data.list set to its listId. Paged."""
    return await serve("list_research_lists", input, call)


@module.action(effects=['read'],
               errors=['not_found'],
               examples=[{'listId': '<listId>'}])
async def read_research_list(input: m.ReadResearchListInput, call: Call) -> r.ResearchList:
    """Read one research list with its members as symbols."""
    return await serve("read_research_list", input, call)


@module.action(effects=['write'],
               errors=['not_found'],
               examples=[{'name': 'US funds', 'symbols': ['SPY', 'TLT', 'GLD']}])
async def create_research_list(input: i.NewResearchList, call: Call) -> r.ResearchList:
    """Create a research list of instruments; an object uses it with update_object {data: {list: <listId>}}."""
    return await serve("create_research_list", input, call)


@module.action(effects=['write'],
               errors=['not_found'],
               examples=[{'listId': '<listId>', 'add': ['QQQ'], 'remove': ['TLT']},
                         {'listId': '<listId>', 'name': 'US core funds', 'symbols': ['SPY', 'QQQ', 'IEF']}])
async def update_research_list(input: i.EditResearchList, call: Call) -> r.ResearchList:
    """Rename a research list, replace its members (symbols) or add and remove some (add, remove; not together with
    symbols). Returns the list and changed."""
    return await serve("update_research_list", input, call)


@module.action(effects=['delete', 'write'],
               target='listId',
               errors=['conflict', 'not_found'],
               examples=[{'listId': '<listId>'}])
async def delete_research_list(input: m.DeleteResearchListInput, call: Call) -> out.DeletedList:
    """Delete a research list; refused with conflict while an object uses it."""
    return await serve("delete_research_list", input, call)


@module.action(effects=['code', 'write'],
               errors=['not_found', 'conflict', 'unavailable'],
               examples=[{'objectId': '<objectId>', 'cellId': '<cellId>'},
                         {'objectId': '<objectId>', 'cellId': '<cellId>', 'timeoutSeconds': 600}])
async def run_cell(input: m.RunCellInput, call: Call) -> out.CellOutput:
    """Run one saved code cell, starting its workspace kernel when needed. Outputs are saved with the cell and
    returned as text (images and rich displays name their type). Waits up to timeoutSeconds (default 120);
    a cell still running then is reported unavailable and
    keeps running. Write the cell first with write_cell."""
    return await serve("run_cell", input, call)


@module.action(effects=['write'],
               target='objectId',
               errors=['conflict', 'not_found', 'invalid_input'],
               examples=[{'objectId': '<objectId>', 'op': 'add', 'source': 'import talyx_quant as q\nq.data().close.tail()'},
                         {'objectId': '<objectId>', 'op': 'replace', 'cellId': '<cellId>', 'source': 'print(1 + 1)'},
                         {'objectId': '<objectId>', 'op': 'delete', 'cellId': '<cellId>'}])
async def write_cell(input: m.CellAdd | m.CellReplace | m.CellDelete, call: Call) -> out.CellWritten:
    """Change one cell of a notebook and save it, as the notebook panel does: op=add a code (or markdown) cell after
    another or at the end, op=replace a cell's text (its outputs are cleared), op=delete. Returns the cell's id and
    every cell in order. Run a code cell with run_cell. Cell ids come from read_object {part:"code"}."""
    return await serve("write_cell", input, call)


@module.action(effects=['read'],
               errors=['not_found'],
               examples=[{'notebookId': '<objectId>'}])
async def read_notebook_variables(input: m.ReadNotebookVariablesInput, call: Call) -> out.NotebookVariables | r.Fields:
    """List kernel variables and their last defining cell, or pass name to read its table (large tables return a JSON file)."""
    return await serve("read_notebook_variables", input, call)


@module.action(effects=['write'],
               errors=['not_found'],
               examples=[{'runId': '<runId>'}])
async def open_run_in_notebook(input: m.OpenRunInNotebookInput, call: Call) -> out.NotebookFromRun:
    """Open (or reuse) the notebook made for a run; its first cell reads that run."""
    return await serve("open_run_in_notebook", input, call)


@module.action(effects=['write'],
               errors=['deep_not_installed', 'not_found'],
               examples=[{'notebookId': '<objectId>', 'environment': 'deep'}])
async def set_notebook_kernel(input: m.SetNotebookKernelInput, call: Call) -> out.KernelSelected:
    """Switch a notebook's kernel environment (research or deep); the kernel restarts and its variables are cleared.
    deep must be installed first (install_deep_environment)."""
    return await serve("set_notebook_kernel", input, call)


@module.action(effects=['read'],
               examples=[{}])
async def read_environments(input: m.ReadDeepEnvironmentInput, call: Call) -> r.Environments | r.Fields:
    """The environments notebooks and code run in: the research and deep-learning kernels and whether each is
    installed, which PyTorch builds this machine can use, and installation progress. part packages lists extension packages and the install job."""
    return await serve("read_environments", input, call)


@module.action(effects=['code', 'external', 'write'],
               task=True,
               examples=[{'variant': 'cpu'}])
async def install_deep_environment(input: m.InstallDeepEnvironmentInput, call: Call) -> out.DeepEnvironment:
    """Download and install the deep-learning environment with the chosen PyTorch build (cpu, cuda or xpu;
    read_environments says which this machine can use). A long task: the answer comes when it is installed."""
    return await serve("install_deep_environment", input, call)


@module.action(effects=['code', 'write'],
               errors=['not_found'],
               examples=[{'strategy': 'BTC 20/100 crossover'},
                         {'strategy': 'BTC 20/100 crossover', 'params': {'slow': 150}},
                         {'strategy': 'Top N by signal', 'signal': {'param': 'signal', 'path': 'scores.parquet'}}])
async def start_notebook_backtest(input: m.StartNotebookBacktestInput, call: Call) -> out.NotebookBacktest:
    """Backtest a strategy (by name) from code, the way q.backtest does: params apply to this run only and are not
    saved; signal makes the strategy read your values in place of one library input. The run is tagged Notebook and
    never represents the object. Returns runId and number at once; the result comes when it ends. To run an object as
    saved, use start_run."""
    return await serve("start_notebook_backtest", input, call)


@module.action(effects=['read'],
               task=True,
               errors=['invalid_input', 'no_data', 'unavailable'],
               examples=[{'index': {'symbol': '^GSPC'}, 'timeframe': '1d', 'start': '2018-01-01'},
                         {'list': 'US funds', 'timeframe': '1d', 'start': '2020-01-01', 'end': '2024-12-31', 'adjustment': 'split_dividend'},
                         {'symbols': ['AAPL', 'MSFT', 'NVDA'], 'timeframe': '1h', 'start': '2025-01-01'}])
async def read_research_bars(input: i.ResearchBars, call: Call) -> r.ResearchBars:
    """Freeze bars of many instruments into one Parquet file for code: an index's historical members (each only while
    it was a member), a research list (by name) or symbols; info.json gives each instrument and who was left out and
    why. A long task; the answer has both file references and the coverage. A few named instruments' bars to read
    directly: market.read_data."""
    return await serve("read_research_bars", input, call)


@module.action(effects=['read'], examples=[{}])
async def list_common_code(input: m.Empty, call: Call) -> r.Fields:
    """List the shared Quant source directory and files. Pass the directory to developer.projects.open to edit the project."""
    return await serve("list_common_code", input, call)


@module.action(effects=['read'], examples=[{}])
async def list_presets(input: m.Empty, call: Call) -> r.Fields:
    """List the strategy, model and factor presets with their descriptions. Use an id with create_object."""
    return await serve("list_presets", input, call)


@module.action(effects=['read'], examples=[{'objectId': '<objectId>', 'code': 'lookback = q.param("lookback", 20)'}])
async def check_parameters(input: i.ParameterSource, call: Call) -> r.Fields:
    """Check parameter declarations in proposed source without saving or running it."""
    return await serve("check_parameters", input, call)


@module.action(effects=['write'], examples=[{'runId': '<runId>', 'cell': 0}])
async def select_sweep_cell(input: i.SweepSelection, call: Call) -> r.Fields:
    """Save the selected completed combination on a strategy sweep. apply_run separately copies its settings to a strategy."""
    return await serve("select_sweep_cell", input, call)


@module.action(effects=['write'], examples=[{'runId': '<runId>'}])
async def promote_model_run(input: i.RunId, call: Call) -> r.RunReceipt:
    """Move a queued model training run to the front of the queue."""
    return await serve("promote_model_run", input, call)


@module.action(effects=['write'], examples=[{'objectId': '<objectId>'}])
async def reset_model(input: i.ObjectId, call: Call) -> r.ObjectSummary:
    """Return a model to the standard pipeline and reset its parameters."""
    return await serve("reset_model", input, call)


@module.action(effects=['read'], examples=[{'objectId': '<objectId>', 'axes': [{'name': 'slow', 'values': [60, 100]}]}])
async def estimate_sweep(input: i.Sweep | i.EstimateWalk, call: Call) -> r.Fields:
    """Estimate combinations, time, concurrency and machine budget before a strategy sweep or walk-forward (kind walk)."""
    return await serve("estimate_sweep", input, call)


@module.action(effects={"path": "op", "branches": {
    "status": {"effects": ["read"]}, "interrupt": {"effects": ["write"], "target": "objectId"},
    "stop": {"effects": ["write"], "target": "objectId"}, "restart": {"effects": ["write"], "target": "objectId"},
}}, examples=[{"objectId": "<objectId>", "op": "status"}])
async def control_notebook_kernel(input: i.KernelControl, call: Call) -> r.Fields:
    """Check the kernel status, interrupt a running cell, stop the kernel, or restart it with a fresh namespace."""
    return await serve("control_notebook_kernel", input, call)


@module.action(effects=['read'], examples=[{'notebookId': '<objectId>', 'cells': ['<cellId>'], 'kind': 'factor'}])
async def preview_notebook_extract(input: i.ExtractPreview, call: Call) -> r.Fields:
    """Preview code, data settings and omissions before creating a research object from notebook cells."""
    return await serve("preview_notebook_extract", input, call)


@module.action(effects=['write'], examples=[{'objectId': '<objectId>', 'expectedVersion': 1, 'notebook': {'cells': [], 'metadata': {}}}])
async def save_notebook(input: i.SaveNotebook, call: Call) -> r.Fields:
    """Save the entire notebook, including cell order, metadata, attachments and outputs. Read part notebook of read_object for the model and document version."""
    return await serve("save_notebook", input, call)


@module.action(effects=['read'], examples=[{'versionId': '<versionId>', 'part': 'report'}])
async def read_library_material(input: i.LibraryReport | i.LibrarySource | i.LibraryFile | i.LibraryTable, call: Call) -> r.Fields:
    """Read a frozen library report, list or read source files, retrieve a named report file, or query a report table. Large JSON results and files return a file reference."""
    return await serve("read_library_material", input, call)


@module.action(effects=['read'], examples=[{'runIds': ['<runId>', '<otherRunId>']}])
async def read_model_figures(input: i.ModelFigures | i.ModelOverlay, call: Call) -> r.Fields:
    """Read model figures by runId and optional keys and comparison run. Supply runIds for a test IC overlay of up to four runs. Large figures return a JSON file."""
    return await serve("read_model_figures", input, call)


@module.action(effects={"path": "op", "branches": {
    "install": {"effects": ["code", "external", "write"]}, "cancel": {"effects": ["write"]},
}}, examples=[{"op": "install", "environment": "research"}, {"op": "cancel", "environment": "deep"}])
async def manage_package_install(input: i.PackageInstall | i.CancelInstall, call: Call) -> r.Fields:
    """Install the research requirements, or cancel an active research or deep environment installation. Read progress with read_environments."""
    return await serve("manage_package_install", input, call)
