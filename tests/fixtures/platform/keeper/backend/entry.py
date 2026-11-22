"""A test module at data format 3: version 1 kept notes.txt, version 2 split it into lines, version 3 numbers them."""

from __future__ import annotations

from pathlib import Path

from talyx_sdk.declare import Model, Module
from talyx_sdk.process import Call, Context

module = Module("keeper")
STARTED = {"notes": None}


class Empty(Model):
    pass


async def folder(ctx: Context) -> Path:
    return Path((await ctx.platform.storage())["dataDir"])


async def split(ctx: Context) -> None:
    data = await folder(ctx)
    if (data / "notes.txt").exists():
        (data / "lines").mkdir(exist_ok=True)
        for index, line in enumerate((data / "notes.txt").read_text(encoding="utf-8").splitlines()):
            (data / "lines" / f"{index}.txt").write_text(line, encoding="utf-8")
        (data / "notes.txt").unlink()


async def number(ctx: Context) -> None:
    lines = await folder(ctx) / "lines"
    for path in sorted(lines.glob("*.txt")) if lines.exists() else []:
        path.write_text(f"{path.stem}: {path.read_text(encoding='utf-8')}", encoding="utf-8")


module.data_format(3, {2: split, 3: number})


@module.on_start
async def start(ctx: Context) -> None:
    lines = await folder(ctx) / "lines"
    STARTED["notes"] = [path.read_text(encoding="utf-8") for path in sorted(lines.glob("*.txt"))] if lines.exists() else []


@module.action(effects=["read"])
async def notes(input: Empty, call: Call) -> dict[str, list[str]]:
    """What the module read once started."""
    return {"notes": STARTED["notes"]}
