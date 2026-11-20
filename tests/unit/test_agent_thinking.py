"""One thinking choice per model: levels from models.dev, and each provider's native request for a level."""

import asyncio

import pytest
from langchain_core.messages import HumanMessage, ToolMessage
from talyx_sdk import ContractError

from modules.agent.backend.providers import ProviderCatalog, ProviderRuntime, ProviderService
from modules.agent.backend.providers.service import PROBE
from modules.agent.backend.usage import UsageLedger
from tests.unit.test_agent_providers import provider_server


def info(reasoning=None, options=None, **extra):
    """One models.dev model entry, in its real shape."""
    model = {"name": "Model", "limit": {"context": 200000, "output": 32000}, "tool_call": True,
             "modalities": {"input": ["text", "image"]}, "cost": {"input": 1, "output": 2}, **extra}
    if reasoning is not None:
        model["reasoning"] = reasoning
    if options is not None:
        model["reasoning_options"] = options
    return model


def effort(*values):
    return {"type": "effort", "values": list(values)}


MODELS_DEV = {
    "openai": {"models": {"gpt-6-sol": info(True, [effort("none", "low", "medium", "high", "xhigh", "max")],
                                            limit={"context": 1050000, "input": 922000, "output": 128000}),
                          "gpt-5.4": info(True, [effort("none", "low", "medium", "high", "xhigh")]),
                          "gpt-4.1": info(False)}},
    "anthropic": {"models": {"claude-opus-5-5": info(True, [effort("low", "medium", "high", "xhigh", "max")]),
                             "claude-sonnet-5-5": info(True, [effort("low", "medium", "high", "xhigh", "max")]),
                             "claude-opus-4-7": info(True, [effort("low", "medium", "high", "xhigh", "max")]),
                             "claude-haiku-4-5": info(True, [{"type": "budget_tokens", "min": 1024}])}},
    "google": {"models": {"gemini-3.5-flash": info(True, [effort("minimal", "low", "medium", "high")]),
                          "gemini-2.5-pro": info(True, [{"type": "budget_tokens", "min": 128, "max": 32768}])}},
    "xai": {"models": {"grok-4.7": info(True, [effort("low", "medium", "high", "xhigh")])}},
    "deepseek": {"models": {"deepseek-v4-pro": info(True, [{"type": "toggle"}, effort("low", "high", "max")])}},
    "moonshotai": {"models": {"kimi-k3": info(True, [effort("low", "high", "max")]), "kimi-k2.6": info(True, [{"type": "toggle"}])}},
    "moonshotai-cn": {"models": {"kimi-k3": info(True, [effort("low", "high", "max")], name="Kimi K3 China"),
                                 "kimi-k2.6": info(True, [{"type": "toggle"}])}},
    "zhipuai": {"models": {"glm-5.3": info(True, [effort("low", "high", "max")]), "glm-4.7": info(True, [{"type": "toggle"}])}},
    "zai": {"models": {"glm-5.3": info(True, [effort("low", "high", "max")]), "glm-4.7": info(True, [{"type": "toggle"}])}},
    "openrouter": {"models": {"anthropic/claude-opus-5-5": info(True, [], name="Aggregated copy"),
                              "claude-opus-5-5": info(True, [], name="Aggregated copy")}},
}


def test_models_dev_levels_defaults_and_matching(tmp_path):
    catalog = ProviderCatalog(tmp_path / "models.sqlite")
    catalog.update_profiles(MODELS_DEV, version="v", refreshed=1)
    connections = {"openai": ("openai", "openai", None, "https://api.openai.com/v1"),
                   "anthropic": ("anthropic", "anthropic", None, "https://api.anthropic.com"),
                   "google": ("google", "google", None, "https://generativelanguage.googleapis.com"),
                   "xai": ("xai", "openai", None, "https://api.x.ai/v1"),
                   "deepseek": ("deepseek", "openai", None, "https://api.deepseek.com"),
                   "kimi": ("moonshotai", "openai", "domestic", "https://api.moonshot.cn/v1"),
                   "zhipu": ("zhipuai", "openai", "domestic", "https://open.bigmodel.cn/api/paas/v4")}
    for identity, (provider, protocol, site, url) in connections.items():
        catalog.put_connection({"id": identity, "name": identity, "providerId": provider, "kind": "official",
                                "protocol": protocol, "baseUrl": url, "secretRef": "key", "site": site})
    catalog.put_connection({"id": "relay", "name": "Relay", "providerId": "anthropic", "kind": "relay", "protocol": "anthropic",
                            "baseUrl": "https://relay.example/v1", "secretRef": "key"})
    claude = ["low", "medium", "high", "xhigh", "max"]
    expected = {
        ("openai", "gpt-6-sol"): (["off", "low", "medium", "high", "xhigh", "max"], "medium"),
        ("openai", "gpt-5.4"): (["off", "low", "medium", "high", "xhigh"], "off"),
        ("openai", "gpt-4.1"): (None, None),
        ("anthropic", "claude-opus-5-5"): (claude, "medium"),
        ("anthropic", "claude-sonnet-5-5"): (["off", *claude], "high"),
        ("anthropic", "claude-opus-4-7"): (["off", *claude], "off"),
        ("anthropic", "claude-haiku-4-5"): (["off", "low", "medium", "high"], "off"),
        ("google", "gemini-3.5-flash"): (["minimal", "low", "medium", "high"], "high"),
        ("google", "gemini-2.5-pro"): (["low", "medium", "high"], "high"),
        ("xai", "grok-4.7"): (["low", "medium", "high", "xhigh"], "high"),
        ("deepseek", "deepseek-v4-pro"): (["off", "low", "high", "max"], "high"),
        ("kimi", "kimi-k3"): (["low", "high", "max"], "max"),
        ("kimi", "kimi-k2.6"): (["off", "on"], "on"),
        ("zhipu", "glm-4.7"): (["off", "on"], "on"),
        ("relay", "anthropic/claude-opus-5-5"): (claude, "medium"),
        ("relay", "claude-opus-5-5"): (claude, "medium"),
    }
    for (connection, name), (levels, default) in expected.items():
        catalog.put_model({"id": connection + "/" + name, "connectionId": connection, "model": name})
        resolved = catalog.resolve(connection + "/" + name)
        schema = resolved["thinkingSchema"]
        assert schema["additionalProperties"] is False
        assert (schema["properties"]["effort"]["enum"] if levels else schema["properties"]) == (levels or {}), name
        assert resolved["thinkingDefault"] == default, name
    # Official sites read their own entry; relays take the original maker's entry over aggregated copies.
    assert catalog.resolve("kimi/kimi-k3")["profile"]["name"] == "Kimi K3 China"
    assert catalog.resolve("relay/anthropic/claude-opus-5-5")["profileProvider"] == "anthropic"
    assert catalog.resolve("relay/claude-opus-5-5")["profileProvider"] == "anthropic"
    sol = catalog.resolve("openai/gpt-6-sol")
    assert sol["contextWindow"] == 922000 and sol["profile"]["context_window"] == 1050000 and sol["images"] is True
    assert sol["price"]["input"] == "1"
    with pytest.raises(ContractError, match="Invalid Agent settings"):
        catalog.put_model({"id": "x", "connectionId": "openai", "model": "gpt-4.1", "thinkingSchema": {"type": "object"}})
    catalog.close()


# connection kind, provider, protocol, model, local thinking report, level, endpoint, what the provider receives
CASES = [
    ("official", "openai", "openai", "gpt-6-sol", None, "xhigh", "/v1/responses", lambda body: body["reasoning"] == {"effort": "xhigh"}),
    ("official", "openai", "openai", "gpt-5.4", None, "off", "/v1/responses", lambda body: body["reasoning"] == {"effort": "none"}),
    ("official", "xai", "openai", "grok-4.7", None, "medium", "/v1/responses", lambda body: body["reasoning"] == {"effort": "medium"}),
    ("official", "deepseek", "openai", "deepseek-v4-pro", None, "max", "/v1/chat/completions",
     lambda body: body["thinking"] == {"type": "enabled"} and body["reasoning_effort"] == "max"),
    ("official", "deepseek", "openai", "deepseek-v4-pro", None, "off", "/v1/chat/completions",
     lambda body: body["thinking"] == {"type": "disabled"} and "reasoning_effort" not in body),
    ("official", "moonshotai", "openai", "kimi-k3", None, "low", "/v1/chat/completions",
     lambda body: body["reasoning_effort"] == "low" and "thinking" not in body),
    ("official", "moonshotai", "openai", "kimi-k2.6", None, "off", "/v1/chat/completions", lambda body: body["thinking"] == {"type": "disabled"}),
    ("official", "zhipuai", "openai", "glm-5.3", None, "high", "/v1/chat/completions",
     lambda body: body["reasoning_effort"] == "high" and "thinking" not in body),
    ("official", "zhipuai", "openai", "glm-4.7", None, "on", "/v1/chat/completions",
     lambda body: body["thinking"] == {"type": "enabled"} and "reasoning_effort" not in body),
    ("relay", "openai", "openai", "gpt-5.4", None, "off", "/v1/chat/completions", lambda body: body["reasoning_effort"] == "none"),
    ("official", "anthropic", "anthropic", "claude-opus-5-5", None, "xhigh", "/v1/messages",
     lambda body: body["thinking"] == {"type": "adaptive", "display": "summarized"} and body["output_config"] == {"effort": "xhigh"}),
    ("official", "anthropic", "anthropic", "claude-sonnet-5-5", None, "off", "/v1/messages",
     lambda body: body["thinking"] == {"type": "between_tools"} and "output_config" not in body),
    ("official", "anthropic", "anthropic", "claude-opus-4-7", None, "off", "/v1/messages", lambda body: "thinking" not in body),
    ("official", "anthropic", "anthropic", "claude-haiku-4-5", None, "medium", "/v1/messages",
     lambda body: body["thinking"] == {"type": "enabled", "budget_tokens": 2047} and body["max_tokens"] == 2048
     and "output_config" not in body),
    ("official", "google", "google", "gemini-3.5-flash", None, "minimal", ":generateContent",
     lambda body: body["generationConfig"]["thinkingConfig"] == {"thinking_level": "MINIMAL", "include_thoughts": True}),
    ("official", "google", "google", "gemini-2.5-pro", None, "low", ":generateContent",
     lambda body: body["generationConfig"]["thinkingConfig"] == {"thinking_budget": 1024, "include_thoughts": True}),
    ("local", "ollama", "ollama", "qwen3:8b", True, "off", "/api/chat", lambda body: body["think"] is False),
    ("local", "ollama", "ollama", "gpt-oss:20b", True, "high", "/api/chat", lambda body: body["think"] == "high"),
]


@pytest.mark.parametrize(("kind", "provider", "protocol", "name", "discovered", "level", "path", "check"), CASES,
                         ids=[f"{case[1]}-{case[3]}-{case[5]}" for case in CASES])
def test_thinking_level_reaches_each_provider_natively(tmp_path, monkeypatch, kind, provider, protocol, name, discovered, level, path, check):
    import talyx_sdk.network as network

    import modules.agent.backend.providers.catalog as catalog_module
    import modules.agent.backend.providers.runtime as runtime_module
    monkeypatch.setattr(runtime_module, "system_http_proxy_url", lambda: None)
    monkeypatch.setattr(network, "system_http_proxy_url", lambda: None)
    with provider_server() as (base, requests):
        # Official Chinese sites are fixed addresses; this local server stands in for them.
        for site in (("moonshotai", "international"), ("zhipuai", "international")):
            monkeypatch.setitem(catalog_module.OFFICIAL_SITES, site, base + "/v1")
        async def scenario():
            catalog = ProviderCatalog(tmp_path / "models.sqlite")
            ledger = UsageLedger(tmp_path / "usage.sqlite")
            catalog.update_profiles(MODELS_DEV, version="v", refreshed=1)
            catalog.put_connection({"id": "c", "name": provider, "providerId": provider, "kind": kind, "protocol": protocol,
                "baseUrl": base + ("/v1" if protocol == "openai" else ""), "secretRef": None if kind == "local" else "key",
                "site": "international" if provider in {"moonshotai", "zhipuai"} else None})
            catalog.put_model({"id": "m", "connectionId": "c", "model": name, "contextWindow": 8192, "tools": True, "thinking": discovered})
            async def secret(_ref):
                return "unit-test-key"
            runtime = ProviderRuntime(catalog, ledger, secret)
            async with runtime.model("m", thinking={"effort": level}, output_reserve=16384) as lease:
                await lease.invoke([HumanMessage(content="probe")], tools=[PROBE])
            assert requests[0][0].endswith(path)
            body = requests[0][1]
            assert check(body), body
            if protocol == "ollama":
                sent = body["options"]["num_predict"]
            elif protocol == "google":
                sent = body["generationConfig"]["maxOutputTokens"]
            elif path.endswith("/responses"):
                sent = body["max_output_tokens"]
            else:
                sent = body["max_completion_tokens"] if "max_completion_tokens" in body else body["max_tokens"]
            assert sent == catalog.resolve("m")["maxOutputTokens"] == 2048
            with pytest.raises(ContractError, match="thinking levels"):
                async with runtime.model("m", thinking={"reasoning_effort": "high"}):
                    pytest.fail("Native parameters are not a thinking choice")
            catalog.close()
            ledger.close()
        asyncio.run(scenario())


def test_chat_completions_reasoning_is_shown_and_sent_back_with_tools(tmp_path, monkeypatch):
    import talyx_sdk.network as network

    import modules.agent.backend.providers.runtime as runtime_module
    monkeypatch.setattr(runtime_module, "system_http_proxy_url", lambda: None)
    monkeypatch.setattr(network, "system_http_proxy_url", lambda: None)
    with provider_server() as (base, requests):
        async def scenario():
            catalog = ProviderCatalog(tmp_path / "models.sqlite")
            ledger = UsageLedger(tmp_path / "usage.sqlite")
            catalog.put_connection({"id": "relay", "name": "Relay", "providerId": "openai", "kind": "relay", "protocol": "openai",
                                    "baseUrl": base + "/v1", "secretRef": "key"})
            catalog.put_model({"id": "m", "connectionId": "relay", "model": "deepseek-v4-pro", "contextWindow": 8192, "tools": True})
            async def secret(_ref):
                return "unit-test-key"
            async with ProviderRuntime(catalog, ledger, secret).model("m") as lease:
                first = await lease.invoke([HumanMessage(content="probe")], tools=[PROBE])
                assert first.content_blocks[0] == {"type": "reasoning", "reasoning": "Weighing the probe"}
                await lease.invoke([HumanMessage(content="probe"), first, ToolMessage(content="talyx", tool_call_id="call-1")], tools=[PROBE])
            assert requests[1][1]["messages"][1]["reasoning_content"] == "Weighing the probe"
            catalog.close()
            ledger.close()
        asyncio.run(scenario())


def test_model_info_refreshes_when_a_connection_is_added_and_when_a_day_old(tmp_path, monkeypatch):
    import modules.agent.backend.providers.service as service_module

    async def scenario():
        catalog = ProviderCatalog(tmp_path / "models.sqlite")
        service = ProviderService(catalog, None)
        fetched = asyncio.Queue()
        now = [1000.0]
        async def refresh():
            catalog.update_profiles(MODELS_DEV, version="v", refreshed=now[0])
            await fetched.put(now[0])
        monkeypatch.setattr(service, "refresh_profiles", refresh)
        monkeypatch.setattr(service_module.time, "time", lambda: now[0])
        task = asyncio.create_task(service.keep_fresh())
        await asyncio.sleep(.05)
        assert fetched.empty()  # No connection yet, nothing to describe.
        catalog.put_connection({"id": "deepseek", "name": "DeepSeek", "providerId": "deepseek", "kind": "official",
                                "protocol": "openai", "baseUrl": "https://api.deepseek.com", "secretRef": "key"})
        service.connection_added()
        async with asyncio.timeout(2):
            await service.first_info()  # The first provider's model list waits for this download.
            assert catalog.refreshed() is not None
            assert await fetched.get() == 1000.0
        service.wake.set()
        await asyncio.sleep(.05)
        assert fetched.empty()  # Still fresh.
        now[0] += service_module.STALE_AFTER
        service.wake.set()
        async with asyncio.timeout(2):
            assert await fetched.get() == 1000.0 + service_module.STALE_AFTER
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)
        catalog.close()
    asyncio.run(scenario())
