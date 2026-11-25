---
name: Chart
description: The workspace's charts, indicators, alerts, watchlists and screens. Read what a chart shows, make and edit charts, set alerts.
---

Find charts with chart.read_chart {view:"list"}.

Usual calls
- New chart: chart.write_chart {operation:{op:"create", symbol:"AAPL", timeframe:"1d", indicators:[{name:"sma", params:{period:50}}]}} -> chartId and openTarget. Show it with platform.panels.open_target {module:"chart", panelType:"main", objectId:"chart:<chartId>"}.
- Change a chart: chart.read_chart {view:"one", chartId}, then chart.write_chart {operation:{op:"edit", chartId, baseRevision, changes}}. Put related changes in one edit. A revision conflict means the chart changed: read it again before deciding what still applies.
- Indicator inputs, bounds and output ids: chart.read_indicator {view:"one", name}. The user's own indicators are listed and used like the built-ins.
- A new indicator: read platform.ai.guide {module:"chart", topic:"indicators"} (from Pine: "pine" too), then chart.write_indicator {operation:{op:"create", name, content}}. Use the returned name directly in the chart's indicators array. Fix a check.error with op:"update".
- Research overlays snapshot supplied series and fills on matching candles. chart.write_chart {operation:{op:"create",layers:[layer]}} accepts a ResearchLayer or its JSON FileRef, reuses a chart with the same layer id, and returns openTarget. To remove it use {op:"remove",chartId,baseRevision,layers:[id]}.
- Alerts: chart.write_alert {operation:{op:"create", target, conditions, frequency}}; a condition uses an indicator's output id. Updating an alert merges: only the fields given change.
- Watchlists: chart.write_watchlist {operation:{op:"create", name, sections:[{name, symbols}]}}; add and remove change the members of an existing list. Make a new list for a new collection unless the user named one to edit.
- Screens: chart.write_screen saves and runs a screen (a long task); read the matches and per-symbol errors with chart.read_screen.

Panel commands, chart copies/imports, templates, backfill_chart, replay_chart and whole-window export_chart: platform.ai.guide {module:"chart",topic:"editing"}. read_chart also lists templates and workspace health.

Conventions that change numbers
- A-share charts open adjusted for splits and dividends, unlike Market's split-only default. Say the chart's price basis when quoting its prices.
- chart.read_data covers the range the chart loaded; report the dates it returned.
- Alerts watch live bars only while their workspace is open. An alert's firing history and its notification delivery are separate records.
