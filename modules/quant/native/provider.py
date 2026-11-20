"""Material conversion into Qlib's documented local binary provider layout."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path

import numpy as np
import pandas as pd


def write_provider(frames, root, metadata):
    root = Path(root)
    if root.exists():
        raise ValueError("Choose a new provider output directory.")
    root.mkdir(parents=True)
    dates = sorted(set(time for frame in frames.values() for time in frame.index))
    calendar = pd.DatetimeIndex(dates)
    frequency = metadata.get("frequency", "day")
    time_format = "%Y-%m-%d" if frequency == "day" else "%Y-%m-%d %H:%M:%S"
    (root / "calendars").mkdir()
    (root / "instruments").mkdir()
    # Finite future range is the actual material; no fabricated trading sessions.
    for name in (frequency + ".txt", frequency + "_future.txt"):
        (root / "calendars" / name).write_text("\n".join(calendar.strftime(time_format)), encoding="utf-8")
    members = []
    for asset, frame in frames.items():
        if not asset or any(c in asset for c in '<>:"/\\|?*'):
            raise ValueError("The provider asset identifier needs a valid file name.")
        frame = frame.sort_index()
        if frame.index.duplicated().any():
            raise ValueError(f"Duplicate material times for {asset}.")
        required = {"open", "high", "low", "close", "volume", "factor"}
        if not required.issubset(frame.columns):
            raise ValueError(f"{asset} needs OHLCV and a real adjustment factor or a complete native provider.")
        first, last = calendar.get_loc(frame.index.min()), calendar.get_loc(frame.index.max())
        aligned = frame.reindex(calendar[first:last + 1])
        directory = root / "features" / asset.lower()
        directory.mkdir(parents=True)
        for field in required:
            np.hstack([[first], aligned[field].to_numpy()]).astype("<f4").tofile(directory / f"{field}.{frequency}.bin")
        members.append(f"{asset}\t{frame.index.min().strftime(time_format)}\t{frame.index.max().strftime(time_format)}")
    (root / "instruments" / "all.txt").write_text("\n".join(members), encoding="utf-8")
    receipt = {**metadata, "assets": list(frames), "calendarRows": len(calendar), "range": [str(calendar[0]), str(calendar[-1])], "files": [{"path": p.relative_to(root).as_posix(), "sha256": hashlib.sha256(p.read_bytes()).hexdigest()} for p in sorted(root.rglob("*")) if p.is_file()]}
    (root / "material.json").write_text(json.dumps(receipt, indent=2), encoding="utf-8")
    return receipt

