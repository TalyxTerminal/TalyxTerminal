"""Notifications: the user's push channels with durable, idempotent deliveries, and Windows toasts."""

from __future__ import annotations

import asyncio
import base64
import hashlib
import hmac
import importlib
import json
import re
import sqlite3
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime
from pathlib import Path
from urllib.parse import urlsplit
from uuid import uuid4

import aiohttp
from talyx_sdk.errors import ContractError
from talyx_sdk.network import (
    NetworkConfigurationError,
    NetworkRouteChanged,
    await_network_route,
    system_client_session,
    system_http_proxy_url,
)

__all__ = ["ChannelSecrets", "Toasts", "NotificationService"]
NOTE = "\n\n[Full result in TALYX]"
TERMINAL = {"sent", "failed", "unknown", "cancelled"}


def now():
    return datetime.now(UTC).isoformat()


def encoded(value):
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"), allow_nan=False)


def clipped(text, limit):
    return (text, False) if len(text) <= limit else (text[:limit - len(NOTE)] + NOTE, True)


def validate_channel(kind, settings, credentials):
    if kind == "telegram":
        if set(credentials) != {"token"} or not re.fullmatch(r"[0-9]+:[A-Za-z0-9_-]+", credentials["token"]):
            raise ContractError("invalid_input", "Enter a Telegram bot token")
        if set(settings) != {"chatId"} or not isinstance(settings["chatId"], str) or not settings["chatId"].strip():
            raise ContractError("invalid_input", "Telegram requires a chat ID")
        return
    if kind not in {"feishu", "discord"} or settings:
        raise ContractError("invalid_input", "Unsupported notification channel or settings")
    if set(credentials) - ({"webhook", "secret"} if kind == "feishu" else {"webhook"}) or "webhook" not in credentials:
        raise ContractError("invalid_input", "Enter the channel webhook")
    url = urlsplit(credentials["webhook"])
    valid = url.scheme == "https" and not url.username and not url.password and not url.query and not url.fragment
    if kind == "feishu":
        valid = valid and url.netloc == "open.feishu.cn" and re.fullmatch(r"/open-apis/bot/v2/hook/[A-Za-z0-9-]+", url.path)
    else:
        valid = valid and url.netloc == "discord.com" and re.fullmatch(r"/api(?:/v[0-9]+)?/webhooks/[0-9]+/[A-Za-z0-9_.-]+", url.path)
    if not valid:
        raise ContractError("invalid_input", "Use the official channel webhook URL without query parameters")


def request_body(kind, settings, credentials, title, body):
    text = title + "\n\n" + body
    if kind == "telegram":
        text, truncated = clipped(text, 4096)
        return "https://api.telegram.org/bot" + credentials["token"] + "/sendMessage", {
            "chat_id": settings["chatId"], "text": text,
        }, truncated
    if kind == "discord":
        text, truncated = clipped(text, 2000)
        return credentials["webhook"] + "?wait=true", {"content": text, "allowed_mentions": {"parse": []}}, truncated
    payload = {"msg_type": "text", "content": {"text": text}}
    if "secret" in credentials and credentials["secret"]:
        timestamp = str(int(time.time()))
        signature = hmac.new((timestamp + "\n" + credentials["secret"]).encode(), b"", hashlib.sha256).digest()
        payload.update(timestamp=timestamp, sign=base64.b64encode(signature).decode())
    truncated = len(encoded(payload).encode("utf-8")) > 20_000
    if truncated:
        low, high = 0, len(text)
        while low < high:
            middle = (low + high + 1) // 2
            payload["content"]["text"] = text[:middle] + NOTE
            if len(encoded(payload).encode("utf-8")) <= 20_000:
                low = middle
            else:
                high = middle - 1
        payload["content"]["text"] = text[:low] + NOTE
    return credentials["webhook"], payload, truncated


async def send_http(url, payload):
    proxy = system_http_proxy_url()

    async def post():
        async with system_client_session(timeout=aiohttp.ClientTimeout(total=20)) as session:
            async with session.post(url, data=encoded(payload).encode("utf-8"),
                                    headers={"Content-Type": "application/json"}, proxy=proxy,
                                    allow_redirects=False) as response:
                try:
                    await response.content.readexactly(65_537)
                except asyncio.IncompleteReadError as exc:
                    raw = exc.partial
                else:
                    return response.status, None
                try:
                    result = json.loads(raw)
                except (ValueError, UnicodeError):
                    result = None
                return response.status, result

    return await await_network_route(post, proxy)


def response_status(kind, status, result):
    if status == 429:
        return "retry", "The channel rate limit was reached"
    if not 200 <= status < 300:
        return ("unknown" if status >= 500 else "failed"), f"Channel returned HTTP {status}"
    if not isinstance(result, dict):
        return "unknown", "The channel returned no verifiable delivery receipt"
    if kind == "telegram":
        if result.get("ok") is True and isinstance(result.get("result"), dict) and "message_id" in result["result"]:
            return "sent", None
        code = result.get("error_code")
        if type(code) is not int:
            return "unknown", "Telegram did not return a message receipt"
        return ("retry" if code == 429 else "failed"), f"Telegram rejected the message (code {code})"
    if kind == "feishu":
        code = result.get("code")
        if type(code) is not int:
            return "unknown", "Feishu did not return a message receipt"
        if code == 0:
            return "sent", None
        return ("retry" if code == 11232 else "failed"), f"Feishu rejected the message (code {code})"
    if isinstance(result.get("id"), str):
        return "sent", None
    return "unknown", "Discord did not return a message receipt"


class Toasts:
    """Windows toasts; ``activate`` runs (on another thread) when the user clicks one."""

    def __init__(self) -> None:
        self.toaster = self.kind = None
        self.reason = "Windows toasts are not available on this system"
        if sys.platform == "win32":
            try:
                module = importlib.import_module("windows_toasts")
                self.kind, self.toaster = module.Toast, module.WindowsToaster("TALYX")
            except ImportError:
                self.reason = "The Windows-Toasts package is not installed"
            except Exception as exc:  # noqa: BLE001 - the WinRT runtime itself may be missing
                self.reason = f"Windows toasts are unavailable: {type(exc).__name__}"

    async def show(self, title: str, message: str, *, activate=None) -> None:
        if self.toaster is None:
            raise ContractError("unavailable", self.reason)

        def deliver():
            toast = self.kind()
            toast.text_fields = [title, message]
            if activate is not None:
                toast.on_activated = lambda _event: activate()
            self.toaster.show_toast(toast)

        await asyncio.to_thread(deliver)


class ChannelSecrets:
    """Channel credentials kept as the platform's own secrets."""

    def __init__(self, secrets):
        self.secrets = secrets

    def exists(self, ref):
        return self.secrets.read("platform", ref) is not None

    def read(self, ref):
        value = self.secrets.read("platform", ref)
        if value is None:
            raise ContractError("secret_store_failed", "The channel's credentials are missing; enter them again")
        return value

    def write(self, ref, value):
        self.secrets.write("platform", ref, value)

    def delete(self, ref):
        self.secrets.write("platform", ref, None)


class NotificationService:
    """The database and the credential store are reached on one thread of their own, never on the event loop."""

    def __init__(self, root, secret_store, *, sender=send_http):
        self.root = Path(root)
        self.root.mkdir(parents=True, exist_ok=True)
        self.secrets, self.sender = secret_store, sender
        self.db = sqlite3.connect(self.root / "notifications.sqlite", check_same_thread=False)
        self.db.row_factory = sqlite3.Row
        self.db.executescript("""
            PRAGMA journal_mode=WAL;
            CREATE TABLE IF NOT EXISTS channels (
                id TEXT PRIMARY KEY, kind TEXT NOT NULL, name TEXT NOT NULL, enabled INTEGER NOT NULL,
                revision INTEGER NOT NULL, settings TEXT NOT NULL, secret_ref TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS notifications (
                producer TEXT NOT NULL, id TEXT NOT NULL, body TEXT NOT NULL,
                PRIMARY KEY(producer,id));
            CREATE TABLE IF NOT EXISTS deliveries (
                producer TEXT NOT NULL, notification_id TEXT NOT NULL, channel_id TEXT NOT NULL,
                channel_revision INTEGER NOT NULL, status TEXT NOT NULL, attempts INTEGER NOT NULL,
                created_at TEXT NOT NULL, updated_at TEXT NOT NULL, error TEXT, truncated INTEGER NOT NULL,
                PRIMARY KEY(producer,notification_id,channel_id));
        """)
        self.changed = asyncio.Event()      # a delivery is waiting to be sent
        self.progressed = asyncio.Event()   # a delivery's status changed
        self.thread = ThreadPoolExecutor(1, thread_name_prefix="notifications")
        self.worker = None
        self.closed = False

    async def _io(self, function, *args):
        return await asyncio.get_running_loop().run_in_executor(self.thread, function, *args)

    async def channels(self):
        return await self._io(self._channels)

    async def configure(self, args):
        return await self._io(self._configure, args)

    async def send(self, producer, args):
        result = await self._io(self._send, producer, args)
        self.changed.set()
        return result

    async def read_delivery(self, producer, notification_id):
        return await self._io(self._read_delivery, producer, notification_id)

    def _channels(self):
        result = []
        for row in self.db.execute("SELECT * FROM channels ORDER BY name,id"):
            configured = self.secrets.exists(row["secret_ref"])
            last = self.db.execute("SELECT * FROM deliveries WHERE channel_id=? ORDER BY updated_at DESC LIMIT 1", (row["id"],)).fetchone()
            result.append({"id": row["id"], "kind": row["kind"], "name": row["name"], "enabled": bool(row["enabled"]),
                           "revision": row["revision"], "settings": json.loads(row["settings"]),
                           "configured": configured, "credentialMask": "••••" if configured else None,
                           "lastDelivery": {"producer": last["producer"], "notificationId": last["notification_id"],
                                            "status": last["status"], "error": last["error"], "updatedAt": last["updated_at"]} if last else None})
        return result

    def _configure(self, args):
        channel_id = args["channelId"]
        current = self.db.execute("SELECT * FROM channels WHERE id=?", (channel_id,)).fetchone() if channel_id else None
        if args["expectedRevision"] != (current["revision"] if current else None):
            raise ContractError("conflict", "Notification channel changed")
        if channel_id is not None and current is None:
            raise ContractError("not_found", "Notification channel was deleted")
        if args["operation"] == "delete":
            if current is None:
                raise ContractError("not_found", "Notification channel does not exist")
            with self.db:
                self._cancel_pending(channel_id)
                self.db.execute("DELETE FROM channels WHERE id=?", (channel_id,))
            self.secrets.delete(current["secret_ref"])
            return self._channels()
        if args["operation"] != "save":
            raise ContractError("invalid_input", "Unknown channel configuration operation")
        if not args["name"].strip():
            raise ContractError("invalid_input", "Channel name is required")
        if "credentials" in args:
            credentials = args["credentials"]
        elif current is not None:
            credentials = json.loads(self.secrets.read(current["secret_ref"]))
        else:
            raise ContractError("invalid_input", "Channel credentials are required")
        validate_channel(args["kind"], args["settings"], credentials)
        channel_id = channel_id or uuid4().hex
        revision = current["revision"] + 1 if current else 1
        secret_ref = f"notifications/{channel_id}/{revision}"
        self.secrets.write(secret_ref, encoded(credentials))
        with self.db:
            self._cancel_pending(channel_id)
            self.db.execute("INSERT OR REPLACE INTO channels VALUES (?,?,?,?,?,?,?)",
                            (channel_id, args["kind"], args["name"], int(args["enabled"]), revision,
                             encoded(args["settings"]), secret_ref))
        if current is not None:
            self.secrets.delete(current["secret_ref"])
        return self._channels()

    def _cancel_pending(self, channel_id):
        self.db.execute("UPDATE deliveries SET status='cancelled',updated_at=?,error=? WHERE channel_id=? AND status='pending'",
                        (now(), "Channel configuration changed", channel_id))

    def _send(self, producer, args):
        if set(args) - {"notificationId", "channelIds", "title", "body", "navigation"}:
            raise ContractError("invalid_input", "Notifications accept configured channel IDs, not destinations")
        ids = args["channelIds"]
        if not ids or len(ids) != len(set(ids)):
            raise ContractError("invalid_input", "Choose distinct configured notification channels")
        payload = encoded(args)
        previous = self.db.execute("SELECT body FROM notifications WHERE producer=? AND id=?",
                                   (producer, args["notificationId"])).fetchone()
        if previous is not None:
            if json.loads(previous["body"]) != args:
                raise ContractError("conflict", "Notification ID already belongs to different content")
            return self._read_delivery(producer, args["notificationId"])
        channels = []
        for channel_id in ids:
            channel = self.db.execute("SELECT * FROM channels WHERE id=?", (channel_id,)).fetchone()
            if channel is None or not channel["enabled"] or not self.secrets.exists(channel["secret_ref"]):
                raise ContractError("unavailable", "A selected notification channel is unavailable", {"channelId": channel_id})
            channels.append(channel)
        timestamp = now()
        with self.db:
            self.db.execute("INSERT INTO notifications VALUES (?,?,?)", (producer, args["notificationId"], payload))
            self.db.executemany("INSERT INTO deliveries VALUES (?,?,?,?,'pending',0,?,?,NULL,0)",
                               [(producer, args["notificationId"], item["id"], item["revision"], timestamp, timestamp) for item in channels])
        return self._read_delivery(producer, args["notificationId"])

    def _read_delivery(self, producer, notification_id):
        rows = list(self.db.execute("SELECT * FROM deliveries WHERE producer=? AND notification_id=? ORDER BY channel_id",
                                    (producer, notification_id)))
        if not rows:
            raise ContractError("not_found", "Notification does not belong to this producer")
        return {"producer": producer, "notificationId": notification_id, "deliveries": [
            {"channelId": row["channel_id"], "status": row["status"], "attempts": row["attempts"],
             "createdAt": row["created_at"], "updatedAt": row["updated_at"], "error": row["error"],
             "truncated": bool(row["truncated"])} for row in rows]}

    async def test(self, channel_id):
        """Send a test message and answer once its delivery ended, or as it stands after 25 s."""
        identity = uuid4().hex
        await self.send("platform", {"notificationId": identity, "channelIds": [channel_id],
                                     "title": "TALYX test notification",
                                     "body": "Your notification channel is connected. Results will be delivered here."})
        try:
            async with asyncio.timeout(25):
                while True:
                    self.progressed.clear()
                    result = await self.read_delivery("platform", identity)
                    if all(item["status"] in TERMINAL for item in result["deliveries"]):
                        return result
                    await self.progressed.wait()
        except TimeoutError:
            return await self.read_delivery("platform", identity)

    async def start(self):
        def unknown():
            with self.db:
                self.db.execute("UPDATE deliveries SET status='unknown',updated_at=?,error=? WHERE status='sending'",
                                (now(), "Host stopped before a delivery receipt was recorded"))
        await self._io(unknown)
        self.worker = asyncio.create_task(self._run(), name="notification-delivery")

    async def close(self):
        self.closed = True
        if self.worker is not None:
            self.worker.cancel()
            try:
                await self.worker
            except asyncio.CancelledError:
                pass
        await self._io(self.db.close)
        self.thread.shutdown()

    async def _run(self):
        while not self.closed:
            self.changed.clear()
            rows = await self._io(lambda: list(self.db.execute(
                "SELECT * FROM deliveries WHERE status='pending' ORDER BY created_at LIMIT 4")))
            if not rows:
                await self.changed.wait()
                continue
            await asyncio.gather(*(self._deliver(row) for row in rows))

    async def _status(self, key, status, error=None, *, truncated=False):
        def write():
            with self.db:
                self.db.execute("UPDATE deliveries SET status=?,updated_at=?,error=?,truncated=? WHERE producer=? AND notification_id=? AND channel_id=?",
                                (status, now(), error, int(truncated), *key))
        await self._io(write)
        self.progressed.set()

    def _prepare(self, key, row, attempt):
        """What to send for one attempt (and the delivery marked as sending), or None when the channel changed."""
        channel = self.db.execute("SELECT * FROM channels WHERE id=?", (row["channel_id"],)).fetchone()
        if channel is None or not channel["enabled"] or channel["revision"] != row["channel_revision"]:
            return None
        message = json.loads(self.db.execute("SELECT body FROM notifications WHERE producer=? AND id=?", key[:2]).fetchone()["body"])
        credentials = json.loads(self.secrets.read(channel["secret_ref"]))
        url, payload, truncated = request_body(channel["kind"], json.loads(channel["settings"]), credentials,
                                               message["title"], message["body"])
        with self.db:
            self.db.execute("UPDATE deliveries SET status='sending',attempts=?,updated_at=?,truncated=? WHERE producer=? AND notification_id=? AND channel_id=?",
                            (attempt, now(), int(truncated), *key))
        return channel["kind"], url, payload, truncated

    async def _deliver(self, row):
        key = (row["producer"], row["notification_id"], row["channel_id"])
        truncated = False
        try:
            for attempt in range(row["attempts"] + 1, 4):
                prepared = await self._io(self._prepare, key, row, attempt)
                if prepared is None:
                    await self._status(key, "cancelled", "Channel configuration changed")
                    return
                kind, url, payload, truncated = prepared
                try:
                    status, result = await self.sender(url, payload)
                    state, error = response_status(kind, status, result)
                except aiohttp.ClientConnectorError:
                    state, error = "retry", "The channel could not be reached before sending"
                except (TimeoutError, aiohttp.ClientConnectionError, aiohttp.ClientPayloadError, NetworkRouteChanged):
                    state, error = "unknown", "Delivery may have reached the channel; no receipt was confirmed"
                if state != "retry" or attempt == 3:
                    await self._status(key, "failed" if state == "retry" else state, error, truncated=truncated)
                    return
                await self._status(key, "pending", error, truncated=truncated)
                await asyncio.sleep(attempt)
        except (ContractError, NetworkConfigurationError) as error:
            await self._status(key, "failed", error.message if isinstance(error, ContractError) else "System network route is unavailable", truncated=truncated)
        except asyncio.CancelledError:
            # Stopping: what was being sent may have reached the channel, so it is recorded as unknown, never resent.
            def stopped():
                status = self.db.execute("SELECT status FROM deliveries WHERE producer=? AND notification_id=? AND channel_id=?", key).fetchone()["status"]
                if status == "sending":
                    with self.db:
                        self.db.execute("UPDATE deliveries SET status='unknown',updated_at=?,error=?,truncated=? WHERE producer=? AND notification_id=? AND channel_id=?",
                                        (now(), "Host stopped before a delivery receipt was recorded", int(truncated), *key))
            self.thread.submit(stopped).result()
            raise
