"""Market's original source failure and termination cause survive the material handoff."""
import asyncio
from types import SimpleNamespace

import pytest
from talyx_sdk import ContractError

from modules.quant.backend.materials import run_task
from tests.unit.quant_runtime_fakes import adapt


@pytest.mark.parametrize("state", ["failed", "interrupted", "cancelled"])
def test_task_failure_keeps_original_source_error_and_specific_terminal_state(state):
    async def submit(name, args):
        return {"taskId": "task"}
    async def task(args):
        return {"state": state, "sequence": 0, "events": [],
                "error": {"code": "rate_limited", "message": "original source text",
                          "details": {"source": "gate", "terminationReason": "source_failure"}} if state == "failed" else None}
    async def cancel(args):
        pass
    ctx = adapt(SimpleNamespace(actions=SimpleNamespace(call=submit),
                          platform=SimpleNamespace(tasks=SimpleNamespace(get=task, cancel=cancel))))
    with pytest.raises(ContractError) as failure:
        asyncio.run(run_task(ctx, "market.export", {}))
    assert failure.value.code == ("rate_limited" if state == "failed" else state)
    if state == "failed":
        assert failure.value.message == "original source text"
        assert failure.value.details == {"source": "gate", "terminationReason": "source_failure"}
    else:
        assert failure.value.details == {"terminationReason": state}
