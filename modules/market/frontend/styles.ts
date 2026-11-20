export const marketStyles = `
.market-search { display: flex; flex-direction: column; gap: var(--tx-space-2); min-width: 0; min-height: 0; height: min(600px, calc(100dvh - 180px)); }
.market-search .tx-tabs { flex-wrap: wrap; gap: var(--tx-space-3); }
.market-search-filters { display: grid; grid-template-columns: minmax(0, 200px); gap: 8px; }
.market-search-filters .tx-select { min-width: 0; width: 100%; }
.market-search-results { flex: 1; min-height: 0; min-width: 0; overflow-y: auto; overflow-x: hidden; container-type: inline-size; }
.market-search-row { display: flex; align-items: center; min-width: 0; border-bottom: 1px solid var(--tx-line); }
.market-search-row[data-active] { background: var(--tx-ember-select); }
.market-search-row[data-current] { box-shadow: inset 2px 0 0 var(--tx-ember); }
.market-search-select { display: grid; flex: 1; grid-template-columns: minmax(95px,1.1fr) minmax(130px,1.6fr) minmax(75px,1fr) minmax(100px,1.1fr); min-width: 0; min-height: 34px; height: auto; text-align: left; gap: var(--tx-space-2); border: 0; }
.market-search-select > span { min-width: 0; }
.market-search-select > span { overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.market-search-venue { text-align: right; }
.market-search-venue small { margin-right: var(--tx-space-1); color: var(--tx-text-3); }
.market-search-symbol { font-weight: var(--tx-weight-strong); color: var(--tx-text); }
.market-search-match { background: none; color: var(--tx-ember-text); }
.market-search-kind { display: flex; gap: var(--tx-space-1); }
.market-search-feedback { flex: none; min-width: 0; }
.market-search-source-error { display: flex; flex-wrap: wrap; align-items: center; gap: var(--tx-space-2); }
.market-search-feedback .tx-details { max-height: 150px; overflow: auto; }
@container (max-width: 640px) { .market-search-select { grid-template-columns: minmax(0,1fr) minmax(0,1fr); padding-block: var(--tx-space-2); } .market-search-select > span:nth-child(3) { color: var(--tx-text-3); font-size: var(--tx-size-xs); } .market-search-venue { text-align: left; } }
.market-search-series .market-search-select { min-height: 32px; padding-left: var(--tx-space-5); }
.market-search-name small { color: var(--tx-text-3); }
`;
