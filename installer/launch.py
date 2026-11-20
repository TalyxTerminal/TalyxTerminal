"""Start the installed desktop with the bundled frontend and per-user storage."""

import argparse
import ctypes
import os
import subprocess
import sys
import traceback
from pathlib import Path

ROOT = Path(__file__).resolve().parent
NODE = ROOT / ".venv/Lib/site-packages/nodejs_wheel/node.exe"


def launch_arguments(argv):
    parser = argparse.ArgumentParser(add_help=False, allow_abbrev=False)
    parser.add_argument("--data-root", type=Path)
    args, desktop_args = parser.parse_known_args(argv)
    data = args.data_root or os.environ.get("TALYX_DATA_DIR") or Path(os.environ["LOCALAPPDATA"]) / "TALYX" / "Data"
    return Path(data).resolve(), desktop_args


def main(data):
    os.chdir(ROOT)
    os.environ.update(
        TALYX_DATA_DIR=str(data),
        TALYX_RELEASE_LAUNCHER=str(ROOT / "launch.py"),
        PATH=os.pathsep.join([str(NODE.parent), str(ROOT / ".venv/Scripts"), os.environ["PATH"]]),
        PYTHONPATH=os.pathsep.join([str(ROOT), str(ROOT / "sdk")]),
        PYTHONNOUSERSITE="1",
        PYTHONDONTWRITEBYTECODE="1",
        UV_CACHE_DIR=str(data / "cache/uv"),
        UV_PYTHON_INSTALL_DIR=str(data / "runtime/python"),
    )
    from talyx_sdk.jobs import Job

    from host.desktop.__main__ import main as desktop
    from host.desktop.instance import HostInstance

    if sys.argv[1:] == ["--uninstall"]:
        from host.desktop.settings import LoginStartup
        LoginStartup(data).configure(False)
        return
    if HostInstance.running(data):
        desktop()
        return
    backend_port = 8751
    with subprocess.Popen(
        [str(NODE), "preview.mjs", "5201", str(backend_port)],
        cwd=ROOT / "web", stdout=subprocess.PIPE, text=True, encoding="utf8",
        stderr=sys.stderr, creationflags=subprocess.CREATE_NO_WINDOW,
    ) as frontend:
        job = Job(frontend)
        try:
            url = frontend.stdout.readline().strip()
            if not url:
                raise RuntimeError(f"Frontend exited with status {frontend.wait()}")
            sys.argv.extend(["--ui-url", url, "--backend-port", str(backend_port)])
            desktop()
        finally:
            job.close()


if __name__ == "__main__":
    data, desktop_args = launch_arguments(sys.argv[1:])
    data.mkdir(parents=True, exist_ok=True)
    log = data / "desktop.log"
    # pythonw has no console. Supply real Windows standard handles to its Host and module children.
    if Path(sys.executable).name.lower() == "pythonw.exe":
        with log.open("a", encoding="utf8") as output:
            result = subprocess.run(
                [str(Path(sys.executable).with_name("python.exe")), "-B", "-X", "utf8",
                 __file__, *sys.argv[1:]], stdin=subprocess.DEVNULL, stdout=output, stderr=output,
                creationflags=subprocess.CREATE_NO_WINDOW,
            )
        if result.returncode:
            ctypes.windll.user32.MessageBoxW(None, f"TALYX could not start. See {log}", "TALYX", 16)
        sys.exit(result.returncode)
    try:
        sys.argv[1:] = desktop_args
        main(data)
    except Exception:
        traceback.print_exc()
        sys.exit(1)
