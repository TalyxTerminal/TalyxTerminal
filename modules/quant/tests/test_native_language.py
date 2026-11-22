"""A factor worker reports its own error in the language passed at launch."""
import json
import subprocess
import sys
from pathlib import Path

from modules.quant.backend.research_process import environment


def test_factor_worker_reports_group_count_in_chinese(monkeypatch):
    module = Path(__file__).resolve().parents[1]
    monkeypatch.setenv("TALYX_LANGUAGE", "zh-CN")
    monkeypatch.setenv("TALYX_LOCALES", json.dumps({"zh-CN": str(module / "locales/zh-CN.json")}))
    result = subprocess.run(
        [sys.executable, "-B", "-c", """
from types import SimpleNamespace
from native.factor_resident import JobError, enough_names
try:
    enough_names(SimpleNamespace(columns=["A", "B"]), {"grouping": {"method": "equal", "count": 5}})
except JobError as error:
    print(error.failure["message"])
"""],
        cwd=module, env=environment(), capture_output=True, text=True, encoding="utf-8", check=True,
    )
    assert result.stdout.strip() == "分组数为 5，但数据只有 2 个标的（A, B）。将分组数降至 2 或以下，或增加标的。"
