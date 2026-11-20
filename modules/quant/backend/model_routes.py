"""Private routes and public actions of a model's Work: the preparation, the check before training, runs and batches, the experiment
table, Results, a day's values, compare and combine, held-out validation, publishing. Every action ends in the same services."""
from __future__ import annotations

from typing import Annotated, Any

from fastapi import APIRouter, Query
from pydantic import Field
from talyx_sdk import ContractError

from .object_routes import Input


class Vary(Input):
    name: str
    values: Annotated[list[Any], Field(max_length=20)]


class ModelPrep(Input):
    params: dict | None = None
    vary: Annotated[list[Vary], Field(max_length=4)] | None = None
    title: str | None = None

    def args(self, **path):
        out = {**path}
        if self.params is not None:
            out["params"] = self.params
        if self.vary is not None:
            out["vary"] = [item.model_dump() for item in self.vary]
        if self.title is not None:
            out["title"] = self.title
        return out


class InputInspection(Input):
    input: str
    feature: str | None = None


class DeepInstall(Input):
    variant: str | None = None


class Resume(Input):
    alone: bool | None = None
    override: dict | None = None


class RunIds(Input):
    ids: Annotated[list[str], Field(min_length=2, max_length=20)]


class Annotation(Input):
    note: str | None = None
    starred: bool | None = None


class ApplyModelRun(Input):
    expectedVersion: int | None = None


class ModelPublication(Input):
    name: Annotated[str, Field(min_length=1, max_length=120)]
    autoUpdate: bool | None = None


class Concurrency(Input):
    count: Annotated[int, Field(ge=1, le=64)]


def mount_models(router: APIRouter, services):
    models, library = services.models, services.model_library

    @router.get("/deep-environment")
    async def read_deep():
        return services.packages.deep_status()

    @router.post("/deep-environment/install")
    async def install_deep(body: DeepInstall):
        return services.packages.install_deep(body.variant)

    @router.post("/deep-environment/cancel")
    async def cancel_deep():
        return await services.packages.cancel_deep()

    @router.get("/objects/{object_id}/model-prep")
    async def read_prep(object_id: str):
        return await models.prep(object_id)

    @router.post("/objects/{object_id}/model-check")
    async def check(object_id: str, body: ModelPrep):
        return await models.check(object_id, body.args())

    @router.post("/objects/{object_id}/model-inputs/inspect")
    async def inspect(object_id: str, body: InputInspection):
        return await models.inspect(object_id, body.input, body.feature)

    @router.post("/objects/{object_id}/model-runs")
    async def train(object_id: str, body: ModelPrep):
        return await models.train(object_id, body.args())

    @router.get("/objects/{object_id}/model-runs")
    async def list_runs(object_id: str, since: int | None = None):
        return models.list(object_id, since)

    @router.post("/objects/{object_id}/model-reset")
    async def reset(object_id: str):
        return models.reset(object_id)

    @router.post("/objects/{object_id}/model-code")
    async def write_as_code(object_id: str):
        return await models.write_as_code(object_id)

    @router.get("/model-runs/compare-figure")
    async def compare_figure(ids: Annotated[list[str], Query()]):
        return models.compare_figure(ids[0].split(",") if len(ids) == 1 else ids)

    @router.get("/model-runs/{run_id}/held-out-plan")
    async def held_out_plan(run_id: str):
        return models.held_out_plan(run_id)

    @router.get("/model-runs/{run_id}/code")
    async def read_code(run_id: str):
        return models.code(run_id)

    @router.get("/model-runs/{run_id}/log")
    async def read_log(run_id: str):
        return models.log(run_id)

    @router.get("/model-runs/{run_id}")
    async def read_run(run_id: str):
        return models.experiment(run_id)

    @router.get("/model-runs/{run_id}/results")
    async def read_results(run_id: str, vs: str | None = None):
        return models.results(run_id, vs)

    @router.get("/model-runs/{run_id}/figures")
    async def read_figures(run_id: str, keys: Annotated[list[str] | None, Query()] = None, vs: str | None = None):
        return models.figures(run_id, keys, vs)

    @router.post("/model-runs/{run_id}/portfolio")
    async def portfolio(run_id: str):
        return await models.portfolio(run_id)

    @router.get("/model-runs/{run_id}/values-progress")
    async def read_values_progress(run_id: str):
        return models.values_progress(run_id)

    @router.get("/model-runs/{run_id}/values")
    async def read_values(run_id: str, date: int | None = None):
        return await models.values(run_id, date)

    @router.post("/model-runs/{run_id}/stop")
    async def stop(run_id: str):
        return await models.stop(run_id)

    @router.post("/model-batches/{batch_id}/stop")
    async def stop_batch(batch_id: str):
        return await models.stop(batch_id)

    @router.post("/model-runs/{run_id}/resume")
    async def resume(run_id: str, body: Resume):
        return await models.resume(run_id, body.args())

    @router.post("/model-runs/{run_id}/next")
    async def run_next(run_id: str):
        return await models.run_next(run_id)

    @router.delete("/model-runs/{run_id}/queue")
    async def remove(run_id: str):
        return await models.remove(run_id)

    @router.patch("/model-runs/{run_id}")
    async def annotate(run_id: str, body: Annotation):
        return models.annotate(run_id, body.args())

    @router.post("/model-runs/compare")
    async def compare(body: RunIds):
        return models.compare(body.ids)

    @router.post("/model-runs/combine")
    async def combine(body: RunIds):
        return await models.combine(body.ids)

    @router.post("/model-runs/{run_id}/held-out")
    async def held_out(run_id: str):
        return await models.held_out(run_id)

    @router.post("/model-runs/{run_id}/apply")
    async def apply(run_id: str, body: ApplyModelRun):
        return await models.apply(run_id, body.expectedVersion)

    @router.post("/model-runs/{run_id}/strategy")
    async def create_strategy(run_id: str):
        return await models.create_strategy(run_id)

    @router.get("/model-runs/{run_id}/publish-plan")
    async def publish_plan(run_id: str, name: str | None = None):
        return library.plan(run_id, name)

    @router.post("/model-runs/{run_id}/publish")
    async def publish(run_id: str, body: ModelPublication):
        return await library.publish(run_id, body.args())

    @router.get("/model-publications/{publication_id}")
    async def read_publication(publication_id: str):
        return library.read(publication_id)

    @router.post("/model-publications/{publication_id}/stop")
    async def stop_publication(publication_id: str):
        return await library.cancel(publication_id)

    @router.put("/model-concurrency")
    async def set_concurrency(body: Concurrency):
        return models.set_concurrency(body.count)


def handle_model_actions(ctx, services):
    models, packages = services.models, services.packages

    async def check_model(args, call):
        return await models.check(args["objectId"], {"vary":args["vary"]} if "vary" in args else {})

    async def inspect_model_input(args, call):
        answer = await models.inspect(args["objectId"],args["input"],args.get("feature"))
        return {k:v for k,v in answer.items() if k != "figures"}

    async def combine_model_runs(args, call):
        from .public_runs import receipt
        return receipt(await models.combine(args["runIds"]))

    async def set_model_concurrency(args, call):
        return models.set_concurrency(args["count"])

    async def read_model_figures(args, call):
        from .public_pages import complete_json
        if "runIds" in args:
            value = models.compare_figure(args["runIds"])
        else:
            value = models.figures(args["runId"], args.get("keys"), args.get("compareTo"))
        return await complete_json(ctx, value, "figures.json")

    async def manage_package_install(args, call):
        from .public_pages import complete_json
        if args.get("environment") == "deep":
            return await packages.cancel_deep()
        result = packages.start_install() if args["op"] == "install" else await packages.cancel()
        return await complete_json(ctx, result, "packages.json")

    async def read_environments(args, call):
        if args.get("part") == "packages":
            from .public_pages import complete_json
            return await complete_json(ctx, packages.status(), "packages.json")
        return {"deep":packages.deep_status(), "kernels":services.notebooks.list_environments()}

    async def install_deep_environment(args, call):
        packages.install_deep(args["variant"])
        await packages.deep_task
        status = packages.deep_status()
        if status["failure"]:
            raise ContractError("unavailable",str(status["failure"]))
        return status

    for fn in (read_model_figures,manage_package_install,check_model,inspect_model_input,combine_model_runs,set_model_concurrency,read_environments,install_deep_environment):
        ctx.handlers[fn.__name__] = fn
