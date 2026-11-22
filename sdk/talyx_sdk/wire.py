"""Frames between the Host and a module process: a small envelope plus the body bytes, passed on untouched.

A frame is ``<u32 envelope length><u32 body length><envelope JSON><body JSON>``. The Host reads only envelopes.
JSON is orjson. orjson writes NaN and Infinity as ``null`` and big integers as they are, so the sender checks its
own output (``encode``): integers beyond JavaScript's safe range and non-finite floats are ``invalid_output``.
"""

from __future__ import annotations

import asyncio
import math
import struct
from typing import Any

import orjson

from .errors import ContractError
from .i18n import platform_t

HEADER = struct.Struct("<II")
# One frame carries at most this much; larger results go as a file reference.
MAX_BODY = 16 * 1024 * 1024
MAX_SAFE_INTEGER = 2**53 - 1


def _default(value):
    from .columns import TextColumn
    if type(value) is TextColumn:
        return orjson.Fragment(value.text)  # checked already where it was declared
    if type(value).__module__ == "numpy":
        if value.dtype.kind == "f":
            import numpy as np
            if not np.isfinite(value).all():
                raise TypeError("The output holds a NaN or infinite number")
        if value.dtype.kind in "iu" and value.size and (
            int(value.min()) < -MAX_SAFE_INTEGER or int(value.max()) > MAX_SAFE_INTEGER
        ):
            raise TypeError("Integer exceeds the JavaScript safe range")
        return orjson.Fragment(orjson.dumps(value, option=orjson.OPT_SERIALIZE_NUMPY))
    raise TypeError(f"{type(value).__name__} is not JSON")


def encode(value: Any, *, checked: bool = False) -> bytes:
    """JSON bytes of ``value``; ``checked`` means a declared type with finite floats already validated it."""
    try:
        data = orjson.dumps(value, option=orjson.OPT_STRICT_INTEGER, default=_default)
    except orjson.JSONEncodeError as exc:
        raise ContractError("invalid_output", platform_t("The output is not JSON: {value}", value=exc.__cause__ or exc), execution="failed") from exc
    if not checked:
        finite(value)
    return data


_PLAIN = {str, int, bool, type(None)}


def finite(value: Any) -> None:
    """Raise ``invalid_output`` when a float in ``value`` is NaN or infinite (orjson would write null)."""
    stack = [value]
    while stack:
        item = stack.pop()
        kind = type(item)
        if kind is float:
            if not math.isfinite(item):
                raise ContractError("invalid_output", platform_t("The output holds a NaN or infinite number"), execution="failed")
        elif kind is dict or kind is list or kind is tuple:
            values = item.values() if kind is dict else item
            # Long lists of text (columns) are passed over at C speed when they hold nothing else.
            if not set(map(type, values)) <= _PLAIN:
                stack.extend(v for v in values if type(v) not in _PLAIN)



def decode(data: bytes | memoryview) -> Any:
    return orjson.loads(data)


def frame(envelope: dict, body: bytes = b"") -> bytes:
    head = orjson.dumps(envelope)
    if len(body) > MAX_BODY:
        raise ContractError("too_large", platform_t("A frame carries at most {MAX_BODY} bytes; this one has {value}. Page the result or hand over a file reference", MAX_BODY=MAX_BODY, value=len(body)), {"bytes": len(body)}, execution="failed")
    return HEADER.pack(len(head), len(body)) + head + body


async def read_frame(reader: asyncio.StreamReader) -> tuple[dict, bytes]:
    head = await reader.readexactly(HEADER.size)
    envelope_size, body_size = HEADER.unpack(head)
    data = await reader.readexactly(envelope_size + body_size)
    return orjson.loads(data[:envelope_size]), data[envelope_size:]


SOCKET_BUFFER = 4 << 20


def tune(writer) -> None:
    """Large socket buffers on a frame connection. With Windows' defaults a page of bars (hundreds of KB) waits on
    the socket now and then (p99 ~25 ms instead of ~9 ms over two hops)."""
    import socket
    sock = writer.get_extra_info("socket")
    if sock is not None:
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_SNDBUF, SOCKET_BUFFER)
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_RCVBUF, SOCKET_BUFFER)
