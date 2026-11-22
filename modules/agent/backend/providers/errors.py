"""Provider failures with the diagnostic message, never raw bodies or credentials."""

import re

import aiohttp
import anthropic
import httpx
import httpx2
import ollama
import openai
from google.genai.errors import APIError as GoogleAPIError
from langchain_google_genai.chat_models import ChatGoogleGenerativeAIError
from talyx_sdk.i18n import t
from talyx_sdk.network import NetworkConfigurationError, NetworkRouteChanged


def transport_error(error):
    return error.__cause__ if isinstance(error, ChatGoogleGenerativeAIError) and isinstance(error.__cause__, GoogleAPIError) else error


def provider_error(error):
    error = transport_error(error)
    if isinstance(error, (openai.AuthenticationError, anthropic.AuthenticationError)):
        return {"state": "failed", "code": "authentication_failed", "message": t("The provider rejected this API key")}
    if isinstance(error, (openai.RateLimitError, anthropic.RateLimitError)):
        return {"state": "failed", "code": "quota_exceeded", "message": t("The provider rate or spending quota was reached")}
    if isinstance(error, (openai.BadRequestError, anthropic.BadRequestError)):
        body = error.body
        if isinstance(body, dict) and isinstance(body.get("error"), dict):
            body = body["error"]
        reason = body.get("message") if isinstance(body, dict) else None
        message = reason if isinstance(reason, str) else "No diagnostic message was supplied"
        for header in ("authorization", "x-api-key", "api-key"):
            secret = error.request.headers.get(header)
            if secret:
                message = message.replace(secret, "[redacted]")
                if header == "authorization" and " " in secret:
                    message = message.replace(secret.split(" ", 1)[1], "[redacted]")
        message = re.sub(r"\bsk-[A-Za-z0-9_-]+", "[redacted]", message)
        return {"state": "failed", "code": "provider_request_rejected",
                "message": t("Provider rejected the request (HTTP {status_code}): {value}", status_code=error.status_code, value=message[:2000])}
    if isinstance(error, (openai.APIStatusError, anthropic.APIStatusError)) and error.status_code == 402:
        return {"state": "failed", "code": "insufficient_balance", "message": t("The provider account has run out of balance; top it up, then try again")}
    if isinstance(error, (openai.APIStatusError, anthropic.APIStatusError)):
        return {"state": "failed", "code": "provider_error", "message": t("The provider returned an error (HTTP {status_code}); try again later", status_code=error.status_code)}
    if isinstance(error, NetworkRouteChanged):
        return {"state": "failed", "code": "network_route_changed", "message": t("The system network route changed; test again")}
    if isinstance(error, NetworkConfigurationError):
        return {"state": "failed", "code": "network_proxy_unsupported", "message": t("The system proxy configuration is unsupported")}
    if isinstance(error, GoogleAPIError):
        code = "authentication_failed" if error.code in {401, 403} else "quota_exceeded" if error.code == 429 else "provider_error"
        return {"state": "failed", "code": code, "message": t("The Google provider request failed; check this connection's configuration and quota")}
    return {"state": "failed", "code": "network_error", "message": t("The provider request failed; check this connection and network")}


TRANSPORT_ERRORS = (openai.APIError, anthropic.APIError, GoogleAPIError, ChatGoogleGenerativeAIError, httpx.HTTPError, httpx2.HTTPError,
                    aiohttp.ClientError, ollama.ResponseError, ConnectionError)
PROVIDER_ERRORS = (*TRANSPORT_ERRORS, NetworkConfigurationError, NetworkRouteChanged)


def request_rejected(error):
    """These responses confirm that the provider did not accept a billable request."""
    error = transport_error(error)
    return isinstance(error, (openai.AuthenticationError, anthropic.AuthenticationError,
                             openai.RateLimitError, anthropic.RateLimitError,
                             openai.BadRequestError, anthropic.BadRequestError)) or (
        isinstance(error, GoogleAPIError) and error.code in {400, 401, 403, 429})
