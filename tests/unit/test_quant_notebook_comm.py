"""A widget's messages between the kernel and the panels: the kernel's go to every panel (and are kept for one that comes later), a panel's go to the kernel's shell."""

from modules.quant.backend.kernels import KernelSession


class Channel:
    def __init__(self):
        self.sent = []

    def send(self, message):
        self.sent.append(message)


class Session:
    def msg(self, kind, content):
        return (kind, content)


class Client:
    def __init__(self):
        self.shell_channel, self.session = Channel(), Session()


def session():
    item = KernelSession.__new__(KernelSession)
    item.since = 0
    item.panels, item.cells, item.requests, item.inputs, item.comms = {}, {}, {}, {}, {}
    item.capturing, item.clearing = {}, set()
    item.variables, item.variable_queries, item.table_queries = [], set(), {}
    item.state, item.message, item.epoch, item.environment, item.record, item.object_id = "idle", None, "e", None, {"settings": {}}, "n1"
    item.client = Client()
    item.attach("a")
    return item


def comms(item, panel):
    out = []
    while not item.panels[panel].queue.empty():
        out.append(item.panels[panel].queue.get_nowait())
    return [m for m in out if m["type"] == "comm"]


def test_kernel_forwards_comm():
    item = session()
    item.comm("comm_open", {"comm_id": "w1", "target_name": "jupyter.widget", "data": {"state": {"value": 3}}})
    item.comm("comm_msg", {"comm_id": "w1", "data": {"method": "update", "state": {"value": 7}}})
    assert [(m["msgType"], m["commId"]) for m in comms(item, "a")] == [("comm_open", "w1"), ("comm_msg", "w1")]
    # a panel that comes later builds the model with its current values
    item.attach("b")
    late = comms(item, "b")
    assert len(late) == 1 and late[0]["msgType"] == "comm_open" and late[0]["data"]["state"]["value"] == 7
    item.comm("comm_close", {"comm_id": "w1", "data": {}})
    item.attach("c")
    assert comms(item, "c") == []


def test_panel_comm_reaches_the_kernel_shell():
    item = session()
    assert item.comm_to_kernel("w1", {"method": "update"}) is False
    item.comm("comm_open", {"comm_id": "w1", "target_name": "jupyter.widget", "data": {"state": {}}})
    assert item.comm_to_kernel("w1", {"method": "update", "state": {"value": 9}}) is True
    assert item.client.shell_channel.sent == [("comm_msg", {"comm_id": "w1", "data": {"method": "update", "state": {"value": 9}}})]


def kernel(kind, parent, **content):
    return {"msg_type": kind, "parent_header": {"msg_id": parent} if parent else {}, "content": content}


def test_what_interact_prints_goes_into_its_output_widget_not_over_the_cell():
    """interact shows its function's result in an Output widget (`with out:` names the request whose outputs it takes); the outputs went to
    the cell, and its clear_output wiped what the cell had shown before."""
    item = session()
    item.cells["run-1"] = {"cell": "c1", "outputs": [], "idle": False}
    item.output = lambda parent, output, *rest: item.cells[parent]["outputs"].append(output)
    item.heard(kernel("display_data", "run-1", data={"image/png": "chart"}, metadata={}))
    item.heard(kernel("comm_open", None, comm_id="out", target_name="jupyter.widget", data={"state": {"_model_name": "OutputModel", "msg_id": "", "outputs": []}}))
    for value, request in ((3, "run-1"), (5, "slider-move")):
        item.heard(kernel("comm_msg", None, comm_id="out", data={"method": "update", "state": {"msg_id": request}}))
        item.heard(kernel("clear_output", request, wait=True))
        item.heard(kernel("stream", request, name="stdout", text=f"x = {value}\n"))
        item.heard(kernel("stream", request, name="stdout", text="done\n"))
        item.heard(kernel("comm_msg", None, comm_id="out", data={"method": "update", "state": {"msg_id": ""}}))
    assert item.cells["run-1"]["outputs"] == [{"output_type": "display_data", "data": {"image/png": "chart"}, "metadata": {}}]
    assert item.comms["out"]["data"]["state"]["outputs"] == [{"output_type": "stream", "name": "stdout", "text": "x = 5\ndone\n"}]
    updates = [m["data"]["state"]["outputs"] for m in comms(item, "a") if m["msgType"] == "comm_msg" and "outputs" in m["data"]["state"]]
    assert updates[-1] == [{"output_type": "stream", "name": "stdout", "text": "x = 5\ndone\n"}]
    # Once the widget stops taking that request, the cell's own outputs go to the cell again.
    item.heard(kernel("stream", "run-1", name="stdout", text="after\n"))
    assert item.cells["run-1"]["outputs"][-1]["text"] == "after\n"
