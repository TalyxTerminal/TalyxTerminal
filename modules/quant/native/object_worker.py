"""One strategy backtest process: the author's trading code on TALYX's data and venue."""
from __future__ import annotations

import json
import os
import sys
import traceback
from pathlib import Path

native_directory = Path(__file__).resolve().parent
# Execute authored imports from the Quant package root, never the adapter directory.
sys.path[:] = [path for path in sys.path if Path(path or os.getcwd()).resolve() != native_directory]
sys.path.insert(0, str(native_directory.parent))
from native.publish import Publisher


def shown_name(path, roots):
    """The name a person sees for a frame's file: the author's files relative to their folder, package code relative to site-packages;
    nothing here exposes this machine's directories."""
    for name, root in roots.items():
        if path.is_relative_to(root):
            return name, path.relative_to(root).as_posix()
    if "site-packages" in path.parts:
        return "package", "/".join(path.parts[path.parts.index("site-packages") + 1:])
    if path.is_relative_to(native_directory.parent):
        return "talyx", "quant/" + path.relative_to(native_directory.parent).as_posix()
    return "python", path.name


def failure(error, roots):
    """The error and every frame of its traceback; `root` says whose code it is (source, library, package, talyx, python)."""
    frames = []
    for frame in traceback.extract_tb(error.__traceback__):
        path = Path(frame.filename).resolve()
        root, file = shown_name(path, roots)
        frames.append({"root": root, "file": file, "path": str(path), "line": frame.lineno, "function": frame.name, "text": frame.line})
    from native.model_client import ModelError
    return {"type": type(error).__name__, "message": str(error), "frames": frames,
            "traceback": "".join(traceback.format_exception(error)),
            **({'modelFailure': error.error} if isinstance(error, ModelError) else {})}


def write_outcome(output, report):
    """The parent's completion receipt. The full report keeps its instruments and artifacts on disk."""
    outcome = {key: report[key] for key in ("status", "metrics", "strategyReport", "failure", "failureContext", "peakMemory", "timings") if key in report}
    # Report-less strategies use the account totals. The daily return series belongs in the report.
    stats_keys = ("accountPerformance", "total_orders", "total_positions", "stats_pnls")
    outcome["units"] = [{"nativeStatistics": {k: stats[k] for k in stats_keys}} for unit in report["units"]
                        if (stats := unit.get("nativeStatistics")) and not report.get("strategyReport")]
    (Path(output) / "outcome.json").write_text(json.dumps(outcome), encoding="utf-8")


def main():
    import time
    started = time.time()
    invocation = json.loads(Path(sys.argv[1]).read_text(encoding="utf-8"))
    invocation["_started"] = started
    source = Path(invocation["source"]).resolve()
    sys.path.insert(0, str(source))
    roots = {"source": source}
    if invocation.get("library"):
        sys.path.insert(0, invocation["library"])
        roots["library"] = Path(invocation["library"]).resolve()
    os.environ["TALYX_RESEARCH_OUTPUT"] = invocation["output"]
    publisher = Publisher(invocation)
    import talyx_quant as q
    from native.model_runtime import Runtime
    q._models = Runtime(invocation)
    try:
        from native.object_backtest import run
        run(invocation, publisher)
        publisher.index["status"] = "completed"
        publisher.write()
    except Exception as error:
        publisher.index.update(status="cancelled" if type(error).__name__ == "RunStopped" else "failed", error=str(error), failure=failure(error, roots))
        publisher.write()
        traceback.print_exc()
        return 1
    finally:
        write_outcome(invocation["output"], publisher.index)
        q._models.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
