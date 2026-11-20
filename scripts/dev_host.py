"""Start the real Host on disposable data (a trial app): its own data, user files and third-party modules."""

import argparse
import asyncio
import logging
import os
import sys
from contextlib import asynccontextmanager
from pathlib import Path

import uvicorn

from host.app import ROOT, Host, HostConfig
from host.server import create_app, server_options


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--data-dir", type=Path, required=True)
    parser.add_argument("--port", type=int, default=8011)
    parser.add_argument("--ui-url", default="http://127.0.0.1:5174/")
    parser.add_argument("--workspace", help="Open this disposable workspace for backend-only MCP trials")
    parser.add_argument("--dev", action="store_true", help="every process reports where its event loop gets stuck")
    args = parser.parse_args()
    root = args.data_dir.resolve()
    if root == (ROOT / "data" / "store").resolve():
        parser.error("Choose a disposable data directory, not the daily data root")
    if args.dev:
        os.environ["TALYX_DEV"] = "1"
    logging.basicConfig(level=logging.INFO, format="%(asctime)s host %(levelname)s %(name)s: %(message)s")
    logging.getLogger("httpx").setLevel(logging.WARNING)
    if sys.platform == "win32":
        asyncio.set_event_loop_policy(asyncio.WindowsProactorEventLoopPolicy())
    host = Host(HostConfig(data_dir=root, user_dir=root, port=args.port, ui_url=args.ui_url))
    app = create_app(host)
    lifespan = app.router.lifespan_context

    @asynccontextmanager
    async def configured(application):
        async with lifespan(application):
            if args.workspace:
                await host.workspaces.open({"workspaceId": args.workspace, "name": args.workspace})
                print(f"TALYX MCP: http://127.0.0.1:{args.port}/mcp/workspaces/{args.workspace}", flush=True)
            yield

    app.router.lifespan_context = configured
    uvicorn.Server(uvicorn.Config(app, **server_options(args.port))).run()


if __name__ == "__main__":
    main()
