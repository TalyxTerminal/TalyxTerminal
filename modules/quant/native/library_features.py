"""Align library factors by when they became known, including delayed live updates."""
from __future__ import annotations

import pandas as pd


def at_closes(rows, bars, known_by=None):
    """Each bar's features: the newest factor value known at the bar becomes available. `known_by` is the moment a library update runs: a value that
    was known by then counts as known when the bar it is about becomes available, so a model's update uses the factor's value of the same bar
    even when that value itself was made late."""
    rows = rows.assign(feature=rows['name'] + ':' + rows['field'])
    parts = []
    for (column, feature), group in rows.groupby(['column', 'feature']):
        closes = bars.loc[bars['column'] == column, ['label', 'knownAt']].drop_duplicates('label').sort_values('knownAt')
        if known_by is not None:
            close_of = dict(zip(closes['label'], closes['knownAt'], strict=True))
            edge = group['datetime'].map(close_of)
            late = (group['availableAt'] <= known_by) & edge.notna() & (edge < group['availableAt'])
            group = group.assign(availableAt=group['availableAt'].where(~late, edge).astype('int64'))
        known = group.sort_values(['availableAt', 'datetime']).drop_duplicates('availableAt', keep='last')
        aligned = pd.merge_asof(closes, known[['availableAt', 'value']], left_on='knownAt', right_on='availableAt', direction='backward')
        aligned = aligned[aligned['availableAt'].notna()]
        parts.append(aligned.assign(column=column, feature=feature)[['label', 'column', 'feature', 'value']])
    if not parts:
        raise ValueError('The selected factor versions have no values for these instruments.')
    aligned = pd.concat(parts, ignore_index=True)
    if aligned.empty:
        raise ValueError('The selected factors had no known values when the training bars became available.')
    return aligned.pivot(index=['label', 'column'], columns='feature', values='value').rename_axis(index=['datetime', 'column'])
