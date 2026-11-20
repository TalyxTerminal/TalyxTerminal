"""Standards 2 and 3: the frame path is fast enough for bar pages and quotes together, and the sender refuses
numbers the receiver would read wrong."""

from __future__ import annotations

import asyncio
import time

import pytest
from talyx_sdk.errors import ContractError

from tests.platform.harness import run, running


def p99(values: list[float]) -> float:
    ordered = sorted(values)
    return ordered[max(0, int(len(ordered) * 0.99) - 1)]


@pytest.mark.timing
def test_ten_thousand_row_pages_and_quotes_together(tmp_path):
    """Standard 2: a 10,000-row page over two hops p99 <= 20 ms; meanwhile 50 quotes a second arrive with p99
    <= 10 ms; the Host's event loop lags p99 <= 20 ms (a probe every 5 ms)."""
    async def scenario():
        async with running(tmp_path) as platform:
            lags: list[float] = []
            stop = asyncio.Event()

            async def probe():
                while not stop.is_set():
                    started = time.perf_counter()
                    await asyncio.sleep(0.005)
                    lags.append((time.perf_counter() - started - 0.005) * 1000)

            probing = asyncio.create_task(probe())
            await platform.call("consumer.bench", {"rows": 10_000, "calls": 5})      # warm up
            result = await platform.call("consumer.bench", {"rows": 10_000, "calls": 300})
            stop.set()
            await probing
            return result, lags

    result, lags = run(scenario())
    pages, ticks = result["pages"], result["ticks"]
    report = f"pages p99 {p99(pages):.1f} ms, quotes p99 {p99(ticks):.1f} ms ({len(ticks)}), loop lag p99 {p99(lags):.1f} ms"
    print(report)
    assert len(ticks) > 50
    assert p99(pages) <= 20, report
    assert p99(ticks) <= 10, report
    assert p99(lags) <= 20, report


def test_the_sender_refuses_nan_infinity_and_unsafe_integers(tmp_path):
    """Standard 3: the receiver never gets null for NaN or a cut big integer; the sender answers invalid_output."""
    async def scenario():
        async with running(tmp_path) as platform:
            for action in ("echo.not_finite", "echo.infinite_untyped", "echo.big_integer"):
                with pytest.raises(ContractError) as error:
                    await platform.call(action)
                assert error.value.code == "invalid_output", action

    run(scenario())


def test_wire_encode_refuses_what_orjson_would_write_wrong():
    from talyx_sdk import wire
    for value in ({"a": float("nan")}, [1.0, float("-inf")], {"n": 2 ** 53 + 1}):
        with pytest.raises(ContractError) as error:
            wire.encode(value)
        assert error.value.code == "invalid_output"
    assert wire.encode({"n": 2 ** 53 - 1, "x": 1.5}) == b'{"n":9007199254740991,"x":1.5}'


def test_an_answer_larger_than_a_frame_reaches_its_caller_as_an_error():
    """The platform's own answers are not framed where they are made; one too large for the caller's connection
    used to vanish there, and the call waited forever."""
    from talyx_sdk import wire

    from kernel.router import Endpoint, Route, Router

    class Framed(Endpoint):
        def __init__(self):
            self.frames = []

        def deliver(self, envelope, body=b""):
            wire.frame(envelope, body)
            self.frames.append(envelope)

        def caller(self, envelope):
            return {"origin": "module", "module": "agent", "workspaceId": None}

    origin, provider = Framed(), Framed()
    router = Router(lambda capability, caller, kind: Route("platform", provider), lambda caller, capability, kind: None)
    router.receive(origin, {"t": "call", "id": "1", "cap": "platform.ai.catalog"}, b"{}")
    router.answer(provider, {"t": "result", "id": provider.frames[0]["id"]}, b" " * (wire.MAX_BODY + 1))
    assert origin.frames[0]["t"] == "error" and origin.frames[0]["err"]["code"] == "too_large"
    assert router.calls == {}


def test_numpy_integer_limits_are_exact_for_scalars_and_arrays():
    from typing import Any

    import numpy as np
    from talyx_sdk.process import Codec

    codec = Codec(Any)
    from talyx_sdk import wire
    with pytest.raises(ContractError, match="NaN"):
        wire.encode(np.array([np.nan]), checked=True)
    for dtype, sign in [(np.int64, -1), (np.int64, 1), (np.uint64, 1)]:
        safe = sign * (2**53 - 1)
        for value in (dtype(safe), np.array([safe], dtype=dtype)):
            assert str(safe).encode() in codec.check({"value": value})
        for value in (dtype(safe + sign), np.array([safe + sign], dtype=dtype)):
            with pytest.raises(ContractError) as error:
                codec.check({"value": value})
            assert error.value.code == "invalid_output"


def test_extension_receipts_refuse_coercible_json():
    from talyx_sdk.process import Codec

    codec = Codec(dict[str, bool])
    assert codec.accept({"closed": False}) == {"closed": False}
    with pytest.raises(ContractError) as error:
        codec.accept({"closed": "false"})
    assert error.value.code == "invalid_output"


def test_cancelling_a_subscription_open_sends_unsub_for_its_subscription():
    from talyx_sdk.declare import Module
    from talyx_sdk.process import ModuleProcess

    async def scenario():
        process = ModuleProcess(Module("caller"), None, None)
        frames = []
        process.send = lambda envelope, body=b"": frames.append(envelope)
        opening = asyncio.create_task(process.ctx.subscribe("data.ticks"))
        await asyncio.sleep(0)
        opening.cancel()
        with pytest.raises(asyncio.CancelledError):
            await opening
        assert frames[1:] == [{"t": "unsub", "sub": frames[0]["sub"]}]
        assert not process.subscriptions and not process.pending
    run(scenario())


def test_write_receipt_over_frame_limit_and_workspace_task_ownership(tmp_path):
    async def scenario():
        async with running(tmp_path, test_modules=("echo",)) as platform:
            ws = await platform.workspace()
            task = await platform.call("echo.count", {"seconds": 0}, workspace=ws)
            assert (await platform.call("platform.tasks.get", task))["workspaceId"] == ws
            with pytest.raises(ContractError) as error:
                await platform.call("echo.write_long_answer", {"rows": 5_600_000}, workspace=ws)
            assert (error.value.code, error.value.execution, error.value.effects) == (
                "too_large", "unknown", "may_have_occurred")
    run(scenario())
