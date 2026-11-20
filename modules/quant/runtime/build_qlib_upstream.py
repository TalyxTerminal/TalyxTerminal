"""One isolated Qlib build using the already available Microsoft tools."""
import hashlib
import json
import os
import shutil
import subprocess
import sys
import tarfile
import time
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
RUNTIME = Path(__file__).resolve().parent
OUT = ROOT / "output" / "quant-native-build"
OUT.mkdir(parents=True, exist_ok=True)

def fetch(url):
    request = urllib.request.Request(url, headers={"User-Agent": "TALYX-handoff"})
    return urllib.request.urlopen(request, timeout=60).read()

identity = json.loads((RUNTIME / "identity.json").read_text(encoding="utf-8"))
commit = identity["qlibCommit"]
source = OUT / "source"
archive = RUNTIME / "upstream" / "qlib-v0.9.8.tar.gz"
if not archive.exists():
    archive.write_bytes(fetch(f"https://codeload.github.com/microsoft/qlib/tar.gz/{commit}"))
assert hashlib.sha256(archive.read_bytes()).hexdigest() == identity["source"]["source_sha256"]
source.mkdir(exist_ok=True)
with tarfile.open(archive) as bundle:
    bundle.extractall(source, filter="data")
project = source / f"qlib-{commit}"
tool_root = Path(subprocess.check_output(
    [sys.executable, str(ROOT / "tools/native-build/prepare.py")],
    text=True,
).strip())
msvc = next((tool_root / "msvc/VC/Tools/MSVC").iterdir())
sdk = tool_root / "microsoft.windows.sdk.cpp/c"
sdk_lib = tool_root / "microsoft.windows.sdk.cpp.x64/c"
version = "10.0.26100.0"
compiler = msvc / "bin/Hostx64/x64"
assert (compiler / "cl.exe").is_file()
env = os.environ.copy()
env.update({
    "PATH": os.pathsep.join([str(compiler), str(sdk / "bin" / version / "x64"), env["PATH"]]),
    "INCLUDE": os.pathsep.join(map(str, [msvc / "include", *[sdk / "Include" / version / name for name in ("ucrt", "shared", "um", "winrt")]])),
    "LIB": os.pathsep.join(map(str, [msvc / "lib/x64", sdk_lib / "Lib" / version / "ucrt/x64", sdk_lib / "Lib" / version / "um/x64"])),
    "DISTUTILS_USE_SDK": "1", "MSSdk": "1",
    "SETUPTOOLS_SCM_PRETEND_VERSION_FOR_PYQLIB": "0.9.8",
    "OMP_NUM_THREADS": "1", "OPENBLAS_NUM_THREADS": "1",
})
# The existing SDK package uses its c/ucrt and c/um directories directly.
env["LIB"] = os.pathsep.join(map(str, [msvc / "lib/x64", sdk_lib / "ucrt/x64", sdk_lib / "um/x64"]))
started = time.monotonic()
command = [shutil.which("uv"), "build", "--wheel", "--python", "3.12", "--out-dir", str(OUT / "wheels"), str(project)]
with (OUT / "qlib-build.log").open("w", encoding="utf-8") as log:
    result = subprocess.run(command, cwd=ROOT, env=env, stdout=log, stderr=subprocess.STDOUT)
receipt = {"commit": commit, "version": "0.9.8", "source_sha256": hashlib.sha256(archive.read_bytes()).hexdigest(), "exit_code": result.returncode, "elapsed_seconds": round(time.monotonic()-started, 2), "compiler": f"MSVC {msvc.name} Hostx64/x64 (tools/native-build/assets.lock.json)", "wheel": []}
for path in (OUT / "wheels").glob("*.whl"):
    receipt["wheel"].append({"file": path.name, "sha256": hashlib.sha256(path.read_bytes()).hexdigest()})
(OUT / "qlib-build.json").write_text(json.dumps(receipt, indent=2), encoding="utf-8")
print(json.dumps(receipt), flush=True)
raise SystemExit(result.returncode)
