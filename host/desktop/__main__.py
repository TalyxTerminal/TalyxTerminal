"""Run the TALYX Windows desktop and its backend with one Python command."""

import argparse
import atexit
import gc
import os
import shutil
import subprocess
import time
from pathlib import Path
from urllib.error import URLError
from urllib.request import urlopen

from talyx_sdk.jobs import Job

from host.files import default_data_dir

from .app import ROOT, Desktop
from .instance import DesktopActivation, HostAlreadyRunning, HostInstance


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--ui-url", help="Use an already running UI server")
    parser.add_argument("--frontend-port", type=int, default=5173)
    parser.add_argument("--backend-port", type=int, default=8000)
    parser.add_argument("--profile")
    parser.add_argument("--workspace-id")
    parser.add_argument("--start-in-tray", action="store_true")
    parser.add_argument("--node", default=shutil.which("node"))
    args = parser.parse_args()
    # Python.NET does repeated full collections at exit. Keep the initial library graph out
    # of those scans; all desktop resources and managed wrappers are created after this point.
    gc.collect()
    gc.freeze()
    atexit.register(gc.unfreeze)
    existing = HostInstance.running(default_data_dir())
    if existing is not None:
        if DesktopActivation.activate(default_data_dir()):
            return
        if existing["port"] is None:
            raise HostAlreadyRunning("The existing Host has no desktop connection address")
        desktop = Desktop(existing["uiUrl"], existing["port"], profile=args.profile,
                          start_in_tray=args.start_in_tray, attach=True, data_root=default_data_dir())
        raise SystemExit(desktop.run(args.workspace_id))
    frontend = job = None
    try:
        ui_url = args.ui_url
        if ui_url is None:
            if args.node is None:
                raise RuntimeError("Node.js is not installed")
            ui_url = f"http://127.0.0.1:{args.frontend_port}/"
            frontend = subprocess.Popen(
                [
                    str(Path(args.node).resolve()),
                    str(ROOT / "web/node_modules/vite/bin/vite.js"),
                    "--host",
                    "127.0.0.1",
                    "--port",
                    str(args.frontend_port),
                    "--strictPort",
                ],
                cwd=ROOT / "web",
                env={**os.environ, "TALYX_DEV_BACKEND": f"http://127.0.0.1:{args.backend_port}"},
                creationflags=subprocess.CREATE_NO_WINDOW,
            )
            job = Job(frontend)
            deadline = time.monotonic() + 60
            while True:
                if frontend.poll() is not None:
                    raise RuntimeError(f"Desktop frontend exit code: {frontend.returncode}")
                try:
                    with urlopen(ui_url, timeout=2) as response:
                        if response.status != 200:
                            raise RuntimeError(f"Desktop frontend HTTP status: {response.status}")
                    break
                except URLError:
                    if time.monotonic() >= deadline:
                        raise TimeoutError("Desktop frontend did not listen") from None
                    time.sleep(0.1)
        desktop = Desktop(ui_url, args.backend_port, args.profile, start_in_tray=args.start_in_tray,
                          data_root=default_data_dir())
        raise SystemExit(desktop.run(args.workspace_id))
    finally:
        if job:
            job.close()
        if frontend:
            frontend.wait(timeout=30)


if __name__ == "__main__":
    main()
