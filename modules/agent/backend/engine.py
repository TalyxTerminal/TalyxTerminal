"""One fixed Deep Agents graph profile and serialized durable continuations."""
from __future__ import annotations

import asyncio
import re
from pathlib import Path

from deepagents import create_deep_agent
from deepagents.middleware.skills import SkillsMiddleware
from langchain.agents.middleware import AgentMiddleware
from langchain_core.messages import AIMessage, HumanMessage
from langgraph.errors import NodeCancelledError
from langgraph.types import Command

from .bots.tools import bot_tools
from .children import CHILD, ChildDisplay
from .compaction import Compaction
from .projection import ProviderProjection
from .providers import project_message
from .research import memory_tools, research_tools
from .reviews import Reviews
from .store import encode
from .tools import AgentState, FixedTools, ModelSelectionChanged, ToolExecutor, model_selection


class ChildModel(AgentMiddleware):
    """Each parallel subagent model request owns its provider lease and usage record."""

    def __init__(self, service, turn, conversation):
        self.service, self.turn, self.conversation = service, turn, conversation

    async def awrap_model_call(self, request, handler):
        child = CHILD.get()
        model_id, thinking = child["modelId"], child["thinking"]
        reserve = self.service.engine.reserve(child, self.service.execution_conversation(child))
        async with self.service.model_runtime.model(model_id, thinking=thinking,
                                                   output_reserve=reserve,
                                                   usage_context=self.service.usage_context(child)) as lease:
            return await lease.guard(lambda: handler(request.override(model=lease.model)))


class SelectedSkills(SkillsMiddleware):
    """A bot sees only the skills chosen for it; a chat sees them all."""

    def __init__(self, selected, **options):
        super().__init__(**options)
        self.selected = set(selected)

    async def abefore_agent(self, state, runtime, config):
        update = await super().abefore_agent(state, runtime, config)
        if update is not None:
            update["skills_metadata"] = [skill for skill in update["skills_metadata"] if skill["name"] in self.selected]
        return update


class ConversationEngine:
    def __init__(self, service, checkpointer):
        self.service, self.checkpointer = service, checkpointer

    async def run(self, turn, *, resume=False):
        continuation = turn["businessContinuation"]
        while True:
            try:
                return await self._invoke(turn, resume=resume, continuation=continuation)
            except ModelSelectionChanged:
                continuation, resume = True, False
            except NodeCancelledError as error:
                raise asyncio.CancelledError from error

    def reserve(self, turn, conversation):
        profile = self.service.catalogue.resolve(conversation["modelId"])
        reserve = profile["maxOutputTokens"]
        self.service.output_reserves[turn["childRunId"] if "childRunId" in turn else turn["id"]] = reserve
        return reserve

    async def assemble(self, turn, conversation, lease, reserve):
        service = self.service
        thread = turn["childRunId"] if "childRunId" in turn else service.history.thread(conversation["id"])
        window = lease.resolved["contextWindow"]
        lease.model.profile = {**(lease.model.profile or {}), "max_input_tokens": window, "max_output_tokens": reserve}
        projection = ProviderProjection(service, turn, window)
        fixed_profile = await service.profiles.read(turn)
        executor = ToolExecutor(service, turn, fixed_profile["tools"])
        tools = FixedTools(service, executor, model_selection(service, conversation), fixed_profile)
        backend, filesystem = await service.files.mount(turn)
        instructions = (await service.instruction_service(turn)).middleware()
        context = Compaction(service, turn, lease.model, backend)
        review = executor.reviews
        middleware = [review.middleware(), ChildDisplay(service, turn), filesystem, context, instructions, tools]
        folder = Path(await service.ctx.platform.workspace_folder(turn["workspaceId"]))
        available = await asyncio.to_thread(service.skills.snapshot, folder / "skills")
        skills = ["/skills/global/", "/skills/workspace/"] if available["items"] else None
        if bot := service.bots.profile(conversation):
            if skills and bot["skills"]:
                middleware.append(SelectedSkills(bot["skills"], backend=backend, sources=skills))
            skills = None
        shared_tools = [*tools.tools, *research_tools(service, turn, filesystem)]
        child = {"name": "general-purpose", "description": "Research an independent part of the user's request.",
                 "system_prompt": "Complete the assigned task and return the evidence and result to the parent.",
                 "tools": shared_tools, "middleware": [review.middleware(), ChildModel(service, turn, conversation), tools,
                                                       filesystem, instructions, Compaction(service, turn, lease.model, backend, child=True)]}
        system_prompt = fixed_profile["system"] + "\n" + service.bots.system_context(conversation)
        if conversation.get("planMode"):
            system_prompt += "\nPlan mode is active. Research with read tools, then present a proposed implementation plan."
        personal = memory_tools(service, turn) if service.memory.enabled(conversation["id"]) else []
        graph = await asyncio.to_thread(create_deep_agent, lease.model, tools=[*shared_tools, *personal, *bot_tools(service, turn)], system_prompt=system_prompt, backend=backend,
            state_schema=AgentState, checkpointer=self.checkpointer, middleware=middleware, subagents=[child],
            skills=skills)
        config = {"configurable": {"thread_id": thread}, "recursion_limit": 1000, "callbacks": [projection],
            "metadata": {"conversationId": conversation["id"], "turnId": turn["id"],
                **({"childRunId": turn["childRunId"]} if "childRunId" in turn else {})}}
        return graph, config, projection, review

    async def _invoke(self, turn, *, resume, continuation):
        service = self.service
        conversation = service.execution_conversation(turn)
        reserve = self.reserve(turn, conversation)
        async with service.model_runtime.model(conversation["modelId"], thinking=conversation["thinking"], output_reserve=reserve,
            usage_context=service.usage_context(turn)) as lease:
            graph, config, projection, review = await self.assemble(turn, conversation, lease, reserve)
            if resume:
                decisions = Reviews.decisions(service.store, service.store.turn(turn["id"]))
                if not decisions:
                    return False
                value = Command(resume=decisions)
            else:
                messages = [] if continuation else await service.input_messages(turn, conversation, lease.resolved)
                if continuation:
                    messages.extend(await service.drain_messages(turn))
                if not continuation:
                    reconciled = await service.reconcile_history(conversation)
                    if reconciled:
                        messages.append(HumanMessage(content="Earlier interrupted calls were observed; do not repeat uncertain effects. No old approval is valid.\n" + encode(reconciled)))
                value = {"messages": messages, "waiting_business": False} if messages else None
                if value and not continuation:
                    value["skills_metadata"] = None
                if value and not continuation and (skill := re.match(r"^/([\w-]+)(?:\s|$)", turn["text"])):
                    if skill[1] not in {"compact", "plan"}:
                        value["pinned_skills"] = [skill[1]]
            while True:
                async def invoke_graph():
                    result, interruptions = {}, {}
                    displayed_todos = None
                    async for mode, chunk in graph.astream(value, config, stream_mode=["values", "messages"]):
                        if mode == "values":
                            if "todos" in chunk and chunk["todos"] != displayed_todos:
                                displayed_todos = chunk["todos"]
                                if displayed_todos:
                                    service.runtime_card(turn, {"id": "todos_" + conversation["id"], "type": "todos",
                                                                "items": displayed_todos})
                            if "__interrupt__" in chunk:
                                interruptions.update({item.id: item for item in chunk["__interrupt__"]})
                            else:
                                result = chunk
                    return {**result, **({"__interrupt__": tuple(interruptions.values())} if interruptions else {})}
                result = await lease.guard(invoke_graph)
                if service.store.turn(turn['id'])['state'] == 'stopping':
                    raise asyncio.CancelledError
                if "__interrupt__" in result:
                    snapshot = await graph.aget_state(config)
                    review.sync(snapshot.interrupts)
                    return False
                messages = await service.drain_messages(turn)
                if messages:
                    value = {"messages": messages, "waiting_business": False}
                    continue
                if service.completions.pending(turn):
                    service.store.state(turn["id"], "waiting_business")
                    return False
                break
            final = next((item for item in reversed(result["messages"]) if isinstance(item, AIMessage) and not item.tool_calls), None)
            if final is not None:
                projected = projection.completed.get(final.id)
                if projected is None:
                    projected = project_message(final)
                references = await service.references.answer(turn, projected["text"], [
                    source for item in [*projection.completed.values(), projected] for source in item["sources"]])
                if conversation.get("planMode"):
                    service.runtime_card(turn, {"id": "plan_" + turn["id"], "type": "plan",
                                                "text": references["text"], "state": "waiting"})
                    return True
                service.runtime_card(turn, {"id": "answer_" + turn["id"], "type": "answer", "role": "assistant",
                    "text": references["text"], "modelStepId": projected.get("modelStepId"), "sources": projected["sources"], "references": references["references"],
                    "view": None, "attachments": [], "checkpointId": await service.history.checkpoint(conversation["id"])})
            return True
