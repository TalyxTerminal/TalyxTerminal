"""Per-use official LangChain models with owned transports and shared usage callbacks."""

from __future__ import annotations

import asyncio
import ssl
from contextlib import AsyncExitStack, asynccontextmanager
from ipaddress import ip_address
from urllib.parse import urlsplit

import anthropic
import httpx2
from google import genai
from google.genai.types import HttpOptions, HttpRetryOptions
from langchain_anthropic import ChatAnthropic
from langchain_core.callbacks import AsyncCallbackHandler
from langchain_google_genai import ChatGoogleGenerativeAI
from langchain_ollama import ChatOllama
from langchain_openai import ChatOpenAI
from talyx_sdk import ContractError
from talyx_sdk.network import (
    NetworkRouteChanged,
    await_network_route,
    system_http_proxy_url,
    system_route_epoch,
)

from ..usage import Price, UsageCallback
from . import thinking as thinking_levels
from .errors import TRANSPORT_ERRORS, provider_error
from .reasoning import ReasoningChatOpenAI
from .xai import ResponseUsage, XAIResponses


def is_loopback(url):
    """The user-entered server address names this computer."""
    host = urlsplit(url).hostname
    if host == "localhost":
        return True
    try:
        return ip_address(host).is_loopback
    except ValueError:
        return False


class RouteCallback(AsyncCallbackHandler):
    raise_error = True

    def __init__(self, proxy, epoch, loopback=False):
        self.proxy, self.epoch, self.loopback = proxy, epoch, loopback

    def check(self):
        # A model server on this computer is reached the same way whatever the system route is.
        if self.loopback:
            return
        if system_http_proxy_url() != self.proxy or system_route_epoch() != self.epoch:
            raise NetworkRouteChanged("The model transport's route changed; start a new request")

    async def on_chat_model_start(self, serialized, messages, **kwargs):
        self.check()

    async def on_llm_new_token(self, token, **kwargs):
        self.check()


class ModelLease:
    def __init__(self, model, resolved, route):
        self.model, self.resolved, self.route = model, resolved, route

    async def guard(self, operation):
        """Wrap an entire engine invocation to cover quiet streams and summarizer calls."""
        try:
            if self.route.loopback:
                return await operation()
            return await await_network_route(operation, self.route.proxy, route_epoch=self.route.epoch)
        except TRANSPORT_ERRORS as error:
            failure = provider_error(error)
            raise ContractError(failure["code"], failure["message"], execution="unknown", effects="may_have_occurred") from None

    async def invoke(self, messages, *, tools=None, config=None, **kwargs):
        model = self.model.bind_tools(tools) if tools is not None else self.model
        return await self.guard(lambda: model.ainvoke(messages, config=config, **kwargs))


class ProviderRuntime:
    def __init__(self, catalog, ledger, get_secret):
        self.catalog, self.ledger, self.get_secret = catalog, ledger, get_secret

    @asynccontextmanager
    async def model(self, model_id, *, thinking=None, output_reserve=None, require_tools=True, search_units=None,
                    provider_search=False, usage_context=None):
        resolved = self.catalog.resolve(model_id, billing=True)
        connection = self.catalog.connection(resolved["connectionId"])
        if resolved["contextWindow"] is None:
            raise ContractError("invalid_input", "Configure this model's context window before using Agent")
        maximum = resolved["maxOutputTokens"]
        output_reserve = maximum if output_reserve is None else min(output_reserve, maximum)
        if require_tools and resolved["tools"] is False:
            raise ContractError("unsupported", "This model does not support tool calls; choose a tool-capable model")
        if require_tools:
            tested = self.catalog.read_test(model_id)
            if "tools" in tested["checks"] and tested["checks"]["tools"]["state"] == "failed":
                raise ContractError("unsupported", "This connection failed its tool-call test; choose another model or correct its configuration")
            if resolved["tools"] is None and ("tools" not in tested["checks"] or tested["checks"]["tools"]["state"] != "passed"):
                raise ContractError("unsupported", "Tool support is unknown; test this connection or explicitly configure the model's capabilities")
        if provider_search and connection.kind == "official" and connection.providerId not in {"openai", "anthropic", "google", "xai"}:
            raise ContractError("unsupported", "This provider's native search is not integrated")
        levels = self.catalog.thinking(model_id)
        native, budget = thinking_levels.native(connection, resolved, levels, thinking_levels.level(levels, thinking))
        if budget:
            if output_reserve <= 1024:
                raise ContractError("invalid_input", f"Output limit {output_reserve} tokens cannot fit the minimum "
                                    "1024-token thinking budget and a reply. Increase the limit or turn thinking off.")
            native["thinking"]["budget_tokens"] = min(budget, output_reserve - 1)
        if native.get("thinking_budget", -1) > output_reserve:
            native["thinking_budget"] = output_reserve - 1
        key = await self.get_secret(connection.secretRef) if connection.secretRef is not None else "local"
        if not key:
            raise ContractError("credential_missing", "Configure the model connection's API key")
        proxy = system_http_proxy_url()
        epoch = system_route_epoch()
        local = connection.kind == "local"
        # Explicit user-configured local servers are reached directly, without a remote proxy.
        transport_proxy = None if local else proxy
        route = RouteCallback(proxy, epoch, loopback=local and is_loopback(connection.baseUrl))
        price = Price(**resolved["price"]) if resolved["price"] is not None else None
        usage = UsageCallback(self.ledger, connection.id, model_id, price=price,
                              search_units=search_units, before_request=route.check,
                              context=usage_context,
                              provider_id=connection.providerId if connection.kind == "official" else None)
        common = {"model": resolved["model"], "callbacks": [route, usage], "max_retries": 0, **native}
        tls = await asyncio.to_thread(ssl.create_default_context) if connection.protocol in {"openai", "anthropic", "google"} else None
        async with AsyncExitStack() as stack:
            stack.callback(usage.abandon)
            if connection.protocol == "openai":
                raw_usage = ResponseUsage() if connection.kind == "official" and connection.providerId == "xai" else None
                sync = await asyncio.to_thread(httpx2.Client, proxy=transport_proxy, trust_env=False, verify=tls, timeout=60,
                                    event_hooks={"response": [raw_usage.sync_hook]} if raw_usage else {})
                asynchronous = await asyncio.to_thread(httpx2.AsyncClient, proxy=transport_proxy, trust_env=False, verify=tls, timeout=60,
                                                 event_hooks={"response": [raw_usage.async_hook]} if raw_usage else {})
                stack.callback(sync.close)
                stack.push_async_callback(asynchronous.aclose)
                responses = (provider_search or connection.kind == "official" and connection.providerId in {"openai", "xai"}
                             or connection.kind == "relay" and resolved["searchMode"] == "provider")
                model_type = XAIResponses if raw_usage else ChatOpenAI if responses else ReasoningChatOpenAI
                interleaved = resolved["profile"].get("interleaved")
                if model_type is ReasoningChatOpenAI:
                    common["catalog_model_id"] = model_id
                    common["reasoning_field"] = interleaved.get("field") if isinstance(interleaved, dict) else None
                model = await asyncio.to_thread(model_type, **common, api_key=key, base_url=connection.baseUrl,
                    http_client=sync, http_async_client=asynchronous, stream_usage=True, max_tokens=output_reserve,
                    use_responses_api=responses, include=["web_search_call.action.sources"] if responses else None)
                if raw_usage is not None:
                    model.set_response_usage(raw_usage)
            elif connection.protocol == "anthropic":
                sync = await asyncio.to_thread(httpx2.Client, proxy=transport_proxy, trust_env=False, verify=tls, timeout=60)
                asynchronous = await asyncio.to_thread(httpx2.AsyncClient, proxy=transport_proxy, trust_env=False, verify=tls, timeout=60)
                client = anthropic.Anthropic(api_key=key, base_url=connection.baseUrl, http_client=sync, max_retries=0)
                async_client = anthropic.AsyncAnthropic(api_key=key, base_url=connection.baseUrl, http_client=asynchronous, max_retries=0)
                stack.callback(client.close)
                stack.push_async_callback(async_client.close)
                model = ChatAnthropic(**common, api_key=key, base_url=connection.baseUrl, max_tokens=output_reserve)
                # Fixed 1.7.6 exposes proxy only, and globally caches its default clients.
                # Bind official SDK clients to this instance, preserving SDK request/stream logic.
                model._client = client
                model._async_client = async_client
            elif connection.protocol == "google":
                model = ChatGoogleGenerativeAI(**common, api_key=key, base_url=connection.baseUrl,
                    vertexai=False, client_args={"proxy": transport_proxy, "trust_env": False}, max_output_tokens=output_reserve)
                await model.aclose()
                sync = await asyncio.to_thread(httpx2.Client, proxy=transport_proxy, trust_env=False, verify=tls, timeout=60)
                asynchronous = await asyncio.to_thread(httpx2.AsyncClient, proxy=transport_proxy, trust_env=False, verify=tls, timeout=60)
                client = genai.Client(api_key=key, vertexai=False, http_options=HttpOptions(base_url=connection.baseUrl,
                    httpx_client=sync, httpx_async_client=asynchronous, retry_options=HttpRetryOptions(attempts=1)))
                model.client = client
                stack.callback(client.close)
                stack.push_async_callback(client.aio.aclose)
            else:
                model = ChatOllama(**common, base_url=connection.baseUrl, num_ctx=resolved["contextWindow"],
                    num_predict=output_reserve, client_kwargs={"proxy": None, "trust_env": False, "timeout": 60})
                stack.callback(model._client._client.close)
                stack.push_async_callback(model._async_client._client.aclose)
            model.profile = {**(model.profile or {}), "max_input_tokens": resolved["contextWindow"],
                             "max_output_tokens": output_reserve}
            yield ModelLease(model, resolved, route)

    def native_search_tools(self, model_id):
        resolved = self.catalog.resolve(model_id)
        if resolved["searchMode"] != "provider":
            return []
        connection = self.catalog.connection(resolved["connectionId"])
        if connection.kind == "official" and connection.providerId not in {"openai", "anthropic", "google", "xai"}:
            raise ContractError("unsupported", "This provider uses TALYX search; its native search is not integrated")
        if connection.kind == "relay":
            tests = self.catalog.read_test(model_id)
            if "search" not in tests["checks"] or tests["checks"]["search"]["state"] != "passed":
                raise ContractError("unsupported", "Test provider search on this relay before using it")
        if connection.protocol == "openai":
            return [{"type": "web_search"}]
        if connection.protocol == "anthropic":
            return [{"type": "web_search_20250305", "name": "web_search", "max_uses": 5}]
        if connection.protocol == "google":
            # K8 exposes provider_search(query, objective); grounding runs in a separate request.
            return []
        raise ContractError("unsupported", "This connection has no supported provider search")
