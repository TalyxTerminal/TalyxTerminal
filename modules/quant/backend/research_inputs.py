"""Market research columns at an explicit knowledge cutoff, in Quant's frozen-input shapes."""
from __future__ import annotations

import asyncio
import time

from talyx_sdk import ContractError
from talyx_sdk.i18n import t, tc

from . import market_inputs as M
from .materials import plain, run_task
from .public_settings import milliseconds as milliseconds
from .public_settings import timestamp as timestamp

MS = 1_000_000


async def read_pages(ctx, request):
    """Read one fixed query. Market repeats its whole-query unmapped count on every page."""
    if 'asOf' not in request:
        raise ValueError(t("Research reads need an explicit asOf cutoff"))
    rows, coverage, cursor, unmapped, note = [], [], None, None, None
    while True:
        page = plain(await ctx.call("market.read_research", {**request, 'limit': 5000, 'cursor': cursor}))
        rows.extend(await asyncio.to_thread(M.table_rows, page))
        coverage.extend(item for item in page['meta'] if item not in coverage)
        if unmapped is not None and page['unmapped'] != unmapped:
            raise ContractError('conflict', t("Market membership changed while its pages were being frozen; run again."))
        unmapped = page['unmapped']
        note = page['note']
        cursor = page['nextCursor']
        if cursor is None:
            return {'rows': rows, 'coverage': coverage, 'unmapped': unmapped, 'note': note}
        request = {**request, 'fill': 'none'}


async def industries(ctx, instruments, since, until, *, progress=None, gate=4):
    """Each instrument's industry over [since, until] (ms) as Market knows it: ``{instrumentId: [(from, until, name), ...]}``, oldest
    first, each from and until the UTC midnight (ms) of a local date, or None where open. Where Market keeps dated answers (A shares,
    read each quarter) a name has the industry of each date; where the source has only its current classification the row has no
    start, and it is applied to the whole period (the caller says so). The sector is used where the source gives one, else the
    industry. Also returns the problems Market gave (``{instrumentId: problem}``)."""
    found, problems = {}, {}
    if not instruments:
        return found, problems
    result = await run_task(ctx, "market.fetch", {"symbols": [item["instrumentId"] for item in instruments],
                                                  "data": [{"kind": "classification"}], "start": timestamp(since), "end": timestamp(until)},
                            progress=progress)
    for failure in result["failures"]:
        problems[failure["instrumentId"]] = failure["problem"]
    limit = asyncio.Semaphore(gate)

    async def one(instrument):
        async with limit:
            page = await read_pages(ctx, {"data": "classification", "symbols": [instrument["instrumentId"]], "start": timestamp(since),
                                          "end": timestamp(until), "asOf": timestamp(int(time.time() * 1000)), "fill": "none"})
        problem = source_problem(page["coverage"])
        if problem is not None:
            problems.setdefault(instrument["instrumentId"], problem)
        level = "sector" if any(row["level"] == "sector" for row in page["rows"]) else "industry"
        rows = sorted(((None if row["start"] is None else milliseconds(row["start"]), None if row["end"] is None else milliseconds(row["end"]),
                        row["name"]) for row in page["rows"] if row["level"] == level and row["name"]),
                      key=lambda row: -1 if row[0] is None else row[0])
        if rows:
            found[instrument["instrumentId"]] = [row for row in rows if (row[1] is None or row[1] > since) and (row[0] is None or row[0] <= until)]

    await asyncio.gather(*(one(item) for item in instruments))
    return {key: value for key, value in found.items() if value}, problems


def industry_at(history, moment):
    """The industry in effect at ``moment`` (ms) in a history of ``industries``, or None."""
    return next((name for start, end, name in reversed(history) if (start is None or start <= moment) and (end is None or moment < end)), None)


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


def price_labels():
    """Display labels for the built-in price and volume fields."""
    from ..native.formula import PRICE_FIELDS

    prices = {"$open": tc("price", "Open"), "$high": tc("price", "High"), "$low": tc("price", "Low"), "$close": tc("price", "Close")}
    return {key: prices[key] if key in prices else t(label) for key, label in PRICE_FIELDS.items()}


async def describe(ctx, instruments):
    """The fields Quant implements, with Market's current source availability."""
    kinds, sources = {}, {}
    for first in range(0, len(instruments), 500):
        for kind in ("classification", "shares"):
            for item in await M.coverage(ctx, instruments[first:first + 500], kind):
                if kind not in kinds or item["source"] is not None:
                    kinds[kind] = item
                if item["source"] is not None:
                    sources[item["source"]["sourceId"]] = {**item["source"], "instruments": len(instruments)}
    fields = [{'key': key.removeprefix('$'), 'label': label, 'group': 'prices', 'basis': 'bars',
               'from': None, 'note': None, 'unavailable': None} for key, label in price_labels().items()]
    for name, label, kind in [('industry', 'Industry', 'classification'), ('shares', 'Shares outstanding', 'shares')]:
        source = kinds.get(kind, {}).get('source')
        fields.append({'key': name, 'label': label, 'group': 'classification', 'basis': kind if source else None,
                       'from': None, 'note': None,
                       'unavailable': None if source else f'Market has no {label.lower()} for these instruments'})
    fields.append({'key': 'size', 'label': t("Market value"), 'group': 'size', 'basis': 'raw_price_and_shares',
                   'from': None, 'note': t("Raw close times raw shares; without shares, 30-day raw dollar volume"), 'unavailable': None})
    return {'fields': fields, 'sources': list(sources.values()), 'limits': []}
