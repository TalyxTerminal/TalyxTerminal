"""Run the declared SDK actions against Quant's services and validate their actual wire responses."""
import json
from types import SimpleNamespace
from uuid import uuid4

from talyx_sdk.process import Call, Caller, Codec

from modules.quant.backend import entry
from modules.quant.backend.allocation_routes import handle_allocation_actions
from modules.quant.backend.library_routes import handle_library_actions
from modules.quant.backend.model_routes import handle_model_actions
from modules.quant.backend.notebook_routes import NotebookServices, handle_notebook_actions
from modules.quant.backend.object_routes import handle_actions


def public_actions(ctx, shell, monkeypatch):
    for register in (handle_actions, handle_model_actions, handle_allocation_actions, handle_library_actions):
        register(ctx, shell)
    notebooks = NotebookServices(ctx, shell.store.records, shell.store, shell.common, shell.packages)
    notebooks.runs = shell.runs
    shell.notebooks = notebooks
    monkeypatch.setitem(entry.state, "notebooks", notebooks)
    handle_notebook_actions(ctx, notebooks)
    return sdk_actions(ctx, shell.store.records, monkeypatch)


def sdk_actions(ctx, records, monkeypatch):
    monkeypatch.setattr(entry, "quant", ctx)
    monkeypatch.setitem(entry.state, "records", records)

    async def invoke(name, values=None, *, origin="user"):
        spec = entry.module.actions[name]
        args = Codec(spec.input).parse(json.dumps(values or {}).encode())
        call = Call(None, Caller(None, "ws", origin), str(uuid4()))
        result = await spec.handler(args, call)
        return json.loads(Codec(spec.output).check(result))

    return invoke


def service_actions(monkeypatch, register, services):
    """Use a single family's existing fixture, with the same public handler registration as Quant startup."""
    ctx = SimpleNamespace(handlers={})
    register(ctx, services)
    return sdk_actions(ctx, None, monkeypatch)


def library_actions(monkeypatch, **services):
    return service_actions(monkeypatch, handle_library_actions, SimpleNamespace(**{
        "library":None, "publisher":None, "lists":None, "history":None, "research_bars":None, "home":None, **services}))
