"""Reproduce the approved PyneCore 6.10.7 wheel; never edit an installation."""
# ruff: noqa: E501 -- native source lines are preserved verbatim for wheel identity

from __future__ import annotations

import base64
import csv
import hashlib
import io
import json
from pathlib import Path
from urllib.request import urlopen
from zipfile import ZIP_DEFLATED, ZipFile, ZipInfo

ROOT = Path(__file__).resolve().parents[1]
DESTINATION = ROOT / "modules/chart/backend/engine_materials"
UPSTREAM_URL = "https://files.pythonhosted.org/packages/50/65/fb92c28e04b9481f87b054e9885975f55126ee7bbd45f7da63b0770164dd/pynesys_pynecore-6.10.7-cp313-cp313-win_amd64.whl"
UPSTREAM_SHA256 = "094efc66773f1c07c9b3c93950279dccfebf7c28931f4102419970417aed81c4"
ORIGINAL_SECURITY = "988183fbc1289d7e986183c0bd7f4706051460eea262d89127c3b45543b2bbcf"
PATCHED_SECURITY = "e70060ba84dc55d1562cccae7dc8eabd8b86d77616617a396d40cce504c60d35"
PATCH_ID = "talyx-security-accepted-observations-v2+runner-context-v1+own-symbol-feed-v1+last-history-bar-v1+captured-lookahead-v1"
BUILD = "8talyx"
WHEEL_NAME = f"pynesys_pynecore-6.10.7-{BUILD}-cp313-cp313-win_amd64.whl"


def patched_security(raw: bytes) -> bytes:
    if hashlib.sha256(raw).hexdigest() != ORIGINAL_SECURITY:
        raise ValueError("The upstream security.py identity differs")
    source = raw.decode().replace("\r\n", "\n")
    replacements = [
        (
            "                    if prefill_target > state.last_confirmed:\n",
            "                    # Compare bar-open identities, not an exclusive boundary\n"
            "                    # against a previously consumed bar's open. An unnecessary\n"
            "                    # empty prefill would erase that bar's published value.\n"
            "                    if (containing is not None and state.bar_opens is not None\n"
            "                            and state.bar_closes is not None):\n"
            "                        prior = bisect_right(state.bar_closes, containing.period_start) - 1\n"
            "                        prefill_target = state.bar_opens[prior] if prior >= 0 else 0\n"
            "                    if prefill_target > state.last_confirmed:\n",
        ),
        (
            "        _buffer: list = []\n",
            "        _buffer: list = []\n        _window_start: int | None = None\n        _window_values: list = []\n",
        ),
        (
            "            published = _buffer[skip:]\n",
            "            nonlocal _window_start, _window_values\n"
            "            period_start = sync_block.get_ltf_period_start(sec_id)\n"
            "            if period_start != _window_start:\n"
            "                _window_start = period_start\n"
            "                _window_values = []\n"
            "            # The file cursor only visits newly closed intrabars. Repeat rounds\n"
            "            # must publish their whole chart window, including earlier writes.\n"
            "            # Values (including na) are retained verbatim, never forward-filled.\n"
            "            _window_values.extend(_buffer[skip:])\n"
            "            published = list(_window_values)\n",
        ),
    ]
    for before, after in replacements:
        if source.count(before) != 1:
            raise ValueError("Approved patch location differs")
        source = source.replace(before, after)
    raw = source.replace("\n", "\r\n").encode()
    if hashlib.sha256(raw).hexdigest() != PATCHED_SECURITY:
        raise ValueError("The approved patched security.py identity differs")
    return raw


def build(destination=DESTINATION):
    upstream = urlopen(UPSTREAM_URL, timeout=60).read()
    if hashlib.sha256(upstream).hexdigest() != UPSTREAM_SHA256:
        raise ValueError("The locked upstream wheel identity differs")
    with ZipFile(io.BytesIO(upstream)) as archive:
        files = {name: archive.read(name) for name in archive.namelist()}
    files["pynecore/core/security.py"] = patched_security(files["pynecore/core/security.py"])
    from scripts.chart_engine.captured_patch import (
        patched_captured_lookahead,
        patched_last_history_bar,
        patched_live_runner,
        patched_own_symbol_feed,
        patched_security_dispatch,
        patched_security_process,
    )
    from scripts.chart_engine.isolation_patch import patched_script_runner, runner_context

    files["pynecore/core/security.py"] = patched_security_dispatch(
        files["pynecore/core/security.py"]
    )
    files["pynecore/core/live_runner.py"] = patched_live_runner(
        files["pynecore/core/live_runner.py"]
    )
    files["pynecore/core/security_process.py"] = patched_security_process(
        files["pynecore/core/security_process.py"]
    )
    files["pynecore/core/script_runner.py"] = patched_script_runner(
        files["pynecore/core/script_runner.py"]
    )
    files["pynecore/core/runner_context.py"] = runner_context()
    files["pynecore/core/security.py"], files["pynecore/core/script_runner.py"] = patched_own_symbol_feed(
        files["pynecore/core/security.py"], files["pynecore/core/script_runner.py"]
    )
    files["pynecore/core/script_runner.py"] = patched_last_history_bar(files["pynecore/core/script_runner.py"])
    files["pynecore/core/security.py"] = patched_captured_lookahead(files["pynecore/core/security.py"])
    # Build metadata records the local wheel identity without changing the
    # locked upstream version or adding another executable code modification.
    wheel = "pynesys_pynecore-6.10.7.dist-info/WHEEL"
    files[wheel] += f"Build: {BUILD}\n".encode()
    record = "pynesys_pynecore-6.10.7.dist-info/RECORD"
    listing = io.StringIO(newline="")
    writer = csv.writer(listing, lineterminator="\n")
    for name in sorted(set(files) - {record}):
        raw = files[name]
        encoded = base64.urlsafe_b64encode(hashlib.sha256(raw).digest()).rstrip(b"=").decode()
        writer.writerow([name, "sha256=" + encoded, len(raw)])
    writer.writerow([record, "", ""])
    files[record] = listing.getvalue().encode()
    destination.mkdir(parents=True, exist_ok=True)
    path = destination / WHEEL_NAME
    with ZipFile(path, "w", compression=ZIP_DEFLATED, compresslevel=9) as archive:
        for name, raw in sorted(files.items()):
            entry = ZipInfo(name, (2026, 11, 14, 0, 0, 0))
            entry.compress_type = ZIP_DEFLATED
            entry.external_attr = 0o644 << 16
            archive.writestr(entry, raw)
    identity = {
        "distribution": "pynesys-pynecore",
        "version": "6.10.7",
        "patchId": PATCH_ID,
        "wheel": WHEEL_NAME,
        "wheelSha256": hashlib.sha256(path.read_bytes()).hexdigest(),
        "upstreamUrl": UPSTREAM_URL,
        "upstreamSha256": UPSTREAM_SHA256,
        "files": {
            name: hashlib.sha256(raw).hexdigest()
            for name, raw in sorted(files.items())
            if name.startswith("pynecore/") or name.endswith(("/METADATA", "/WHEEL"))
        },
    }
    (destination / "identity.json").write_text(
        json.dumps(identity, indent=2) + "\n", encoding="utf-8"
    )
    return identity


if __name__ == "__main__":
    print(json.dumps(build(), indent=2))
