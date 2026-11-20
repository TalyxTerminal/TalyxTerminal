export const marketPanelStyles = `
.market-panel { display: flex; flex-direction: column; height: 100%; min-height: 0; min-width: 0; }
.market-tabs { flex: none; padding: 0 var(--tx-space-3); border-bottom: var(--tx-hairline) solid var(--tx-line); }
.market-fill { flex: 1 1 auto; display: flex; align-items: center; justify-content: center; height: 100%; }
.market-clip { overflow: hidden; text-overflow: ellipsis; white-space: nowrap; min-width: 0; }

/* Library: a compact toolbar with the path, the list, one series' detail on the right. */
.market-library { display: flex; flex-direction: column; flex: 1 1 auto; min-height: 0; min-width: 0; }
.market-start { display: flex; flex-direction: column; gap: var(--tx-space-3); width: min(640px, 100%); margin: auto; padding: var(--tx-space-6) 0; overflow: auto; }
.market-start-title { font-size: var(--tx-size-lg); font-weight: var(--tx-weight-strong); padding: 0 var(--tx-space-2) var(--tx-space-1); }
.market-start-list { display: flex; flex-direction: column; border-top: var(--tx-hairline) solid var(--tx-line); border-bottom: var(--tx-hairline) solid var(--tx-line); padding: var(--tx-space-1) 0; }
.market-start .market-pick { min-height: 36px; }
.market-start .market-pick > svg { color: var(--tx-text-3); }
.market-start-more { display: flex; gap: var(--tx-space-2); padding: 0 var(--tx-space-2); }
.market-crumbs { display: flex; align-items: center; gap: var(--tx-space-half); min-width: 0; }
.market-crumb-sep { color: var(--tx-text-4); padding: 0 var(--tx-space-half); }
.market-crumb-current { color: var(--tx-text); font-weight: var(--tx-weight-strong); }
.market-list { height: 100%; min-height: 0; min-width: 0; overflow: auto; }
.market-filter { width: 260px; }

/* Text: the code and the dataset are the line; numbers next; names, sources and sizes recede. */
.market-group > td { padding-top: var(--tx-space-3); background: var(--tx-bg); }
.market-group-label { font-size: var(--tx-size-key); letter-spacing: .07em; text-transform: uppercase; color: var(--tx-text-3); font-weight: var(--tx-weight-medium); }
.market-group-count { margin-left: var(--tx-space-2); color: var(--tx-text-4); font-weight: var(--tx-weight); }
.market-strong { color: var(--tx-text); }
.market-faint { color: var(--tx-text-4); }
.market-name { color: var(--tx-text-3); }
.market-sets, .market-series, .market-sources { table-layout: fixed; }
.market-set-row { cursor: pointer; }
.market-col-set { width: 16%; } .market-col-count { width: 96px; } .market-col-date { width: 104px; } .market-col-gaps { width: 60px; }
.market-col-source { width: 12%; } .market-col-size { width: 84px; } .market-col-status { width: 136px; } .market-col-open { width: 40px; }
.market-col-instrument { width: 32%; } .market-series[data-narrow] .market-col-instrument { width: 40%; } .market-col-kind { width: 168px; } .market-col-rows { width: 76px; }
.market-instrument { display: flex; align-items: baseline; gap: var(--tx-space-2); min-width: 0; overflow: hidden; }
.market-instrument > :not(:last-child) { flex: none; }
.market-instrument > :last-child { flex: 1 1 auto; min-width: 0; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.market-open { text-align: right; }
.market-open > *, .market-open-cell > * { opacity: 0; }
tr:hover > .market-open > *, tr[data-selected="true"] > .market-open > *, tr:hover > .market-open-cell > *, .market-open > :focus-visible, .market-open-cell > :focus-visible { opacity: 1; }
.market-ticks { position: relative; display: block; height: 1.4em; }
.market-ticks > span { position: absolute; top: 0; padding-left: var(--tx-space-1); border-left: var(--tx-hairline) solid var(--tx-line-2); font-size: var(--tx-size-2xs); color: var(--tx-text-3); font-weight: var(--tx-weight); }
.market-gaps { color: var(--tx-ember-text); }

/* Coverage: a series' stretch on its dataset's axis; a dataset's density through time. Bright on a dark track. */
.market-bar, .market-density { position: relative; display: block; height: 10px; border-radius: var(--tx-space-half); background: var(--tx-white-025); overflow: hidden; }
.market-bar > i { position: absolute; top: 2px; bottom: 2px; border-radius: var(--tx-hairline); }
.market-bar-fill { background: var(--tx-text-2); }
.market-bar-fill[data-status="delisted"], .market-bar-fill[data-status="ended"] { background: var(--tx-text-3); }
tr[data-selected="true"] .market-bar-fill:not([data-status="delisted"]):not([data-status="ended"]) { background: var(--tx-text); }
.market-bar-tail { background: repeating-linear-gradient(90deg, var(--tx-text-3) 0 2px, var(--tx-clear) 2px 4px); }
.market-bar-tail[data-status="failed"] { background: repeating-linear-gradient(90deg, var(--tx-ember) 0 2px, var(--tx-clear) 2px 4px); }
.market-bar-tail[data-status="behind"] { background: repeating-linear-gradient(90deg, var(--tx-watch) 0 2px, var(--tx-clear) 2px 4px); }
.market-bar-gap { position: absolute; top: 0; bottom: 0; width: 2px; margin-left: -1px; background: var(--tx-ember); }
.market-density { display: flex; }
.market-density > i { flex: 1 1 0; background: var(--tx-text-2); }
.market-density-end { position: absolute; right: 0; top: 0; bottom: 0; width: 3px; background: var(--tx-ember); }
.market-density-end[data-status="behind"] { background: var(--tx-watch); }
/* One dataset's head: what it is, key numbers, what decides whether to trust it. */
.market-head { display: flex; flex-direction: column; gap: var(--tx-space-2); padding: var(--tx-space-3) var(--tx-space-3); border-bottom: var(--tx-hairline) solid var(--tx-line); }
.market-head-title { display: flex; align-items: center; gap: var(--tx-space-3); }
.market-head-name { font-size: var(--tx-size-lg); font-weight: var(--tx-weight-strong); }
.market-stats { display: flex; flex-wrap: wrap; align-items: center; gap: var(--tx-space-1) var(--tx-space-6); }
.market-stat { display: inline-flex; align-items: baseline; gap: var(--tx-space-1-half); color: var(--tx-text-3); white-space: nowrap; }
.market-stat > b { font-size: var(--tx-size-lg); font-weight: var(--tx-weight-medium); color: var(--tx-text); }
.market-stat[data-tone="fix"] > b { color: var(--tx-ember-text); }
.market-facts { display: grid; grid-template-columns: repeat(auto-fit, minmax(340px, 1fr)); gap: var(--tx-space-1) var(--tx-space-5); margin: 0; }
.market-fact { display: grid; grid-template-columns: 92px minmax(0, 1fr); gap: var(--tx-space-2); min-width: 0; }
.market-fact dt { color: var(--tx-text-3); }
.market-fact dd { margin: 0; color: var(--tx-text-2); overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.market-show { display: flex; padding: var(--tx-space-2) var(--tx-space-3); }

/* Fetch jobs above the list. */
.market-problem { flex: none; padding: var(--tx-space-1) var(--tx-space-3); border-bottom: var(--tx-hairline) solid var(--tx-line); }
.market-tasks { flex: none; display: flex; flex-direction: column; padding: var(--tx-space-1) 0; border-bottom: var(--tx-hairline) solid var(--tx-line); background: var(--tx-panel); }
.market-task { display: grid; grid-template-columns: minmax(0, 360px) minmax(60px, 1fr) auto auto max-content auto; align-items: center; gap: var(--tx-space-3); min-height: 30px; padding: 0 var(--tx-space-3); }
.market-task-count, .market-task-size { text-align: right; white-space: nowrap; }
.market-task-actions { display: flex; justify-content: flex-end; gap: var(--tx-space-1); }

/* Series detail. */
.market-coverage { display: flex; flex-direction: column; gap: var(--tx-space-1); }
.market-coverage-year { display: flex; align-items: center; gap: var(--tx-space-3); }
.market-coverage-label { width: 40px; flex: none; }
.market-coverage-key { display: flex; gap: var(--tx-space-3); margin-top: var(--tx-space-2); color: var(--tx-text-3); }
.market-detail-title { display: flex; align-items: baseline; gap: var(--tx-space-2); min-width: 0; }
.market-fact-status { display: inline-flex; align-items: center; flex-wrap: wrap; gap: var(--tx-space-2); min-width: 0; }

/* Sources. */
.market-sources-view { flex: 1 1 auto; min-height: 0; display: flex; flex-direction: column; }
.market-col-use { width: 56px; } .market-col-name { width: 160px; } .market-col-state { width: 240px; } 
.market-latency { color: var(--tx-text-2); }
.market-source-status { display: flex; align-items: center; gap: var(--tx-space-2); min-width: 0; }
.market-sources-view > .tx-split > .tx-split-main > .tx-toolbar { flex: none; } .market-col-local { width: 84px; } .market-col-key { width: 92px; }
.market-source-name { display: flex; align-items: center; gap: var(--tx-space-2); min-width: 0; }
.market-source-row { cursor: pointer; }
.market-source-row[data-off="true"] td { color: var(--tx-text-3); }
.market-sheet { display: flex; flex-direction: column; gap: var(--tx-space-3); }
.market-sheet-pair { display: grid; grid-template-columns: minmax(0, 1fr) 120px; gap: var(--tx-space-3); }
.market-key-row { display: grid; grid-template-columns: minmax(0, 1fr) auto; gap: var(--tx-space-2); align-items: end; }
.market-test { display: flex; flex-direction: column; gap: var(--tx-space-1); }

/* Get data. */
.market-get { display: grid; grid-template-columns: minmax(0, 1fr) 400px; gap: var(--tx-space-6); min-height: 400px; }
.market-get-col { display: flex; flex-direction: column; gap: var(--tx-space-3); min-width: 0; }
.market-get-list { display: flex; flex-direction: column; min-width: 0; }
.market-get-picked { max-height: 240px; overflow: auto; padding-bottom: var(--tx-space-2); border-bottom: var(--tx-hairline) solid var(--tx-line); }
.market-get-offered { flex: 1 1 auto; min-height: 0; max-height: 300px; overflow: auto; }
.market-pick { display: grid; grid-template-columns: minmax(96px, max-content) minmax(0, 1fr) auto auto; align-items: center; gap: var(--tx-space-3); width: 100%; min-width: 0; min-height: 30px; }
.market-kinds { display: flex; flex-direction: column; gap: var(--tx-space-1); }
.market-kind { display: grid; grid-template-columns: 156px minmax(0, 1fr); align-items: start; gap: var(--tx-space-2); min-height: 28px; padding: var(--tx-space-1) 0; }
.market-kind-from { display: flex; flex-direction: column; min-width: 0; padding-top: var(--tx-space-half); }
.market-get-sum { display: flex; flex-direction: column; gap: var(--tx-space-1); padding-top: var(--tx-space-3); border-top: var(--tx-hairline) solid var(--tx-line); }

.market-export { display: flex; flex-direction: column; gap: var(--tx-space-3); }
.market-disk { color: var(--tx-ember-text); }

/* Import. */
.market-import-folder { display: flex; align-items: flex-end; gap: var(--tx-space-2); }
.market-import-folder > .tx-field { flex: 1 1 auto; min-width: 0; }
.market-import-summary { display: flex; align-items: center; gap: var(--tx-space-2); margin-top: var(--tx-space-3); }
.market-import-files { max-height: 46vh; overflow: auto; margin-top: var(--tx-space-2); }
`;
