"""User packages: one list beside the common code, resolved against the fixed research and strategy locks into platform environments.

The bundled locks are the constraint: a package may be added, a version already in a lock never moves, and a request that
needs it to is refused naming what conflicts. The research environment (Python 3.12) and the strategy environment (3.13)
are resolved and prepared separately, so a package the strategy side cannot take says exactly that.
"""
from __future__ import annotations

import asyncio
import contextlib
import hashlib
import json
import os
import re
import shutil
import subprocess
import time
from pathlib import Path

from talyx_sdk import ContractError

from .materials import plain
from .native_environment import bundled_wheels, prepare_lock
from .object_store import OBJECT
from .project_paths import MODULE

RUNTIME = MODULE / "runtime"
BASE_LOCKS = {"research": RUNTIME / "research.lock", "strategy": RUNTIME / "strategy.lock"}
# What uv resolves for, and the interpreter version an environment is named by (runs use the same names, so a lock without user packages is the same environment).
RESOLVE_FOR = {"research": "3.12", "strategy": "3.13"}
PYTHON = {"research": "3.12", "strategy": "3.13.13", "deep": "3.12"}
DEEP_VARIANTS = {"cpu": {"lock": "deep-cpu.lock", "torch": "2.15.0+cpu", "size": "about 1.5 GB", "label": "PyTorch 2.15 (CPU)"},
                 "cuda": {"lock": "deep-cuda.lock", "torch": "2.15.0+cu132", "size": "about 3.4 GB", "label": "PyTorch 2.15 (CUDA 13.2)"},
                 "xpu": {"lock": "deep-xpu.lock", "torch": "2.15.0+xpu", "size": "about 4 GB", "label": "PyTorch 2.15 (Intel XPU)"}}
CUDA_NEEDED = (13, 2)
MIN_COMPUTE = 7.5


def _cuda_tuple(text):
    found = re.search(r"(\d+)\.(\d+)", text or "")
    return (int(found.group(1)), int(found.group(2))) if found else (0, 0)


def detect_devices(run=subprocess.run):
    """What this machine has, before any environment exists: the CPU, each NVIDIA card (name, memory, driver, the newest CUDA the driver supports,
    compute capability) from `nvidia-smi`, and Intel Arc graphics from the video controller list. `run` is replaceable (tests feed recorded output)."""
    def ask(command):
        try:
            done = run(command, capture_output=True, text=True, timeout=20)
        except (OSError, subprocess.SubprocessError):
            return ""
        return done.stdout if getattr(done, "returncode", 1) == 0 else ""
    nvidia = []
    table = ask(["nvidia-smi", "--query-gpu=index,name,memory.total,memory.free,driver_version,compute_cap", "--format=csv,noheader,nounits"])
    header = ask(["nvidia-smi"])
    found = re.search(r"CUDA Version:\s*([\d.]+)", header)
    newest = found.group(1) if found else ""
    for line in table.splitlines():
        parts = [part.strip() for part in line.split(",")]
        if len(parts) >= 5:
            nvidia.append({"index": int(parts[0]), "name": parts[1], "memoryTotal": float(parts[2]) * 2**20, "memoryFree": float(parts[3]) * 2**20, "driver": parts[4],
                           "cudaMax": newest, "compute": float(parts[5]) if len(parts) > 5 and re.fullmatch(r"[\d.]+", parts[5]) else None})
    listing = ask(["powershell", "-NoProfile", "-Command", "Get-CimInstance Win32_VideoController | Select-Object -ExpandProperty Name"])
    intel = [{"name": line.strip()} for line in listing.splitlines() if "intel" in line.lower() and "arc" in line.lower()]
    from .run_queue import physical_memory
    return {"cpu": {"cores": os.cpu_count() or 1, "memory": physical_memory()}, "nvidia": nvidia, "intel": intel}


def deep_offer(devices):
    """Which PyTorch build to offer for these devices, and why not a better one: {variant, size, label, choices?, reason?}."""
    card = devices["nvidia"][0] if devices["nvidia"] else None
    if card is not None:
        if _cuda_tuple(card["cudaMax"]) < CUDA_NEEDED:
            reason = f"{card['name']}: the driver supports CUDA {card['cudaMax'] or 'none'}; PyTorch 2.15 needs 13.2. Update the driver to train on it."
            return {"variant": "cpu", **DEEP_VARIANTS["cpu"], "reason": reason}
        if card.get("compute") is not None and card["compute"] < MIN_COMPUTE:
            reason = f"{card['name']}: compute capability {card['compute']} is below what PyTorch 2.15 CUDA 13.2 supports; training runs on the CPU."
            return {"variant": "cpu", **DEEP_VARIANTS["cpu"], "reason": reason}
        return {"variant": "cuda", **DEEP_VARIANTS["cuda"]}
    if devices["intel"]:
        return {"variant": "cpu", **DEEP_VARIANTS["cpu"], "choices": ["cpu", "xpu"]}
    return {"variant": "cpu", **DEEP_VARIANTS["cpu"]}
OWNER = "quant-vscode"
REQUIREMENTS = "requirements.txt"
HEADER = "# Packages for TALYX Quant: one requirement per line, as pip reads them (pandas-ta, ta>=0.11). Install them from the Packages page; versions the bundled environment already fixes cannot change.\n"
SETTINGS = ".vscode/settings.json"


def normalized(name):
    return re.sub(r"[-_.]+", "-", name).lower()


def lock_blocks(text):
    """A lock's entries by package name, each with its hash lines and comments; the header comment is left out."""
    entries, name = {}, None
    for line in text.splitlines():
        if line and not line[0].isspace() and not line.startswith("#"):
            name = normalized(re.split(r"[ =@;]", line, maxsplit=1)[0])
            entries[name] = [line]
        elif name is not None and (line.startswith((" ", "\t"))):
            entries[name].append(line)
    return entries


def merge_locks(base, resolved):
    """The base lock followed by the packages it lacks; one it already pins must stay at the same version."""
    fixed, found = lock_blocks(base), lock_blocks(resolved)
    for name, block in found.items():
        if name in fixed and block[0].split(" \\")[0] != fixed[name][0].split(" \\")[0]:
            raise ContractError("conflict", f"The resolution would move {name}, which the environment fixes.", {"package": name})
    added = [line for name, block in found.items() if name not in fixed for line in block]
    return base.rstrip("\n") + "\n" + "\n".join(added) + "\n" if added else base


def requested_names(text):
    return [line.split("#", 1)[0].strip() for line in text.splitlines() if line.split("#", 1)[0].strip()]


def jsonc(text):
    """VS Code settings are JSON with comments and trailing commas."""
    output, index = [], 0
    while index < len(text):
        char = text[index]
        if char == '"':
            end = index + 1
            while text[end] != '"':
                end += 2 if text[end] == "\\" else 1
            output.append(text[index:end + 1])
            index = end + 1
        elif text.startswith("//", index):
            index = text.find("\n", index) if "\n" in text[index:] else len(text)
        elif text.startswith("/*", index):
            index = text.index("*/", index) + 2
        else:
            output.append(char)
            index += 1
    return json.loads(re.sub(r",(\s*[}\]])", r"\1", "".join(output)))


class UserPackages:
    def __init__(self, ctx, records, common, store):
        self.ctx, self.records, self.common, self.store = ctx, records, common, store
        self.root = records.root / "packages"
        self.root.mkdir(parents=True, exist_ok=True)
        self.state_path = self.root / "state.json"
        self.state = json.loads(self.state_path.read_text(encoding="utf-8")) if self.state_path.is_file() else {"research": None, "strategy": None, "vscode": None, "deep": None}
        self.task = None
        self.job = None
        self.deep_task = None
        self.deep_job = None
        self.deep_devices = None
        self.preparations = {}
        self.kernels = None  # set by the services: environments of the kernels now running, to say which need a restart

    @property
    def requirements_path(self):
        return self.common.root / REQUIREMENTS

    def requirements(self):
        path = self.requirements_path
        if not path.is_file():
            path.write_text(HEADER, encoding="utf-8")
        return path.read_text(encoding="utf-8")

    def save_state(self):
        temporary = self.state_path.with_suffix(".tmp")
        temporary.write_text(json.dumps(self.state, indent=1), encoding="utf-8")
        temporary.replace(self.state_path)

    # what new work uses
    def effective(self, environment):
        """The lock new kernels, runs and backtests use: the bundled one, or it with the user's packages once those are installed."""
        installed = self.state[environment]
        text = Path(installed["lockPath"]).read_text(encoding="utf-8") if installed else BASE_LOCKS[environment].read_text(encoding="utf-8")
        origin = {"kind": "bundled-research", "pythonVersion": PYTHON[environment], "engine": "research" if environment == "research" else "strategy"}
        return {"lock": text, "digest": hashlib.sha256(text.encode()).hexdigest(), "origin": origin, "lockPath": installed["lockPath"] if installed else str(BASE_LOCKS[environment])}

    async def research_environment(self, publish=None):
        """The research environment for the effective lock (the same one when it was already prepared)."""
        effective = self.effective("research")
        return {**await prepare_lock(self.ctx, bundled_wheels(effective["lock"], RUNTIME), effective["origin"], publish), "lockDigest": effective["digest"], "lockPath": effective["lockPath"]}

    # which environment a notebook's kernel runs in
    def kernel_environments(self):
        """The environments a kernel can run in: the research one, and the deep-learning one once the platform has it (D adds `deep_status` and `deep_environment`)."""
        found = [{"id": "research", "label": f"Research, Python {PYTHON['research']}", "python": PYTHON["research"], "installed": True}]
        if True:
            status = self.deep_status()
            devices = status["devices"] if "devices" in status else []
            found.append({"id": "deep", "label": "Deep learning, Python 3.12, PyTorch", "python": "3.12", "installed": bool(status["installed"]),
                          **({"device": devices[0]["label"]} if devices else {})})
        return found

    async def kernel_environment(self, environment, publish=None):
        """The prepared environment a kernel starts in. The deep-learning one is D's: it is installed through D's own action, never from here."""
        if environment == "research":
            return await self.research_environment(publish)
        if environment == "deep":
            if not self.deep_status()["installed"]:
                raise ContractError("deep_not_installed", "The deep-learning environment is not installed.", {"offer": self.deep_status().get("offer")})
            return await self.deep_environment()
        raise ContractError("invalid_input", f"There is no kernel environment named {environment}.", {"environments": [item["id"] for item in self.kernel_environments()]})

    # the deep learning environment: the research packages plus one PyTorch build, installed once for every neural network
    def devices(self, run=subprocess.run):
        """The machine's devices, looked at again after a minute."""
        if self.deep_devices is None or time.time() - self.deep_devices[0] > 60:
            self.deep_devices = (time.time(), detect_devices(run))
        return self.deep_devices[1]

    def deep_lock(self, variant):
        text = (RUNTIME / DEEP_VARIANTS[variant]["lock"]).read_text(encoding="utf-8")
        return {"lock": text, "digest": hashlib.sha256(text.encode()).hexdigest(),
                "origin": {"kind": "bundled-research", "pythonVersion": PYTHON["deep"], "engine": "deep"}}

    def deep_status(self):
        installed = self.state.get("deep")
        devices = self.devices()
        offer = deep_offer(devices)
        listing = [{"id": "cpu", "kind": "cpu", "label": f"CPU · {devices['cpu']['cores']} cores"}]
        if installed and installed["variant"] == "cuda":
            listing = [{"id": f"cuda:{card['index']}", "kind": "cuda", "label": f"{card['name']} · {card['memoryTotal'] / 2**30:.0f} GB",
                        "memoryTotal": card["memoryTotal"], "memoryFree": card["memoryFree"]} for card in devices["nvidia"]] + listing
        job = self.deep_job
        mismatch = bool(installed) and installed["variant"] != offer["variant"] and offer["variant"] == "cuda"
        return {"installed": bool(installed), "variant": installed["variant"] if installed else None,
                "offer": {key: offer[key] for key in ("variant", "size", "label", "choices", "reason") if key in offer and offer[key] is not None},
                # What is being installed and how much it downloads: the platform's preparation reports no bytes, so the size is the build's own.
                "installing": {"state": job["state"], "fraction": job.get("fraction"), "label": DEEP_VARIANTS[job["variant"]]["label"], "size": DEEP_VARIANTS[job["variant"]]["size"],
                               "startedAt": job["startedAt"]} if job and job["state"] in ("running", "failed") else None,
                "failure": job["message"] if job and job["state"] == "failed" else None, "devices": listing, "newCard": mismatch}

    async def deep_environment(self, publish=None):
        """The installed deep learning environment (the same one when it was already prepared)."""
        installed = self.state.get("deep")
        if not installed:
            raise ContractError("unavailable", "The deep learning environment is not installed; install it from a model's Algorithm settings.")
        effective = self.deep_lock(installed["variant"])
        return {**await prepare_lock(self.ctx, bundled_wheels(effective["lock"], RUNTIME), effective["origin"], publish),
                "lockDigest": effective["digest"], "lockPath": str(RUNTIME / DEEP_VARIANTS[installed["variant"]]["lock"])}

    def install_deep(self, variant=None):
        if self.deep_task is not None and not self.deep_task.done():
            raise ContractError("conflict", "The deep learning environment is already being installed.")
        offer = deep_offer(self.devices())
        variant = variant or offer["variant"]
        if variant not in DEEP_VARIANTS or (variant != offer["variant"] and variant not in offer.get("choices", ["cpu"])):
            raise ContractError("invalid_input", f"This machine cannot use the {variant} build.")
        self.deep_job = {"state": "running", "variant": variant, "fraction": None, "message": None, "startedAt": int(time.time() * 1000)}

        async def run():
            effective = self.deep_lock(variant)

            def publish(state):
                self.deep_job["preparationId"] = state["preparationId"]
                self.deep_job["fraction"] = state.get("progress", {}).get("fraction") if isinstance(state.get("progress"), dict) else None
            try:
                prepared = await prepare_lock(self.ctx, bundled_wheels(effective["lock"], RUNTIME), effective["origin"], publish)
            except asyncio.CancelledError:
                self.deep_job.update(state="cancelled")
                raise
            except ContractError as error:
                self.deep_job.update(state="failed", message=error.message)
                return
            previous, self.state["deep"] = self.state.get("deep"), {"preparationId": prepared["preparationId"], "lockDigest": effective["digest"], "variant": variant}
            self.save_state()
            self.ctx.environments.hold(prepared["preparationId"], "qpackages:deep")
            if previous and previous["preparationId"] != prepared["preparationId"]:
                with contextlib.suppress(ContractError):
                    self.ctx.environments.release(previous["preparationId"], "qpackages:deep")
            self.deep_job.update(state="completed")

        self.deep_task = self.ctx.start(run())
        return self.deep_status()

    async def cancel_deep(self):
        if self.deep_task is None or self.deep_task.done():
            raise ContractError("conflict", "The deep learning environment is not being installed.")
        task, job = self.deep_task, self.deep_job
        task.cancel()
        job["state"] = "cancelled"
        if preparation := job.get("preparationId"):
            await self.ctx.environments.cancel(preparation)
        await asyncio.gather(task, return_exceptions=True)
        return self.deep_status()

    # installing
    def status(self):
        names = requested_names(self.requirements())
        needing = self.kernels.stale(self.effective("research")["digest"]) if self.kernels else []
        return {"requirements": names, "file": str(self.requirements_path), "job": self.job,
                "environments": {environment: None if self.state[environment] is None else {key: self.state[environment][key] for key in ("lockDigest", "installedAt", "packages")}
                                 for environment in ("research", "strategy")},
                "restartKernels": needing}

    async def uv(self, arguments, cwd):
        uv = shutil.which("uv")
        if uv is None:
            raise ContractError("unavailable", "Install uv to add packages.")
        process = await asyncio.create_subprocess_exec(uv, *arguments, cwd=cwd, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE)
        try:
            _, stderr = await process.communicate()
        except asyncio.CancelledError:
            with contextlib.suppress(ProcessLookupError):
                process.kill()
            await process.wait()
            raise
        return process.returncode, stderr.decode("utf-8", errors="replace")

    async def resolve(self, environment, requested):
        """The environment's lock with the requested packages added, or why it cannot be."""
        base = BASE_LOCKS[environment].read_text(encoding="utf-8")
        directory = self.root / "resolve" / environment
        directory.mkdir(parents=True, exist_ok=True)
        (directory / "constraints.txt").write_text(bundled_wheels(base, RUNTIME), encoding="utf-8")
        (directory / "requested.in").write_text("\n".join(requested) + "\n", encoding="utf-8")
        output = directory / "resolved.lock"
        output.unlink(missing_ok=True)
        code, message = await self.uv(["pip", "compile", "requested.in", "-c", "constraints.txt", "--python-version", RESOLVE_FOR[environment], "--python-platform", "windows",
                                       "--generate-hashes", "--no-header", "-o", "resolved.lock"], directory)
        if code != 0:
            cause = next((line.strip() for line in message.splitlines() if line.strip().startswith("cause:")), message.strip()[-1500:]).removeprefix("cause:").strip()
            raise ContractError("conflict", f"These packages cannot be added to the {environment} environment: {cause}",
                                {"environment": environment, "requested": requested, "cause": cause})
        return merge_locks(base, output.read_text(encoding="utf-8"))

    async def environment_for(self, environment, lock, requested):
        """Prepare the resolved lock as a platform environment and keep it as this environment's lock."""
        digest = hashlib.sha256(lock.encode()).hexdigest()
        path = self.root / "locks" / f"{environment}-{digest[:16]}.lock"
        path.parent.mkdir(exist_ok=True)
        path.write_text(lock, encoding="utf-8")
        origin = {"kind": "bundled-research", "pythonVersion": PYTHON[environment], "engine": "research" if environment == "research" else "strategy"}

        def publish(state):
            self.job["environments"][environment] = {"state": state["state"], "message": state["message"] if "message" in state else None}
            self.preparations[environment] = state["preparationId"]

        prepared = await prepare_lock(self.ctx, bundled_wheels(lock, RUNTIME), origin, publish)
        return {"lockPath": str(path), "lockDigest": digest, "preparationId": prepared["preparationId"], "interpreterPath": prepared["interpreterPath"],
                "installedAt": int(time.time() * 1000), "packages": requested}

    async def install(self):
        """Resolve and prepare both environments; each reports on its own, and a failed one keeps its previous lock."""
        requested = requested_names(self.requirements())
        failures = {}
        for environment in ("research", "strategy"):
            self.job["stage"] = f"{environment}: resolving"
            self.job["environments"][environment] = {"state": "resolving", "message": None}
            installed = None
            try:
                if requested:
                    lock = await self.resolve(environment, requested)
                    self.job["stage"] = f"{environment}: installing"
                    installed = await self.environment_for(environment, lock, requested)
            except ContractError as error:
                failures[environment] = error.to_dict()
                self.job["environments"][environment] = {"state": "failed", "message": error.message}
                continue
            previous, self.state[environment] = self.state[environment], installed
            self.save_state()
            self.job["environments"][environment] = {"state": "ready", "message": None}
            owner = "qpackages:" + environment
            if installed is not None:
                self.ctx.environments.hold(installed["preparationId"], owner)
            if previous is not None and (installed is None or previous["preparationId"] != installed["preparationId"]):
                self.ctx.environments.release(previous["preparationId"], owner)
        if "research" not in failures:
            await self.point_vscode(await self.research_environment())
        return failures

    def start_install(self):
        if self.task is not None and not self.task.done():
            raise ContractError("conflict", "Packages are already being installed.")
        self.preparations = {}
        self.job = {"state": "running", "stage": "starting", "startedAt": int(time.time() * 1000), "environments": {}, "failures": {}, "endedAt": None}

        async def run():
            try:
                self.job["failures"] = await self.install()
                self.job.update(state="failed" if self.job["failures"] else "completed")
            except asyncio.CancelledError:
                self.job.update(state="cancelled")
                for item in self.job["environments"].values():
                    if item["state"] in {"resolving", "queued", "running"}:
                        item.update(state="cancelled")
                raise
            finally:
                self.job.update(endedAt=int(time.time() * 1000), stage="done")
                # A notebook shows "restart for the new packages" from this (its kernels' environment may now be older than the lock).
                self.ctx.publish("packages_changed", {"state": self.job["state"]})

        self.task = self.ctx.start(run())
        return self.status()

    async def cancel(self):
        if self.task is None or self.task.done():
            raise ContractError("conflict", "No package installation is running.")
        task, job = self.task, self.job
        task.cancel()
        for preparation in self.preparations.values():
            state = plain(self.ctx.environments.get(preparation))
            if state["state"] in {"queued", "running"}:
                await self.ctx.environments.cancel(preparation)
        await asyncio.gather(task, return_exceptions=True)
        if job["state"] == "running":  # cancelled before the install coroutine started
            job.update(state="cancelled", stage="done", endedAt=int(time.time() * 1000))
            self.ctx.publish("packages_changed", {"state": "cancelled"})
        return self.status()

    def activity(self, workspace_id):
        if self.task is None or self.task.done():
            return []
        return [{"id": "packages", "kind": "calculation", "title": "Installing packages", "target": {"panel": "quant.workspace"}}]

    # VS Code
    async def point_vscode(self, installed, records=None):
        """The notebook projects (all of them unless some are named) name the research environment as their interpreter, so VS Code runs the same Python."""
        self.ctx.environments.hold(installed["preparationId"], OWNER)
        previous = self.state["vscode"]
        self.state["vscode"] = installed["preparationId"]
        self.save_state()
        if previous is not None and previous != installed["preparationId"]:
            with contextlib.suppress(ContractError):
                self.ctx.environments.release(previous, OWNER)
        notebooks = records if records is not None else [item for item in self.store.records.list(OBJECT) if item["kind"] == "notebook" and not item.get("deleted")]
        for record in notebooks:
            await self.write_interpreter(record, installed["interpreterPath"])

    async def write_interpreter(self, record, interpreter):
        """`python.defaultInterpreterPath` in the notebook project's .vscode/settings.json, edited as a Developer document so its buffer and history stay whole."""
        path = Path(record["directory"]) / SETTINGS
        documents = {"open": "developer.documents.open", "edit": "developer.documents.edit", "save": "developer.documents.save", "create": "developer.documents.create"}
        if record.get("developer") is None or await self.ctx.developer("developer.documents.list", {"rootUris": [path.parent.as_uri()]}) is None:
            # Developer is not installed (or never opened this project): the settings file is written directly.
            current = {}
            if path.is_file():
                try:
                    current = jsonc(path.read_text(encoding="utf-8"))
                except (ValueError, IndexError) as error:
                    raise ContractError("invalid_input", ".vscode/settings.json is not valid JSON. Fix it in VS Code, then retry.") from error
            if current.get("python.defaultInterpreterPath") != interpreter:
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text(json.dumps({**current, "python.defaultInterpreterPath": interpreter}, indent=2) + "\n", encoding="utf-8")
            return
        if path.is_file():
            opened = plain(await self.ctx.call(documents["open"], {"source": {"kind": "file", "uri": path.as_uri()}}))
            if opened["dirty"] or opened["conflict"]:
                raise ContractError("conflict", "Save or resolve .vscode/settings.json before the interpreter is updated.")
            try:
                current = jsonc(path.read_text(encoding="utf-8"))
            except (ValueError, IndexError) as error:
                raise ContractError("invalid_input", ".vscode/settings.json is not valid JSON. Fix it in VS Code, then retry.") from error
            if current.get("python.defaultInterpreterPath") == interpreter:
                return
            content = json.dumps({**current, "python.defaultInterpreterPath": interpreter}, indent=2) + "\n"
            edited = plain(await self.ctx.call(documents["edit"], {"documentId": opened["documentId"], "expectedVersion": opened["version"], "content": content}))
            saved = plain(await self.ctx.call(documents["save"], {"documentId": edited["documentId"], "expectedVersion": edited["version"]}))
            if saved["status"] not in {"saved", "clean"}:
                raise ContractError("conflict", ".vscode/settings.json changed before the interpreter was saved.")
        else:
            content = json.dumps({"python.defaultInterpreterPath": interpreter}, indent=2) + "\n"
            await self.ctx.call(documents["create"], {"projectId": record["developer"]["projectId"], "relativePath": SETTINGS, "content": content})
