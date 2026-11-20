"""Actual official integration HTTP transports, with a local deterministic provider server."""

import asyncio
import json
import threading
import time
from contextlib import contextmanager
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest
from langchain_core.messages import HumanMessage
from talyx_sdk import ContractError
from talyx_sdk.network import NetworkRouteChanged

from modules.agent.backend.providers import ProviderCatalog, ProviderRuntime, ProviderService
from modules.agent.backend.providers.projection import project_message
from modules.agent.backend.providers.service import PROBE
from modules.agent.backend.usage import UsageContext, UsageLedger


@contextmanager
def provider_server(statuses=()):
    requests = []
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass
        def do_POST(self):
            value = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
            requests.append((self.path, value, dict(self.headers)))
            if len(requests) <= len(statuses) and statuses[len(requests) - 1] != 200:
                status = statuses[len(requests) - 1]
                body = json.dumps({"error": {"code": status, "message": "Temporary provider limit", "status": "RESOURCE_EXHAUSTED"}}).encode()
                self.send_response(status)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(body)))
                self.send_header("Retry-After", "0")
                self.end_headers()
                self.wfile.write(body)
                return
            if "messages" in value and any(message["content"] == "delay" for message in value["messages"]):
                time.sleep(1)
            if self.path.endswith("/responses"):
                output = [{"type": "function_call", "id": "function-1", "call_id": "call-1", "name": PROBE["name"],
                           "arguments": '{"word":"talyx"}', "status": "completed"}]
                if any(tool["type"] == "web_search" for tool in value["tools"]):
                    output = [{"type": "web_search_call", "id": "search-1", "status": "completed",
                        "action": {"type": "search", "query": "Python", "sources": [{"type": "url", "url": "https://www.python.org"}]}},
                        {"type": "message", "id": "message-1", "role": "assistant", "status": "completed", "content": [
                            {"type": "output_text", "text": "Python", "annotations": [{"type": "url_citation", "url": "https://www.python.org",
                                "title": "Python", "start_index": 0, "end_index": 6}]}]}]
                response = {"id": "response-1", "object": "response", "created_at": 1, "status": "completed", "error": None,
                    "model": "model", "output": output, "usage": {"input_tokens": 12, "output_tokens": 4, "total_tokens": 16,
                        "input_tokens_details": {"cached_tokens": 0}, "output_tokens_details": {"reasoning_tokens": 0}}}
                if value["model"] == "grok-test":
                    response["usage"]["server_side_tool_usage_details"] = {"web_search_calls": 2}
                    if value["stream"]:
                        initial = {**response, "status": "in_progress", "output": [], "usage": None}
                        events = [{"type": "response.created", "response": initial, "sequence_number": 0},
                                  {"type": "response.completed", "response": response, "sequence_number": 1}]
                        body = b"".join(b"data: " + json.dumps(event).encode() + b"\n\n" for event in events)
                        self.send_response(200)
                        self.send_header("Content-Type", "text/event-stream")
                        self.send_header("Content-Length", str(len(body)))
                        self.end_headers()
                        self.wfile.write(body)
                        return
            elif self.path.endswith("/chat/completions"):
                response = {"id": "chatcmpl-1", "object": "chat.completion", "created": 1, "model": "model",
                    "choices": [{"index": 0, "message": {"role": "assistant", "content": "ok", "tool_calls": [
                        {"id": "call-1", "type": "function", "function": {"name": PROBE["name"], "arguments": '{"word":"talyx"}'}}]},
                        "finish_reason": "tool_calls"}], "usage": {"prompt_tokens": 12, "completion_tokens": 4, "total_tokens": 16}}
                if value["model"].startswith("deepseek"):
                    response["choices"][0]["message"]["reasoning_content"] = "Weighing the probe"
            elif self.path.endswith("/messages"):
                response = {"id": "msg-1", "type": "message", "role": "assistant", "model": "model",
                    "content": [{"type": "tool_use", "id": "tool-1", "name": PROBE["name"], "input": {"word": "talyx"}}],
                    "stop_reason": "tool_use", "stop_sequence": None, "usage": {"input_tokens": 12, "output_tokens": 4}}
            elif "generateContent" in self.path:
                response = {"candidates": [{"content": {"role": "model", "parts": [{"functionCall": {
                    "name": PROBE["name"], "args": {"word": "talyx"}}}]}, "finishReason": "STOP", "index": 0}],
                    "usageMetadata": {"promptTokenCount": 12, "candidatesTokenCount": 4, "totalTokenCount": 16}, "modelVersion": "model"}
                if any("googleSearch" in tool for tool in value["tools"]):
                    response["candidates"][0]["content"]["parts"] = [{"text": "A grounded answer"}]
                    response["candidates"][0]["groundingMetadata"] = {
                        "groundingChunks": [{"web": {"uri": "https://example.com/article", "title": "Article"}}],
                        "groundingSupports": [{"segment": {"startIndex": 0, "endIndex": 8}, "groundingChunkIndices": [0]}],
                        "webSearchQueries": ["first query", "second query"]}
            elif self.path.endswith("/api/chat"):
                response = {"model": "model", "created_at": "2026-10-24T12:00:00Z", "done": True, "done_reason": "stop",
                    "message": {"role": "assistant", "content": "", "tool_calls": [{"function": {
                        "name": PROBE["name"], "arguments": {"word": "talyx"}}}]}, "prompt_eval_count": 12, "eval_count": 4}
            else:
                self.send_error(404)
                return
            body = json.dumps(response).encode() + (b"\n" if self.path.endswith("/api/chat") else b"")
            self.send_response(200)
            self.send_header("Content-Type", "application/x-ndjson" if self.path.endswith("/api/chat") else "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            try:
                self.wfile.write(body)
            except (BrokenPipeError, ConnectionResetError):
                pass
    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{server.server_port}", requests
    finally:
        server.shutdown()
        server.server_close()
        thread.join()


def test_catalog_requires_window_preserves_overrides_and_invalidates_tests(tmp_path):
    catalog = ProviderCatalog(tmp_path / "models.sqlite")
    catalog.put_connection({"id": "relay", "name": "Relay", "providerId": "openai", "kind": "relay", "protocol": "openai",
                            "baseUrl": "https://example.com/v1", "secretRef": "provider.key"})
    with pytest.raises(ContractError, match="context window"):
        catalog.put_model({"id": "unknown", "connectionId": "relay", "model": "unknown"})
    catalog.put_model({"id": "known", "connectionId": "relay", "model": "private-alias",
        "profileProvider": "openai", "profileModel": "gpt-5", "contextWindow": 12345})
    resolved = catalog.resolve("known")
    assert resolved["contextWindow"] == 12345 and resolved["tools"] is True
    # Before any models.dev refresh, the bundled LangChain profile supplies the levels.
    assert resolved["thinkingSchema"]["properties"]["effort"]["enum"] == ["minimal", "low", "medium", "high"]
    assert resolved["thinkingDefault"] == "medium"
    catalog.save_test("known", catalog.fingerprint("known"), {"state": "tested", "checks": {"tools": {"state": "passed"}}})
    assert catalog.read_test("known")["state"] == "tested"
    catalog.put_model({**catalog.model("known").model_dump(), "contextWindow": 25000, "priceMultiplier": "2"})
    assert catalog.read_test("known")["checks"]["tools"]["state"] == "passed"
    catalog.put_model({**catalog.model("known").model_dump(), "contextWindow": 12345})
    catalog.update_profiles({"openai": {"models": {"gpt-5": {"cost": {"input": 2, "output": 8}}}}}, version="v1", refreshed=1)
    assert catalog.resolve("known")["contextWindow"] == 12345
    assert catalog.resolve("known")["price"]["input"] == "2"
    catalog.put_connection({**catalog.connection("relay").model_dump(), "baseUrl": "https://other.example/v1"})
    assert catalog.read_test("known")["state"] == "not_tested"
    with pytest.raises(ContractError, match="Invalid Agent settings"):
        catalog.put_connection({**catalog.connection("relay").model_dump(), "protocol": "google"})
    catalog.close()


def test_request_rejection_reports_provider_reason_without_credentials():
    import httpx
    import openai

    from modules.agent.backend.providers.errors import provider_error

    request = httpx.Request("POST", "https://api.deepseek.com/chat/completions",
                            headers={"Authorization": "Bearer private-test-key"})
    response = httpx.Response(400, request=request)
    error = openai.BadRequestError("rejected", response=response, body={"error": {
        "message": "messages[4]: tool_call_id missing; private-test-key", "type": "invalid_request_error"}})
    failure = provider_error(error)
    assert failure["code"] == "provider_request_rejected"
    assert "tool_call_id missing" in failure["message"]
    assert "private-test-key" not in failure["message"]


def test_all_provider_metadata_and_unknown_tool_capabilities_are_explicit(tmp_path, monkeypatch):
    import talyx_sdk.network as network

    import modules.agent.backend.providers.runtime as runtime_module
    monkeypatch.setattr(runtime_module, "system_http_proxy_url", lambda: None)
    monkeypatch.setattr(network, "system_http_proxy_url", lambda: None)
    with provider_server() as (base, requests):
        async def scenario():
            catalog = ProviderCatalog(tmp_path / "models.sqlite")
            ledger = UsageLedger(tmp_path / "usage.sqlite")
            catalog.put_connection({"id": "direct", "name": "DeepSeek", "providerId": "deepseek", "kind": "official",
                "protocol": "openai", "baseUrl": base + "/v1", "secretRef": "key"})
            catalog.update_profiles({"deepseek": {"models": {"known": {"limit": {"context": 128000}, "tool_call": True,
                "modalities": {"input": ["text"]}, "cost": {"input": .1, "output": .2}}}}}, version="test", refreshed=1)
            assert catalog.put_model({"id": "known", "connectionId": "direct", "model": "known"})["contextWindow"] == 128000
            catalog.put_model({"id": "unknown", "connectionId": "direct", "model": "unknown", "contextWindow": 8192})
            async def secret(_ref):
                return "unit-test-key"
            runtime = ProviderRuntime(catalog, ledger, secret)
            with pytest.raises(ContractError, match="Tool support is unknown"):
                async with runtime.model("unknown"):
                    pytest.fail("Unknown tools must not reach the provider")
            tested = await ProviderService(catalog, runtime).test_connection("unknown")
            assert tested["checks"]["tools"]["state"] == "passed" and tested["checks"]["search"]["state"] == "unsupported"
            assert len(requests) == 1 and requests[0][0] == "/v1/chat/completions"
            async with runtime.model("unknown") as lease:
                await lease.invoke([HumanMessage(content="probe")], tools=[PROBE])
            catalog.credentials_changed("direct")
            with pytest.raises(ContractError, match="Tool support is unknown"):
                async with runtime.model("unknown"):
                    pytest.fail("Rotated credentials invalidate the old proof")
            catalog.close()
            ledger.close()
        asyncio.run(scenario())


def test_openai_responses_native_search_and_relay_probe(tmp_path, monkeypatch):
    import talyx_sdk.network as network

    import modules.agent.backend.providers.runtime as runtime_module
    monkeypatch.setattr(runtime_module, "system_http_proxy_url", lambda: None)
    monkeypatch.setattr(network, "system_http_proxy_url", lambda: None)
    with provider_server() as (base, requests):
        async def scenario():
            catalog = ProviderCatalog(tmp_path / "models.sqlite")
            ledger = UsageLedger(tmp_path / "usage.sqlite")
            for kind in ("official", "relay"):
                catalog.put_connection({"id": kind, "name": kind, "providerId": "openai", "kind": kind,
                    "protocol": "openai", "baseUrl": base + "/v1", "secretRef": "key"})
                catalog.put_model({"id": kind, "connectionId": kind, "model": "model", "contextWindow": 8192,
                    "tools": True, "searchMode": "provider" if kind == "official" else "talyx"})
            async def secret(_ref):
                return "unit-test-key"
            runtime = ProviderRuntime(catalog, ledger, secret)
            service = ProviderService(catalog, runtime)
            for kind in ("official", "relay"):
                result = await service.test_connection(kind)
                assert result["checks"]["connection"]["state"] == result["checks"]["tools"]["state"] == result["checks"]["search"]["state"] == "passed"
                assert result["checks"]["search"]["sources"][0]["url"] == "https://www.python.org"
            assert [request[0] for request in requests] == ["/v1/responses", "/v1/responses", "/v1/chat/completions", "/v1/responses"]
            searches = [json.loads(row[0]) for row in ledger.db.execute("SELECT search_units FROM requests")]
            assert sum(item["openai_web_search_call"] for item in searches if "openai_web_search_call" in item) == 2
            catalog.close()
            ledger.close()
        asyncio.run(scenario())


@pytest.mark.parametrize("protocol", ["openai", "anthropic", "google", "ollama"])
def test_native_http_integrations_tools_usage_and_owned_transport(tmp_path, monkeypatch, protocol):
    import talyx_sdk.network as network

    import modules.agent.backend.providers.runtime as runtime_module
    monkeypatch.setattr(runtime_module, "system_http_proxy_url", lambda: None)
    monkeypatch.setattr(network, "system_http_proxy_url", lambda: None)
    monkeypatch.setenv("HTTP_PROXY", "http://127.0.0.1:1")
    monkeypatch.setenv("HTTPS_PROXY", "http://127.0.0.1:1")
    monkeypatch.setenv("NO_PROXY", "")
    monkeypatch.setenv("GOOGLE_GENAI_USE_VERTEXAI", "true")
    with provider_server() as (base, requests):
        async def scenario():
            catalog = ProviderCatalog(tmp_path / "models.sqlite")
            ledger = UsageLedger(tmp_path / "usage.sqlite")
            catalog.put_connection({"id": "connection", "name": protocol, "providerId": protocol,
                "kind": "local" if protocol == "ollama" else "official" if protocol == "google" else "relay",
                "protocol": protocol, "baseUrl": base + ("/v1" if protocol == "openai" else ""),
                "secretRef": "key" if protocol != "ollama" else None})
            catalog.put_model({"id": "model", "connectionId": "connection", "model": "model", "contextWindow": 8192, "tools": True,
                "showPrice": False, "price": {"input": "2", "output": "8", "version": "test"}})
            async def secret(_ref):
                return "unit-test-key"
            runtime = ProviderRuntime(catalog, ledger, secret)
            async with runtime.model("model", output_reserve=128) as first:
                async with runtime.model("model", output_reserve=128) as second:
                    assert first.model is not second.model
                    response = await first.invoke([HumanMessage(content="probe")], tools=[PROBE])
                    assert response.tool_calls[0]["args"] == {"word": "talyx"}
                # Closing another lease must not close this lease's independent HTTP clients.
                response = await first.invoke([HumanMessage(content="again")], tools=[PROBE])
                assert response.usage_metadata["input_tokens"] == 12
            assert len(requests) == 2
            if protocol == "ollama":
                assert requests[0][1]["options"]["num_ctx"] == 8192
            summary = ledger.summary(group_by="model")["groups"][0]
            assert summary["requests"] == 2 and summary["inputTokens"] == 24 and summary["outputTokens"] == 8
            assert summary["amounts"] == {"USD": "0.000112"} and summary["unpricedRequests"] == 0
            catalog.close()
            ledger.close()
        asyncio.run(scenario())


def test_route_change_rejects_an_existing_model_and_local_services_are_finite(tmp_path, monkeypatch):
    import talyx_sdk.network as network

    import modules.agent.backend.providers.runtime as runtime_module
    monkeypatch.setattr(runtime_module, "system_http_proxy_url", lambda: None)
    monkeypatch.setattr(network, "system_http_proxy_url", lambda: None)
    async def scenario():
        catalog = ProviderCatalog(tmp_path / "models.sqlite")
        ledger = UsageLedger(tmp_path / "usage.sqlite")
        # A server on another machine follows the system route; one on this computer does not.
        catalog.put_connection({"id": "connection", "name": "lan", "providerId": "ollama", "kind": "local", "protocol": "ollama", "baseUrl": "http://192.0.2.1:1"})
        catalog.put_connection({"id": "loopback", "name": "local", "providerId": "ollama", "kind": "local", "protocol": "ollama", "baseUrl": "http://127.0.0.1:1"})
        catalog.put_model({"id": "model", "connectionId": "connection", "model": "model", "contextWindow": 8192, "tools": True})
        catalog.put_model({"id": "here", "connectionId": "loopback", "model": "model", "contextWindow": 8192, "tools": True})
        runtime = ProviderRuntime(catalog, ledger, lambda _ref: None)
        async with runtime.model("model") as lease, runtime.model("here") as here:
            monkeypatch.setattr(runtime_module, "system_http_proxy_url", lambda: "http://127.0.0.1:2")
            with pytest.raises(NetworkRouteChanged):
                await lease.model.ainvoke([HumanMessage(content="no request")])
            here.route.check()
        assert ledger.db.execute("SELECT COUNT(*) FROM requests").fetchone()[0] == 0
        service = ProviderService(catalog, runtime)
        assert (await service.discover("ollama", base_url="http://127.0.0.1:1"))["state"] == "unavailable"
        assert (await service.discover("lmstudio", base_url="http://127.0.0.1:1"))["state"] == "unavailable"
        ledger.close()
        catalog.close()
    asyncio.run(scenario())


def test_grounding_projection_retains_sources_and_segment_metadata():
    from langchain_core.messages import AIMessage
    message = AIMessage(content="A grounded answer", response_metadata={"model_provider": "google_genai",
        "grounding_metadata": {"grounding_chunks": [{"web": {"uri": "https://example.com/article", "title": "Article"}}],
            "grounding_supports": [{"segment": {"start_index": 0, "end_index": 8}, "grounding_chunk_indices": [0]}],
            "search_entry_point": {"rendered_content": "provider display data"}}})
    result = project_message(message)
    assert result["sources"][0]["url"] == "https://example.com/article"
    assert result["groundingMetadata"]["grounding_supports"][0]["segment"]["end_index"] == 8


def test_unexecuted_model_tool_arguments_are_not_search_sources():
    from langchain_core.messages import AIMessage
    message = AIMessage(content="", tool_calls=[{"id": "invented", "name": "read_page", "args": {"url": "https://example.com/invented"}}])
    assert project_message(message)["sources"] == []


@pytest.mark.parametrize("stream", [False, True])
def test_xai_responses_retains_actual_search_billing_and_attribution(tmp_path, monkeypatch, stream):
    import talyx_sdk.network as network

    import modules.agent.backend.providers.runtime as runtime_module
    from modules.agent.backend.usage import Price, UsageContext
    monkeypatch.setattr(runtime_module, "system_http_proxy_url", lambda: None)
    monkeypatch.setattr(network, "system_http_proxy_url", lambda: None)
    with provider_server() as (base, requests):
        async def scenario():
            catalog = ProviderCatalog(tmp_path / "models.sqlite")
            ledger = UsageLedger(tmp_path / "usage.sqlite")
            ledger.configure_group("g", "w", monthly_limit="1")
            catalog.put_connection({"id": "xai", "name": "xAI", "providerId": "xai", "kind": "official",
                                    "protocol": "openai", "baseUrl": base + "/v1", "secretRef": "key"})
            price = Price(input="1", output="1", version="test", search={"xai_web_search_call": "0.005"})
            catalog.put_model({"id": "grok", "connectionId": "xai", "model": "grok-test", "contextWindow": 8192,
                               "tools": True, "searchMode": "provider", "price": price.__dict__})
            async def secret(_):
                return "test-key"
            runtime = ProviderRuntime(catalog, ledger, secret)
            context = UsageContext(workspace_id="w", group_id="g", work_run_id="run", unattended=True)
            async with runtime.model("grok", usage_context=context, output_reserve=128) as lease:
                if stream:
                    model = lease.model.bind_tools(runtime.native_search_tools("grok"))
                    chunks = [chunk async for chunk in model.astream([HumanMessage(content="search")])]
                    message = chunks[0]
                    for chunk in chunks[1:]:
                        message += chunk
                else:
                    message = await lease.invoke([HumanMessage(content="search")], tools=runtime.native_search_tools("grok"))
            assert message.response_metadata["rawUsage"]["server_side_tool_usage_details"]["web_search_calls"] == 2
            row = ledger.db.execute("SELECT * FROM requests").fetchone()
            assert json.loads(row["search_units"]) == {"xai_web_search_call": 2}
            assert ledger.db.execute("SELECT work_run_id FROM request_context WHERE request_id=?", (row["id"],)).fetchone()[0] == "run"
            assert requests[0][0] == "/v1/responses"

            if not stream:
                assert project_message(message)["sources"][0]["url"] == "https://www.python.org"
            catalog.close()
            ledger.close()
        asyncio.run(scenario())


def test_official_sites_are_connection_identity(tmp_path):
    catalog = ProviderCatalog(tmp_path / "models.sqlite")
    connection = {"id": "kimi-cn", "name": "Kimi China", "kind": "official", "providerId": "moonshotai", "protocol": "openai",
                  "baseUrl": "https://api.moonshot.cn/v1", "secretRef": "cn-key", "site": "domestic"}
    catalog.put_connection(connection)
    with pytest.raises(ContractError, match="new connection"):
        catalog.put_connection({**connection, "site": "international", "baseUrl": "https://api.moonshot.ai/v1"})
    with pytest.raises(ContractError, match="Invalid Agent settings"):
        catalog.put_connection({**connection, "baseUrl": "https://api.moonshot.ai/v1"})
    catalog.close()


@pytest.mark.parametrize("retry", [False, True])
def test_google_search_is_one_independent_grounding_request(tmp_path, monkeypatch, retry):
    import talyx_sdk.network as network

    import modules.agent.backend.providers.runtime as runtime_module
    monkeypatch.setattr(runtime_module, "system_http_proxy_url", lambda: None)
    monkeypatch.setattr(network, "system_http_proxy_url", lambda: None)
    with provider_server((429, 200) if retry else ()) as (base, requests):
        async def scenario():
            catalog = ProviderCatalog(tmp_path / "models.sqlite")
            ledger = UsageLedger(tmp_path / "usage.sqlite")
            catalog.put_connection({"id": "google", "name": "Google", "providerId": "google", "kind": "official",
                "protocol": "google", "baseUrl": base, "secretRef": "key"})
            catalog.put_model({"id": "model", "connectionId": "google", "model": "gemini-3-pro-preview", "contextWindow": 8192,
                               "tools": True, "searchMode": "provider", "price": {"input": "1", "output": "1", "version": "test", "search": {"google_grounding_query": "0.001"}}})
            async def secret(_ref):
                return "unit-test-key"
            service = ProviderService(catalog, ProviderRuntime(catalog, ledger, secret))
            grounded = await service.provider_search("model", "query", "objective", usage_context=UsageContext(unattended=retry))
            assert grounded["sources"][0]["url"] == "https://example.com/article"
            assert len(requests) == (2 if retry else 1) and requests[0][1]["tools"] == [{"googleSearch": {}}]
            if retry:
                assert requests[0][:2] == requests[1][:2]
                assert [{key.lower(): value for key, value in item[2].items()}["x-goog-api-key"] for item in requests] == ["unit-test-key"] * 2
                assert [row[0] for row in ledger.db.execute("SELECT state FROM requests ORDER BY rowid")] == ["rejected", "final"]
            row = ledger.db.execute("SELECT search_units FROM requests WHERE state='final'").fetchone()
            assert json.loads(row[0]) == {"google_grounding_query": 2}
            catalog.close()
            ledger.close()
        asyncio.run(scenario())


@pytest.mark.parametrize("change_route", [False, True])
def test_inflight_cancel_and_quiet_route_change_mark_usage_unknown(tmp_path, monkeypatch, change_route):
    import talyx_sdk.network as network

    import modules.agent.backend.providers.runtime as runtime_module
    monkeypatch.setattr(runtime_module, "system_http_proxy_url", lambda: None)
    monkeypatch.setattr(network, "system_http_proxy_url", lambda: None)
    with provider_server() as (base, requests):
        async def scenario():
            catalog = ProviderCatalog(tmp_path / "models.sqlite")
            ledger = UsageLedger(tmp_path / "usage.sqlite")
            catalog.put_connection({"id": "relay", "name": "relay", "providerId": "openai", "kind": "relay",
                "protocol": "openai", "baseUrl": base + "/v1", "secretRef": "key"})
            catalog.put_model({"id": "model", "connectionId": "relay", "model": "model", "contextWindow": 8192, "tools": True})
            async def secret(_ref):
                return "unit-test-key"
            runtime = ProviderRuntime(catalog, ledger, secret)
            async with runtime.model("model") as lease:
                work = asyncio.create_task(lease.invoke([HumanMessage(content="delay")], tools=[PROBE]))
                async with asyncio.timeout(3):
                    while not requests:
                        await asyncio.sleep(.01)
                if change_route:
                    monkeypatch.setattr(network, "system_http_proxy_url", lambda: "http://127.0.0.1:1")
                    with pytest.raises(NetworkRouteChanged):
                        await work
                else:
                    work.cancel()
                    with pytest.raises(asyncio.CancelledError):
                        await work
            assert work.done()
            group = ledger.summary()["groups"][0]
            assert group["requests"] == 1 and group["unconfirmedRequests"] == 1
            catalog.close()
            ledger.close()
        asyncio.run(scenario())


def test_ollama_discovery_reads_window_and_abilities_from_its_real_response_shape(tmp_path):
    shown = {
        "qwen3:8b": {"model_info": {"general.architecture": "qwen3", "qwen3.context_length": 40960},
                     "capabilities": ["completion", "tools", "thinking"]},
        "llava:7b": {"model_info": {"llama.context_length": 4096}, "capabilities": ["completion", "vision"]},
    }

    class Ollama(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass

        def reply(self, value):
            body = json.dumps(value).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def do_GET(self):
            self.reply({"models": [{"name": name, "model": name, "size": 1} for name in shown]})

        def do_POST(self):
            name = json.loads(self.rfile.read(int(self.headers["Content-Length"])))["model"]
            self.reply({"modelfile": "", "parameters": "", "template": "", **shown[name]})

    server = ThreadingHTTPServer(("127.0.0.1", 0), Ollama)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    try:
        catalog = ProviderCatalog(tmp_path / "models.sqlite")
        ledger = UsageLedger(tmp_path / "usage.sqlite")
        service = ProviderService(catalog, ProviderRuntime(catalog, ledger, lambda _ref: None))
        found = asyncio.run(service.discover("ollama", base_url=f"http://127.0.0.1:{server.server_port}"))
        assert found["state"] == "available"
        assert found["models"] == [
            {"model": "qwen3:8b", "contextWindow": 40960, "tools": True, "images": False, "thinking": True},
            {"model": "llava:7b", "contextWindow": 4096, "tools": False, "images": True, "thinking": False}]
        catalog.close()
        ledger.close()
    finally:
        server.shutdown()


def test_an_empty_provider_balance_is_not_reported_as_a_network_problem():
    import httpx2
    import openai

    from modules.agent.backend.providers.errors import provider_error

    request = httpx2.Request("POST", "https://api.deepseek.com/chat/completions")
    empty = openai.APIStatusError("Insufficient Balance", response=httpx2.Response(402, request=request), body=None)
    assert provider_error(empty)["code"] == "insufficient_balance"
    down = openai.InternalServerError("Unavailable", response=httpx2.Response(503, request=request), body=None)
    assert provider_error(down) == {"state": "failed", "code": "provider_error", "message": "The provider returned an error (HTTP 503); try again later"}
