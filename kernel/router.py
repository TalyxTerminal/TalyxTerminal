"""Moves calls, streams and events between endpoints, reading only envelopes.

An endpoint is anything frames go to and come from: a module's process, a window's bridge session, an AI or code
entry, or the platform's own services. Identity is stamped once, where a frame enters (``Endpoint.caller``); after
that it travels in the envelope and nobody along the way can change it. Bodies are passed on as the bytes they
arrived as: no parsing, no validation, no copies. The receiving module validates its input; the sending module
checked its output.
"""

from __future__ import annotations

import asyncio
import itertools
import logging
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any
from uuid import uuid4

from talyx_sdk.errors import ContractError, provider_restarted
from talyx_sdk.i18n import t

log = logging.getLogger("talyx.router")
# Calls to a module that is still starting wait this long, at most this many at a time.
QUEUE_SECONDS = 30.0
QUEUE_LIMIT = 1000


class Endpoint:
    """One side of the router. ``deliver`` never blocks; an endpoint keeps its own outgoing queue."""

    name: str
    module: str | None = None

    def deliver(self, envelope: dict, body: bytes = b"") -> None:
        raise NotImplementedError

    def caller(self, envelope: dict) -> dict:
        """The identity of a frame arriving from this endpoint (``origin``, ``module``, ``workspaceId``...)."""
        raise NotImplementedError


@dataclass
class _Call:
    origin: Endpoint
    origin_id: str
    provider: Endpoint
    capability: str
    caller: dict
    effects: tuple[str, ...]
    on_end: Callable | None = None


@dataclass
class _Subscription:
    origin: Endpoint
    origin_id: str
    provider: Endpoint | None
    capability: str
    caller: dict
    event: bool = False


@dataclass(eq=False)
class _Waiting:
    """A call (``kind`` "call") or subscription ("sub") of ``origin`` waiting for its provider to start."""

    module: str
    action: Callable
    deadline: float
    fail: Callable
    origin: Endpoint
    origin_id: str
    kind: str


@dataclass
class Route:
    """Where a capability goes: a provider endpoint (or a module still starting), plus what the contract says."""

    module: str | None
    endpoint: Endpoint | None
    effects: tuple[str, ...] = ()
    event: bool = False


class Router:
    def __init__(self, resolve: Callable[[str, dict, str], Route], authorize: Callable[[dict, str, str], None],
                 record: Callable[[dict, str, bytes, str], Any] | None = None):
        """``resolve(capability, caller, kind)`` finds where a call (``kind`` "call") or subscription ("sub") goes
        or raises ``ContractError``; ``authorize(caller, capability, kind)`` refuses what this caller may not do;
        ``record`` is told of calls whose caller is AI or code (the call log)."""
        self.resolve, self.authorize, self.record = resolve, authorize, record
        self.ids = itertools.count(1)
        self.instance = uuid4().hex
        self.calls: dict[str, _Call] = {}
        self.subscriptions: dict[str, _Subscription] = {}
        self.events: dict[str, set[str]] = {}
        self.waiting: list[_Waiting] = []
        self.ready: dict[str, Endpoint] = {}

    def next_id(self) -> str:
        return f"{self.instance}-{next(self.ids)}"

    # ── providers coming and going ───────────────────────────────────────

    def provider_ready(self, module: str, endpoint: Endpoint) -> None:
        self.ready[module] = endpoint
        waiting, self.waiting = self.waiting, []
        for item in waiting:
            if item.module == module:
                item.action()
            else:
                self.waiting.append(item)

    def provider_gone(self, module: str, endpoint: Endpoint, *, stopped: bool = False) -> None:
        """A module's process ended: its callers get ``provider_restarted`` (or ``unavailable`` when stopped on
        purpose), its streams end so subscribers' SDKs open them again, and its own calls and streams go away."""
        if self.ready.get(module) is endpoint:
            del self.ready[module]
        self.endpoint_closed(endpoint, provider_error=None if not stopped else ContractError(
            "unavailable", t("{module} was stopped", module=module), execution="unknown", effects="may_have_occurred"))
        if stopped:
            keep = []
            for item in self.waiting:
                if item.module == module:
                    item.fail(ContractError("unavailable", t("{module} was stopped", module=module)))
                else:
                    keep.append(item)
            self.waiting = keep

    def _unwait(self, origin: Endpoint, origin_id: str | None = None, kind: str | None = None) -> list[_Waiting]:
        """Take out what ``origin`` has waiting (one call or subscription, or everything): it never reaches the
        provider."""
        taken = [item for item in self.waiting if item.origin is origin
                 and (origin_id is None or (item.origin_id, item.kind) == (origin_id, kind))]
        self.waiting = [item for item in self.waiting if item not in taken]
        return taken

    def endpoint_closed(self, endpoint: Endpoint, *, provider_error: ContractError | None = None) -> None:
        self._unwait(endpoint)
        for host_id, call in list(self.calls.items()):
            if call.provider is endpoint:
                del self.calls[host_id]
                error = provider_error or provider_restarted(call.capability)
                call.origin.deliver({"t": "error", "id": call.origin_id, "callId": host_id, "err": error.to_dict()})
                self._ended(call, None, error.to_dict())
            elif call.origin is endpoint:
                del self.calls[host_id]
                call.provider.deliver({"t": "cancel", "id": host_id})
                self._ended(call, None, ContractError("cancelled", t("Caller disconnected"), execution="unknown",
                                                       effects="may_have_occurred").to_dict())
        for host_id, subscription in list(self.subscriptions.items()):
            if subscription.provider is endpoint and not subscription.event:
                del self.subscriptions[host_id]
                error = provider_error or provider_restarted(subscription.capability)
                subscription.origin.deliver({"t": "end", "sub": subscription.origin_id, "err": error.to_dict()})
            elif subscription.origin is endpoint:
                self._drop_subscription(host_id)

    # ── frames arriving from an endpoint ─────────────────────────────────

    def receive(self, endpoint: Endpoint, envelope: dict, body: bytes) -> None:
        kind = envelope["t"]
        if kind == "call":
            self.call(endpoint, envelope, body)
        elif kind == "sub":
            self.subscribe(endpoint, envelope, body)
        elif kind in ("result", "error"):
            self.answer(endpoint, envelope, body)
        elif kind == "item":
            self.item(endpoint, envelope, body)
        elif kind == "end":
            self.end(endpoint, envelope)
        elif kind == "unsub":
            self._unwait(endpoint, envelope["sub"], "sub")
            for host_id, subscription in list(self.subscriptions.items()):
                if subscription.origin is endpoint and subscription.origin_id == envelope["sub"]:
                    self._drop_subscription(host_id)
        elif kind == "cancel":
            for item in self._unwait(endpoint, envelope["id"], "call"):
                item.fail(ContractError("cancelled", t("Cancelled before it was sent"), execution="not_started"))
            for host_id, call in list(self.calls.items()):
                if call.origin is endpoint and call.origin_id == envelope["id"]:
                    call.provider.deliver({"t": "cancel", "id": host_id})
        elif kind == "event":
            self.publish(endpoint.caller(envelope), envelope["cap"], body)
        else:
            raise ContractError("invalid_input", t("Unknown frame type {kind}", kind=f'{kind!r}'))

    def call(self, origin: Endpoint, envelope: dict, body: bytes, *, on_end: Callable | None = None,
             route: Route | None = None, host_id: str | None = None) -> None:
        """Pass a call on; ``route`` (the Host's own calls into a window) skips resolving."""
        origin_id = envelope["id"]
        host_id = host_id or self.next_id()
        try:
            caller = origin.caller(envelope)
            capability = envelope["cap"]
            if route is None:
                self.authorize(caller, capability, "call")
                route = self.resolve(capability, caller, "call")
        except ContractError as exc:
            origin.deliver({"t": "error", "id": origin_id, "callId": host_id, "err": exc.to_dict()})
            if on_end is not None:
                on_end(None, exc.to_dict())
            return

        def send(endpoint: Endpoint) -> None:
            self.calls[host_id] = _Call(origin, origin_id, endpoint, capability, caller, route.effects, on_end)
            if self.record is not None and (caller["origin"] in ("ai", "code") or
                    (caller["origin"] == "module" and route.module != "platform" and
                     {"write", "delete"}.intersection(route.effects))):
                self.record(caller, capability, body, host_id)
            endpoint.deliver({"t": "call", "id": host_id, "cap": capability, "from": caller}, body)

        self._when_ready(route, send, lambda error: (
            origin.deliver({"t": "error", "id": origin_id, "callId": host_id, "err": error.to_dict()}),
            on_end and on_end(None, error.to_dict())), origin, origin_id, "call")

    def _when_ready(self, route: Route, action: Callable[[Endpoint], None], fail: Callable, origin: Endpoint,
                    origin_id: str, kind: str) -> None:
        if route.endpoint is not None:
            action(route.endpoint)
            return
        if len(self.waiting) >= QUEUE_LIMIT:
            fail(ContractError("unavailable", t("{module} is starting and too many calls are waiting for it", module=route.module)))
            return
        loop = asyncio.get_running_loop()
        item = _Waiting(route.module, lambda: action(self.ready[route.module]), loop.time() + QUEUE_SECONDS, fail,
                        origin, origin_id, kind)
        self.waiting.append(item)

        def expire():
            if item in self.waiting:
                self.waiting.remove(item)
                fail(ContractError("unavailable", t("{module} did not start in time", module=route.module)))

        loop.call_later(QUEUE_SECONDS, expire)

    def answer(self, provider: Endpoint, envelope: dict, body: bytes) -> None:
        host_id = envelope["id"]
        call = self.calls.get(host_id)
        if call is None or call.provider is not provider:
            return
        del self.calls[host_id]
        if envelope["t"] == "result":
            try:
                call.origin.deliver({"t": "result", "id": call.origin_id, "callId": host_id}, body)
            except ContractError as exc:
                # An answer that cannot travel to its caller (larger than a frame) reaches it as that error.
                call.origin.deliver({"t": "error", "id": call.origin_id, "callId": host_id, "err": exc.to_dict()})
                self._ended(call, None, exc.to_dict())
                return
            self._ended(call, body, None)
        else:
            call.origin.deliver({"t": "error", "id": call.origin_id, "callId": host_id, "err": envelope["err"]})
            self._ended(call, None, envelope["err"])

    def _ended(self, call: _Call, body: bytes | None, error: dict | None) -> None:
        if call.on_end is not None:
            call.on_end(body, error)

    def subscribe(self, origin: Endpoint, envelope: dict, body: bytes) -> None:
        origin_id = envelope["sub"]
        try:
            caller = origin.caller(envelope)
            capability = envelope["cap"]
            self.authorize(caller, capability, "sub")
            route = self.resolve(capability, caller, "sub")
        except ContractError as exc:
            origin.deliver({"t": "error", "id": envelope.get("id", origin_id), "err": exc.to_dict()})
            return
        host_id = self.next_id()
        if route.event:
            self.subscriptions[host_id] = _Subscription(origin, origin_id, None, capability, caller, event=True)
            self.events.setdefault(capability, set()).add(host_id)
            origin.deliver({"t": "result", "id": envelope.get("id", origin_id)}, b"null")
            return

        def send(endpoint: Endpoint) -> None:
            if origin_id is None:
                return
            self.subscriptions[host_id] = _Subscription(origin, origin_id, endpoint, capability, caller)
            forwarded = {"t": "sub", "sub": host_id, "cap": capability, "from": caller}
            if "cursor" in envelope:
                forwarded["cursor"] = envelope["cursor"]
            endpoint.deliver(forwarded, body)
            origin.deliver({"t": "result", "id": envelope.get("id", origin_id)}, b"null")

        self._when_ready(route, send, lambda error: origin.deliver(
            {"t": "error", "id": envelope.get("id", origin_id), "err": error.to_dict()}), origin, origin_id, "sub")

    def item(self, provider: Endpoint, envelope: dict, body: bytes) -> None:
        subscription = self.subscriptions.get(envelope["sub"])
        if subscription is None or subscription.provider is not provider:
            return
        forwarded = {"t": "item", "sub": subscription.origin_id}
        if "cursor" in envelope:
            forwarded["cursor"] = envelope["cursor"]
        subscription.origin.deliver(forwarded, body)

    def end(self, provider: Endpoint, envelope: dict) -> None:
        subscription = self.subscriptions.get(envelope["sub"])
        if subscription is None or subscription.provider is not provider:
            return
        del self.subscriptions[envelope["sub"]]
        subscription.origin.deliver({"t": "end", "sub": subscription.origin_id,
                                     **({"err": envelope["err"]} if envelope.get("err") else {})})

    def _drop_subscription(self, host_id: str) -> None:
        subscription = self.subscriptions.pop(host_id, None)
        if subscription is None:
            return
        if subscription.event:
            self.events.get(subscription.capability, set()).discard(host_id)
        elif subscription.provider is not None:
            subscription.provider.deliver({"t": "unsub", "sub": host_id})

    def publish(self, caller: dict, capability: str, body: bytes) -> None:
        """An event: only its owner publishes it, every current subscriber gets it, nothing is kept."""
        owner = capability.split(".", 1)[0]
        if caller.get("module") != owner:
            raise ContractError("permission_denied", t("Only {owner} publishes {capability}", owner=owner, capability=capability))
        for host_id in list(self.events.get(capability, ())):
            subscription = self.subscriptions[host_id]
            subscription.origin.deliver({"t": "item", "sub": subscription.origin_id}, body)

    def end_streams(self, capability_prefix: str, error: ContractError) -> None:
        """End the platform's own streams (``platform.``) on its side, e.g. at shutdown."""
        for host_id, subscription in list(self.subscriptions.items()):
            if subscription.capability.startswith(capability_prefix):
                del self.subscriptions[host_id]
                subscription.origin.deliver({"t": "end", "sub": subscription.origin_id, "err": error.to_dict()})
