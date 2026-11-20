"""Publish only complete native outputs, keeping the result index atomic."""
from __future__ import annotations

import json
import math
import os
import time
from pathlib import Path


def safe(value):
    if isinstance(value, dict):
        return {str(k): safe(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [safe(v) for v in value]
    if hasattr(value, "item"):
        return safe(value.item())
    if isinstance(value, float) and not math.isfinite(value):
        return None
    if hasattr(value, "isoformat"):
        return value.isoformat()
    return value


class Publisher:
    def __init__(self, invocation):
        self.invocation = invocation
        self.root = Path(invocation["output"])
        self.root.mkdir(parents=True, exist_ok=True)
        self.index = {"runId": invocation["runId"], "operation": invocation.get("operation", "native.run"), "sourceRefs": invocation.get("sourceRefs", []), "recipeRef": invocation.get("recipeRef"), "status": "running", "units": [], "sections": [], "artifacts": [], "metrics": {}, "progress": {"completed": 0, "total": None}}
        self.write()
        research = invocation.get('research')
        index = invocation.get('index') or (research['info'].get('index') if research is not None else None)
        if index is not None:
            self.index['dataLimitations'] = {'statement': index['limitation'], 'exclusions': index['exclusions']}
            if index['exclusions']:
                import pandas as pd
                artifact = self.table(pd.DataFrame(index['exclusions']), 'data/index-exclusions', 'Excluded historical index members and periods')
                self.section('index-data-limitations', 'Excluded index members and periods', [artifact['name']], note=index['limitation'])
            self.write()

    def write(self):
        temporary = self.root / "result-index.pending"
        temporary.write_text(json.dumps(safe(self.index), ensure_ascii=False, allow_nan=False), encoding="utf-8")
        for attempt in range(20):
            try:
                os.replace(temporary, self.root / "result-index.json")
                break
            except PermissionError:
                if attempt == 19:
                    raise
                time.sleep(.025)

    def artifact(self, path, title, kind, **metadata):
        item = {"name": path.relative_to(self.root).as_posix(), "title": title, "kind": kind, **metadata}
        self.index["artifacts"].append(item)
        return item

    def named_table(self, frame, name, type, **metadata):
        from .topic_artifacts import table
        return table(self, frame, name, type, **metadata)

    def named_file(self, path, name, type, **metadata):
        from .topic_artifacts import metadata as publish_metadata
        return publish_metadata(self, path, name, type, **metadata)

    def table(self, frame, name, title, **metadata):
        import pandas as pd
        if isinstance(frame, pd.Series):
            frame = frame.to_frame()
        frame = frame.reset_index()
        frame.columns = [" · ".join(str(x) for x in col if str(x)) if isinstance(col, tuple) else str(col) for col in frame.columns]
        path = self.root / (name + ".parquet")
        path.parent.mkdir(parents=True, exist_ok=True)
        frame.to_parquet(path, index=False)
        return self.artifact(path, title, "table", rows=len(frame), columns=list(frame.columns), **metadata)

    def plotly(self, figure, name, title, *, chapter, order, engine, note=None):
        import plotly.io as pio
        path = self.root / (name + ".plotly.json")
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(pio.to_json(figure, validate=False), encoding="utf-8")
        return self.artifact(path, title, "plotly", chapter=chapter, order=order,
                             engine=engine, **({"note": note} if note else {}))

    def svg(self, matplotlib_figure, name, title, *, chapter, order, engine, note=None):
        import matplotlib as mpl
        from matplotlib.text import Text
        figure = matplotlib_figure
        # Restyle only chart furniture; native data lines and fills retain colors.
        for text in figure.findobj(Text):
            text.set_color("#a1a1aa")
            text.set_fontfamily(["Inter", "Segoe UI", "sans-serif"])
            box = text.get_bbox_patch()
            if box is not None:
                box.set_facecolor("#111114")
                box.set_edgecolor("#2a2a2e")
                box.set_alpha(.9)
        for axis in figure.axes:
            axis.tick_params(colors="#a1a1aa", which="both")
            axis.xaxis.get_offset_text().set_color("#a1a1aa")
            axis.yaxis.get_offset_text().set_color("#a1a1aa")
            for spine in axis.spines.values():
                spine.set_color("#3a3a40")
            legend = axis.get_legend()
            if legend is not None:
                legend.get_frame().set_facecolor("#111114")
                legend.get_frame().set_edgecolor("#2a2a2e")
                legend.get_frame().set_alpha(.9)
            for line in [*axis.get_xgridlines(), *axis.get_ygridlines()]:
                line.set_color("#2a2a2e")
        path = self.root / (name + ".svg")
        path.parent.mkdir(parents=True, exist_ok=True)
        with mpl.rc_context({"svg.fonttype": "none", "font.family": ["Inter", "Segoe UI", "sans-serif"],
                             "text.color": "#a1a1aa", "axes.labelcolor": "#a1a1aa",
                             "axes.edgecolor": "#a1a1aa", "xtick.color": "#a1a1aa",
                             "ytick.color": "#a1a1aa", "grid.color": "#2a2a2e"}):
            figure.savefig(path, format="svg", bbox_inches="tight", transparent=True)
        return self.artifact(path, title, "svg", chapter=chapter, order=order,
                             engine=engine, **({"note": note} if note else {}))

    def section(self, identity, title, artifacts, **metadata):
        self.index["sections"].append({"id": identity, "title": title, "artifacts": artifacts, **metadata})
        self.write()
