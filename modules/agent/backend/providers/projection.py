"""Normalize provider output into text, sources and preserved native search metadata."""

import hashlib
from datetime import UTC, datetime
from urllib.parse import urlsplit


def safe_source(url):
    try:
        parsed = urlsplit(url)
        return parsed.scheme in {"http", "https"} and bool(parsed.hostname) and parsed.username is None and parsed.password is None
    except ValueError:
        return False


def project_message(message):
    sources = {}

    def collect(value):
        if isinstance(value, list):
            for item in value:
                collect(item)
        elif isinstance(value, dict):
            if value.get("type") in {"tool_call", "tool_use", "function"}:
                return
            url = value["url"] if "url" in value else value["uri"] if "uri" in value else None
            if isinstance(url, str) and safe_source(url):
                sources[url] = {"id": "source_" + hashlib.sha256(url.encode()).hexdigest()[:24], "url": url,
                                "title": value["title"] if "title" in value else url,
                                "publishedAt": None, "datePrecision": None}
            for item in value.values():
                if isinstance(item, (list, dict)):
                    collect(item)

    blocks = message.content_blocks
    collect(blocks)
    collect(message.response_metadata)
    text = "\n".join(block["text"] for block in blocks if block["type"] == "text")
    grounding = message.response_metadata["grounding_metadata"] if "grounding_metadata" in message.response_metadata else None
    # Keep ranges/supports, native display material and raw citations for the presentation layer.
    return {"text": text, "sources": list(sources.values()), "contentBlocks": blocks,
            "provider": message.response_metadata["talyxProvider"] if "talyxProvider" in message.response_metadata else message.response_metadata.get("model_provider"),
            "groundingMetadata": grounding, "usage": message.usage_metadata,
            "observedAt": datetime.now(UTC).isoformat(),
            "rawUsage": message.response_metadata["rawUsage"] if "rawUsage" in message.response_metadata else None}
