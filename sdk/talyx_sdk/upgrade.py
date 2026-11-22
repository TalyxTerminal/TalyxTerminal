"""Data kept across TALYX versions: each owner's data carries the version of its format, and a newer TALYX brings it up.

An owner (a module, or the platform) declares the version of the format it keeps its data in and, for each version
after the first, a step that turns data of the version before into it (``Module.data_format``). Before the owner
starts, its data folder's ``format.json`` says which version is there; the steps from there up run in order, the
version written after each, so a step that failed runs again next time (a step must be able to run again after failing
part way, and find nothing to do where there is no data). Data without ``format.json`` is version 1: written before
versions were kept (TALYX 0.1.0), by an owner still at version 1, or none yet. Data of a version newer than the owner reads (the user went back to an older TALYX) is not touched: the
owner does not start, and says so.
"""

from __future__ import annotations

import json
import os
from collections.abc import Awaitable, Callable
from pathlib import Path

from .errors import ContractError
from .i18n import platform_t as t

FILE = "format.json"
FIRST = 1


def stored_version(folder: Path) -> int:
    path = folder / FILE
    return json.loads(path.read_text(encoding="utf-8"))["version"] if path.exists() else FIRST


def _write(folder: Path, version: int) -> None:
    folder.mkdir(parents=True, exist_ok=True)
    temporary = folder / f"{FILE}.{os.getpid()}.tmp"
    temporary.write_text(json.dumps({"version": version}), encoding="utf-8")
    temporary.replace(folder / FILE)


def check_steps(current: int, steps: dict[int, Callable]) -> None:
    if current < FIRST or set(steps) != set(range(FIRST + 1, current + 1)):
        raise ValueError(f"format {current} needs exactly one step for each version from {FIRST + 1} to {current}")


def _plan(folder: Path, current: int, steps: dict[int, Callable]) -> range:
    check_steps(current, steps)
    stored = stored_version(folder)
    if stored > current:
        raise ContractError("data_too_new", t(
            "Its data was written by a newer TALYX (format {stored}; this version reads up to {current}). Install the "
            "newer TALYX again; the data is left as it is.", stored=stored, current=current))
    return range(stored + 1, current + 1)


async def bring_up_to_date(folder: Path, current: int, steps: dict[int, Callable[[], Awaitable[None]]]) -> list[int]:
    """Runs the steps from the stored version up to ``current``; answers the versions it reached."""
    reached = []
    for version in _plan(folder, current, steps):
        await steps[version]()
        _write(folder, version)
        reached.append(version)
    return reached


def bring_up_to_date_now(folder: Path, current: int, steps: dict[int, Callable[[], None]]) -> list[int]:
    """The same with plain steps, for data read before anything runs (the Host's own)."""
    reached = []
    for version in _plan(folder, current, steps):
        steps[version]()
        _write(folder, version)
        reached.append(version)
    return reached
