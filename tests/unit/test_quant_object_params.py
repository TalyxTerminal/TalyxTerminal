import pytest

import modules.quant.talyx_quant as q
from modules.quant.backend.object_params import check_values, read_parameters, read_project

CODE = '''
import talyx_quant as q

LIMIT = q.param("limit", 2.5, min=0, max=10, label="Limit")

class S(q.Strategy):
    def __init__(self):
        super().__init__()
        self.fast = q.param("fast", 10, min=2, max=200, label="Fast window")
        self.on = q.param("on", True)
        self.mode = q.param("mode", "long")
'''


def test_declarations_are_read_in_code_order_without_running_the_code():
    result = read_parameters(CODE)
    assert result["errors"] == []
    assert [(item["name"], item["type"], item["default"], item["min"], item["max"], item["label"]) for item in result["parameters"]] == [
        ("limit", "float", 2.5, 0, 10, "Limit"), ("fast", "int", 10, 2, 200, "Fast window"), ("on", "bool", True, None, None, "on"),
        ("mode", "text", "long", None, None, "mode")]


def test_only_literals_are_declarations_and_each_problem_names_its_line():
    result = read_parameters('import talyx_quant as q\nq.param("a", 1)\nq.param("b", 2 + 3)\nq.param(name, 1)\nq.param("a", 5)\nq.param("c", 1, colour=2)\nq.param("d", "x", min=1)\n')
    assert [item["name"] for item in result["parameters"]] == ["a"]
    assert sorted(item["line"] for item in result["errors"]) == [3, 4, 5, 6, 7]


@pytest.mark.parametrize("values, message", [({"fast": 1}, "between 2 and 200"), ({"fast": 2.5}, "whole number"), ({"on": "yes"}, "true or false"),
                                             ({"mode": 3}, "text"), ({"nope": 1}, "no setting")])
def test_panel_values_must_fit_the_declaration(values, message):
    with pytest.raises(ValueError, match=message):
        check_values(read_parameters(CODE)["parameters"], values)


def test_run_values_override_the_literal_defaults_and_the_actual_values_are_recorded():
    q._bind_parameters({"fast": 20.0, "limit": 3})
    assert (q.param("fast", 10, min=2, max=200), q.param("limit", 2.5), q.param("on", True)) == (20, 3.0, True)
    assert q._used == {"fast": 20, "limit": 3.0, "on": True}
    q._bind_parameters({"fast": 500})
    with pytest.raises(ValueError, match="between 2 and 200"):
        q.param("fast", 10, min=2, max=200)
    q._bind_parameters({})


def test_a_library_parameter_names_a_library_item_and_takes_nothing_but_a_label():
    result = read_parameters('import talyx_quant as q\nq.library_param("signal", "", label="Signal")\nq.library_param("b", 3)\nq.library_param("c", "", min=1)\n')
    assert [(item["name"], item["type"], item["default"], item["label"]) for item in result["parameters"]] == [("signal", "library", "", "Signal")]
    assert [item["line"] for item in result["errors"]] == [3, 4]
    assert "library item" in result["errors"][0]["message"] and "does not take min" in result["errors"][1]["message"]
    definitions = result["parameters"]
    check_values(definitions, {"signal": "Momentum"})
    with pytest.raises(ValueError, match="name of a library item"):
        check_values(definitions, {"signal": 3})


def test_a_library_parameter_in_code_is_the_panels_item_name():
    q._bind_parameters({"signal": "Momentum"})
    assert q.library_param("signal", "") == "Momentum" and q._used == {"signal": "Momentum"}
    q._bind_parameters({})
    assert q.library_param("signal", "") == ""
    q._bind_parameters({"signal": 3})
    with pytest.raises(TypeError, match="name of a library item"):
        q.library_param("signal", "")
    q._bind_parameters({})


def test_settings_carry_their_presentation_and_choices_picks_and_tables_are_declarations():
    code = ('import talyx_quant as q\n'
            'w = q.param("window", 5, min=1, max=60, step=1, label="Lookback", help="Days back", group="Signal")\n'
            'how = q.choice("how", "rank", [("rank", "Rank"), ("z", "Z-score")], group="Signal", show_if={"window": [5, 10]})\n'
            'base = q.pick("base", kinds=["factor"], pinned="version")\n'
            'rows = q.table("weights", [{"name": "a", "weight": 1.0}])\n')
    result = read_parameters(code, "factor.py")
    assert result["errors"] == []
    by_name = {item["name"]: item for item in result["parameters"]}
    assert [item["name"] for item in result["parameters"]] == ["window", "how", "base", "weights"]
    assert (by_name["window"]["step"], by_name["window"]["help"], by_name["window"]["group"], by_name["window"]["file"]) == (1, "Days back", "Signal", "factor.py")
    assert by_name["how"]["type"] == "choice" and [entry["value"] for entry in by_name["how"]["options"]] == ["rank", "z"]
    assert by_name["how"]["showIf"] == {"window": [5, 10]}
    assert (by_name["base"]["type"], by_name["base"]["kinds"], by_name["base"]["pinned"]) == ("pick", ["factor"], "version")
    assert by_name["weights"]["type"] == "table"
    check_values(result["parameters"], {"how": "z"})
    with pytest.raises(ValueError, match="one of rank, z"):
        check_values(result["parameters"], {"how": "x"})


def test_a_project_declares_each_name_once_and_the_second_place_is_named_with_the_first():
    result = read_project({"factor.py": 'import talyx_quant as q\nq.param("window", 5)\n', "universe.py": 'import talyx_quant as q\n\nq.param("window", 10)\nq.param("top", 50)\n'})
    assert [(item["name"], item["file"], item["line"]) for item in result["parameters"]] == [("window", "factor.py", 2), ("top", "universe.py", 4)]
    [error] = result["errors"]
    assert (error["file"], error["line"], error["also"]) == ("universe.py", 3, {"file": "factor.py", "line": 2})
    assert "declared twice" in error["message"]


def test_show_if_must_name_a_declared_setting():
    result = read_parameters('import talyx_quant as q\nq.param("a", 1, show_if={"nope": [1]})\n')
    assert [error["line"] for error in result["errors"]] == [2] and "not declared" in result["errors"][0]["message"]