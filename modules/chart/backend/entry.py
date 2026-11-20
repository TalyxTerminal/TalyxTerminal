"""Chart's declarations: its public actions (typed by ``models``), its settings, what it implements for Developer,
the panel's private routes and streams, and copying a workspace. The contract is generated from this file."""
from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from pathlib import Path
from typing import Any

from talyx_sdk import ContractError
from talyx_sdk.declare import Module
from talyx_sdk.process import Call, Context

from . import actions, authoring, changes, exports, object_actions
from . import models as m
from . import wire as w
from .app import ChartApp

module = Module("chart")
app: ChartApp | None = None   # the running Chart, set when the process starts
Answer = dict[str, Any]

module.setting("alertPopup", bool, True, title="Show popup", group="New alerts",
               description="Whether a new alert starts with a desktop popup; each alert can change it.")
module.setting("alertSound", bool, True, title="Play sound", group="New alerts",
               description="Whether a new alert starts with a sound; each alert can change it.")

module.implement("developer.document_source", "indicator-docs", actions={"read": authoring.read, "write": authoring.write})
module.implement("developer.development_support", "indicators", actions={"invoke": authoring.invoke})


def _value(model) -> dict:
    """Chart's code reads requests as the JSON it was sent."""
    return model.model_dump(mode="json", by_alias=True, exclude_unset=True)


@module.changes
class ChartChanges:
    @staticmethod
    async def list(call_ids: list[str], run_id: str | None) -> list[dict]:
        return changes.listed(app.store, call_ids, run_id)

    @staticmethod
    async def revert(call_id: str, direction: str) -> None:
        changes.revert(app, call_id, direction)


@module.on_delete_workspace
async def delete_workspace(workspace_id: str, *, preview: bool) -> list[str]:
    return await app.delete_workspace(workspace_id, preview)


@module.stream(cursor=True, trigger=True)
async def alert_fired(params: m.AlertFilter, call: Call, cursor=None) -> AsyncIterator[m.AlertFired]:
    """One fact per actual alert firing in this workspace. Filter by alert or instrument. Times are epoch nanoseconds."""
    async for event in app.store.follow_firings(actions.workspace_of(call), params.alertId, params.instrumentId, cursor):
        yield event


@module.on_start
async def start(ctx: Context) -> None:
    global app
    directories = await ctx.platform.storage()
    app = ChartApp(ctx, Path(directories["dataDir"]), Path(directories["userDir"]) / "indicators")
    app.warm()  # the Pine worker pool and the catalog, before the frontend asks
    # subscribed before the open workspaces are listed, so one opening in between is not missed
    changes = await ctx.subscribe("platform.workspace.changes")
    for workspace in await ctx.platform.open_workspaces():
        app.workspace_opened(workspace)
    app.watch_platform(changes, _follow_workspaces)
    app.watch_platform(await ctx.subscribe("platform.settings.changes"), _follow_settings)


async def _follow_workspaces(changes) -> None:
    """Alerts watch while their workspace is open; a closed workspace's charts go back to their saved revisions."""
    async for change in changes:
        if change["kind"] == "opened":
            app.workspace_opened(change["workspaceId"])
        elif change["kind"] == "closed":
            app.workspace_closed(change["workspaceId"])


async def _follow_settings(changes) -> None:
    """New alerts start from the settings of their workspace; the panels hear when they change."""
    async for change in changes:
        if change["module"] == "chart":
            await app.alert_defaults_changed(change["workspaceId"])


@module.on_copy_workspace
async def copy_workspace(source: str, copy: str) -> None:
    """A copied workspace gets its own copies of the charts and watchlists (alerts, screens stay with the original)."""
    app.copy_workspace(source, copy)


@module.private_routes
def routes(ctx: Context):
    """The Chart panel's own requests (``wire.ROUTES``) and its two streams over WebSockets: one chart's, and the
    workspace's alerts, watchlists, screens, templates and health."""
    from .routes import router
    return router(app)


# ── public actions: what AI, other modules and running code call; the panel does the same through its routes ──

@module.action(effects=["read"], errors=["not_found", "invalid_input", "too_large"],
               examples=[{"view": "list"}])
async def read_chart(input: m.ReadChartInput, call: Call) -> Answer:
    """Read the workspace's charts: what the user sees. view=list: every chart (chartId, symbol, timeframe, chart
    type, revision, saved, Market's data status while it is open, how many indicators, drawings and comparisons).
    view=one: one chart's panes, indicators (id, name, params, pane), comparisons and the drawings shown now (tool,
    points with ISO-8601 UTC times and prices, text), 50 drawings per page (nextCursor). The chart's bars and indicator
    values come from read_data with its chartId; change a chart with write_chart at its revision. The current view
    resource names the chart on screen. view=layer with chartId and layerId returns the saved research layer or its JSON FileRef.
    view=workspace reads alert defaults and engine/storage health.
    view=templates lists global templates, or reads one including content by templateId. Example: {"view":"one","chartId":"chart-1a2b"}."""
    return await actions.read_chart(app, _value(input), call)


@module.action(effects=["read"], errors=["not_found", "invalid_input", "too_large", "unavailable"],
               examples=[{"symbol": "okx:BTC/USDT", "timeframe": "1h", "indicators": [{"name": "rsi", "params": {"period": 14}}], "limit": 50}])
async def read_data(input: m.ReadDataInput, call: Call) -> Answer:
    """Bars and indicator values, as columns: of a chart (chartId: its series and every indicator on it, the values
    it shows), or of a symbol and timeframe with indicators computed without a chart (indicators: [{name,
    params}], names from read_indicator). Rows are closed bars, oldest first, the latest `limit` (default 100, max
    500) of the window Chart computes on (the latest 10,000 bars, the same window charts and alerts use); nextCursor
    pages back through it. Columns: time (bar open, ISO-8601 UTC), open, high, low, close, volume, then
    <indicatorId>.<output> (null where the indicator has no value); `columns` picks some. indicators[].outputs names
    each output column; an indicator that cannot compute says why in its error and the others still come. status is
    Market's state of the series (connecting, live, delayed, history_only, market_closed, recovering, failed);
    forming is the bar still open. An answer over 18,000 characters fails with too_large and how many rows fit.
    Example: {"symbol":"okx:BTC/USDT","timeframe":"1h","indicators":[{"name":"rsi","params":{"period":14}}],
    "limit":50}."""
    return await actions.read_data(app, _value(input), call)


@module.action(effects={"path": "operation.op", "branches": {
    "create": {"effects": ["write"]},
    "edit": {"effects": ["write"], "target": "operation.chartId"},
    "remove": {"effects": ["delete"], "target": "operation.chartId"},
}}, errors=["not_found", "invalid_input", "conflict", "too_large"],
               examples=[{"operation": {"op": "create", "symbol": "AAPL", "timeframe": "1d", "indicators": [{"name": "sma", "params": {"period": 200}}, {"name": "rsi", "params": {"period": 14}}]}},
                         {"operation": {"op": "edit", "chartId": "chart-1a2b", "baseRevision": 7, "changes": [{"kind": "indicator", "name": "ema", "params": {"period": 50}}]}}])
async def write_chart(input: m.WriteChartInput, call: Call) -> m.ChartChangeResult:
    """op=create makes a new chart {symbol, timeframe, indicators [{name, params}]} (names and params from
    read_indicator; oscillators get their own pane) and returns its chartId, revision and openTarget; show it with
    platform.panels.open_target. Other ops change a chart at the revision you read (baseRevision; a stale one fails
    with conflict and the current revision). op=edit applies changes in order, adding or changing only: kind=selection {symbol, timeframe,
    priceBasis, adjustment}; kind=chart_type {chartType}; kind=indicator {name (from read_indicator),
    params, paneId, label, visible} adds one, or with the id of one on the chart changes it; new oscillators get
    their own pane; kind=drawing {tool, points [{time ISO-8601, price}], text, style, settings, label, locked, visible} adds one
    (tools: trendline, ray, extended_line, hline, horizontal_ray, vline, rect, parallel_channel, fib, text, note,
    arrow_marker, long_position, short_position, path, ...), or with an id changes it; kind=comparison {symbol,
    scale, color} adds another symbol, or with an id changes it. op=remove deletes indicators, drawings or
    comparisons or layers by id. For research snapshots create with layers:[ResearchLayer]; an existing chart with a
    matching layer id is reused. kind=layer {layer} adds or replaces a snapshot by id. The change is on the user's undo stack. Returns the
    new revision and the ids of what was added. Create accepts importId or copyOf alone. For panel edits use
    commands instead of changes, with the panel's type and fields (times in epoch milliseconds). Examples:
    [{type:"display.update",patch:{timeZone:"UTC"}},{type:"pane.update",id:"price",height:4}].
    Use op=remove for a batch containing deletion or drawings.replace/indicators.replace. See the Chart guide."""
    with changes.recording(call):
        return await actions.write_chart(app, _value(input), call)


@module.action(effects=["write"], errors=["not_found", "unavailable", "invalid_input"],
               examples=[{"chartId": "chart-1a2b", "bars": 500}])
async def backfill_chart(input: m.BackfillChart, call: Call) -> w.BackfillResult:
    """Extend an open chart's historical window by bars or back to an ISO date (one required). Read the added
    history with read_data or export_chart. Returns loaded, reachedStart and limitReached."""
    if (input.to is None) == (input.bars is None):
        raise ContractError("invalid_input", "Give exactly one of to and bars")
    body = w.Backfill(chartId=input.chartId, bars=input.bars, to=actions.ms_of(input.to, "to") if input.to is not None else None)
    return await app.backfill(actions.workspace_of(call), body)


@module.action(effects=["write"], errors=["not_found", "unavailable", "invalid_input", "conflict"],
               examples=[{"operation": {"op": "start", "chartId": "chart-1a2b", "at": "2026-11-16"}}])
async def replay_chart(input: m.ReplayInput, call: Call) -> Answer:
    """Control bar replay on an open chart. Start at an ISO date/time, step by 1 to 1000 bars, or stop to return
    to live data. Returns active, current bar time (UTC epoch milliseconds) and end of window."""
    workspace, op = actions.workspace_of(call), input.operation
    flow = app.flow(workspace, op.chartId)
    if op.op == "start":
        await flow.start_replay(actions.ms_of(op.at, "at"))
    elif op.op == "step":
        await flow.step_replay(op.bars)
    else:
        await flow.stop_replay()
    return flow.replay_item()


@module.action(effects=["read"], errors=["not_found", "unavailable"],
               examples=[{"chartId": "chart-1a2b", "format": "parquet"}])
async def export_chart(input: w.ExportChart, call: Call) -> m.ExportedChart:
    """Export all closed bars and computed indicator columns of the open chart window to CSV, JSON or Parquet.
    Returns filename, mediaType, rows and file {path, sha256, bytes}. Chart keeps the file for 24 hours."""
    flow = app.flow(actions.workspace_of(call), input.chartId)
    columns = exports.table(flow)
    return await asyncio.to_thread(exports.hand_over, app.export_folder, exports.filename(flow, input.format),
                                   input.format, columns)


@module.action(effects={"path": "operation.op", "branches": {
    "save": {"effects": ["write"], "target": "operation.templateId"},
    "apply": {"effects": ["write"], "target": "operation.chartId"},
    "delete": {"effects": ["delete"], "target": "operation.templateId"},
}}, errors=["not_found", "invalid_input", "conflict"],
               examples=[{"operation": {"op": "save", "chartId": "chart-1a2b", "name": "Trend", "kind": "indicators"}}])
async def write_template(input: m.WriteTemplate, call: Call) -> Answer:
    """Save a global template from a chart, apply one at the chart's baseRevision, or delete by templateId.
    kind=indicators saves indicators and panes, chart saves appearance, drawing saves the selected tool's style
    and settings (drawingId required to save or apply). Read ids and content with read_chart view=templates."""
    with changes.recording(call):
        return await actions.write_template(app, input.operation, call)


@module.action(effects=["read"], errors=["not_found", "invalid_input", "too_large"],
               examples=[{"view": "one", "name": "rsi"}])
async def read_indicator(input: m.ReadIndicatorInput, call: Call) -> Answer:
    """The indicator library. view=list: built-in and the user's indicators (name, source builtin or user, title,
    short title, category, overlay), filter by query, category or source, 100 per page. view=one: one indicator's
    description, inputs (id, type, default, bounds, options), alert conditions and, for the user's, whether it loads
    (error, line) and its code's file uri. Output ids appear as read_data columns. Example:
    {"view":"one","name":"rsi"}."""
    return await actions.read_indicator(app, _value(input), call)


@module.action(effects={"path": "operation.op", "branches": {
    "create": {"effects": ["write", "code"]},
    "update": {"effects": ["write", "code"], "target": "operation.name"},
    "delete": {"effects": ["delete"], "target": "operation.name"},
}}, errors=["not_found", "invalid_input", "conflict"],
               examples=[{"operation": {"op": "create", "name": "distance.py", "content": "\"\"\"@pyne\"\"\"\nfrom pynecore.lib import close, plot, script, ta\n\n@script.indicator(\"Distance from MA\", overlay=False)\ndef main():\n    plot(close - ta.sma(close, 50), \"Distance\")\n"}}])
async def write_indicator(input: m.WriteIndicatorInput, call: Call) -> m.IndicatorWritten:
    """Save one of the user's indicators and check it, as Chart's editor does (how to write one: the indicators doc,
    platform.ai.guide {module:"chart", topic:"indicators"}). op=create writes a new file, op=update replaces a file's
    whole text, op=delete removes an indicator. name is the file in the user's indicators folder: chan.py, or
    chan/main.py and chan/helpers.py for an indicator of several files (named chan). Returns the indicator's name and
    check: how Chart loads it now (title, overlay, inputs, alert conditions; error and line when it does not load).
    Fix the error and update until it loads, then add it to a chart with write_chart by that name."""
    return await actions.write_indicator(app, _value(input), call)


@module.action(effects=["read"], errors=["not_found", "invalid_input", "too_large"],
               examples=[{"view": "log", "limit": 20}])
async def read_alert(input: m.ReadAlertInput, call: Call) -> Answer:
    """The workspace's alerts. view=list (20 per page, filter by state active, paused, triggered, expired) and
    view=one: target (a chart's series, a symbol and timeframe, or every member of a watchlist on a timeframe),
    conditions, frequency, state and why, Market's state of the series, outdated (an indicator changed since), last
    fired. view=log: triggers newest first (time, bar, symbol, message, value, delivery), optionally of one alertId;
    90 days are kept. Example: {"view":"log","limit":20}."""
    return await object_actions.read_alert(app, _value(input), call)


@module.action(effects={"path": "operation.op", "branches": {
    "create": {"effects": ["write"]},
    "update": {"effects": ["write"], "target": "operation.alertId"},
    "pause": {"effects": ["write"], "target": "operation.alertId"},
    "resume": {"effects": ["write"], "target": "operation.alertId"},
    "delete": {"effects": ["delete"], "target": "operation.alertId"},
}}, errors=["not_found", "invalid_input", "conflict"],
               examples=[{"operation": {"op": "create", "target": {"symbol": "okx:BTC/USDT", "timeframe": "1h"}, "conditions": [{"left": {"kind": "price"}, "op": "crossing_up", "right": {"kind": "value", "value": 100000}}]}},
                         {"operation": {"op": "update", "alertId": "<alertId>", "revision": 2, "frequency": "once_per_bar_close"}}])
async def write_alert(input: m.WriteAlertInput, call: Call) -> Answer:
    """Create and manage alerts. They watch while the workspace is open, on realtime bars only. op=create {target,
    conditions, frequency, expiresAt, message, name, channels}: target {chartId} (that chart's series), {symbol,
    timeframe} or {watchlistId, timeframe}; up to 5 conditions that must all hold: {left, op, right} with operands
    price, indicator, drawing (a chart's drawing; not on watchlists), value; channel ops take a
    channel drawing or right and right2; moving ops take left, amount and bars; triggered takes an alertcondition.
    frequency once (default), once_per_bar, once_per_bar_close, once_per_minute. op=update at its revision merges:
    the fields given replace, null clears an optional one, the rest stay; op=pause, op=resume (also retries a failed
    one) at its revision; op=delete."""
    with changes.recording(call):
        return await object_actions.write_alert(app, _value(input), call)


@module.action(effects=["read"], errors=["not_found", "invalid_input", "too_large"],
               examples=[{"view": "list"}])
async def read_watchlist(input: m.ReadWatchlistInput, call: Call) -> Answer:
    """The workspace's watchlists. view=list: name, revision and sections. view=one: members in order with their
    section and current quote (price, change and percent with its basis, delayed, as of), 50 per page. Example:
    {"view":"one","watchlistId":"wl-1a2b"}."""
    return await object_actions.read_watchlist(app, _value(input), call)


@module.action(effects={"path": "operation.op", "branches": {
    "create": {"effects": ["write"]},
    "update": {"effects": ["write"], "target": "operation.watchlistId"},
    "delete": {"effects": ["delete"], "target": "operation.watchlistId"},
}}, errors=["not_found", "invalid_input", "conflict"],
               examples=[{"operation": {"op": "create", "name": "Banks", "sections": [{"name": "US", "symbols": ["JPM", "BAC", "WFC", "C"]}]}},
                         {"operation": {"op": "update", "watchlistId": "wl-1a2b", "revision": 3, "add": [{"symbol": "okx:ETH/USDT"}]}}])
async def write_watchlist(input: m.WriteWatchlistInput, call: Call) -> Answer:
    """Create and change watchlists (at most 200 symbols each). op=create {name, sections [{name, symbols}]};
    op=update at its revision: name, sections (replaces all), add [{symbol, section}] (a new section name creates
    it), remove [symbol]; op=delete. Symbols as Market writes them (okx:BTC/USDT, AAPL, 600519.SH)."""
    with changes.recording(call):
        return await object_actions.write_watchlist(app, _value(input), call)


@module.action(effects=["read"], errors=["not_found", "invalid_input", "too_large"],
               examples=[{"view": "list"}])
async def read_screen(input: m.ReadScreenInput, call: Call) -> Answer:
    """The workspace's screens. view=list: name, scope, timeframe, conditions and the last run (state, done of total,
    how many matched). view=one: the last run's rows (symbol, matched, the values compared, bar time, error),
    matchedOnly to keep matches, 50 per page. Example: {"view":"one","screenId":"sc-1a2b","matchedOnly":true}."""
    return await object_actions.read_screen(app, _value(input), call)


@module.action(effects={"path": "operation.op", "branches": {
    "save": {"effects": ["write"], "target": "operation.screenId"},
    "run": {"effects": ["write"], "target": "operation.screenId"},
    "delete": {"effects": ["delete"], "target": "operation.screenId"},
}}, task=True, errors=["not_found", "invalid_input", "conflict"],
               examples=[{"operation": {"op": "save", "name": "RSI under 30", "scope": {"watchlistId": "wl-1a2b"}, "timeframe": "1d", "conditions": [{"left": {"kind": "indicator", "name": "rsi", "output": "rsi"}, "op": "less", "right": {"kind": "value", "value": 30}}]}}])
async def write_screen(input: m.WriteScreenInput, call: Call) -> Answer:
    """Screens find the symbols whose latest closed bar meets every condition. op=save {name, scope {watchlistId} or
    {symbols}, timeframe, conditions} creates one, or with screenId and revision changes it; conditions as alerts'
    with price, indicator and value operands. op=run computes it on each member's latest
    10,000 bars and finishes with the matches; op=delete. Every op returns a task: read it with platform.tasks.get,
    cancel a run with platform.tasks.cancel. Example: {"operation":{"op":"save","name":"RSI under 30","scope":
    {"watchlistId":"wl-1a2b"},"timeframe":"1d","conditions":[{"left":{"kind":"indicator","name":"rsi",
    "output":"rsi"},"op":"less","right":{"kind":"value","value":30}}]}}."""
    with changes.recording(call):
        return await object_actions.write_screen(app, _value(input), call)


@module.action(effects=["write"],
               examples=[{}])
async def restart_indicators(input: m.RestartIndicatorsInput, call: Call) -> m.RestartIndicatorsInput:
    """Start Chart's indicator processes anew; the indicators on charts, alerts and screens start again (the health
    status item's Restart)."""
    if app is None:
        raise ContractError("unavailable", "Chart is starting")
    app.engine.restart_pool()
    return {}
