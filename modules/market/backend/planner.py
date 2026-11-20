"""Which request answers which part of a missing span: the source's files first, then its API.

A source declares per period the files it keeps (``archives``: unit month or day, from, until, publishDelay,
dayZone, priceBases) beside its API's depth and rows per request (``requestRows``; ``basisRows`` where a price basis
pages differently). Market walks the layers the same way for every source: month files, then day files, then the
API. A file is used where the API cannot reach or where the part of the span it holds needs more than one API page.
A file the source says is missing, or that cannot be had (its host unreachable or refusing, the file unreadable),
falls to the next layer (a month to its days, a day to the API), and so do days a month file left without rows when
the source also keeps day files. What no layer can give yet is ``unavailable`` until the file is due, or a while
later when it is late. The API's part is cut into as many continuous pieces as the source runs at once, each paged
by the source's ``next``. One unit is one file or one API piece; every unit's answer is committed as it arrives
(``Data.fill``).
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime

from .common import DAY_NS, NS
from .routing import depth_start, local_date, zone_offset

RETRY_LATE = 6 * 3600 * NS      # a file that should be out but is not
RETRY_REFUSED = 3600 * NS       # the source refused (HTTP 451, 403): the user may change the network
END = 2**62


@dataclass(frozen=True)
class Unit:
    start: int
    end: int
    archive: tuple[str, int] | None = None   # ("month" | "day", the file's start); None asks the API
    retry_at: int | None = None              # no layer can give it yet: unavailable until then


class Layers:
    """The request layers of one series (a declared period and a price basis) at one moment."""

    def __init__(self, period: dict, price_basis: str, now: int, pieces: int, count, step: int | None):
        """``count(a, b)``: bars in [a, b) on the instrument's calendar; ``step``: the bar length when the calendar
        runs around the clock (API pieces are cut on page boundaries), else None (one piece)."""
        self.archives = sorted((item for item in period.get("archives", []) if price_basis in item["priceBases"]),
                               key=lambda item: item["unit"] != "month")
        self.rows = period.get("basisRows", {}).get(price_basis, period.get("requestRows"))
        self.api_from = depth_start(period["depth"], now)
        self.count, self.step, self.now, self.pieces = count, step, now, max(1, pieces)

    def reach(self) -> list[tuple[int, int]]:
        """Spans some layer can give, sorted and merged (the API from its depth, each archive over [from, until))."""
        spans = sorted([(self.api_from, END)] + [
            (local_date(item["from"], item["dayZone"]),
             local_date(item["until"], item["dayZone"]) if item.get("until") else END) for item in self.archives])
        merged: list[tuple[int, int]] = []
        for a, b in spans:
            if merged and a <= merged[-1][1]:
                merged[-1] = (merged[-1][0], max(merged[-1][1], b))
            else:
                merged.append((a, b))
        return merged

    def units(self, gaps: list[tuple[int, int]]) -> list[Unit]:
        return [unit for a, b in gaps for unit in self._cover(a, b, 0, None)]

    def below(self, unit: Unit) -> list[Unit]:
        """What answers a unit the source says is missing or a file that cannot be had: for a file, the next layer
        down; for the API (it no longer serves this contract: a delisted one), every published file holding it."""
        if unit.archive is None:
            return self._cover(unit.start, unit.end, 0, None, api=False)
        return self._cover(unit.start, unit.end, self._level(unit.archive[0]) + 1, None)

    def unanswered(self, unit: Unit, times, none: list[tuple[int, int]]) -> list[Unit]:
        """Days of a month file holding neither rows nor a span the source says is empty, asked from the day files
        (and below) when the source keeps day files."""
        if unit.archive[0] != "month" or not any(item["unit"] == "day" for item in self.archives):
            return []
        level = self._level("day")
        offset = zone_offset(self.archives[level]["dayZone"])
        held = {int(t + offset) // DAY_NS for t in times}
        out, day = [], unit.start
        while day < unit.end:
            stop = min(unit.end, _bounds("day", day, offset)[1])
            if int(day + offset) // DAY_NS not in held and not any(a < stop and day < b for a, b in none):
                if out and out[-1][1] == day:
                    out[-1] = (out[-1][0], stop)
                else:
                    out.append((day, stop))
            day = stop
        return [item for a, b in out for item in self._cover(a, b, level, None)]

    def requests(self, units: list[Unit]) -> int:
        return sum(1 if unit.archive else self._pages(unit.start, unit.end) for unit in units
                   if unit.retry_at is None)

    def _level(self, name: str) -> int:
        return next(index for index, item in enumerate(self.archives) if item["unit"] == name)

    def _pages(self, a: int, b: int) -> int:
        if not self.rows:
            return 1
        return max(1, -(-self.count(a, b) // self.rows))

    def _cover(self, a: int, b: int, level: int, due: int | None, api: bool = True) -> list[Unit]:
        """Units for [a, b) from archive ``level`` down; ``due`` is when a file above that holds it comes out;
        without ``api`` only files answer."""
        if level == len(self.archives):
            return self._api(a, b, due) if api else [Unit(a, b, retry_at=due if due is not None
                                                             else self.now + RETRY_LATE)]
        archive = self.archives[level]
        offset = zone_offset(archive["dayZone"])
        lo = local_date(archive["from"], archive["dayZone"])
        hi = local_date(archive["until"], archive["dayZone"]) if archive.get("until") else END
        delay = self.now - depth_start(archive["publishDelay"], self.now)
        out: list[Unit] = []
        rest: list[list] = []  # [start, end, earliest due] of the parts this layer leaves
        moment = a
        while moment < b:
            first, last = _bounds(archive["unit"], moment, offset)
            start, end = max(a, first), min(b, last)
            kept = lo <= first and last <= hi
            if kept and last + delay <= self.now and (not api or start < self.api_from or self._pages(start, end) > 1):
                out.append(Unit(start, end, (archive["unit"], first)))
            else:
                coming = last + delay if kept and last + delay > self.now else None
                if rest and rest[-1][1] == start:
                    rest[-1][1] = end
                    rest[-1][2] = _earliest(rest[-1][2], coming)
                else:
                    rest.append([start, end, coming])
            moment = last
        for start, end, coming in rest:
            out += self._cover(start, end, level + 1, _earliest(due, coming), api)
        return out

    def _api(self, a: int, b: int, due: int | None) -> list[Unit]:
        out = []
        if a < self.api_from:
            stop = min(b, self.api_from)
            out.append(Unit(a, stop, retry_at=due if due is not None else self.now + RETRY_LATE))
            a = stop
        if a >= b:
            return out
        if not self.rows or not self.step:
            return out + [Unit(a, b)]
        page = self.rows * self.step
        pages = self._pages(a, b)
        size = -(-pages // min(pages, self.pieces)) * page
        pieces, end = [], b
        while end > a:  # from the newest end, so the newest page is whole
            pieces.append(Unit(max(a, end - size), end))
            end -= size
        return out + pieces[::-1]


def _earliest(a: int | None, b: int | None) -> int | None:
    return b if a is None else a if b is None else min(a, b)


def _bounds(unit: str, moment: int, offset: int) -> tuple[int, int]:
    """[start, end) of the day or month holding ``moment`` in a zone ``offset`` east of UTC."""
    if unit == "day":
        start = (moment + offset) // DAY_NS * DAY_NS - offset
        return start, start + DAY_NS
    local = datetime.fromtimestamp((moment + offset) // NS, UTC)
    first = datetime(local.year, local.month, 1, tzinfo=UTC)
    following = datetime(local.year + local.month // 12, local.month % 12 + 1, 1, tzinfo=UTC)
    return int(first.timestamp()) * NS - offset, int(following.timestamp()) * NS - offset
