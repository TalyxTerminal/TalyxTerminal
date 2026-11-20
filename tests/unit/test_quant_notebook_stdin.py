import asyncio

from modules.quant.backend.kernels import KernelSession


class Client:
    def __init__(self):
        self.executed, self.answers, self.requests = [], [], asyncio.Queue()

    def execute(self, code, allow_stdin, stop_on_error):
        self.executed.append((code, allow_stdin))
        return f"m{len(self.executed)}"

    def input(self, value):
        self.answers.append(value)

    async def get_stdin_msg(self):
        return await self.requests.get()


class Manager:
    ctx = None


def session():
    item = KernelSession.__new__(KernelSession)
    item.since = 0
    item.panels, item.cells, item.requests, item.inputs = {}, {}, {}, {}
    item.variables, item.variable_queries, item.table_queries, item.comms = [], set(), {}, {}
    item.state, item.message, item.epoch, item.environment, item.record, item.object_id = "idle", None, "e", None, {"settings": {}}, "n1"
    item.client, item.failed, item.ready = Client(), None, asyncio.Event()
    item.ready.set()
    item.attach("a"), item.attach("b")
    return item


def drain(item, panel):
    out = []
    while not item.panels[panel].queue.empty():
        out.append(item.panels[panel].queue.get_nowait())
    return [m for m in out if m["type"].startswith("input") or m["type"] == "output"]


def ask(item, msg, prompt, password=False):
    item.client.requests.put_nowait({"msg_type": "input_request", "parent_header": {"msg_id": msg}, "content": {"prompt": prompt, "password": password}})


async def pump(item, count=1):
    task = asyncio.create_task(item.stdin())
    await asyncio.sleep(0.01)
    task.cancel()


def test_user_cells_allow_stdin_and_ai_cells_do_not():
    async def go():
        item = session()
        await item.execute("c1", "input()", allow_stdin=True)
        await item.execute("c2", "input()")
        return item.client.executed
    assert asyncio.run(go()) == [("input()", True), ("input()", False)]


def test_input_request_reaches_the_panel_and_reply_reaches_the_kernel():
    async def go():
        item = session()
        await item.execute("c1", 'input("n? ")', allow_stdin=True)
        ask(item, "m1", "n? ")
        await pump(item)
        for panel in "ab":
            assert [m for m in drain(item, panel) if m["type"] == "input_request"] == [{"type": "input_request", "cellId": "c1", "prompt": "n? ", "password": False}]
        assert item.input_reply("c1", "3") and item.client.answers == ["3"]
        done = [m for m in drain(item, "a") if m["type"] == "input_done"]
        assert done == [{"type": "input_done", "cellId": "c1", "text": "n? 3"}]
        assert item.cells["m1"]["outputs"][-1] == {"output_type": "stream", "name": "stdin", "text": "n? 3\n"}
        # the second panel's late reply is ignored
        assert not item.input_reply("c1", "4") and item.client.answers == ["3"]
    asyncio.run(go())


def test_password_input_not_saved():
    async def go():
        item = session()
        await item.execute("c1", "getpass()", allow_stdin=True)
        ask(item, "m1", "Password: ", True)
        await pump(item)
        item.input_reply("c1", "hunter2")
        text = item.cells["m1"]["outputs"][-1]["text"]
        assert text == "Password: ········\n" and "hunter2" not in str(item.cells)
    asyncio.run(go())


def test_interrupt_while_waiting_for_input():
    async def go():
        item = session()
        item.event = type("E", (), {"set": lambda self: None})()
        await item.execute("c1", "input()", allow_stdin=True)
        ask(item, "m1", "? ")
        await pump(item)
        drain(item, "a")
        item.interrupt()
        assert [m for m in drain(item, "a") if m["type"] == "input_done"] == [{"type": "input_done", "cellId": "c1", "text": ""}]
        assert item.inputs == {}
    asyncio.run(go())


def test_input_reply_first_panel_wins():
    test_input_request_reaches_the_panel_and_reply_reaches_the_kernel()


def test_ai_cells_and_variable_queries_have_no_stdin():
    async def go():
        item = session()
        await item.execute("c1", "input()")
        ask(item, "m1", "? ")
        await pump(item)
        # the kernel itself answers an AI cell with StdinNotImplementedError; no box is raised for it here beyond what the kernel sends
        assert item.client.executed[0][1] is False
    asyncio.run(go())
