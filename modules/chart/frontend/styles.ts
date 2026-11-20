/* Chart panel styles: layout from tokens; colours, type and spacing come from the platform. */
export const chartStyles = `
.chart-research { position: relative; display: flex; flex-direction: column; width: 100%; height: 100%; min-width: 0; min-height: 0; background: var(--tx-panel); }
.chart-research-head { position: absolute; top: var(--tx-space-1); right: calc(var(--chart-research-axis) + var(--tx-space-1)); z-index: 3; border-radius: var(--tx-radius-pill); background: var(--tx-chart-legend); }
.chart-research-canvas { flex: 1 1 auto; min-width: 0; min-height: 0; }
.chart-tree-layer-owner { flex: 0 1 auto; min-width: 0; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; color: var(--tx-text-3); font-size: var(--tx-size-xs); }
.chart-panel { display: flex; flex-direction: column; height: 100%; min-height: 0; min-width: 0; background: var(--tx-panel); }
.chart-loading { display: flex; align-items: center; justify-content: center; height: 100%; color: var(--tx-text-3); font-size: var(--tx-size-sm); }

/* A narrow chart scrolls its bars sideways (wheel or touchpad; no resident scrollbar) instead of cutting them off. */
.chart-topbar, .chart-bottombar { flex: none; display: flex; align-items: center; gap: var(--tx-space-half); height: 38px; padding: 0 var(--tx-space-1-half); overflow-x: auto; overflow-y: hidden; overscroll-behavior-x: contain; }
.chart-topbar > :not(.chart-tb-spacer), .chart-bottombar > :not(.chart-tb-spacer) { flex: none; }
.chart-topbar { border-bottom: var(--tx-hairline) solid var(--tx-line); }
.chart-bottombar { height: 32px; border-top: var(--tx-hairline) solid var(--tx-line); }
.chart-tb-symbol { font-weight: var(--tx-weight-strong); color: var(--tx-text); }
.chart-tb-divider { flex: none; width: var(--tx-hairline); height: 20px; margin: 0 var(--tx-space-1); background: var(--tx-line); }
.chart-tb-spacer, .chart-dt-spacer { flex: 1 1 auto; }
.chart-clock { font-variant-numeric: tabular-nums; }

.chart-body { flex: 1 1 auto; display: flex; min-height: 0; min-width: 0; }
.chart-body > .tx-split { flex: 1 1 auto; min-width: 0; min-height: 0; }
.chart-main { flex: 1 1 auto; display: flex; flex-direction: column; min-width: 0; min-height: 0; height: 100%; }
.chart-main-row { flex: 1 1 auto; display: flex; min-width: 0; min-height: 0; }

.chart-bottom { flex: none; display: flex; flex-direction: column; min-height: 0; position: relative; border-top: var(--tx-hairline) solid var(--tx-line); }
.chart-bottom-edge { position: absolute; top: -3px; left: 0; right: 0; height: 6px; cursor: row-resize; z-index: 2; }
.chart-bottom-tabs { flex: none; display: flex; align-items: center; gap: var(--tx-space-half); }
.chart-bottom-toolbar { flex: none; display: flex; align-items: center; gap: var(--tx-space-1); height: 32px; padding: 0 var(--tx-space-1-half); }
.chart-bottom-body { flex: 1 1 auto; display: flex; flex-direction: column; min-height: 0; }
.chart-bottom-body > .tx-empty { margin: auto; }
.chart-bottom-toolbar > .chart-alert-error { flex: 0 1 auto; min-width: 0; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.chart-bottom-title { font-weight: var(--tx-weight-strong); }
.chart-bottom-view { flex: 1 1 auto; min-height: 0; overflow: auto; }
.chart-screen-name { width: 160px; }
.chart-screen-conditions { flex: none; display: flex; flex-wrap: wrap; align-items: center; gap: var(--tx-space-1) var(--tx-space-3); padding: 0 var(--tx-space-1-half) var(--tx-space-1); }
.chart-screen-condition { display: flex; align-items: center; gap: var(--tx-space-half); }
.chart-screen-condition > .chart-condition { flex-direction: row; align-items: center; }
.chart-screen-condition .chart-style-row { flex-wrap: nowrap; gap: var(--tx-space-half); }
.chart-bottom-toolbar .tx-select, .chart-screen-condition .tx-select { flex: none; width: auto; max-width: 220px; }
.chart-screen-condition .tx-input, .chart-screen-condition .tx-affix { width: 110px; }
.chart-bottom-toolbar > .chart-muted { white-space: nowrap; }
.chart-screen-row { cursor: pointer; }

.chart-rail { flex: none; display: flex; flex-direction: column; align-items: center; gap: var(--tx-space-1); width: 46px; padding: var(--tx-space-1) 0; border-left: var(--tx-hairline) solid var(--tx-line); }
.chart-rail-panel { display: flex; flex-direction: column; height: 100%; min-height: 0; min-width: 0; }
.chart-rail-head { flex: none; display: flex; align-items: center; gap: var(--tx-space-1); min-height: 38px; padding: 0 var(--tx-space-1-half); border-bottom: var(--tx-hairline) solid var(--tx-line); }
.chart-rail-title { font-weight: var(--tx-weight-strong); }
.chart-rail-list { flex: 1 1 auto; min-height: 0; overflow: auto; }
.chart-rail-note { padding: var(--tx-space-1-half) var(--tx-space-2); }
.chart-rail-center { display: flex; justify-content: center; }
.chart-watch-list { display: grid; column-gap: var(--tx-space-2); align-content: start; }
.chart-watch-list > *, .chart-watch-list [role="group"] > *, .chart-watch-item > .chart-watch-row { grid-column: 1 / -1; }
.chart-watch-list [role="group"], .chart-watch-item, .chart-watch-row { display: grid; grid-template-columns: subgrid; align-items: center; }
.chart-watch-row { font-variant-numeric: tabular-nums; }
.chart-watch-value { white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
.chart-watch-columns { position: sticky; top: 0; z-index: 1; background: var(--tx-panel); padding: var(--tx-space-1) var(--tx-space-2); font-size: var(--tx-size-xs); color: var(--tx-text-3); border-bottom: var(--tx-hairline) solid var(--tx-line); }
.chart-watch-columns > span:not(:first-child), .chart-watch-value { text-align: right; }
.chart-watch-item { position: relative; }
.chart-watch-item[data-current] { box-shadow: inset 2px 0 0 var(--tx-ember); }
.chart-watch-item > .tx-icon-btn { position: absolute; right: var(--tx-space-1); visibility: hidden; }
.chart-watch-item:hover > .tx-icon-btn { visibility: visible; }
.chart-watch-symbol { overflow: hidden; text-overflow: ellipsis; white-space: nowrap; color: var(--tx-text); font-weight: var(--tx-weight-medium); }
.chart-watch-value[data-direction="up"] { color: var(--tx-up); }
.chart-watch-value[data-direction="down"] { color: var(--tx-down); }
.chart-watch-section { width: 100%; }
.chart-watch-details { flex: none; display: flex; flex-direction: column; gap: var(--tx-space-1); padding: var(--tx-space-2); border-top: var(--tx-hairline) solid var(--tx-line); }
.chart-watch-details-head { display: flex; flex-direction: column; gap: 2px; }
.chart-watch-details-price { display: flex; flex-wrap: wrap; align-items: baseline; column-gap: var(--tx-space-2); font-variant-numeric: tabular-nums; }
.chart-watch-details-price > .chart-watch-value { overflow: visible; }
.chart-watch-big { font-size: var(--tx-size-num); font-weight: var(--tx-weight-strong); color: var(--tx-text); }
.chart-watch-details-meta { display: flex; flex-wrap: wrap; align-items: center; gap: var(--tx-space-2); font-size: var(--tx-size-sm); color: var(--tx-text-2); }
.chart-alert-item { display: flex; align-items: center; gap: var(--tx-space-2); padding: var(--tx-space-1-half) var(--tx-space-2); border-bottom: var(--tx-hairline) solid var(--tx-line); }
.chart-alert-text { flex: 1 1 auto; display: flex; flex-direction: column; min-width: 0; }
.chart-alert-text > span { overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.chart-alert-title { color: var(--tx-text); }
.chart-alert-tags { flex: none; display: inline-flex; gap: var(--tx-space-1); }
.chart-alert-actions { display: none; }
.chart-alert-item:hover > .chart-alert-actions { display: inline-flex; }
.chart-alert-time { flex: none; font-variant-numeric: tabular-nums; }
.chart-tree-pane { padding: var(--tx-space-2) var(--tx-space-2) var(--tx-space-1); font-size: var(--tx-size-xs); color: var(--tx-text-3); }
.chart-tree-row { display: flex; align-items: center; gap: var(--tx-space-2); min-height: 28px; padding: 0 var(--tx-space-2) 0 var(--tx-space-4); color: var(--tx-text); }
.chart-tree-row > span:first-child { flex: 1 1 auto; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.chart-tree-row[data-hidden] > span:first-child { color: var(--tx-text-3); }
.chart-tree-actions { display: none; }
.chart-tree-row:hover > .chart-tree-actions { display: inline-flex; flex: none; }
.chart-data-row { display: flex; justify-content: space-between; gap: var(--tx-space-2); padding: 2px var(--tx-space-2) 2px var(--tx-space-4); font-variant-numeric: tabular-nums; }
.chart-data-row > span:first-child { color: var(--tx-text-2); }
.chart-drawbar { flex: none; display: flex; flex-direction: column; align-items: center; gap: 2px; width: 46px; padding: var(--tx-space-1) 0; border-right: var(--tx-hairline) solid var(--tx-line); overflow: hidden; }
.chart-dt-group { position: relative; display: flex; }
.chart-dt-more { position: absolute; right: -6px; bottom: 0; width: 10px; min-width: 0; height: 14px; padding: 0; opacity: 0; }
.chart-dt-group:hover .chart-dt-more, .chart-dt-more[data-state="open"] { opacity: 1; }
.chart-dt-divider { flex: none; width: 28px; height: var(--tx-hairline); margin: var(--tx-space-1) 0; background: var(--tx-line); }
.chart-dt-menu { display: flex; flex-direction: column; min-width: 240px; padding: var(--tx-space-1) 0; }
.chart-dt-menu-title, .chart-layouts-title { padding: var(--tx-space-1) var(--tx-space-2); font-size: var(--tx-size-xs); color: var(--tx-text-3); }
.chart-dt-menu .tx-btn { display: flex; align-items: center; gap: var(--tx-space-2); }
.chart-dt-menu kbd { margin-left: auto; }

.chart-starlist { display: flex; flex-direction: column; min-width: 220px; max-height: 70vh; overflow: auto; padding: var(--tx-space-1) 0; }
.chart-starlist-group + .chart-starlist-group { border-top: var(--tx-hairline) solid var(--tx-line); margin-top: var(--tx-space-1); padding-top: var(--tx-space-1); }
.chart-starlist-head { display: flex; justify-content: space-between; }
.chart-starlist-row { display: flex; align-items: center; }
.chart-starlist-row > .tx-btn { flex: 1 1 auto; display: flex; align-items: center; gap: var(--tx-space-2); }
.chart-starlist-row > .tx-icon-btn { visibility: hidden; }
.chart-starlist-row:hover > .tx-icon-btn, .chart-starlist-row[data-starred="true"] > .tx-icon-btn { visibility: visible; }

.chart-search { height: min(560px, 70vh); display: flex; flex-direction: column; min-height: 0; }
.chart-library { display: flex; flex-direction: column; gap: var(--tx-space-2); height: min(560px, 70vh); min-height: 0; }
.chart-library-body { flex: 1 1 auto; display: flex; min-height: 0; border-top: var(--tx-hairline) solid var(--tx-line); }
.chart-library-nav { flex: none; display: flex; flex-direction: column; width: 200px; padding: var(--tx-space-1) 0; overflow: auto; border-right: var(--tx-hairline) solid var(--tx-line); }
.chart-library-sub { padding-left: var(--tx-space-4); }
.chart-library-list { flex: 1 1 auto; overflow: auto; padding: var(--tx-space-1) 0; }
.chart-library-row { display: flex; align-items: center; }
.chart-library-row > .tx-btn { flex: 1 1 auto; display: flex; justify-content: space-between; gap: var(--tx-space-2); }
.chart-library-row > .tx-icon-btn { visibility: hidden; }
.chart-library-row:hover > .tx-icon-btn, .chart-library-row > .tx-icon-btn[data-tone="active"] { visibility: visible; }
.chart-library-error { color: var(--tx-state-fix); font-size: var(--tx-size-xs); overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }

.chart-layouts { display: flex; flex-direction: column; gap: 2px; padding: var(--tx-space-1-half); }
.chart-layouts-row { display: flex; align-items: center; gap: 2px; }
.chart-layouts-count { width: 16px; text-align: center; font-size: var(--tx-size-xs); color: var(--tx-text-3); }
.chart-layout-picture { display: grid; gap: 1px; width: 20px; height: 16px; }
.chart-layout-picture > span { border: var(--tx-hairline) solid currentColor; border-radius: 1px; }
.chart-layouts-sync { display: flex; flex-direction: column; gap: var(--tx-space-1); margin-top: var(--tx-space-1); padding-top: var(--tx-space-1); border-top: var(--tx-hairline) solid var(--tx-line); }
.chart-form { display: flex; flex-direction: column; gap: var(--tx-space-2); padding-top: var(--tx-space-2); }
.chart-settings-form { min-width: 380px; max-height: 60vh; overflow: auto; }
.chart-settings { display: flex; gap: var(--tx-space-3); min-height: 300px; }
.chart-settings-nav { flex: none; display: flex; flex-direction: column; gap: 2px; width: 150px; padding-right: var(--tx-space-2); border-right: var(--tx-hairline) solid var(--tx-line); }
.chart-settings-nav > .tx-btn { justify-content: flex-start; }
.chart-settings-page { flex: 1 1 auto; min-width: 280px; padding-top: 0; }
.chart-style-row { display: flex; align-items: center; gap: var(--tx-space-2); flex-wrap: wrap; }
.chart-levels { display: grid; grid-template-columns: repeat(2, minmax(0, 1fr)); gap: var(--tx-space-1) var(--tx-space-3); }
.chart-condition { display: flex; flex-direction: column; gap: var(--tx-space-1); }
.chart-alert-error { flex: 1 1 auto; color: var(--tx-state-fix); font-size: var(--tx-size-sm); }
.chart-muted { color: var(--tx-text-3); font-size: var(--tx-size-sm); }

.chart-grid { flex: 1 1 auto; display: grid; min-width: 0; gap: var(--tx-hairline); min-height: 0; background: var(--tx-line); }
.chart-grid-cell { position: relative; min-width: 0; min-height: 0; overflow: hidden; background: var(--tx-panel); }
.chart-cell { position: absolute; inset: 0; }
.chart-price-plus { position: absolute; z-index: 3; transform: translateY(-50%); }
.chart-replay-pick { position: absolute; inset: 0; z-index: 4; cursor: crosshair; }
.chart-replay-line { position: absolute; top: 0; bottom: 0; width: 1px; background: var(--tx-series-1); pointer-events: none; }
.chart-replay-shade { position: absolute; top: 0; bottom: 0; right: 0; background: var(--tx-black-25); pointer-events: none; }
.chart-replay-bar { position: absolute; bottom: var(--tx-space-8); left: 50%; transform: translateX(-50%); z-index: 3; display: flex; align-items: center; gap: var(--tx-space-1); padding: var(--tx-space-1); border-radius: var(--tx-radius-menu); white-space: nowrap; }
.chart-panel-note { margin: 0; padding: var(--tx-space-1) var(--tx-space-3); }
.chart-drawing-bar { position: absolute; top: var(--tx-space-2); left: 50%; transform: translateX(-50%); z-index: 3; display: flex; align-items: center; gap: var(--tx-space-1); padding: var(--tx-space-1); border-radius: var(--tx-radius-menu); }
.chart-cell[data-active="true"]::after { content: ""; position: absolute; inset: 0; border: var(--tx-hairline) solid var(--tx-ember); pointer-events: none; z-index: 3; }
.chart-canvas { position: absolute; inset: 0; outline: none; }

.chart-legend { position: absolute; top: var(--tx-space-1-half); left: calc(var(--tx-space-2) + var(--chart-scale-left, 0px)); z-index: 2; display: flex; flex-direction: column; gap: 2px; max-width: calc(100% - 96px); font-size: var(--tx-size-sm); color: var(--tx-text-2); pointer-events: none; }
.chart-legend-row { display: flex; align-items: center; gap: var(--tx-space-2); min-width: 0; white-space: nowrap; }
.chart-legend-row > * { pointer-events: auto; }
/* A legend line that keeps to its pane: parts that don't fit wrap onto a second line that is cut off. */
.chart-legend-fit { flex-wrap: wrap; align-content: flex-start; height: 22px; overflow: hidden; }
.chart-legend-fit > * { height: 22px; display: inline-flex; align-items: center; }
.chart-legend-head { display: inline-flex; align-items: center; gap: var(--tx-space-2); min-width: 0; max-width: 100%; }
.chart-legend-title, .chart-legend-name { min-width: 0; overflow: hidden; text-overflow: ellipsis; }
.chart-legend-title { color: var(--tx-text); font-weight: var(--tx-weight-medium); }
.chart-legend-values { display: flex; gap: var(--tx-space-2); font-variant-numeric: tabular-nums; }
.chart-legend-values[data-direction="up"] { color: var(--tx-up); }
.chart-legend-values[data-direction="down"] { color: var(--tx-down); }
.chart-legend-key { margin-right: 2px; color: var(--tx-text-3); }
.chart-state-dot { flex: none; width: 6px; height: 6px; border-radius: 50%; background: var(--tx-state-ok-mark); }
.chart-state-dot[data-state="delayed"], .chart-state-dot[data-state="recovering"] { background: var(--tx-state-watch); }
.chart-state-dot[data-state="market_closed"], .chart-state-dot[data-state="history_only"] { background: var(--tx-state-off-mark); }
.chart-state-dot[data-state="failed"] { background: var(--tx-state-fix-mark); }
.chart-data-state { color: var(--tx-text-3); }
.chart-data-state[data-state="failed"] { color: var(--tx-state-fix); }
/* A row with hover actions takes the pointer over its whole width, gaps included: otherwise the pointer leaves the row
   between the name and the actions that appear after it, and they go before it gets there (a row without values). */
.chart-legend-indicator { align-self: flex-start; pointer-events: auto; font-variant-numeric: tabular-nums; }
.chart-legend-indicator[data-hidden="true"] { color: var(--tx-text-4); }
.chart-legend-name { color: var(--tx-text-2); }
.chart-legend-error { color: var(--tx-state-fix); overflow: hidden; text-overflow: ellipsis; }
.chart-legend-fit > .chart-legend-actions { display: none; gap: 2px; }
.chart-legend-row:hover > .chart-legend-actions { display: inline-flex; }
.chart-legend-toggle { pointer-events: auto; }
`;
