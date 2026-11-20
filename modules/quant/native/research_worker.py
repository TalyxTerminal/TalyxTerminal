"""One research run: the author's factor, model or allocation code on the object's bars, with its standard report.

Two modes. `run` (and `quick`) write the report. `values` run the same code once per cutoff without any report and write only what
a publication would hold: the library uses it to look for future data and to extend a published item by one period.
"""
from __future__ import annotations

import json
import os
import runpy
import sys
import traceback
from pathlib import Path

native_directory = Path(__file__).resolve().parent
# Execute authored imports from the Quant package root, never the adapter directory.
sys.path[:] = [path for path in sys.path if Path(path or os.getcwd()).resolve() != native_directory]
sys.path.insert(0, str(native_directory.parent))
from native.object_worker import failure
from native.publish import Publisher


def raw_figures(publisher):
    """Plain matplotlib figures the code left open: raw output, not a standard report."""
    import matplotlib.pyplot as plt
    from native.chart_publication import svg_figure
    artifacts = []
    for number in plt.get_fignums():
        figure = plt.figure(number)
        if any(artist is not figure.patch and artist.get_visible() for artist in figure.get_children()):
            title = next((axis.get_title() for axis in figure.axes if axis.get_title()), f"Figure {number}")
            artifacts += svg_figure(publisher, figure, f"raw/figure-{number}", title, chapter="raw", engine="matplotlib", order=number)
    if artifacts:
        publisher.section("raw-figures", "Figures from the code (raw output)", artifacts)
    plt.close("all")


def execute(invocation):
    """The author's entry as `__main__`."""
    os.chdir(invocation["source"])
    sys.argv = [invocation["entry"]]
    runpy.run_path(str(Path(invocation["source"]) / invocation["entry"]), run_name="__main__")


def run_report(invocation, roots):
    import talyx_quant as q
    publisher = Publisher(invocation)
    try:
        q._bind_parameters(invocation["params"])
        q._bind_research(invocation, publisher)
        execute(invocation)
        raw_figures(publisher)
        publisher.index["parameters"] = dict(q._used)
        publisher.index["status"] = "completed"
        publisher.write()
        return 0
    except Exception as error:
        raw_figures(publisher)
        publisher.index.update(status="failed", error=str(error), failure=failure(error, roots), parameters=dict(q._used))
        publisher.write()
        traceback.print_exc()
        return 1


def values_run(invocation, roots, cutoff):
    """The values every publication of the code holds when it only sees bars up to `cutoff` (None: all of them)."""
    import talyx_quant as q
    q._bind_parameters(invocation["params"])
    q._bind_research({**invocation, "mode": "values", "cutoff": cutoff}, None)
    execute(invocation)
    return list(q._Run.publications)


def run_values(invocation, roots):
    """Write `values-<n>.parquet` for each cutoff; a failing run leaves `error.json` with where the author's code broke."""
    output = Path(invocation["output"])
    output.mkdir(parents=True, exist_ok=True)
    try:
        for number, cutoff in enumerate(invocation["cutoffs"]):
            publications = values_run(invocation, roots, cutoff)
            items = []
            for item in publications:
                entry = {"name": item["name"], "kind": item["kind"], **item["details"]}
                if item["values"] is not None:
                    path = output / f"values-{number}-{item['name']}.parquet"
                    item["values"].to_parquet(path, index=False)
                    entry["path"] = path.name
                items.append(entry)
            (output / f"values-{number}.json").write_text(json.dumps({"cutoff": cutoff, "publications": items}), encoding="utf-8")
        (output / "done.json").write_text("{}", encoding="utf-8")
        return 0
    except Exception as error:
        (output / "error.json").write_text(json.dumps({"message": str(error), **failure(error, roots)}), encoding="utf-8")
        traceback.print_exc()
        return 1


def main():
    invocation = json.loads(Path(sys.argv[1]).read_text(encoding="utf-8"))
    source = Path(invocation["source"]).resolve()
    sys.path.insert(0, str(source))
    roots = {"source": source}
    if invocation.get("common"):
        sys.path.insert(0, invocation["common"])
        roots["library"] = Path(invocation["common"]).resolve()
    os.environ["TALYX_RESEARCH_OUTPUT"] = invocation["output"]
    os.environ.setdefault("MPLBACKEND", "Agg")
    return run_values(invocation, roots) if invocation["mode"] == "values" else run_report(invocation, roots)


if __name__ == "__main__":
    raise SystemExit(main())
