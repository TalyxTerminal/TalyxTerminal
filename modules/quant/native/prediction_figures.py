"""Original Qlib position analysis and LightGBM model interpretation."""
from __future__ import annotations

import pandas as pd
from native.chart_publication import plotly_figures


def feature_importance(publisher, recorder, paths, name, failures, *, figures=True):
    if "params.pkl" not in paths:
        return []
    artifacts = []
    try:
        # These are this execution's own native Recorder objects, from the
        # same trusted project already executed by TrainerR/model_state.
        model = recorder.load_object("params.pkl", trusted=True)
        if type(model).__name__ != "LGBModel":
            return []
        import plotly.graph_objects as go
        booster = model.model
        names = booster.feature_name()
        if "dataset" in paths:
            dataset = recorder.load_object("dataset", trusted=True)
            try:
                columns = dataset.handler.get_cols(col_set="feature")
            except AttributeError:
                # Native Recorder serialization omits cached _infer tables;
                # its data loader retains the declared input feature names.
                loader = getattr(dataset.handler, "data_loader", None)
                fields = getattr(loader, "fields", {})
                specification = fields.get("feature") if isinstance(fields, dict) else None
                columns = specification[1] if isinstance(specification, tuple) and len(specification) == 2 else []
            if len(columns) == len(names):
                names = list(map(str, columns))
        for order, kind in enumerate(("gain", "split"), 10):
            frame = pd.DataFrame({"feature": names, "native_feature": booster.feature_name(), "importance": booster.feature_importance(importance_type=kind)})
            artifacts.append(publisher.table(frame, f"{name}/feature-importance-{kind}", "LightGBM feature importance · " + kind))
            if not figures:  # a rolling window keeps its importance; the run's summary is drawn
                continue
            selected = frame.nlargest(30, "importance").sort_values("importance")
            figure = go.Figure(go.Bar(x=selected.importance, y=selected.feature, orientation="h"))
            artifacts.extend(plotly_figures(publisher, [figure], f"{name}/feature-importance-{kind}", "LightGBM feature importance · " + kind,
                                            chapter="training", order=order, engine="lightgbm"))
    except Exception as error:
        failures.append({"title": "Feature importance", "message": str(error)})
    return artifacts


def position_figures(publisher, recorder, paths, name, failures):
    from qlib.contrib.report.analysis_position.cumulative_return import cumulative_return_graph
    from qlib.contrib.report.analysis_position.rank_label import rank_label_graph
    from qlib.data import D
    artifacts = []
    for path in paths:
        filename = path.rsplit("/", 1)[-1]
        if not filename.startswith("positions_normal_"):
            continue
        frequency = filename.removeprefix("positions_normal_").removesuffix(".pkl")
        try:
            positions = recorder.load_object(path, trusted=True)
            report = recorder.load_object("portfolio_analysis/report_normal_" + frequency + ".pkl")
            assets = sorted({asset for position in positions.values() for asset in position.get_stock_list()})
            # Position attribution uses T -> T+1 labels, regardless of the
            # training target's horizon. The original provider owns prices.
            provider_frequency = "day" if frequency == "1day" else frequency
            label = D.features(assets, ["Ref($close, -1)/$close-1"], report.index.min(), report.index.max(), freq=provider_frequency)
            if label.empty:
                raise ValueError("The fixed provider has no next-period position labels at frequency " + provider_frequency)
            label.columns = ["label"]
            artifacts.append(publisher.table(label, f"{name}/position-label-{frequency}", "Position analysis · next-period realized label"))
            for stem, draw, order in [("position-cumulative", lambda: cumulative_return_graph(positions, report, label.copy(), show_notebook=False), 20),
                                       ("rank-label", lambda: rank_label_graph(positions, label.copy(), show_notebook=False), 30)]:
                try:
                    artifacts.extend(plotly_figures(publisher, draw(), f"{name}/{stem}-{frequency}", stem.replace("-", " ").title(),
                                                    chapter="performance" if stem == "position-cumulative" else "quality", order=order, engine="qlib"))
                except Exception as error:
                    failures.append({"title": stem, "message": str(error)})
        except Exception as error:
            failures.append({"title": "Position analysis · " + frequency, "message": str(error)})
    return artifacts
