"""Words in the chosen language: the translator's rules and a module's dictionaries (rules tested directly)."""

from __future__ import annotations

import json

import pytest
from talyx_sdk import i18n
from talyx_sdk.errors import ContractError

from host.catalog import locale_files


@pytest.fixture
def chinese(tmp_path):
    path = tmp_path / "zh-CN.json"
    path.write_text(json.dumps({"Fetching prices": "正在获取价格", "{done} of {total}": "{total} 中的 {done}",
                                "Close": "关闭", "@context": {"price": {"Close": "收盘价"}}}, ensure_ascii=False),
                    encoding="utf-8")
    i18n.configure({"zh-CN": path}, "zh-CN")
    yield path
    i18n.configure({}, "en")


def test_a_text_is_translated_with_its_placeholders_filled_in_the_translations_order(chinese):
    assert i18n.t("Fetching prices") == "正在获取价格"
    assert i18n.t("{done} of {total}", done=3, total=10) == "10 中的 3"


def test_a_text_the_dictionary_lacks_stays_english(chinese):
    assert i18n.t("Saving the code") == "Saving the code"
    assert i18n.t("{count} rows", count=5) == "5 rows"


def test_the_same_english_reads_differently_where_its_context_says(chinese):
    assert i18n.t("Close") == "关闭"
    assert i18n.tc("price", "Close") == "收盘价"
    assert i18n.tc("button", "Close") == "Close"


def test_a_placeholder_without_a_value_is_left_as_written(chinese):
    assert i18n.t("Use {symbol} in the template") == "Use {symbol} in the template"


def test_english_needs_no_dictionary_and_a_process_started_later_inherits_the_language(chinese, monkeypatch):
    i18n.set_language("en")
    assert i18n.t("Fetching prices") == "Fetching prices"
    i18n.set_language("zh-CN")
    import os
    assert os.environ[i18n.LANGUAGE_ENV] == "zh-CN"
    with pytest.raises(ValueError):
        i18n.set_language("fr")


@pytest.mark.parametrize("content, problem", [
    ({"{done} of {total}": "{done} / {count}"}, "placeholders"),
    ({"Rows": 3}, "not text"),
    ({"@context": {"price": "收盘价"}}, "@context"),
])
def test_a_dictionary_that_would_show_wrong_words_is_refused(tmp_path, content, problem):
    path = tmp_path / "zh-CN.json"
    path.write_text(json.dumps(content, ensure_ascii=False), encoding="utf-8")
    with pytest.raises(ValueError, match=problem):
        i18n.read_dictionary(path)


def test_a_module_with_a_broken_or_outside_dictionary_does_not_load(tmp_path):
    module = tmp_path / "words"
    (module / "locales").mkdir(parents=True)
    (module / "locales" / "zh-CN.json").write_text('{"Rows": "行"}', encoding="utf-8")
    assert locale_files(module, {"locales": {"zh-CN": "locales/zh-CN.json"}}) == {
        "zh-CN": (module / "locales" / "zh-CN.json").resolve()}
    (module / "locales" / "zh-CN.json").write_text('{"{n} rows": "行"}', encoding="utf-8")
    with pytest.raises(ContractError, match="placeholders"):
        locale_files(module, {"locales": {"zh-CN": "locales/zh-CN.json"}})
    with pytest.raises(ContractError, match="inside the module"):
        locale_files(module, {"locales": {"zh-CN": "../elsewhere.json"}})
