"""Preserve xAI billing fields that ChatOpenAI's Responses projection omits."""

import json

import httpx2
from langchain_openai import ChatOpenAI
from pydantic import PrivateAttr


class ResponseUsage:
    def __init__(self):
        self.responses = {}

    def parser(self, response):
        streaming = "text/event-stream" in response.headers.get("content-type", "")
        pending = bytearray()

        def receive(chunk, *, final=False):
            pending.extend(chunk)
            if streaming:
                while b"\n" in pending:
                    line, _, rest = pending.partition(b"\n")
                    pending[:] = rest
                    if line.startswith(b"data:") and line[5:].strip() not in (b"[DONE]", b""):
                        value = json.loads(line[5:].lstrip())
                        if value["type"] in {"response.completed", "response.incomplete"}:
                            self.remember(value["response"])
            elif final:
                self.remember(json.loads(pending))
        return receive

    def remember(self, response):
        if "id" in response and "usage" in response:
            self.responses[response["id"]] = response["usage"]

    def sync_hook(self, response):
        response.stream = UsageStream(response.stream, self.parser(response))

    async def async_hook(self, response):
        response.stream = AsyncUsageStream(response.stream, self.parser(response))

    def attach(self, message, response_id=None):
        metadata = message.response_metadata
        # Keep LangChain's OpenAI block decoder; this is the actual endpoint identity.
        metadata["talyxProvider"] = "xai"
        response_id = metadata["id"] if "id" in metadata else response_id
        if response_id in self.responses and message.usage_metadata is not None:
            metadata["rawUsage"] = self.responses.pop(response_id)


class UsageStream(httpx2.SyncByteStream):
    def __init__(self, stream, receive):
        self.stream, self.receive = stream, receive

    def __iter__(self):
        for chunk in self.stream:
            self.receive(chunk)
            yield chunk
        self.receive(b"", final=True)

    def close(self):
        self.stream.close()


class AsyncUsageStream(httpx2.AsyncByteStream):
    def __init__(self, stream, receive):
        self.stream, self.receive = stream, receive

    async def __aiter__(self):
        async for chunk in self.stream:
            self.receive(chunk)
            yield chunk
        self.receive(b"", final=True)

    async def aclose(self):
        await self.stream.aclose()


class XAIResponses(ChatOpenAI):
    _response_usage: ResponseUsage = PrivateAttr()

    def set_response_usage(self, usage):
        self._response_usage = usage

    async def _agenerate(self, *args, **kwargs):
        result = await super()._agenerate(*args, **kwargs)
        for generation in result.generations:
            self._response_usage.attach(generation.message)
        return result

    def _generate(self, *args, **kwargs):
        result = super()._generate(*args, **kwargs)
        for generation in result.generations:
            self._response_usage.attach(generation.message)
        return result

    async def _astream(self, *args, **kwargs):
        response_id = None
        async for chunk in super()._astream(*args, **kwargs):
            if "id" in chunk.message.response_metadata:
                response_id = chunk.message.response_metadata["id"]
            self._response_usage.attach(chunk.message, response_id)
            yield chunk

    def _stream(self, *args, **kwargs):
        response_id = None
        for chunk in super()._stream(*args, **kwargs):
            if "id" in chunk.message.response_metadata:
                response_id = chunk.message.response_metadata["id"]
            self._response_usage.attach(chunk.message, response_id)
            yield chunk
