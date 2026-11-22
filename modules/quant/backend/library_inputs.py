"""The library items a strategy reads: the values of the newest version of each, as a file the backtest turns into library data."""
from __future__ import annotations

import asyncio
from collections import defaultdict
from pathlib import Path

import pandas as pd
from talyx_sdk import ContractError
from talyx_sdk.i18n import t

from .object_data import PERIOD_MS

NS = 1_000_000


def saved_versions(settings):
    pins = settings.get("inputs") or {}
    if isinstance(pins, dict):
        pins = [pin for group in ("library", "models") for pin in pins.get(group, {}).values()]
    return {pin["name"]: pin["versionId"] for pin in pins if pin.get("name") and pin.get("versionId")}


def named_items(definitions, values):
    """(parameter, library item name) for each library parameter the strategy declares and the panel has filled in."""
    return [(item["name"], values[item["name"]]) for item in definitions if item["type"] == "library" and values[item["name"]]]


async def prepare(library, items, sources, since, until, timeframe, path, *, versions=None, available_until=None, overrides=None):
    """Write the values of each named item that concern the run's symbols and fall inside its range; returns what the run records about them.
    A value reaches the strategy at the moment it became available, which can be later than the bar it is about; one made for a missed
    period was available only when it was made."""
    if not items:
        return {"path": None, "items": []}
    by_instrument = {source["marketInstrument"]["instrumentId"]: source["nativeInstrument"] for source in sources}
    # A notebook names instruments the way its own tables do: by the symbol the sources show (AAPL, 600642.XSHG) as well as by id; data put in from a
    # notebook keeps those names, so a library item is matched the same way.
    candidates = defaultdict(set)
    for source in sources:
        for symbol in (source["marketInstrument"].get("displaySymbol"), source["marketInstrument"].get("symbol")):
            if symbol:
                candidates[symbol].add(source["nativeInstrument"])
    names = {symbol: next(iter(values)) for symbol, values in candidates.items() if len(values) == 1}
    known = {**names, **by_instrument, **{native: native for native in by_instrument.values()}}
    period = int(timeframe[:-1]) * PERIOD_MS[timeframe[-1]]
    frames, summary = [], []
    prices = Path(path).with_name("prices.parquet")
    for parameter, name in items:
        if overrides and parameter in overrides and isinstance(overrides[parameter], dict):
            # Values taken from a model run that was never published: no library version, so nothing to pin.
            replaced = await asyncio.to_thread(replacement, overrides[parameter]["path"], {**names, **by_instrument}, "prediction", name, prices, 0)
            frames.append(replaced["frame"])
            summary.append({"parameter": parameter, "name": name, "version": 0, "versionId": "", "state": "run", "fields": ["prediction"], "rows": len(replaced["frame"]),
                            "instruments": replaced["frame"]["instrument"].nunique(), "firstAvailableAt": replaced["first"], "lastAvailableAt": replaced["last"], "fromModelRun": overrides[parameter]["from"]})
            continue
        item = library.resolve_values(name) if versions is None else library.read_version(versions[parameter])
        if overrides and parameter in overrides:
            replaced = await asyncio.to_thread(replacement, overrides[parameter], {**names, **by_instrument}, item["fields"][0] if item["fields"] else "value", name, prices, item["version"])
            frames.append(replaced["frame"])
            summary.append({"parameter": parameter, "name": name, "version": item["version"], "versionId": item["versionId"], "state": item["state"], "fields": [replaced["field"]],
                            "rows": len(replaced["frame"]), "instruments": replaced["frame"]["instrument"].nunique(), "firstAvailableAt": replaced["first"], "lastAvailableAt": replaced["last"],
                            "from": "a notebook"})
            continue
        if not item["fields"]:
            raise ContractError("invalid_input", t("{name} holds no values (a trained model without predictions); a strategy reads the values of a factor, a model's predictions, an allocation or a data item.", name=name),
                                {"parameter": parameter})
        if item.get("archivedAt") is not None:
            newer = next((other for other in library.store.item_versions(item["itemId"]) if other["archivedAt"] is None and other["state"] in ("updating", "failed", "fixed")), None)
            raise ContractError("not_found", (t("{name} v{version} was archived; its values are gone. Switch to v{next_version}.",
                        name=name, version=item['version'], next_version=newer['version'])
                    if newer else t("{name} v{version} was archived; its values are gone.", name=name, version=item['version'])),
                                {"parameter": parameter, **({"switchTo": newer["versionId"]} if newer else {})})
        async with library.reading:
            rows = await asyncio.to_thread(library.store.read_values, item["versionId"])
        projected, detail = await asyncio.to_thread(project_values, rows, known, sources, item,
            parameter, name, since, until, period, available_until)
        frames.append(projected)
        summary.append(detail)
    def write():
        frame = pd.concat(frames, ignore_index=True).sort_values(["ts_init", "instrument", "field"], kind="stable")
        frame.to_parquet(path, index=False)
    await asyncio.to_thread(write)
    return {"path": str(path), "items": summary}


def project_values(rows, known, sources, item, parameter, name, since, until, period, available_until):
    table = pd.DataFrame(rows, columns=["datetime", "instrument", "field", "value", "availableAt", "recordedAt", "mode"])
    table = table.assign(native=table["instrument"].map(known))
    held, stored = table[table["native"].notna()], set(table["instrument"])
    table = held[(held["availableAt"] >= since) & (held["availableAt"] <= (until + period if available_until is None else available_until))]
    if table.empty:
        shown = {source["nativeInstrument"]: source["marketInstrument"].get("displaySymbol") or source["marketInstrument"].get("symbol") or source["nativeInstrument"] for source in sources}
        message = (f"{name} v{item['version']} has values for {', '.join(sorted({shown[native] for native in held['native']}))} from {shown_day(held['availableAt'].min())} "
                   f"to {shown_day(held['availableAt'].max())}, none between {shown_day(since)} and {shown_day(until)}." if not held.empty else
                   f"{name} v{item['version']} has no values for {', '.join(sorted(shown.values()))}; its {item_names(stored)}.")
        raise ContractError("unavailable", message, {"parameter": parameter, "name": name, "version": item["version"], "since": since, "until": until})
    projected = pd.DataFrame({
        "name": name, "instrument": table["native"], "field": table["field"], "value": table["value"].astype(float), "version": item["version"],
        "ts_event": (table["datetime"].clip(upper=table["availableAt"]) * NS).astype("int64"), "ts_init": (table["availableAt"] * NS).astype("int64")})
    detail = {"parameter": parameter, "name": name, "version": item["version"], "versionId": item["versionId"], "state": item["state"],
                    "fields": sorted(set(table["field"])), "rows": len(table), "instruments": table["instrument"].nunique(),
                    # how many names it has a value for on a typical day (an index-wide strategy may trade far more names than it scores)
                    "perDay": int(round(float(table.groupby(table["datetime"] // 86_400_000)["instrument"].nunique().median()))),
                    "firstAvailableAt": int(table["availableAt"].min()), "lastAvailableAt": int(table["availableAt"].max())}
    return projected, detail


def shown_day(milliseconds):
    moment = pd.Timestamp(int(milliseconds), unit="ms", tz="UTC")
    return f"{moment:%b} {moment.day}, {moment.year}"


def item_names(stored):
    """What a library item holds, said when none of it is a run's: the names as stored (a notebook's symbols), or only how many when they are Market ids."""
    held = sorted(stored)
    if any(name.startswith(("vi1_", "vi2_")) for name in held):
        return f"{len(held)} names are other instruments"
    return ("values are for " + ", ".join(held[:5]) + (f" and {len(held) - 5} more" if len(held) > 5 else ""))


def milliseconds(stamps):
    moment = pd.to_datetime(stamps, unit="ms", utc=True) if pd.api.types.is_numeric_dtype(stamps) else pd.to_datetime(stamps, utc=True)
    return moment.dt.as_unit("ms").astype("int64")


def replacement(path, by_instrument, field, name, prices, version=0):
    """The values a notebook handed over in place of a library item: `datetime`, `instrument`, `value` rows; each is available at its supplied publication time or when the frozen bar is known. The instrument
    is the market instrument id, the native id or the symbol the sources carry."""
    table = pd.read_parquet(path)
    known = {**by_instrument, **{native: native for native in by_instrument.values()}}
    table = table.assign(native=table["instrument"].map(known))
    table = table[table["native"].notna()]
    if table.empty:
        raise ContractError("invalid_input", t("None of the instruments in the data given to q.backtest are among this strategy's symbols."))
    moved = milliseconds(table["datetime"])
    available = pd.Series(pd.NA, index=table.index, dtype="Int64")
    if "availableAt" in table:
        given = table["availableAt"].notna()
        available.loc[given] = milliseconds(table.loc[given, "availableAt"])
    missing = available.isna()
    if missing.any():
        clocks = pd.read_parquet(prices, columns=["instrument", "label", "knownAt"]).set_index(["instrument", "label"])
        keys = pd.MultiIndex.from_arrays([table.loc[missing, "native"], moved.loc[missing]])
        available.loc[missing] = clocks["knownAt"].reindex(keys).to_numpy()
        if available.isna().any():
            raise ContractError("invalid_input", t("A signal without availableAt must name a bar in the frozen price history."))
    available = available.astype("int64")
    frame = pd.DataFrame({"name": name, "instrument": table["native"].to_numpy(), "field": field, "value": table["value"].astype(float).to_numpy(), "version": version,
                          "ts_event": (moved.clip(upper=available) * NS).to_numpy(), "ts_init": (available * NS).to_numpy()})
    return {"frame": frame, "field": field, "first": int(available.min()), "last": int(available.max())}
