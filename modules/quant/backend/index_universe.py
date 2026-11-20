"""Freeze Market 2.0 membership intervals and preserve missing historical identities."""

import asyncio
from datetime import UTC, datetime

from talyx_sdk import ContractError

from .public_settings import milliseconds, timestamp
from .research_inputs import instruments_by_id, read_pages, source_problem

MISSING_PRICE = 'The free source has no prices for this past member'
MISSING_SERIES = {'not_found', 'no_data', 'unsupported', 'history_unavailable', 'market_identity_mismatch'}


def summary(index):
    """What a record keeps of a frozen index: the selection, the period, the limitation and the counts. The members and the exclusions
    (megabytes for the S&P 500 over ten years) stay in the file of the frozen data."""
    return {key: index[key] for key in ('selection', 'since', 'until', 'limitation', 'assumedSince') if key in index} | {
        'memberCount': len({member['assetId'] for interval in index['intervals'] for member in interval['members']}),
        'mappedCount': len(index['instruments']),
        'excludedCount': len({item['assetId'] for item in index['exclusions']}),
        'unmappedCount': index['unmappedCount']}


def member_spans(index):
    """Each mapped member's stretches in the index, {instrumentId: [[since, until], ...]} in inclusive ms: who was a member on a day,
    without the full member identities of every interval."""
    spans = {}
    for interval in index['intervals']:
        for member in interval['members']:
            if member['instrument'] is not None:
                span = [member['effectiveTime'], member['effectiveUntil']]
                held = spans.setdefault(member['instrument']['instrumentId'], [])
                if span not in held:
                    held.append(span)
    return spans


def gaps(index, without_prices):
    """What the index's members lack, from the frozen record: how many members over the whole period have no Market instrument (kind 'unmapped'),
    the most in one stretch and the year it falls in, and how many mapped members had no prices. None when nothing is lacking."""
    unmapped = {item['assetId'] for item in index['exclusions'] if item.get('kind') == 'unmapped'}
    worst, year = 0, None
    for interval in index['intervals']:
        lacking = sum(1 for member in interval['members'] if member['instrument'] is None)
        if lacking > worst:
            worst, year = lacking, datetime.fromtimestamp(interval['since'] / 1000, UTC).year
    if not unmapped and not without_prices:
        return None
    return {'unmapped': index['unmappedCount'], 'worst': worst, 'year': year, 'withoutPrices': without_prices}


def gap_text(found):
    """The data-settings limitation for those gaps, in numbers."""
    parts = []
    if found['unmapped']:
        parts.append(f"{found['unmapped']} members over the period have no Market instrument and are left out (up to {found['worst']} a day in {found['year']})")
    if found['withoutPrices']:
        parts.append(f"{found['withoutPrices']} have no prices")
    return "Index members: " + "; ".join(parts)


def members_at(index, instrument_id, interval):
    return [member for member in interval['members'] if member['instrument'] is not None
            and member['instrument']['instrumentId'] == instrument_id]


def exclude(index, instrument_id, since, until, kind, *, source_reason=None):
    """Keep identity and membership intact; record the unavailable trading interval."""
    for interval in index['intervals']:
        start, end = max(since, interval['since']), min(until, interval['until'])
        if start > end:
            continue
        for member in members_at(index, instrument_id, interval):
            item = {'assetId': member['assetId'], 'instrumentId': instrument_id,
                    'symbol': member['instrument']['displaySymbol'],
                    'since': start, 'until': end, 'kind': kind, 'reason': MISSING_PRICE}
            if source_reason is not None:
                item['sourceReason'] = source_reason
            if item not in index['exclusions']:
                index['exclusions'].append(item)


def check(selection):
    if not isinstance(selection, dict) or set(selection) - {'instrument', 'asOf', 'at'}:
        raise ContractError('invalid_input', 'index holds an instrument and optional asOf.')
    if not isinstance(selection.get('instrument'), dict) or not selection['instrument'].get('instrumentId'):
        raise ContractError('invalid_input', 'Choose the Market instrument of an index.')
    if 'asOf' in selection and (type(selection['asOf']) is not int or selection['asOf'] < 0):
        raise ContractError('invalid_input', 'index.asOf is an availability cutoff in UTC milliseconds.')


async def freeze(ctx, selection, since, until, *, progress=None, timeframe="1d"):
    from .materials import run_task
    check(selection)
    fetched = await run_task(ctx, 'market.fetch', {'symbols': [selection['instrument']['instrumentId']],
        'data': [{'kind': 'members'}], 'start': timestamp(since), 'end': timestamp(until)}, progress=progress)
    if fetched['failures']:
        problem = fetched['failures'][0]['problem']
        raise ContractError(problem['code'], problem['message'], problem)
    # Fresh listing catalogs cannot map old codes until price history establishes listedFrom.
    prices = await run_task(ctx, 'market.fetch', {'index': {'symbol': selection['instrument']['instrumentId'], **({'at': selection['at']} if selection.get('at') else {})},
        'data': [{'kind': 'bars', 'timeframe': timeframe}], 'start': timestamp(since),
        'end': timestamp(until)}, progress=progress)
    identity_failure = next((item for item in prices['failures'] if item['kind'] == 'identity'), None)
    if identity_failure is not None:
        problem = identity_failure['problem']
        raise ContractError(problem['code'], problem['message'], problem)
    # Individual price failures remain Market's missing members/prices; the Quant freeze records their exclusions.
    found = await read_pages(ctx, {'data': 'members', 'index': {'symbol': selection['instrument']['instrumentId'], **({'at': selection['at']} if selection.get('at') else {})},
        'start': timestamp(since), 'end': timestamp(until),
        'asOf': timestamp(selection.get('asOf', until)), 'fill': 'none'})
    problem = source_problem(found['coverage'])
    if problem is not None:
        raise ContractError(problem['code'], problem['message'], problem)
    rows = found['rows']
    identities = await instruments_by_id(ctx, [row['instrumentId'] for row in rows if row['instrumentId'] is not None])
    return await asyncio.to_thread(freeze_rows, selection, since, until, rows, identities, unmapped=found['unmapped'], note=found['note'])


def freeze_rows(selection, since, until, rows, identities, *, unmapped, note=None):
    """Project half-open ns membership rows into Quant's inclusive ms intervals."""
    members, boundaries = [], {since, until + 1}
    for row in rows:
        start = max(since, milliseconds(row['start']))
        end = min(until, milliseconds(row['end'], end=True)) if row['end'] is not None else until
        if start > end:
            continue
        instrument = identities.get(row['instrumentId']) if row['instrumentId'] is not None else None
        asset = instrument['instrumentId'] if instrument is not None else 'unmapped:' + row['symbol'] + ':' + row['start']
        members.append({'assetId': asset, 'code': row['symbol'], 'instrument': instrument,
                        'effectiveTime': start, 'effectiveUntil': end, 'knownAt': milliseconds(row['knownAt'])})
        boundaries.update((start, end + 1))
    points = sorted(boundaries)
    intervals, instruments, exclusions = [], {}, []
    for start, end in zip(points[:-1], points[1:], strict=True):
        current = [member for member in members if member['effectiveTime'] <= start <= member['effectiveUntil']]
        for member in current:
            instrument = member['instrument']
            if instrument is None:
                exclusions.append({'assetId': member['assetId'], 'instrumentId': None, 'symbol': member['code'],
                                   'since': start, 'until': end - 1, 'kind': 'unmapped', 'reason': MISSING_PRICE})
            else:
                instruments[instrument['instrumentId']] = instrument
        intervals.append({'since': start, 'until': end - 1, 'members': current})
    if not instruments:
        raise ContractError('no_data', 'The index has no mapped members in the selected range.', {'unmapped': unmapped})
    return {'selection': dict(selection), 'since': since, 'until': until, 'intervals': intervals,
            'instruments': list(instruments.values()), 'exclusions': exclusions, 'unmappedCount': unmapped,
            'limitation': note}  # the gaps in numbers (limitation()), no caution sentence: the free source's limits are told once, outside the app (design.md 11-13)


def active(index, instrument_id, timestamp):
    if any(item['instrumentId'] == instrument_id and item['since'] <= timestamp <= item['until'] for item in index['exclusions']):
        return False
    return is_member(index, instrument_id, timestamp)


def is_member(index, instrument_id, timestamp):
    return any(interval['since'] <= timestamp <= interval['until'] and
        members_at(index, instrument_id, interval) for interval in index['intervals'])


def check_prices(index, selection, raw, now, calendar, *, since=None, until=None):
    """Exclude a member over the sessions it has no price for; `calendar`: the (open, close) pairs of the whole window."""
    instrument = selection['instrument']
    actual = set(raw['time'])
    for interval in index['intervals']:
        if not members_at(index, instrument['instrumentId'], interval):
            continue
        start = max(interval['since'], index['since'] if since is None else since)
        end = min(interval['until'], index['until'] if until is None else until)
        if start > end:
            continue
        missing_since = None
        for opened, closed in (pair for pair in calendar if start <= pair[0] <= end):
            if closed > now:
                break
            if opened not in actual:
                if missing_since is None:
                    missing_since = opened
            elif missing_since is not None:
                # Remains unavailable until the recovered session's price is known.
                exclude(index, instrument['instrumentId'], missing_since, closed - 1, 'missing_price')
                missing_since = None
        if missing_since is not None:
            exclude(index, instrument['instrumentId'], missing_since, end, 'missing_price')
