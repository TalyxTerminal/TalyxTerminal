"""Run one real Quant scenario through Quant's public actions on a running Host and report how long it took.

Reports the total time, the time of each stage of each run (from the run's own record), the Host's event-loop lag while the scenario
ran (p50, p99, longest; a trivial request every 5 ms on a kept-alive connection), and the scenario's key numbers. Quant runs in
its own process; its own loop lag is in the Host's log (talyx.stall, "quant loop lag").

    python -B -m scripts.quant_scenario <scenario> --mcp http://127.0.0.1:8031/mcp/workspaces/<id> [options]
    python -B -m scripts.quant_scenario call --mcp ... --tool quant_list_objects --args "{\"all\": true}"

Scenarios (each picks its object by --object, or by name or preset in the Host's data directory):
    topn         a stock Top N strategy (S&P 500): run it on the data it has, or on --since/--until/--names for a small first look
    btc          the BTC breakout preset as it comes (funding rates on)
    strategy     any strategy object as it is (--object), e.g. one whose data has bad bars
    factor       a factor run, then the same factor with only the formula changed (the second run's data stage is the number to watch)
    runs         open a strategy's run list and its newest run, three times each
"""

import argparse
import asyncio
import contextlib
import http.client
import json
import sys
import threading
import time
from datetime import timedelta
from pathlib import Path
from urllib.parse import urlparse

from mcp import ClientSession
from mcp.client.streamable_http import streamablehttp_client

FINAL = {"completed", "failed", "cancelled", "stopped", "interrupted"}


class Probe(threading.Thread):
    """A trivial GET every 5 ms; how long the Host takes to answer it is its event-loop lag plus about a millisecond of HTTP."""

    def __init__(self, host, port):
        super().__init__(daemon=True)
        self.host, self.port = host, port
        self.samples = []
        self.done = threading.Event()

    def run(self):
        connection = http.client.HTTPConnection(self.host, self.port, timeout=120)
        while not self.done.is_set():
            started = time.perf_counter()
            connection.request("GET", "/api/platform/health")
            connection.getresponse().read()
            took = (time.perf_counter() - started) * 1000
            self.samples.append((time.time(), took))
            rest = 0.005 - (time.perf_counter() - started)
            if rest > 0:
                time.sleep(rest)

    def summary(self, since=None, until=None):
        values = sorted(v for t, v in self.samples if (since is None or t >= since) and (until is None or t <= until))
        if not values:
            return {"n": 0}

        def q(p):
            return round(values[min(len(values) - 1, int(p * len(values)))], 1)
        return {"n": len(values), "p50": q(0.5), "p99": q(0.99), "max": round(values[-1], 1),
                "over20ms": sum(1 for v in values if v > 20), "over100ms": sum(1 for v in values if v > 100)}


class Session:
    """One MCP connection to the Host. A long run outlives a connection now and then (the stream drops while the Host is busy); a status
    read is then made again on a new connection, never a call that changes something."""

    def __init__(self, url, log):
        self.url, self.log, self.stack, self.session = url, log, None, None

    async def connect(self):
        if self.stack is not None:
            with contextlib.suppress(BaseException):
                await self.stack.aclose()
        self.stack = contextlib.AsyncExitStack()
        read, write, _ = await self.stack.enter_async_context(streamablehttp_client(self.url, timeout=timedelta(seconds=60), sse_read_timeout=timedelta(hours=3)))
        self.session = await self.stack.enter_async_context(ClientSession(read, write, read_timeout_seconds=timedelta(hours=3)))
        await self.session.initialize()

    async def close(self):
        if self.stack is not None:
            with contextlib.suppress(BaseException):
                await self.stack.aclose()

    async def call(self, tool, args=None, *, quiet=False):
        started = time.perf_counter()
        for attempt in range(4):
            try:
                result = await self.session.call_tool(tool, args or {})
                break
            except Exception as error:  # noqa: BLE001 - the connection dropped; only a status read is made again
                if tool != "quant_read_run" or attempt == 3:
                    raise
                self.log(f"  connection lost ({type(error).__name__}); reading the status again on a new one")
                await asyncio.sleep(1)
                await self.connect()
        took = time.perf_counter() - started
        text = "".join(getattr(item, "text", "") for item in result.content)
        body = json.loads(text)
        if not quiet:
            self.log(f"{tool} {took:.2f}s" + (" ERROR " + text[:600] if result.isError else ""))
        if result.isError:
            raise RuntimeError(f"{tool}: {text[:2000]}")
        return body, took

    async def wait(self, run_id, every=1.0, limit=7200):
        """Poll the run until it ends; returns the run summary and the moments its stage changed."""
        started, stages, last = time.perf_counter(), [], None
        while True:
            run, _ = await self.call("quant_read_run", {"runId": run_id}, quiet=True)
            key = (run["state"], run.get("stage"))
            if key != last:
                stages.append({"at": round(time.perf_counter() - started, 1), "state": run["state"], "stage": run.get("stage"), "clock": time.time()})
                self.log(f"  +{time.perf_counter() - started:7.1f}s {run['state']} {run.get('stage') or ''} {(run.get('message') or '')[:160]}")
                last = key
            if run["state"] in FINAL:
                return run, stages
            if time.perf_counter() - started > limit:
                raise TimeoutError(f"{run['runId']} still {run['state']} after {limit} s")
            await asyncio.sleep(every)


async def find_object(s, kind, args, *, name=None, preset=None):
    if args.object:
        return args.object
    request = {"kind": kind, "scope": "all"}
    while True:
        found, _ = await s.call("quant_list_objects", request, quiet=True)
        for item in found["items"]:
            if (name and item["name"] == name) or (preset and item.get("preset") == preset):
                return item["objectId"]
        if found["nextCursor"] is None:
            break
        request["cursor"] = found["nextCursor"]
    raise SystemExit(f"No {kind} object named {name or preset}; pass --object")


async def one_run(s, object_id, start_args, report):
    started = time.perf_counter()
    if start_args:
        await s.call("quant_update_object", {"objectId":object_id,**start_args})
    started_run,took = await s.call("quant_start_run", {"objectId":object_id})
    report.setdefault("startCallSeconds", []).append(round(took, 2))
    run, stages = await s.wait(started_run["runId"])
    entry = {"runId": run["runId"], "state": run["state"], "seconds": round(time.perf_counter() - started, 1), "stages": stages,
             "materialsSeconds": run.get("materialsSeconds"), "timings": run.get("timings"), "message": run.get("message"), "error": run.get("error")}
    report.setdefault("runs", []).append(entry)
    return run


def strategy_numbers(read):
    stats = read["numbers"]
    keep = ("cagr", "sharpe", "sortino", "maxDrawdown", "volatility", "turnover", "costsAYear", "trades", "rejected", "rebalances", "skippedRebalances")
    return {key: stats[key] for key in keep if key in stats}


async def scenario_strategy(s, args, report, object_id):
    """One run of the strategy. With --since/--until/--symbols the data settings are changed for this run and put back afterwards."""
    current, _ = await s.call("quant_read_object", {"objectId": object_id}, quiet=True)
    original = current["settings"]["data"]
    held = current["settings"]["run"].get("heldOutFrom")
    small = dict(original)
    small.update({key: value for key, value in (("start", args.since), ("end", args.until)) if value})
    if args.symbols:
        for option in ("list", "index", "indexAsOf", "universe"):
            small.pop(option, None)
        small["symbols"] = args.symbols.split(",")
    changed = small != original
    try:
        if changed:
            await s.call("quant_update_object", {"objectId": object_id, "data": {**dict.fromkeys(original.keys() - small.keys()), **small}})
        run = await one_run(s, object_id, {}, report)
    finally:
        if changed:
            await s.call("quant_update_object", {"objectId": object_id, "data": {**dict.fromkeys(small.keys() - original.keys()), **original}})
            await s.call("quant_update_object", {"objectId": object_id, "run": {**(current["settings"].get("run") or {}), "heldOutFrom": held}})
    if run["state"] == "completed":
        work, _ = await s.call("quant_read_run", {"runId": run["runId"]})
        report["key"] = strategy_numbers(work)


async def scenario_factor(s, args, report, object_id):
    first = await one_run(s, object_id, {}, report)
    current, _ = await s.call("quant_read_object", {"objectId": object_id}, quiet=True)
    formula = current.get("formula") or "$close / Ref($close, 20) - 1"
    changed = formula + " + 0" if not formula.endswith(" + 0") else formula[:-4]
    try:
        second = await one_run(s, object_id, {"formula": changed}, report)
    finally:
        await s.call("quant_update_object", {"objectId": object_id, "formula": current["formula"]})
    for run in (first, second):
        if run["state"] == "completed":
            work, _ = await s.call("quant_read_run", {"runId": run["runId"]}, quiet=True)
            report.setdefault("key", []).append(work["numbers"])


async def scenario_runs(s, args, report, object_id):
    for _ in range(3):
        runs, took = await s.call("quant_list_runs", {"objectId": object_id})
        report.setdefault("listSeconds", []).append(round(took, 2))
        if runs["items"]:
            _, took = await s.call("quant_read_run", {"runId": runs["items"][0]["runId"]})
            report.setdefault("readSeconds", []).append(round(took, 2))


async def main_async(args):
    url = urlparse(args.mcp)
    probe = Probe(url.hostname, url.port)
    log_lines = []

    def log(line):
        line = f"[{time.strftime('%H:%M:%S')}] {line}"
        log_lines.append(line)
        print(line, flush=True)
    report = {"scenario": args.scenario}
    s = Session(args.mcp, log)
    await s.connect()
    try:
            if not any(tool.name.startswith("quant_") for tool in (await s.session.list_tools()).tools):
                raise SystemExit("Quant is not installed or did not start: see the Host's log.")
            if args.scenario == "call":
                result, took = await s.call(args.tool, json.loads(Path(args.args[1:]).read_text(encoding="utf-8") if (args.args or "").startswith("@") else args.args or "{}"))
                print(json.dumps(result, ensure_ascii=False, indent=1)[: args.show])
                return
            probe.start()
            await asyncio.sleep(1)  # the probe's first connection is not part of the scenario
            began = time.time()
            started = time.perf_counter()
            if args.scenario == "topn":
                await scenario_strategy(s, args, report, await find_object(s, "strategy", args, preset="top_n"))
            elif args.scenario == "btc":
                await scenario_strategy(s, args, report, await find_object(s, "strategy", args, name="BTC breakout"))
            elif args.scenario == "strategy":
                await scenario_strategy(s, args, report, await find_object(s, "strategy", args))
            elif args.scenario == "factor":
                await scenario_factor(s, args, report, await find_object(s, "factor", args))
            elif args.scenario == "runs":
                await scenario_runs(s, args, report, await find_object(s, "strategy", args))
            report["seconds"] = round(time.perf_counter() - started, 1)
            probe.done.set()
            report["hostLag"] = probe.summary(since=began)
            # The lag while each stage of each run went on (from one state change to the next).
            for entry in report.get("runs", []):
                marks = entry["stages"]
                for stage, following in zip(marks, marks[1:], strict=False):
                    stage["hostLag"] = probe.summary(since=stage["clock"], until=following["clock"])
                for stage in marks:
                    stage.pop("clock")
    finally:
        await s.close()
    print(json.dumps(report, indent=1, ensure_ascii=False))
    if args.out:
        Path(args.out).write_text(json.dumps(report, indent=1, ensure_ascii=False) + "\n" + "\n".join(log_lines), encoding="utf-8")


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("scenario", choices=("topn", "btc", "strategy", "factor", "runs", "call"))
    parser.add_argument("--mcp", required=True, help="the Host's MCP address for an open workspace")
    parser.add_argument("--object", help="the object to use")
    parser.add_argument("--since", help="data from this day (small first look)")
    parser.add_argument("--until", help="data until this day")
    parser.add_argument("--symbols", help="only these Market symbols, comma separated (small first look instead of the index)")
    parser.add_argument("--out", help="also write the report here")
    parser.add_argument("--tool", help="call: the action")
    parser.add_argument("--args", help="call: its arguments as JSON, or @file")
    parser.add_argument("--show", type=int, default=6000, help="call: characters of the result to print")
    asyncio.run(main_async(parser.parse_args()))


if __name__ == "__main__":
    sys.exit(main())
