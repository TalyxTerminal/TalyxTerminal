import asyncio
from types import SimpleNamespace

import pytest
from talyx_sdk import ContractError

from modules.quant.backend.notebook_file import parse
from modules.quant.backend.notebook_routes import NotebookServices


class Records(dict):
    objects = [{"id": "o1", "kind": "factor", "name": "Short-term reversal"}]

    def list(self, kind):
        return self.objects


class Store:
    def __init__(self):
        self.made, self.records = [], Records()

    def read(self, identity):
        if identity not in self.records:
            raise ContractError("not_found", "gone")
        return self.records[identity]

    async def make(self, kind, name, folder, preset, source, settings):
        identity = f"nb{len(self.made) + 1}"
        self.records[identity] = {"id": identity, "kind": kind, "name": name}
        self.made.append((kind, name, source, settings))
        return {"id": identity, "name": name}


class Runs:
    def __init__(self):
        self.rows = {"r21": {"id": "r21", "objectId": "o1", "objectKind": "factor", "name": "Short-term reversal", "number": None, "createdAt": 3}}
        self.rows["r20"] = {"id": "r20", "objectId": "o1", "objectKind": "factor", "name": "Short-term reversal", "number": None, "createdAt": 2}
        self.records = SimpleNamespace(list=lambda kind: list(self.rows.values()))

    def read(self, identity):
        if identity not in self.rows:
            raise ContractError("not_found", "This run does not exist.")
        return self.rows[identity]

    def save(self, record, **changes):
        self.rows[record["id"]] = {**record, **changes}


def services():
    item = NotebookServices.__new__(NotebookServices)
    item.store, item.runs = Store(), Runs()
    return item


def test_first_cell_loaded():
    item = services()
    opened = asyncio.run(item.open_run("r21"))
    kind, name, source, settings = item.store.made[0]
    cells = parse(source)["cells"]
    assert opened == {"notebookId": "nb1", "created": True} and kind == "notebook" and name == "Short-term reversal #2"
    assert cells[0]["source"] == "run = q.run('r21')\nrun" and settings["openedFrom"] == {"objectId": "o1", "runId": "r21", "kind": "factor", "at": 3}


def test_open_run_in_notebook_reuses():
    item = services()
    first = asyncio.run(item.open_run("r21"))
    again = asyncio.run(item.open_run("r21"))
    assert again == {"notebookId": first["notebookId"], "created": False} and len(item.store.made) == 1
    # the notebook was deleted: a new one is made
    del item.store.records["nb1"]
    assert asyncio.run(item.open_run("r21")) == {"notebookId": "nb2", "created": True}


def test_the_run_that_was_clicked_not_the_latest():
    item = services()
    asyncio.run(item.open_run("r20"))
    assert item.store.made[0][2].count("q.run('r20')") == 1


def test_a_deleted_run_is_not_opened():
    with pytest.raises(ContractError):
        asyncio.run(services().open_run("r99"))


def test_new_notebooks_are_named_untitled_then_untitled_2_and_a_deleted_one_frees_its_name(tmp_path):
    from tests.unit.test_quant_object_runs import setup

    async def scenario():
        _, _, store, _ = setup(tmp_path, None)
        names = [(await store.create({"kind": "notebook"}))["name"] for _ in range(2)]
        first = await store.create({"kind": "notebook"})
        named = (await store.create({"kind": "notebook", "name": "Mine"}))["name"]
        return names, first["name"], named

    names, third, named = asyncio.run(scenario())
    assert names == ["Untitled", "Untitled 2"] and third == "Untitled 3" and named == "Mine"


def test_the_first_cell_names_the_run_when_the_name_is_shared_and_the_band_gets_its_facts():
    item = services()
    item.runs.rows["r21"].update({"data": {"dataFrom": 1, "dataTo": 2, "heldOutFrom": 3}, "createdAt": 5})
    item.store.records.objects = [{"id": "o1", "kind": "factor", "name": "Short-term reversal"}, {"id": "o9", "kind": "factor", "name": "Short-term reversal"}]
    asyncio.run(item.open_run("r21"))
    _, _, source, settings = item.store.made[0]
    assert parse(source)["cells"][0]["source"] == "run = q.run('r21')\nrun"
    assert settings["openedFrom"] == {"objectId": "o1", "runId": "r21", "kind": "factor", "at": 5, "dataFrom": 1, "dataTo": 2, "heldOutFrom": 3}


def test_open_library_item_in_a_notebook_reads_that_version_in_its_first_cell():
    made = services()
    opened = asyncio.run(made.open_library_item("Reversal 1M", 2, "factor"))
    model = asyncio.run(made.open_library_item("2nd Net", 1, "model"))
    assert opened["created"] and model["notebookId"] != opened["notebookId"]
    first, second = made.store.made
    assert first[1] == "Reversal 1M v2" and parse(first[2])["cells"][0]["source"] == 'reversal_1m = q.library.read(\'Reversal 1M\', version=2)\nreversal_1m'
    assert parse(second[2])["cells"][0]["source"] == "_2nd_net = q.model('2nd Net', 1)\n_2nd_net"


@pytest.mark.parametrize("kind, stored", [("factor", 1496275200000), ("model", 1496275200000), ("allocation", "2017-06-01"), ("strategy", "2017-06-01T00:00:00Z")])
def test_the_run_band_reads_every_kind_of_run_dates_as_milliseconds(kind, stored):
    item = services()
    item.runs.rows["r30"] = {"id": "r30", "objectId": "o1", "objectKind": kind, "name": "Short-term reversal", "number": None, "createdAt": 4,
                             "data": {"dataFrom": stored, "dataTo": stored, "heldOutFrom": None}}
    asyncio.run(item.open_run("r30"))
    shown = item.store.made[0][3]["openedFrom"]
    assert shown["dataFrom"] == 1496275200000 and shown["dataTo"] == 1496275200000 and "heldOutFrom" not in shown


def test_the_first_cell_adds_the_kind_when_only_the_kind_tells_the_name_apart():
    item = services()
    item.store.records.objects = [{"id": "o1", "kind": "factor", "name": "Short-term reversal"}, {"id": "o9", "kind": "strategy", "name": "Short-term reversal"}]
    asyncio.run(item.open_run("r21"))
    _, _, source, _ = item.store.made[0]
    assert parse(source)["cells"][0]["source"] == "run = q.run('r21')\nrun"
