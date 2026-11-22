"""Start the research worker for a research run, a publication's checks or a library update: the same fixed inputs and channel."""
from __future__ import annotations

import asyncio
import contextlib
import json
import os
import shutil
from pathlib import Path

from talyx_sdk import ContractError, talyx_call

from .materials import plain
from .project_paths import MODULE

WORKER = MODULE / "native" / "research_worker.py"


def write_support(directory):
    """The one-file client with which the worker's code calls platform capabilities, kept in a folder only the Host writes."""
    support = Path(directory) / "support"
    support.mkdir(exist_ok=True)
    shutil.copyfile(talyx_call.__file__, support / "talyx_call.py")
    return support


def environment():
    env = {key: os.environ[key] for key in ("SystemRoot", "WINDIR", "TEMP", "TMP", "PATH", "COMSPEC", "PATHEXT", "USERPROFILE", "APPDATA", "LOCALAPPDATA", "TALYX_LANGUAGE", "TALYX_LOCALES") if key in os.environ}
    env.update(OMP_NUM_THREADS="1", OPENBLAS_NUM_THREADS="1", MKL_NUM_THREADS="1", NUMEXPR_NUM_THREADS="1", MPLBACKEND="Agg", PYTHONUNBUFFERED="1",
               PYTHONUTF8="1", PYTHONIOENCODING="utf-8", PYTHONDONTWRITEBYTECODE="1")
    return env


async def start(ctx, *, key, directory, invocation, state, context):
    """Launch the worker; returns its lease."""
    directory = Path(directory)
    output = directory / "output"
    env = environment()
    if invocation.get("tracking"):
        env.update(MLFLOW_ALLOW_FILE_STORE="true")
    launch = {"submission_key": key, "argv": [state["interpreterPath"], "-B", str(WORKER), str(directory / "invocation.json")], "cwd": str(directory / "source"),
              "env": env, "stdout_path": str(output / "stdout.log"), "stderr_path": str(output / "stderr.log"),
              "channel": {"directory": str(output / "channel"), "context": context}}
    launching = asyncio.create_task(ctx.processes.start(launch))
    try:
        return plain(await asyncio.shield(launching))
    except asyncio.CancelledError:
        # The process exists as soon as the platform answers; nothing may outlive the run that asked for it.
        lease = plain(await launching)
        with contextlib.suppress(ContractError):
            await ctx.processes.cancel(lease)
        raise


async def wait(ctx, lease):
    """Until the process ends; returns its exit code and the capability calls its code made through the channel."""
    while plain(await ctx.processes.status(lease))["running"]:
        await asyncio.sleep(.2)
    code = await ctx.processes.wait(lease)
    calls = plain(await ctx.processes.calls(lease))
    return code, calls


def write_invocation(directory, invocation):
    Path(directory, "invocation.json").write_text(json.dumps(invocation), encoding="utf-8")
