"""Recorded tool calls, asked of the user first when the conversation's permission says so, for the official Deep
Agents graph."""

from __future__ import annotations

import asyncio
import contextvars
import logging
import time
from typing import NotRequired
from uuid import uuid4

from deepagents.graph import DeepAgentState
from deepagents.middleware._utils import append_to_system_message
from langchain.agents.middleware import AgentMiddleware, hook_config
from langchain_core.messages import ToolMessage
from langchain_core.messages.utils import count_tokens_approximately
from langchain_core.tools import StructuredTool
from langgraph.errors import GraphBubbleUp
from talyx_sdk import ContractError

from .children import CHILD
from .profile import model_tools
from .providers.retry import retry_request
from .reviews import Reviews
from .store import encode

log = logging.getLogger(__name__)
TOOL_CALL = contextvars.ContextVar("agent_tool_call")
# A step carries its input only when small enough for the folded step line
# (search query, page address); larger inputs are read through the call detail.
STEP_INPUT_LIMIT = 2000


def observed_task(task):
    """A platform task as the conversation follows it: its status, whether it ended, and its result or error."""
    state = task["state"]
    result = task["result"] if state == "completed" else {"error": task["error"]} if task["error"] is not None else None
    return {"business": {"status": state, "terminal": state != "running", "action": task["action"]},
            "result": result, "task": {"taskId": task["taskId"], "progress": task["progress"]}, "observedAt": time.time()}


class ModelSelectionChanged(Exception):
    """Rebuild the official graph at its saved boundary using the user's choice."""


def model_selection(service, conversation):
    return service.catalogue.fingerprint(conversation["modelId"]), encode(conversation["thinking"])


class AgentState(DeepAgentState):
    waiting_business: NotRequired[bool]


def tool(name, description, schema, function):
    return StructuredTool.from_function(coroutine=function, name=name, description=description,
                                       args_schema=schema, infer_schema=False)


def schema_tokens(tools):
    values = model_tools(tools)
    return (len(encode(values)) + 3) // 4


class ToolExecutor:
    def __init__(self, service, turn, tools):
        self.service, self.store, self.parent = service, service.store, turn
        self.tools = {item["action"]: item for item in tools}
        self.reviews = Reviews(service, turn, self.tools)

    @property
    def turn(self):
        return CHILD.get() or self.parent

    def steps(self):
        runtime = self.turn.get("childRunId", self.turn["id"])
        if runtime not in self.service.steps:
            self.service.steps[runtime] = self.service.run_steps(self.turn)
        return self.service.steps[runtime]

    def show_steps(self):
        runtime = self.turn.get("childRunId", self.turn["id"])
        self.service.runtime_card(self.turn, {"id": "process_" + runtime, "type": "process_group",
                                             "title": "Working", "steps": list(self.steps().values())})

    async def invoke(self, action, arguments, *, tool_call=None):
        """Run the call once: replayed at a saved boundary, it answers what the recorded call did."""
        return await self._perform(action, arguments, tool_call or TOOL_CALL.get())

    async def _current(self, response):
        if response.get("status") != "accepted":
            return response
        observed = observed_task(await self.service.ctx.call("platform.tasks.get", {"taskId": response["taskId"]}))
        business = observed["business"]
        return {**response, "status": business["status"] if business["terminal"] else "accepted",
                "task": observed["task"], "result": observed["result"]}

    async def _perform(self, action, arguments, tool_call):
        for previous in reversed(self.store.calls(self.turn["id"])):
            if previous["toolCallId"] == tool_call and previous["action"] == action and previous["input"] == arguments:
                if previous["state"] == "completed":
                    return await self._current(previous["response"])
                if previous["error"] is None:
                    return {"status": "unknown", "callId": previous["callId"], "message": "The previous call was dispatched; inspect its original status before retrying"}
                return {"error": previous["error"], "callId": previous["callId"]}
        return await self._dispatch(action, arguments, tool_call)

    def _check_running(self):
        if self.store.turn(self.turn["id"])["state"] == "stopping" or "childRunId" in self.turn and self.turn["childRunId"] in self.service.children.stopping:
            raise ContractError("cancelled", "This work is stopping; no new operation was submitted", execution="not_started", effects="none")

    async def _dispatch(self, action, arguments, tool_call):
        self._check_running()
        if action not in self.tools:
            raise ContractError("unknown_capability", f"{action} is not among this conversation's tools")
        item = self.tools[action]
        call_id = "call_" + uuid4().hex
        self.store.call(self.turn["id"], tool_call, call_id, action, arguments, child_run_id=self.turn.get("childRunId"))
        runtime_id = self.turn["childRunId"] if "childRunId" in self.turn else self.turn["id"]
        card_id = "process_" + runtime_id
        steps = self.steps()
        effects = self.reviews.operation("talyx_call_action", {"name": action, "input": arguments})[2]
        step = {"id": tool_call, "callId": call_id, "title": action.split(".", 1)[1].replace("_", " ").capitalize(), "action": action,
                "op": None, "startedAt": time.time(), "finishedAt": None, "state": "running", "details": {"action": action, "callId": call_id, "effects": effects,
                **({"input": arguments} if len(encode(arguments)) <= STEP_INPUT_LIMIT else {})}}
        steps[tool_call] = step

        def process():
            self.service.runtime_card(self.turn, {"id": card_id, "type": "process_group", "title": "Working", "steps": list(steps.values())})
        process()
        try:
            receipt = await self.service.ai_call(self.turn, action, arguments)
        except ContractError as error:
            self.store.finish_call(call_id, host_id=error.call_id, error=error.to_dict())
            call_id = error.call_id or call_id
            step["callId"] = step["details"]["callId"] = call_id
            step.update(state="failed", finishedAt=time.time(), error=error.to_dict())
            process()
            return {"error": error.to_dict(), "callId": call_id}
        dispatch_id = call_id
        result, call_id = receipt["result"], receipt["callId"]
        self.service.result_summary(self.turn, result, item.get("summaryPaths", []))
        step["callId"] = step["details"]["callId"] = call_id
        if item["task"]:
            response = {"status": "accepted", "callId": call_id, "taskId": result["taskId"], "task": {"taskId": result["taskId"], "progress": None}, "result": None}
        elif {"write", "code"}.intersection(effects):
            response = await self.service.activities.track(self.turn, action, call_id, result)
        else:
            response = {"status": "completed", "callId": call_id, "result": result}
        if {"write", "delete"}.intersection(effects):
            await self.service.changes.collect(self.turn, action, call_id)
        self.store.finish_call(dispatch_id, host_id=call_id, result=result, response=response)
        step["details"]["result"] = encode(result)[:STEP_INPUT_LIMIT]
        step.update(state="running" if response["status"] == "accepted" else "completed",
                    finishedAt=None if response["status"] == "accepted" else time.time())
        process()
        if response["status"] == "accepted":
            await self.service.watch_business(self.turn, call_id)
        return response

FIND_SCHEMA = {"type": "object", "additionalProperties": False, "properties": {
    "module": {"type": "string", "description": "A module id (or platform): list its actions"},
    "query": {"type": "string", "description": "Words to find in actions' names and descriptions"}}}
READ_SCHEMA = {"type": "object", "additionalProperties": False, "required": ["name"],
               "properties": {"name": {"type": "string", "description": "module.action"}}}
CALL_SCHEMA = {"type": "object", "additionalProperties": False, "required": ["name", "input"], "properties": {
    "name": {"type": "string", "description": "module.action"},
    "input": {"type": "object", "description": "The action's input, as talyx_read_action gives its schema"}}}


def summary(description):
    first = description.split("\n\n")[0].replace("\n", " ")
    return first if len(first) <= 240 else first[:239] + "…"


class FixedTools(AgentMiddleware):
    """The platform's AI catalog, fixed when the conversation began, through three tools: find actions, read one,
    call one. Every action is in it; the catalog is far larger than what models take as separate tools."""
    state_schema = AgentState

    def __init__(self, service, executor, selection, profile):
        self.service, self.executor, self.selection = service, executor, selection
        self.profile = profile
        catalog = {item["action"]: item for item in profile["tools"]}

        async def find(module=None, query=None):
            if module is None and query is None:
                counts = {}
                for name in catalog:
                    counts[name.split(".", 1)[0]] = counts.get(name.split(".", 1)[0], 0) + 1
                return encode({"modules": [{"module": key, "actions": value} for key, value in sorted(counts.items())],
                               "next": "Give module or query to list actions."})
            found = [{"name": name, "summary": summary(item["description"])}
                     for name, item in catalog.items()
                     if (module is None or name.startswith(module + "."))
                     and (query is None or query.casefold() in (name + " " + item["description"]).casefold())]
            return encode({"actions": found})

        async def read(name):
            if name not in catalog:
                raise ContractError("unknown_capability", f"No action {name}; find actions with talyx_find_actions")
            item = catalog[name]
            detail = await self.service.ctx.call("platform.ai.catalog", {"action": name})
            return encode({"name": name, "outputFields": detail["outputFields"], "description": item["description"], "effects": item["effects"],
                           "longTask": item["task"], "input": item["inputSchema"], "examples": item.get("examples", []),
                           "errors": item.get("errors", []), "effectBranches": item.get("effectBranches")})

        async def call(name, input):
            # A long task's result comes to the model when it ends (watch_business), as a completion message.
            return encode(self.service.model_result(await self.executor.invoke(name, input)))

        self.tools = [
            tool("talyx_find_actions", "Find TALYX actions: without arguments, the modules and how many actions each has; "
                 "with module or query, each action's name and one line.", FIND_SCHEMA, find),
            tool("talyx_read_action", "One TALYX action's description, examples, effects, input schema and output fields "
                 "(names, types, units).", READ_SCHEMA, read),
            tool("talyx_call_action", "Run one TALYX action with its input. Actions that change something may first wait "
                 "for the user's approval, as this chat's permission says.", CALL_SCHEMA, call)]

    @hook_config(can_jump_to=["end"])
    async def abefore_model(self, state, runtime):
        turn = self.executor.turn
        if self.service.store.turn(turn["id"])["state"] == "stopping" or "childRunId" in turn and turn["childRunId"] in self.service.children.stopping:
            raise asyncio.CancelledError
        conversation = self.service.execution_conversation(turn)
        if "childRunId" not in turn and model_selection(self.service, conversation) != self.selection:
            raise ModelSelectionChanged
        messages = await self.service.completions.drain(turn) if "childRunId" in turn else await self.service.drain_messages(turn)

        if self.service.completions.pending(turn) and not messages and self.service.completions.should_wait(state):
            return {"jump_to": "end", "waiting_business": True}
        return {"messages": messages, "waiting_business": False} if messages else None

    async def awrap_model_call(self, request, handler):
        # StructuredTool's default provider conversion dereferences JSON schemas.
        # Send the shared immutable schema directly; dispatch still uses the
        # original StructuredTools and the receiving module validates the input.
        turn = self.executor.turn
        enabled = self.service.store.runtime_settings(turn["conversationId"])["childrenEnabled"]
        plan = self.service.execution_conversation(turn).get("planMode")
        hidden = {"write_file", "edit_file", "delete", "run_python", "bot_manage", "bot_delete", "remember"} if plan else set()
        if not enabled:
            hidden.add("task")
        declared = [tool for tool in request.tools if (tool["function"]["name"] if isinstance(tool, dict) else tool.name) not in hidden]
        request = request.override(tools=model_tools(declared))
        if "childRunId" not in turn and self.service.memory.enabled(turn["conversationId"]):
            common = self.service.memory.common(turn["workspaceId"], turn["conversationId"],
                token_budget=self.service.store.defaults(turn["workspaceId"])["memoryTokens"])
            if common:
                request = request.override(system_message=append_to_system_message(request.system_message,
                    "Remembered (core entries; search_memory finds the rest):\n" + "\n".join(item["body"] for item in common)))
        estimated = await asyncio.to_thread(count_tokens_approximately, request.messages) + schema_tokens(request.tools)
        if request.system_message is not None:
            estimated += int(count_tokens_approximately([request.system_message]))
        turn = self.executor.turn
        conversation = self.service.execution_conversation(turn)
        window = self.service.catalogue.resolve(conversation["modelId"])["contextWindow"]
        self.service.context(turn, estimated=estimated, actual=None, window=window)
        if turn["unattended"]:
            response = await retry_request(lambda: handler(request))
        else:
            response = await handler(request)
        self.service.completions.consumed(request.messages)
        return response

    async def awrap_tool_call(self, request, handler):
        call = request.tool_call
        token = TOOL_CALL.set(call["id"])
        try:
            name, args = call["name"], call["args"]
            if name == "task" and not self.service.store.runtime_settings(self.executor.turn["conversationId"])["childrenEnabled"]:
                return ToolMessage(content="Subagents are disabled for this conversation.", tool_call_id=call["id"], status="error")
            if self.executor.reviews.decision(name, args) == "refuse":
                return ToolMessage(content="Plan mode permits reading only.", tool_call_id=call["id"], status="error")
            approved = self.service.store.approval_for(self.executor.turn["id"], call["id"])
            if approved and approved["state"] == "allowed":
                self.service.store.consume_approval(approved["id"])
            if name == "talyx_call_action":
                return await handler(request)
            steps = self.executor.steps()
            step = steps.setdefault(call["id"], {"id": call["id"], "title": name.replace("_", " ").capitalize(),
                "action": name, "startedAt": time.time(), "finishedAt": None, "state": "running",
                "details": {"effects": self.executor.reviews.operation(name, args)[2],
                            **({"input": args} if len(encode(args)) < STEP_INPUT_LIMIT else {"tool": name})}})
            self.executor.show_steps()
            result = await handler(request)
            step.update(state="failed" if isinstance(result, ToolMessage) and result.status == "error" else "completed",
                        finishedAt=time.time())
            if isinstance(result, ToolMessage):
                step["details"]["result"] = result.text[:2000]
            self.executor.show_steps()
            return result
        except ContractError as error:
            return self.failed(call, error.to_dict())
        except GraphBubbleUp:
            raise
        except Exception as error:
            # A tool's own failure goes back to the model to take another way; the turn goes on.
            log.exception("Tool %s failed", call["name"])
            return self.failed(call, {"code": "tool_failed", "message": f"{type(error).__name__}: {error}"})
        finally:
            TOOL_CALL.reset(token)

    def failed(self, call, error):
        step = self.executor.steps().get(call["id"])
        if step is not None and step["state"] == "running":
            step.update(state="failed", finishedAt=time.time(), error=error)
            self.executor.show_steps()
        return ToolMessage(content=encode({"error": error}), tool_call_id=call["id"], status="error")
