"""Read fixed tabular materials without changing their observation timestamps."""
from __future__ import annotations

from pathlib import Path

import pandas as pd


def input_path(invocation, config, role, *, required=True):
    material = next((item for item in invocation.get("materials", []) if item.get("role") == role), None)
    value = material["path"] if material else config.get(role)
    if not value:
        if required:
            raise ValueError(f"Choose a {role} file or directory before inspecting this research.")
        return None
    path = Path(str(value).removeprefix("\\\\?\\"))
    if not path.is_absolute():
        path = Path(invocation["source"]) / path
    return path


def read_table(path):
    path = Path(path)
    if path.suffix.lower() in {".parquet", ".pq"}:
        return pd.read_parquet(path)
    if path.suffix.lower() in {".pkl", ".pickle"}:
        return pd.read_pickle(path)
    if path.suffix.lower() == ".tsv":
        return pd.read_csv(path, sep="\t")
    return pd.read_csv(path)


def coverage(frame):
    if isinstance(frame.index, pd.MultiIndex):
        dates = frame.index.get_level_values("date")
        return pd.DataFrame({"observations": frame.notna().sum(), "missing": frame.isna().sum(), "missing_fraction": frame.isna().mean(), "entities": {column: frame.loc[frame[column].notna()].index.get_level_values("entity").nunique() for column in frame}, "first_observation": {column: dates[frame[column].notna()].min() for column in frame}, "last_observation": {column: dates[frame[column].notna()].max() for column in frame}}).rename_axis("variable")
    return pd.DataFrame({"observations": frame.notna().sum(), "missing": frame.isna().sum(), "missing_fraction": frame.isna().mean(), "first_observation": frame.apply(lambda column: column.first_valid_index()), "last_observation": frame.apply(lambda column: column.last_valid_index())}).rename_axis("variable")
