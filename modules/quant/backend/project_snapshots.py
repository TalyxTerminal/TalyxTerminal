"""Immutable, content-addressed copies of a project folder, under Quant's data directory: what a run fixes as its code
(the platform's artifacts service, which made these before, is gone)."""

from __future__ import annotations

import hashlib
import os
import shutil
from pathlib import Path
from uuid import uuid4

from talyx_sdk import ContractError as ToolError
from talyx_sdk.i18n import t


def _filesystem_path(path: Path) -> Path:
    """Use Win32 extended paths for platform-owned deep snapshot operations."""

    absolute = os.path.abspath(os.fspath(path))
    if os.name != "nt" or absolute.startswith("\\\\?\\"):
        return Path(absolute)
    if absolute.startswith("\\\\"):
        return Path("\\\\?\\UNC\\" + absolute[2:])
    return Path("\\\\?\\" + absolute)


def _project_files(root: Path, selected_paths=None) -> tuple[list[Path], list[Path]]:
    files: list[Path] = []
    directories: list[Path] = []
    if selected_paths is None:
        candidates = root.rglob("*")
    else:
        candidates = set()
        for relative in selected_paths:
            if Path(relative).is_absolute() or ".." in Path(relative).parts or Path(relative) == Path("."):
                raise ToolError("invalid_input", t("Selected runtime paths must stay inside their source"), {})
            selected = root / relative
            if not selected.exists():
                raise ToolError("unavailable", t("A selected runtime input is missing"), {"path": str(selected)})
            candidates.add(selected)
            if selected.is_dir() and not selected.is_symlink() and not selected.is_junction():
                candidates.update(selected.rglob("*"))
            for parent in selected.parents:
                if parent == root:
                    break
                candidates.add(parent)
    for path in sorted(candidates):
        if path.is_symlink() or path.is_junction():
            raise ToolError(
                "invalid_input",
                t("Runtime project snapshots do not follow symbolic links"),
                {"path": os.fspath(path)},
            )
        if path.is_file():
            files.append(path)
        elif path.is_dir():
            directories.append(path)
    if not files:
        raise ToolError("invalid_input", t("Runtime project has no files"), {"path": os.fspath(root)})
    if len(files) > 10_000:
        raise ToolError(
            "invalid_input", t("Runtime project contains too many files"), {"count": len(files)}
        )
    return files, directories


def _file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        while chunk := stream.read(1024 * 1024):
            digest.update(chunk)
    return digest.hexdigest()


def snapshot_project(data_dir: Path, project_path: str, *, selected_paths=None) -> dict:
    source_path = Path(os.path.abspath(os.path.expanduser(project_path)))
    source = _filesystem_path(source_path)
    if not source.is_dir():
        raise ToolError(
            "invalid_input", t("projectPath must name a directory"), {"path": project_path}
        )
    files, directories = _project_files(source, selected_paths)
    digest = hashlib.sha256()
    records = []
    total = 0
    for directory in directories:
        digest.update(b"directory\0")
        digest.update(directory.relative_to(source).as_posix().encode("utf-8"))
        digest.update(b"\0")
    for path in files:
        relative = path.relative_to(source).as_posix()
        content_digest = hashlib.sha256()
        size = 0
        with path.open("rb") as stream:
            while chunk := stream.read(1024 * 1024):
                size += len(chunk)
                total += len(chunk)
                content_digest.update(chunk)
        if total > 1024 * 1024 * 1024:
            raise ToolError(
                "invalid_input",
                t("Runtime project snapshot exceeds 1 GiB"),
                {"path": os.fspath(source)},
            )
        file_digest = content_digest.hexdigest()
        digest.update(relative.encode("utf-8"))
        digest.update(b"\0")
        digest.update(file_digest.encode("ascii"))
        records.append({"path": relative, "sizeBytes": size, "sha256": file_digest})
    project_digest = digest.hexdigest()
    snapshots = Path(data_dir) / "project-snapshots"
    destination = snapshots / project_digest
    destination_filesystem = _filesystem_path(destination)
    if not destination_filesystem.exists():
        snapshots.mkdir(parents=True, exist_ok=True)
        # Keep the unpublished segment short.  On Windows the source tree may
        # already be deeply nested, and a digest plus UUID here can push valid
        # project files past the legacy MAX_PATH boundary during copy.
        staging = snapshots / f".tmp-{uuid4().hex[:12]}"
        staging_filesystem = _filesystem_path(staging)
        staging_filesystem.mkdir()
        try:
            for directory in directories:
                (staging_filesystem / directory.relative_to(source)).mkdir(
                    parents=True, exist_ok=True
                )
            for record, source_file in zip(records, files, strict=True):
                target = staging_filesystem / record["path"]
                target.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(source_file, target)
                if (
                    target.stat().st_size != record["sizeBytes"]
                    or _file_sha256(target) != record["sha256"]
                ):
                    raise ToolError(
                        "conflict",
                        t("Runtime project changed while its snapshot was being created"),
                        {"path": os.fspath(source_file)},
                    )
            try:
                staging_filesystem.replace(destination_filesystem)
            except (FileExistsError, PermissionError):
                # Windows reports access denied when another publisher has
                # already installed this nonempty directory. Reuse only an
                # existing directory; its complete content is verified below.
                if not destination_filesystem.is_dir():
                    raise
                shutil.rmtree(staging_filesystem)
        except BaseException:
            if staging_filesystem.exists():
                shutil.rmtree(staging_filesystem)
            raise
    for record in records:
        copied = destination_filesystem / record["path"]
        if (
            not copied.is_file()
            or copied.stat().st_size != record["sizeBytes"]
            or _file_sha256(copied) != record["sha256"]
        ):
            raise ToolError(
                "conflict",
                t("An existing Runtime snapshot failed content verification"),
                {"digest": project_digest, "path": record["path"]},
            )
    for directory in directories:
        if not (destination_filesystem / directory.relative_to(source)).is_dir():
            raise ToolError(
                "conflict",
                t("An existing Runtime snapshot is missing a source directory"),
                {"digest": project_digest, "path": directory.relative_to(source).as_posix()},
            )
    return {
        "protocol": "platform.runtime-project-snapshot.v1",
        "sourcePath": os.fspath(source_path),
        "snapshotPath": os.fspath(destination_filesystem),
        "digest": project_digest,
        "sizeBytes": total,
        "files": records,
        "directories": [directory.relative_to(source).as_posix() for directory in directories],
    }
