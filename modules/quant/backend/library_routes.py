"""Private routes and public actions of the library, the home page, research lists, the common code folder and Quant's notification settings.

Every route that reads the library's store runs on a worker thread: the list, an item's page and a day of values never hold the Host's loop."""
from __future__ import annotations

import asyncio
from typing import Annotated, Literal

from fastapi import APIRouter, HTTPException, Query
from fastapi.responses import FileResponse
from pydantic import Field
from talyx_sdk import ContractError

from . import library_ai
from .object_routes import Input
from .public_inputs import TableQuery
from .result_tables import read_result_table


class Publication(Input):
    name: Annotated[str, Field(min_length=1, max_length=120)]
    items: list[str] | None = None
    checks: Annotated[int, Field(ge=1, le=3)] | None = None
    auto: bool | None = None


class SwitchVersion(Input):
    versionId: Annotated[str, Field(min_length=1)]


class ListCreation(Input):
    name: str
    members: list[dict] | None = None


class ListUpdate(Input):
    name: str | None = None
    members: list[dict] | None = None


class RowsPage(Input):
    field: str | None = None
    instrument: str | None = None
    start: int | None = None
    end: int | None = None
    asOf: int | None = None
    latest: bool | None = None
    offset: Annotated[int, Field(ge=0)] | None = None
    limit: Annotated[int, Field(ge=1, le=20_000)] | None = None


class DayPage(Input):
    date: int | None = None
    order: Literal["top", "bottom", "missing"] = "top"
    q: str | None = None
    page: Annotated[int, Field(ge=0)] = 0
    field: str | None = None


class Auto(Input):
    on: bool


class Stop(Input):
    dryRun: bool = False


def thread(function, *args, **kwargs):
    return asyncio.to_thread(function, *args, **kwargs)


def mount_library(router: APIRouter, services):
    library, publisher, lists, common, history, home = (services.library, services.publisher, services.lists, services.common,
                                                        services.history, services.home)

    @router.get("/library")
    async def list_library(query: str | None = None, kind: str | None = None, state: str | None = None, notebook: bool = False):
        listing = await thread(library.list, {key: value for key, value in {"query": query, "kind": kind, "state": state}.items() if value})
        if notebook:
            from .notebook_routes import python_name
            for item in listing["items"]:
                item["variable"] = python_name(item["name"])
        return listing

    @router.get("/library/items/{item_id}")
    async def read_item(item_id: str, versionId: str | None = None, metric: Literal["ic", "ls"] = "ic", span: Literal["since", "all"] = "since"):
        return await thread(library.detail, item_id, versionId, metric, span)

    @router.get("/library/items/{item_id}/updates")
    async def item_updates(item_id: str, limit: Annotated[int, Query(ge=1, le=500)] = 50):
        return await thread(library.updates, {"itemId": item_id, "limit": limit})

    @router.get("/library/versions/{version_id}")
    async def read_version(version_id: str):
        return await thread(library.read_version, version_id)

    @router.get("/library/versions/{version_id}/updates")
    async def version_updates(version_id: str, limit: Annotated[int, Query(ge=1, le=500)] = 50):
        return await thread(library.updates, {"versionId": version_id, "limit": limit})

    @router.get("/library/versions/{version_id}/log")
    async def read_log(version_id: str):
        return await thread(library.log, version_id)

    @router.get("/library/versions/{version_id}/values")
    async def read_day(version_id: str, query: Annotated[DayPage, Query()]):
        return await thread(library.values_page, version_id, date=query.date, order=query.order, query=query.q, page=query.page, field=query.field)

    @router.get("/library/versions/{version_id}/rows")
    async def read_rows(version_id: str, query: Annotated[RowsPage, Query()]):
        return await library.values({**query.args(), "versionId": version_id})

    @router.get("/library/versions/{version_a}/compare/{version_b}")
    async def compare(version_a: str, version_b: str):
        return await thread(library.compare, version_a, version_b)

    @router.put("/library/versions/{version_id}/auto")
    async def set_auto(version_id: str, body: Auto):
        return library.set_auto(version_id, body.on)

    @router.post("/library/versions/{version_id}/retry")
    async def retry(version_id: str):
        return library.retry(version_id)

    @router.post("/library/versions/{version_id}/cancel")
    async def cancel(version_id: str):
        return await services.loop.cancel(version_id)

    @router.post("/library/versions/{version_id}/stop")
    async def stop(version_id: str, body: Stop):
        return library.stop(version_id, dry_run=body.dryRun)

    @router.post("/library/versions/{version_id}/resume")
    async def resume(version_id: str):
        return library.resume(version_id)

    @router.post("/library/versions/{version_id}/archive")
    async def archive(version_id: str):
        return await library.archive(version_id)

    @router.get("/library/versions/{version_id}/report")
    async def read_report(version_id: str):
        return await thread(library.report, version_id)

    @router.get("/library/versions/{version_id}/files/{name:path}")
    async def report_file(version_id: str, name: str):
        try:
            return FileResponse(await thread(library.report_file, version_id, name))
        except ContractError as error:
            raise HTTPException(404, str(error)) from None

    @router.post("/library/versions/{version_id}/table")
    async def read_table(version_id: str, body: TableQuery):
        page = await asyncio.to_thread(read_result_table, library.report_file(version_id, body.name), body.args())
        return {**page, "assetLabels": {}}

    @router.get("/library/versions/{version_id}/source")
    async def read_source(version_id: str, name: str | None = None):
        if name is None:
            return {"files": await thread(library.source_files, version_id)}
        return await thread(library.source_file, version_id, name)

    @router.get("/object-runs/{run_id}/publish-plan")
    async def plan_factor_publication(run_id: str, name: str | None = None):
        return publisher.plan_factor(run_id, name)  # reads the run records, which belong to the loop's thread

    @router.post("/object-runs/{run_id}/publish")
    async def publish(run_id: str, body: Publication):
        return history.summary(await publisher.start(run_id, body.args()))

    @router.post("/objects/{object_id}/library-version")
    async def switch_object_version(object_id: str, body: SwitchVersion):
        return home.switch_version(object_id, body.versionId)

    @router.get("/quant/attention")
    async def read_attention():
        return home.attention()

    @router.get("/quant/queue")
    async def read_queue():
        return home.queue()

    @router.get("/research-lists")
    async def list_lists():
        return lists.list()

    @router.post("/research-lists")
    async def create_list(body: ListCreation):
        return await lists.create(body.args())

    @router.get("/research-lists/{list_id}")
    async def read_list(list_id: str):
        return lists.detail(lists.read(list_id))

    @router.patch("/research-lists/{list_id}")
    async def update_list(list_id: str, body: ListUpdate):
        return await lists.update(list_id, body.args())

    @router.delete("/research-lists/{list_id}")
    async def delete_list(list_id: str):
        return lists.delete(list_id)

    @router.get("/common-code")
    async def read_common():
        return await common.read()

    @router.post("/common-code/open")
    async def open_common():
        return await common.open()



def handle_library_actions(ctx, services):
    from . import market_inputs as market
    from .model_inputs import binding
    from .public_pages import complete_json, page, table
    from .public_runs import receipt
    from .public_settings import changed, edit_list, milliseconds, timestamp

    library, lists, home = services.library, services.lists, services.home

    async def plan_publication(args, call):
        run = services.runs.read(args["runId"])
        name = args.get("name") or run["name"]
        if run["objectKind"] == "factor":
            return services.publisher.plan_factor(run["id"], name)
        if run["objectKind"] == "model":
            return services.model_library.plan(run["id"], name)
        if run["objectKind"] == "allocation":
            return services.allocation.plan_publication(run["id"], name)
        raise ContractError("invalid_input","A strategy cannot be published to this library.")

    async def publish_run(args, call):
        run = services.runs.read(args["runId"])
        if run["objectKind"] == "model":
            published = await services.model_library.publish(run["id"],args)
        else:
            published = await services.publisher.start(run["id"],{"name":args["name"],"auto":args.get("autoUpdate",True)})
        return receipt(published)

    async def list_library(args, call):
        found = await thread(library.list,args)
        rows = [library_ai.item_row(item, 5) for item in found["items"]]
        return page(rows, args, needsAttention=found["needsAttention"], archivable=found["archivable"],
                    nextRound=found["nextRound"], production=found["production"])

    async def read_library_item(args, call):
        if bool(args.get("itemId")) == bool(args.get("name")):
            raise ContractError("invalid_input","Choose either name or itemId.")
        if args.get("itemId"):
            identity = args["itemId"]
        else:
            found = library.store.find_item(args["name"])
            if not found:
                raise ContractError("not_found","Library item not found.")
            identity = found[0]["id"]
        result = await thread(library_ai.detail,library,identity,args.get("versionId"))
        if args.get("compareTo"):
            result["compare"] = await thread(library_ai.compare,library,result["versionId"],args["compareTo"])
        return result

    async def list_common_code(args, call):
        result = await services.common.read()
        return await complete_json(ctx, result, "common-code.json")

    async def read_library_material(args, call):
        identity, part = args["versionId"], args.get("part", "report")
        await thread(library.store.version, identity)
        if part == "report":
            value = await thread(library.report, identity)
        elif part == "source":
            if "name" in args:
                value = await thread(library.source_file, identity, args["name"])
            else:
                value = {"files": await thread(library.source_files, identity)}
        elif part == "file":
            path = await thread(library.report_file, identity, args["name"])
            return {"file": await ctx.hand_over(path, path.name)}
        else:
            query = args["query"]
            path = await thread(library.report_file, identity, query["name"])
            value = await thread(read_result_table, path, query)
        return await complete_json(ctx, value, part + ".json")

    async def read_library_log(args, call):
        found = await thread(library.log,args["versionId"])
        return await table(ctx,found["periods"],args)

    async def read_library_model(args, call):
        version = library.resolve(args["name"],args.get("version"),"model")
        found = await thread(binding,library,args["name"],version_id=version["versionId"])
        return {k:found[k] for k in ("name","versionId","version","trainingCutoff","model")}

    async def read_library_values(args, call):
        if bool(args.get("versionId")) == bool(args.get("name")) or "version" in args and not args.get("name"):
            raise ContractError("invalid_input","Choose name and optionally version, or versionId.")
        version = args.get("versionId") or library.resolve_values(args["name"],args.get("version"))["versionId"]
        if args.get("date"):
            if any(k in args for k in ("start","end")):
                raise ContractError("invalid_input","date selects a cross section; use start and end for history.")
            opts = {"date":milliseconds(args["date"]),"order":args.get("order","top"),"field":args.get("field")}
            if args.get("asOf"):
                opts["as_of"] = milliseconds(args["asOf"], end=True)
            found = await thread(library.values_page,version,**opts)
            raw = list(found["rows"])
            for index in range(1,found.get("pages",0)):
                raw.extend((await thread(library.values_page,version,**opts,page=index))["rows"])
        else:
            if "order" in args:
                raise ContractError("invalid_input","order applies to a dated cross section.")
            bounds = {k:milliseconds(args[k],end=k in {"asOf","end"}) for k in ("start","end") if args.get(k)}
            if "end" in bounds:
                bounds.setdefault("asOf",bounds["end"])
            raw = (await library.values({"versionId":version,"limit":2**63-1,"field":args.get("field"),**bounds}))["rows"]
        selected = {item["instrument"]["instrumentId"] for item in await market.symbol_items(ctx,args["symbols"])} if args.get("symbols") else None
        if selected is not None:
            raw = [row for row in raw if (row.get("instrumentId") or row.get("instrument")) in selected]
        total = len(raw)
        from .public_pages import offset
        start = offset(args) if args.get("format") != "file" else 0
        if args.get("format") != "file":
            raw = raw[start:start+args.get("limit",25)]
        identities = list(dict.fromkeys(r.get("instrumentId") or r.get("instrument") for r in raw if r.get("instrumentId") or r.get("instrument")))
        labels = {}
        for position in range(0,len(identities),500):
            batch = identities[position:position+500]
            for identity,item in zip(batch,await market.lookup(ctx,"symbols",batch),strict=True):
                labels[identity] = item["symbol"] if item["status"] in {"found","redirect"} else identity
        rows = []
        for original in raw:
            row = dict(original)
            identity = row.get("instrumentId") or row.pop("instrument",None)
            if selected is not None and identity not in selected:
                continue
            if identity is not None:
                row.update(instrumentId=identity,symbol=labels[identity])
            if "datetime" in row:
                row["date"] = timestamp(row.pop("datetime"))
            for key in ("availableAt","recordedAt"):
                if row.get(key) is not None:
                    row[key] = timestamp(row[key])
            rows.append(row)
        columns = ["date","symbol","instrumentId","field","value","availableAt","recordedAt"] if not args.get("date") else None
        result = await table(ctx,rows,{**args,"cursor":None},columns=columns)
        result["total"] = total
        if "rows" in result:
            finish = start + len(result["rows"])
            result["nextCursor"] = str(finish) if finish < total else None
        version_row = library.store.version(version)
        result["meta"] = {"versionId":version,"version":version_row["version"], "state":version_row["state"],
                          "publishedAt":timestamp(version_row["created_at"]),"producedByRun":version_row["run_id"]}
        return result

    async def publish_library_values(args, call):
        names = list(dict.fromkeys(row["symbol"] for row in args["rows"]))
        labels = {}
        for start in range(0,len(names),500):
            batch = names[start:start+500]
            labels.update((label,item["instrument"]["instrumentId"]) for label,item in zip(batch,await market.symbol_items(ctx,batch),strict=True))
        rows = []
        for row in args["rows"]:
            if bool(row.get("date")) == bool(row.get("time")):
                raise ContractError("invalid_input","A value row needs one date or time.")
            rows.append({"datetime":milliseconds(row.get("date") or row["time"]),"instrument":labels[row["symbol"]],
                         "field":row["field"],"value":row["value"]})
        return await library.publish_values({**args,"rows":rows})

    async def update_library_version(args, call):
        identity = args["versionId"]
        if args.get("dryRun") and (args.get("producing") is not False or "autoUpdate" in args):
            raise ContractError("invalid_input","dryRun previews stopping one version.")
        if "producing" in args:
            result = library.resume(identity) if args["producing"] else library.stop(identity,dry_run=args.get("dryRun",False))
            if args.get("dryRun"):
                return result
        if "autoUpdate" in args:
            library.set_auto(identity,args["autoUpdate"])
        version = library.read_version(identity)
        return {"versionId":identity,"state":version["state"],"autoUpdate":version["auto"]}

    async def retry_library_version(args,call):
        return library.retry(args["versionId"])

    async def cancel_library_update(args,call):
        return await services.loop.cancel(args["versionId"])

    async def archive_library_version(args,call):
        library.archivable(args["versionId"])
        return await library.archive(args["versionId"])

    async def read_quant_attention(args,call):
        answer = home.attention()
        rows = []
        for row in answer["items"]:
            action, subject = row["action"], row["subject"]
            version = subject.get("versionId")
            operation = action["id"]
            if operation == "review_period":
                name = "review_allocation_period"
                values = {"versionId":version,"period":timestamp(action["period"])[:10],"how":"accept"}
            elif operation == "switch_version" and action.get("versionId"):
                name = "update_object"
                version_row = library.read_version(action["versionId"])
                values = {"objectId":subject["id"],"libraryInputs":{version_row["name"]:action["versionId"]}}
            elif operation in {"retry","check_source"}:
                name, values = "retry_library_version", {"versionId":version}
            elif action.get("objectId"):
                name, values = "read_object", {"objectId":action["objectId"]}
            else:
                name, values = "read_library_item", {"itemId":action.get("itemId") or subject["id"]}
            rows.append({**row,"action":"quant."+name,"input":values,"since":timestamp(row["since"]) if row.get("since") else None})
        return page(rows,args,watchInLibrary=answer["watchInLibrary"])

    async def read_queue(args,call):
        found = home.queue()
        result = page(found["jobs"],args,production=found["production"])
        result["jobs"] = result.pop("items")
        return result

    async def read_research_bars(args,call):
        return await services.research_bars.export(args,progress=call.progress)

    async def list_research_lists(args,call):
        return page([{"listId":r["id"],"name":r["name"],"count":r["count"]} for r in lists.list()["items"]],args)

    async def read_research_list(args,call):
        row = lists.read(args["listId"])
        symbols = [item["symbol"] for item in await market.saved_items(ctx,row["members"])]
        return {"listId":row["id"],"name":row["name"],"symbols":symbols}

    async def create_research_list(args,call):
        items = await market.symbol_items(ctx,args["symbols"])
        result = await lists.create({"name":args["name"], "members":[{"instrument":item["instrument"]} for item in items]})
        return {"listId":result["id"],"name":result["name"],"symbols":[item["symbol"] for item in items]}

    async def update_research_list(args,call):
        before = await read_research_list(args,call)
        edits = {k:args[k] for k in ("add","remove") if k in args}
        if "symbols" in args and edits:
            raise ContractError("invalid_input","Choose symbols or add/remove.")
        changes = {"name":args["name"]} if args.get("name") else {}
        if "symbols" in args or edits:
            names = args["symbols"] if "symbols" in args else edit_list(before["symbols"],edits,"symbols")
            kept = dict(zip(before["symbols"],lists.read(args["listId"])["members"],strict=True))
            new = [n for n in names if n not in kept]
            kept.update((n,item["instrument"]) for n,item in zip(new,await market.symbol_items(ctx,new),strict=True))
            changes["members"] = [{"instrument":kept[n]} for n in names]
        await lists.update(args["listId"],changes)
        after = await read_research_list(args,call)
        return {**after,"changed":changed(before,after)}

    async def delete_research_list(args,call):
        return lists.delete(args["listId"])

    for fn in (list_common_code,read_library_material,plan_publication,publish_run,list_library,read_library_item,read_library_log,read_library_values,
               read_library_model,publish_library_values,update_library_version,retry_library_version,cancel_library_update,
               archive_library_version,read_quant_attention,read_queue,read_research_bars,list_research_lists,
               read_research_list,create_research_list,update_research_list,delete_research_list):
        ctx.handlers[fn.__name__] = fn
