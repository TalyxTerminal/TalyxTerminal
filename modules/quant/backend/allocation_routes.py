"""Private routes and public actions of an allocation's Work: the preparation, the trial, runs, a run's Work and Results."""
from __future__ import annotations

from typing import Literal

from fastapi import APIRouter, Query

from . import ai_views
from .object_routes import Input


def figure_for_ai(value):
    """A Plotly figure as an AI reads it: what it shows (its key) and, for each line or part, its name, how many points, the span of x and the last value; the
    screen keeps the points."""
    traces = []
    for trace in value.get("data") or []:
        xs, ys = trace.get("x") or [], trace.get("y") if isinstance(trace.get("y"), list) else []
        traces.append({"name": trace.get("name") or (trace.get("meta") or {}).get("key"), "points": len(xs), **({"from": xs[0], "to": xs[-1]} if xs else {}),
                       **({"last": ys[-1]} if ys else {})})
    return {"figure": ((value.get("layout") or {}).get("meta") or {}).get("key"), "traces": traces}


def for_ai(value):
    """An allocation answer for an AI: every figure as `figure_for_ai`, the instruments of the data settings as their symbols, and what is still over the budget
    cut by design (`cut` says which list keeps how many)."""
    def walk(item):
        if isinstance(item, dict):
            if isinstance(item.get("data"), list) and isinstance(item.get("layout"), dict):
                return figure_for_ai(item)
            return {key: walk(entry) for key, entry in item.items()}
        if isinstance(item, list):
            return [walk(entry) for entry in item]
        return item
    return ai_views.bound(ai_views.lean(walk(value)))


class Trial(Input):
    changes: dict | None = None
    pin: dict | None = None
    published: dict | None = None
    heldFrom: int | None = None
    runId: str | None = None


class AllocationRunStart(Input):
    kind: Literal["backtest", "heldout"] | None = None
    changes: dict | None = None
    of: str | None = None


class Axis(Input):
    name: str
    values: list


class Sweep(Input):
    axes: list[Axis]


class Annotation(Input):
    note: str | None = None
    starred: bool | None = None


class PeriodReview(Input):
    period: int
    how: Literal["accept", "one_off"]
    weights: dict[str, float] | None = None
    reason: str | None = None


class Publication(Input):
    name: str | None = None
    auto: bool | None = None


class Apply(Input):
    expectedVersion: int | None = None


class Shock(Input):
    target: str
    pct: float


def mount_allocations(router: APIRouter, services):
    runs = services.allocation

    @router.get("/objects/{object_id}/allocation-prep")
    async def read_prep(object_id: str):
        return await runs.prep(object_id)

    @router.get("/objects/{object_id}/allocation-links")
    async def link_candidates(object_id: str):
        return {"runs": await runs.link_candidates(), "library": runs.library_candidates()}

    @router.post("/objects/{object_id}/allocation-code")
    async def write_as_code(object_id: str):
        return await runs.write_as_code(object_id)

    @router.get("/objects/{object_id}/allocation-held-out")
    async def held_out(object_id: str, runId: str | None = None):
        return await runs.held_status(object_id, runId)

    @router.post("/objects/{object_id}/allocation-trial")
    async def trial(object_id: str, body: Trial):
        return await runs.trial(object_id, body.args())

    @router.post("/objects/{object_id}/allocation-runs")
    async def start(object_id: str, body: AllocationRunStart):
        return await runs.start(object_id, body.args())

    @router.post("/objects/{object_id}/allocation-sweeps")
    async def sweep(object_id: str, body: Sweep):
        return await runs.start_sweep(object_id, {"axes": [axis.model_dump() for axis in body.axes]})

    @router.get("/objects/{object_id}/allocation-runs")
    async def list_runs(object_id: str):
        return runs.list(object_id)

    @router.get("/allocation-runs/{run_id}")
    async def read_run(run_id: str):
        return runs.work(run_id)

    @router.get("/allocation-runs/{run_id}/code")
    async def read_code(run_id: str):
        return runs.code(run_id)

    @router.get("/allocation-runs/{run_id}/results")
    async def read_results(run_id: str, tab: str = Query("summary")):
        return runs.results(run_id, tab)

    @router.get("/allocation-runs/{run_id}/periods/{index}")
    async def read_period(run_id: str, index: int):
        return runs.period(run_id, index)

    @router.post("/allocation-runs/{run_id}/stop")
    async def stop(run_id: str):
        return await runs.stop(run_id)

    @router.patch("/allocation-runs/{run_id}")
    async def annotate(run_id: str, body: Annotation):
        return runs.annotate(run_id, body.args())

    @router.get("/objects/{object_id}/allocation-published")
    async def published(object_id: str):
        return runs.published(object_id) or {}

    @router.get("/allocation-runs/{run_id}/publish-plan")
    async def publish_plan(run_id: str, name: str | None = None):
        return runs.plan_publication(run_id, name)

    @router.post("/allocation-runs/{run_id}/publish")
    async def publish(run_id: str, body: Publication):
        return await runs.publish(run_id, body.name, body.auto if body.auto is not None else True)

    @router.post("/library-versions/{version_id}/allocation-review")
    async def review(version_id: str, body: PeriodReview):
        return await runs.review({"versionId": version_id, **body.args()})

    @router.post("/allocation-runs/{run_id}/apply")
    async def apply(run_id: str, body: Apply):
        return await runs.apply(run_id, body.expectedVersion)

    @router.post("/allocation-runs/{run_id}/shock")
    async def shock(run_id: str, body: Shock):
        return await runs.add_shock(run_id, body.target, body.pct)


def handle_allocation_actions(ctx, services):
    from .public_settings import milliseconds, params_in
    runs = services.allocation

    async def trial_allocation(args, call):
        options = {k:v for k,v in args.items() if k != "objectId"}
        if "changes" in options:
            options["changes"] = params_in(services.store.read(args["objectId"]), options["changes"])
        return for_ai(await runs.trial(args["objectId"], options))

    async def review_allocation_period(args, call):
        found = await runs.review({**args,"period":milliseconds(args["period"])})
        return {**found,"period":args["period"]}

    async def add_allocation_shock(args, call):
        return await runs.add_shock(args["runId"],args["target"],args["pct"])

    for fn in (trial_allocation,review_allocation_period,add_allocation_shock):
        ctx.handlers[fn.__name__] = fn
