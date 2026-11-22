"""A newer TALYX: whether one is published, getting it, and this version's release notes once.

- Checking: the latest public release on GitHub against this installation's version. The desktop app asks when it starts
  and once a day while the General setting ``checkForUpdates`` is on; anyone can ask now (``platform.updates.check``).
  The request names no user and sends nothing but itself.
- Getting it (``platform.updates.download``): the release's Windows installer is downloaded into the platform's data
  (``platform/updates``), with progress, and checked against the SHA-256 GitHub publishes for it (the asset's
  ``digest``); one that does not match is deleted, never installed. The desktop then quits, runs it (Inno Setup's own
  progress window) and opens TALYX again (``host/desktop/install.py``); the installer upgrades in place and keeps the
  user's data. A release without such an installer is downloaded from its page, as before.
- Release notes: the first start of a new version (updated here or installed by hand) shows that version's notes once
  (``notes``, until ``platform.updates.notes_seen``). They come from the download when TALYX updated itself, otherwise
  from that version's release on GitHub. A new installation has nothing to tell and shows none.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import os
import re
import time
import tomllib
import webbrowser
from collections.abc import AsyncIterator, Callable
from pathlib import Path

import httpx
from talyx_sdk.errors import ContractError
from talyx_sdk.i18n import t

log = logging.getLogger("talyx.updates")
# TALYX_UPDATE_FEED: another place that answers as GitHub's releases API does (a trial with a local fake version).
FEED = os.environ.get("TALYX_UPDATE_FEED") or "https://api.github.com/repos/TalyxTerminal/TalyxTerminal/releases"
INSTALLER = "TALYX-Setup-x64.exe"
DAY_SECONDS = 24 * 3600
NOTES_LIMIT = 20_000
PROGRESS_SECONDS = 0.25
RELEASE = re.compile(r"^v?(\d+)\.(\d+)\.(\d+)")
HEADERS = {"Accept": "application/vnd.github+json", "User-Agent": "TALYX"}


def current_version() -> str:
    """This installation's version: the one its installer was built with."""
    return tomllib.loads((Path(__file__).resolve().parents[1] / "pyproject.toml").read_text(encoding="utf-8"))["project"]["version"]


def release(version: str) -> tuple[int, int, int] | None:
    match = RELEASE.match(version)
    return (int(match[1]), int(match[2]), int(match[3])) if match else None


def update_folder(data_dir: Path) -> Path:
    """Where downloads, the version whose notes were seen and the last installer's result are kept."""
    return data_dir / "platform" / "updates"


def installer_of(found: dict) -> dict | None:
    """The release's Windows installer with the SHA-256 GitHub publishes for it, or None when it has none."""
    for asset in found.get("assets") or []:
        digest = str(asset.get("digest") or "")
        if asset.get("name") == INSTALLER and digest.startswith("sha256:"):
            return {"url": asset["browser_download_url"], "size": asset.get("size"), "sha256": digest.removeprefix("sha256:").lower()}
    return None


class Updates:
    def __init__(self, enabled: Callable[[], bool], folder: Path, *, fresh: bool = False,
                 transport: httpx.AsyncBaseTransport | None = None):
        """`folder`: see `update_folder`. `fresh`: this start made the data folder, so no earlier version ran here."""
        self.enabled, self.folder, self.transport = enabled, folder, transport
        self.state = {"current": current_version(), "latest": None, "checkedAt": None, "problem": None, "download": None, "notes": None}
        self.asset: dict | None = None
        self.listeners: set[asyncio.Queue] = set()
        self.checking: asyncio.Task | None = None
        self.downloading: asyncio.Task | None = None
        self.install_problem: str | None = None   # the last installer's failure: kept through this session's checks
        self.notes_due = self._settle(fresh)

    # ── what this start finds ───────────────────────────────────────────

    def _settle(self, fresh: bool) -> bool:
        """Clear what an earlier version left, read the last installer's result, and say whether this version's notes are
        still to be shown (taking them from the download when TALYX updated itself)."""
        current = self.state["current"]
        self.folder.mkdir(parents=True, exist_ok=True)
        for item in self.folder.glob("TALYX-Setup-*"):
            item.unlink(missing_ok=True)
        result = self.folder / "install.json"
        if result.exists():
            try:
                found = json.loads(result.read_text(encoding="utf-8-sig"))
                wanted = release(str(found["version"]))
                if wanted is not None and wanted > release(current):
                    self.state["problem"] = self.install_problem = t("The update to {version} did not install (its installer ended with code {code}); this is still {current}.",
                                              version=found["version"], code=found["code"], current=current)
            except (ValueError, KeyError, TypeError):
                log.warning("Unreadable installer result %s", result)
            result.unlink()
        seen_file = self.folder / "seen.json"
        seen = json.loads(seen_file.read_text(encoding="utf-8"))["version"] if seen_file.exists() else None
        for item in self.folder.glob("notes-*.md"):
            if item.name != f"notes-{current}.md":
                item.unlink()
        if seen == current:
            return False
        if (seen is None and fresh) or (seen is not None and (release(seen) or (0, 0, 0)) > release(current)):
            self._seen()
            return False
        kept = self.folder / f"notes-{current}.md"
        if kept.exists():
            self._show_notes(kept.read_text(encoding="utf-8"))
            return False
        return True

    def _seen(self) -> None:
        (self.folder / "seen.json").write_text(json.dumps({"version": self.state["current"]}), encoding="utf-8")

    def _show_notes(self, notes: str) -> None:
        if notes.strip():
            self.state["notes"] = {"version": self.state["current"], "notes": notes[:NOTES_LIMIT]}
        else:
            self._seen()

    def notes_seen(self) -> dict:
        """The user has read this version's notes: they are not shown again."""
        self._seen()
        (self.folder / f"notes-{self.state['current']}.md").unlink(missing_ok=True)
        self.notes_due = False
        self._set(notes=None)
        return self.state

    async def _fetch_notes(self) -> None:
        """This version's notes from its release, when TALYX was installed by hand (none were downloaded)."""
        try:
            async with self._client() as client:
                response = await client.get(f"{FEED}/tags/v{self.state['current']}")
            if response.status_code != 200:
                raise ValueError(f"HTTP {response.status_code}")
            self.notes_due = False
            self._show_notes(str(response.json().get("body") or ""))
            self._publish()
        except (httpx.HTTPError, ValueError, KeyError, TypeError, AttributeError) as exc:
            log.info("Could not read the notes of this version: %s", exc)   # asked again at the next start

    # ── checking ────────────────────────────────────────────────────────

    def _client(self, **options) -> httpx.AsyncClient:
        return httpx.AsyncClient(timeout=15, transport=self.transport, headers=HEADERS, **options)

    def _set(self, **changes) -> None:
        self.state = {**self.state, **changes}
        self._publish()

    def _publish(self) -> None:
        for queue in self.listeners:
            queue.put_nowait(self.state)

    async def run(self) -> None:
        """While the desktop app runs: ask now, then once a day, whenever the setting is on."""
        while True:
            if self.enabled():
                if self.notes_due:
                    await self._fetch_notes()
                await self.check()
            await asyncio.sleep(DAY_SECONDS)

    async def check(self) -> dict:
        """Ask GitHub for the latest release; one question at a time."""
        if self.checking is None or self.checking.done():
            self.checking = asyncio.create_task(self._ask())
        await asyncio.shield(self.checking)
        return self.state

    async def _ask(self) -> None:
        changes: dict = {}
        try:
            async with self._client() as client:
                response = await client.get(f"{FEED}/latest")
            if response.status_code != 200:
                raise ValueError(f"HTTP {response.status_code}")
            found = response.json()
            version = str(found["tag_name"]).removeprefix("v")
            newer = release(version) is not None and release(version) > release(self.state["current"])
            self.asset = installer_of(found) if newer else None
            latest = {"version": version, "url": found["html_url"], "notes": (found.get("body") or "")[:NOTES_LIMIT],
                      "publishedAt": found.get("published_at"), "installer": self.asset is not None} if newer else None
            changes = {"latest": latest, "problem": self.install_problem}
        except (httpx.HTTPError, ValueError, KeyError, TypeError) as exc:
            log.info("Could not check for a newer TALYX: %s", exc)
            changes = {"problem": t("Could not reach GitHub to check for a newer version ({reason}).", reason=str(exc) or type(exc).__name__)}
        self._set(**changes, checkedAt=int(time.time() * 1000))

    def open_download(self) -> None:
        latest = self.state["latest"]
        if latest is None:
            raise ContractError("not_found", t("No newer version is known; check for one first."))
        webbrowser.open(latest["url"])

    # ── downloading ─────────────────────────────────────────────────────

    async def download(self) -> dict:
        """Start downloading the newer version's installer (or go on with the download already running) and return at once."""
        latest = self.state["latest"]
        if latest is None or self.asset is None:
            raise ContractError("not_found", t("No newer version with an installer is known; check for one first."))
        download = self.state["download"]
        running = self.downloading is not None and not self.downloading.done()
        if not running and not (download and download["version"] == latest["version"] and download["state"] == "ready"):
            self.downloading = asyncio.create_task(self._fetch(latest, self.asset))
            await asyncio.sleep(0)
        return self.state

    async def _fetch(self, latest: dict, asset: dict) -> None:
        version = latest["version"]
        part, target = self.folder / f"TALYX-Setup-{version}.exe.part", self.folder / f"TALYX-Setup-{version}.exe"
        progress = {"version": version, "state": "downloading", "received": 0, "total": asset["size"], "problem": None, "path": None}
        self._set(download=progress)
        digest, last = hashlib.sha256(), 0.0
        try:
            async with self._client(follow_redirects=True) as client, client.stream("GET", asset["url"], headers={"Accept": "application/octet-stream"},
                                                                                     timeout=httpx.Timeout(30, read=120)) as response:
                if response.status_code != 200:
                    raise ValueError(f"HTTP {response.status_code}")
                total = int(response.headers.get("content-length") or 0) or asset["size"]
                with part.open("wb") as out:
                    async for chunk in response.aiter_bytes(1 << 20):
                        out.write(chunk)
                        digest.update(chunk)
                        progress = {**progress, "received": progress["received"] + len(chunk), "total": total}
                        if time.monotonic() - last >= PROGRESS_SECONDS:
                            last = time.monotonic()
                            self._set(download=progress)
        except (httpx.HTTPError, ValueError, OSError) as exc:
            part.unlink(missing_ok=True)
            log.info("The download of %s stopped: %s", version, exc)
            self._set(download={**progress, "state": "failed", "problem": t("The download stopped ({reason}). Try again.", reason=str(exc) or type(exc).__name__)})
            return
        found = digest.hexdigest()
        if found != asset["sha256"]:
            part.unlink()
            log.warning("The installer of %s does not match its checksum: %s, expected %s", version, found, asset["sha256"])
            self._set(download={**progress, "state": "failed", "problem": t(
                "The downloaded installer does not match the SHA-256 checksum GitHub publishes for it (expected {expected}, got {found}), "
                "so it was deleted and not installed. Try again; if it happens again, get the installer from the release page.",
                expected=asset["sha256"][:12] + "…", found=found[:12] + "…")})
            return
        part.replace(target)
        (self.folder / f"notes-{version}.md").write_text(latest["notes"], encoding="utf-8")   # shown once it runs
        self._set(download={**progress, "state": "ready", "path": str(target)})

    async def changes(self) -> AsyncIterator[dict]:
        queue: asyncio.Queue = asyncio.Queue()
        self.listeners.add(queue)
        try:
            yield self.state
            while True:
                yield await queue.get()
        finally:
            self.listeners.discard(queue)
