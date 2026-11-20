"""User-controlled automatic and manual compaction on the Deep Agents summarizer."""

import asyncio
import json
import time
from contextvars import ContextVar

from deepagents.middleware.summarization import (
    SUMMARIZATION_SESSION_ID_KEY,
    SummarizationMiddleware,
)
from langchain_core.exceptions import ContextOverflowError
from langchain_core.messages import HumanMessage, SystemMessage
from langgraph.func import task

from .children import CHILD
from .projection import SUMMARIZING

# Different child model calls can use the middleware concurrently.
SUMMARY_BOUNDARY = ContextVar("summary_boundary")


class Compaction(SummarizationMiddleware):
    @property
    def name(self):
        return "SummarizationMiddleware"

    def __init__(self, service, turn, model, backend, *, child=False):
        self.service, self.turn, self.child = service, turn, child
        settings = service.store.defaults(turn["workspaceId"], kind=service.execution_conversation(turn).get("kind", "chat"))
        self.automatic = settings["autoCompact"]
        self.manual_ids = []
        summary_model = settings["summaryModelId"] or (settings["childModelId"] if child else None) or service.execution_conversation(turn)["modelId"]
        profile = service.catalogue.resolve(summary_model)
        budget = int(profile["contextWindow"] * 0.95) - profile["maxOutputTokens"]
        # These are separate durable graph tasks: the encompassing model node can still fail
        # after both have completed. Resuming that node replays their saved results.
        self.write_history = task(name="agent_compact_history")(self._write_history)
        self.make_summary = task(name="agent_compact_summary")(super()._acreate_summary)
        super().__init__(model, backend=backend, trigger=("fraction", settings["compactAt"] / 100),
                         keep=("messages", 6), summarizer=self.summarize, trim_tokens_to_summarize=max(1, budget))

    def _input_budget(self, request):
        # Ollama uses num_predict, while other providers have different max_tokens aliases.
        # Every lease records the actual wire limit in the same profile field.
        profile = request.model.profile
        return max(0, int(profile["max_input_tokens"] * 0.95) - profile["max_output_tokens"])

    def _get_session_id(self, state):
        # The first offload and its replay must reference the same history file.
        owner = CHILD.get() or self.turn
        return state.get(SUMMARIZATION_SESSION_ID_KEY) or "session_" + owner.get("childRunId", owner["id"])

    async def _write_history(self, messages, session_id):
        return await super()._aoffload_to_backend(self._backend, messages, session_id)

    async def _aoffload_to_backend(self, backend, messages, session_id):
        return await self.write_history(messages, session_id)

    async def _acreate_summary(self, messages):
        return await self.make_summary(messages)

    async def summarize(self, messages):
        service, turn = self.service, self.turn
        instructions = []
        for identity in self.manual_ids:
            row = service.store.db.execute("SELECT value FROM cards WHERE id=?", (identity,)).fetchone()
            if row:
                instructions.append(json.loads(row[0])["text"].removeprefix("/compact").strip())
        prompt = ("Summarize this conversation for continuation. Keep exact identifiers, amounts, units, quantities, "
                  "fees, sign-offs, observed results and unfinished work needed in this conversation. A request "
                  "not to save something in long-term memory or repeat it in a reply still requires keeping it "
                  "in this conversation summary for later turns. Preserve the scope of those instructions.")
        prompts = [SystemMessage(prompt), *messages, HumanMessage("\n".join([prompt, *instructions]))]
        turn = CHILD.get() or turn
        owner = service.execution_conversation(turn)
        model_id = service.store.defaults(turn["workspaceId"], kind=owner.get("kind", "chat"))["summaryModelId"] or owner["modelId"]
        started = time.time()
        token = SUMMARIZING.set(True)
        try:
            async with service.model_runtime.model(model_id, require_tools=False,
                                                   usage_context=service.usage_context(turn)) as lease:
                response = await lease.invoke(prompts)
        finally:
            SUMMARIZING.reset(token)
        if self.manual_ids:
            service.knowledge.compact_consumed(self.manual_ids)
        service.runtime_card(turn, {"type": "compaction", "id": f"compact_{started}", "startedAt": started,
                                    "finishedAt": time.time(), "text": response.text, "inputEndId": SUMMARY_BOUNDARY.get()[0]})
        return response.text

    def _should_summarize(self, messages, total_tokens):
        return bool(self.manual_ids) or SUMMARY_BOUNDARY.get()[1] or self.automatic and super()._should_summarize(messages, total_tokens)

    async def awrap_model_call(self, request, handler):
        self.manual_ids = [] if self.child else self.service.knowledge.compact_pending(self.turn)
        end_id = request.messages[-1].id
        reuse = False
        if self.turn["businessContinuation"]:
            child = CHILD.get()
            table, field, owner = ("child_details", "child", child["childRunId"]) if child else ("cards", "turn_id", self.turn["id"])
            reuse = self.service.store.db.execute(
                f"SELECT 1 FROM {table} WHERE {field}=? AND json_extract(value,'$.type')='compaction' "
                "AND json_extract(value,'$.inputEndId')=? LIMIT 1", (owner, end_id)).fetchone() is not None
        if self.automatic or self.manual_ids or reuse:
            marker = SUMMARY_BOUNDARY.set((end_id, reuse))
            try:
                return await super().awrap_model_call(request, handler)
            finally:
                SUMMARY_BOUNDARY.reset(marker)
        effective = self._get_effective_messages(request)
        count = await asyncio.to_thread(self._count_tokens, effective, request.system_message, request.tools)
        if self._over_budget(request, count):
            raise ContextOverflowError("This conversation reached the model's input limit. Use /compact to continue.")
        return await handler(request.override(messages=effective))
