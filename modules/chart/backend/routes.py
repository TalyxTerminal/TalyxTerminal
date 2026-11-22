"""Chart's private routes, served under ``/m/chart/`` through the Host for Chart's own panel:

- ``POST /<path>``: bodies and responses as in ``wire.ROUTES``;
- ``WS /streams/chart/<chartId>``: one chart's stream (``wire.ChartItem``);
- ``WS /streams/workspace``: the workspace's alerts, watchlists, screens, templates and health (``wire.WorkspaceItem``).

The workspace is the one the Host says the window shows (``x-talyx-workspace``). A stream's messages are JSON items;
the last one is ``{"end": null}`` or ``{"end": <error>}``.
"""
from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator

import orjson
from fastapi import FastAPI, Request, WebSocket
from talyx_sdk import ContractError, wire
from talyx_sdk.i18n import t

from .wire import ROUTES


def _workspace(headers) -> str:
    workspace = headers.get("x-talyx-workspace")
    if not workspace:
        raise ContractError("invalid_input", t("Chart's panel works in a workspace; this request names none"))
    return workspace


async def _serve(socket: WebSocket, open_items) -> None:
    """Send a stream's items until it ends or the window goes; the window going cancels the stream."""
    await socket.accept()

    async def send() -> dict | None:
        try:
            items: AsyncIterator[dict] = await open_items()
            async for item in items:
                await socket.send_bytes(wire.encode(item))
        except ContractError as error:
            return error.to_dict()
        return None

    sending = asyncio.create_task(send())
    closed = asyncio.create_task(socket.receive())
    done, _ = await asyncio.wait({sending, closed}, return_when=asyncio.FIRST_COMPLETED)
    if sending in done:
        closed.cancel()
        await socket.send_bytes(orjson.dumps({"end": sending.result()}))
        await socket.close()
    else:
        sending.cancel()
        await asyncio.gather(sending, return_exceptions=True)


def router(app) -> FastAPI:
    routes = FastAPI()
    handlers = app.handlers()
    for path, (body_model, response_model) in ROUTES.items():
        handler = handlers[path]

        def endpoint_for(handler=handler, body_model=body_model):
            async def endpoint(body, request):
                return await handler(_workspace(request.headers), body)
            # Annotations as objects: FastAPI reads them to parse the body (postponed ones would be strings).
            endpoint.__annotations__ = {"body": body_model, "request": Request}
            return endpoint

        routes.add_api_route(path, endpoint_for(), methods=["POST"], response_model=response_model,
                             response_model_exclude_unset=False)

    @routes.exception_handler(ContractError)
    async def failed(_request, error: ContractError):
        from fastapi.responses import JSONResponse
        status = {"not_found": 404, "conflict": 409, "invalid_input": 400, "permission_denied": 403}.get(error.code, 500)
        return JSONResponse({"error": error.to_dict()}, status_code=status)

    async def chart_stream(socket: WebSocket, chart_id: str):
        workspace = _workspace(socket.headers)
        await _serve(socket, lambda: app.chart_stream(workspace, chart_id))

    async def workspace_stream(socket: WebSocket):
        workspace = _workspace(socket.headers)

        async def items():
            return app.workspace_stream(workspace)
        await _serve(socket, items)

    chart_stream.__annotations__ = {"socket": WebSocket, "chart_id": str}
    workspace_stream.__annotations__ = {"socket": WebSocket}
    routes.add_api_websocket_route("/streams/chart/{chart_id}", chart_stream)
    routes.add_api_websocket_route("/streams/workspace", workspace_stream)
    return routes
