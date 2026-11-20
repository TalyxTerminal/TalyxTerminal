"""``python -m host``: run TALYX's Host."""

from __future__ import annotations

import argparse
import asyncio
import logging
import os
import secrets
import sys
from pathlib import Path

from .app import ROOT, Host, HostConfig
from .server import run

DAILY_DATA = ROOT / "data" / "store"


def main() -> None:
    parser = argparse.ArgumentParser(description="Run the TALYX Host")
    parser.add_argument("--data-dir", default=os.environ.get("TALYX_DATA_DIR") or str(DAILY_DATA))
    parser.add_argument("--user-dir", default=None,
                        help="the user's files: default user/ with the daily data (data/store), else the data dir")
    parser.add_argument("--port", type=int, default=8000)
    parser.add_argument("--ui-url", default="http://127.0.0.1:5173/")
    parser.add_argument("--dev", action="store_true",
                        help="development: every process logs where its event loop gets stuck, and its lag each minute")
    parser.add_argument("--desktop", action="store_true",
                        help="run for the desktop app: apply sign-in startup and keep-awake from General settings, and "
                             "stop when the desktop closes this process's standard input")
    args = parser.parse_args()
    if args.dev:
        os.environ["TALYX_DEV"] = "1"
    data = Path(args.data_dir).resolve()
    user = Path(args.user_dir or (ROOT / "user" if data == DAILY_DATA.resolve() else data)).resolve()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s host %(levelname)s %(name)s: %(message)s")
    logging.getLogger("httpx").setLevel(logging.WARNING)
    if sys.platform == "win32":
        asyncio.set_event_loop_policy(asyncio.WindowsProactorEventLoopPolicy())
    desktop_token = secrets.token_urlsafe(32)
    instance = None
    if sys.platform == "win32":
        from .desktop.instance import HostInstance

        instance = HostInstance(data)   # one Host per data root; the desktop finds it here
        instance.acquire({"port": args.port, "uiUrl": args.ui_url, "desktopToken": desktop_token})
    try:
        run(Host(HostConfig(data_dir=data, user_dir=user, port=args.port, ui_url=args.ui_url,
                             desktop=args.desktop, desktop_token=desktop_token)), args.port, stop_on_stdin_close=args.desktop)
    finally:
        if instance is not None:
            instance.close()


if __name__ == "__main__":
    main()
