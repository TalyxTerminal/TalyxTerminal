"""Exact Market reference candles accompany executable LAST candles."""
from __future__ import annotations

import asyncio

from talyx_sdk import ContractError
from talyx_sdk.i18n import t

from . import market_inputs as M
from . import object_data
from .materials import Materials, read_selection, release

FIELDS = ("open", "high", "low", "close")


async def configure(ctx, sources, bases, *, environment="production", since=None, realtime=False):
    for source in sources:
        instrument = source["marketInstrument"]
        series = {}
        for basis in bases:
            selected = M.price_series(instrument, source["series"]["timeframe"], False, price_basis=basis)
            await object_data.preflight(ctx, instrument, selected, since=since, environment=environment, realtime=realtime)
            series[basis] = selected
        if series:
            source["referenceSeries"] = series


def project(sources, frame, selections):
    result = frame.copy()
    by_id = {source["marketInstrument"]["instrumentId"]: source for source in sources}
    for selection in selections:
        source = by_id[selection["instrument"]["instrumentId"]]
        basis = selection["series"]["priceBasis"]
        raw = read_selection(selection).set_index("closeNs")
        chosen = result["instrument"] == source["nativeInstrument"]
        needed = result.loc[chosen, "ts_event"].astype("int64")
        if not needed.isin(raw.index).all():
            raise ContractError("history_gap", t("{displaySymbol}: missing {upper} reference candles.", displaySymbol=source['marketInstrument']['displaySymbol'], upper=basis.upper()))
        values = M.decimal_columns(raw.loc[needed].reset_index(drop=True), fields=FIELDS)
        for field in FIELDS:
            result.loc[chosen, basis + "_" + field] = values[field].to_numpy()
    return result


async def attach(ctx, sources, frame, since, until, owner, *, environment="production"):
    selections = [{"instrument": source["marketInstrument"], "series": series}
                  for source in sources for series in source.get("referenceSeries", {}).values()]
    if not selections or frame.empty:
        return frame
    captured = await Materials(ctx).capture({"market": {"selections": selections, "since": since, "until": until,
                                                       "environment": environment}}, owner)
    try:
        return await asyncio.to_thread(project, sources, frame, captured["manifest"]["selections"])
    finally:
        await release(ctx, captured["references"], owner)
