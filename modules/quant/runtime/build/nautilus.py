"""Build Nautilus Trader wheels for the Quant runtime from fixed source archives.

Normal use never compiles: Quant installs the published wheels in ../wheels.
This entry is only for producing the next patched version.

  prepare  unpack the starting source into the fixed source tree, apply patch
           files and set the wheel version. The start is either a published
           version of this runtime, or "official:<version>": the official sdist
           (plus the Cargo patch crates and python/pyproject.toml it omits, from
           the release tag) listed in build/sources.json
  build    compile that tree with maturin; repeat freely while the patch changes
  publish  only once the patch is final: copy the wheel, source archive, patches
           and build record into the runtime; --lock also moves those locks to it
  adopt    move locks to an already published version

The source tree and Cargo target live at fixed paths under
output/native-build/nautilus. Re-preparing rewrites only files whose bytes
change, so Cargo recompiles only the crates a patch actually touches.
"""
from __future__ import annotations

import argparse
import base64
import csv
import hashlib
import importlib.util
import io
import json
import os
import re
import shutil
import subprocess
import sys
import tarfile
import time
import urllib.request
import zipfile
from pathlib import Path, PurePosixPath
from urllib.parse import quote

HERE = Path(__file__).resolve().parent
RUNTIME = HERE.parent
REPO = RUNTIME.parents[2]
WORK = REPO / "output/native-build/nautilus"
SOURCE = WORK / "source"
TARGET = WORK / "target"
STAGE = WORK / "stage"
CACHE = WORK / "cache"
WHEELS = WORK / "wheels"
TOOL_LOCK = HERE / "tools.json"
SOURCES = HERE / "sources.json"
LOCKS = ("strategy", "lp")
OFFICIAL = "official:"

# Cargo features compiled into every published wheel.
# Dropping unused adapters (betfair, redis, hypersync, ...) would shorten the
# build, but changes the native API surface: verify the
# trimmed set together with the next patch before publishing it.
FEATURES = [
    "extension-module",
    "arrow",
    "betfair",
    "high-precision",
    "mimalloc",
    "redis",
    "postgres",
    "defi",
    "hypersync",
    "tracing-bridge",
]
PROFILE = {"name": "release", "lto": False, "codegen_units": 16}


def digest(path, algorithm="sha256"):
    with Path(path).open("rb") as stream:
        return hashlib.file_digest(stream, algorithm).hexdigest()


def tree_digest(root):
    """One digest for every file path and byte in a source tree."""
    total = hashlib.sha256()
    count = 0
    for path in sorted(root.rglob("*")):
        if path.is_file():
            total.update(path.relative_to(root).as_posix().encode() + b"\0" + digest(path).encode() + b"\n")
            count += 1
    return {"files": count, "sha256": total.hexdigest()}


def sources():
    return json.loads(SOURCES.read_text(encoding="utf-8"))


def write_json(path, value):
    path.write_text(json.dumps(value, indent=2, ensure_ascii=False) + "\n", encoding="utf-8", newline="\n")


# --- fixed tools ---------------------------------------------------------------


def download(asset):
    cache = CACHE / "downloads"
    cache.mkdir(parents=True, exist_ok=True)
    path = cache / asset["filename"]
    if not path.is_file():
        partial = path.with_suffix(path.suffix + ".partial")
        print(f"Downloading fixed {asset['id']} {asset['version']}", flush=True)
        request = urllib.request.Request(asset["url"], headers={"User-Agent": "TALYX-local-build"})
        with urllib.request.urlopen(request, timeout=60) as response, partial.open("wb") as target:
            shutil.copyfileobj(response, target)
        if digest(partial, asset.get("algorithm", "sha256")) != asset["digest"]:
            raise RuntimeError(f"Tool download checksum differs: {asset['id']}")
        partial.replace(path)
    if digest(path, asset.get("algorithm", "sha256")) != asset["digest"]:
        raise RuntimeError(f"Cached tool checksum differs: {asset['id']}")
    return path


def microsoft_asset(asset):
    return asset["id"].lower().startswith("microsoft.")


def prepare_tools():
    """Unpack the pinned Rust, CMake, NASM and maturin; MSVC/SDK come from tools/native-build."""
    assets = json.loads(TOOL_LOCK.read_text(encoding="utf-8"))["archives"]
    tools = CACHE / "tools"
    tools.mkdir(parents=True, exist_ok=True)
    for asset in assets:
        if microsoft_asset(asset):
            continue
        archive = download(asset)
        mark = tools / (asset["id"] + ".json")
        if mark.is_file() and json.loads(mark.read_text(encoding="utf-8"))["digest"] == asset["digest"]:
            continue
        if asset["id"] in {"rustc", "cargo", "rust-std"}:
            with tarfile.open(archive) as package:
                for entry in package:
                    parts = Path(entry.name).parts
                    if not entry.isfile() or len(parts) < 3 or not (parts[1] in {"rustc", "cargo"} or parts[1].startswith("rust-std-")):
                        continue
                    target = (tools / "rust" / Path(*parts[2:])).resolve()
                    if not target.is_relative_to((tools / "rust").resolve()):
                        raise RuntimeError("Rust archive path escaped the fixed tools")
                    target.parent.mkdir(parents=True, exist_ok=True)
                    with package.extractfile(entry) as source, target.open("wb") as output:
                        shutil.copyfileobj(source, output)
        else:
            destination = tools / asset["id"]
            with zipfile.ZipFile(archive) as package:
                for entry in package.infolist():
                    if entry.is_dir():
                        continue
                    target = (destination / entry.filename).resolve()
                    if not target.is_relative_to(destination.resolve()):
                        raise RuntimeError("Tool archive path escaped the fixed tools")
                    target.parent.mkdir(parents=True, exist_ok=True)
                    with package.open(entry) as source, target.open("wb") as output:
                        shutil.copyfileobj(source, output)
        write_json(mark, asset)
    spec = importlib.util.spec_from_file_location("talyx_microsoft_tools", REPO / "tools/native-build/prepare.py")
    microsoft = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(microsoft)
    ms_tools = microsoft.prepare()
    # The shared Microsoft archives must also match this build's own tool lock.
    for asset in assets:
        if microsoft_asset(asset):
            path = ms_tools.parent / asset["filename"]
            if not path.is_file():
                path = download(asset)
            if digest(path, asset.get("algorithm", "sha256")) != asset["digest"]:
                raise RuntimeError(f"Microsoft tool differs from the fixed build lock: {asset['id']}")
    return tools, ms_tools, {asset["id"]: asset["version"] for asset in assets}


def environment(python, jobs):
    tools, microsoft, versions = prepare_tools()
    msvc = next((microsoft / "msvc/VC/Tools/MSVC").iterdir())
    sdk = microsoft / "microsoft.windows.sdk.cpp/c"
    sdk_lib = microsoft / "microsoft.windows.sdk.cpp.x64/c"
    compiler = msvc / "bin/Hostx64/x64"
    cmake = next((tools / "cmake").glob("*/bin/cmake.exe"))
    nasm = next((tools / "nasm").rglob("nasm.exe"))
    cargo = tools / "rust/bin/cargo.exe"
    maturin = next((tools / "maturin").rglob("maturin.exe"))
    # The stub generator is a native executable and must find this interpreter's
    # python313.dll, which is outside .venv on a uv-managed Windows install.
    python_base = Path(subprocess.check_output([str(python), "-c", "import sys; print(sys.base_prefix)"], text=True).strip())
    env = os.environ.copy()
    env.update({
        "PATH": os.pathsep.join(map(str, [compiler, tools / "rust/bin", cmake.parent, nasm.parent, sdk / "bin/10.0.26100.0/x64", Path(python).parent, python_base, env["PATH"]])),
        "INCLUDE": os.pathsep.join(map(str, [msvc / "include", *[sdk / "Include/10.0.26100.0" / name for name in ("ucrt", "shared", "um", "winrt")]])),
        "LIB": os.pathsep.join(map(str, [msvc / "lib/x64", sdk_lib / "ucrt/x64", sdk_lib / "um/x64"])),
        "CARGO_HOME": str(CACHE / "cargo-home"), "CARGO_TARGET_DIR": str(TARGET),
        "CARGO_BUILD_JOBS": str(jobs), "NUM_JOBS": str(jobs), "CMAKE_BUILD_PARALLEL_LEVEL": str(jobs),
        "PYO3_PYTHON": str(python), "PYTHON_SYS_EXECUTABLE": str(python),
        # Match maturin 1.15's native CPython environment, so the stub binary
        # does not invalidate the compiled extension dependency graph.
        "PYO3_ENVIRONMENT_SIGNATURE": "cpython-3.13-64bit", "PYO3_BUILD_EXTENSION_MODULE": "1",
        "OMP_NUM_THREADS": "1", "OPENBLAS_NUM_THREADS": "1",
        "CARGO_PROFILE_RELEASE_LTO": "false", "CARGO_PROFILE_RELEASE_CODEGEN_UNITS": str(PROFILE["codegen_units"]),
        # Keep Git (patching and any build script) from discovering the TALYX repository.
        "GIT_CEILING_DIRECTORIES": str(WORK),
    })
    receipt = {"tool_lock_sha256": digest(TOOL_LOCK), "rustc": versions["rustc"] + " (build/tools.json)",
               "compiler": f"MSVC {msvc.name} Hostx64/x64 (tools/native-build/assets.lock.json)",
               "maturin": versions["maturin"] + " (build/tools.json)", "jobs": jobs, "profile": PROFILE}
    return env, cargo, maturin, receipt


# --- commands ------------------------------------------------------------------


def _report(message):
    """Console diagnostics must never replace the command's build failure.

    Escape characters the console cannot encode; never reconfigure the caller's streams.
    """
    for stream in (sys.stdout, sys.stderr):
        try:
            encoding = getattr(stream, "encoding", None) or "utf-8"
            printable = message.encode(encoding, errors="backslashreplace").decode(encoding)
            print(printable, file=stream, flush=True)
            return
        except Exception:
            # A closed stream, broken pipe or custom output wrapper is secondary
            # to the persistent log and the exception returned to the caller.
            continue


def run(command, source, env, log):
    command = list(map(str, command))
    _report("Running " + " ".join(command))
    with log.open("w", encoding="utf-8") as output:
        result = subprocess.run(command, cwd=source, env=env, stdout=output, stderr=subprocess.STDOUT)
    if result.returncode:
        diagnostics = []
        try:
            tail = log.read_text(encoding="utf-8", errors="replace")[-10000:]
        except Exception as error:
            tail = ""
            diagnostics.append(f"Could not read failure log: {type(error).__name__}: {error}")
        receipt = log.parent / "build.json"
        try:
            if receipt.is_file():
                facts = json.loads(receipt.read_text(encoding="utf-8"))
                if facts.get("status") == "building":
                    facts.update(status="build_failed", error=f"Native command exited {result.returncode}",
                        returncode=result.returncode, failed_log=log.name, failed_command=command, failure_tail=tail)
                    receipt.write_text(json.dumps(facts, indent=2), encoding="utf-8")
        except Exception as error:
            diagnostics.append(f"Could not update build receipt {receipt}: {type(error).__name__}: {error}")
        message = f"Native command failed ({result.returncode}); see {log}"
        details = "\n".join([*diagnostics, *([tail] if tail else [])])
        if details:
            message += "\n" + details
        _report(message)
        raise RuntimeError(message)


def pack_stubs(wheel, source):
    """Add this DLL's generated typing files, preserving every compiled byte."""
    with zipfile.ZipFile(wheel) as archive:
        if archive.testzip() is not None:
            raise RuntimeError("The compiled wheel is damaged")
        entries = {name: archive.read(name) for name in archive.namelist()
                   if not name.startswith("nautilus_trader/adapters/dydx/")}
    record_name = next(name for name in entries if name.endswith(".dist-info/RECORD"))
    native = {name: hashlib.sha256(payload).hexdigest() for name, payload in entries.items() if name.endswith(".pyd")}
    stubs = [path for path in (source / "nautilus_trader").rglob("*.pyi")
             if not path.relative_to(source).as_posix().startswith("nautilus_trader/adapters/dydx/")]
    if not stubs or not any("class CurrencyPair" in path.read_text(encoding="utf-8") for path in stubs):
        raise RuntimeError("Generated native typing is absent")
    for path in stubs:
        entries[path.relative_to(source).as_posix()] = path.read_bytes()
    records = io.StringIO(newline="")
    writer = csv.writer(records, lineterminator="\n")
    for name, payload in sorted(entries.items()):
        if name != record_name:
            token = base64.urlsafe_b64encode(hashlib.sha256(payload).digest()).rstrip(b"=").decode()
            writer.writerow([name, "sha256=" + token, len(payload)])
    writer.writerow([record_name, "", ""])
    entries[record_name] = records.getvalue().encode()
    staged = wheel.with_suffix(".typing.whl")
    with zipfile.ZipFile(staged, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=6) as archive:
        for name, payload in sorted(entries.items()):
            archive.writestr(name, payload)
    with zipfile.ZipFile(staged) as archive:
        if archive.testzip() is not None or any(hashlib.sha256(archive.read(name)).hexdigest() != sha for name, sha in native.items()):
            raise RuntimeError("Typing packaging changed compiled native bytes")
    staged.replace(wheel)
    return {"generated_typing_files": len(stubs), "native_binaries_sha256": native,
            "packaging": "Maturin compiled wheel plus same-DLL Rust inventory typing; wheel RECORD regenerated"}


# --- source tree ---------------------------------------------------------------


def official_release(version):
    entry = sources()["official"]["releases"].get(version)
    if entry is None:
        raise ValueError(f"No official release {version} in build/sources.json; choose one of {sorted(sources()['official']['releases'])}")
    return entry


def official_overlay(release, destination):
    """The files the official sdist omits, from the release tag's commit, written as LF."""
    checkout = CACHE / "official" / release["tag"]
    if not (checkout / ".git").is_dir():
        if checkout.exists():
            shutil.rmtree(checkout)
        checkout.parent.mkdir(parents=True, exist_ok=True)
        git = ["git", "-c", "core.autocrlf=false", "-c", "advice.detachedHead=false"]
        subprocess.run([*git, "clone", "--quiet", "--depth", "1", "--branch", release["tag"], "--filter=blob:none", "--sparse",
                        "https://github.com/nautechsystems/nautilus_trader.git", str(checkout)], check=True)
        subprocess.run([*git, "sparse-checkout", "set", "patches", "python"], cwd=checkout, check=True)
    head = subprocess.run(["git", "rev-parse", "HEAD"], cwd=checkout, check=True, capture_output=True, text=True).stdout.strip()
    if head != release["commit"]:
        raise ValueError(f"Tag {release['tag']} is at {head}, not the fixed commit in build/sources.json")
    for name in ("patches", "python/pyproject.toml"):
        source = checkout / name
        target = destination / name
        if source.is_dir():
            shutil.copytree(source, target)
        else:
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(source, target)
    return tree_digest(destination)


def official_tree(version, destination):
    """Official sdist as published, plus the overlay, both checked against build/sources.json."""
    release = official_release(version)
    sdist = release["sdist"]
    archive = download({"id": "nautilus-sdist", "version": version, "filename": sdist["filename"], "url": sdist["url"], "digest": sdist["sha256"]})
    extract(archive, destination)
    overlay = CACHE / "official" / (release["tag"] + "-overlay")
    if overlay.exists():
        shutil.rmtree(overlay)
    overlay.mkdir(parents=True)
    found = official_overlay(release, overlay)
    if found != release["overlay"]:
        raise ValueError(f"The fixed overlay of {version} differs from build/sources.json: {found}")
    for path in sorted(overlay.rglob("*")):
        if path.is_file():
            target = destination / path.relative_to(overlay)
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(path, target)
    shutil.rmtree(overlay)


def base_record(base):
    """The sources.json entry a base resolves to: an official release or a published version."""
    if base.startswith(OFFICIAL):
        release = official_release(base.removeprefix(OFFICIAL))
        return {"version": base, "sha256": release["sdist"]["sha256"], "overlay": release["overlay"]["sha256"]}
    return {"version": base, "sha256": base_archive(base)[1]["sha256"]}


def base_archive(version):
    entry = sources()["archives"].get(version)
    if entry is None:
        raise ValueError(f"No published source archive for {version}; choose one of {sorted(sources()['archives'])}")
    archive = RUNTIME / "upstream" / entry["filename"]
    if not archive.is_file():
        raise FileNotFoundError(f"Missing source archive {archive.relative_to(REPO)}")
    if digest(archive) != entry["sha256"]:
        raise ValueError(f"Source archive differs from build/sources.json: {archive.name}")
    return archive, entry


def extract(archive, destination):
    """Unpack one source archive without its top-level version directory."""
    if destination.exists():
        shutil.rmtree(destination)
    destination.mkdir(parents=True)
    root = destination.resolve()
    with tarfile.open(archive, "r:gz") as package:
        for member in package:
            parts = PurePosixPath(member.name).parts
            if not member.isfile() or len(parts) < 2:
                continue
            target = destination.joinpath(*parts[1:])
            if not target.resolve().is_relative_to(root):
                raise ValueError("Source archive member escapes the build directory: " + member.name)
            target.parent.mkdir(parents=True, exist_ok=True)
            with package.extractfile(member) as source, target.open("wb") as output:
                shutil.copyfileobj(source, output)


def set_version(source, version):
    """The builder owns the wheel version; patches should not edit it."""
    for name in ("pyproject.toml", "python/pyproject.toml"):
        path = source / name
        text = path.read_bytes().decode("utf-8")
        updated, count = re.subn(r'(?m)^version = "[^"]+"', f'version = "{version}"', text, count=1)
        if count != 1:
            raise ValueError(f"No project version line in {name}")
        path.write_bytes(updated.encode("utf-8"))


def patch_bytes(path):
    """Patch text as LF: the sources are LF, while a Windows checkout may give the file CRLF."""
    return Path(path).read_bytes().replace(b"\r\n", b"\n")


def stage(base, patches, version, destination=STAGE):
    if base.startswith(OFFICIAL):
        official_tree(base.removeprefix(OFFICIAL), destination)
    else:
        extract(base_archive(base)[0], destination)
    env = {**os.environ, "GIT_CEILING_DIRECTORIES": str(destination.parent)}
    for patch in patches:
        result = subprocess.run(["git", "-c", "core.autocrlf=false", "apply", "--whitespace=nowarn", "-"],
                                input=patch_bytes(patch), cwd=destination, env=env, capture_output=True)
        if result.returncode:
            raise RuntimeError(f"Patch does not apply to {base}: {patch}\n{result.stderr.decode(errors='replace')}")
    set_version(destination, version)
    members = (destination / "Cargo.toml").read_text(encoding="utf-8")
    if '"crates/pyo3"' not in members:
        raise RuntimeError("The prepared source is not the complete Nautilus workspace")
    return destination


def sync(staged, source):
    """Make source byte-identical to staged, rewriting only files that differ.

    Untouched files keep their timestamps, which is what lets Cargo skip
    every crate a patch does not change.
    """
    source.mkdir(parents=True, exist_ok=True)
    wanted, changed = set(), []
    for path in sorted(staged.rglob("*")):
        if not path.is_file():
            continue
        relative = path.relative_to(staged)
        wanted.add(relative)
        destination = source / relative
        payload = path.read_bytes()
        if destination.is_file() and destination.read_bytes() == payload:
            continue
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_bytes(payload)
        changed.append(relative.as_posix())
    removed = []
    for path in sorted(source.rglob("*"), reverse=True):
        relative = path.relative_to(source)
        if path.is_file() and relative not in wanted:
            path.unlink()
            removed.append(relative.as_posix())
        elif path.is_dir() and not any(path.iterdir()):
            path.rmdir()
    return changed, removed


def patch_record(path):
    path = Path(path).resolve()
    shown = path.relative_to(REPO).as_posix() if path.is_relative_to(REPO) else str(path)
    return {"file": shown, "sha256": hashlib.sha256(patch_bytes(path)).hexdigest()}


def prepare(args):
    patches = [Path(patch).resolve() for patch in args.patch]
    for patch in patches:
        if not patch.is_file():
            raise FileNotFoundError(patch)
    staged = stage(args.base, patches, args.version)
    changed, removed = sync(staged, SOURCE)
    shutil.rmtree(staged)
    prepared = {"base": args.base, "version": args.version, "patches": [patch_record(patch) for patch in patches],
                "tree": tree_digest(SOURCE), "changedFiles": len(changed), "removedFiles": len(removed)}
    write_json(WORK / "prepared.json", prepared)
    print(json.dumps({**prepared, "changed": changed[:50], "removed": removed[:50]}, indent=2), flush=True)


def build(args):
    prepared = json.loads((WORK / "prepared.json").read_text(encoding="utf-8"))
    python = Path(sys.executable)
    if sys.version_info[:2] != (3, 13) or sys.maxsize <= 2**32:
        raise RuntimeError("Run the build with the repository's 64-bit CPython 3.13 (.venv)")
    env, cargo, maturin, tools = environment(python, args.jobs)
    version = prepared["version"]
    tree = tree_digest(SOURCE)
    started = time.monotonic()
    receipt = {"status": "building", "version": version, "base": prepared["base"], "patches": prepared["patches"],
               "tree": tree, "tools": tools, "features": FEATURES}
    write_json(WORK / "build.json", receipt)
    run([cargo, "fetch", "--locked"], SOURCE, env, WORK / "cargo-fetch.log")
    env["CARGO_NET_OFFLINE"] = "true"
    WHEELS.mkdir(parents=True, exist_ok=True)
    for old in WHEELS.glob("*.whl"):
        old.unlink()
    command = [maturin, "build", "--release", "--locked", "--interpreter", python, "--out", WHEELS, "--features", ",".join(FEATURES)]
    run(command, SOURCE, env, WORK / "compile.log")
    if tree_digest(SOURCE) != tree:
        raise RuntimeError("The source tree changed during compilation; prepare and build again")
    wheel = WHEELS / f"nautilus_trader-{version}-cp313-cp313-win_amd64.whl"
    # Generate typing from this exact DLL's Rust inventory, outside the source
    # tree, so no second cargo build can alter PyO3 fingerprints.
    uv = shutil.which("uv")
    if not uv:
        raise RuntimeError("uv is required to generate the native typing files")
    stub_env = WORK / "stub-env"
    stub_python = stub_env / "Scripts/python.exe"
    run([uv, "venv", "--allow-existing", stub_env, "--python", python], WORK, env, WORK / "stub-env.log")
    run([uv, "pip", "install", "--offline", "--no-deps", "--reinstall", "--python", stub_python, wheel], WORK, env, WORK / "stub-install.log")
    stub_source = WORK / "stub-source"
    if stub_source.exists():
        shutil.rmtree(stub_source)
    manifest = stub_source / "python/pyproject.toml"
    manifest.parent.mkdir(parents=True)
    # Upstream's python/pyproject.toml puts the package beside it; the typing is written next to
    # the source tree's own nautilus_trader directory, one level up.
    text = (SOURCE / "python/pyproject.toml").read_bytes().decode("utf-8")
    if text.count('python-source = "."') != 1:
        raise RuntimeError("python/pyproject.toml has no single python-source line to redirect")
    manifest.write_bytes(text.replace('python-source = "."', 'python-source = ".."').encode("utf-8"))
    run([stub_python, "-B", "-c", "import sys; from nautilus_trader import _libnautilus; _libnautilus._generate_type_stubs(sys.argv[1])", manifest],
        WORK, {**env, "CARGO_MANIFEST_DIR": str(stub_source / "crates/pyo3")}, WORK / "stubs.log")
    receipt["typing"] = pack_stubs(wheel, stub_source)
    with zipfile.ZipFile(wheel) as package:
        native = next(name for name in package.namelist() if name.endswith(".pyd"))
        receipt["native_sha256"] = hashlib.sha256(package.read(native)).hexdigest()
    shown = {str(maturin): "maturin", str(python): "<CPython 3.13>", str(WHEELS): "<wheels>"}
    receipt.update(status="built", command=[shown.get(str(part), str(part)) for part in command],
                   wheel={"filename": wheel.name, "sha256": digest(wheel), "byteSize": wheel.stat().st_size},
                   elapsed_seconds=round(time.monotonic() - started, 2))
    write_json(WORK / "build.json", receipt)
    print(json.dumps({key: receipt[key] for key in ("status", "version", "wheel", "native_sha256", "elapsed_seconds")}, indent=2), flush=True)


def publish(args):
    """Publish a finished patch: new version number, wheel, source, records and lock."""
    built = json.loads((WORK / "build.json").read_text(encoding="utf-8"))
    if built.get("status") != "built":
        raise RuntimeError("No completed build; run prepare and build first")
    version = built["version"]
    catalogue = sources()
    if version in catalogue["archives"] or version == built["base"]:
        raise ValueError(f"{version} is already published; prepare the next patch with a new --version")
    wheel = WHEELS / built["wheel"]["filename"]
    if digest(wheel) != built["wheel"]["sha256"]:
        raise ValueError("The built wheel changed after its build")
    if tree_digest(SOURCE) != built["tree"]:
        raise ValueError("The source tree changed after the build; build again before publishing")
    # The patch is final only if base + recorded patch files reproduce the compiled tree exactly.
    check = stage(built["base"], [REPO / patch["file"] for patch in built["patches"]], version, WORK / "publish-check")
    reproduced = tree_digest(check)
    shutil.rmtree(check)
    if reproduced != built["tree"]:
        raise ValueError("The compiled tree is not base + recorded patches; regenerate the patch file, prepare and build again")
    published = RUNTIME / "wheels" / wheel.name
    if published.exists():
        raise ValueError(f"{published.name} already exists in the runtime")
    shutil.copyfile(wheel, published)
    archive = RUNTIME / "upstream" / f"nautilus-trader-{version}-source.tar.gz"
    with tarfile.open(archive, "w:gz") as package:
        package.add(SOURCE, arcname=f"nautilus_trader-{version}")
    patches = []
    for patch in built["patches"]:
        destination = RUNTIME / "patches" / f"nautilus-trader-{version}-{Path(patch['file']).name}"
        destination.write_bytes(patch_bytes(REPO / patch["file"]))
        patches.append({"file": destination.relative_to(RUNTIME).as_posix(), "sha256": patch["sha256"]})
    record = {"version": version, "base": base_record(built["base"]), "patches": patches,
              "source": archive.relative_to(RUNTIME).as_posix(), "source_sha256": digest(archive),
              "tools": built["tools"], "features": built["features"], "command": built["command"],
              "typing": built["typing"], "native_sha256": built["native_sha256"],
              "wheel": wheel.name, "sha256": built["wheel"]["sha256"]}
    write_json(RUNTIME / "patches" / f"nautilus-trader-{version}-build.json", record)
    catalogue["archives"][version] = {"filename": archive.name, "sha256": record["source_sha256"],
                                      "byteSize": archive.stat().st_size, "base": built["base"],
                                      "patches": [patch["file"] for patch in patches], "locks": args.lock}
    write_json(SOURCES, catalogue)
    adopt_locks(version, args.lock)
    print(json.dumps({"published": version, "wheel": published.name, "locks": args.lock}, indent=2))


def adopt_locks(version, names):
    """Point the named locks and identities at a published version."""
    record = json.loads((RUNTIME / "patches" / f"nautilus-trader-{version}-build.json").read_text(encoding="utf-8"))
    published = RUNTIME / "wheels" / record["wheel"]
    archive = RUNTIME / "upstream" / Path(record["source"]).name
    with tarfile.open(archive, "r:gz") as package:
        source_files = sum(member.isfile() for member in package.getmembers())
    native = {"nautilus_trader/_libnautilus.cp313-win_amd64.pyd": record["native_sha256"]}
    for name in names:
        requirements = RUNTIME / f"{name}.in"
        lines = requirements.read_text(encoding="utf-8").splitlines()
        if not lines or not lines[0].startswith("nautilus-trader @ "):
            raise ValueError(f"{requirements.name} must start with the nautilus-trader wheel line")
        lines[0] = "nautilus-trader @ file:./" + quote(published.relative_to(REPO).as_posix(), safe="/")
        requirements.write_text("\n".join(lines) + "\n", encoding="utf-8", newline="\n")
        lock = RUNTIME / f"{name}.lock"
        subprocess.run([shutil.which("uv"), "pip", "compile", requirements.relative_to(REPO).as_posix(), "--python-version", "3.13.13",
                        "--generate-hashes", "--output-file", lock.relative_to(REPO).as_posix()], cwd=REPO, check=True)
        identity_path = RUNTIME / f"{name}.identity.json"
        identity = json.loads(identity_path.read_text(encoding="utf-8"))
        identity.update(version=version, baseVersion=record["base"]["version"], sourceCommit=sources()["official"]["releases"][version.split("+")[0]]["commit"],
                        features=record["features"], nativeBinary=native,
                        wheel={"filename": published.name, "sha256": record["sha256"], "byteSize": published.stat().st_size},
                        sourceArchive={"filename": archive.name, "sha256": record["source_sha256"],
                                       "byteSize": archive.stat().st_size, "sourceFiles": source_files},
                        validationPending=["acceptance of the published wheel"], acceptanceState="validation_pending")
        if "native_binary" in identity:
            identity["native_binary"] = native
        write_json(identity_path, identity)


def adopt(args):
    adopt_locks(args.version, args.lock)
    print(json.dumps({"adopted": args.version, "locks": args.lock}, indent=2))


def tools(_args):
    _, _, _, receipt = environment(Path(sys.executable), os.cpu_count() or 1)
    print(json.dumps(receipt, indent=2))


def main():
    global WORK, SOURCE, TARGET, STAGE, WHEELS
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument('--work-dir', type=Path, default=WORK, help='Independent preparation/build/publication directory; immutable download cache is shared.')
    parser.add_argument('--target-dir', type=Path, help='Cargo artifact cache; Cargo serializes builds sharing this directory.')
    commands = parser.add_subparsers(dest="command", required=True)
    step = commands.add_parser("prepare", help="unpack a published source, apply patches, set the version")
    step.add_argument("--base", required=True, help="published version or official:<version> to start from, e.g. official:2.0.0rc8")
    step.add_argument("--version", required=True, help="version the wheel will carry, e.g. 2.0.0rc8+talyx.18")
    step.add_argument("--patch", action="append", default=[], help="patch file (git diff format), relative to the source root")
    step.set_defaults(handler=prepare)
    step = commands.add_parser("build", help="compile the prepared source tree")
    step.add_argument("--jobs", type=int, default=os.cpu_count() or 1, help="parallel compiler jobs (default: all CPUs)")
    step.set_defaults(handler=build)
    step = commands.add_parser("publish", help="publish a final patch as a new runtime version")
    step.add_argument("--lock", action="append", choices=LOCKS, default=[],
                      help="runtime lock that adopts the new wheel right away; repeat to share one wheel across locks")
    step.set_defaults(handler=publish)
    step = commands.add_parser("adopt", help="move runtime locks to an already published version")
    step.add_argument("--version", required=True)
    step.add_argument("--lock", required=True, action="append", choices=LOCKS)
    step.set_defaults(handler=adopt)
    step = commands.add_parser("tools", help="download and check the fixed toolchain only")
    step.set_defaults(handler=tools)
    args = parser.parse_args()
    WORK = args.work_dir.resolve()
    SOURCE, STAGE, WHEELS = WORK / 'source', WORK / 'stage', WORK / 'wheels'
    TARGET = args.target_dir.resolve() if args.target_dir else WORK / 'target'
    WORK.mkdir(parents=True, exist_ok=True)
    args.handler(args)


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    main()
