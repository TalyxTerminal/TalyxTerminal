"""Readable prediction evaluation, using the same Qlib calculation as SigAnaRecord."""
from __future__ import annotations

import math

import pandas as pd
from qlib.contrib.eva.alpha import calc_ic


def prediction_quality(prediction, labels, label_column=0):
    score = prediction.iloc[:, 0]
    target = labels.iloc[:, label_column]
    ic, rank_ic = calc_ic(score, target)
    aligned = pd.concat([score.rename("score"), target.rename("target")], axis=1)
    finite = aligned.replace([float("inf"), -float("inf")], float("nan")).dropna()
    counts = finite.groupby(level="datetime").size().rename("paired_assets")
    daily = pd.concat([ic.rename("IC"), rank_ic.rename("Rank IC"), counts], axis=1)
    metrics = {}
    for key, values in (("IC", ic), ("Rank IC", rank_ic)):
        mean, deviation = values.mean(), values.std()
        metrics[key] = float(mean) if pd.notna(mean) and math.isfinite(mean) else None
        metrics[key + "IR"] = float(mean / deviation) if pd.notna(deviation) and deviation > 0 and math.isfinite(mean / deviation) else None
    metrics.update({"Evaluated dates": int(ic.notna().sum()), "Paired observations": len(finite), "Prediction dates": int(aligned.index.get_level_values("datetime").nunique())})
    return daily, metrics
