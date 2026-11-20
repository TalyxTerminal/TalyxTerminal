"""Calls and subscriptions waiting for a module that is still starting: what the caller cancels, unsubscribes or leaves
behind never reaches the module once it is ready; the rest does."""

from __future__ import annotations

import asyncio

from kernel.router import Endpoint, Route, Router


class Box(Endpoint):
    def __init__(self, name: str):
        self.name, self.frames = name, []

    def deliver(self, envelope, body=b""):
        self.frames.append(envelope)

    def caller(self, envelope):
        return {"origin": "module", "module": "consumer", "workspaceId": None}


def scenario(steps) -> tuple[Router, Box, Box]:
    """``steps(router, origin)`` while ``market`` is starting; then it becomes ready."""
    async def run():
        router = Router(lambda capability, caller, kind: Route("market", None), lambda caller, capability, kind: None)
        origin, provider = Box("origin"), Box("market")
        steps(router, origin)
        router.provider_ready("market", provider)
        return router, origin, provider
    return asyncio.run(run())


def call(router, origin, request_id="1"):
    router.receive(origin, {"t": "call", "id": request_id, "cap": "market.write"}, b"{}")


def subscribe(router, origin, subscription_id="s1"):
    router.receive(origin, {"t": "sub", "sub": subscription_id, "cap": "market.session"}, b"{}")


def test_a_cancelled_call_is_answered_and_never_sent():
    def steps(router, origin):
        call(router, origin)
        router.receive(origin, {"t": "cancel", "id": "1"}, b"")
    router, origin, provider = scenario(steps)
    assert provider.frames == [] and router.waiting == []
    assert origin.frames[0]["t"] == "error" and origin.frames[0]["err"]["code"] == "cancelled"


def test_what_a_caller_that_went_away_had_waiting_is_never_sent():
    def steps(router, origin):
        call(router, origin)
        subscribe(router, origin)
        router.endpoint_closed(origin)
    router, _origin, provider = scenario(steps)
    assert provider.frames == [] and router.waiting == []


def test_an_unsubscribed_stream_is_never_opened():
    def steps(router, origin):
        subscribe(router, origin)
        router.receive(origin, {"t": "unsub", "sub": "s1"}, b"")
    router, _origin, provider = scenario(steps)
    assert provider.frames == [] and router.waiting == [] and router.subscriptions == {}


def test_only_what_was_cancelled_is_taken_out():
    def steps(router, origin):
        call(router, origin, "1")
        call(router, origin, "2")
        subscribe(router, origin, "s1")
        subscribe(router, origin, "s2")
        router.receive(origin, {"t": "cancel", "id": "1"}, b"")
        router.receive(origin, {"t": "unsub", "sub": "s1"}, b"")
    _router, _origin, provider = scenario(steps)
    assert [(frame["t"], frame["cap"]) for frame in provider.frames] == [("call", "market.write"),
                                                                        ("sub", "market.session")]
