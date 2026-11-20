"""Imported builder diagnostics must preserve compiler failures on Windows."""
import importlib.util
import io
import json
from pathlib import Path
from types import SimpleNamespace

import pytest


@pytest.fixture
def builder():
    path = Path(__file__).parents[2] / "modules/quant/runtime/build/nautilus.py"
    spec = importlib.util.spec_from_file_location("native_build_logging_test", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def stub_command(monkeypatch, builder, payload, returncode=101):
    calls = []
    def run(command, *, cwd, env, stdout, stderr):
        calls.append((command, cwd, env))
        stdout.buffer.write(payload)
        return SimpleNamespace(returncode=returncode)
    monkeypatch.setattr(builder.subprocess, "run", run)
    return calls


def receipt(tmp_path):
    path = tmp_path / "build.json"
    path.write_text(json.dumps({"status": "building", "preserved": "original receipt fact"}), encoding="utf-8")
    return path


def test_imported_run_keeps_unicode_compiler_failure_with_strict_gbk_stdout(builder, monkeypatch, tmp_path):
    raw = io.BytesIO()
    console = io.TextIOWrapper(raw, encoding="gbk", errors="strict")
    monkeypatch.setattr(builder.sys, "stdout", console)
    failure = "error[E0308]: 🦀 compiler rejected value — field café\n"
    payload = failure.encode("utf-8")
    calls = stub_command(monkeypatch, builder, payload)
    receipt_path = receipt(tmp_path)
    log = tmp_path / "编译🧪.log"
    command = [Path("工具🧪") / "cargo.exe", "build", "--release"]
    with pytest.raises(RuntimeError) as raised:
        builder.run(command, tmp_path, {"BUILD_TEST": "1"}, log)
    assert "Native command failed (101)" in str(raised.value)
    assert str(log) in str(raised.value) and failure.strip() in str(raised.value)
    assert log.read_bytes() == payload
    facts = json.loads(receipt_path.read_text(encoding="utf-8"))
    assert facts["status"] == "build_failed" and facts["returncode"] == 101
    assert facts["failed_command"] == list(map(str, command))
    assert facts["failed_log"] == log.name and facts["failure_tail"] == failure
    assert facts["preserved"] == "original receipt fact"
    assert calls == [(list(map(str, command)), tmp_path, {"BUILD_TEST": "1"})]
    assert console.encoding == "gbk" and console.errors == "strict"
    assert b"error[E0308]" in raw.getvalue() and b"\\U0001f980" in raw.getvalue()


@pytest.mark.parametrize("stream_error", [OSError("broken pipe"), ValueError("closed stream"), RuntimeError("damaged wrapper")])
def test_broken_console_streams_cannot_mask_exit_code_or_native_reason(builder, monkeypatch, tmp_path, stream_error):
    class BrokenStream:
        encoding = "gbk"
        def write(self, _):
            raise stream_error
        def flush(self):
            raise stream_error
    monkeypatch.setattr(builder.sys, "stdout", BrokenStream())
    monkeypatch.setattr(builder.sys, "stderr", BrokenStream())
    stub_command(monkeypatch, builder, b"fatal error C1083: missing_header.h\n", returncode=7)
    receipt_path = receipt(tmp_path)
    with pytest.raises(RuntimeError, match=r"Native command failed \(7\)") as raised:
        builder.run(["cargo", "build"], tmp_path, {}, tmp_path / "native.log")
    assert "fatal error C1083: missing_header.h" in str(raised.value)
    facts = json.loads(receipt_path.read_text(encoding="utf-8"))
    assert facts["returncode"] == 7 and "missing_header.h" in facts["failure_tail"]


def test_unreadable_receipt_and_invalid_utf8_log_still_raise_native_failure(builder, monkeypatch, tmp_path):
    receipt_path = tmp_path / "build.json"
    receipt_path.write_text("malformed receipt", encoding="utf-8")
    stub_command(monkeypatch, builder, b"native failure: bad object \xff\n", returncode=3)
    with pytest.raises(RuntimeError, match=r"Native command failed \(3\)") as raised:
        builder.run(["link.exe"], tmp_path, {}, tmp_path / "native.log")
    assert "Could not update build receipt" in str(raised.value)
    assert "native failure: bad object" in str(raised.value)
    assert receipt_path.read_text(encoding="utf-8") == "malformed receipt"


def test_receipt_write_failure_does_not_replace_native_exit(builder, monkeypatch, tmp_path):
    receipt_path = receipt(tmp_path)
    stub_command(monkeypatch, builder, b"error: unresolved external symbol\n", returncode=9)
    original = Path.write_text
    def reject(path, *args, **kwargs):
        if path == receipt_path:
            raise OSError("receipt disk failure")
        return original(path, *args, **kwargs)
    monkeypatch.setattr(Path, "write_text", reject)
    with pytest.raises(RuntimeError, match=r"Native command failed \(9\)") as raised:
        builder.run(["link.exe"], tmp_path, {}, tmp_path / "native.log")
    assert "unresolved external symbol" in str(raised.value)
    assert "receipt disk failure" in str(raised.value)


def test_success_under_gbk_does_not_change_receipt_or_run_configuration(builder, monkeypatch, tmp_path):
    console = io.TextIOWrapper(io.BytesIO(), encoding="gbk", errors="strict")
    monkeypatch.setattr(builder.sys, "stdout", console)
    calls = stub_command(monkeypatch, builder, b"success\n", returncode=0)
    receipt_path = receipt(tmp_path)
    before = receipt_path.read_bytes()
    builder.run(["工具🧪.exe"], tmp_path, {}, tmp_path / "native.log")
    assert len(calls) == 1 and receipt_path.read_bytes() == before


def test_existing_completed_receipt_is_not_rewritten(builder, monkeypatch, tmp_path):
    receipt_path = tmp_path / "build.json"
    receipt_path.write_text(json.dumps({"status": "complete", "old": "evidence"}), encoding="utf-8")
    before = receipt_path.read_bytes()
    stub_command(monkeypatch, builder, b"error: verification command failed\n")
    with pytest.raises(RuntimeError, match="verification command failed"):
        builder.run(["check.exe"], tmp_path, {}, tmp_path / "native.log")
    assert receipt_path.read_bytes() == before
