"""The Quant dictionary passed to a research, strategy or notebook process at launch."""
import json
import os
import re
from pathlib import Path

_language = os.environ.get("TALYX_LANGUAGE", "en")
_path = json.loads(os.environ.get("TALYX_LOCALES") or "{}").get(_language)
_messages = json.loads(Path(_path).read_text(encoding="utf-8")) if _path and _language != "en" else {}
_messages.pop("@context", None)
_placeholder = re.compile(r"\{([A-Za-z_][A-Za-z0-9_]*)\}")


def t(text: str, /, **values) -> str:
    return _placeholder.sub(lambda match: str(values[match[1]]) if match[1] in values else match[0], _messages.get(text, text))


def mark(text: str, /) -> str:
    return text
