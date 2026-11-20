"""Reconcile installed PyO3 stubs with their actual Python export surface.

The native wheel's generated stubs retain Rust names (PyStrategy, py_from_str,
Buy), omit constructor defaults and mark subclassable types final. Derive an
editor-only copy from the installed stubs and runtime descriptors. No engine
implementation or handwritten duplicate API is supplied here.
"""
from __future__ import annotations

import ast
import hashlib
import importlib
import importlib.util
import inspect
import json
import keyword
import re
import sys
from pathlib import Path


def expression(value):
    return ast.parse(value, mode="eval").body


def runtime_name(name, namespace):
    for candidate in (name, name.removeprefix("Py"), name.removeprefix("py_"), re.sub(r"(?<!^)(?=[A-Z])", "_", name).upper()):
        if candidate in namespace:
            return candidate
    return None


def signature_arguments(actual, original, *, constructor=False):
    """Runtime defines names/defaults; bundled stub defines parameter types."""
    try:
        signature = inspect.signature(actual)
    except (ValueError, TypeError):
        return original
    annotations = {item.arg: item.annotation for item in [*original.posonlyargs, *original.args, *original.kwonlyargs]}
    args = ast.arguments(posonlyargs=[], args=[], vararg=None, kwonlyargs=[], kw_defaults=[], kwarg=None, defaults=[])
    if constructor:
        args.args.append(ast.arg(arg="cls"))
    for parameter in signature.parameters.values():
        argument = ast.arg(arg=parameter.name, annotation=annotations.get(parameter.name))
        if parameter.kind == parameter.POSITIONAL_ONLY:
            args.posonlyargs.append(argument)
        elif parameter.kind == parameter.POSITIONAL_OR_KEYWORD:
            args.args.append(argument)
        elif parameter.kind == parameter.KEYWORD_ONLY:
            args.kwonlyargs.append(argument)
            args.kw_defaults.append(None if parameter.default is parameter.empty else expression("..."))
        elif parameter.kind == parameter.VAR_POSITIONAL:
            args.vararg = argument
        elif parameter.kind == parameter.VAR_KEYWORD:
            if not original.kwarg or original.kwarg.arg != parameter.name:
                argument.annotation = expression("typing.Any")
            args.kwarg = argument
        if parameter.kind in {parameter.POSITIONAL_ONLY, parameter.POSITIONAL_OR_KEYWORD} and parameter.default is not parameter.empty:
            args.defaults.append(expression("..."))
    if constructor and args.posonlyargs:
        args.posonlyargs.insert(0, args.args.pop(0))
    return args


def adapt_class(node, actual):
    node.name = actual.__name__
    if any(ast.unparse(item) in {"typing.final", "final"} for item in node.decorator_list):
        try:
            type("_TalyxLanguageSubclassProbe", (actual,), {})
        except TypeError:
            pass
        else:
            node.decorator_list = [item for item in node.decorator_list if ast.unparse(item) not in {"typing.final", "final"}]
    members = vars(actual)
    adapted = []
    constructor = next((item for item in node.body if isinstance(item, ast.FunctionDef) and item.name in {"py_new", "__new__"}), None)
    for member in node.body:
        if isinstance(member, ast.FunctionDef):
            if member.name == "py_new":
                member.name = "__new__"
                member.args = signature_arguments(actual, member.args, constructor=True)
                member.returns = expression("typing.Self")
                member.decorator_list = []
            else:
                name = runtime_name(member.name, members)
                if not name:
                    # Remove unavailable Rust-only spellings, keeping normal
                    # inherited dunder protocol declarations.
                    if member.name.startswith("py_"):
                        continue
                else:
                    member.name = name
                    descriptor = inspect.getattr_static(actual, name)
                    if inspect.isgetsetdescriptor(descriptor) or isinstance(descriptor, property):
                        member.decorator_list = [expression("property")]
                        member.args = ast.arguments(posonlyargs=[], args=[ast.arg(arg="self")], vararg=None, kwonlyargs=[], kw_defaults=[], kwarg=None, defaults=[])
                    elif name == "__new__":
                        member.args = signature_arguments(actual, member.args, constructor=True)
                        member.returns = expression("typing.Self")
                    elif name == "__init__" and constructor:
                        # PyO3 __init__ often exposes (*args, **kwargs); its
                        # class signature is the authoritative constructor.
                        member.args = signature_arguments(actual, constructor.args, constructor=True)
                        member.args.args[0].arg = "self"
                        member.returns = expression("None")
                    else:
                        static = isinstance(descriptor, staticmethod)
                        member.args = signature_arguments(getattr(actual, name), member.args)
                        if static:
                            member.decorator_list = [expression("staticmethod")]
                        elif isinstance(descriptor, classmethod) or isinstance(getattr(getattr(actual, name), "__self__", None), type):
                            member.decorator_list = [expression("classmethod")]
                            member.args.args.insert(0, ast.arg(arg="cls"))
        elif isinstance(member, (ast.Assign, ast.AnnAssign)):
            targets = member.targets if isinstance(member, ast.Assign) else [member.target]
            for target in targets:
                if isinstance(target, ast.Name):
                    name = runtime_name(target.id, members)
                    if name:
                        target.id = name
        adapted.append(member)
    node.body = adapted
    return node


def build(destination: Path):
    destination.mkdir(parents=True, exist_ok=True)
    if importlib.util.find_spec("nautilus_trader") is None:
        (destination / "receipt.json").write_text(json.dumps({"identity": hashlib.sha256(Path(__file__).read_bytes()).hexdigest(), "interpreter": sys.executable, "files": 0}), encoding="utf-8")
        return
    import nautilus_trader
    root = Path(nautilus_trader.__file__).parent
    originals = list(root.rglob("*.pyi"))
    digest = hashlib.sha256(Path(__file__).read_bytes())
    for source in sorted(originals):
        digest.update(source.relative_to(root).as_posix().encode())
        digest.update(source.read_bytes())
    identity = digest.hexdigest()
    receipt = destination / "receipt.json"
    if receipt.is_file() and json.loads(receipt.read_text())["identity"] == identity:
        return
    trees, aliases = {}, {}
    for source in originals:
        relative = source.relative_to(root)
        text = source.read_text(encoding="utf-8")
        # The generator emits qualified ellipsis defaults such as model....;
        # these have no Python syntax. Ellipsis retains the declared default.
        text = re.sub(r"\b(?:\w+\.)+\.\.\.", "...", text)
        text = re.sub(r"(?m)^(\s+)None = \.\.\.$", r"\1NONE = ...", text)
        for word in keyword.kwlist:
            text = re.sub(r"(?<=[,(]) " + word + r":", " " + word + "_:", text)
        tree = ast.parse(text)
        module_name = "nautilus_trader." + ".".join(relative.with_suffix("").parts)
        module_name = module_name.removesuffix(".__init__")
        if "automatically generated by pyo3_stub_gen" in text and "._libnautilus" not in module_name:
            try:
                namespace = vars(importlib.import_module(module_name))
            except ImportError:
                namespace = {}
            for node in tree.body:
                if isinstance(node, ast.ClassDef):
                    name = runtime_name(node.name, namespace)
                    actual = namespace.get(name)
                    if inspect.isclass(actual):
                        if node.name != name:
                            aliases[node.name] = name
                        adapt_class(node, actual)
        trees[relative] = tree
    class Names(ast.NodeTransformer):
        def visit_Name(self, node):
            node.id = aliases.get(node.id, node.id)
            return node
        def visit_Attribute(self, node):
            self.generic_visit(node)
            node.attr = aliases.get(node.attr, node.attr)
            return node
        def visit_Constant(self, node):
            if isinstance(node.value, str) and node.value in aliases:
                node.value = aliases[node.value]
            return node
    for relative, tree in trees.items():
        target = destination / "nautilus_trader" / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        tree = Names().visit(tree)
        ast.fix_missing_locations(tree)
        target.write_text("# Derived from the installed Nautilus stubs and runtime descriptors.\n" + ast.unparse(tree) + "\n", encoding="utf-8")
    receipt.write_text(json.dumps({"identity": identity, "interpreter": sys.executable, "upstream": str(root), "files": len(trees), "aliases": aliases}, indent=2), encoding="utf-8")


if __name__ == "__main__":
    build(Path(sys.argv[1]).resolve())
