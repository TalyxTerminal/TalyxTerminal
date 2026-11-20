"""One native figure per artifact, with unformatted plotted values for export."""
from __future__ import annotations

import numpy as np
import pandas as pd


def plotly_figures(publisher, figures, name, title, *, chapter, engine, order=1):
    artifacts = []
    if hasattr(figures, "to_plotly_json"):
        figures = [figures]
    for number, figure in enumerate(figures, 1):
        stem = name if number == 1 else f"{name}-{number}"
        native_title = getattr(getattr(figure.layout, "title", None), "text", None)
        artifacts.append(publisher.plotly(figure, stem, native_title or title, chapter=chapter,
                                         order=order + number - 1, engine=engine))
        rows = []
        for trace_number, trace in enumerate(figure.data):
            values = {key: getattr(trace, key, None) for key in ("x", "y", "z", "open", "high", "low", "close", "labels", "values")}
            values = {key: value for key, value in values.items() if value is not None}
            for key, value in values.items():
                array = np.asarray(value, dtype=object)
                for index in np.ndindex(array.shape):
                    rows.append({"trace": trace_number, "series": str(trace.name or trace.type), "field": key,
                                 "row": index[0], "column": index[1] if len(index) > 1 else None,
                                 "value": array[index]})
        if rows:
            # Date axes, category labels and numeric observations have separate
            # columns so Parquet never coerces a numeric value to display text.
            for row in rows:
                value = row.pop("value")
                if isinstance(value, (int, float, np.number)):
                    row["value"] = value
                elif hasattr(value, "isoformat"):
                    row["coordinate"] = value.isoformat()
                else:
                    row["coordinate"] = str(value) if value is not None else None
            artifacts.append(publisher.table(pd.DataFrame(rows), stem + "-values", title + " · plotted values", chapter=chapter))
    return artifacts


def svg_figure(publisher, figure, name, title, *, chapter, engine, order=1):
    from matplotlib.collections import PathCollection
    from matplotlib.patches import Rectangle
    artifacts = [publisher.svg(figure, name, title, chapter=chapter, order=order, engine=engine)]
    rows = []
    for panel, axis in enumerate(figure.axes):
        for line_number, line in enumerate(axis.lines):
            for observation, (x, y) in enumerate(zip(line.get_xdata(orig=False), line.get_ydata(orig=False))):
                rows.append({"panel": panel, "series": line_number, "component": "line", "observation": observation, "x": x, "y": y})
        for collection_number, collection in enumerate(axis.collections):
            series = len(axis.lines) + collection_number
            if hasattr(collection, "get_segments"):
                for segment, points in enumerate(collection.get_segments()):
                    for observation, (x, y) in enumerate(points):
                        rows.append({"panel":panel, "series":series, "component":"segment", "segment":segment,
                                     "observation":observation, "x":float(x), "y":float(y)})
            elif isinstance(collection, PathCollection):
                for observation, (x, y) in enumerate(collection.get_offsets()):
                    rows.append({"panel":panel, "series":series, "component":"points", "observation":observation,
                                 "x":float(x), "y":float(y)})
            elif collection.get_array() is not None:
                values = np.ma.asarray(collection.get_array())
                for index in np.ndindex(values.shape):
                    value = values[index]
                    rows.append({"panel":panel, "series":series, "component":"mesh", "observation":index[0],
                                 "column":index[1] if len(index)>1 else None, "value":np.nan if np.ma.is_masked(value) else float(value)})
            else:
                for polygon, path in enumerate(collection.get_paths()):
                    for observation, (x, y) in enumerate(path.vertices):
                        rows.append({"panel":panel, "series":series, "component":"polygon", "segment":polygon,
                                     "observation":observation, "x":float(x), "y":float(y)})
        for patch_number, patch in enumerate(axis.patches):
            if isinstance(patch, Rectangle):
                rows.append({"panel":panel, "series":patch_number, "component":"bar", "observation":patch_number,
                             "x":float(patch.get_x()), "y":float(patch.get_height()),
                             "baseline":float(patch.get_y()), "width":float(patch.get_width())})
    if rows:
        artifacts.append(publisher.table(pd.DataFrame(rows), name + "-values", title + " · plotted values", chapter=chapter))
    return artifacts
