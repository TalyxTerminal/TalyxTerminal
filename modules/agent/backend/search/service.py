"""Exactly one configured search provider; errors never select a different source."""

from __future__ import annotations

import hashlib
import json
import re
from datetime import UTC, date, datetime, timedelta
from uuid import uuid4

import httpx
from mcp import ClientSession
from mcp.client.streamable_http import streamable_http_client
from talyx_sdk import ContractError
from talyx_sdk.network import await_network_route, system_http_proxy_url

from ..providers.presets import OFFICIAL_SITES, SEARCH_ENDPOINTS
from ..providers.projection import safe_source
from ..providers.retry import retry_request
from ..usage import Price
from .web import WebReader


class SearchService:
    def __init__(self, path, get_secret, ledger, *, catalog=None):
        self.path, self.get_secret, self.ledger, self.catalog = path, get_secret, ledger, catalog
        self.reader = WebReader()
        if not path.exists():
            self.configure(provider="exa", secret_ref=None, mode="auto")

    def settings(self):
        return json.loads(self.path.read_text(encoding="utf-8"))

    def configure(self, *, provider, secret_ref=None, mode="fixed", connection_id=None, prices=None):
        if mode not in {"auto", "fixed"} or provider not in {"exa", "tavily", "deepseek", "moonshotai", "zhipuai"}:
            raise ContractError("invalid_input", "Choose automatic search or a supported fixed provider")
        if mode == "auto" and provider not in {"exa", "tavily"}:
            raise ContractError("invalid_input", "Automatic search requires Exa or Tavily as the configured alternate source")
        if provider == "tavily" and not secret_ref:
            raise ContractError("credential_missing", "Tavily requires a configured API key")
        if secret_ref is not None and not isinstance(secret_ref, str):
            raise ContractError("invalid_input", "Search credential reference must be a string")
        if provider not in {"exa", "tavily"}:
            if connection_id is None or self.catalog is None:
                raise ContractError("invalid_input", "Official search requires a configured model connection")
            connection = self.catalog.connection(connection_id)
            if self._official(connection) is None or connection.providerId != provider:
                raise ContractError("invalid_input", "Search must use the selected provider's official connection")
        prices = {} if prices is None else prices
        try:
            for value in prices.values():
                Price(**value)
        except (TypeError, ValueError, ArithmeticError) as error:
            raise ContractError("invalid_input", "Invalid search price") from error
        self.path.parent.mkdir(parents=True, exist_ok=True)
        temporary = self.path.with_suffix(".tmp")
        temporary.write_text(json.dumps({"mode": mode, "provider": provider, "secretRef": secret_ref,
            "connectionId": connection_id, "prices": prices}), encoding="utf-8")
        temporary.replace(self.path)
        return self.settings()

    @staticmethod
    def _official(connection):
        identity = (connection.providerId, connection.site)
        if connection.kind != "official" or identity not in OFFICIAL_SITES:
            return None
        base = connection.baseUrl.rstrip("/")
        expected = OFFICIAL_SITES[identity]
        if base != expected and not (connection.providerId == "deepseek" and base == expected + "/v1"):
            return None
        return identity

    async def select(self, model_id=None):
        settings = self.settings()
        connection = None
        resolved = None
        if settings["mode"] == "auto" and model_id is not None and self.catalog is not None:
            resolved = self.catalog.resolve(model_id, billing=True)
            candidate = self.catalog.connection(resolved["connectionId"])
            if self._official(candidate) is not None:
                key = await self.get_secret(candidate.secretRef)
                if key:
                    connection = candidate
        elif settings["provider"] not in {"exa", "tavily"}:
            connection = self.catalog.connection(settings["connectionId"])
            if self._official(connection) is None or connection.providerId != settings["provider"]:
                raise ContractError("conflict", "The selected search connection no longer matches its official site")
            key = await self.get_secret(connection.secretRef)
            if not key:
                raise ContractError("credential_missing", "Configure the selected search connection's API key")
        if connection is not None:
            provider, site = connection.providerId, connection.site
            price_key = provider + ":" + (site or "global")
            configured_price = settings["prices"][price_key] if price_key in settings["prices"] else resolved["price"] if resolved is not None else None
            return {"provider": provider, "site": site, "connectionId": connection.id, "key": key,
                    "price": Price(**configured_price) if configured_price is not None else None}
        provider = settings["provider"]
        key = await self.get_secret(settings["secretRef"]) if settings["secretRef"] is not None else None
        if provider == "tavily" and not key:
            raise ContractError("credential_missing", "Configure the Tavily API key")
        price_key = provider + ":global"
        return {"provider": provider, "site": None, "connectionId": "search:" + provider, "key": key,
                "price": Price(**settings["prices"][price_key]) if price_key in settings["prices"] else None}

    async def search(self, query, objective="", *, model_id=None, usage_context=None):
        if not query.strip() or len(query) > 4000 or len(objective) > 4000:
            raise ContractError("invalid_input", "Search requires a nonempty query of at most 4000 characters")
        selected = await self.select(model_id)
        if usage_context is not None and usage_context.unattended:
            return await retry_request(lambda: self._selected_search(selected, query, objective, usage_context))
        return await self._selected_search(selected, query, objective, usage_context)

    async def _selected_search(self, selected, query, objective, usage_context):
        provider, site = selected["provider"], selected["site"]
        payload = self._payload(provider, site, query, objective) if provider not in {"exa", "tavily"} else None
        unit = SEARCH_ENDPOINTS[(provider, site)][1] if payload is not None else "exa_search_request" if provider == "exa" else "tavily_credit"
        request_id = uuid4().hex
        self.ledger.start(request_id, selected["connectionId"], "search:" + provider, price=selected["price"],
                            search_units={unit: 1}, context=usage_context)
        proxy = system_http_proxy_url()
        settled = False
        try:
            if provider == "exa":
                result = await await_network_route(lambda: self._exa(query, objective, proxy), proxy)
            elif provider == "tavily":
                result = await await_network_route(lambda: self._tavily(query, objective, selected["key"], proxy), proxy)
            else:
                result = await await_network_route(lambda: self._official_search(provider, site, payload, selected["key"], proxy), proxy)
            result = {**result, "site": site, "query": query, "observedAt": datetime.now(UTC).isoformat()}
            result["truncated"] = len(result["text"]) > 20000
            result["text"] = result["text"][:20000]
            self.ledger.finish(request_id, {"input_tokens": 0, "output_tokens": 0}, search_units=result["billingUnits"])
            settled = True
            return result
        except ContractError as error:
            if error.code in {"quota_exceeded", "credential_invalid"}:
                self.ledger.reject(request_id)
                settled = True
            raise
        finally:
            if not settled:
                self.ledger.unknown(request_id)

    @staticmethod
    def _payload(provider, site, query, objective):
        if provider == "deepseek":
            if len(query) > 128 or len(objective) > 128:
                raise ContractError("invalid_input", "DeepSeek search query and objective each allow at most 128 characters")
            return {"query": query, "count": 5, "need_content": False, **({"ori_question": objective} if objective else {})}
        text = query + ("\n" + objective if objective else "")
        if provider == "moonshotai":
            return {"text_query": text, "limit": 5, "timeout_seconds": 25}
        if site == "domestic" and len(text) > 70:
            raise ContractError("invalid_input", "Zhipu domestic search allows at most 70 query characters")
        return {"search_query": text, "search_engine": "search_pro" if site == "domestic" else "search-prime", "count": 5,
                **({"search_intent": False} if site == "domestic" else {})}

    async def _official_search(self, provider, site, payload, key, proxy):
        endpoint, unit = SEARCH_ENDPOINTS[(provider, site)]
        try:
            async with httpx.AsyncClient(proxy=proxy, trust_env=False, timeout=30) as client:
                async with client.stream("POST", endpoint, headers={"Authorization": "Bearer " + key}, json=payload) as response:
                    if response.status_code in {401, 403}:
                        raise ContractError("credential_invalid", "The selected search site rejected its configured key")
                    if response.status_code == 429:
                        raise ContractError("quota_exceeded", "The selected search site's quota or rate limit was reached") from httpx.HTTPStatusError(
                            "Search request rate limited", request=response.request, response=response)
                    response.raise_for_status()
                    body = bytearray()
                    async for chunk in response.aiter_bytes():
                        body.extend(chunk)
                        if len(body) > 2 * 1024 * 1024:
                            raise ContractError("invalid_output", "Search response exceeds the download limit")
                data = json.loads(body)
            items = data["results"] if provider == "deepseek" else data["search_results"] if provider == "moonshotai" else data["search_result"]
            sources, text = [], []
            for item in items:
                url = item["link"] if provider == "zhipuai" else item["url"]
                if not safe_source(url):
                    continue
                if provider == "deepseek":
                    content, published = item["summary"] or item["snippet"], item["date"]
                elif provider == "moonshotai":
                    content = "\n".join(chunk["text"] for chunk in item["chunks"]) if "chunks" in item else item["snippet"]
                    published = item["date"] if "date" in item else None
                else:
                    content, published = item["content"], item["publish_date"] if "publish_date" in item else None
                sources.append(self.source(url, item["title"], published))
                text.append(item["title"] + "\n" + url + "\n" + content)
            return {"provider": provider, "text": "\n\n".join(text), "sources": sources, "billingUnits": {unit: 1}}
        except (httpx.HTTPError, ValueError, KeyError, TypeError) as error:
            raise ContractError("search_failed", "The selected search site returned an invalid response or could not be reached") from error

    async def _exa(self, query, objective, proxy):
        async with httpx.AsyncClient(proxy=proxy, trust_env=False, timeout=30) as client:
            async with streamable_http_client("https://mcp.exa.ai/mcp", http_client=client) as (read, write, _):
                async with ClientSession(read, write, read_timeout_seconds=timedelta(seconds=25)) as session:
                    await session.initialize()
                    tools = await session.list_tools()
                    tool = next((item for item in tools.tools if item.name == "web_search_exa"), None)
                    if tool is None:
                        raise ContractError("unavailable", "Exa's web search tool is not available")
                    arguments = {"query": query + ("\n" + objective if objective else "")}
                    if "numResults" in tool.inputSchema["properties"]:
                        arguments["numResults"] = 5
                    if "contextMaxCharacters" in tool.inputSchema["properties"]:
                        arguments["contextMaxCharacters"] = 12000
                    response = await session.call_tool(tool.name, arguments)
                    if response.isError:
                        raise ContractError("search_failed", "Exa search failed or its free quota was exceeded; choose Tavily in Search settings to change provider")
                    text = "\n".join(block.text for block in response.content if block.type == "text")
                    urls = list(dict.fromkeys(url.rstrip(".,);]") for url in re.findall(r"https?://[^\s<>\"']+", text)))
                    return {"provider": "exa", "text": text, "sources": [self.source(url, url) for url in urls if safe_source(url)],
                            "billingUnits": {"exa_search_request": 1}}

    async def _tavily(self, query, objective, key, proxy):
        try:
            async with httpx.AsyncClient(proxy=proxy, trust_env=False, timeout=30) as client:
                response = await client.post("https://api.tavily.com/search", headers={"Authorization": "Bearer " + key},
                    json={"query": query + ("\n" + objective if objective else ""), "max_results": 5,
                          "search_depth": "basic", "include_raw_content": False})
                if response.status_code in {401, 403}:
                    raise ContractError("credential_invalid", "Tavily rejected the configured API key")
                if response.status_code == 429:
                    raise ContractError("quota_exceeded", "Tavily quota reached; change the key or wait for the quota reset") from httpx.HTTPStatusError(
                        "Search request rate limited", request=response.request, response=response)
                response.raise_for_status()
                data = response.json()
            results = data["results"]
            return {"provider": "tavily", "text": "\n\n".join(item["title"] + "\n" + item["content"] for item in results),
                    "sources": [self.source(item["url"], item["title"]) for item in results if safe_source(item["url"])],
                    "billingUnits": {"tavily_credit": 1}}
        except (httpx.HTTPError, ValueError, KeyError) as error:
            raise ContractError("search_failed", "Tavily search failed; check Search settings and the network") from error

    @staticmethod
    def source(url, title, published=None):
        parsed, precision = None, None
        if isinstance(published, str) and published:
            try:
                if re.fullmatch(r"\d{4}-\d{2}-\d{2}", published):
                    parsed, precision = date.fromisoformat(published).isoformat(), "date"
                else:
                    value = datetime.fromisoformat(published.replace("Z", "+00:00"))
                    if value.tzinfo is not None:
                        parsed, precision = value.isoformat(), "datetime"
            except ValueError:
                pass
        return {"id": "source_" + hashlib.sha256(url.encode()).hexdigest()[:24], "url": url, "title": title,
                "publishedAt": parsed, "datePrecision": precision, "publishedRaw": published}

    async def read_page(self, url):
        return await self.reader.read(url)
