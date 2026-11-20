"""Bounded page extraction, redirect SSRF checks and single-source search failure semantics."""

import asyncio
import json
import socket
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import httpx
import pytest
from talyx_sdk import ContractError

from modules.agent.backend.providers import ProviderCatalog
from modules.agent.backend.providers.presets import OFFICIAL_SITES, SEARCH_ENDPOINTS
from modules.agent.backend.search import SearchService, WebReader
from modules.agent.backend.search.web import MAX_BYTES, public_target
from modules.agent.backend.usage import UsageLedger


def test_public_targets_reject_private_mixed_dns_and_unverifiable_tun(monkeypatch):
    async def scenario():
        for url in ("file:///C:/secret", "http://localhost/", "http://127.0.0.1/", "http://[::1]/", "https://example.com:8000/", "https://user:password@example.com/"):
            with pytest.raises(ContractError):
                await public_target(url)
        loop = asyncio.get_running_loop()
        addresses = ["8.8.8.8", "127.0.0.1"]
        async def resolve(host, port, **kwargs):
            return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", (address, port)) for address in addresses]
        monkeypatch.setattr(loop, "getaddrinfo", resolve)
        with pytest.raises(ContractError):
            await public_target("https://public.example/")
        addresses[:] = ["198.18.1.7"]
        with pytest.raises(ContractError) as tun:
            await public_target("https://public.example/")
        assert tun.value.code == "network_dns_unverifiable"
        addresses[:] = ["8.8.8.8"]
        pinned, hostname, host = await public_target("https://public.example/article")
        assert pinned == "https://8.8.8.8/article" and hostname == host == "public.example"
    asyncio.run(scenario())


def test_page_body_extraction_limits_and_redirect_targets(tmp_path, monkeypatch):
    import talyx_sdk.network as network

    import modules.agent.backend.search.web as web
    monkeypatch.setattr(web, "system_http_proxy_url", lambda: None)
    monkeypatch.setattr(network, "system_http_proxy_url", lambda: None)
    text = "This article explains reproducible market research, data quality and the decisions behind reliable results. " * 30
    page = ("<html><head><title>Market research</title></head><body><article><h1>Market research</h1><p>" + text + "</p></article></body></html>").encode()
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass
        def do_GET(self):
            if self.path == "/redirect":
                self.send_response(302)
                self.send_header("Location", "http://127.0.0.1/admin")
                self.end_headers()
                return
            body = b"x" * (MAX_BYTES + 1) if self.path == "/large" else page
            self.send_response(200)
            self.send_header("Content-Type", "text/plain" if self.path == "/large" else "text/html")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            try:
                self.wfile.write(body)
            except (BrokenPipeError, ConnectionResetError):
                pass
    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    observed = []
    async def resolve(url):
        observed.append(url)
        if url.startswith("http://public.example/"):
            return url.replace("public.example", f"127.0.0.1:{server.server_port}"), "127.0.0.1", "public.example"
        return await public_target(url)
    monkeypatch.setattr(web, "public_target", resolve)
    try:
        async def scenario():
            reader = WebReader()
            result = await reader.read("http://public.example/article")
            assert "reproducible market research" in result["text"] and result["title"] == "Market research"
            with pytest.raises(ContractError, match="size limit"):
                await reader.read("http://public.example/large")
            with pytest.raises(ContractError):
                await reader.read("http://public.example/redirect")
            assert observed[-1] == "http://127.0.0.1/admin"
        asyncio.run(scenario())
    finally:
        server.shutdown()
        server.server_close()
        thread.join()


def test_selected_search_never_falls_back_and_releases_rejected_usage(tmp_path, monkeypatch):
    async def scenario():
        ledger = UsageLedger(tmp_path / "usage.sqlite")
        async def secret(_ref):
            return "test-only-key"
        service = SearchService(tmp_path / "search.json", secret, ledger)
        service.configure(provider="tavily", secret_ref="search.key")
        exa_called = False
        async def exa(*args):
            nonlocal exa_called
            exa_called = True
        async def tavily(*args):
            raise ContractError("quota_exceeded", "Tavily quota reached")
        monkeypatch.setattr(service, "_exa", exa)
        monkeypatch.setattr(service, "_tavily", tavily)
        with pytest.raises(ContractError) as error:
            await service.search("query")
        assert error.value.code == "quota_exceeded" and not exa_called
        group = ledger.summary()["groups"][0]
        assert group["unconfirmedRequests"] == 0
        assert "test-only-key" not in json.dumps(service.settings())
        ledger.close()
    asyncio.run(scenario())


@pytest.mark.parametrize("provider,site", list(SEARCH_ENDPOINTS))
def test_official_search_uses_fixed_site_key_schema_dates_and_no_fallback(tmp_path, monkeypatch, provider, site):
    import modules.agent.backend.search.service as search_module
    from modules.agent.backend.usage import Price, UsageContext

    async def scenario():
        ledger = UsageLedger(tmp_path / "usage.sqlite")
        ledger.configure_group("group", "workspace", monthly_limit="1")
        catalog = ProviderCatalog(tmp_path / "models.sqlite")
        catalog.put_connection({"id": "selected", "name": "Selected", "kind": "official", "providerId": provider,
            "protocol": "openai", "baseUrl": OFFICIAL_SITES[(provider, site)], "site": site, "secretRef": "selected-key"})
        unit = SEARCH_ENDPOINTS[(provider, site)][1]
        price = Price(input="0", output="0", version="unit", search={unit: "0.01"}).__dict__
        catalog.put_model({"id": "model", "connectionId": "selected", "model": "model", "contextWindow": 1000, "tools": True, "price": price, "showPrice": False})
        async def secret(ref):
            assert ref == "selected-key"
            return "only-this-site-key"
        service = SearchService(tmp_path / "search.json", secret, ledger, catalog=catalog)
        requests, status = [], [200]
        def response(request):
            requests.append(request)
            assert str(request.url) == SEARCH_ENDPOINTS[(provider, site)][0]
            assert request.headers["authorization"] == "Bearer only-this-site-key"
            payload = json.loads(request.content)
            items = [{"title": "Dated", "url": "https://example.com/dated", "summary": "Text", "snippet": "Snippet", "date": "2026-10-25T08:00:00Z"},
                     {"title": "Unknown", "url": "https://example.com/unknown", "summary": "Text", "snippet": "Snippet", "date": None}]
            if provider == "deepseek":
                assert payload == {"query": "query", "count": 5, "need_content": False, "ori_question": "objective"}
                data = {"results": items}
            elif provider == "moonshotai":
                assert payload == {"text_query": "query\nobjective", "limit": 5, "timeout_seconds": 25}
                items[0].update(date="2026-10-25", chunks=[{"text": "Relevant passage", "score": 1}])
                data = {"search_results": items}
            else:
                assert payload["search_engine"] == ("search_pro" if site == "domestic" else "search-prime")
                data = {"created": 999, "search_result": [{"title": item["title"], "link": item["url"], "content": "Text", "publish_date": item["date"]} for item in items]}
            selected_status = status.pop(0) if len(status) > 1 else status[0]
            return httpx.Response(selected_status, headers={"retry-after": "0"}, json=data)
        client = httpx.AsyncClient
        monkeypatch.setattr(search_module.httpx, "AsyncClient", lambda **kwargs: client(transport=httpx.MockTransport(response)))
        context = UsageContext(workspace_id="workspace", group_id="group", unattended=True)
        result = await service.search("query", "objective", model_id="model", usage_context=context)
        assert result["provider"] == provider and result["site"] == site and result["query"] == "query"
        assert result["sources"][0]["datePrecision"] == ("date" if provider == "moonshotai" else "datetime")
        assert result["sources"][1]["publishedAt"] is None and result["sources"][1]["datePrecision"] is None
        assert ledger.group_status("group")["spent"] == "0.01"
        status[0] = 429
        with pytest.raises(ContractError) as failure:
            await service.search("query", "objective", model_id="model", usage_context=context)
        assert failure.value.code == "quota_exceeded" and len(requests) == 4

        status[:] = [429, 200]
        recovered = await service.search("query", "objective", model_id="model", usage_context=context)
        assert recovered["provider"] == provider and len(requests) == 6
        assert ledger.group_status("group")["spent"] == "0.02"

        catalog.close()
        ledger.close()
    asyncio.run(scenario())


def test_auto_search_does_not_send_relay_key_to_an_official_site(tmp_path):
    async def scenario():
        ledger = UsageLedger(tmp_path / "usage.sqlite")
        catalog = ProviderCatalog(tmp_path / "models.sqlite")
        catalog.put_connection({"id": "relay", "name": "Relay", "kind": "relay", "providerId": "deepseek", "protocol": "openai",
                                "baseUrl": "https://relay.example/v1", "secretRef": "relay-secret"})
        catalog.put_model({"id": "model", "connectionId": "relay", "model": "model", "contextWindow": 1000, "tools": True})
        async def secret(ref):
            pytest.fail("The relay credential must not be requested by official search selection")
        service = SearchService(tmp_path / "search.json", secret, ledger, catalog=catalog)
        assert (await service.select("model"))["provider"] == "exa"
        catalog.close()
        ledger.close()
    asyncio.run(scenario())
