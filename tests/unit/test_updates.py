"""A newer TALYX: what GitHub's latest release says against this installation's version, downloading its installer
against the published checksum, and this version's notes once (no network)."""

from __future__ import annotations

import asyncio
import hashlib
import json

import httpx
import pytest
from talyx_sdk.errors import ContractError

from host.desktop import install
from host.updates import INSTALLER, Updates, current_version, release

SETUP = b"MZ" + bytes(range(256)) * 9000   # about 2.3 MB: several chunks, so progress is reported along the way


def newer() -> str:
    major, minor, patch = release(current_version())
    return f"{major}.{minor}.{patch + 1}"


def latest(version: str, digest: str | None = "sha256:" + hashlib.sha256(SETUP).hexdigest(), body: str = "Fixes") -> dict:
    assets = [{"name": INSTALLER, "browser_download_url": "https://github.com/download/setup.exe", "size": len(SETUP),
               **({"digest": digest} if digest else {})}]
    return {"tag_name": f"v{version}", "html_url": f"https://github.com/TalyxTerminal/TalyxTerminal/releases/tag/v{version}",
            "body": body, "published_at": "2026-12-01T00:00:00Z", "assets": assets}


def server(release_json: dict | None = None, setup: bytes = SETUP, tag: dict | None = None):
    def answer(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/latest"):
            return httpx.Response(200, json=release_json) if release_json is not None else httpx.Response(403, json={})
        if "/tags/" in request.url.path:
            return httpx.Response(200, json=tag) if tag is not None else httpx.Response(404, json={})
        return httpx.Response(200, content=setup, headers={"content-length": str(len(setup))})
    return httpx.MockTransport(answer)


def updates(folder, transport, fresh=False) -> Updates:
    return Updates(lambda: True, folder, fresh=fresh, transport=transport)


async def downloaded(found: Updates) -> list[dict]:
    """Check, download, and every download state published on the way."""
    seen: list[dict] = []
    stream = found.changes()
    await anext(stream)
    await found.check()
    await found.download()
    while True:
        state = await asyncio.wait_for(anext(stream), 10)
        if state["download"] is not None:
            seen.append(state["download"])
            if state["download"]["state"] != "downloading":
                return seen


def test_a_newer_published_version_is_offered_with_its_page_notes_and_installer(tmp_path):
    state = asyncio.run(updates(tmp_path, server(latest(newer()))).check())
    assert state["latest"] == {"version": newer(), "url": f"https://github.com/TalyxTerminal/TalyxTerminal/releases/tag/v{newer()}",
                               "notes": "Fixes", "publishedAt": "2026-12-01T00:00:00Z", "installer": True}
    assert state["problem"] is None and state["checkedAt"] is not None


def test_the_same_or_an_older_version_offers_nothing(tmp_path):
    for version in (current_version(), "0.0.1"):
        state = asyncio.run(updates(tmp_path, server(latest(version))).check())
        assert state["latest"] is None and state["problem"] is None


def test_a_failed_check_says_why_and_keeps_what_was_known(tmp_path):
    state = asyncio.run(updates(tmp_path, server(None)).check())
    assert state["latest"] is None and "HTTP 403" in state["problem"]


def test_versions_compare_as_numbers():
    assert release("v0.10.0") > release("0.9.9")
    assert release("v1.2.3-beta") == (1, 2, 3)


def test_the_installer_downloads_with_progress_and_is_ready_only_when_its_sha256_matches(tmp_path):
    found = updates(tmp_path, server(latest(newer())))
    steps = asyncio.run(downloaded(found))
    assert steps[0]["state"] == "downloading" and steps[0]["total"] == len(SETUP)
    assert any(0 < step["received"] < len(SETUP) for step in steps[:-1]), "progress is reported before the end"
    last = steps[-1]
    assert last["state"] == "ready" and last["received"] == len(SETUP) and last["problem"] is None
    assert (tmp_path / f"TALYX-Setup-{newer()}.exe").read_bytes() == SETUP and last["path"] == str(tmp_path / f"TALYX-Setup-{newer()}.exe")
    assert not list(tmp_path.glob("*.part"))


def test_an_installer_that_does_not_match_its_sha256_is_deleted_and_never_ready(tmp_path):
    found = updates(tmp_path, server(latest(newer()), setup=SETUP[:-1] + b"X"))
    last = asyncio.run(downloaded(found))[-1]
    assert last["state"] == "failed" and last["path"] is None
    assert "does not match the SHA-256 checksum" in last["problem"] and hashlib.sha256(SETUP).hexdigest()[:12] in last["problem"]
    assert not list(tmp_path.glob("TALYX-Setup-*")), "nothing that failed the check is kept"


def test_a_release_without_a_published_checksum_is_not_downloaded(tmp_path):
    found = updates(tmp_path, server(latest(newer(), digest=None)))
    state = asyncio.run(found.check())
    assert state["latest"]["installer"] is False
    with pytest.raises(ContractError):
        asyncio.run(found.download())


def test_a_new_installation_shows_no_notes(tmp_path):
    assert updates(tmp_path, server(), fresh=True).state["notes"] is None
    assert json.loads((tmp_path / "seen.json").read_text())["version"] == current_version()


def test_after_updating_itself_this_versions_notes_show_once(tmp_path):
    (tmp_path / "seen.json").write_text(json.dumps({"version": "0.0.1"}))
    (tmp_path / f"notes-{current_version()}.md").write_text("**Faster** backtests", encoding="utf-8")   # kept by the download
    first = updates(tmp_path, server())
    assert first.state["notes"] == {"version": current_version(), "notes": "**Faster** backtests"}
    first.notes_seen()
    assert first.state["notes"] is None
    assert updates(tmp_path, server()).state["notes"] is None, "not again at the next start"


def test_a_version_installed_by_hand_shows_its_releases_notes_once(tmp_path):
    found = updates(tmp_path, server(tag={"tag_name": f"v{current_version()}", "body": "Hong Kong backtests"}))   # no seen.json: an older TALYX ran here
    assert found.notes_due and found.state["notes"] is None
    asyncio.run(found._fetch_notes())
    assert found.state["notes"] == {"version": current_version(), "notes": "Hong Kong backtests"}
    found.notes_seen()
    again = updates(tmp_path, server(tag={"tag_name": f"v{current_version()}", "body": "Hong Kong backtests"}))
    assert not again.notes_due and again.state["notes"] is None


def test_notes_that_cannot_be_read_are_asked_for_again_at_the_next_start(tmp_path):
    found = updates(tmp_path, server(tag=None))
    asyncio.run(found._fetch_notes())
    assert found.notes_due and found.state["notes"] is None
    assert updates(tmp_path, server()).notes_due


def test_an_installer_that_failed_is_reported_once_and_its_download_cleared(tmp_path):
    (tmp_path / "install.json").write_text(json.dumps({"version": newer(), "code": 2}))
    (tmp_path / f"TALYX-Setup-{newer()}.exe").write_bytes(SETUP)
    found = updates(tmp_path, server(), fresh=True)
    assert f"did not install (its installer ended with code 2); this is still {current_version()}" in found.state["problem"]
    assert not (tmp_path / "install.json").exists() and not list(tmp_path.glob("TALYX-Setup-*"))
    found.transport = server(latest(newer()))
    assert "did not install" in asyncio.run(found.check())["problem"], "a check that succeeds does not hide it"
    assert updates(tmp_path, server()).state["problem"] is None


def test_the_install_helper_waits_for_the_desktop_installs_silently_and_reopens_as_opened(tmp_path, monkeypatch):
    monkeypatch.setenv("TALYX_RELEASE_LAUNCHER", r"C:\Users\o'brien\TALYX\launch.py")
    command, folder = install.relaunch()
    assert command[0].lower().endswith("pythonw.exe") and command[-1] == r"C:\Users\o'brien\TALYX\launch.py" and folder == r"C:\Users\o'brien\TALYX"
    text = install.script(4242, tmp_path / "TALYX-Setup-9.9.9.exe", "9.9.9", tmp_path / "install.json", command, folder)
    lines = text.splitlines()
    assert "Get-Process -Id 4242" in lines[1] and "WaitForExit" in lines[1]
    assert "/SILENT /SUPPRESSMSGBOXES /NORESTART" in lines[3] and "-Wait" in lines[3]
    assert r"o''brien" in lines[-1], "a quote in a path is escaped for PowerShell"
    assert lines.index(lines[3]) < len(lines) - 2, "the result is written after the installer ends, before reopening"
