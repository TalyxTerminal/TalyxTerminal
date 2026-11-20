"""Anonymous bounded HTTP(S) reading, with public DNS targets pinned before each hop."""

from __future__ import annotations

import asyncio
import ipaddress
import socket
from urllib.parse import urljoin, urlsplit

import aiohttp
import trafilatura
from talyx_sdk import ContractError
from talyx_sdk.network import await_network_route, system_http_proxy_url
from yarl import URL

MAX_BYTES = 2 * 1024 * 1024


async def public_target(url):
    try:
        parsed = urlsplit(url)
        if (parsed.scheme not in {"http", "https"} or not parsed.hostname or parsed.username is not None
                or parsed.password is not None or parsed.port not in {None, 80, 443}):
            raise ValueError("Only public HTTP(S) pages on standard ports can be read")
        addresses = await asyncio.get_running_loop().getaddrinfo(parsed.hostname, parsed.port or (443 if parsed.scheme == "https" else 80), type=socket.SOCK_STREAM)
        ips = [ipaddress.ip_address(item[4][0]) for item in addresses]
        if any(address in ipaddress.ip_network("198.18.0.0/15") for address in ips if address.version == 4):
            raise ContractError("network_dns_unverifiable", "The active network returns reserved DNS addresses; configure real DNS in the TUN to verify public web targets")
        if not ips or any(not address.is_global for address in ips):
            raise ValueError("Web reading cannot access local, private or reserved services")
        return str(URL(url).with_host(str(ips[0]))), parsed.hostname, parsed.netloc
    except (ValueError, OSError) as error:
        raise ContractError("invalid_input", "The page must resolve to a public HTTP(S) target", {"field": "url"}) from error


class WebReader:
    async def read(self, url):
        proxy = system_http_proxy_url()
        async def fetch():
            current = url
            async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=30),
                    trust_env=False, cookie_jar=aiohttp.DummyCookieJar()) as client:
                for hop in range(6):
                    pinned, hostname, host = await public_target(current)
                    async with client.get(pinned, headers={"Host": host, "User-Agent": "TALYX-WebReader/1.0"},
                            proxy=proxy, server_hostname=hostname if urlsplit(current).scheme == "https" else None,
                            allow_redirects=False) as response:
                        if response.status in {301, 302, 303, 307, 308}:
                            if hop == 5 or "Location" not in response.headers:
                                raise ContractError("unavailable", "Page exceeded the redirect limit or omitted its redirect target")
                            current = urljoin(current, response.headers["Location"])
                            continue
                        if response.status >= 400:
                            raise ContractError("unavailable", "The page could not be fetched", {"status": response.status})
                        content_type = response.content_type
                        if content_type not in {"text/html", "application/xhtml+xml", "text/plain"}:
                            raise ContractError("unsupported", "Web reading requires an HTML or text response", {"contentType": content_type})
                        body = bytearray()
                        async for chunk in response.content.iter_chunked(65536):
                            body.extend(chunk)
                            if len(body) > MAX_BYTES:
                                raise ContractError("invalid_output", "Page exceeds the web-reading size limit")
                        if content_type == "text/plain":
                            text = body.decode(response.charset or "utf-8")
                            title = hostname
                        else:
                            extracted = await asyncio.to_thread(trafilatura.bare_extraction, bytes(body), url=current,
                                include_comments=False, include_tables=True, favor_precision=True, with_metadata=True)
                            if extracted is None or not extracted.text:
                                raise ContractError("unavailable", "No readable page body; the page may require login or JavaScript")
                            text, title = extracted.text, extracted.title or hostname
                        return {"url": current, "title": title, "text": text,
                                "summary": text[:1600], "characters": len(text)}
            raise ContractError("unavailable", "The page did not produce a readable response")
        try:
            return await await_network_route(fetch, proxy)
        except (aiohttp.ClientError, TimeoutError, UnicodeError) as error:
            raise ContractError("network_error", "Page reading failed; check the page and network") from error
