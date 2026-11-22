"""Freeze Market 2.0 research prices; mutable prices remain owned by Market."""
from __future__ import annotations

import asyncio
import time
from collections import Counter
from pathlib import Path

import numpy as np
import pandas as pd
import pyarrow as pa
import pyarrow.parquet as pq
from talyx_sdk import ContractError
from talyx_sdk.i18n import t

from . import market_inputs as M
from . import research_calendar
from .materials import ExportFiles, Materials, read_selection, release
from .object_data import day_ms, frozen_data, none_left, preflight, required_settings


async def resolve_series(ctx, instrument, timeframe, adjusted):
    instrument = await M.current(ctx, instrument)
    return instrument, M.price_series(instrument, timeframe, adjusted)


def column_names(instruments):
    """Readable labels; distinct identities never share a research column."""
    counts = Counter(item["displaySymbol"] for item in instruments)
    base = [item["displaySymbol"] if counts[item["displaySymbol"]] == 1
            else f"{item['displaySymbol']}@{item['venueId']}" for item in instruments]
    repeated = {name: sorted(item["instrumentId"] for item, label in zip(instruments, base, strict=True) if label == name)
                for name, count in Counter(base).items() if count > 1}
    return [label if label not in repeated else f"{label} [{repeated[label].index(item['instrumentId']) + 1}]"
            for item, label in zip(instruments, base, strict=True)]


def member_mask(index, instrument_id, times):
    from .index_universe import members_at
    mask = np.zeros(len(times), dtype=bool)
    for interval in index["intervals"]:
        if members_at(index, instrument_id, interval):
            mask |= (times >= interval["since"]) & (times <= interval["until"])
    return mask


def labels_of(times, timeframe, zone):
    if timeframe.endswith("d"):
        dates = pd.to_datetime(times, unit="ms", utc=True).dt.tz_convert(zone).dt.normalize()
        return dates.dt.tz_localize(None).dt.tz_localize("UTC").dt.as_unit("ms").astype("int64")
    return times.astype("int64")


def write_bars(selections, raw_by_id, columns, path, timeframe, zone, index, files=None):
    """One instrument per row group, off the event loop; `files` opens each export file once for all the names. `zone` is the
    calendar's time zone, or a map of instrument id to its own calendar's zone (a pool on several calendars)."""
    instruments, rows = [], []
    writer = None
    try:
        for selection, column in zip(selections, columns, strict=True):
            instrument = selection["instrument"]
            frame = read_selection(selection, files)
            original = read_selection(raw_by_id[instrument["instrumentId"]], files)
            if not np.array_equal(frame["time"].to_numpy(), original["time"].to_numpy()):
                raise ContractError("conflict", t("{displaySymbol}: raw and adjusted price rows differ.", displaySymbol=instrument['displaySymbol']))
            result = frame[["time", "closeAt", "knownAt", *M.FIELDS]].copy()
            result["column"] = column
            result["label"] = labels_of(result["time"], timeframe, zone[instrument["instrumentId"]] if isinstance(zone, dict) else zone)
            result["rawClose"], result["rawVolume"] = original["close"].to_numpy(), original["volume"].to_numpy()
            if index is not None:
                result["isMember"] = member_mask(index, instrument["instrumentId"], result["time"].to_numpy())
            table = pa.Table.from_pandas(result, preserve_index=False)
            if writer is None:
                writer = pq.ParquetWriter(path, table.schema)
            writer.write_table(table)
            instruments.append({
                "column": column, "instrumentId": instrument["instrumentId"], "displaySymbol": instrument["displaySymbol"],
                "venue": instrument["venueId"], "marketType": instrument["marketType"], "instrument": instrument,
                "series": selection["series"], "revision": selection["version"], "source": selection["source"],
                "version": selection["version"], "range": selection["coverage"]["exportedRange"],
                "rows": len(frame), "missingBars": selection["coverage"]["missingBars"]})
            rows.append({"instrumentId": instrument["instrumentId"], "ticker": column, "sector": None,
                         "result": "fetched", "reason": "", "bars": len(frame), "from": None, "new": len(frame)})
    finally:
        if writer is not None:
            writer.close()
    return instruments, rows


async def unchanged(ctx, info):
    """Whether Market still has every series of a freeze at the revision it was frozen at (one batch of revision reads)."""
    selections = {}
    for item in info["instruments"]:
        for series in (item["series"], M.price_series(item["instrument"], info["timeframe"], False)):
            selections[series["seriesId"]] = {"instrument": item["instrument"], "series": series}
    if set(info["revisions"]) - set(selections):
        return False
    found = await M.revisions(ctx, [selections[key] for key in info["revisions"]], info["until"])
    return all(found[key]["revision"] == version for key, version in info["revisions"].items())


def shared_calendar(schedules, timeframe, calendars="one"):
    """The run's calendar and the bars every calendar has ({(calendar, scope): (zone, [(open, close)])}, day labels for daily bars): the
    calendar whose bars are the shared ones, or come closest (the fewest), with one calendar that one. The observations a year are the
    shared bars' (stocks and crypto on common days: the stock calendar's, about 252)."""
    if calendars == "many":
        timeframe = "1d"  # allocations compare local daily closes, including when reading hourly prices
    days = {key: set(labels_of(pd.Series([opened for opened, _ in found], dtype="int64"), timeframe, zone).tolist()) for key, (zone, found) in schedules.items()}
    return min(sorted(schedules), key=lambda key: len(days[key])), sorted(set.intersection(*days.values()))


async def prepare(ctx, data, owner, directory, *, progress=None, calendars="one"):
    """Freeze the data's prices for a run. `calendars="one"` (factors, models, strategies, which compare names bar by bar) refuses names on
    different calendars; `"many"` (an allocation, whose returns are taken on the days every asset has a close, or weekly or monthly) freezes
    each name on its own calendar, and the observations a year are those of the days the calendars share."""
    if data.get("universe") == "code":
        return {"research": None, "references": [], "summary": {"universe": "code"}}
    required_settings(data)
    data, index = await frozen_data(ctx, data, progress=progress)
    symbols, timeframe = data["symbols"], data["timeframe"]
    adjusted = data.get("adjustment", "split_dividend")  # unset is adjusted for splits and dividends, as the data settings show it
    since, until = day_ms(data["since"], False), day_ms(data["until"], True)
    selected, skipped = [], []
    for position, symbol in enumerate(symbols):
        if progress is not None:
            progress({"done": position, "total": len(symbols), "waitingFor": None, "waitingUntil": None, "message": t("checking names")})
        try:
            instrument, series = await resolve_series(ctx, symbol["instrument"], timeframe, adjusted)
            await preflight(ctx, instrument, series)
        except ContractError as error:
            # A name Market cannot price is left out and named in the run's data notes, for a list as for an index (as Alphalens, JoinQuant
            # and RiceQuant run a universe whose members lack data); the run fails only when no name is left.
            from .index_universe import exclude
            if index is not None:
                exclude(index, symbol["instrument"]["instrumentId"], since, until, "no_price_series", source_reason=error.message)
            skipped.append({"instrumentId": symbol["instrument"]["instrumentId"], "ticker": symbol["instrument"]["displaySymbol"],
                            "sector": None, "result": "failed", "reason": error.message, "bars": 0, "from": None, "new": 0})
            continue
        selected.append({"instrument": instrument, "series": series})
    if not selected:
        raise none_left(skipped, index)
    windows = await research_calendar.date_windows(ctx, [item["instrument"] for item in selected], timeframe, since, until)
    spans = {(item["instrument"]["calendarId"], item["series"]["sessionScope"]): windows[item["instrument"]["instrumentId"]] for item in selected}
    schedules = {(name, scope): await research_calendar.boundaries(ctx, name, timeframe, scope, *window) for (name, scope), window in sorted(spans.items())}
    if calendars == "one" and len({(zone, tuple(found)) for zone, found in schedules.values()}) != 1:
        # Name which names keep which calendar (one line, no names; and the details had tuple keys, which no answer could carry).
        by_calendar = {}
        for item in selected:
            key = f"{item['instrument']['calendarId']} ({item['series']['sessionScope']})"
            by_calendar.setdefault(key, []).append(item["instrument"]["displaySymbol"])
        named = "; ".join(f"{key}: {', '.join(names[:5])}{f' and {len(names) - 5} more' if len(names) > 5 else ''}" for key, names in sorted(by_calendar.items()))
        raise ContractError("invalid_input", t("These names trade on different calendars ({named}); research runs on one calendar, so keep names of one calendar together here.", named=named), {"calendars": by_calendar})
    calendar_of = {item["instrument"]["instrumentId"]: (item["instrument"]["calendarId"], item["series"]["sessionScope"]) for item in selected}
    zone_of = {identity: schedules[key][0] for identity, key in calendar_of.items()}
    (calendar_id, scope), shared = shared_calendar(schedules, timeframe, calendars)
    zone, found = schedules[calendar_id, scope]
    originals = {item["instrument"]["instrumentId"]: {"instrument": item["instrument"],
                 "series": M.price_series(item["instrument"], timeframe, False)} for item in selected}
    wanted = {item["series"]["seriesId"]: item for item in [*selected, *originals.values()]}
    captured = await Materials(ctx).capture({"market": {
        "selections": list(wanted.values()), "since": since, "until": until, "windows": windows,
        "allowEmpty": True, "progress": progress}}, owner)
    files = ExportFiles()
    try:
        by_series = {item["series"]["seriesId"]: item for item in captured["manifest"]["selections"]}
        raw_by_id = {identity: by_series[item["series"]["seriesId"]] for identity, item in originals.items()}
        now, usable = int(time.time() * 1000), []
        expected = {key: np.asarray([opened for opened, closed in found if closed <= now], dtype=np.int64) for key, (_, found) in schedules.items()}
        for position, choice in enumerate(selected):
            if progress is not None:   # the minute after the export was a count standing at its end; this step has its own
                progress({"done": position, "total": len(selected), "waitingFor": None, "waitingUntil": None, "message": t("checking prices")})
            item = by_series[choice["series"]["seriesId"]]
            instrument = item["instrument"]
            if not item["coverage"]["exportedRows"] or not raw_by_id[instrument["instrumentId"]]["coverage"]["exportedRows"]:
                from .index_universe import exclude
                reason = item["problem"]["message"] if "problem" in item else "Market has no prices in this period."
                if index is not None:
                    exclude(index, instrument["instrumentId"], since, until, "no_prices", source_reason=reason)
                skipped.append({"instrumentId": instrument["instrumentId"], "ticker": instrument["displaySymbol"], "sector": None,
                                "result": "none", "reason": reason, "bars": 0, "from": None, "new": 0})
                continue
            frame = await asyncio.to_thread(read_selection, item, files)
            # Sessions a name has no bar for between its first and last (halted) leave it out of those days' cross-sections, as Alphalens and
            # JoinQuant do; before its first bar it was not trading yet, after its last it has ended (delisted, or the source has none after) and
            # the run holds it out at its last close. None of these stops the run.
            sessions = expected[calendar_of[instrument["instrumentId"]]]
            sessions = sessions[(sessions >= frame["time"].min()) & (sessions <= frame["time"].max())]
            item["coverage"]["missingBars"] = int(np.count_nonzero(~np.isin(sessions, frame["time"].to_numpy())))
            if index is not None:
                from .index_universe import check_prices
                check_prices(index, item, frame, now, found)
            usable.append(item)
        if not usable:
            raise none_left(skipped, index)
        directory = Path(directory)
        directory.mkdir(parents=True, exist_ok=True)
        path = directory / "bars.parquet"
        columns = column_names([item["instrument"] for item in usable])
        instruments, rows = await asyncio.to_thread(write_bars, usable, raw_by_id, columns, path, timeframe, zone_of if len(schedules) > 1 else zone, index, files)
        info = {"timeframe": timeframe, "annualization": research_calendar.annualization(shared, since, until),
                "calendarId": calendar_id, "sessionScope": scope, "timezone": zone, "since": since, "until": until,
                "adjusted": adjusted, "instruments": instruments,
                "revisions": {item["series"]["seriesId"]: item["version"] for item in captured["manifest"]["selections"]}}
        if calendars == "many" and not timeframe.endswith("d"):
            info["sessionZones"] = {item["column"]: zone_of[item["instrumentId"]] for item in instruments}
        if index is not None:
            info["index"] = index
        return {"research": {"barsPath": str(path), "info": info}, "references": [],
                "summary": {**info, "requested": len(symbols), "rows": sum(item["rows"] for item in instruments),
                            "fetchRows": [*rows, *skipped]}}
    finally:
        files.close()
        await release(ctx, captured["references"], owner)
