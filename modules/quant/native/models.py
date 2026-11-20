"""Qlib Dataset, Trainer, TaskGen, Collector, Record and original reports."""
from __future__ import annotations

import pandas as pd
from joblib import parallel_backend
from qlib.model.trainer import begin_task_train, end_task_train

from .model_training import validate_training_windows


class ModelTaskError(ValueError):
    def __init__(self, error, recorder):
        super().__init__(str(error))
        self.recorder = recorder


def checked_task_train(task, experiment_name, recorder_name=None):
    validate_training_windows(task)
    # Processing helpers such as Alpha360's ProcessInf use joblib directly.
    # Keep them in this isolated worker, as in advanced model workflows.
    with parallel_backend("threading"):
        recorder = begin_task_train(task, experiment_name, recorder_name)
        try:
            return end_task_train(recorder, experiment_name, trusted=True)
        except Exception as error:
            raise ModelTaskError(error, recorder) from error


def publish_recorder(recorder, task, publisher, name, title, *, include_model=True, figures=True):
    """One Recorder's tables, numbers and figures. A rolling run's windows pass `figures=False`: each window keeps its numbers and
    tables, and only the whole run's summary is drawn (one set of figures a run)."""
    from native.chart_publication import plotly_figures as draw

    def plotly_figures(*args, **kwargs):
        return draw(*args, **kwargs) if figures else []
    from qlib.contrib.report.analysis_model.analysis_model_performance import (
        model_performance_graph,
    )
    from qlib.contrib.report.analysis_position.report import report_graph
    from qlib.contrib.report.analysis_position.risk_analysis import risk_analysis_graph
    artifacts, failures = [], []
    paths = recorder.list_artifacts()
    # Recorders retain all native objects; converted tables are view materials.
    native = {"recorderId": recorder.id, "experimentId": recorder.experiment_id, "uri": recorder.uri}
    # Keep each run's native bytes readable even when a native online routine
    # later updates the original store. The experiment itself remains native.
    try:
        from native.model_state import archive_recorder, publish_states
        native_files = archive_recorder(recorder, publisher, name)
        artifacts.extend(native_files)
        artifacts.extend(publish_states(recorder, task, publisher, name, native_files, include_model=include_model))
    except Exception as error:
        failures.append({"title": "Native Recorder files", "message": str(error)})
    try:
        from mlflow.tracking import MlflowClient
        from plotly import graph_objects as go
        client = MlflowClient(tracking_uri=recorder.uri)
        history = []
        figure = go.Figure()
        for metric in recorder.list_metrics():
            points = client.get_metric_history(recorder.id, metric)
            if len(points) > 1:
                history.extend({"metric": metric, "step": point.step, "value": point.value, "timestamp": point.timestamp} for point in points)
                figure.add_trace(go.Scatter(x=[point.step for point in points], y=[point.value for point in points], name=metric, mode="lines"))
        if history:
            artifacts.append(publisher.table(pd.DataFrame(history), f"{name}/learning-history", "Native learning metrics", **native))
            artifacts.extend(plotly_figures(publisher, [figure], f"{name}/learning-curves", "Training and validation learning curves", chapter="training", engine="lightgbm"))
    except Exception as error:
        failures.append({"title": "Learning curves", "message": str(error)})
    actual_ranges = {}
    quality_metrics = {}
    for filename, label in (("pred.pkl", "Prediction"), ("label.pkl", "Label")):
        if filename in paths:
            values = recorder.load_object(filename)
            dates = values.index.get_level_values("datetime")
            if len(dates):
                actual_ranges[label.lower()] = [dates.min().isoformat(), dates.max().isoformat()]
            artifacts.append(publisher.table(values, f"{name}/{filename[:-4]}", label, **native))
    if {"pred.pkl", "label.pkl"}.issubset(paths):
        pred, label = recorder.load_object("pred.pkl"), recorder.load_object("label.pkl")
        analysis_record = next((record for record in task.get("record", []) if isinstance(record, dict) and record.get("class") == "SigAnaRecord"), {})
        target_column = analysis_record.get("kwargs", {}).get("label_col", 0)
        aligned = pd.concat([pred.iloc[:, 0].rename("score"), label.iloc[:, target_column].rename("label")], axis=1)
        try:
            from native.model_quality import prediction_quality
            quality, quality_metrics = prediction_quality(pred, label, target_column)
            artifacts.append(publisher.table(quality, f"{name}/prediction-quality", "Prediction quality by observation", **native))
        except Exception as error:
            failures.append({"title": "Prediction quality", "message": str(error)})
        try:
            artifacts.extend(plotly_figures(publisher, model_performance_graph(aligned, N=min(5, len(aligned.index.get_level_values("instrument").unique())), show_notebook=False), f"{name}/signal", "Signal analysis", chapter="quality", engine="qlib"))
        except Exception as error:
            failures.append({"title": "Signal figures", "message": str(error)})
        try:
            from qlib.contrib.report.analysis_position.score_ic import _get_score_ic, score_ic_graph
            artifacts.append(publisher.table(_get_score_ic(aligned), f"{name}/score-ic", "Score IC and Rank IC", **native))
            artifacts.extend(plotly_figures(publisher, score_ic_graph(aligned, show_notebook=False), f"{name}/score-ic", "Score IC", chapter="quality", engine="qlib", order=10))
        except Exception as error:
            failures.append({"title": "Score IC", "message": str(error)})
    from native.prediction_figures import feature_importance, position_figures
    artifacts.extend(feature_importance(publisher, recorder, paths, name, failures, figures=figures) if include_model else [])
    portfolio_paths = recorder.list_artifacts("portfolio_analysis") if "portfolio_analysis" in paths else []
    artifacts.extend(position_figures(publisher, recorder, portfolio_paths, name, failures) if figures else [])
    for path in portfolio_paths:
        filename = path.rsplit("/", 1)[-1]
        if filename.startswith("report_normal_"):
            frequency = filename.removeprefix("report_normal_").removesuffix(".pkl")
            report = recorder.load_object(path)
            if len(report):
                actual_ranges[filename[:-4]] = [report.index.min().isoformat(), report.index.max().isoformat()]
            report_title = "Absolute returns and costs · no benchmark" if report.attrs.get("benchmarkMeaning") else "Returns, costs and benchmark"
            artifacts.append(publisher.table(report, f"{name}/{filename[:-4]}", report_title, frequency=frequency, **native))
            from native.model_performance import account_curve
            curve, conditions = account_curve(report, task)
            if not curve.empty and "account" in curve:
                publisher.named_table(curve, f"{name}/account_curve_{frequency}", "curve", instrument="research_account", sourceAxes={"frequency": frequency}, units={"account": "native_account_currency", "net_value": "ratio"})
                artifacts.append(publisher.table(curve, f"{name}/account_curve_{frequency}", "Account value and benchmark" if "benchmark_account" in curve else "Account value · no comparable benchmark", frequency=frequency, **native))
                from plotly import graph_objects as go
                columns = [column for column in ("net_value", "benchmark_net_value", "reference_net_value") if column in curve] or [column for column in ("account", "benchmark_account") if column in curve]
                labels = {"net_value":"Strategy net value after costs", "benchmark_net_value":"Benchmark net value", "reference_net_value":"Zero-return reference · no benchmark", "account":"Native account value", "benchmark_account":"Benchmark account value"}
                figure = go.Figure([go.Scatter(x=curve.index, y=curve[column], name=labels[column], mode="lines") for column in columns])
                artifacts.extend(plotly_figures(publisher, [figure], f"{name}/account-curve-{frequency}", "Cumulative net asset value and benchmark" if "net_value" in curve else "Cumulative account value and benchmark", chapter="performance", engine="qlib", order=1))
            publisher.index.setdefault("modelBacktests", {}).setdefault(name, {})[frequency] = conditions
            try:
                artifacts.extend(plotly_figures(publisher, report_graph(report.copy(), show_notebook=False, time_format="%Y-%m-%d %H:%M:%S"), f"{name}/{filename[:-4]}-report", "Research backtest · cumulative period returns", chapter="performance", engine="qlib", order=10))
            except Exception as error:
                failures.append({"title": "Financial figures", "message": str(error)})
        elif filename.startswith("port_analysis_"):
            frequency = filename.removeprefix("port_analysis_").removesuffix(".pkl")
            analysis = recorder.load_object(path)
            artifacts.append(publisher.table(analysis, f"{name}/{filename[:-4]}", "Risk analysis", frequency=frequency, **native))
            try:
                artifacts.extend(plotly_figures(publisher, risk_analysis_graph(analysis, show_notebook=False), f"{name}/{filename[:-4]}-risk", "Risk analysis", chapter="risk", engine="qlib"))
            except Exception as error:
                failures.append({"title": "Risk figures", "message": str(error)})
    unit = {"id": name, "title": title, "state": "completed", **native, "segments": task.get("dataset", {}).get("kwargs", {}).get("segments"), "actualRanges": actual_ranges, "metrics": {**quality_metrics, **recorder.list_metrics()}, "reportErrors": failures}
    existing = next((item for item in publisher.index["units"] if item["id"] == name), None)
    if existing is None:
        publisher.index["units"].append(unit)
    else:
        existing.update(unit)
    publisher.section(name, title, artifacts, **native)
