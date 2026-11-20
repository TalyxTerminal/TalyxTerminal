"""Read and edit research objects through the public contract."""

from __future__ import annotations

from talyx_sdk import ContractError

from . import ai_views, library_inputs, public_settings, strategy_settings
from .object_store import NO_PARAMETERS
from .public_pages import complete_json, offset, page


def settings_view(groups, values, percent):
    """The standard pipeline's settings by group as callers set them: the public name (percent ones end in Pct), type,
    default, the current value where it differs, choices, bounds and what the screen says about it."""
    view = []
    for group in groups:
        items = []
        for item in group["settings"]:
            name = item["name"]
            entry = {"name": percent.get(name, name), "type": item["type"], "default": item["default"]}
            if name in percent:
                entry["unit"] = "%"
            if name in values and values[name] != entry["default"]:
                entry["value"] = values[name]
            entry.update({key: item[key] for key in ("options", "columns", "min", "max", "label", "help") if key in item})
            items.append(entry)
        view.append({"group": group["name"], "settings": items})
    return view


def last_run(last):
    if not last:
        return None
    return {
        "runId": last["id"],
        "state": last["state"],
        "createdAt": public_settings.timestamp(last["createdAt"]),
    }


async def summary(services, record):
    store = services.store
    settings, _ = await public_settings.settings(store.ctx, record)
    kind = record["kind"]
    effective, errors = {}, []
    if kind != "notebook":
        # What the code editor's check shows: syntax and the settings the code declares. While the code has errors
        # its params are unknown, and a run would fail on the first one.
        detail = store.detail(record)
        errors = detail["errors"]
        effective["params"] = None if errors else public_settings.params_out(
            record, store.parameter_values(record, detail["parameters"]))
        effective["run"] = public_settings.run_out(record, dict(detail["settings"].get("run") or {}))
    if kind == "strategy":
        saved = record["settings"]
        data = saved["data"]
        instruments = [
            item["instrument"]
            for item in (
                store.lists.members(data["list"]) if data.get("list") else data.get("symbols") or []
            )
        ]
        traded = (
            "stock"
            if data.get("index")
            else strategy_settings.traded(instruments)
            if instruments
            else "perpetual"
        )
        account = saved.get("account") or {}
        effective["run"] = strategy_settings.effective(
            traded,
            saved.get("run"),
            account,
            account.get("fee"),
            "account settings" if account.get("fee") else None,
            held_out=data.get("heldOutFrom"),
            names=len(instruments) or 1,
        )
    history = services.home.runs_by_object(record["id"]).get(record["id"], [])
    _, chosen = services.home.representative(
        history, services.library.store.published_runs() if history else {}
    )
    return {
        "objectId": record["id"],
        "lastRun": last_run(store.last_run(record["id"])) if store.last_run else None,
        "libraryInputs": library_inputs.saved_versions(record["settings"]),
        "representative": {
            "runId": chosen["run"],
            "number": chosen["number"],
            "how": chosen["how"],
        },
        "mode": record["settings"].get("mode"),
        "kind": kind,
        "name": record["name"],
        "version": record["version"],
        "folder": record["folder"],
        "workspaceId": record["workspaceId"],
        "settings": settings,
        "effective": effective,
        "codeErrors": errors,
        "entryUri": store.source_path(record).as_uri(),
        **({"formula": record["settings"]["formula"]} if "formula" in record["settings"] else {}),
    }


def handle_objects(ctx, services):
    store = services.store

    async def list_objects(args, call):
        found = services.home.objects(args)
        rows = []
        for item in found["items"]:
            representative = item["representative"]
            rows.append(
                {
                    "objectId": item["id"],
                    "name": item["name"],
                    "kind": item["kind"],
                    "preset": item.get("preset"),
                    "folder": item["folder"],
                    "workspaceId": item["workspaceId"],
                    "representative": {
                        "runId": representative["run"],
                        "number": representative["number"],
                        "how": representative["how"],
                    },
                    "lastRun": last_run(item["lastRun"]),
                    "numbers": {
                        k: v
                        for k, v in (item["keyNumbers"] or {}).items()
                        if type(v) in (int, float)
                    },
                    "library": item["library"],
                }
            )
        return page(rows, args, counts=found["kinds"], folders=found["folders"])

    async def read_object(args, call):
        obj = store.read(args["objectId"])
        part = args.get("part", "summary")
        if "cursor" in args and part != "code":
            raise ContractError("invalid_input", "cursor applies to part code.")
        if part == "notebook":
            found = await services.notebooks.files.read(obj["id"])
            document = await complete_json(ctx, {"version": found["version"], "notebook": found["notebook"]}, "notebook.json")
            return {"objectId": obj["id"], **document}
        if part == "dataDefaults":
            return {"objectId": obj["id"], "options": await complete_json(ctx, await services.data_defaults(obj["id"]), "data-defaults.json")}
        if part == "summary":
            return await summary(services, obj)
        if part == "code":
            source = store.code(obj)
            if obj["kind"] == "notebook":
                source = ai_views.notebook_text(source)
            chunk = ai_views.chunk_files([{"file": obj["entry"], "text": source}], offset(args))
            return {
                "entryUri": store.source_path(obj).as_uri(),
                "files": chunk["files"],
                "total": chunk["chars"],
                "nextCursor": str(chunk["nextOffset"]) if chunk["nextOffset"] is not None else None,
            }
        kind, identity = obj["kind"], obj["id"]
        standard = obj["settings"].get("mode", "standard") == "standard"
        if kind == "model" and standard:
            found = ai_views.model_prep(await services.models.prep(identity))
            return {"objectId": identity, "options": ai_views.bound({
                "params": settings_view(found["groups"], found["values"], {}),
                "algorithm": found["algorithm"], "algorithms": found["algorithms"],
                "inputsAvailable": found["offers"], "run": public_settings.run_out(obj, found["run"]),
                "batchLimit": found["batchLimit"],
                "concepts": services.models.concepts(identity)})}
        if kind == "allocation" and standard:
            found = ai_views.terse(ai_views.compact(await services.allocation.prep(identity)))
            return {"objectId": identity, "options": ai_views.bound({
                "params": settings_view(found["groups"], found["values"], public_settings.PERCENT),
                "objectives": {name: sorted(key for key, value in supports.items() if value is True)
                               for name, supports in found["capabilities"].items()},
                "pool": found["pool"]})}
        if kind == "factor":
            fields = await services.factors.fields(identity)
        elif kind == "strategy":
            fields = await services.strategy.run_settings(identity)
            fields["inputs"] = services.strategy.inputs(identity)
        else:
            fields = {}
        declared = store.project_parameters(obj) if kind != "notebook" else NO_PARAMETERS
        return {
            "objectId": identity,
            "options": ai_views.bound({**fields, "parameters": ai_views.compact(declared["parameters"]),
                                       "errors": declared["errors"]}),
        }

    async def edit(obj, args):
        store.check_version(obj, args.get("expectedVersion"))
        if "code" in args:
            if not isinstance(args["code"], str) or "formula" in args:
                raise ContractError("invalid_input", "Supply a code file as text, or a formula.")
            obj = store.code_target(obj)
        before = {k: obj[k] for k in ("name", "folder")}
        if "formula" in args:
            before["formula"] = obj["settings"].get("formula")
        if "libraryInputs" in args:
            before["libraryInputs"] = (await summary(services, obj))["libraryInputs"]
        updates, changes = await public_settings.prepare_update(store, obj, args)
        meta = store.metadata(obj, args)
        pins = args.get("libraryInputs") or {}
        for name, version_id in pins.items():
            version = services.library.read_version(version_id)
            if version["name"] != name:
                raise ContractError(
                    "invalid_input", f"libraryInputs.{name} needs a version of {name}."
                )
            if version["state"] == "delisted" or version["archivedAt"] is not None:
                raise ContractError("conflict", f"{name} no longer produces values.")
        if pins and obj["kind"] == "strategy":
            updates = {**updates, "inputs": store.strategy_bindings(obj, updates, pins, args.get("code"))}
        store.check_version(store.read(obj["id"]), obj["version"])
        if "code" in args:
            meta["developer"] = await store.write_entry(obj, obj["entry"], args["code"])
            store.check_version(store.read(obj["id"]), obj["version"])
        if meta.get("folder"):
            store.register_folder(obj["kind"], meta["folder"])
        if "code" in args or updates != obj["settings"] or any(v != obj[k] for k, v in meta.items()):
            store.save(obj, settings=updates, **meta)
        if obj["kind"] != "strategy":
            for version_id in pins.values():
                services.home.switch_version(obj["id"], version_id)
        result = await summary(services, store.read(obj["id"]))
        return {
            **result,
            "changed": [*changes, *public_settings.changed(before, {k: result[k] for k in before})],
        }

    async def update_object(args, call):
        return await edit(store.read(args["objectId"]), args)

    async def create_object(args, call):
        sources = [
            key
            for key in ("copyOf", "preset", "fromRun", "fromVersion", "fromNotebook")
            if args.get(key) is not None
        ]
        if len(sources) > 1 or "cell" in args and "fromRun" not in sources:
            raise ContractError(
                "invalid_input", "Choose one object source. cell selects a combination of fromRun."
            )
        kind, name = args["kind"], args.get("name")
        if "copyOf" in sources:
            if store.read(args["copyOf"])["kind"] != kind:
                raise ContractError("invalid_input", "The source to copy has a different kind.")
            created = await store.copy(args["copyOf"], name)
        elif "fromVersion" in sources:
            if kind == "notebook":
                version = services.library.read_version(args["fromVersion"])
                made = await services.notebooks.open_library_item(version["name"], version["version"], version["kind"])
                created = {"id": made["notebookId"]}
            else:
                created = await services.home.create_from_library(args["fromVersion"], kind, name)
        elif "fromRun" in sources:
            source = services.runs.read(args["fromRun"])
            if source["objectKind"] == "factor":
                created = await services.factors.create_from(source["id"], kind, args.get("cell"))
            elif source["objectKind"] == "model" and kind == "strategy" and "cell" not in args:
                created = await services.models.create_strategy(source["id"])
            else:
                raise ContractError(
                    "invalid_input",
                    "A factor run can start a model or strategy. A model run can start a strategy.",
                )
        elif "fromNotebook" in sources:
            extracted = {**args["fromNotebook"], "kind": kind, "name": name or "Untitled " + kind}
            shown = await services.notebooks.extract_view(extracted)
            created = await services.notebooks.create_from_notebook(extracted)
        else:
            data = args.get("data", {})
            default_universe = data is not None and all(data.get(key) is None for key in public_settings.SELECTIONS)
            if kind == "strategy" and data and isinstance(data.get("symbols"), dict):
                default_universe = True  # Add/remove edits start with the preset's own symbols.
            created = await store.create(
                {k: args[k] for k in ("kind", "name", "folder", "preset") if k in args},
                default_universe=default_universe,
            )
        try:
            values = {
                k: args[k]
                for k in ("code", "data", "run", "params", "formula", "libraryInputs")
                if k in args
            }
            if sources and sources[0] not in {"preset", "copyOf"} and name:
                values["name"] = name
            if sources and args.get("folder"):
                values["folder"] = args["folder"]
            obj = store.read(created["id"])
            result = await edit(obj, values) if values else await summary(services, obj)
            if "fromNotebook" in sources:
                result["notTaken"] = shown["notTaken"]
            return result
        except (ContractError, ValueError):
            store.delete(created["id"])
            raise

    async def write_as_code(args, call):
        obj = store.read(args["objectId"])
        if "formula" in args and obj["kind"] != "factor":
            raise ContractError("invalid_input", "formula is used only by formula factors.")
        if obj["kind"] == "factor":
            await services.factors.to_code(
                obj["id"], args.get("formula", obj["settings"].get("formula", ""))
            )
        elif obj["kind"] == "model":
            await services.models.write_as_code(obj["id"])
        elif obj["kind"] == "allocation":
            await services.allocation.write_as_code(obj["id"])
        else:
            raise ContractError(
                "invalid_input",
                "write_as_code takes a standard model, allocation or formula factor.",
            )
        return await summary(services, store.read(obj["id"]))

    for handler in (list_objects, read_object, update_object, create_object, write_as_code):
        ctx.handlers[handler.__name__] = handler
