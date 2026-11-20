"""The factor formula box: what it marks while typing (and where), the code it turns into, and the editor stub that covers its operators."""
import ast
from pathlib import Path

import pytest

from modules.quant.native import formula as F

QUANT = Path(__file__).resolve().parents[2] / "modules" / "quant"

FIELDS = {"$close": {}, "$open": {}, "$volume": {}, "$funding": {"unavailable": "Only for crypto perpetuals"}}


@pytest.mark.parametrize("text, kind, at, length, fix", [
    ("-($close / Ref($close, 4) - 1) / Stdev($close / Ref($close, 1) - 1, 20)", "function", 33, 5, "Std"),
    ("-($closee / Ref($close, 5) - 1)", "field", 2, 7, "$close"),
    ("-($close / Ref($close, 5) - 1", "brackets", 29, 0, None),
    ("-($close / Ref($close, -5) - 1)", "future", 11, 15, None),
    ("$close $open", "syntax", 7, 5, None),
    ("Meen($close, 5)", "function", 0, 4, "Mean"),
    ("Foo($close, 5)", "function", 0, 3, None),   # two edits from Cov, but no more like it than anything else
])
def test_each_mistake_is_marked_where_it_is_with_a_fix_when_one_is_close(text, kind, at, length, fix):
    error = F.check(text, FIELDS)
    assert (error["kind"], error["at"], error["length"], error.get("fix")) == (kind, at, length, fix)
    if kind in {"function", "field"}:
        assert error["message"].endswith(text[at:at + length])


def test_a_field_this_data_cannot_give_says_why_and_a_wrong_argument_count_shows_the_signature():
    assert F.check("$funding * 2", FIELDS)["message"] == "$funding is not available: Only for crypto perpetuals"
    assert F.check("Mean($close)", FIELDS)["message"] == "Mean takes 2 arguments: Mean(x, n)"
    assert F.check("CSRank(-$close)", FIELDS) is None


def test_to_code_writes_the_same_factor_with_the_inputs_it_reads():
    code = F.to_code("CSRank(-($close / Ref($close, 5) - 1)) * Mean($volume, 20)", "My factor")
    tree = ast.parse(code)
    assert ast.get_docstring(tree).startswith("My factor, written from the formula")
    assert "factor = ops.CSRank(-(data.close / ops.Ref(data.close, 5) - 1)) * ops.Mean(data.volume, 20)" in code
    assert "q.report.factor(factor, inputs={'close': data.close, 'volume': data.volume})" in code
    called = {node.func.attr for node in ast.walk(tree) if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
              and isinstance(node.func.value, ast.Name) and node.func.value.id == "ops"}
    assert called == {"CSRank", "Ref", "Mean"}


def test_the_editor_stub_declares_every_formula_operator_and_no_catch_all():
    stub = ast.parse((QUANT / "talyx_quant.pyi").read_text(encoding="utf-8"))
    ops = next(node for node in stub.body if isinstance(node, ast.ClassDef) and node.name == "_Ops")
    declared = {node.name for node in ops.body if isinstance(node, ast.FunctionDef)}
    assert {item["name"] for item in F.declared_operators()} <= declared
    # A notebook's run and backtest hand back outputs the code named itself (r.predictions), so only they may answer any name.
    named_outputs = {"Run", "Backtest"}
    scopes = [stub, *(node for node in stub.body if isinstance(node, ast.ClassDef) and node.name not in named_outputs)]
    assert not any(isinstance(node, ast.FunctionDef) and node.name == "__getattr__" for scope in scopes for node in scope.body)
    research = next(node for node in stub.body if isinstance(node, ast.ClassDef) and node.name == "ResearchData")
    close = next(node for node in research.body if isinstance(node, ast.FunctionDef) and node.name == "close")
    assert ast.unparse(close.returns) == "pd.DataFrame"


QLIB_NAMES = r'''
import json, sys
sys.path.insert(0, sys.argv[1])
import qlib.data.ops as ops
from native import formula as F
public = sorted(cls.__name__ for cls in ops.OpsList)
print(json.dumps({"public": public, "offered": sorted(F.OPERATORS)}))
'''


def test_every_qlib_operator_is_offered(tmp_path):
    """Qlib's own operators, one by one, are in the formula language (Mask and PFeature are Qlib's internals, not offered); run in the research environment."""
    import json
    import subprocess

    from tests.native.environments import environment
    script = tmp_path / "names.py"
    script.write_text(QLIB_NAMES, encoding="utf-8")
    done = subprocess.run([str(environment("research.lock", "3.12")), "-B", str(script), str(QUANT)], capture_output=True, text=True, encoding="utf-8", check=True)
    found = json.loads(done.stdout.strip().splitlines()[-1])
    # Mask and PFeature are Qlib's internals; Feature is `$field`, Add/Sub/Mul/Div are + - * /, Rolling is the base class of the rolling operators: all written by syntax, not as a function
    missing = sorted(set(found["public"]) - {"Mask", "PFeature", "Feature", "Add", "Sub", "Mul", "Div", "Rolling"} - set(found["offered"]))
    assert not missing, f"Qlib operators the formula language does not offer: {missing}"
    assert len(found["public"]) > 40  # the list was read, not empty


def test_a_window_has_a_value_only_once_it_holds_the_whole_window_of_that_name():
    """a 3-bar mean has no value on a name's first two bars (Qlib gives part-filled windows); each name counts from its own first bar."""
    import numpy as np
    import pandas as pd
    index = pd.RangeIndex(6)
    close = pd.DataFrame({"A": [1, 2, 3, 4, 5, 6], "B": [np.nan, np.nan, 10, 11, 12, 13]}, index=index, dtype=float)
    context = F.Context({"$close": close})
    source = "Mean($close / Ref($close, 1) - 1, 3)"   # reads 3 bars of returns, each one bar back: 3 bars before the first value
    part_filled = close / close.shift(1) - 1
    part_filled = part_filled.rolling(3, min_periods=1).mean()
    out = F.warmed(part_filled, F.parse(source), source, context)
    assert out["A"].isna().tolist() == [True, True, True, False, False, False]
    assert out["B"].isna().tolist() == [True] * 5 + [False]
    assert out.loc[3, "A"] == part_filled.loc[3, "A"]
    plain = "$close"
    assert F.warmed(close, F.parse(plain), plain, context).equals(close)


@pytest.mark.parametrize("text, at", [("Mean($close, 0)", 13), ("Corr($close, $volume, 0)", 22), ("Std($close / Ref($close, 1) - 1, 0)", 33)])
def test_a_window_of_zero_is_refused_where_it_is_written(text, at):
    """Qlib reads a window of 0 as the whole history so far, a different factor from the one written."""
    error = F.check(text, FIELDS)
    assert (error["kind"], error["at"], error["length"]) == ("function", at, 1) and "window of 1 day or more" in error["message"]
