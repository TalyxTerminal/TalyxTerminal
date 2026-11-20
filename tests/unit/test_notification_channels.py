"""Persistent channel delivery, real local HTTP receipts and secret boundaries."""

import asyncio
import json
from pathlib import Path

import pytest
from aiohttp import web
from talyx_sdk import ContractError

from host.notifications import (
    ChannelSecrets,
    NotificationService,
    encoded,
    request_body,
    response_status,
    send_http,
)
from host.secrets import MemorySecrets, Secrets


class FakeLocalSecretStore(ChannelSecrets):
    """Channel credentials kept in memory (``values`` shows what is stored)."""

    def __init__(self):
        self.memory = MemorySecrets()
        self.values = self.memory.values
        super().__init__(Secrets(Path("fixture-root"), self.memory))


async def configure(service, kind):
    credentials = {"token": "123:fixture_token"} if kind == "telegram" else {
        "webhook": "https://open.feishu.cn/open-apis/bot/v2/hook/fixture-id", "secret": "fixture-signing-secret"
    } if kind == "feishu" else {"webhook": "https://discord.com/api/webhooks/123/fixture-token"}
    return await service.configure({"operation": "save", "channelId": None, "expectedRevision": None,
                              "kind": kind, "name": kind, "enabled": True,
                              "settings": {"chatId": "123456"} if kind == "telegram" else {},
                              "credentials": credentials})


async def terminal(service, producer, identity):
    async with asyncio.timeout(10):
        while True:
            result = await service.read_delivery(producer, identity)
            if all(item["status"] not in {"pending", "sending"} for item in result["deliveries"]):
                return result
            await asyncio.sleep(0.01)


def test_three_channel_payloads_round_trip_and_dedup(tmp_path, monkeypatch):
    async def run():
        received = []
        async def receive(request):
            value = await request.json()
            received.append(value)
            if "chat_id" in value:
                response = web.StreamResponse()
                await response.prepare(request)
                await response.write(b'{"ok":')
                await asyncio.sleep(0.02)
                await response.write_eof(b'true,"result":{"message_id":9}}')
                return response
            if "msg_type" in value:
                return web.json_response({"code": 0})
            return web.json_response({"id": "receipt"})
        app = web.Application()
        app.router.add_post("/send", receive)
        runner = web.AppRunner(app)
        await runner.setup()
        site = web.TCPSite(runner, "127.0.0.1", 0)
        await site.start()
        port = site._server.sockets[0].getsockname()[1]
        async def sender(url, payload):
            assert url.startswith("https://")
            return await send_http(f"http://127.0.0.1:{port}/send", payload)
        monkeypatch.setattr("host.notifications.system_http_proxy_url", lambda: None)
        monkeypatch.setattr("host.notifications.await_network_route", lambda operation, proxy: operation())
        secrets = FakeLocalSecretStore()
        service = NotificationService(tmp_path, secrets, sender=sender)
        try:
            for kind in ("telegram", "feishu", "discord"):
                await configure(service, kind)
            channels = await service.channels()
            assert all(item["credentialMask"] == "••••" and item["configured"] for item in channels)
            assert "fixture-token" not in json.dumps(channels)
            assert "fixture_token" not in (tmp_path / "notifications.sqlite").read_bytes().decode("utf-8", errors="ignore")
            args = {"notificationId": "analysis", "channelIds": [item["id"] for item in channels],
                    "title": "BTC research", "body": "行情😀" * 10_000, "navigation": {"workspaceId": "a"}}
            await service.send("agent", args)
            await service.send("agent", args)
            await service.start()
            result = await terminal(service, "agent", "analysis")
            assert len(received) == 3
            assert all(item["status"] == "sent" and item["truncated"] for item in result["deliveries"])
            telegram = next(value for value in received if "chat_id" in value)
            assert len(telegram["text"]) <= 4096 and "parse_mode" not in telegram
            discord = next(value for value in received if "allowed_mentions" in value)
            assert len(discord["content"]) <= 2000 and discord["allowed_mentions"] == {"parse": []}
            feishu = next(value for value in received if "msg_type" in value)
            assert len(encoded(feishu).encode("utf-8")) <= 20_000 and feishu["sign"]
            assert all("navigation" not in value for value in received)
            with pytest.raises(ContractError, match="different content"):
                await service.send("agent", {**args, "body": "changed"})
            with pytest.raises(ContractError, match="producer"):
                await service.read_delivery("chart", "analysis")
            with pytest.raises(ContractError, match="destinations"):
                await service.send("agent", {**args, "url": "https://example.com"})
            test = await service.test(channels[0]["id"])
            assert test["producer"] == "platform" and test["deliveries"][0]["status"] == "sent"
        finally:
            await service.close()
            await runner.cleanup()
    asyncio.run(run())


def test_unknown_survives_restart_and_channel_change_cancels_pending(tmp_path):
    async def run():
        calls = []
        async def uncertain(url, payload):
            calls.append(payload)
            raise TimeoutError
        secrets = FakeLocalSecretStore()
        service = NotificationService(tmp_path, secrets, sender=uncertain)
        channel, = await configure(service, "telegram")
        args = {"notificationId": "once", "channelIds": [channel["id"]], "title": "Done", "body": "Result"}
        await service.send("agent", args)
        await service.start()
        result = await terminal(service, "agent", "once")
        assert result["deliveries"][0]["status"] == "unknown"
        await service.close()
        restored = NotificationService(tmp_path, secrets, sender=uncertain)
        await restored.start()
        assert (await restored.channels())[0]["lastDelivery"]["status"] == "unknown"
        await restored.send("agent", args)
        await asyncio.sleep(0.03)
        assert len(calls) == 1
        await restored.send("agent", {**args, "notificationId": "pending"})
        await restored.configure({"operation": "save", "channelId": channel["id"], "expectedRevision": 1,
                            "kind": "telegram", "name": "telegram", "enabled": False, "settings": channel["settings"]})
        assert (await restored.read_delivery("agent", "pending"))["deliveries"][0]["status"] == "cancelled"
        with pytest.raises(ContractError, match="changed"):
            await restored.configure({"operation": "delete", "channelId": channel["id"], "expectedRevision": 1})
        await restored.configure({"operation": "delete", "channelId": channel["id"], "expectedRevision": 2})
        assert (await restored.channels()) == [] and secrets.values == {}
        await restored.close()
    asyncio.run(run())


def test_rate_limit_retries_in_same_delivery_and_crashed_claim_is_unknown(tmp_path):
    async def run():
        attempts = []
        async def retry(url, payload):
            attempts.append(payload)
            return (429, {"retry_after": 1}) if len(attempts) < 3 else (200, {"ok": True, "result": {"message_id": 1}})
        secrets = FakeLocalSecretStore()
        service = NotificationService(tmp_path, secrets, sender=retry)
        channel, = await configure(service, "telegram")
        args = {"notificationId": "retry", "channelIds": [channel["id"]], "title": "Result", "body": "Details"}
        await service.send("agent", args)
        await service.start()
        result = await terminal(service, "agent", "retry")
        assert result["deliveries"][0]["status"] == "sent" and result["deliveries"][0]["attempts"] == 3
        await service.send("agent", {**args, "notificationId": "crashed"})
        with service.db:
            service.db.execute("UPDATE deliveries SET status='sending' WHERE notification_id='crashed'")
        await service.close()
        restored = NotificationService(tmp_path, secrets, sender=retry)
        await restored.start()
        assert (await restored.read_delivery("agent", "crashed"))["deliveries"][0]["status"] == "unknown"
        assert len(attempts) == 3
        await restored.close()
    asyncio.run(run())


def test_provider_business_errors_and_official_destinations(tmp_path):
    assert response_status("feishu", 200, {"code": 19024})[0] == "failed"
    assert response_status("discord", 200, {})[0] == "unknown"
    assert response_status("telegram", 200, {"ok": False, "error_code": 403})[0] == "failed"
    assert response_status("telegram", 502, {})[0] == "unknown"
    service = NotificationService(tmp_path, FakeLocalSecretStore())
    with pytest.raises(ContractError, match="official"):
        asyncio.run(service.configure({"operation": "save", "channelId": None, "expectedRevision": None, "kind": "discord",
                           "name": "Bad", "enabled": True, "settings": {},
                           "credentials": {"webhook": "http://127.0.0.1:8000/secret"}}))
    url, payload, truncated = request_body("telegram", {"chatId": "1"}, {"token": "123:abc"}, "Title", "Details")
    assert url.endswith("/sendMessage") and payload["text"] == "Title\n\nDetails" and not truncated
    asyncio.run(service.close())


def test_stopping_a_sending_delivery_records_unknown_without_resending(tmp_path):
    async def run():
        started = asyncio.Event()
        async def sending(url, payload):
            started.set()
            await asyncio.Event().wait()
        secrets = FakeLocalSecretStore()
        service = NotificationService(tmp_path, secrets, sender=sending)
        channel, = await configure(service, "telegram")
        await service.send("agent", {"notificationId": "sending", "channelIds": [channel["id"]], "title": "Title", "body": "Body"})
        await service.start()
        await asyncio.wait_for(started.wait(), 2)
        await service.close()
        restarted = NotificationService(tmp_path, secrets, sender=sending)
        await restarted.start()
        assert (await restarted.read_delivery("agent", "sending"))["deliveries"][0]["status"] == "unknown"
        await restarted.close()
    asyncio.run(run())
