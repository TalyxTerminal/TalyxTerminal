# Panel edits and chart windows

- `write_chart` can create from a Market import with `symbol`, `timeframe`, and `importId`, or copy the complete document with `{op:"create",copyOf:chartId}`.
- For panel operations pass `commands` in place of `changes`. Each command has a `type`. For example, `[{type:"display.update",patch:{timeZone:"Asia/Singapore",scale:{mode:"log"}}},{type:"pane.update",id:"price",height:4}]`. Use `display.replace` with `display` for the full settings. Use `pane.move` with `id,direction` (-1 or 1).
- `drawing.add` takes `value:{id,tool,points,settings,...}`. Panel point times are UTC epoch milliseconds. `drawing.update` takes `id,patch`. `drawing.bulk` takes optional `ids` and one of `locked,visible,remove`. `drawing.duplicate` takes `ids`. `drawing.reorder` takes `id,direction`. `drawings.replace` takes `drawings`.
- `indicator.add` takes `value:{id,definition:{source,id},paneId,params}`. `indicator.update` takes `id,patch` (including `styles` keyed by output id). `indicator.duplicate` takes `id`. `indicator.reorder` takes `id,direction`. `indicators.replace` takes both `indicators` and `panes`. Use `op:"remove"` for batches containing a deletion or either group replacement. Edits support platform undo by call.
- Read global templates with `chart.read_chart {view:"templates"}`, then pass `templateId` for the content. `chart.write_template` saves from a chart by kind (indicators, chart, drawing), applies by `templateId,chartId,baseRevision`, and deletes by `templateId`. Drawing templates also take `drawingId` when saving or applying.
- With the chart open, `chart.backfill_chart {chartId,bars:500}` extends its window. Use `to` instead of `bars` for an ISO date. `chart.replay_chart {operation:{op:"start",chartId,at:"2026-11-16T12:00:00Z"}}` starts practice on a loaded bar. Step with `op:"step",bars:1`; finish with `op:"stop"`. Backfill first for an earlier date.
- `chart.export_chart {chartId,format:"parquet"}` exports the whole displayed window, including computed indicator columns, and returns rows and a file reference (path, sha256, bytes). CSV and JSON are also available. `chart.read_chart {view:"workspace"}` returns Chart engine/storage health and alert defaults.

## Research overlays

Embed `chart.layer_chart` with `{layer, focusTime?}`. Pass a ResearchLayer or a module's JSON FileRef. The view reads the instrument's candles from Market on the snapshot's timeframe and adjustment. It shares one time axis across price, the panes above it and the panes below it.

`write_chart {operation:{op:"create",layers:[layer]}}` opens a document on the first layer's instrument, timeframe and adjustment. A matching layer id reuses that document. Use the returned openTarget with platform.panels.open_target. To add or replace on a particular chart, use `{op:"edit",chartId,baseRevision,changes:[{kind:"layer",layer}]}`. To delete use `{op:"remove",chartId,baseRevision,layers:[id]}`. A saved document keeps its own snapshot even after the caller removes the source file.

Each point belongs to the most recent candle open at or before its timestamp. Coarser candles keep the last series value and every trade marker. A layer can cover up to 200,000 candles, 200,000 points per series and 20,000 trade markers.
