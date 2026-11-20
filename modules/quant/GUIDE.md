---
name: Quant research
description: Strategies (backtests), factors, models, allocations and notebooks. Build one, run it on a defined sample, read and compare its results.
---

Use quant.<action>. Updates merge; null resets. list_objects defaults to this workspace, scope all searches every workspace.

Usual order
- Backtest: create_object {kind:"strategy", preset:"crossover", data:{symbols:["binance:BTC/USDT"], timeframe:"1d", start:"2020-01-01"}, params:{fast:20, slow:100}} -> start_run {objectId}
- Factor: create_object {kind:"factor", formula:"-($close/Ref($close,5)-1)", data:{index:{symbol:"^GSPC"}, timeframe:"1d", start:"2018-01-01"}, run:{holding:5}} -> start_run. read_object options lists formula fields and operators.
- Change and compare: update_object -> start_run -> read_run {runId, compareTo}. Sweep: start_sweep {objectId, axes:[{name:"slow", values:[100,150]}]}
- Model: create_object {kind:"model", data} -> read_object options -> update_object {params} -> check_model -> start_run
- Allocation: create_object {kind:"allocation", data:{symbols}, params} -> trial_allocation {objectId} -> start_run
- Own code: read the strategy/research doc, save code with create_object or update_object, fix codeErrors, then start_run.
- Notebook: create_object {kind:"notebook", name} -> write_cell {objectId, op:"add", source} -> run_cell {objectId, cellId}.
- Held-out: set run.heldOutFrom before tuning; finally plan_held_out -> validate_held_out {runId}, once
- Deeper: read_run {runId, part}; rows and daily series: read_run_table {runId, table, start, end}, format:"file" for code

To show fills on Chart, call quant.read_run {runId,part:"chartLayer"}, chart.write_chart {operation:{op:"create",layers:[data.layer]}}, then platform.panels.open_target with its openTarget. Pass data.layer unchanged. buys/sells count the fills, layerIds confirms attachment, and the returned panelId confirms display.

start_run returns its result.

Pin inputs with libraryInputs {name:versionId}. A name alone binds on first use. read_object gives saved pins, read_run {runId, part:"inputVersions"} the frozen versions, read_library_item the version number.

Numbers
- start and end are inclusive; no end = latest close. A result's tested.start/end are the days actually tested; symbols gives set, used and dropped.
- Fractions unless the name ends in Pct or Bps; declared params show their unit in options.
- Factor group returns are demeaned by default; turnover = share of names replaced.
- list_objects numbers are the representative run's.
- Annualization uses the trading calendar.
