import ast
import asyncio
import json

from modules.quant.backend import editing_language
from modules.quant.native.nautilus_language import adapt_class, runtime_name


def test_pyo3_language_surface_keeps_native_names_properties_and_argument_types():
    class Strategy:
        def __new__(cls, config=None):
            return object.__new__(cls)

        @property
        def cache(self):
            return None

        @staticmethod
        def from_str(value):
            return value

        def submit_order(self, order, position_id=None):
            pass

    declaration = ast.parse('''
@typing.final
class PyStrategy:
    def py_new(self, config: typing.Any) -> PyStrategy: ...
    def py_cache(self) -> Cache: ...
    def py_from_str(self, value: str) -> PyStrategy: ...
    def py_submit_order(self, order: Order, position_id: str) -> None: ...
''').body[0]
    result = adapt_class(declaration, Strategy)
    ast.fix_missing_locations(result)
    rendered = ast.unparse(result)
    assert result.name == "Strategy" and not result.decorator_list
    assert "def __new__(cls, config: typing.Any=...) -> typing.Self" in rendered
    assert "@property\n    def cache(self) -> Cache" in rendered
    assert "@staticmethod\n    def from_str(value: str)" in rendered
    assert "def submit_order(self, order: Order, position_id: str=...)" in rendered
    assert "py_" not in rendered


def test_enum_runtime_exports_control_spelling_and_real_final_types_remain_final():
    assert runtime_name("Buy", {"BUY": object()}) == "BUY"
    assert runtime_name("PyStrategy", {"Strategy": object()}) == "Strategy"
    assert runtime_name("py_missing", {}) is None
    declaration = ast.parse("@typing.final\nclass Bool:\n    pass\n").body[0]
    result = adapt_class(declaration, bool)
    assert ast.unparse(result.decorator_list[0]) == "typing.final"


def test_bound_classmethod_gets_one_class_argument_and_preserves_parameter_type():
    class Native:
        @classmethod
        def from_str(cls, value):
            return cls()
    declaration = ast.parse("class Native:\n    @classmethod\n    def from_str(cls, value: str) -> Native: ...\n").body[0]
    result = adapt_class(declaration, Native)
    ast.fix_missing_locations(result)
    assert "def from_str(cls, value: str)" in ast.unparse(result)


def test_language_materials_use_exact_interpreter_and_do_not_change_execution_environment(tmp_path, monkeypatch):
    calls = []
    async def command(ctx, argv, cwd, directory, name, **options):
        calls.append(argv)
        (directory / "receipt.json").write_text(json.dumps({"identity": "installed-stubs", "files": 3}))
    monkeypatch.setattr(editing_language, "command", command)
    original = {"interpreterPath": "selected-native-python", "environmentPath": "fixed-lock-environment", "pythonVersion": "3.13"}
    async def scenario():
        first = await editing_language.prepare(None, tmp_path, "strategy", original)
        second = await editing_language.prepare(None, tmp_path, "strategy", original)
        assert first == second and len(calls) == 1
        assert calls[0][0] == original["interpreterPath"]
        assert first["languageMaterials"][0]["kind"] == "python-path"
        assert "languageMaterials" not in original and "env" not in first
        assert await editing_language.prepare(None, tmp_path, "factor", original) is original
    asyncio.run(scenario())
