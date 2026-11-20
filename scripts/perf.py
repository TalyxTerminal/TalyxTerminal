"""Measure the platform's performance lines on the real application, one command each (README, "Measuring performance").

    python -B -m scripts.perf startup --data-dir output\\trial\\<name> --workspace <saved workspace> --out output\\tmp\\startup.txt
    python -B -m scripts.perf load    --data-dir output\\trial\\<name> --workspace <saved workspace> --out output\\tmp\\load.txt --batch none

startup (standard 15): starts the Host (with --dev and the event-loop probe of every process reporting each second), opens the saved
    workspace from the home screen in a headless browser, and waits until it is usable. Reports the seconds from starting the Host to
    usable, and each process's worst per-second p99 and longest event-loop lag over that time.
load (standard 16): in the saved workspace (which must show charts), switches the first chart's timeframe and opens other symbols in
    the last chart for --seconds, timing each from the click to the new picture; with --batch, a batch download in Market runs
    meanwhile. Reports the times, source-to-screen delay (Market's receipt stamp of a bar update to the browser frame that drew it),
    each process's event-loop lag, and each process group's CPU and private memory.

The report is one number per line; a number that has a line in docs/architecture.md ("Processes") says whether it passed. Both commands start
and stop their own Host (--port) and Vite (--web-port), and a browser; they change the data directory (see README), so use a
disposable one. They are only meaningful on an otherwise quiet machine: the report says how busy the machine was.
"""

import argparse
import contextlib
import json
import math
import os
import re
import shutil
import socket
import subprocess
import sys
import threading
import time
from datetime import datetime
from pathlib import Path
from queue import Empty, Queue

from scripts import perf_proc as proc

ROOT = Path(__file__).resolve().parents[1]
TMP = ROOT / "output" / "tmp"
BATCHES = ("none", "1 hour", "1 minute", "update all")

# The lines, from docs/architecture.md ("Processes"). The startup line is still the 29 s the application took before the platform overhaul.
STARTUP_SECONDS = 29
LOOP_P99_MS, LOOP_LONGEST_MS = 20, 200      # standard 15, any process
OPEN_AND_SWITCH_MS = 500                    # standard 16: opening a chart, switching a timeframe
SOURCE_TO_SCREEN_P99_MS = 100               # standard 16, under a large batch download


# ----------------------------------------------------------------------------------------------------------------- the report

class Report:
    def __init__(self):
        self.header, self.rows = [], []

    def note(self, text):
        self.header.append(f"# {text}")

    def add(self, key, value, unit="", limit=None, why=None):
        """One number. ``limit``: it must not exceed it. ``value`` None: it could not be measured, ``why`` says what was missing."""
        if value is None:
            verdict = "not measured: " + why
        elif limit is None:
            verdict = ""
        else:
            verdict = f"line <= {limit:g}: " + ("pass" if value <= limit else "FAIL")
        self.rows.append((key, value, unit, verdict))

    def text(self):
        width = max([len(key) for key, *_ in self.rows] + [10])
        shown = [(key, "n/a" if value is None else f"{value:.1f}" if isinstance(value, float) else str(value), unit, verdict)
                 for key, value, unit, verdict in self.rows]
        size = max([len(value) for _, value, _, _ in shown] + [3])
        lines = [f"{key:<{width}}  {value:>{size}} {unit:<6} {verdict}".rstrip() for key, value, unit, verdict in shown]
        return "\n".join(self.header + lines) + "\n"


def percentile(values, p):
    ordered = sorted(values)
    return ordered[min(len(ordered) - 1, max(0, math.ceil(len(ordered) * p) - 1))] if ordered else None


def git_head():
    done = subprocess.run(["git", "rev-parse", "--short", "HEAD"], cwd=ROOT, capture_output=True, text=True, check=False)
    dirty = subprocess.run(["git", "status", "--porcelain", "--untracked-files=no"], cwd=ROOT, capture_output=True, text=True, check=False)
    return done.stdout.strip() + (" (uncommitted changes)" if dirty.stdout.strip() else "")


# ------------------------------------------------------------------------------------------------------------------ the data

def read_json(path):
    return json.loads(path.read_text(encoding="utf-8-sig"))


class Data:
    """The data directory's saved workspaces and its list of open ones (platform/workspaces.json)."""

    def __init__(self, directory):
        self.root = Path(directory).resolve()
        if not (self.root / "platform").is_dir():
            raise SystemExit(f"{self.root} is not a TALYX data directory (no platform folder): start the application on it once first")
        self.list_path = self.root / "platform" / "workspaces.json"

    def opened(self):
        return read_json(self.list_path)["opened"] if self.list_path.exists() else {}

    def set_opened(self, opened):
        """Only with the Host stopped: it writes this file."""
        document = read_json(self.list_path) if self.list_path.exists() else {}
        document["opened"] = opened
        self.list_path.write_text(json.dumps(document, separators=(",", ":")), encoding="utf-8")

    def saved(self, name):
        found = [read_json(path) for path in (self.root / "platform" / "snapshots").glob("*.json")]
        match = [item for item in found if item["name"] == name]
        if not match:
            raise SystemExit(f"no saved workspace named {name!r} in {self.root}; saved: " + ", ".join(sorted(item["name"] for item in found)))
        return match[0]

    def expected(self, name, saved_only=False):
        """What the workspace shows once usable, from the state it opens with: the open workspace's own when it is open, else the
        saved one. {"panels": [module kinds], "charts": number of charts}."""
        record = None if saved_only else next((item for item in self.opened().values() if item["name"] == name), None)
        panels = record["presentation"]["panels"] if record and record.get("presentation") else self.saved(name)["workspace"]["panels"]
        charts = 0
        for panel in panels:
            if panel["kind"] == "module:chart":    # a chart panel keeps its charts in its layout; an unvisited one has the default one
                state = (panel["state"] or {}).get("state")
                charts += len(state["layout"]["charts"]) if state else 1
        return {"panels": [panel["kind"] for panel in panels], "charts": charts}


# ------------------------------------------------------------------------------------------------------- the application

def listening(port):
    with contextlib.suppress(OSError), socket.create_connection(("127.0.0.1", port), timeout=0.3):
        return True
    return False


def wait_until(condition, seconds, what):
    end = time.time() + seconds
    while time.time() < end:
        if condition():
            return
        time.sleep(0.05)
    raise SystemExit(f"timed out after {seconds} s waiting for {what}")


class Application:
    """The Vite dev server (when one is not running) and one Host at a time, on this command's own ports and log files."""

    def __init__(self, args, token):
        self.args, self.token = args, token
        self.vite = None
        self.host = None
        self.host_pid = None
        self.host_token = None
        self.hosts = 0
        self.logs = []
        self.host_log = None
        TMP.mkdir(parents=True, exist_ok=True)

    def environment(self, **extra):
        # Everything this command makes goes under output/: temp files, uv and npm caches.
        return {**os.environ, "TEMP": str(TMP), "TMP": str(TMP), "UV_CACHE_DIR": str(ROOT / "output" / "uv-cache"),
                "npm_config_cache": str(ROOT / "output" / "npm-cache"), "TALYX_PERF_RUN": self.token, **extra}

    def log_file(self, name):
        path = TMP / f"perf-{os.getpid()}-{name}.log"
        self.logs.append(path)
        return path

    def start_web(self):
        """Returns True when it started the Vite server (its first page loads are slow: the caller warms it up)."""
        if listening(self.args.web_port):
            if not self.args.reuse_web:
                raise SystemExit(f"port {self.args.web_port} is in use; pick --web-port, or --reuse-web to use the server there "
                                 f"(it must proxy to http://127.0.0.1:{self.args.port})")
            return False
        log = self.log_file("vite").open("wb")
        self.vite = subprocess.Popen(["npm.cmd", "--prefix", "web", "run", "dev", "--", "--port", str(self.args.web_port), "--strictPort"],
                                     cwd=ROOT, env=self.environment(TALYX_DEV_BACKEND=f"http://127.0.0.1:{self.args.port}"),
                                     stdout=log, stderr=subprocess.STDOUT, stdin=subprocess.DEVNULL)
        wait_until(lambda: listening(self.args.web_port), 120, "Vite to listen")
        return True

    def start_host(self, name):
        """Starts the Host; returns the time just before it started and the path of its log."""
        if listening(self.args.port):
            raise SystemExit(f"port {self.args.port} is in use; pick --port")
        path = self.host_log = self.log_file(name)
        log = path.open("wb")
        command = [sys.executable, "-B", "-m", "scripts.dev_host", "--data-dir", str(self.args.data_dir), "--port", str(self.args.port),
                   "--ui-url", f"http://127.0.0.1:{self.args.web_port}/", "--dev"]
        self.hosts += 1
        self.host_token = f"{self.token}-{self.hosts}"
        launched = time.time()
        self.host = subprocess.Popen(command, cwd=ROOT, env=self.environment(TALYX_DEV_REPORT_SECONDS="1", TALYX_PERF_HOST=self.host_token),
                                     stdout=log, stderr=subprocess.STDOUT, stdin=subprocess.DEVNULL)
        return launched, path

    def wait_for_host(self):
        wait_until(lambda: listening(self.args.port), 180, "the Host to listen")
        if self.host.poll() is not None:
            raise SystemExit(f"the Host exited with code {self.host.returncode}; see its log")
        table = proc.processes()
        # The venv's python.exe starts the real interpreter: the Host is the deepest process running the dev Host module.
        running = [pid for pid in proc.tree(table, self.host.pid) if "scripts.dev_host" in (proc.command_line(pid) or "")]
        self.host_pid = next(pid for pid in running if not any(table[other][0] == pid for other in running))
        return time.time()

    def stop_host(self):
        if self.host is not None:
            proc.kill_tree(self.host.pid)
            self.host.wait()
        self.host, self.host_pid = None, None
        wait_until(lambda: not listening(self.args.port), 30, "the Host's port to close")
        self.kill_strays("TALYX_PERF_HOST", self.host_token)

    def kill_strays(self, name, value):
        """Whatever still runs with this environment value: a module process the Host's end did not take along."""
        if value is None:
            return
        for pid in proc.processes():
            if pid != os.getpid() and (proc.environment(pid) or {}).get(name) == value:
                proc.kill_tree(pid)

    def stop(self):
        self.stop_host()
        if self.vite is not None:
            proc.kill_tree(self.vite.pid)
            self.vite.wait()
        self.kill_strays("TALYX_PERF_RUN", self.token)
        if self.args.host_log and self.host_log:
            shutil.copyfile(self.host_log, self.args.host_log)
        for path in self.logs:
            with contextlib.suppress(OSError):
                path.unlink()


class Browser:
    """scripts/perf_browser.mjs: its events arrive on a queue."""

    def __init__(self, application, mode, options):
        self.events = Queue()
        command = ["node", str(ROOT / "scripts" / "perf_browser.mjs"), mode, json.dumps(options)]
        self.process = subprocess.Popen(command, cwd=ROOT, env=application.environment(), stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                                        stderr=subprocess.STDOUT, text=True, encoding="utf-8")
        threading.Thread(target=self.read, daemon=True).start()

    def read(self):
        for line in self.process.stdout:
            if line.startswith("@ "):
                self.events.put(json.loads(line[2:]))
            else:
                print("browser:", line.rstrip())
        self.events.put({"event": "exit"})

    def wait(self, event, seconds):
        end = time.time() + seconds
        while True:
            try:
                got = self.events.get(timeout=max(0.1, end - time.time()))
            except Empty:
                raise SystemExit(f"timed out after {seconds} s waiting for the browser to say {event!r}") from None
            if got["event"] == "error":
                raise SystemExit("the browser failed: " + got["message"] + (f" (page errors: {got['pageErrors']})" if got["pageErrors"] else ""))
            if got["event"] == "exit":
                raise SystemExit(f"the browser program ended before saying {event!r}")
            if got["event"] == event:
                return got

    def chromium_pid(self):
        """The browser's main process: the program's child that is not one of the browser's own helpers."""
        table = proc.processes()
        return next(pid for pid in proc.children(table, self.process.pid) if table[pid][1].lower() == "chrome.exe" and "--type=" not in (proc.command_line(pid) or ""))

    def go(self):
        self.process.stdin.write("go\n")
        self.process.stdin.flush()

    def close(self):
        with contextlib.suppress(OSError):
            self.process.stdin.close()
        try:
            self.process.wait(30)
        except subprocess.TimeoutExpired:
            proc.kill_tree(self.process.pid)


# ------------------------------------------------------------------------------------------------------ event-loop lag

LAG = re.compile(r"^(\d{4}-\d\d-\d\d \d\d:\d\d:\d\d,\d{3}) (\S+) \w+ talyx\.stall: (\S+) (?:loop lag over (\d+) samples: p50 ([\d.]+) ms, "
                 r"p99 ([\d.]+) ms, longest ([\d.]+) ms|event loop stuck)")


def loop_lags(log, since, until):
    """Every process's lag reports (one per second) written between two times (epoch seconds) in the Host's log.
    {name: {"windows", "samples", "p99_worst", "longest", "stuck": [(seconds after since, top frames)]}}"""
    found = {}
    lines = log.read_text(encoding="utf-8", errors="replace").splitlines()
    for index, line in enumerate(lines):
        match = LAG.match(line)
        if not match:
            continue
        at = datetime.strptime(match.group(1), "%Y-%m-%d %H:%M:%S,%f").timestamp()
        if not since <= at <= until:
            continue
        item = found.setdefault(match.group(3), {"windows": 0, "samples": 0, "p99_worst": 0.0, "longest": 0.0, "stuck": []})
        if match.group(4):
            item["windows"] += 1
            item["samples"] += int(match.group(4))
            item["p99_worst"] = max(item["p99_worst"], float(match.group(6)))
            item["longest"] = max(item["longest"], float(match.group(7)))
        else:
            frames = [text.strip() for text in lines[index + 1:index + 14] if text.startswith("  File")][-3:]
            item["stuck"].append((round(at - since, 1), " | ".join(frames)))
    return found


def report_lags(report, found, line):
    for name, item in sorted(found.items()):
        report.add(f"loop.{name}.p99_worst_ms", item["p99_worst"], "ms", LOOP_P99_MS if line else None)
        report.add(f"loop.{name}.longest_ms", item["longest"], "ms", LOOP_LONGEST_MS if line else None)
        # The probe's per-second reports miss a stretch that began before the loop's first sample; its stuck watch (thread) sees it.
        report.add(f"loop.{name}.stuck_over_200ms", len(item["stuck"]), "times", 0 if line else None)
        report.add(f"loop.{name}.windows", item["windows"], "s")
        for at, frames in item["stuck"]:
            report.note(f"{name} stuck over 200 ms at +{at} s in: {frames}")


# ----------------------------------------------------------------------------------------------------------------- startup

def startup(args, data, application, report):
    expected = data.expected(args.workspace, saved_only=True)
    if data.opened() and not args.close_open:
        names = ", ".join(item["name"] or "Untitled" for item in data.opened().values())
        raise SystemExit(f"the Host would restore the open workspaces ({names}), so the first open would not be the first; "
                         "close them, or give --close-open to clear this data directory's open list")
    warm = application.start_web() and not args.no_warmup
    runs = []
    for number in range(-1 if warm else 0, args.runs):
        data.set_opened({})
        browser = Browser(application, "startup", {"base": f"http://127.0.0.1:{args.web_port}", "workspace": args.workspace,
                                                    "width": args.viewport[0], "height": args.viewport[1], "expected": expected})
        try:
            browser.wait("ready", 120)
            launched, log = application.start_host(f"host{number}")
            application.wait_for_host()
            listening_at = time.time()
            browser.go()
            result = browser.wait("result", 300)
            time.sleep(2)   # the lag reports of the last second reach the log
        finally:
            browser.close()
            application.stop_host()
            data.set_opened({})
        if number < 0:
            print("warm-up run (Vite's first page loads) done; its numbers are dropped")
            continue
        marks = result["marks"]
        runs.append({"listening": listening_at - launched, "usable": marks["usable"] / 1000 - launched,
                     "click_to_usable": (marks["usable"] - marks["openClicked"]) / 1000,
                     "lags": loop_lags(log, launched, marks["usable"] / 1000 + 1.5), "errors": result["pageErrors"]})
        print(f"run {number + 1}: usable {runs[-1]['usable']:.1f} s after starting the Host")
    report.note(f"{args.runs} run(s) of: stop everything, start the Host (--dev, probe report every second), open {args.workspace!r} "
                f"from the home screen, wait until usable; the worst of the runs is reported")
    report.add("startup.runs", len(runs), "")
    report.add("startup.host_listening_s", max(run["listening"] for run in runs), "s")
    report.add("startup.host_to_usable_s", max(run["usable"] for run in runs), "s", STARTUP_SECONDS)
    report.add("startup.click_to_usable_s", max(run["click_to_usable"] for run in runs), "s")
    for number, run in enumerate(runs, 1):
        report.add(f"startup.run{number}.host_to_usable_s", run["usable"], "s")
        if run["errors"]:
            report.note(f"run {number} page errors: {run['errors']}")
    names = sorted({name for run in runs for name in run["lags"]})
    worst = {name: {"windows": sum(run["lags"].get(name, {"windows": 0})["windows"] for run in runs),
                    "samples": 0,
                    "p99_worst": max(run["lags"].get(name, {"p99_worst": 0.0})["p99_worst"] for run in runs),
                    "longest": max(run["lags"].get(name, {"longest": 0.0})["longest"] for run in runs),
                    "stuck": [stuck for run in runs for stuck in run["lags"].get(name, {"stuck": []})["stuck"]]} for name in names}
    report_lags(report, worst, line=True)


# -------------------------------------------------------------------------------------------------------------------- load

class Sampler(threading.Thread):
    """CPU and private memory of the Host, each module's process (with the processes under it) and the browser, every few seconds."""

    def __init__(self, host_pid, interval=5.0):
        super().__init__(daemon=True)
        self.host_pid, self.browser_pid, self.interval = host_pid, None, interval
        self.stopped = threading.Event()
        self.names = {}                     # module launcher pid -> label
        self.first, self.last = {}, {}      # pid -> CPU seconds when first and last seen
        self.owner = {}                     # pid -> group
        self.memory = {}                    # group -> [private bytes at each sample]
        self.times = []                     # (wall time, machine times) at each sample
        self.started = False

    def label(self, pid, table):
        if pid not in self.names:
            module = (proc.environment(pid) or {}).get("TALYX_MODULE")
            self.names[pid] = module or f"{Path(table[pid][1]).stem}-{pid}"
        return self.names[pid]

    def sample(self):
        table = proc.processes()
        groups = {"host": [self.host_pid]}
        for child in proc.children(table, self.host_pid):
            groups[self.label(child, table)] = proc.tree(table, child)
        if self.browser_pid in table:
            groups["browser"] = proc.tree(table, self.browser_pid)
        self.times.append((time.time(), proc.machine_times()))
        for group, members in groups.items():
            total = 0
            for pid in members:
                private, cpu = proc.private_bytes(pid), proc.cpu_seconds(pid)
                if private is None or cpu is None:
                    continue
                total += private
                self.last[pid] = cpu
                self.owner[pid] = group
                self.first.setdefault(pid, 0.0 if self.started else cpu)   # a process born during the run used its CPU in the run
            self.memory.setdefault(group, []).append(total)
        self.started = True

    def run(self):
        self.sample()
        while not self.stopped.wait(self.interval):
            self.sample()

    def finish(self):
        self.stopped.set()
        self.join()
        self.sample()
        seconds = self.times[-1][0] - self.times[0][0]
        cpu = {}
        for pid, group in self.owner.items():
            cpu[group] = cpu.get(group, 0.0) + self.last[pid] - self.first[pid]
        return {"seconds": seconds, "busy": proc.busy_between(self.times[0][1], self.times[-1][1]),
                "cpu": {group: 100 * used / seconds for group, used in cpu.items()},
                "memory": {group: (values[-1], max(values)) for group, values in self.memory.items()}}


class Tap(threading.Thread):
    """Subscribes to Market's session of each chart's series over the Host's window connection, as the chart does, and keeps every
    update's receipt stamp (Market's clock when it sent the row) with the row: the source end of the source-to-screen join."""

    def __init__(self, port, web_port, charts):
        super().__init__(daemon=True)
        self.port, self.charts = port, charts
        self.origin = f"http://127.0.0.1:{web_port}"
        self.updates, self.notes, self.error = [], [], None
        self.ready, self.stopped = threading.Event(), threading.Event()

    def run(self):
        try:
            self.follow()
        except Exception as error:  # noqa: BLE001 - reported with the numbers; the browser half does not depend on it
            self.error = f"{type(error).__name__}: {error}"
            self.ready.set()

    def follow(self):
        from websockets.sync.client import connect
        with connect(f"ws://127.0.0.1:{self.port}/ws/bridge", origin=self.origin, proxy=None, max_size=None, compression=None) as sock:
            self.listen(sock)

    def listen(self, sock):
        from vocabulary.instruments import MarketInstrument, ohlcv_series
        welcome = json.loads(sock.recv(timeout=60).partition(b"\n")[0])
        if welcome["t"] != "welcome":
            raise RuntimeError(welcome)

        def send(envelope, body):
            sock.send(json.dumps(envelope).encode() + b"\n" + json.dumps(body).encode())

        def call(capability, body, ident):
            send({"t": "call", "id": ident, "cap": capability}, body)
            while True:
                head, _, rest = sock.recv(timeout=60).partition(b"\n")
                envelope = json.loads(head)
                if envelope.get("id") == ident:
                    if envelope["t"] == "error":
                        raise RuntimeError(envelope["err"])
                    return json.loads(rest)

        subscribed = {}
        for number, legend in enumerate(self.charts):
            name, timeframe, venue = (legend.split(" · ") + ["", ""])[:3]
            found = call("market.instruments.search", {"query": name, "limit": 25}, f"s{number}")["results"]
            # The legend names the venue as the chart does: its name (NASDAQ), else its id (okx).
            same = [item["instrument"] for item in found
                    if venue.lower() in ((item["instrument"].get("venueName") or "").lower(), item["instrument"]["venueId"].lower())]
            if not same:
                self.notes.append(f"no instrument for {legend!r} (search found "
                                  f"{[(item['instrument'].get('symbol'), item['instrument'].get('venueId')) for item in found][:5]})")
                continue
            series = ohlcv_series(MarketInstrument.from_wire(same[0]), timeframe.lower()).to_dict()
            send({"t": "sub", "id": f"q{number}", "sub": f"tap{number}", "cap": "market.session"},
                 {"instrument": same[0], "series": series, "warmup": 5})
            subscribed[f"tap{number}"] = legend
        self.ready.set()
        while not self.stopped.is_set():
            try:
                message = sock.recv(timeout=1)
            except TimeoutError:
                continue
            at = time.time_ns()
            head, _, rest = message.partition(b"\n")
            envelope = json.loads(head)
            if envelope.get("t") != "item" or envelope.get("sub") not in subscribed:
                continue
            item = json.loads(rest)
            if item.get("type") == "update":
                row = item["row"]
                self.updates.append({"time": int(row["time"]) // 1_000_000, "close": float(row["close"]), "volume": float(row["volume"]),
                                     "receivedAt": int(item["receivedAt"]), "tapAt": at})


def join(bars, updates):
    """Each chart bar update the page received, matched by its values to the Market update it came from: milliseconds from Market's
    receipt stamp to the page's receipt of it and to the frame that drew it."""
    index = {}
    for item in updates:
        index.setdefault((item["time"], item["close"], item["volume"]), []).append(item)
    receipt, frame, unmatched, delivered = [], [], 0, set()
    for entry in sorted(bars, key=lambda entry: entry["at"]):
        for bar in (bar for bar in entry["bars"] if not bar["replay"] and bar["volume"] is not None):
            found = index.get((bar["time"], bar["close"], bar["volume"]), [])
            # An update counts once, by its first delivery: a chart that subscribes again (a new timeframe, a new symbol) is sent the
            # newest bar again, which says how long ago Market last sent something, not how long delivery takes.
            sent = max((item for item in found if id(item) not in delivered and item["receivedAt"] / 1e6 <= entry["at"] + 1),
                       key=lambda item: item["receivedAt"], default=None)
            if sent is None:
                unmatched += 1
                continue
            delivered.add(id(sent))
            receipt.append(entry["at"] - sent["receivedAt"] / 1e6)
            if entry["frame"] is not None:
                frame.append(entry["frame"] - sent["receivedAt"] / 1e6)
    return receipt, frame, unmatched


def timed(report, key, samples, t0):
    ok = [item["ms"] for item in samples if item["ok"]]
    for item in samples:
        if not item["ok"]:
            report.note(f"{key} to {item['want']} at +{(item['at'] - t0) / 1000:.1f} s showed nothing new in 5 s; the chart "
                        f"showed: {item['shown']}")
    report.add(f"{key}.count", len(samples), "")
    report.add(f"{key}.timed_out", len(samples) - len(ok), "", 0)
    report.add(f"{key}.p50_ms", percentile(ok, 0.5), "ms", why="no sample finished")
    report.add(f"{key}.max_ms", max(ok) if ok else None, "ms", OPEN_AND_SWITCH_MS, why="no sample finished")


def load(args, data, application, report):
    expected = data.expected(args.workspace)
    saved = data.saved(args.workspace)
    original_opened = data.opened()
    application.start_web()
    options = {"base": f"http://127.0.0.1:{args.web_port}", "workspace": args.workspace, "snapshotId": saved["snapshotId"],
               "restored": bool(original_opened), "width": args.viewport[0], "height": args.viewport[1], "expected": expected,
               "seconds": args.seconds, "batch": args.batch, "symbols": args.symbols}
    log = application.start_host("host")[1]
    sampler = tap = None
    try:
        application.wait_for_host()
        browser = Browser(application, "load", options)
        try:
            usable = browser.wait("usable", 300)
            # Only the charts the scenario leaves alone: a chart that subscribes again (the first one's timeframe, the
            # last one's symbol) is sent the forming bar Market last had, seconds old, which is not a delivery delay.
            tap = Tap(args.port, args.web_port, usable["charts"][1:-1])
            tap.start()
            tap.ready.wait(120)
            time.sleep(2)
            sampler = Sampler(application.host_pid)
            sampler.browser_pid = browser.chromium_pid()
            sampler.start()
            time.sleep(0.5)
            browser.go()
            result = browser.wait("result", args.seconds + 300)
        finally:
            browser.close()
        time.sleep(2)
        used = sampler.finish()
        tap.stopped.set()
        tap.join(10)
    finally:
        application.stop_host()
        data.set_opened(original_opened)
    t0, t1 = result["t0"] / 1000, result["t1"] / 1000
    shown = ", ".join(text.replace(" " + chr(183) + " ", "/") for text in usable["charts"])
    report.note(f"workspace {args.workspace!r}, batch {args.batch!r}, {args.seconds} s, charts: {shown}; "
                f"watchlist shown: {usable['watchlist']}")
    report.note("timeframe switch: chart 1 between two timeframes; open symbol: the last chart, each timed from the click to the new picture")
    report.note("source to screen: the charts between the first and the last; the others' bar updates count as unmatched")
    timed(report, "load.timeframe_switch", result["switches"], result["t0"])
    timed(report, "load.open_symbol", result["opens"], result["t0"])
    receipt, frame, unmatched = join(result["bars"], tap.updates)
    if tap.error or not frame:
        why = tap.error or ("the workspace has no chart besides the first and the last (the ones the scenario changes)"
                            if len(usable["charts"]) < 3 else "no chart bar update was matched to a Market update (page "
                            f"received {len(result['bars'])} bar updates, the tap {len(tap.updates)} updates)")
        report.add("load.source_to_screen_p99_ms", None, "ms", SOURCE_TO_SCREEN_P99_MS, why=why)
    else:
        report.add("load.source_to_screen_p99_ms", percentile(frame, 0.99), "ms", SOURCE_TO_SCREEN_P99_MS)
        report.add("load.source_to_screen_p50_ms", percentile(frame, 0.5), "ms")
        report.add("load.source_to_screen_max_ms", max(frame), "ms")
        report.add("load.source_to_page_p99_ms", percentile(receipt, 0.99), "ms")
        report.add("load.source_to_screen_samples", len(frame), "")
    report.add("load.bar_updates_unmatched", unmatched, "")
    report.add("load.market_to_tap_p99_ms", percentile([(item["tapAt"] - item["receivedAt"]) / 1e6 for item in tap.updates], 0.99), "ms",
               why="the tap received no update")
    for text in tap.notes:
        report.note("tap: " + text)
    dts = result["frameTimes"]
    report.add("load.frame_time_p99_ms", percentile(dts, 0.99), "ms", why="no frames")
    report.add("load.frames_over_50ms", sum(1 for item in dts if item > 50), "")
    report_lags(report, loop_lags(log, t0, t1 + 1.5), line=False)
    for group, percent in sorted(used["cpu"].items()):
        report.add(f"cpu.{group}.percent_of_one_core", percent, "%")
    for group, (last, peak) in sorted(used["memory"].items()):
        report.add(f"memory.{group}.private_mb", round(last / 2**20), "MB")
        report.add(f"memory.{group}.private_peak_mb", round(peak / 2**20), "MB")
    report.add("machine.cpu_busy_percent", 100 * used["busy"], "%")
    if result["pageErrors"]:
        report.note(f"page errors: {result['pageErrors']}")


# ------------------------------------------------------------------------------------------------------------ command line

def size(text):
    width, _, height = text.partition("x")
    return int(width), int(height)


def verify_market_year(table, listings, sessions):
    """Exactly one daily bar per eligible trading date for each selected listing."""
    from collections import Counter, defaultdict

    actual = defaultdict(list)
    for record in table.select(["instrumentId", "time"]).to_pylist():
        actual[record["instrumentId"]].append(record["time"].isoformat())
    if set(actual) != set(listings):
        raise RuntimeError("The Parquet instrument IDs differ from the selected cohort")
    for id_, listing in listings.items():
        expected = [day for day, opened in sessions[listing["calendarId"]]
                    if (listing.get("listedFrom") is None or opened >= int(listing["listedFrom"]))
                    and (listing.get("delistedAt") is None or opened < int(listing["delistedAt"]))]
        if sorted(actual[id_]) != expected:
            counts = Counter(actual[id_])
            missing = sorted(set(expected) - counts.keys())
            unexpected = sorted(counts.keys() - set(expected))
            repeated = sorted(day for day, count in counts.items() if count > 1)
            raise RuntimeError(f"{id_}: expected one daily bar on each of {len(expected)} trading dates, "
                               f"received {len(actual[id_])} rows; missing {missing[:3]}, "
                               f"unexpected {unexpected[:3]}, repeated {repeated[:3]}")


async def market_read(url, start, end, at):
    """Time one public batch read; verify that the file actually covers the full cohort."""
    import pyarrow.parquet as pq
    from mcp import ClientSession
    from mcp.client.streamable_http import streamablehttp_client

    request = {"index": {"symbol": "^GSPC", "at": at}, "timeframe": "1d",
               "start": start, "end": end, "format": "file"}
    async with streamablehttp_client(url) as (read, write, _):
        async with ClientSession(read, write) as session:
            await session.initialize()
            async def pages(tool, query):
                while True:
                    reply = await session.call_tool(tool, query)
                    if reply.isError:
                        raise RuntimeError(reply.content[0].text)
                    page = json.loads(reply.content[0].text)
                    yield page
                    if page["nextCursor"] is None:
                        break
                    query = {**query, "cursor": page["nextCursor"]}

            async def paged_rows(tool, query):
                async for page in pages(tool, query):
                    names = [column["name"] for column in page["columns"]]
                    for row in page["rows"]:
                        yield dict(zip(names, row, strict=True))

            members, missing = {}, set()
            selector = {"index":{"symbol":"^GSPC"}, "data":"members", "limit":40}
            if at:
                selector["index"]["at"] = at
            else:
                selector.update(start=start, end=end)
            async for row in paged_rows("market_read_research", selector):
                if row["instrumentId"]:
                    members[row["instrumentId"]] = row["code"]
                else:
                    missing.add(row["code"])
            if missing:
                print(f"{len(missing)} unresolved membership records: {', '.join(sorted(missing))}")
            cohort = sorted(members, key=lambda i: (members[i], i))
            if not at:
                # Time the stored-data path. Find which historical members have daily bars
                # spanning this year before starting the clock, using the ordinary public inventory.
                stored = []
                for i in range(0, len(cohort), 20):
                    query = {"symbols":cohort[i:i + 20], "data":"bars", "timeframe":"1d", "limit":20}
                    async for row in paged_rows("market_read_coverage", query):
                        local = row["local"]
                        if local and local["range"]["start"][:10] <= start and local["range"]["end"][:10] >= end:
                            stored.append(row["instrumentId"])
                print(f"{len(stored)} historical members have stored daily coverage for the window")
                cohort = stored[:503]
            listings, calendars = {}, {}
            for batch in range(0, len(cohort), 24):
                async for page in pages("market_instruments_get", {"symbols": cohort[batch:batch + 24]}):
                    for item in page["items"]:
                        record = item["instrument"]
                        listings[record["instrumentId"]] = record
            if set(listings) != set(cohort):
                raise RuntimeError("The listing records differ from the selected cohort")
            from datetime import datetime
            for calendar in {record["calendarId"] for record in listings.values()}:
                days = []
                query = {"calendarId": calendar, "startDate": start, "endDate": end, "limit": 60}
                async for page in pages("market_calendar_sessions", query):
                    if page["evidence"] != "verified":
                        raise RuntimeError(f"Unverified calendar coverage for {calendar}")
                    days.extend((row["date"], int(datetime.fromisoformat(row["open"]).timestamp()) * 10**9)
                                for row in page["sessions"])
                calendars[calendar] = days
            request.pop("index")
            request["symbols"] = cohort
            before = time.perf_counter()
            reply = await session.call_tool("market_read_data", request)
            seconds = time.perf_counter() - before
    if reply.isError:
        raise RuntimeError(reply.content[0].text)
    result = json.loads(reply.content[0].text)
    data = pq.read_table(result["file"]["path"], columns=["instrumentId", "time"])
    verify_market_year(data, listings, calendars)
    count = len(data.column("instrumentId").unique())
    if data.num_rows != result["total"]:
        raise RuntimeError(f"Expected 503 instruments and {result['total']} rows, got {count} and {data.num_rows}")
    return seconds, count, data.num_rows


def market_read_cli(argv):
    import asyncio
    from datetime import date

    parser = argparse.ArgumentParser(description="Read one stored year for 503 S&P 500 listings through an open workspace.")
    parser.add_argument("--mcp-url", required=True)
    parser.add_argument("--start", type=date.fromisoformat, required=True)
    parser.add_argument("--end", type=date.fromisoformat, required=True)
    parser.add_argument("--at", type=date.fromisoformat, help="membership on this date; omit for historical members with a full stored window")
    args = parser.parse_args(argv)
    if not 364 <= (args.end - args.start).days <= 366:
        parser.error("Choose one calendar year of stored daily bars")
    seconds, instruments, rows = asyncio.run(market_read(args.mcp_url, str(args.start), str(args.end), str(args.at) if args.at else None))
    print(f"market.read_data: {seconds:.3f}s, {instruments} instruments, {rows} rows, line <= 3s")
    return 0 if seconds <= 3 and instruments == 503 else 1


def main():
    if sys.argv[1:2] == ["market-read"]:
        raise SystemExit(market_read_cli(sys.argv[2:]))
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("mode", choices=("startup", "load"))
    parser.add_argument("--data-dir", type=Path, required=True, help="a disposable data directory with the saved workspace (output\\trial\\<name>)")
    parser.add_argument("--workspace", required=True, help="name of the saved workspace to open")
    parser.add_argument("--out", type=Path, required=True, help="the report, one number per line")
    parser.add_argument("--port", type=int, default=8051, help="the Host's port")
    parser.add_argument("--web-port", type=int, default=5191, help="the Vite server's port")
    parser.add_argument("--reuse-web", action="store_true", help="use the Vite server already on --web-port (it must proxy to --port)")
    parser.add_argument("--host-log", type=Path, help="keep a copy of the Host's log of the (last) run here")
    parser.add_argument("--viewport", type=size, help="browser window, WIDTHxHEIGHT (default 1440x900 for startup, 2400x1300 for load)")
    parser.add_argument("--runs", type=int, default=1, help="startup: how many times to start and open (the worst is reported)")
    parser.add_argument("--close-open", action="store_true", help="startup: clear the data directory's list of open workspaces first")
    parser.add_argument("--no-warmup", action="store_true", help="startup: no warm-up run when this command started Vite")
    parser.add_argument("--batch", choices=BATCHES, default="none", help="load: a batch download in Market while measuring")
    parser.add_argument("--seconds", type=int, default=60, help="load: how long to measure")
    parser.add_argument("--symbols", type=lambda text: text.split(","), default="AMZN,GOOGL,META,TSLA,JPM,XOM,COST,KO".split(","),
                        help="load: symbols the last chart opens in turn")
    args = parser.parse_args()
    sys.stdout.reconfigure(errors="replace")
    if sys.platform != "win32":
        raise SystemExit("the performance commands read Windows process facts: run them on Windows")
    args.viewport = args.viewport or ((1440, 900) if args.mode == "startup" else (2400, 1300))
    args.data_dir = args.data_dir.resolve()
    data = Data(args.data_dir)
    token = f"{os.getpid()}-{int(time.time())}"
    application = Application(args, token)
    report = Report()
    report.note(f"perf {args.mode}, {datetime.now():%Y-%m-%d %H:%M:%S}, commit {git_head()}, {os.cpu_count()} logical cores")
    report.note(f"data {args.data_dir}, Host port {args.port}, web port {args.web_port}, window {args.viewport[0]}x{args.viewport[1]}")
    report.note(f"machine CPU busy before the run: {100 * proc.busy_fraction(2):.0f} %")
    try:
        (startup if args.mode == "startup" else load)(args, data, application, report)
    finally:
        application.stop()
    text = report.text()
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(text, encoding="utf-8")
    print(text)


if __name__ == "__main__":
    main()
