"""Build the Windows installer from the committed checkout and the locked native release materials."""

import hashlib
import json
import os
import shutil
import subprocess
import tarfile
import tempfile
import tomllib
import urllib.request
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "output/installer"
INNO_URL = "https://api.nuget.org/v3-flatcontainer/tools.innosetup/6.7.3/tools.innosetup.6.7.3.nupkg"
INNO_HASH = "f780898e402ff80612cc8d9fcb8c6e02932bd1cb4c900ffdaa31f9341cfb49f4"
WEBVIEW_URL = "https://go.microsoft.com/fwlink/p/?LinkId=2124703"
UV_WHEEL = (
    "https://files.pythonhosted.org/packages/2a/06/bda383b338dd21bcd74c9b4ba47301315573fdb5aba46fe40835f8299482/"
    "uv-0.12.21-py3-none-win_amd64.whl#sha256=8c7c3eb32d84c711906dedd09df7379cf4d9f5bb84b8e58a1244ca0a8a1209d4"
)


def run(*args, **kwargs):
    subprocess.run(args, check=True, **kwargs)


def check_hash(path, expected):
    with path.open("rb") as stream:
        if hashlib.file_digest(stream, "sha256").hexdigest() != expected:
            raise ValueError(f"SHA-256 mismatch: {path}")


def build(work):
    app = work / "app"
    app.mkdir()
    commit = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=ROOT, text=True).strip()
    print(f"Packaging {commit}", flush=True)
    archive = work / "source.tar"
    run("git", "archive", "-o", archive, commit, "--", "host", "kernel", "modules", "sdk", "vocabulary",
        "contracts", "scripts/contracts", "web", "examples", "LICENSE", "README.md", "pyproject.toml",
        "uv.lock", ".python-version", cwd=ROOT)
    with tarfile.open(archive) as source:
        source.extractall(app, filter="data")

    # The large patched Nautilus wheel and its corresponding source are release attachments.
    runtime = Path("modules/quant/runtime")
    identity = json.loads((app / runtime / "strategy.identity.json").read_text(encoding="utf8"))
    attachments = []
    for field, folder in [("wheel", "wheels"), ("sourceArchive", "upstream")]:
        asset = identity[field]
        path = runtime / folder / asset["filename"]
        check_hash(ROOT / path, asset["sha256"])
        shutil.copy2(ROOT / path, app / path)
        attachments.append(app / path)

    base = Path(subprocess.check_output(
        ["uv", "python", "find", "--system", "--managed-python", "--no-project", "3.13"], text=True,
    ).strip()).parent
    shutil.copytree(base, app / "python",
                    ignore=shutil.ignore_patterns("__pycache__", "*.pyc", "site-packages"))
    env = {**os.environ, "UV_PROJECT_ENVIRONMENT": str(app / ".venv"), "UV_LINK_MODE": "copy"}
    run("uv", "venv", "--relocatable", "--python", app / "python/python.exe", app / ".venv", env=env, cwd=app)
    run("uv", "sync", "--locked", "--no-dev", "--no-editable", env=env, cwd=app)
    python = app / ".venv/Scripts/python.exe"
    run(python, "-I", "-c", "import pathlib, sys; assert pathlib.Path(sys.base_prefix).samefile(sys.argv[1])",
        app / "python", cwd=app)
    run("uv", "pip", "install", "--no-deps", "--python", python, UV_WHEEL, env=env, cwd=app)
    # basedpyright's locked runtime already contains Node and its licenses.
    node = app / ".venv/Lib/site-packages/nodejs_wheel/node.exe"

    web = app / "web"
    run(shutil.which("npm.cmd"), "ci", "--no-audit", "--no-fund", cwd=web)
    run(node, "scripts/typecheck.mjs", cwd=web)
    shutil.copy2(ROOT / "installer/frontend.mjs", web)
    run(node, "--max-old-space-size=4096", "frontend.mjs", cwd=web)
    (web / "frontend.mjs").unlink()
    shutil.copy2(ROOT / "installer/preview.mjs", web)
    shutil.copy2(ROOT / "installer/launch.py", app)

    package = work / "inno.zip"
    urllib.request.urlretrieve(INNO_URL, package)
    check_hash(package, INNO_HASH)
    with zipfile.ZipFile(package) as compiler:
        compiler.extractall(work / "inno")
    bootstrap = work / "MicrosoftEdgeWebview2Setup.exe"
    urllib.request.urlretrieve(WEBVIEW_URL, bootstrap)
    run("powershell.exe", "-NoProfile", "-Command",
        "$s=Get-AuthenticodeSignature -LiteralPath $env:TALYX_WEBVIEW_SETUP; "
        "if ($s.Status -ne 'Valid' -or $s.SignerCertificate.GetNameInfo('SimpleName',$false) "
        "-ne 'Microsoft Corporation') { throw 'Invalid Microsoft WebView2 signature' }",
        env={**os.environ, "TALYX_WEBVIEW_SETUP": str(bootstrap)})
    version = tomllib.loads((app / "pyproject.toml").read_text(encoding="utf8"))["project"]["version"]
    run(work / "inno/tools/ISCC.exe", "/Qp", f"/DPayload={app}", f"/DBuild={work}",
        f"/DVersion={version}", ROOT / "installer/setup.iss")
    for asset in attachments:
        shutil.copy2(asset, OUT / asset.name)
    installer = work / "TALYX-Setup-x64.exe"
    installer.replace(OUT / installer.name)
    print(f"{commit}: {OUT / installer.name} ({(OUT / installer.name).stat().st_size:,} bytes)", flush=True)


if __name__ == "__main__":
    OUT.mkdir(parents=True, exist_ok=True)
    temp = ROOT / "output/tmp"
    temp.mkdir(exist_ok=True)
    os.environ.update(TEMP=str(temp), TMP=str(temp), UV_CACHE_DIR=str(ROOT / "output/uv-cache"),
                      npm_config_cache=str(ROOT / "output/npm-cache"))
    with tempfile.TemporaryDirectory(prefix="build-", dir=OUT) as scratch:
        build(Path(scratch))
