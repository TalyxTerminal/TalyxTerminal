"""Route recorded changes back to their owners. Objects and before/after images stay inside the module."""

from __future__ import annotations

import asyncio

from talyx_sdk.errors import ContractError
from talyx_sdk.i18n import t

from kernel.router import QUEUE_SECONDS


def check_owner(requester: dict, recorded: dict) -> None:
    if (requester["module"], requester.get("workspaceId")) == (recorded["module"], recorded.get("workspaceId")):
        origin = recorded["origin"]
        if origin == "ai" and requester["origin"] == "ai":
            original = recorded["ai"]
            if original.get("conversation") and original == requester["ai"]:
                return
        elif origin == "code":
            run = recorded["run"]
            conversation = run.get("conversation")
            if requester["origin"] == "code" and requester["run"]["id"] == run["id"]:
                return
            if requester["origin"] == "module" and conversation is None:
                return
            if requester["origin"] == "ai" and conversation and requester["ai"].get("conversation") == conversation:
                return
        elif origin == requester["origin"] == "module":
            return
    raise ContractError("permission_denied", t("Only the original caller in its workspace and conversation can access this call's changes"))


async def _ask(host, module_id: str, envelope: dict):
    host._module_route(module_id)
    process = host.supervisor.processes[module_id]
    try:
        await asyncio.wait_for(process.ready_event.wait(), QUEUE_SECONDS)
    except TimeoutError as exc:
        raise ContractError("unavailable", t("{module_id} did not become ready to handle its changes", module_id=module_id)) from exc
    try:
        return await host._ask_module(process, envelope, QUEUE_SECONDS)
    except TimeoutError as exc:
        raise ContractError("unavailable", t("{module_id} did not confirm its changes request", module_id=module_id), execution="unknown",
                            effects="may_have_occurred" if envelope["t"] == "revert_changes" else "none") from exc


async def list_changes(host, caller: dict, call_ids: list[str], run_id: str | None) -> list[dict]:
    records = await host.calls.lookup(call_ids, run_id)
    grouped = {}
    for record in records:
        check_owner(caller, record["caller"])
        owner = record["capability"].partition(".")[0]
        if owner != "platform" and host.catalog.get(owner).contract.get("changes"):
            grouped.setdefault(owner, []).append(record["callId"])
    rows = []
    for owner, ids in grouped.items():
        changes = await _ask(host, owner, {"t": "list_changes", "callIds": ids, "runId": run_id})
        rows.extend(changes)
    order = {r["callId"]: index for index, r in enumerate(records)}
    return sorted(rows, key=lambda row: order[row["callId"]])


async def revert(host, caller: dict, call_id: str, direction: str) -> None:
    [record] = await host.calls.lookup([call_id])
    check_owner(caller, record["caller"])
    owner = record["capability"].partition(".")[0]
    if owner == "platform" or not host.catalog.get(owner).contract.get("changes"):
        raise ContractError("unavailable", t("{owner} does not provide change restoration", owner=owner))
    await _ask(host, owner, {"t": "revert_changes", "callId": call_id, "direction": direction})
