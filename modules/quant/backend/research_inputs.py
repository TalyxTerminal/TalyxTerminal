"""Market research columns at an explicit knowledge cutoff, in Quant's frozen-input shapes."""
from __future__ import annotations

import asyncio

from talyx_sdk import ContractError

from . import market_inputs as M
from .materials import plain
from .public_settings import milliseconds as milliseconds
from .public_settings import timestamp as timestamp

MS = 1_000_000


async def read_pages(ctx, request):
    """Read one fixed query. Market repeats its whole-query unmapped count on every page."""
    if 'asOf' not in request:
        raise ValueError('Research reads need an explicit asOf cutoff')
    rows, coverage, cursor, unmapped, note = [], [], None, None, None
    while True:
        page = plain(await ctx.call("market.read_research", {**request, 'limit': 5000, 'cursor': cursor}))
        rows.extend(await asyncio.to_thread(M.table_rows, page))
        coverage.extend(item for item in page['meta'] if item not in coverage)
        if unmapped is not None and page['unmapped'] != unmapped:
            raise ContractError('conflict', 'Market membership changed while its pages were being frozen; run again.')
        unmapped = page['unmapped']
        note = page['note']
        cursor = page['nextCursor']
        if cursor is None:
            return {'rows': rows, 'coverage': coverage, 'unmapped': unmapped, 'note': note}
        request = {**request, 'fill': 'none'}


def column_rows(columns):
    names = list(columns)
    return [dict(zip(names, values, strict=True)) for values in zip(*(columns[name] for name in names), strict=True)] if names else []

def source_problem(coverage):
    return next((item['problem'] for item in coverage if item['problem'] is not None), None)


async def instruments_by_id(ctx, identities):
    found = {}
    identities = list(dict.fromkeys(identities))
    for first in range(0, len(identities), 500):
        batch = identities[first:first + 500]
        for identity, item in zip(batch, await M.lookup(ctx, "symbols", batch), strict=True):
            if item["status"] in {"found", "redirect"}:
                found[identity] = item["instrument"]
    return found


async def describe(ctx, instruments):
    """The fields Quant implements, with Market's current source availability."""
    from ..native.formula import PRICE_FIELDS

    kinds, sources = {}, {}
    for first in range(0, len(instruments), 500):
        for kind in ("classification", "shares"):
            for item in await M.coverage(ctx, instruments[first:first + 500], kind):
                if kind not in kinds or item["source"] is not None:
                    kinds[kind] = item
                if item["source"] is not None:
                    sources[item["source"]["sourceId"]] = {**item["source"], "instruments": len(instruments)}
    fields = [{'key': key.removeprefix('$'), 'label': label, 'group': 'prices', 'basis': 'bars',
               'from': None, 'note': None, 'unavailable': None} for key, label in PRICE_FIELDS.items()]
    for name, label, kind in [('industry', 'Industry', 'classification'), ('shares', 'Shares outstanding', 'shares')]:
        source = kinds.get(kind, {}).get('source')
        fields.append({'key': name, 'label': label, 'group': 'classification', 'basis': kind if source else None,
                       'from': None, 'note': 'Current classification applied to the whole period' if name == 'industry' and source else None,
                       'unavailable': None if source else f'Market has no {label.lower()} for these instruments'})
    fields.append({'key': 'size', 'label': 'Market value', 'group': 'size', 'basis': 'raw_price_and_shares',
                   'from': None, 'note': 'Raw close times raw shares; without shares, 30-day raw dollar volume', 'unavailable': None})
    limits = [{'text': "Industry is the source's current classification applied to the whole period."}] if kinds.get('classification', {}).get('source') else []
    return {'fields': fields, 'sources': list(sources.values()), 'limits': limits}
