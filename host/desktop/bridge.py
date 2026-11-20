"""The desktop's own connection to the Host, as a window has one: binary frames on /ws/bridge (an envelope line, then
the body). The desktop only calls public platform actions; it runs none of the window's own actions."""

import json
import threading
from concurrent.futures import Future
from itertools import count

from websockets.exceptions import ConnectionClosed
from websockets.sync.client import connect


class BridgeError(RuntimeError):
    def __init__(self, error: dict):
        super().__init__(error.get("message") or error.get("code"))
        self.code = error.get("code")


class Bridge:
    def __init__(self, url, data_root):
        instance = json.loads((data_root / "host" / "instance.json").read_bytes())
        self.socket = connect(url, additional_headers={"Authorization": f"Bearer {instance['desktopToken']}"}, proxy=None, max_size=None, compression=None)
        welcome = json.loads(self.socket.recv(timeout=180).partition(b"\n")[0])
        if welcome.get("t") != "welcome":
            raise RuntimeError(welcome)
        self.pending: dict[str, Future] = {}
        self.ids = count(1)
        self.lock = threading.Lock()
        self.reader = threading.Thread(target=self.receive, name="desktop-bridge")
        self.reader.start()

    def send(self, envelope: dict, body=None) -> None:
        self.socket.send(json.dumps(envelope).encode() + b"\n" + (b"" if body is None else json.dumps(body).encode()))

    def receive(self):
        try:
            for message in self.socket:
                head, _, body = message.partition(b"\n")
                envelope = json.loads(head)
                kind = envelope.get("t")
                if kind in ("result", "error"):
                    with self.lock:
                        future = self.pending.pop(envelope["id"], None)
                    if future is None:
                        continue
                    if kind == "error":
                        future.set_exception(BridgeError(envelope["err"]))
                    else:
                        future.set_result(json.loads(body) if body else None)
                elif kind == "call":
                    # The Host asks a window to act on its panels; the desktop shows none itself.
                    self.send({"t": "error", "id": envelope["id"], "err": {
                        "code": "not_found", "message": "The desktop has no panels", "execution": "not_started",
                        "effects": "none"}})
        except ConnectionClosed:
            pass  # the Host stopped (quitting, or it went away); calls still waiting fail below
        finally:
            with self.lock:
                for future in self.pending.values():
                    future.set_exception(RuntimeError("The desktop's connection to the Host closed"))
                self.pending.clear()

    def call(self, capability, value=None, timeout=60):
        request_id, future = f"d{next(self.ids)}", Future()
        with self.lock:
            self.pending[request_id] = future
        self.send({"t": "call", "id": request_id, "cap": capability}, {} if value is None else value)
        return future.result(timeout=timeout)

    def close(self):
        self.socket.close()
        self.reader.join(10)
