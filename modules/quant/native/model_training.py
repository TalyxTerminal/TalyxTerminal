"""Validate explicitly declared learning intervals before native training."""
from __future__ import annotations

import re

import pandas as pd


def validate_training_windows(task):
    dataset = task.get("dataset", {}).get("kwargs", {})
    train = dataset.get("segments", {}).get("train")
    handler = dataset.get("handler", {})
    if not train or not isinstance(handler, dict):
        return
    parameters = handler.get("kwargs", {})
    declarations = [("handler", parameters)]
    for kind in ("infer_processors", "learn_processors", "shared_processors"):
        for number, processor in enumerate(parameters.get(kind, [])):
            if isinstance(processor, dict):
                declarations.append((f"{kind}[{number}] {processor.get('class', '')}", processor.get("kwargs", {})))
    for name, settings in declarations:
        for key, boundary, earlier in (("fit_start_time", train[0], True), ("fit_end_time", train[1], False)):
            value = settings.get(key)
            if value is None or boundary is None:
                continue
            actual, permitted = pd.Timestamp(value), pd.Timestamp(boundary)
            if (actual < permitted if earlier else actual > permitted):
                raise ValueError(f"{name} {key}={value} falls outside the native training segment {train}. Fix its explicit learning interval before training.")
    validate_return_target_boundaries(dataset)


def validate_return_target_boundaries(dataset, calendar=None):
    """A preset's labels must be observable before the next held-out segment."""
    handler = dataset.get("handler", {}).get("kwargs", {})
    labels = handler.get("label", handler.get("data_loader", {}).get("kwargs", {}).get("config", {}).get("label", []))
    if isinstance(labels, str):
        labels = [labels]
    elif labels and isinstance(labels[0], list):
        labels = labels[0]
    lookaheads = []
    for label in labels:
        match = re.fullmatch(r"Ref\(\$close,\s*-(\d+)\)\s*/\s*Ref\(\$close,\s*-1\)\s*-\s*1", label)
        if match:
            lookaheads.append(int(match[1]))
    if not lookaheads:
        return
    lookahead = max(lookaheads)
    if calendar is None:
        from qlib.data import D
        frequency = handler.get("freq", handler.get("data_loader", {}).get("kwargs", {}).get("freq", "day"))
        calendar = D.calendar(freq=frequency)
    dates = pd.DatetimeIndex(calendar)
    segments = [(name, value) for name, value in dataset.get("segments", {}).items() if name in {"train", "valid", "test"} and value and all(value)]
    segments.sort(key=lambda item: pd.Timestamp(item[1][0]))
    for (name, interval), (next_name, next_interval) in zip(segments, segments[1:]):
        last = dates.searchsorted(pd.Timestamp(interval[1]), side="right") - 1
        boundary = dates.searchsorted(pd.Timestamp(next_interval[0]), side="left")
        if last >= 0 and last + lookahead >= boundary:
            permitted = boundary - lookahead - 1
            suggestion = f" Move {name} end to {dates[permitted].isoformat()} or earlier." if permitted >= 0 else " Extend the data sample."
            raise ValueError(f"The prediction target uses {lookahead} future observations. Labels at the end of {name} reach into {next_name}." + suggestion)
