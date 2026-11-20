"""The fixed Nautilus source tree is rewritten only where a patch changes bytes."""
import importlib.util
import io
import json
import os
import tarfile
from pathlib import Path

import pytest


@pytest.fixture
def builder(tmp_path, monkeypatch):
    path = Path(__file__).parents[2] / "modules/quant/runtime/build/nautilus.py"
    spec = importlib.util.spec_from_file_location("native_build_tree_test", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    runtime = tmp_path / "runtime"
    (runtime / "upstream").mkdir(parents=True)
    archive = runtime / "upstream/nautilus-trader-1.0+talyx.1-source.tar.gz"
    files = {
        "Cargo.toml": '[workspace]\nmembers = ["crates/core", "crates/pyo3"]\n',
        "pyproject.toml": '[project]\nname = "nautilus_trader"\nversion = "1.0+talyx.1"\n',
        "python/pyproject.toml": '[project]\nname = "nautilus_trader"\nversion = "1.0+talyx.1"\n',
        "crates/core/src/lib.rs": "pub fn untouched() {}\n",
        "crates/pyo3/src/lib.rs": "pub fn value() -> u8 { 1 }\n",
    }
    with tarfile.open(archive, "w:gz") as package:
        for name, text in files.items():
            payload = text.encode()
            member = tarfile.TarInfo("nautilus_trader-1.0+talyx.1/" + name)
            member.size = len(payload)
            package.addfile(member, io.BytesIO(payload))
    catalogue = runtime / "sources.json"
    catalogue.write_text(json.dumps({"archives": {"1.0+talyx.1": {"filename": archive.name, "sha256": module.digest(archive)}}}))
    monkeypatch.setattr(module, "RUNTIME", runtime)
    monkeypatch.setattr(module, "SOURCES", catalogue)
    return module


def test_prepare_strips_version_directory_sets_version_and_applies_patch(builder, tmp_path):
    patch = tmp_path / "value.patch"
    # Written with CRLF, as a Windows checkout of a patch file would be.
    patch.write_bytes(b"--- a/crates/pyo3/src/lib.rs\r\n+++ b/crates/pyo3/src/lib.rs\r\n@@ -1 +1 @@\r\n"
                      b"-pub fn value() -> u8 { 1 }\r\n+pub fn value() -> u8 { 2 }\r\n")
    staged = builder.stage("1.0+talyx.1", [patch], "1.0+talyx.2", tmp_path / "work/stage")
    assert (staged / "crates/pyo3/src/lib.rs").read_text() == "pub fn value() -> u8 { 2 }\n"
    for name in ("pyproject.toml", "python/pyproject.toml"):
        assert 'version = "1.0+talyx.2"' in (staged / name).read_text()
    assert not (staged / "nautilus_trader-1.0+talyx.1").exists()


def test_sync_keeps_unchanged_files_and_removes_stale_ones(builder, tmp_path):
    source = tmp_path / "work/source"
    first = builder.stage("1.0+talyx.1", [], "1.0+talyx.1", tmp_path / "work/stage")
    changed, removed = builder.sync(first, source)
    assert len(changed) == 5 and removed == []
    untouched = source / "crates/core/src/lib.rs"
    os.utime(untouched, (1_000_000_000, 1_000_000_000))
    (source / "stale.rs").write_text("left from an earlier patch")
    second = builder.stage("1.0+talyx.1", [], "1.0+talyx.2", tmp_path / "work/stage")
    changed, removed = builder.sync(second, source)
    assert sorted(changed) == ["pyproject.toml", "python/pyproject.toml"]
    assert removed == ["stale.rs"]
    assert untouched.stat().st_mtime == 1_000_000_000
    assert builder.tree_digest(source) == builder.tree_digest(second)


def test_changed_archive_is_rejected(builder):
    archive = builder.RUNTIME / "upstream/nautilus-trader-1.0+talyx.1-source.tar.gz"
    archive.write_bytes(archive.read_bytes() + b"\0")
    with pytest.raises(ValueError, match="differs"):
        builder.base_archive("1.0+talyx.1")


def official_catalogue(builder, tmp_path, monkeypatch, overlay_digest=None):
    """An official release whose sdist (no Cargo patch crates) and tag overlay are local stand-ins."""
    sdist = tmp_path / "official.tar.gz"
    with tarfile.open(sdist, "w:gz") as package:
        for name, text in {"Cargo.toml": '[workspace]\nmembers = ["crates/pyo3"]\n',
                           "pyproject.toml": '[project]\nversion = "2.0"\n',
                           "crates/pyo3/src/lib.rs": "pub fn value() -> u8 { 1 }\n"}.items():
            payload = text.encode()
            member = tarfile.TarInfo("nautilus_trader-2.0/" + name)
            member.size = len(payload)
            package.addfile(member, io.BytesIO(payload))

    def write_overlay(release, destination):
        (destination / "python").mkdir()
        (destination / "python/pyproject.toml").write_text('[project]\nversion = "2.0"\n')
        (destination / "patches/arrow").mkdir(parents=True)
        (destination / "patches/arrow/Cargo.toml").write_text("[package]\n")
        return builder.tree_digest(destination)

    monkeypatch.setattr(builder, "download", lambda asset: sdist)
    monkeypatch.setattr(builder, "official_overlay", write_overlay)
    monkeypatch.setattr(builder, "CACHE", tmp_path / "cache")
    probe = tmp_path / "probe"
    probe.mkdir()
    overlay = write_overlay(None, probe)
    builder.SOURCES.write_text(json.dumps({"official": {"releases": {"2.0": {
        "tag": "v2.0", "commit": "0" * 40, "overlay": overlay_digest or overlay,
        "sdist": {"filename": "official.tar.gz", "url": "https://example.invalid/official.tar.gz", "sha256": builder.digest(sdist)}}}},
        "archives": {}}))


def test_official_start_adds_the_files_the_sdist_omits(builder, tmp_path, monkeypatch):
    official_catalogue(builder, tmp_path, monkeypatch)
    staged = builder.stage("official:2.0", [], "2.0+talyx.1", tmp_path / "work/stage")
    assert (staged / "patches/arrow/Cargo.toml").is_file()
    assert (staged / "python/pyproject.toml").read_text() == '[project]\nversion = "2.0+talyx.1"\n'
    assert (staged / "crates/pyo3/src/lib.rs").read_text() == "pub fn value() -> u8 { 1 }\n"


def test_official_start_rejects_an_overlay_that_differs_from_the_catalogue(builder, tmp_path, monkeypatch):
    official_catalogue(builder, tmp_path, monkeypatch, overlay_digest={"files": 0, "sha256": "0" * 64})
    with pytest.raises(ValueError, match="overlay"):
        builder.stage("official:2.0", [], "2.0+talyx.1", tmp_path / "work/stage")
