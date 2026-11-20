"""Freeze library factors selected by a research object's declared parameters."""
from __future__ import annotations

import asyncio

import pandas as pd
from talyx_sdk import ContractError

from .library_inputs import named_items


async def prepare(library, definitions, params, research, directory, *, frozen=None, known_by=None):
    """The rows of each factor version the object's library parameters name, for its training range and names. `known_by` is set by a library
    update: what the factors had published by then counts as known at the close of the bar it is about (see native.library_features)."""
    items, frames = [], []
    versions = None if frozen is None else {item['parameter']: item['versionId'] for item in frozen}
    for parameter, name in named_items(definitions, params):
        item = library.resolve(name, kind='factor') if versions is None else library.read_version(versions[parameter])
        if item['state'] == 'delisted':
            raise ContractError('not_found', f'{name} was delisted from the library.')
        async with library.reading:
            rows = await asyncio.to_thread(library.store.read_values, item['versionId'])
        frame = await asyncio.to_thread(selected_values, rows, research["info"])
        if frame.empty:
            raise ContractError('unavailable', f'{name} has no factor values for this training range and instrument selection.')
        frames.append(frame.assign(name=name, versionId=item['versionId']))
        items.append({key: item[key] for key in ('name', 'versionId', 'version')} | {'parameter': parameter})
    if not frames:
        return None
    path = directory / 'training-factors.parquet'
    await asyncio.to_thread(write_values, frames, path)
    return {'path': str(path), 'items': items, **({'knownBy': known_by} if known_by is not None else {})}


def selected_values(rows, info):
    frame = pd.DataFrame(rows)
    instruments = {entry['instrumentId'] for entry in info['instruments']}
    return frame[frame['instrument'].isin(instruments) & frame['datetime'].between(info['since'], info['until'])]


def write_values(frames, path):
    pd.concat(frames, ignore_index=True).to_parquet(path, index=False)
