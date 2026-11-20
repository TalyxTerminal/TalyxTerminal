"""Provider-neutral process/thinking data; only the final agent reply is an answer."""

from __future__ import annotations

import contextvars
import time

from langchain_core.callbacks import AsyncCallbackHandler

from .children import CHILD
from .providers import project_message

SUMMARIZING = contextvars.ContextVar("agent_summarizing", default=False)


class ProviderProjection(AsyncCallbackHandler):
    raise_error = True

    def __init__(self, service, turn, window):
        self.service, self.turn, self.window = service, turn, window
        self.running = {}
        self.completed = {}

    async def on_chat_model_start(self, serialized, messages, *, run_id, metadata=None, **kwargs):
        if SUMMARIZING.get():
            return
        if metadata is None or (metadata.get("turnId"), metadata.get("childRunId")) != (
            self.turn["id"], self.turn.get("childRunId")
        ):
            return
        owner = CHILD.get() or self.turn
        self.running[str(run_id)] = {"turn": owner, "startedAt": time.time(), "text": "", "reasoning": "",
                                     "lastEmitted": 0.0}

    async def on_llm_new_token(self, token, *, run_id, chunk=None, **kwargs):
        record = self.running.get(str(run_id))
        if record is None:
            return
        if chunk is not None:
            message = chunk.message
            for block in message.content_blocks:
                if block["type"] == "reasoning" and "reasoning" in block:
                    record["reasoning"] += block["reasoning"]
        record["text"] += token
        if time.time() - record["lastEmitted"] >= .15:
            self._process(str(run_id), record, "running")
            record["lastEmitted"] = time.time()

    def _process(self, run_id, record, state):
        self.service.runtime_card(record["turn"], {
            "id": "model_" + run_id, "type": "process_group", "title": "Composing response",
            "steps": [{"id": run_id, "title": "Model response", "state": state, "startedAt": record["startedAt"],
                "finishedAt": time.time() if state != "running" else None,
                "details": {"text": record["text"]}}]})
        if record["reasoning"]:
            self.service.runtime_card(record["turn"], {
                "id": "thinking_" + run_id, "type": "thinking", "text": record["reasoning"],
                "startedAt": record["startedAt"], "finishedAt": time.time() if state != "running" else None})

    async def on_llm_end(self, response, *, run_id, **kwargs):
        record = self.running.pop(str(run_id), None)
        if record is None:
            return
        message = response.generations[0][0].message
        projected = project_message(message)
        record["text"] = projected["text"]
        reasoning = "\n".join(block["reasoning"] for block in projected["contentBlocks"] if block["type"] == "reasoning" and "reasoning" in block)
        if reasoning:
            record["reasoning"] = reasoning
        self._process(str(run_id), record, "completed")
        self.completed[message.id] = {**projected, "modelStepId": str(run_id)}
        owner = record["turn"]
        usage = projected["usage"]
        if usage is not None:
            window = self.service.catalogue.resolve(owner["modelId"])["contextWindow"] if "childRunId" in owner else self.window
            self.service.context(owner, estimated=None, actual=usage["input_tokens"], window=window)

    async def on_llm_error(self, error, *, run_id, **kwargs):
        record = self.running.pop(str(run_id), None)
        if record is not None:
            self._process(str(run_id), record, "failed")
