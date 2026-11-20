"""Market membership endpoints are authoritative; missing future rows are never invented."""
from modules.quant.backend import index_universe as U
from modules.quant.backend.public_settings import timestamp

DAY = 86_400_000
MS = 1_000_000
SELECTION = {"instrument": {"instrumentId": "index", "displaySymbol": "^GSPC"}}


def freeze(records, until_day):
    rows = [{"instrumentId": "i:" + name, "symbol":name, "start":timestamp(first*DAY)[:10],
             "end":None if last is None else timestamp(last*DAY)[:10], "knownAt":"1970-01-01"}
            for names, first, last in records for name in names]
    identities = {row["instrumentId"]: {"instrumentId": row["instrumentId"], "displaySymbol": row["symbol"]} for row in rows}
    return U.freeze_rows(SELECTION, 0, (until_day + 1) * DAY - 1, rows, identities, unmapped=0)


def members_on(index, day):
    return {name for name in "ABCDE" if U.is_member(index, f"i:{name}", day * DAY + 3_600_000)}


def test_closed_membership_does_not_extend_past_the_recorded_end():
    index = freeze([("AB", 0, 2), ("ABC", 3, 4)], 8)
    assert [members_on(index, day) for day in range(9)] == [{"A", "B"}] * 3 + [{"A", "B", "C"}] * 2 + [set()] * 4
    assert "assumedSince" not in index


def test_missing_middle_day_is_not_filled_from_another_interval():
    index = freeze([("AB", 0, 1), ("C", 3, 4)], 6)
    assert members_on(index, 2) == set() and members_on(index, 3) == {"C"} and members_on(index, 6) == set()


def test_new_open_ended_members_replace_old_members_at_the_exact_boundary():
    index = freeze([("AB", 0, 4), ("ABD", 5, None)], 8)
    assert members_on(index, 4) == {"A", "B"} and members_on(index, 5) == {"A", "B", "D"}
    assert members_on(index, 8) == {"A", "B", "D"}
