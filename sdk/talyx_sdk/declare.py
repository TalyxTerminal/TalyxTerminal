"""How a module declares what it offers: decorators over typed Python functions.

The functions' types (pydantic) are the schema; their docstrings are what people and AI read. The contract
(``public/contract.json``), the TypeScript client and the Python client are generated from these declarations
by ``scripts/contracts/generate.py``; nobody writes JSON Schema by hand.

    module = Module("example")

    @module.action(effects=["read"])
    async def search(input: SearchInput, call: Call) -> SearchOutput:
        "Find items by code or name."

Effects say what an action does beyond answering: ``read`` (only reads), ``write`` (changes something), ``delete``,
``code`` (runs code), ``external`` (sends something out of this machine), ``funds`` (moves money, also when it does
so indirectly). AI clients decide from these whether to ask the user first. ``target`` names the input field
holding the object the action acts on; an action whose effects depend on an operation declares them per branch
(``{"path": ..., "branches": {op: {"effects": [...], "target": ...}}}``), each branch with its own target.
"""

from __future__ import annotations

import inspect
import typing
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Annotated, Any, Literal

from pydantic import BaseModel, ConfigDict, Field, TypeAdapter

EFFECTS = ("read", "write", "delete", "code", "external", "funds")
Effect = Literal["read", "write", "delete", "code", "external", "funds"]


class Model(BaseModel):
    """Base for declared types: JSON numbers must be finite, unknown fields are refused."""

    model_config = ConfigDict(extra="forbid", allow_inf_nan=False, use_attribute_docstrings=True)


FieldPath = Annotated[str, Field(pattern=r"^[A-Za-z_][A-Za-z_0-9]*(\.[A-Za-z_][A-Za-z_0-9]*)*$")]
Caller = Literal["user", "ai", "module", "code"]


class EffectBranch(Model):
    effects: Annotated[list[Effect], Field(min_length=1)]
    target: FieldPath | None = None


class EffectBranches(Model):
    path: FieldPath
    branches: Annotated[dict[str, EffectBranch], Field(min_length=1)]


@dataclass
class ActionSpec:
    name: str
    handler: Callable
    input: Any
    output: Any
    effects: tuple[str, ...]
    task: bool
    description: str
    examples: tuple[dict, ...] = ()
    errors: tuple[str, ...] = ()
    callers: tuple[Caller, ...] | None = None
    effect_branches: EffectBranches | None = None
    target: str | None = None

    @property
    def title(self) -> str:
        """What people read for a running call of a long task: its name in words (the description is for AI)."""
        return self.name.replace(".", " ").replace("_", " ").capitalize()


@dataclass
class StreamSpec:
    name: str
    handler: Callable
    params: Any
    item: Any
    description: str
    cursor: bool = False
    trigger: bool = False


@dataclass
class EventSpec:
    name: str
    payload: Any
    description: str


@dataclass
class ExtensionPointSpec:
    """An interface the owner defines and others implement: actions and streams every implementation provides."""

    name: str
    description: str
    descriptor: Any
    actions: dict[str, tuple[Any, Any, str]] = field(default_factory=dict)
    streams: dict[str, tuple[Any, Any, str]] = field(default_factory=dict)


@dataclass
class SettingSpec:
    key: str
    type: Any
    default: Any
    title: str
    description: str
    global_only: bool = False
    group: str = ""


def _hints(function: Callable) -> tuple[Any, Any, str]:
    hints = typing.get_type_hints(function, include_extras=True)
    parameters = list(inspect.signature(function).parameters)
    if not parameters:
        raise TypeError(f"{function.__qualname__} takes its input as the first parameter")
    first = hints.get(parameters[0])
    if first is None or "return" not in hints:
        raise TypeError(f"{function.__qualname__} needs a typed input and a typed return")
    return first, hints["return"], inspect.cleandoc(function.__doc__ or "")


def _item_type(annotation: Any) -> Any:
    """``AsyncIterator[Item]`` → ``Item``."""
    args = typing.get_args(annotation)
    if not args:
        raise TypeError("A stream returns AsyncIterator[Item]")
    return args[0]


class Module:
    """Everything one module declares. The module's backend entry exposes one of these as ``module``."""

    def __init__(self, id: str):
        self.id = id
        self.actions: dict[str, ActionSpec] = {}
        self.streams: dict[str, StreamSpec] = {}
        self.events: dict[str, EventSpec] = {}
        self.extension_points: dict[str, ExtensionPointSpec] = {}
        self.settings: dict[str, SettingSpec] = {}
        # point -> implementation id -> {"actions": {name: fn}, "streams": {name: fn}}
        self.implementations: dict[str, dict[str, dict[str, dict[str, Callable]]]] = {}
        self.starters: list[Callable] = []
        self.copiers: list[Callable] = []
        self.change_handlers: type | None = None
        self.workspace_deleter: Callable | None = None
        self.routes: Callable | None = None

    def _name(self, name: str | None, function: Callable) -> str:
        return name or function.__name__

    def action(self, name: str | None = None, *, effects: list[str] | dict, task: bool = False,
               examples: list[dict] = (), errors: list[str] = (), callers: list[Caller] | None = None,
               target: str | None = None) -> Callable:
        branches = None
        if target is not None:
            if isinstance(effects, dict):
                raise ValueError("An action with effect branches declares the target in each branch")
            TypeAdapter(FieldPath).validate_python(target)
        if isinstance(effects, dict):
            branches = EffectBranches.model_validate(effects)
            effects = list(dict.fromkeys(effect for branch in branches.branches.values() for effect in branch.effects))
        unknown = set(effects) - set(EFFECTS)
        if unknown or not effects:
            raise ValueError(f"effects must be some of {EFFECTS}: {sorted(unknown) or 'none given'}")
        if callers is not None:
            TypeAdapter(Annotated[list[Caller], Field(min_length=1)]).validate_python(callers)

        def register(function):
            input_type, output_type, description = _hints(function)
            key = self._name(name, function)
            if not description:
                raise TypeError(f"Action {key} needs a docstring: it is what people and AI read")
            self.actions[key] = ActionSpec(key, function, input_type, output_type, tuple(effects), task,
                                           description, tuple(examples), tuple(errors),
                                           tuple(callers) if callers is not None else None, branches, target)
            return function
        return register

    def stream(self, name: str | None = None, *, cursor: bool = False, trigger: bool = False) -> Callable:
        """An async generator ``(params, call) -> AsyncIterator[Item]``. ``trigger`` streams can be read again
        from a cursor, so a consumer that was away misses nothing."""

        def register(function):
            params_type, returned, description = _hints(function)
            key = self._name(name, function)
            if not description:
                raise TypeError(f"Stream {key} needs a docstring")
            if trigger and not cursor:
                raise ValueError("A trigger stream must take a cursor")
            self.streams[key] = StreamSpec(key, function, params_type, _item_type(returned), description, cursor,
                                           trigger)
            return function
        return register

    def event(self, name: str, payload: Any, description: str) -> None:
        self.events[name] = EventSpec(name, payload, description)

    def extension_point(self, name: str, *, description: str, descriptor: Any,
                        actions: dict[str, tuple[Any, Any, str]] = None,
                        streams: dict[str, tuple[Any, Any, str]] = None) -> None:
        self.extension_points[name] = ExtensionPointSpec(name, description, descriptor, dict(actions or {}),
                                                         dict(streams or {}))

    def implement(self, point: str, id: str, *, actions: dict[str, Callable] = None,
                  streams: dict[str, Callable] = None) -> None:
        """Implement ``point`` (``owner.name``) as ``id``; the descriptor is in the manifest's ``provides``."""
        self.implementations.setdefault(point, {})[id] = {"actions": dict(actions or {}),
                                                          "streams": dict(streams or {})}

    def setting(self, key: str, type: Any, default: Any, *, title: str, description: str = "",
                global_only: bool = False, group: str = "") -> None:
        """A setting stored by the platform. ``group`` is the heading it is listed under in the settings window.
        ``global_only``: bound to this machine or shared data
        (a data source and its key, a notification channel's sign-in), so a workspace cannot have its own."""
        TypeAdapter(type).validate_python(default)
        self.settings[key] = SettingSpec(key, type, default, title, description, global_only, group)

    def on_start(self, function: Callable) -> Callable:
        """``async (ctx) -> None``: runs once the process is connected, before it reports ready."""
        self.starters.append(function)
        return function

    def on_copy_workspace(self, function: Callable) -> Callable:
        """``async (source_workspace_id, copy_workspace_id) -> None``: copy what belongs to a workspace."""
        self.copiers.append(function)
        return function

    def on_delete_workspace(self, function: Callable) -> Callable:
        """Register async (workspace_id, *, preview) -> list[str]. Preview names the contents the user
        would lose. Deletion removes them idempotently, including after a partially completed attempt."""
        self.workspace_deleter = function
        return function

    def private_routes(self, function: Callable) -> Callable:
        """``(ctx) -> ASGI app`` serving ``/m/<id>/`` for this module's own frontend; not in the contract."""
        self.routes = function
        return function

    def changes(self, handlers: type) -> type:
        """Declare async static methods list(call_ids, run_id) and revert(call_id, direction)."""
        if not (inspect.iscoroutinefunction(handlers.list) and inspect.iscoroutinefunction(handlers.revert)):
            raise TypeError("changes needs async list and revert handlers")
        self.change_handlers = handlers
        return handlers
