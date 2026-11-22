"""Words people read: what is translated, what is missing, and English still shown without the translator.

Per owner (the platform, each module): the texts its code gives the translator (``t``, ``t.c``, ``t.rich`` in
TypeScript; ``t``, ``tc``, ``mark`` in Python) and its declared words (manifest name, description and panel titles;
setting titles, descriptions, groups and choices; long-task titles; titles and names in what it provides), against its
dictionary for each language. Reported:

- missing: a text used or declared that the dictionary lacks;
- unused: a dictionary entry nothing uses;
- shown: English written for people but not given to the translator (JSX text and labels, error messages, activity
  titles, notes); a line marked ``i18n-ignore`` (TypeScript) or ``i18n: ignore`` (Python) is left out;
- dynamic: calls with a text the check cannot read (``t(label)``): their texts must be found some other way
  (``mark`` in Python; a literal ``t("...")`` where the table is built in TypeScript).

    python scripts/checks/check_i18n.py                  # a summary per owner
    python scripts/checks/check_i18n.py --owner <module> --details
    python scripts/checks/check_i18n.py --strict         # fails on any problem
"""

from __future__ import annotations

import argparse
import ast
import json
import re
import subprocess
import sys
from collections import defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "sdk"))
from talyx_sdk.i18n import CONTEXT, LANGUAGES, SOURCE, read_dictionary  # noqa: E402

PLATFORM_LOCALES = {"zh-CN": ROOT / "host/locales/zh-CN.json"}
SKIP_PARTS = {"node_modules", "__pycache__", "tests", "public", "runtime", "dist", "output"}
# A page reached only by its explicit URL while building TALYX, and a generated client: not shown to people using it.
SKIP_FILES = {"web/src/shell/DesignSystemPage.tsx", "sdk/ui/src/platform.tsx"}
PROSE = re.compile(r"[a-z]{2,}")
ERROR_CODE = re.compile(r"^[a-z][a-z0-9_]*$")
NOTE_KEYS = {"message", "note", "text", "title", "summary", "label", "detail", "hint"}
DESCRIPTOR_KEYS = {"title", "name", "label", "placeholder", "help"}


def owner_sources() -> dict[str, list[Path]]:
    owners: dict[str, list[Path]] = {"platform": []}
    for top in ("web/src", "sdk/ui/src", "host", "kernel", "sdk/talyx_sdk"):
        owners["platform"] += _files(ROOT / top)
    for directory in sorted((ROOT / "modules").iterdir()):
        if (directory / "talyx.module.json").exists():
            owners[directory.name] = _files(directory)
    return owners


def _files(directory: Path) -> list[Path]:
    found = []
    for path in directory.rglob("*"):
        relative = path.relative_to(ROOT).as_posix()
        if SKIP_PARTS & set(path.relative_to(ROOT).parts) or relative in SKIP_FILES or not path.is_file():
            continue
        if path.suffix in (".ts", ".tsx", ".py") and not re.search(r"\.(test|spec)\.|\.d\.ts$|(^|/)test_", relative):
            found.append(path)
    return found


def dictionaries(owner: str) -> dict[str, Path | None]:
    if owner == "platform":
        files = PLATFORM_LOCALES
    else:
        manifest = json.loads((ROOT / "modules" / owner / "talyx.module.json").read_text(encoding="utf-8"))
        files = {language: ROOT / "modules" / owner / path for language, path in manifest.get("locales", {}).items()}
    return {language: files.get(language) for language in LANGUAGES if language != SOURCE}


def label(value: str) -> str:
    words = re.sub(r"[-_]+", " ", value)
    return words[:1].upper() + words[1:]


def declared(owner: str) -> set[tuple[str | None, str]]:
    """The owner's declared words, as the platform and other modules show them (translated with its dictionary)."""
    texts: set[str] = set()
    contract_path = ROOT / ("host/public/contract.json" if owner == "platform" else f"modules/{owner}/public/contract.json")
    contract = json.loads(contract_path.read_text(encoding="utf-8"))
    if owner != "platform":
        manifest = json.loads((ROOT / "modules" / owner / "talyx.module.json").read_text(encoding="utf-8"))
        texts |= {manifest["name"], manifest.get("description", "")}
        texts |= {panel["title"] for panel in manifest.get("panels", {}).values()}
        for entries in manifest.get("provides", {}).values():
            for entry in entries:
                texts |= set(_descriptor_words(entry["descriptor"]))
    for setting in contract.get("settings", {}).values():
        texts |= {setting["title"], setting.get("description", ""), setting.get("group", "")}
        choices = setting["schema"].get("enum") or []
        texts |= {setting.get("options", {}).get(choice, label(choice)) for choice in choices if isinstance(choice, str)}
    for name, action in contract.get("actions", {}).items():
        if action.get("task"):
            texts.add(name.replace(".", " ").replace("_", " ").capitalize())
    return {(None, text) for text in texts if text}


def _descriptor_words(value):
    if isinstance(value, dict):
        for key, item in value.items():
            if key in DESCRIPTOR_KEYS and isinstance(item, str):
                yield item
            else:
                yield from _descriptor_words(item)
    elif isinstance(value, list):
        for item in value:
            yield from _descriptor_words(item)


def python_messages(path: Path) -> dict:
    source = path.read_text(encoding="utf-8")
    lines = source.splitlines()
    tree = ast.parse(source, filename=str(path))
    keys, shown, dynamic = [], [], []

    def ignored(node) -> bool:
        return "i18n: ignore" in lines[node.lineno - 1]

    def value_text(node) -> str | None:
        if isinstance(node, ast.Constant) and isinstance(node.value, str):
            return node.value
        if isinstance(node, ast.JoinedStr):
            return "".join(part.value if isinstance(part, ast.Constant) else "{…}" for part in node.values)
        return None

    def report(node) -> None:
        text = value_text(node)
        if text is not None and PROSE.search(text) and not ignored(node):
            shown.append({"text": text.strip(), "line": node.lineno})

    def name(func) -> str | None:
        if isinstance(func, ast.Name):
            return func.id
        if isinstance(func, ast.Attribute):
            return func.attr
        return None

    for node in ast.walk(tree):
        if isinstance(node, ast.Call):
            called = name(node.func)
            translator = called in ("t", "tc", "mark", "platform_t") and (isinstance(node.func, ast.Name) or (
                isinstance(node.func.value, ast.Name) and node.func.value.id == "i18n"))
            if translator:
                args = node.args
                context = None
                if called == "tc":
                    context = args[0].value if args and isinstance(args[0], ast.Constant) else None
                    args = args[1:]
                if args and isinstance(args[0], ast.Constant) and isinstance(args[0].value, str) and (called != "tc" or context):
                    keys.append({"text": args[0].value, "context": context, "line": node.lineno})
                else:
                    dynamic.append({"line": node.lineno})
            elif called and called.endswith("Error") and len(node.args) >= 2 and isinstance(node.args[0], ast.Constant) \
                    and isinstance(node.args[0].value, str) and ERROR_CODE.match(node.args[0].value):
                report(node.args[1])
            elif called == "activity" and len(node.args) >= 2:
                report(node.args[1])
        elif isinstance(node, ast.Dict):
            for key, value in zip(node.keys, node.values, strict=True):
                if isinstance(key, ast.Constant) and key.value in NOTE_KEYS:
                    report(value)
    return {"keys": keys, "shown": shown, "dynamic": dynamic}


def typescript_messages(paths: list[Path]) -> dict[str, dict]:
    if not paths:
        return {}
    files = [path.relative_to(ROOT).as_posix() for path in paths]
    output = subprocess.run(["node", str(ROOT / "scripts/checks/ts_messages.mjs")], input=json.dumps(files),
                            capture_output=True, text=True, encoding="utf-8", check=True, cwd=ROOT).stdout
    return json.loads(output)


def check(owner: str, sources: list[Path]) -> dict:
    found = typescript_messages([path for path in sources if path.suffix in (".ts", ".tsx")])
    for path in sources:
        if path.suffix == ".py":
            found[path.relative_to(ROOT).as_posix()] = python_messages(path)
    used: dict[tuple[str | None, str], list[str]] = defaultdict(list)
    shown, dynamic = [], []
    for file, result in sorted(found.items()):
        for key in result["keys"]:
            used[(key["context"], key["text"])].append(f"{file}:{key['line']}")
        shown += [f"{file}:{item['line']}  {item['text'][:100]}" for item in result["shown"]]
        dynamic += [f"{file}:{item['line']}" for item in result["dynamic"]]
    wanted = set(used) | declared(owner)
    report = {"owner": owner, "used": len(wanted), "shown": shown, "dynamic": dynamic, "languages": {}}
    for language, path in dictionaries(owner).items():
        if path is None:
            entries = set()
        else:
            messages, contexts = read_dictionary(path)
            entries = {(None, text) for text in messages} | {(context, text) for context, group in contexts.items()
                                                             for text in group}
        report["languages"][language] = {
            "dictionary": path.relative_to(ROOT).as_posix() if path else None,
            "missing": sorted((_key(key), used.get(key, ["declared"])[0]) for key in wanted - entries),
            "unused": sorted(_key(key) for key in entries - wanted),
        }
    return report


def _key(key: tuple[str | None, str]) -> str:
    context, text = key
    return f"{CONTEXT}.{context}: {text}" if context else text


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--owner", action="append", help="platform or a module id (repeatable); all by default")
    parser.add_argument("--details", action="store_true", help="list every problem, not only the counts")
    parser.add_argument("--json", action="store_true", help="the whole report as JSON")
    parser.add_argument("--strict", action="store_true", help="exit 1 when there is any problem")
    args = parser.parse_args()
    sources = owner_sources()
    owners = args.owner or list(sources)
    reports = [check(owner, sources[owner]) for owner in owners]
    if args.json:
        print(json.dumps(reports, ensure_ascii=False, indent=1))
    problems = 0
    for report in reports:
        counts = {language: (len(item["missing"]), len(item["unused"])) for language, item in report["languages"].items()}
        problems += len(report["shown"]) + sum(missing + unused for missing, unused in counts.values())
        if args.json:
            continue
        languages = "; ".join(f"{language}: {missing} missing, {unused} unused" for language, (missing, unused) in counts.items())
        print(f"{report['owner']}: {report['used']} texts; {len(report['shown'])} shown untranslated; "
              f"{len(report['dynamic'])} dynamic; {languages}")
        if args.details:
            for line in report["shown"]:
                print(f"  shown    {line}")
            for language, item in report["languages"].items():
                for text, where in item["missing"]:
                    print(f"  missing  [{language}] {text}  ({where})")
                for text in item["unused"]:
                    print(f"  unused   [{language}] {text}")
            for line in report["dynamic"]:
                print(f"  dynamic  {line}")
    if args.strict and problems:
        sys.exit(1)


if __name__ == "__main__":
    main()
