"""Check module manifests against their schema and process constraints."""

import json
from pathlib import Path

from jsonschema import Draft202012Validator

ROOT = Path(__file__).resolve().parents[2]
V5_SCHEMA = json.loads((ROOT / "contracts" / "manifest_v5.schema.json").read_text(encoding="utf-8"))
SCHEMAS = {5: V5_SCHEMA}

_MANIFEST_PATTERNS = (
    "modules/*/talyx.module.json",
    "examples/*/talyx.module.json",
    "user/modules/*/talyx.module.json",
)


def _manifests() -> list[Path]:
    found: list[Path] = []
    for pattern in _MANIFEST_PATTERNS:
        found.extend(ROOT.glob(pattern))
    return sorted(found)


def test_schema_itself_is_valid_202012() -> None:
    for schema in SCHEMAS.values():
        Draft202012Validator.check_schema(schema)


def test_official_module_guides_and_topics_load():
    from host.catalog import read_docs, read_guide

    for path in sorted(ROOT.glob("modules/*/talyx.module.json")):
        manifest = json.loads(path.read_text(encoding="utf-8"))
        if "ai" in manifest:
            ai = manifest["ai"]
            read_guide(path.parent, ai["guide"])
            read_docs(path.parent, ai.get("docs", []))


def test_all_manifests_conform() -> None:
    manifests = _manifests()
    assert manifests, "No module manifests found"
    for path in manifests:
        data = json.loads(path.read_text(encoding="utf-8"))
        version = data.get("manifestVersion", 3)
        assert version in SCHEMAS
        validator = Draft202012Validator(SCHEMAS[version])
        errors = sorted(validator.iter_errors(data), key=lambda e: list(e.path))
        assert not errors, f"{path} does not conform to manifest v{version}: " + "; ".join(
            f"{'.'.join(map(str, e.path)) or '<root>'}: {e.message}" for e in errors
        )


def _contract(owner: str) -> dict | None:
    """The owner's generated contract, when the owner is the platform or a version-5 module."""
    if owner == "platform":
        return json.loads((ROOT / "host" / "public" / "contract.json").read_text(encoding="utf-8"))
    manifest = ROOT / "modules" / owner / "talyx.module.json"
    if not manifest.exists() or json.loads(manifest.read_text(encoding="utf-8"))["manifestVersion"] != 5:
        return None
    return json.loads((manifest.parent / "public" / "contract.json").read_text(encoding="utf-8"))


def test_what_a_module_provides_is_the_descriptor_its_owner_declares() -> None:
    """A version-5 manifest's provides entry is ``{id, descriptor}``, the descriptor shaped as the point's owner
    declares it; the window reads it as declared, so a wrong shape would draw nothing."""
    checked = 0
    for path in _manifests():
        data = json.loads(path.read_text(encoding="utf-8"))
        if data.get("manifestVersion") != 5:
            continue
        for point, entries in data.get("provides", {}).items():
            owner, name = point.split(".", 1)
            contract = _contract(owner)
            if contract is None:
                continue
            schema = {**contract["extensionPoints"][name]["descriptor"], "$defs": contract["$defs"]}
            for entry in entries:
                errors = list(Draft202012Validator(schema).iter_errors(entry["descriptor"]))
                assert not errors, f"{path} {point} {entry['id']}: " + "; ".join(error.message for error in errors)
                checked += 1
    assert checked


def test_user_installed_modules_never_run_in_process() -> None:
    """User-installed modules run in separate processes."""
    for path in sorted(ROOT.glob("user/modules/*/talyx.module.json")):
        data = json.loads(path.read_text(encoding="utf-8"))
        lane = data.get("runtime", {}).get("backend")
        assert lane != "in_process", f"{path}: user-installed module cannot declare in_process"
