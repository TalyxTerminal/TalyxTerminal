"""Call TALYX from code a module started (a script, a notebook, a model run).

Standard library only: a module copies this file next to the code it runs. The module asked the platform for a run
token (``ctx.run_token``) and started the process with ``TALYX_CALL_URL`` and ``TALYX_RUN_TOKEN``; every call is
recorded as ``code`` from that module and workspace, and the context the module attached reaches the handlers called.
``TALYX_RUN_CONTEXT``, when the module sets it, is what ``context()`` returns.
"""

import json
import os
import urllib.error
import urllib.request
from pathlib import Path

MAX_REQUEST_BYTES = 8 * 1024 * 1024
DEFAULT_TIMEOUT = 3600.0


def platform_t(text, **params):
    """This file runs on its own in notebooks, with the platform dictionary passed by the Host."""
    language = os.environ.get("TALYX_LANGUAGE", "en")
    if language != "en":
        path = json.loads(os.environ.get("TALYX_PLATFORM_LOCALES", "{}")).get(language)
        if path:
            text = json.loads(Path(path).read_text(encoding="utf-8")).get(text, text)
    return text.format(**params)


class CallError(RuntimeError):
    """The platform or the action's owner refused or failed the call."""

    def __init__(self, code, message, details=None):
        super().__init__(f"{code}: {message}")
        self.code, self.message, self.details = code, message, details


def context():
    """The JSON the starting module gave this run, if any."""
    return json.loads(os.environ.get("TALYX_RUN_CONTEXT") or "null")


def call(name, input=None, *, timeout=DEFAULT_TIMEOUT):
    """The JSON result of action ``name`` (``module.action``). ``timeout=None`` waits without limit."""
    url, token = os.environ.get("TALYX_CALL_URL"), os.environ.get("TALYX_RUN_TOKEN")
    if not url or not token:
        raise CallError("unavailable", platform_t("This process was not started by TALYX with a run token"))
    raw = json.dumps({"action": name, "input": {} if input is None else input}, allow_nan=False).encode("utf-8")
    if len(raw) > MAX_REQUEST_BYTES:
        raise ValueError(platform_t("The request exceeds 8 MiB"))
    request = urllib.request.Request(url, data=raw, method="POST", headers={
        "authorization": "Bearer " + token, "content-type": "application/json"})
    # No proxy: the Host is on this computer.
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    try:
        with opener.open(request, timeout=timeout) as response:
            return json.loads(response.read() or b"null")
    except urllib.error.HTTPError as failure:
        try:
            error = json.loads(failure.read())["error"]
        except (ValueError, KeyError, TypeError):
            raise CallError("unavailable", platform_t("TALYX answered {code}", code=failure.code)) from None
        raise CallError(error.get("code", "unavailable"), error.get("message", ""), error.get("details")) from None
    except urllib.error.URLError as failure:
        raise CallError("unavailable", platform_t("TALYX could not be reached: {reason}", reason=failure.reason)) from None
