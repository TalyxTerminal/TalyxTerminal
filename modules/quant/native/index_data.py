"""Frozen index identity and price availability, shared by authored strategies."""

_tables = {}  # id(index) -> (index, exclusions by instrument, member intervals by instrument): built once per frozen index


def _table(index):
    found = _tables.get(id(index))
    if found is None or found[0] is not index:
        exclusions, members = {}, {}
        for row in index['exclusions']:
            exclusions.setdefault(row['instrumentId'], []).append((row['since'], row['until']))
        for interval in index['intervals']:
            for member in interval['members']:
                if member['instrument'] is not None:
                    members.setdefault(member['instrument']['instrumentId'], []).append((interval['since'], interval['until']))
        found = _tables[id(index)] = (index, exclusions, members)
    return found


def can_trade(index, native_id, at):
    _, exclusions, _ = _table(index)
    return not any(since <= at <= until for since, until in exclusions.get(index['nativeInstruments'][native_id], ()))


def eligible(index, native_id, at):
    if not can_trade(index, native_id, at):
        return False
    _, _, members = _table(index)
    return any(since <= at <= until for since, until in members.get(index['nativeInstruments'][native_id], ()))


def eligible_times(index, native_id, times):
    """The same inclusive intervals as eligible(), for a vector of bar closes in milliseconds."""
    import numpy as np

    _, exclusions, members = _table(index)
    instrument = index["nativeInstruments"][native_id]

    def covered(spans):
        if not spans:
            return np.zeros(len(times), dtype=bool)
        starts, ends = np.array(sorted(spans), dtype=np.int64).T
        positions = np.searchsorted(starts, times, side="right") - 1
        return (positions >= 0) & (times <= np.maximum.accumulate(ends)[positions])

    return covered(members.get(instrument)) & ~covered(exclusions.get(instrument))
