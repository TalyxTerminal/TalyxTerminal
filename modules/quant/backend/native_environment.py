"""Resolve original project locks and local packages into platform environments."""
from __future__ import annotations

import asyncio
import contextlib
import hashlib
import os
import re
import shutil
import tomllib
from pathlib import Path
from urllib.parse import unquote, urlparse

from talyx_sdk import ContractError
from talyx_sdk.i18n import t

from .materials import plain
from .project_snapshots import snapshot_project
from .work_records import digest

# Bundled locks name their fixed wheels relative to the repository root
# (file:./modules/quant/runtime/wheels/...). Locks copied into older projects
# may still carry an absolute file:///<old root>/modules/quant/runtime/wheels/...
# One research environment (research.lock) serves every research engine.
RESEARCH_IMPORTS = ["qlib", "alphalens", "statsmodels", "arch", "linearmodels", "skfolio", "QuantLib", "ORE", "ipykernel", "jupyter_client"]

RUNTIME = Path(__file__).resolve().parents[1] / "runtime"
BUNDLED_WHEEL = re.compile(r"file:(?:///[^\s]+/|\./)modules/quant/runtime/(wheels/[^\s]+)")


def bundled_wheels(lock, runtime):
    """Resolve bundled wheel references against this checkout's runtime directory.

    Hashes in the lock still verify the exact wheel bytes; only the location
    is supplied by the current repository.
    """
    runtime = Path(runtime)
    return BUNDLED_WHEEL.sub(lambda match: (runtime / unquote(match.group(1))).resolve().as_uri(), lock)


def stable_lock(lock):
    """The lock with its bundled wheels named relative to the repository (`file:./modules/quant/runtime/wheels/...`), whichever checkout resolved them."""
    return BUNDLED_WHEEL.sub(lambda match: "file:./modules/quant/runtime/" + match.group(1), lock)


def preparation_of(error):
    """The environment a conflicting submission names (the same key, a lock resolved from another checkout), if it does."""
    details = error.details or {}
    return details.get("preparationId") or (details.get("cause") or {}).get("preparationId")


async def prepare_lock(ctx, lock, origin, publish=None):
    """The environment for `lock` (the one already there when it was). The environment is the same wherever the checkout is: the key names the lock
    with its bundled wheels as the repository writes them, and a lock resolved from another checkout meets the one already prepared. A failed
    earlier attempt is tried once more for this caller; a failure says the last line of the preparation's log."""
    deep = origin.get("engine") == "deep"
    imports = RESEARCH_IMPORTS + ["torch"] if deep else ["site"] if origin["kind"] in {"original-project", "saved-result"} else ["nautilus_trader", "pandas", "pyarrow", "plotly"] if origin.get("engine") in {"nautilus", "strategy"} else ["nautilus_trader", "pandas", "pyarrow"] if origin.get("engine") == "lp" else RESEARCH_IMPORTS
    lock_digest = hashlib.sha256(lock.encode()).hexdigest()
    identity = digest({"lockDigest": hashlib.sha256(stable_lock(lock).encode()).hexdigest(), "pythonVersion": origin["pythonVersion"], "imports": imports})
    submission = {"submission_key": "research-env-" + identity,
        "profile_id": "research-" + identity[:24], "python_version": origin["pythonVersion"],
        "requirements_lock": lock, "lock_digest": lock_digest, "verify_imports": imports}
    earlier = False
    try:
        state = plain(ctx.environments.submit(**submission))
        earlier = bool(state.get("duplicate"))
    except ContractError as error:
        found = preparation_of(error) if error.code == "conflict" else None
        if found is None:
            raise
        state, earlier = plain(ctx.environments.get(found)), True
    if earlier and state["state"] in {"failed", "cancelled"}:
        # The new request starts a fresh attempt with the same lock after a failure or explicit cancellation.
        state = plain(ctx.environments.retry(state["preparationId"]))
    while True:
        if publish:
            publish({**state, "origin": origin})
        if state["state"] not in {"queued", "running", "cancel_requested"}:
            break
        await asyncio.sleep(.2)
        state = plain(ctx.environments.get(state["preparationId"]))
    if state["state"] != "completed":
        # The window has no way to open the log, so the failure says its last line (uv names what it could not do there).
        try:
            log = await asyncio.to_thread(Path(state["stderrPath"]).read_text, encoding="utf-8", errors="replace")
        except FileNotFoundError:
            log = ""
        lines = log.strip().splitlines()
        reason = lines[-1].strip() if lines else "the preparation stopped without saying why"
        raise ContractError("unavailable", t("The research environment could not be prepared: {reason}", reason=reason), {"preparation": state})
    return {**state, "origin": origin}


async def command(ctx, argv, cwd, directory, name):
    """Run one trusted helper step."""
    environment = {key: os.environ[key] for key in ("SystemRoot", "WINDIR", "TEMP", "TMP", "PATH", "USERPROFILE", "APPDATA", "LOCALAPPDATA") if key in os.environ}
    lease = None
    launch = {"submission_key": digest(str(directory)) + ":" + name, "argv": argv, "cwd": str(cwd), "env": environment, "stdout_path": str(directory / (name + ".stdout.log")), "stderr_path": str(directory / (name + ".stderr.log"))}
    launching = asyncio.create_task(ctx.processes.start(launch))
    try:
        try:
            lease = plain(await asyncio.shield(launching))
        except asyncio.CancelledError:
            lease = plain(await launching)
            raise
        while plain(await ctx.processes.status(lease))["running"]:
            await asyncio.sleep(.2)
        if await ctx.processes.wait(lease) != 0:
            message = (directory / (name + ".stderr.log")).read_text(encoding="utf-8", errors="replace")[-3000:]
            raise ContractError("unavailable", t("Native project environment step {name} failed: {message}", name=name, message=message))
    except asyncio.CancelledError:
        if lease:
            await ctx.processes.cancel(lease)
        raise
    finally:
        if lease:
            with contextlib.suppress(ContractError):
                await ctx.processes.close(lease)


async def project_lock(ctx, project, captured, directory, baseline):
    root = Path(captured["snapshotPath"])
    version_file = root / ".python-version"
    version = version_file.read_text(encoding="utf-8").strip() if version_file.is_file() else "3.13.13" if project.get("workbench") in {"strategy", "lp"} else "3.12"
    lock_path = root / project.get("requirements", "requirements.lock")
    original = True
    if lock_path.is_file():
        lock = lock_path.read_text(encoding="utf-8")
    elif (root / "pyproject.toml").is_file():
        uv = shutil.which("uv")
        if not uv:
            raise ContractError("unavailable", t("uv is required to prepare this original Python project."))
        fixed_python = version

        async def run(arguments, working, name):
            await command(ctx, [uv, *arguments], working, directory, name)
        lock_path = directory / "requirements.resolved.lock"
        if (root / "uv.lock").is_file():
            await run(["export", "--frozen", "--format", "requirements.txt", "--no-emit-project", "--output-file", str(lock_path)], root, "export-lock")
            source_lock = tomllib.loads((root / "uv.lock").read_text(encoding="utf-8"))
            packages = [(item["name"], item["source"].get("editable", item["source"].get("directory"))) for item in source_lock.get("package", []) if "editable" in item["source"] or "directory" in item["source"]]
            directories = {(root / relative).resolve() for _, relative in packages}
            selected, third_party = set(), []
            for line in lock_path.read_text(encoding="utf-8").splitlines():
                declaration = line.strip()
                if declaration.startswith("-e "):
                    target = declaration[3:].split(" ; ", 1)[0]
                elif " @ file:" in declaration:
                    target = declaration.split(" @ ", 1)[1].split(" ; ", 1)[0]
                else:
                    third_party.append(line)
                    continue
                if target.startswith("file:"):
                    target = unquote(urlparse(target).path)
                    if os.name == "nt" and target.startswith("/") and len(target) > 2 and target[2] == ":":
                        target = target[1:]
                target_path = (root / target).resolve()
                if target_path in directories:
                    selected.add(target_path)
                else:
                    # A direct wheel URL is already a hashed requirement; only
                    # editable/directory packages need the fixed-wheel rewrite.
                    third_party.append(line)
            local = [(name, relative) for name, relative in packages if relative == "." or (root / relative).resolve() in selected]
            lock_path.write_text("\n".join(third_party) + "\n", encoding="utf-8")
        else:
            local = []
            metadata = tomllib.loads((root / "pyproject.toml").read_text(encoding="utf-8"))
            settings = metadata.get("tool", {}).get("uv", {})
            sources = settings.get("sources", {})
            for name, specification in sources.items():
                choices = specification if isinstance(specification, list) else [specification]
                for choice in choices:
                    if "path" in choice:
                        local.append((name, choice["path"]))
            # Workspace packages retain their own original metadata and layout.
            # Wheel overrides resolve local names without editable/live paths.
            for pattern in settings.get("workspace", {}).get("members", []):
                for member in root.glob(pattern):
                    if (member / "pyproject.toml").is_file():
                        package = tomllib.loads((member / "pyproject.toml").read_text(encoding="utf-8"))
                        local.append((package["project"]["name"], member.relative_to(root).as_posix()))
            if "build-system" in metadata:
                local.append((metadata.get("project", {}).get("name", "project"), "."))
        lock = lock_path.read_text(encoding="utf-8") if (root / "uv.lock").is_file() else ""
        wheel_requirements = []
        project_requirement = None
        built = set()
        for index, (name, relative) in enumerate(local):
            if name in built:
                continue
            built.add(name)
            source = (root / relative).resolve()
            if not source.is_relative_to(root.resolve()):
                # Native local dependencies outside the selected root are fixed
                # separately, rather than letting installation read a live tree.
                author = (Path(project["directory"]) / relative).resolve(strict=True)
                material = await asyncio.to_thread(snapshot_project, ctx.data_dir, str(author))
                captured.setdefault("localDependencies", []).append({"name": name, **material})
                source = Path(material["snapshotPath"])
            metadata = tomllib.loads((source / "pyproject.toml").read_text(encoding="utf-8")) if (source / "pyproject.toml").is_file() else {}
            if relative == "." and "build-system" not in metadata:
                continue
            wheels = directory / f"local-wheels-{index}"
            await run(["build", "--wheel", "--python", fixed_python, "--out-dir", str(wheels), str(source)], source, f"build-{index}")
            wheel = next(wheels.glob("*.whl"))
            requirement = f"{name} @ {wheel.resolve().as_uri()} --hash=sha256:{hashlib.sha256(wheel.read_bytes()).hexdigest()}"
            wheel_requirements.append(requirement)
            if relative == ".":
                project_requirement = requirement
        if not (root / "uv.lock").is_file():
            overrides = directory / "local-wheel-overrides.txt"
            overrides.write_text("\n".join(wheel_requirements) + "\n", encoding="utf-8")
            await run(["pip", "compile", "pyproject.toml", "--no-sources", "--overrides", str(overrides), "--generate-hashes", "--python", fixed_python, "--output-file", str(lock_path)], root, "resolve-lock")
            lock = lock_path.read_text(encoding="utf-8")
            # pip compile resolves dependency names; the original project itself
            # remains installable for its entry points and package resources.
            installed_names = {line.split(" @ ", 1)[0].strip() for line in lock.splitlines() if " @ " in line}
            wheel_requirements = [line for line in wheel_requirements if line == project_requirement and line.split(" @ ", 1)[0] not in installed_names]
        lock += "\n" + "\n".join(wheel_requirements) + "\n"
    else:
        original = False
        lock_path = baseline
        lock = baseline.read_text(encoding="utf-8")
    # Bundled wheels stay relocatable with the repository. Hashes still verify
    # their exact bytes; upstream and patched wheels remain separately retained.
    lock = bundled_wheels(lock, RUNTIME)
    if lock_path.parent == directory:
        # Keep the actual installation lock, including fixed local wheels and
        # their hashes, beside the original uv.lock/pyproject source snapshot.
        lock_path.write_text(lock, encoding="utf-8")
    return lock, {"kind": "original-project" if original else "bundled-research", "sourceLock": str(lock_path), "pythonVersion": version, "engine": project.get("engine", project.get("workbench"))}
