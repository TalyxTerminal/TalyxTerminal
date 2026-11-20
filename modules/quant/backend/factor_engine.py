"""The process that computes factor Work runs: open and kept for the person working. Jobs that run alone (a run's Results, an
allocation run, a model job) get a process of their own.

The open process keeps the run's data in memory, so the next run with the same data settings is answered in about two seconds. It stops
after ten idle minutes, and starts again after an error or a run that took too long.

The open process is Quant's own, not the work of the workspace that happened to start it: every workspace's runs share it, and closing
one workspace leaves it running. It computes one job at a time, the others wait here; a job whose workspace closes (its caller is
cancelled) leaves the queue, or, being computed, ends with the process, and the jobs waiting go on in a fresh one.
"""
from __future__ import annotations

import asyncio
import contextlib
import contextvars
import json
import logging
import shutil
import time
from pathlib import Path
from uuid import uuid4

from talyx_sdk import ContractError

from . import research_process
from .materials import plain
from .project_paths import MODULE

RESIDENT = MODULE / "native" / "factor_resident.py"
RESULTS = MODULE / "native" / "factor_results.py"
MODEL_WORKER = MODULE / "native" / "model_run.py"
IDLE_SECONDS = 600
JOB_SECONDS = 900
MEMORY = 600 * 1024 ** 2  # what the open process holds for one data set of 500 names over ten years, about


def _own(coroutine) -> asyncio.Task:
    """A task of Quant's own: no workspace, no activity, whoever's request led to it (asyncio would copy the caller's)."""
    return asyncio.get_running_loop().create_task(coroutine, context=contextvars.Context())


class FactorEngine:
    def __init__(self, ctx, packages, root, *, deep=False):
        self.ctx, self.packages, self.root, self.deep = ctx, packages, Path(root), deep
        self.root.mkdir(parents=True, exist_ok=True)
        self.lease = None
        self.environment = None
        self.pump = None
        self.pending = {}           # job id -> the caller's future, sent or waiting
        self.queue = []             # (job id, message) not sent yet, in order
        self.current = None         # the job id the process is computing
        self.later = {}
        self.starting = asyncio.Lock()
        self.idle = None
        self.closed = False
        self.started_at = None
        self.resuming = None

    # ------------------------------------------------------------------------------------------------------------ the open process
    def state(self):
        return {"running": self.lease is not None, "startedAt": self.started_at, "jobs": len(self.pending)}

    async def ensure(self, publish=None):
        """Start the open process if it is not running (as Quant's own, whoever asks)."""
        await asyncio.shield(_own(self._ensure(publish)))

    async def _ensure(self, publish):
        async with self.starting:
            if self.lease is not None:
                return
            environment = await (self.packages.deep_environment(publish) if self.deep else self.packages.research_environment(publish))
            directory = self.root / ("engine-" + uuid4().hex[:12])
            directory.mkdir(parents=True)
            support = research_process.write_support(directory)
            (directory / "invocation.json").write_text(json.dumps({"mode": "resident", "support": str(support)}), encoding="utf-8")
            self.ctx.environments.hold(environment["preparationId"], "qfactor-engine:" + directory.name)
            launch = {"submission_key": "qfactor-" + directory.name, "argv": [environment["interpreterPath"], "-B", str(RESIDENT), str(directory / "invocation.json")],
                      "cwd": str(directory), "env": research_process.environment(), "stdout_path": str(directory / "stdout.log"),
                      "stderr_path": str(directory / "stderr.log"), "lines": True,
                      "channel": {"directory": str(directory / "channel"), "context": {"kind": "factor"}}}
            lease = plain(await self.ctx.processes.start(launch))
            try:
                async with asyncio.timeout(120):
                    while True:
                        event = plain(await self.ctx.processes.receive(lease, 1.0))
                        if event["state"] == "closed":
                            raise ContractError("unavailable", "The research process stopped while it was starting.", {"stderr": self._tail(directory)})
                        if event["state"] == "message" and isinstance(event["message"], dict) and event["message"].get("ready"):
                            break
            except BaseException:
                await self._stop(lease)
                await self._release(environment, directory)
                raise
            self.lease, self.environment, self.directory, self.started_at = lease, environment, directory, int(time.time() * 1000)
            self.pump = _own(self._pump(lease))

    @staticmethod
    def _tail(directory):
        log = Path(directory) / "stderr.log"
        return log.read_text(encoding="utf-8", errors="replace")[-3000:] if log.is_file() else ""

    async def _pump(self, lease):
        try:
            while True:
                event = plain(await self.ctx.processes.receive(lease, 5.0))
                if event["state"] == "closed":
                    break
                if event["state"] != "message":
                    continue
                message = event["message"]
                if not isinstance(message, dict) or message.get("talyx") != "factor":
                    continue
                identity = message.get("id")
                if "future" in message:
                    listener = self.later.pop(identity, None)
                    if listener is not None:
                        listener(message["future"])
                    continue
                if identity == self.current:
                    self.current = None
                waiting = self.pending.pop(identity, None)
                if waiting is not None and not waiting.done():
                    waiting.set_result(message)
                await self._send_next()
        except ContractError:
            pass
        finally:
            if self.lease is lease:
                stderr = self._tail(self.directory)
                self.lease = None
                # The job being computed ends with the process; the ones waiting go on in a fresh one.
                waiting = self.pending.pop(self.current, None) if self.current is not None else None
                if waiting is not None and not waiting.done():
                    waiting.set_exception(ContractError("unavailable", "The research process stopped during the run.", {"stderr": stderr}))
                self.later.pop(self.current, None)
                self.current = None
                with contextlib.suppress(ContractError):
                    await self.ctx.processes.close(lease)
                await self._release(self.environment, self.directory)
                if self.queue and not self.closed:
                    self.resuming = _own(self._resume())

    async def _resume(self):
        try:
            await self._ensure(None)
        except Exception as error:  # noqa: BLE001 - the jobs waiting get the reason
            for identity, _message in self.queue:
                waiting = self.pending.pop(identity, None)
                if waiting is not None and not waiting.done():
                    waiting.set_exception(error)
            self.queue.clear()
            return
        await self._send_next()

    async def _send_next(self):
        if self.current is not None or not self.queue or self.lease is None:
            return
        identity, message = self.queue.pop(0)
        self.current = identity
        await self.ctx.processes.send(self.lease, message)

    async def _stop(self, lease):
        with contextlib.suppress(ContractError):
            await self.ctx.processes.cancel(lease)
        with contextlib.suppress(ContractError):
            await self.ctx.processes.close(lease)

    async def _release(self, environment, directory):
        if environment is not None:
            with contextlib.suppress(ContractError):
                self.ctx.environments.release(environment["preparationId"], "qfactor-engine:" + Path(directory).name)

    async def restart(self):
        """Stop the open process; the jobs waiting, and the next run, go on in a fresh one (after an error, a run that took too long, or
        changed packages)."""
        lease = self.lease
        if lease is not None:
            await self._stop(lease)
            if self.pump is not None:
                await asyncio.gather(self.pump, return_exceptions=True)

    def _touch(self):
        if self.idle is not None:
            self.idle.cancel()
        self.idle = asyncio.get_running_loop().call_later(IDLE_SECONDS, lambda: _own(self._idle_stop()))

    async def _idle_stop(self):
        if not self.pending and self.lease is not None:
            await self.restart()

    async def _job(self, op, job, seconds, late, *, on_future=None, publish=None):
        """One job of the open process, in turn with the others: its answer."""
        await self.ensure(publish)
        identity = uuid4().hex
        waiting = asyncio.get_running_loop().create_future()
        self.pending[identity] = waiting
        if on_future is not None:
            self.later[identity] = on_future
        self.queue.append((identity, {"op": op, "id": identity, "job": job}))
        try:
            await self._send_next()
            async with asyncio.timeout(seconds):
                return await waiting
        except TimeoutError as error:
            await asyncio.shield(self._drop(identity))
            raise ContractError("unavailable", f"{late}; the research process was restarted.") from error
        except asyncio.CancelledError:
            # Its workspace closed or its caller stopped it: computed, the process ends with it (the next job starts a fresh one).
            await asyncio.shield(self._drop(identity))
            raise
        finally:
            self.pending.pop(identity, None)
            self._touch()

    async def _drop(self, identity):
        self.later.pop(identity, None)
        self.queue = [item for item in self.queue if item[0] != identity]
        if self.current == identity:
            await self.restart()

    async def run_open(self, job, *, on_future=None, publish=None):
        """One job in the open process; returns its answer. `on_future(result)` gets the code's future-data check when it comes later."""
        return await self._job("run", job, JOB_SECONDS, f"The run took more than {JOB_SECONDS // 60} minutes", on_future=on_future,
                               publish=publish)

    async def call(self, op, job, *, publish=None, seconds=120):
        """Another job of the open process than a run (a model's check before training, an allocation's update): its answer, on the data the process
        keeps."""
        return await self._job(op, job, seconds, f"The check took more than {seconds} seconds", publish=publish)

    # ------------------------------------------------------------------------------------------------------------ a model run
    async def run_model(self, job, directory, *, context, publish=None, on_lease=None):
        """One model job (a training run, a publication's check or update, a day's values) in its own process: `native/model_run.py` with
        the job written beside it. The answer comes from `answer.json`."""
        from ..native.model_algorithms import NEURAL_IDS
        deep = (job.get("params") or {}).get("algorithm") in NEURAL_IDS and job.get("mode") != "combine"  # a neural network's jobs run where PyTorch is
        environment = await (self.packages.deep_environment(publish) if deep else self.packages.research_environment(publish))
        directory = Path(directory)
        output = Path(job["output"])
        directory.mkdir(parents=True, exist_ok=True)
        output.mkdir(parents=True, exist_ok=True)
        shutil.rmtree(output / "channel", ignore_errors=True)  # a run the application stopped left its channel behind; the platform makes a new one
        support = research_process.write_support(directory)
        (directory / "invocation.json").write_text(json.dumps({"support": str(support), "job": job}), encoding="utf-8")
        launch = {"submission_key": "qmodel-" + directory.name + "-" + uuid4().hex[:8], "argv": [environment["interpreterPath"], "-B", str(MODEL_WORKER), str(directory / "invocation.json")],
                  "cwd": str(output), "env": research_process.environment(), "stdout_path": str(output / "stdout.log"), "stderr_path": str(output / "stderr.log"),
                  "channel": {"directory": str(output / "channel"), "context": context}}
        owner = "qmodel-run:" + directory.name
        self.ctx.environments.hold(environment["preparationId"], owner)
        launching = asyncio.create_task(self.ctx.processes.start(launch))
        lease = None
        try:
            lease = plain(await asyncio.shield(launching))
            if on_lease is not None:
                on_lease(lease)
            while plain(await self.ctx.processes.status(lease))["running"]:
                await asyncio.sleep(.2)
            await self.ctx.processes.wait(lease)
        except asyncio.CancelledError:
            if lease is None:
                lease = plain(await launching)
            await asyncio.shield(self._stop(lease))
            raise
        finally:
            if lease is not None:
                with contextlib.suppress(ContractError):
                    await self.ctx.processes.close(lease)
            with contextlib.suppress(ContractError):
                self.ctx.environments.release(environment["preparationId"], owner)
        answer = output / "answer.json"
        if not answer.is_file():
            stderr = (output / "stderr.log").read_text(encoding="utf-8", errors="replace")[-2000:] if (output / "stderr.log").is_file() else ""
            raise ContractError("unavailable", "The model process stopped before it answered.", {"stderr": stderr})
        return json.loads(answer.read_text(encoding="utf-8"))

    # ------------------------------------------------------------------------------------------------------------ a job on its own
    async def run_results(self, job, directory, *, context, publish=None):
        """A run's Results in their own process (TALYX's code only, on the run's saved values): Work's open process stays free meanwhile."""
        return await self.run_once(job, directory, context=context, publish=publish, script=RESULTS, prefix="qfactor-results-")

    async def run_once(self, job, directory, *, context, publish=None, script=RESIDENT, prefix="qfactor-once-", op="run", environment=None):
        """One job in a process of its own; the answer comes from `answer.json` in its output folder."""
        if environment is None:
            environment = await self.packages.research_environment(publish)
        directory = Path(directory)
        directory.mkdir(parents=True, exist_ok=True)
        output = Path(job["output"])
        output.mkdir(parents=True, exist_ok=True)
        shutil.rmtree(output / "channel", ignore_errors=True)  # a run that failed left its channel behind; the platform makes a new one
        support = research_process.write_support(directory)
        (directory / "invocation.json").write_text(json.dumps({"mode": "once", "op": op, "support": str(support), "job": job}), encoding="utf-8")
        # Every run's folder is called "engine" (its Results' "results-engine"): the key and the hold on the environment name this one start,
        # else a second run reads as the first one's request.
        start = uuid4().hex
        launch = {"submission_key": prefix + start, "argv": [environment["interpreterPath"], "-B", str(script), str(directory / "invocation.json")],
                  "cwd": str(output), "env": research_process.environment(), "stdout_path": str(output / "stdout.log"), "stderr_path": str(output / "stderr.log"),
                  "channel": {"directory": str(output / "channel"), "context": context}}
        self.ctx.environments.hold(environment["preparationId"], "qfactor-run:" + start)
        launching = asyncio.create_task(self.ctx.processes.start(launch))
        lease = None
        try:
            lease = plain(await asyncio.shield(launching))
            while plain(await self.ctx.processes.status(lease))["running"]:
                await asyncio.sleep(.2)
            await self.ctx.processes.wait(lease)
        except asyncio.CancelledError:
            if lease is None:
                lease = plain(await launching)
            await asyncio.shield(self._stop(lease))
            raise
        finally:
            if lease is not None:
                with contextlib.suppress(ContractError):
                    await self.ctx.processes.close(lease)
            with contextlib.suppress(ContractError):
                self.ctx.environments.release(environment["preparationId"], "qfactor-run:" + start)
        answer = output / "answer.json"
        if not answer.is_file():
            stderr = (output / "stderr.log").read_text(encoding="utf-8", errors="replace")[-2000:] if (output / "stderr.log").is_file() else ""
            raise ContractError("unavailable", "The research process stopped before it answered.", {"stderr": stderr})
        return json.loads(answer.read_text(encoding="utf-8"))

    async def close(self):
        self.closed = True
        if self.idle is not None:
            self.idle.cancel()
        try:
            await self.restart()
        except Exception:  # pragma: no cover - closing must not raise
            logging.getLogger(__name__).exception("Closing the factor engine failed")
        for identity, _message in self.queue:
            waiting = self.pending.pop(identity, None)
            if waiting is not None and not waiting.done():
                waiting.set_exception(ContractError("unavailable", "Quant stopped."))
        self.queue.clear()
