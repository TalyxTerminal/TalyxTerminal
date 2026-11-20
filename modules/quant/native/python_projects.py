"""The bag of report helpers a native result collection (backtest output, fitted results, recorders) publishes through."""
from __future__ import annotations

import html
import json
import pickle
import re
from pathlib import Path


class NativeCollector:
    def __init__(self, invocation, publisher):
        self.invocation, self.publisher = invocation, publisher
        self.source = Path(invocation["source"]).resolve()
        self.root = publisher.root.resolve()
        self.seen = {}
        self.serial, self.failures = 0, []

    def name(self, title):
        self.serial += 1
        return f"python/{self.serial:03d}-{re.sub(r'[^a-zA-Z0-9_-]', '-', title)[:70]}"

    def guarded(self, title, callback):
        try:
            return callback()
        except Exception as error:
            self.failures.append({"title": title, "message": str(error)})

    def section(self, identity, title, artifacts, **metadata):
        self.publisher.section(identity, title, artifacts, **metadata)

    def table(self, value, title):
        identity = id(value)
        if identity in self.seen:
            return
        self.seen[identity] = value
        name = self.name(title)
        self.section(name, title, [self.publisher.table(value, name, title)])

    def figure(self, value, title):
        if hasattr(value, "savefig") and not any(artist is not value.patch and artist.get_visible() for artist in value.get_children()):
            # Native libraries may leave an unused pyplot canvas open. Do not
            # publish it or mark it seen: the author can still draw on it later.
            return
        identity = id(value)
        if identity in self.seen:
            return
        self.seen[identity] = value
        name = self.name(title)
        engine = {"model":"qlib", "factor":"alphalens", "portfolio":"skfolio", "statistics":"statsmodels", "strategy":"nautilus"}.get(self.invocation.get("workbench"), "python")
        chapter = {"model":"quality", "factor":"summary", "portfolio":"performance", "statistics":"fit", "strategy":"performance"}.get(self.invocation.get("workbench"), "summary")
        from native.chart_publication import plotly_figures, svg_figure
        if hasattr(value, "to_html"):
            artifacts = plotly_figures(self.publisher, [value], name, title, chapter=chapter, order=self.serial, engine=engine)
        else:
            artifacts = svg_figure(self.publisher, value, name, title, chapter=chapter, order=self.serial, engine=engine)
        self.section(name, title, artifacts)

    def fitted(self, result, title="Native estimate"):
        if id(result) in self.seen:
            return
        self.seen[id(result)] = result
        import pandas as pd
        import plotly.graph_objects as go
        name, artifacts = self.name(title), []
        path = self.root / (name + ".html")
        path.parent.mkdir(parents=True, exist_ok=True)
        summary = result.summary() if callable(result.summary) else result.summary
        body = summary.as_html() if hasattr(summary, "as_html") else "<pre>" + html.escape(str(summary)) + "</pre>"
        path.write_text("<!doctype html><meta charset='utf-8'><style>body{background:#111;color:#ddd;font:14px sans-serif}table{border-collapse:collapse}td,th{padding:5px;text-align:right}</style>" + body, encoding="utf-8")
        artifacts.append(self.publisher.artifact(path, title + " · original summary", "html"))
        if hasattr(result, "params"):
            if isinstance(result.params, pd.DataFrame):
                for attribute in ("params", "bse", "std_errors", "pvalues"):
                    value = getattr(result, attribute, None)
                    if value is not None:
                        artifacts.append(self.publisher.table(value, name + "-" + attribute, "Native " + attribute))
            else:
                params = pd.Series(result.params)
                artifacts.append(self.publisher.table(pd.DataFrame({"coefficient": params, "standard_error": pd.Series(getattr(result, "bse", getattr(result, "std_errors", getattr(result, "std_err", None))), index=params.index), "p_value": pd.Series(getattr(result, "pvalues", None), index=params.index)}), name + "-coefficients", "Native coefficients"))
        series = {}
        for attribute in ("fittedvalues", "fitted_values", "resid", "resids", "conditional_volatility", "std_resid"):
            value = getattr(result, attribute, None)
            if value is not None:
                if not isinstance(value, (pd.DataFrame, pd.Series)):
                    value = pd.Series(value)
                frame = value.to_frame(attribute) if isinstance(value, pd.Series) else value
                artifacts.append(self.publisher.table(frame, name + "-" + attribute, attribute.replace("_", " ").title()))
                for column in frame.columns:
                    series[str(column)] = frame[column]
        if series:
            figure = go.Figure()
            for label, values in series.items():
                figure.add_scatter(x=values.index, y=values, name=label)
            artifacts.append(self.publisher.plotly(figure, name + "-curves", "Native fitted values and diagnostics", chapter="fit", order=1, engine="statsmodels"))
        if hasattr(result, "get_influence"):
            from native.statistics_figures import regression_figures
            artifacts.extend(regression_figures(self.publisher, result, name + "-diagnostics"))
        if hasattr(result, "plot_diagnostics"):
            from native.statistics_figures import capture
            artifacts.extend(capture(self.publisher, lambda: result.plot_diagnostics(), name + "-diagnostics", "Native time-series diagnostics", "residuals", 1))
        if type(result).__module__.startswith("arch") and hasattr(result, "plot"):
            import matplotlib.pyplot as plt
            from native.chart_publication import svg_figure
            figure = result.plot()
            artifacts.extend(svg_figure(self.publisher, figure, name + "-volatility", "Native arch volatility", chapter="volatility", engine="arch"))
            plt.close(figure)
        raw = self.root / (name + ".pkl")
        with raw.open("wb") as stream:
            pickle.dump(result, stream, protocol=pickle.HIGHEST_PROTOCOL)
        artifacts.append(self.publisher.artifact(raw, title + " · complete native result", "file"))
        self.section(name, title, artifacts)
        self.publisher.index["units"].append({"id": name, "title": title, "state": "completed", "nativeKind": type(result).__name__})
        self.publisher.write()

    def recorder(self, recorder):
        name = self.name("recorder-" + recorder.id)
        artifacts = []
        native = {"recorderId": recorder.id, "experimentId": recorder.experiment_id, "uri": recorder.uri, "nativeStatus": recorder.status}
        locator = self.root / name / "recorder.json"
        locator.parent.mkdir(parents=True, exist_ok=True)
        locator.write_text(json.dumps({**native, "metrics": recorder.list_metrics(), "params": recorder.list_params(), "tags": recorder.list_tags()}, default=str, indent=2), encoding="utf-8")
        artifacts.append(self.publisher.artifact(locator, "Native recorder and parameters", "file", **native))
        self.section(name + "-original", "Recorder · complete original artifacts", artifacts, **native)
        try:
            task = recorder.load_object("task", trusted=True)
        except Exception:
            task = {}
        from native.models import publish_recorder
        publish_recorder(recorder, task if isinstance(task, dict) else {}, self.publisher, name, "Recorder · native analysis")
        self.publisher.index["units"][-1]["nativeStatus"] = recorder.status
        self.publisher.write()
