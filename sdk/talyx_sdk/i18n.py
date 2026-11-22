"""Words people read, in the language they chose.

English is the source: code writes the English text and ``t()`` looks it up in the owner's dictionary for the current
language; text the dictionary does not have stays English. Each owner (a module, or the platform) has one dictionary
per language, a JSON file named in its manifest under ``locales`` and shared by its frontend and backend::

    {"Fetching prices": "正在获取价格",
     "{done} of {total}": "{done} / {total}",
     "@context": {"price": {"Close": "收盘价"}}}

``{name}`` is a placeholder filled from the call's parameters; ``@context`` holds the texts whose translation depends
on where they are used (``tc("price", "Close")`` beside ``t("Close")`` on a button).

A module's process (and every process it starts) is set up from its environment: ``TALYX_LOCALES`` (the dictionary
files by language, as JSON), ``TALYX_PLATFORM_LOCALES`` (the platform's, for the SDK's own words: ``platform_t``) and
``TALYX_LANGUAGE``; the SDK follows the platform's language setting from then on and
keeps ``TALYX_LANGUAGE`` current, so a process started later starts in the language of the moment. A process started
earlier keeps the language it started in. The Host sets up its own (the platform's) dictionary.
"""

from __future__ import annotations

import json
import os
import re
from pathlib import Path

LANGUAGES = ("en", "zh-CN")
SOURCE = "en"
CONTEXT = "@context"
LANGUAGE_ENV, LOCALES_ENV, PLATFORM_LOCALES_ENV = "TALYX_LANGUAGE", "TALYX_LOCALES", "TALYX_PLATFORM_LOCALES"
PLACEHOLDER = re.compile(r"\{([A-Za-z_][A-Za-z0-9_]*)\}")

_files: dict[str, Path] = {}
_platform_files: dict[str, Path] = {}
_language = SOURCE
_messages: dict[str, str] = {}
_contexts: dict[str, dict[str, str]] = {}
_platform_messages: dict[str, str] = {}


def read_dictionary(path: Path) -> tuple[dict[str, str], dict[str, dict[str, str]]]:
    """A dictionary file's texts and context texts; ``ValueError`` (or ``OSError``) when it is not one."""
    value = json.loads(Path(path).read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError("a dictionary is a JSON object of English text to translation")
    contexts = value.pop(CONTEXT, {})
    if not isinstance(contexts, dict) or not all(isinstance(group, dict) for group in contexts.values()):
        raise ValueError(f"{CONTEXT} maps a context to an object of English text to translation")
    for text, translation in [*value.items(), *(item for group in contexts.values() for item in group.items())]:
        if not isinstance(translation, str):
            raise ValueError(f"the translation of {text!r} is not text")
        if set(PLACEHOLDER.findall(text)) != set(PLACEHOLDER.findall(translation)):
            raise ValueError(f"the translation of {text!r} does not have the same placeholders")
    return value, contexts


def configure(files: dict[str, str | Path], language: str, platform: dict[str, str | Path] | None = None) -> None:
    """Which dictionary files this process uses, by language (its owner's, and the platform's for the SDK's words; the
    same for the Host), and the language to use now."""
    global _files, _platform_files
    _files = {key: Path(path) for key, path in files.items()}
    _platform_files = {key: Path(path) for key, path in (files if platform is None else platform).items()}
    set_language(language)


def set_language(language: str) -> None:
    global _language, _messages, _contexts, _platform_messages
    if language not in LANGUAGES:
        raise ValueError(f"{language} is not one of {', '.join(LANGUAGES)}")
    path = _files.get(language) if language != SOURCE else None
    _messages, _contexts = read_dictionary(path) if path is not None else ({}, {})
    path = _platform_files.get(language) if language != SOURCE else None
    _platform_messages = read_dictionary(path)[0] if path is not None else {}
    _language = language
    os.environ[LANGUAGE_ENV] = language


def language() -> str:
    return _language


def _fill(text: str, params: dict) -> str:
    return PLACEHOLDER.sub(lambda match: str(params[match[1]]) if match[1] in params else match[0], text)


def t(text: str, /, **params) -> str:
    """``text`` (English) in the current language, with its ``{placeholders}`` filled from ``params``."""
    return _fill(_messages.get(text, text), params)


def tc(context: str, text: str, /, **params) -> str:
    """Like ``t``, for a text whose translation depends on where it is used (``tc("price", "Close")``)."""
    return _fill(_contexts.get(context, {}).get(text, text), params)


def platform_t(text: str, /, **params) -> str:
    """``t`` with the platform's dictionary: the SDK's own words, said in a module's process."""
    return _fill(_platform_messages.get(text, text), params)


def mark(text: str, /) -> str:
    """``text`` itself: marks a text kept to be translated later with ``t(value)`` (a table of labels built at import,
    before the language is known), so the checks find it."""
    return text


def _from_environment() -> None:
    files, platform = os.environ.get(LOCALES_ENV), os.environ.get(PLATFORM_LOCALES_ENV)
    if files or platform:
        configure(json.loads(files or "{}"), os.environ.get(LANGUAGE_ENV, SOURCE), json.loads(platform or "{}"))


_from_environment()
