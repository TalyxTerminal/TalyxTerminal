"""The project view Developer offers other modules: one view, public, that takes the project and the marks an embedding module gives by file."""
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2] / "modules" / "developer"
VIEWS = json.loads((ROOT / "talyx.module.json").read_text(encoding="utf-8"))["views"]


def test_the_view_is_declared_with_its_props_and_events():
    view = VIEWS["project_editor"]
    properties = view["props"]["properties"]
    assert {"projectId", "rootUri", "file", "position", "markers", "readOnly", "tree"} <= set(properties) and view["props"]["required"] == ["rootUri"]
    assert set(view["emits"]) == {"changed", "opened"} and {"uri", "documentId", "version", "dirty"} <= set(view["emits"]["changed"]["properties"])


def test_a_mark_names_its_file_and_otherwise_has_the_editors_fields():
    mark = VIEWS["project_editor"]["props"]["properties"]["markers"]["items"]
    editor = VIEWS["editor"]["props"]["properties"]["markers"]["items"]
    assert "uri" in mark["required"] and set(mark["properties"]) == set(editor["properties"]) | {"uri"}


def test_the_module_provides_it_and_the_project_view_does_not_write_a_second_tree():
    module = (ROOT / "frontend" / "module.tsx").read_text(encoding="utf-8")
    assert 'ctx.views.provide("project_editor", mountProjectEditor)' in module
    source = (ROOT / "frontend" / "documents" / "projectEditorView.tsx").read_text(encoding="utf-8")
    # it shares the document model and the tree's look with the full editor
    assert "documents.open(" in source and "developer-tree-row" in source and "createEditor" in source


def test_developer_offers_three_editor_views():
    assert set(VIEWS) == {"editor", "text_editor", "project_editor"}
