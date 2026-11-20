"""Generate the deep-learning locks: the research lock plus one torch build.

Run from the repository root:  python modules/quant/runtime/build_deep_locks.py [cpu cuda xpu]

The platform installs with `uv --no-config pip sync --require-hashes`, which only knows PyPI, so the
locks carry no index lines; packages PyPI lacks are written as one direct wheel URL with one hash.
"""
import re
import subprocess
import sys
import tempfile
import urllib.request
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[2]
VARIANTS = {
    "cpu": "https://download.pytorch.org/whl/cpu",
    "cuda": "https://download.pytorch.org/whl/cu132",
    "xpu": "https://download.pytorch.org/whl/xpu",
}


def run(command, **kw):
    return subprocess.run(command, capture_output=True, text=True, encoding="utf-8", errors="replace", cwd=ROOT, **kw)


def strip_indexes(text):
    return "".join(line for line in text.splitlines(True) if not line.startswith(("--index-url", "--extra-index-url", "--find-links")))


def direct(lock, index, python):
    """Rewrite packages PyPI lacks as direct wheel URLs until a dry-run sync resolves."""
    for _ in range(40):
        out = run(["uv", "--no-config", "pip", "sync", "--dry-run", "--python", python, "--require-hashes", str(lock)])
        text = out.stdout + out.stderr
        found = re.search(r"there is no version of ([A-Za-z0-9_.\-]+)==(\S+?)(?: and|,)", text)
        if not found:
            if out.returncode:
                raise SystemExit(f"{lock.name}: dry-run still fails\n{text[-1500:]}")
            return
        name, version = found.group(1), found.group(2)
        page = urllib.request.urlopen(f"{index}/{name.lower().replace('_', '-')}/", timeout=60).read().decode()
        stem = re.sub(r"[-.]", "_", name)
        files = re.findall(r'href="([^"#]*?/(' + re.escape(stem) + "-" + re.escape(version.replace("+", "%2B")) + r'-[^"#]*?\.whl))#sha256=([0-9a-f]+)', page, re.I)
        picks = [f for f in files if any(tag in f[1] for tag in ("cp312-cp312-win_amd64", "none-win_amd64", "none-any"))]
        if not picks:
            raise SystemExit(f"no Windows cp312 wheel for {name} {version} at {index}")
        href, _, digest = picks[0]
        url = href if href.startswith("http") else "https://download.pytorch.org" + href
        body = open(lock, encoding="utf-8").read()
        block = re.search(r"(?ms)^" + re.escape(name) + "==" + re.escape(version) + r" \\\n((?:    --hash=\S+(?: \\)?\n)+)", body)
        if not block:
            raise SystemExit(f"{name}=={version} not found in {lock.name}")
        body = body.replace(block.group(0), f"{name} @ {url} \\\n    --hash=sha256:{digest}\n")
        open(lock, "w", encoding="utf-8", newline="\n").write(body)
        print("direct:", name, version)


def main(names):
    with tempfile.TemporaryDirectory() as scratch:
        venv = Path(scratch) / "venv"
        made = run(["uv", "venv", "--python", "3.12", str(venv)])
        if made.returncode:
            raise SystemExit(made.stderr)
        python = str(venv / "Scripts" / "python.exe")
        for name in names:
            source = HERE / f"deep-{name}.in"
            lock = HERE / f"deep-{name}.lock"
            command = ["uv", "pip", "compile", str(source.relative_to(ROOT)).replace("\\", "/"), "--python-version", "3.12",
                       "--python-platform", "windows", "--generate-hashes", "--index", VARIANTS[name],
                       "--index-strategy", "unsafe-best-match", "-o", str(lock.relative_to(ROOT)).replace("\\", "/")]
            done = run(command)
            if done.returncode:
                raise SystemExit(done.stderr[-2000:])
            body = strip_indexes(open(lock, encoding="utf-8").read())
            open(lock, "w", encoding="utf-8", newline="\n").write(body)
            direct(lock, VARIANTS[name], python)
            print(name, lock.stat().st_size, "bytes")


if __name__ == "__main__":
    main(sys.argv[1:] or list(VARIANTS))
