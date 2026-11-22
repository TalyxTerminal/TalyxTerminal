"""A frozen index as Quant keeps it, written in tests with each interval's members in place."""
from __future__ import annotations


def frozen_index(index):
    """`index` with intervals listing member dicts, as `index_universe.freeze_rows` keeps it: the members once, each interval naming them by place."""
    members, places = [], {}
    intervals = []
    for interval in index["intervals"]:
        named = []
        for member in interval["members"]:
            if id(member) not in places:
                places[id(member)] = len(members)
                members.append(member)
            named.append(places[id(member)])
        intervals.append({**interval, "members": named})
    return {**index, "members": members, "intervals": intervals}
