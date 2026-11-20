"""Bounded retries of one model/search request, never an entire business graph."""

import asyncio
import math
import time
from datetime import UTC, datetime
from email.utils import parsedate_to_datetime

import aiohttp
import anthropic
import httpx
import httpx2
import ollama
import openai
from google.genai.errors import APIError as GoogleAPIError
from talyx_sdk import ContractError

from .errors import TRANSPORT_ERRORS, transport_error


def delay(error, attempt, now):
    error = transport_error(error)
    headers = {}
    if isinstance(error, (openai.APIStatusError, anthropic.APIStatusError)):
        status, headers = error.status_code, error.response.headers
    elif isinstance(error, GoogleAPIError):
        status = error.code
        if error.response is not None:
            headers = error.response.headers
    elif isinstance(error, (httpx.HTTPStatusError, httpx2.HTTPStatusError)):
        status, headers = error.response.status_code, error.response.headers
    elif isinstance(error, aiohttp.ClientResponseError):
        status = error.status
        if error.headers is not None:
            headers = error.headers
    elif isinstance(error, ollama.ResponseError):
        status = error.status_code
    elif isinstance(error, (openai.APIConnectionError, anthropic.APIConnectionError,
            httpx.TransportError, httpx2.TransportError, aiohttp.ClientConnectionError, ConnectionError)):
        status = 503
    else:
        return None
    if status not in {429, 500, 502, 503, 504}:
        return None
    retry_after = headers.get("retry-after")
    if retry_after is not None:
        try:
            value = float(retry_after)
            return max(0.0, value) if math.isfinite(value) else None
        except ValueError:
            try:
                moment = parsedate_to_datetime(retry_after)
                if moment.tzinfo is None:
                    moment = moment.replace(tzinfo=UTC)
                return max(0.0, (moment - datetime.fromtimestamp(now, UTC)).total_seconds())
            except (ValueError, TypeError, OverflowError):
                return None
    return 0.5 * 2 ** attempt


async def retry_request(operation):
    """Three attempts, at most thirty seconds spent retrying; cancellation propagates."""
    deadline = time.monotonic() + 30
    for attempt in range(3):
        try:
            return await operation()
        except (*TRANSPORT_ERRORS, ContractError) as error:
            original = error.__cause__ if isinstance(error, ContractError) and error.code in {"search_failed", "quota_exceeded"} else error
            wait = delay(original, attempt, time.time())
            if attempt == 2 or wait is None or time.monotonic() + wait > deadline:
                raise
            await asyncio.sleep(wait)
