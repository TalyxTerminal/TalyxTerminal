"""Settings-facing discovery/testing and the independent Google grounding tool."""

from __future__ import annotations

import asyncio
import contextvars
import hashlib
import json
import logging
import ssl
import time

import httpx2
from langchain_core.messages import HumanMessage
from talyx_sdk import ContractError
from talyx_sdk.i18n import t
from talyx_sdk.network import (
    await_network_route,
    system_http_proxy_url,
)

from .errors import PROVIDER_ERRORS, provider_error
from .projection import project_message
from .retry import retry_request

PROBE = {"name": "talyx_connection_probe", "description": "Return the exact supplied word; this changes nothing.",
         "parameters": {"type": "object", "properties": {"word": {"type": "string"}}, "required": ["word"], "additionalProperties": False}}
MODELS_DEV = "https://models.dev/api.json"
STALE_AFTER = 24 * 3600
RETRY_AFTER = 3600
log = logging.getLogger(__name__)


class ProviderService:
    def __init__(self, catalog, runtime):
        self.catalog, self.runtime = catalog, runtime
        self.wake = asyncio.Event()
        self.added = False
        self.first = None
        self.task = None

    async def start(self):
        self.task = asyncio.create_task(self.keep_fresh(), context=contextvars.Context())

    def connection_added(self):
        self.added = True
        if self.catalog.refreshed() is None and self.first is None:
            self.first = asyncio.get_running_loop().create_future()
        self.wake.set()

    async def first_info(self):
        """Wait while the very first model info download runs, so the first provider's model list isn't empty."""
        if self.first is not None:
            await asyncio.shield(self.first)

    async def keep_fresh(self):
        """Model info is fetched when a connection is added, then again once it is a day old."""
        while True:
            self.wake.clear()
            source = self.catalog.refreshed()
            age = None if source is None else time.time() - source["refreshedAt"]
            wait = STALE_AFTER if age is None else max(1.0, STALE_AFTER - age)
            if self.added or self.catalog.list_connections() and (age is None or age >= STALE_AFTER):
                self.added = False
                try:
                    await self.refresh_profiles()
                    wait = STALE_AFTER
                except (*PROVIDER_ERRORS, json.JSONDecodeError, KeyError, ContractError) as error:
                    # models.dev is an external download; a failed refresh keeps the previous snapshot.
                    log.warning("Model info refresh failed: %s", error.message if isinstance(error, ContractError)
                                else provider_error(error)["message"] if isinstance(error, PROVIDER_ERRORS) else repr(error))
                    wait = RETRY_AFTER
                if self.first is not None:
                    self.first.set_result(None)
                    self.first = None
            try:
                await asyncio.wait_for(self.wake.wait(), wait)
            except TimeoutError:
                pass

    async def discover(self, kind, *, base_url=None):
        """Finite probes only when settings opens or the user refreshes local models."""
        if kind not in {"ollama", "lmstudio"}:
            raise ContractError("invalid_input", t("Discovery requires Ollama or LM Studio"))
        base = base_url or ("http://127.0.0.1:11434" if kind == "ollama" else "http://127.0.0.1:1234")
        try:
            async with httpx2.AsyncClient(base_url=base, trust_env=False, verify=ssl.create_default_context(), timeout=3) as client:
                response = await client.get("/api/tags" if kind == "ollama" else "/api/v0/models")
                response.raise_for_status()
                data = response.json()
                models = []
                if kind == "ollama":
                    for item in data["models"]:
                        detail_response = await client.post("/api/show", json={"model": item["name"]})
                        detail_response.raise_for_status()
                        detail = detail_response.json()
                        info = detail["model_info"] if "model_info" in detail else {}
                        contexts = [value for key, value in info.items() if key.endswith(".context_length")]
                        capabilities = detail["capabilities"] if "capabilities" in detail else None
                        models.append({"model": item["name"], "contextWindow": contexts[0] if contexts else None,
                            "tools": "tools" in capabilities if capabilities is not None else None,
                            "images": "vision" in capabilities if capabilities is not None else None,
                            "thinking": "thinking" in capabilities if capabilities is not None else None})
                else:
                    for item in data["data"]:
                        # LM Studio lists capabilities such as ["tool_use"]; thinking otherwise comes from models.dev.
                        capabilities = item["capabilities"] if "capabilities" in item else None
                        models.append({"model": item["id"],
                            "contextWindow": item["max_context_length"] if "max_context_length" in item else None,
                            "tools": "tool_use" in capabilities if capabilities is not None else None,
                            "images": item["type"] == "vlm" if "type" in item else None,
                            "thinking": True if capabilities is not None and "reasoning" in capabilities else None})
                return {"kind": kind, "state": "available", "models": models}
        except (httpx2.HTTPError, ValueError, KeyError) as error:
            # A missing/invalid local response is external input, not an internal invariant.
            return {"kind": kind, "state": "unavailable", "models": [], "reason": provider_error(error)}

    async def refresh_profiles(self):
        proxy = system_http_proxy_url()
        async def fetch():
            async with httpx2.AsyncClient(proxy=proxy, trust_env=False, verify=ssl.create_default_context(), timeout=30) as client:
                async with client.stream("GET", MODELS_DEV) as response:
                    response.raise_for_status()
                    content = bytearray()
                    async for chunk in response.aiter_bytes():
                        content.extend(chunk)
                        if len(content) > 20 * 1024 * 1024:
                            raise ContractError("invalid_output", t("Model profile catalog exceeds the download limit"))
            data = json.loads(content)
            version, refreshed = hashlib.sha256(content).hexdigest(), time.time()
            self.catalog.update_profiles(data, version=version, refreshed=refreshed)
            return {"version": version, "refreshedAt": refreshed}
        return await await_network_route(fetch, proxy)

    async def provider_search(self, model_id, query, objective="", *, _test=False, usage_context=None):
        resolved = self.catalog.resolve(model_id, billing=True)
        if not _test and resolved["searchMode"] != "provider":
            raise ContractError("unsupported", t("This model is configured to use TALYX search"))
        connection = self.catalog.connection(resolved["connectionId"])
        native_model = self.catalog.model(model_id)
        family = native_model.profileModel or native_model.model
        unit = "google_grounding_query" if family.startswith("gemini-3") else "google_grounding_request"
        tools = [{'google_search':{}}] if connection.protocol == 'google' else self.runtime.native_search_tools(model_id)
        async with self.runtime.model(model_id, require_tools=False, provider_search=True,
            search_units={unit:1} if connection.protocol=='google' else {}, usage_context=usage_context) as lease:
            model = lease.model.bind_tools(tools)
            messages = [HumanMessage(content=f"Search the web for: {query}\nObjective: {objective}\nCite the sources you used.")]
            async def request():
                if usage_context is not None and usage_context.unattended:
                    return await retry_request(lambda: model.ainvoke(messages))
                return await model.ainvoke(messages)
            message = await lease.guard(request)
            projected = project_message(message)
            if not projected['sources'] or connection.protocol=='google' and projected['groundingMetadata'] is None:
                raise ContractError("search_unconfirmed", t("The provider returned no confirmed sources for this search"))
            return projected

    async def test_connection(self, model_id, *, thinking=None, test_search=True):
        fingerprint = self.catalog.fingerprint(model_id)
        connection = self.catalog.connection(self.catalog.model(model_id).connectionId)
        checks = {}
        result = {"state": "tested", "checks": checks, "testedAt": time.time()}
        try:
            async with self.runtime.model(model_id, thinking=thinking, output_reserve=2048, require_tools=False) as lease:
                # Thinking modes (DeepSeek, Anthropic) reject a forced tool_choice; the prompt asks for the call and the check below observes it.
                model = lease.model.bind_tools([PROBE])
                response = await lease.guard(lambda: model.ainvoke([HumanMessage(content="Call talyx_connection_probe with word talyx. Do not answer with text.")]))
                checks["connection"] = {"state": "passed"}
                called = any(call["name"] == PROBE["name"] and call["args"] == {"word": "talyx"} for call in response.tool_calls)
                checks["tools"] = {"state": "passed" if called else "failed",
                                   "message": "Tool call observed" if called else "This model did not return the required tool call"}
                # Sending an accepted parameter is not evidence that an endpoint honored it.
                reasoning_observed = any(block["type"] == "reasoning" for block in response.content_blocks)
                checks["thinking"] = {"state": "passed" if thinking and reasoning_observed else "accepted_unconfirmed" if thinking else "provider_default"}
        except PROVIDER_ERRORS as error:
            checks["connection"] = provider_error(error)
            checks["tools"] = {"state": "not_tested"}
            checks["thinking"] = {"state": "not_tested"}
        except ContractError as error:
            if error.code == "budget_exceeded":
                raise
            checks["connection"] = {"state": "failed", "code": error.code, "message": error.message}
            checks["tools"] = {"state": "not_tested"}
            checks["thinking"] = {"state": "not_tested"}
        supports_search = (connection.kind == "official" and connection.providerId in {"openai", "anthropic", "google", "xai"}
                           or connection.kind == "relay")
        if test_search and checks["connection"]["state"] == "passed" and supports_search:
            try:
                if connection.protocol == "google":
                    projected = await self.provider_search(model_id, "OpenAI official documentation", "Find one official source", _test=True)
                else:
                    tools = [{"type": "web_search"}] if connection.protocol == "openai" else [{"type": "web_search_20250305", "name": "web_search", "max_uses": 1}]
                    async with self.runtime.model(model_id, output_reserve=4096, require_tools=False, provider_search=True) as lease:
                        model = lease.model.bind_tools(tools, tool_choice="web_search")
                        response = await lease.guard(lambda: model.ainvoke([HumanMessage(content="Search the web for the official Python documentation. Give one source with a citation.")]))
                        projected = project_message(response)
                checks["search"] = {"state": "passed" if projected["sources"] else "failed", "sources": projected["sources"]}
            except PROVIDER_ERRORS as error:
                checks["search"] = provider_error(error)
            except ContractError as error:
                if error.code == "budget_exceeded":
                    raise
                checks["search"] = {"state": "failed", "code": error.code, "message": error.message}
        else:
            checks["search"] = {"state": "not_tested" if not test_search else "unsupported"}
        self.catalog.save_test(model_id, fingerprint, result)
        return result
