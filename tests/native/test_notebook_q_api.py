"""What a notebook kernel offers beyond running cells, in the real research environment (set TALYX_NATIVE_SMOKE=1; TALYX_NATIVE_INTERPRETER names a prepared research interpreter,
otherwise the fixed environment of environments.py is used)."""
from __future__ import annotations

import asyncio
import os
from pathlib import Path
from types import SimpleNamespace

import pytest

from modules.quant.backend.kernels import Kernels
from tests.native.environments import environment
from tests.native.test_notebook_kernel import Packages, context, idle, until

pytestmark = pytest.mark.skipif(not os.environ.get("TALYX_NATIVE_SMOKE"), reason="Set TALYX_NATIVE_SMOKE=1 to run native engines")


def started(tmp_path):
    given = os.environ.get("TALYX_NATIVE_INTERPRETER")
    interpreter = Path(given) if given else environment("research.lock", "3.12")
    ctx = context(tmp_path)
    common = SimpleNamespace(root=tmp_path / "common")
    common.root.mkdir()
    (tmp_path / "book").mkdir()
    kernels = Kernels(ctx, SimpleNamespace(root=tmp_path / "records"), common, Packages(interpreter))
    return kernels, kernels.ensure({"id": "nb1", "name": "Notebook", "directory": str(tmp_path / "book"), "kind": "notebook", "settings": {}})


def test_variables_pip_and_input_in_a_real_kernel(tmp_path):
    async def scenario():
        kernels, session = started(tmp_path)
        panel = session.attach("p1")
        await until(panel, idle)
        # variables: after a cell the list arrives; names starting with an underscore, modules and functions are not in it; a size that cannot be read is None
        code = "\n".join(["import pandas as pd", "frame = pd.DataFrame({'a': [1, 2, 3]})", "n = 5", "def helper(): pass", "class Odd:", "    shape = (2, 3)", "odd = Odd()"])
        await session.execute("c1", code)
        listing, _ = await until(panel, lambda message: message["type"] == "variables")
        rows = {row["name"]: row for row in listing["variables"]}
        assert set(rows) == {"frame", "n", "odd"}
        assert rows["frame"]["shape"] == "3 × 1" and rows["frame"]["size"] == int(__import__("pandas").DataFrame({"a": [1, 2, 3]}).memory_usage(deep=False).sum())
        assert rows["n"]["size"] is None and rows["odd"]["size"] is None and rows["frame"]["fromCell"] == 1
        # 1,000 variables answer quickly
        await session.execute("c2", "for i in range(1000): globals()[f'v{i}'] = i")
        listing, _ = await until(panel, lambda message: message["type"] == "variables" and len(message["variables"]) > 900)
        assert len(listing["variables"]) == 1004 and {row["name"]: row for row in listing["variables"]}["n"]["fromCell"] == 1
        # pip is refused inside the kernel as well
        await session.execute("c3", "%pip install x")
        _, seen = await until(panel, lambda message: message["type"] == "reply")
        assert any(item["type"] == "output" and "Packages page" in item["output"].get("evalue", "") for item in seen)
        # input(): the prompt reaches the panel, the reply goes back, the cell finishes with the value
        await session.execute("c4", "x = input('n? ')\nprint(int(x) * 2)", allow_stdin=True)
        request, _ = await until(panel, lambda message: message["type"] == "input_request")
        assert request["prompt"] == "n? " and request["password"] is False and request["cellId"] == "c4"
        assert session.input_reply("c4", "21")
        _, seen = await until(panel, lambda message: message["type"] == "reply")
        texts = [item["output"]["text"] for item in seen if item["type"] == "output" and item["output"]["output_type"] == "stream"]
        assert "n? 21\n" in texts and "42\n" in texts
        # a cell an AI runs (no stdin) gets the kernel's own refusal
        await session.execute("c5", "input('?')")
        _, seen = await until(panel, lambda message: message["type"] == "reply")
        assert any(item["type"] == "output" and item["output"].get("ename") == "StdinNotImplementedError" for item in seen)
        await session.stop()
    asyncio.run(scenario())


def test_traceback_user_frames_only(tmp_path):
    async def scenario():
        kernels, session = started(tmp_path)
        panel = session.attach("p1")
        await until(panel, idle)
        code = "\n".join(["import pandas as pd", "def inner():", "    return pd.DataFrame({'a': [1]})['missing']", "def middle():", "    return inner()", "def outer():", "    return middle()", "outer()"])
        await session.execute("c1", code)
        _, seen = await until(panel, lambda message: message["type"] == "reply")
        error = next(item["output"] for item in seen if item["type"] == "output" and item["output"]["output_type"] == "error")
        text = "\n".join(error["traceback"])
        assert error["ename"] == "KeyError" and "inner" in text and "middle" in text and "outer" in text
        assert "frames inside" in text and "pandas" in text.split("frames inside")[1]
        await session.stop()
    asyncio.run(scenario())


def test_dataframe_output_carries_the_table_explorer(tmp_path):
    async def scenario():
        kernels, session = started(tmp_path)
        panel = session.attach("p1")
        await until(panel, idle)
        await session.execute("c1", "import pandas as pd, numpy as np\npd.DataFrame({'a': np.arange(600.0), 'b': ['x'] * 600, 'c': [None] * 599 + [1.0]})")
        _, seen = await until(panel, lambda message: message["type"] == "reply")
        data = next(item["output"]["data"] for item in seen if item["type"] == "output" and "data" in item["output"])
        table = data["application/vnd.talyx.table+json"]
        assert table["shape"] == [600, 3] and len(table["rows"]) == 500 and "text/html" in data
        assert [(c["name"], c["type"], c["missing"]) for c in table["columns"]] == [("a", "float", 0), ("b", "text", 0), ("c", "float", 599)]
        assert sum(table["columns"][0]["hist"]) == 600 and len(table["columns"][0]["hist"]) == 12 and table["size"] > 0
        await session.stop()
    asyncio.run(scenario())
