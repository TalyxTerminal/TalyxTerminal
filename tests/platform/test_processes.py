"""Standards 1, 4, 5 and 12: module processes crash, restart, start late, reload; who is calling is stamped once."""

from __future__ import annotations

import asyncio
import json
import time

import pytest
from talyx_sdk.errors import ContractError

from tests.platform.harness import real_chart, run, running, until, write_contract


def test_a_crashed_module_restarts_and_nobody_else_notices(tmp_path):
    """Standard 1 (platform part): kill echo; it is back within 10 s, calls to others keep working, calls it had
    not answered fail as provider_restarted (unknown, may have happened), and consumer's subscription resumes."""
    async def scenario():
        async with running(tmp_path) as platform:
            workspace = await platform.workspace()
            first = await platform.call("echo.whoami", workspace=workspace)
            await asyncio.sleep(0.5)
            before = (await platform.call("consumer.state"))["ticks"]
            assert before > 5
            pending = asyncio.create_task(platform.call("echo.slow", {"seconds": 30}))
            await asyncio.sleep(0.3)
            killed_at = time.monotonic()
            platform.process("echo").job.terminate()
            with pytest.raises(ContractError) as error:
                await pending
            assert (error.value.code, error.value.execution, error.value.effects) == \
                ("provider_restarted", "unknown", "may_have_occurred")
            assert (await platform.call("late.ping"))["readyAt"]           # others answer meanwhile
            await platform.ready("echo", seconds=10)
            assert time.monotonic() - killed_at < 10
            again = await platform.call("echo.whoami", workspace=workspace)
            assert again["pid"] != first["pid"]
            seen = (await platform.call("consumer.state"))["ticks"]
            await asyncio.sleep(1.0)
            assert (await platform.call("consumer.state"))["ticks"] > seen + 10   # the subscription resumed

    run(scenario())


def test_module_state_reports_the_interpreter_pid_across_reload_and_disable(tmp_path):
    async def scenario():
        async with running(tmp_path, test_modules=("echo",)) as platform:
            async def current_pid():
                state, = await platform.call("platform.modules.list")
                assert state["pid"] == (await platform.call("echo.whoami"))["pid"]
                return state["pid"]

            first = await current_pid()
            await platform.call("platform.modules.reload", {"id": "echo"})
            await platform.ready("echo")
            assert await current_pid() != first
            stopped = await platform.call("platform.modules.disable", {"id": "echo"})
            assert stopped["state"] == "stopped" and stopped["pid"] is None

    run(scenario())


async def restart(platform, module: str) -> None:
    """Kill a module's process and wait until the Host has started the next one and it is ready."""
    process = platform.process(module)
    exited = process.process
    process.job.terminate()
    await exited.wait()

    async def replaced():
        return process.process is not exited and process.state == "ready"
    await until(replaced, bool, seconds=10)


def test_market_restart_keeps_chart_alerts(tmp_path):
    """Standard 1 with the real Chart: an alert watches Market's bars; Market's process is killed and restarted; a
    bar crossing the alert's line after that fires it (Chart's subscription resumed by itself)."""
    async def scenario():
        async with real_chart(tmp_path) as platform:
            workspace = await platform.workspace()
            created = await platform.call("chart.write_alert", {"operation": {
                "op": "create", "target": {"symbol": "okx:BTC/USDT", "timeframe": "1m"},
                "conditions": [{"left": {"kind": "price"}, "op": "crossing_up", "right": {"kind": "value", "value": 200}}],
                "channels": {"toast": False, "sound": False, "mobile": False}}}, workspace=workspace)
            assert created["alert"]["state"] == "active"
            await until(lambda: platform.call("market.subscribers"), lambda value: value["sessions"] == 1)
            await restart(platform, "market")
            await until(lambda: platform.call("market.subscribers"), lambda value: value["sessions"] == 1)
            await asyncio.sleep(0.5)                                  # Chart takes the window and the live status
            await platform.call("market.push", {"close": 150})       # a new window's first bar is the baseline
            await platform.call("market.push", {"close": 250})
            log = await until(lambda: platform.call("chart.read_alert", {"view": "log"}, workspace=workspace),
                              lambda value: value["events"])
            assert len(log["events"]) == 1

    run(scenario())


def test_calls_to_chart_answer_while_market_restarts(tmp_path):
    """Standard 1, a dependent official module's part: while Market is down and restarting, Chart's actions answer."""
    async def scenario():
        async with real_chart(tmp_path) as platform:
            workspace = await platform.workspace()
            process = platform.process("market")
            exited = process.process
            process.job.terminate()
            await exited.wait()
            answered = await platform.call("chart.read_alert", {"view": "list"}, workspace=workspace)
            assert answered["alerts"] == []                    # answered without Market
            await until(lambda: asyncio.sleep(0, process.process is not exited and process.state == "ready"), bool,
                        seconds=10)
            assert (await platform.call("chart.read_alert", {"view": "list"}, workspace=workspace))["alerts"] == []

    run(scenario())


def test_identity_is_stamped_by_the_host_and_cannot_be_claimed(tmp_path):
    """Standard 4: a module cannot say it is the user, AI or another module, nor act for an unknown workspace."""
    async def scenario():
        async with running(tmp_path) as platform:
            workspace = await platform.workspace()
            results = await platform.call("echo.impersonate", {"text": workspace})
            assert results["as_user"] == {"error": "permission_denied"}
            assert results["as_ai"] == {"error": "permission_denied"}
            assert results["unknown_workspace"] == {"error": "permission_denied"}
            assert results["own_workspace"]["origin"] == "module"
            assert results["own_workspace"]["module"] == "echo"
            assert results["own_workspace"]["workspaceId"] == workspace

    run(scenario())


def test_running_code_calls_as_code_with_its_run_and_the_starter_reads_its_calls(tmp_path):
    """Standard 4: a run token makes calls ``code`` from the starting module and workspace, the context arrives as
    given, and the starter reads back the run's calls."""
    async def scenario():
        async with running(tmp_path) as platform:
            workspace = await platform.workspace()
            result = await platform.call("echo.run_code", {"folder": str(tmp_path)}, workspace=workspace)
            answer = json.loads(result["answer"])
            assert answer["origin"] == "code"
            assert answer["module"] == "echo"
            assert answer["workspaceId"] == workspace
            assert answer["run"] == {"id": result["runId"], "context": {"note": "from echo", "n": 7}}
            assert [call["capability"] for call in result["calls"]] == ["echo.whoami"]
            assert result["calls"][0]["ok"] is True

    run(scenario())


def test_calls_to_a_module_still_starting_wait_for_it(tmp_path):
    """Standard 5: consumer is ready before late and calls it from its start; the call waits and is delivered."""
    async def scenario():
        async with running(tmp_path) as platform:
            deadline = time.monotonic() + 5       # late is ready; its answer to consumer may still be on its way
            while (state := await platform.call("consumer.state"))["late"] is None and time.monotonic() < deadline:
                await asyncio.sleep(0.05)
            assert state["late"] is not None and "ok" in state["late"], state
            assert state["readyAt"] < state["late"]["ok"]["readyAt"]

    run(scenario())


def test_reloading_one_module_restarts_only_it(tmp_path):
    """Standard 12: change late's code and reload it: only late restarts, consumer keeps receiving echo's ticks,
    and the new action is in the AI catalog."""
    async def scenario():
        async with running(tmp_path) as platform:
            consumer_pid = platform.process("consumer").process.pid
            echo_pid = platform.process("echo").process.pid
            entry = tmp_path / "modules" / "late" / "backend" / "entry.py"
            entry.write_text(entry.read_text() + '''

@module.action(effects=["read"])
async def added(input: Empty, call: Call) -> dict[str, float]:
    """Added after the start."""
    return {"readyAt": READY["at"]}
''')
            before = (await platform.call("consumer.state"))["ticks"]
            await platform.call("platform.modules.reload", {"id": "late"})
            await platform.ready("late")
            assert platform.process("consumer").process.pid == consumer_pid
            assert platform.process("echo").process.pid == echo_pid
            assert (await platform.call("late.added"))["readyAt"]
            names = [tool["name"] for tool in await platform.ai_tools("late")]
            assert "late.added" in names
            assert (await platform.call("consumer.state"))["ticks"] > before

    run(scenario())


def test_a_handler_that_does_not_match_the_contract_fails_activation(tmp_path):
    """Standard 9 (activation): the contract says one thing, the code serves another: the module does not start."""
    async def scenario():
        from tests.platform.harness import prepare
        root = prepare(tmp_path / "modules", ("echo", "consumer", "late"))
        contract_path = root / "late" / "public" / "contract.json"
        contract = json.loads(contract_path.read_text())
        contract["actions"]["missing"] = contract["actions"]["ping"]
        contract_path.write_text(json.dumps(contract))
        async with running(tmp_path, official_root=root, wait=("echo",)) as platform:
            deadline = time.monotonic() + 15
            while platform.process("late").state != "failed" and time.monotonic() < deadline:
                await asyncio.sleep(0.1)
            assert platform.process("late").state == "failed"
            assert "missing" in platform.process("late").problem

    run(scenario())


__all__ = ["write_contract"]


def test_long_module_log_lines_do_not_interrupt_exit_handling():
    import asyncio
    from types import SimpleNamespace
    from unittest.mock import AsyncMock, Mock

    from host.supervisor import ModuleProcess

    async def scenario():
        stream = asyncio.StreamReader()
        stream.feed_data(b"x" * 70_000 + b"\nlast line")
        stream.feed_eof()
        child = SimpleNamespace(stdout=stream, wait=AsyncMock(return_value=0))
        module = ModuleProcess(Mock(), "sample", None, "sample", "entry", {}, "")
        module.process = child
        await module._watch(child)
        assert list(module.output) == ["x" * 8192, "last line"]
        module.supervisor.changed.assert_called_once_with(module)

    asyncio.run(scenario())
