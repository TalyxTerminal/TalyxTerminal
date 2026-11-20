/* Research workbench pieces: the panel head with one run button, run progress, the data chip, the run strip,
   key numbers, checks, the two-layer parameter column, linked inputs, the runs table, tags and state words,
   per-period strips, the heatmap and in-place failures. Generic: nothing here knows a module. */
export const researchCss = `
.tx-panelhead { height: 48px; flex: none; display: flex; align-items: center; gap: 8px; padding: 0 10px; border-bottom: 1px solid var(--tx-line); min-width: 0; container-type: inline-size; }
.tx-panelhead > * { flex: none; }
.tx-panelhead > .tx-objtitle { flex: 0 0 auto; max-width: 36%; min-width: 110px; }
.tx-panelhead > .tx-datachip { flex: 0 1 auto; min-width: 0; }
.tx-panelhead > .tx-spacer { flex: 1 1 0; min-width: 0; }
.tx-objtitle { display: flex; align-items: baseline; gap: 8px; padding: 4px 6px; border-radius: var(--tx-radius-sm); white-space: nowrap; min-width: 0; background: none; border: 0; color: inherit; cursor: pointer; }
.tx-objtitle:hover { background: var(--tx-white-04); }
.tx-objtitle b { font-size: var(--tx-size-lg); font-weight: var(--tx-weight-strong); letter-spacing: var(--tx-tracking-heading); overflow: hidden; text-overflow: ellipsis; }
.tx-objtitle .tx-objkind { font-size: var(--tx-size-sm); color: var(--tx-text-3); }
.tx-objtitle .tx-tag { align-self: center; }
.tx-datachip { display: inline-flex; align-items: center; height: 28px; padding: 0 6px 0 10px; gap: 9px; border: 1px solid var(--tx-line-2); border-radius: var(--tx-radius-sm); background: var(--tx-white-02); font-size: var(--tx-size-sm); color: var(--tx-text-2); white-space: nowrap; overflow: hidden; cursor: pointer; }
.tx-datachip:hover { border-color: var(--tx-line-3); color: var(--tx-text); }
.tx-datachip > span { flex: none; }
.tx-datachip > b { font-weight: var(--tx-weight-medium); color: var(--tx-text); flex: 0 1 auto; min-width: 3.5em; overflow: hidden; text-overflow: ellipsis; }
.tx-datachip .tx-sep { width: 1px; height: 12px; background: var(--tx-line-3); flex: none; }
.tx-datachip .tx-lock { display: inline-flex; align-items: center; gap: 4px; }
.tx-datachip .tx-icon { width: 11px; height: 11px; color: var(--tx-text-3); }
.tx-datachip > .tx-icon:last-child { width: 12px; height: 12px; }
/* A hidden part takes the separator after it; a separator with no part still shown after it (the hidden parts were last) goes too. */
@container (max-width: 1180px) { .tx-datachip [data-priority="2"], .tx-datachip [data-priority="2"] + .tx-sep, .tx-datachip .tx-sep:not(:has(~ :is(b, span):not(.tx-sep, [data-priority="2"]))) { display: none; } }
@container (max-width: 1060px) { .tx-datachip [data-priority="3"], .tx-datachip [data-priority="3"] + .tx-sep, .tx-datachip .tx-sep:not(:has(~ :is(b, span):not(.tx-sep, [data-priority="2"], [data-priority="3"]))) { display: none; } }
@container (max-width: 1180px) { .tx-objtitle .tx-objkind { display: none; } }
@container (max-width: 960px) { .tx-panelhead .tx-codetoggle span { display: none; } }
.tx-panelhead:has(.tx-runprogress) .tx-datachip [data-priority="2"], .tx-panelhead:has(.tx-runprogress) .tx-datachip [data-priority="2"] + .tx-sep, .tx-panelhead:has(.tx-runprogress) .tx-datachip .tx-sep:not(:has(~ :is(b, span):not(.tx-sep, [data-priority="2"]))) { display: none; }
.tx-codetoggle { display: inline-flex; align-items: center; gap: 6px; height: 28px; padding: 0 10px; border-radius: var(--tx-radius-pill); font-size: var(--tx-size-sm); font-weight: var(--tx-weight-medium); color: var(--tx-text-3); white-space: nowrap; background: none; border: 0; cursor: pointer; }
.tx-codetoggle:hover { color: var(--tx-text); background: var(--tx-white-04); }
.tx-codetoggle[aria-pressed="true"] { color: var(--tx-text); }
.tx-codetoggle .tx-icon { width: 14px; height: 14px; }
.tx-runbutton { display: inline-flex; align-items: stretch; height: 28px; border-radius: var(--tx-radius-pill); border: 1px solid var(--tx-ember-40); background: linear-gradient(180deg, var(--tx-ember-20), var(--tx-ember-14)); color: var(--tx-ember-text); overflow: hidden; flex: none; }
.tx-runbutton:hover { border-color: var(--tx-ember-55); }
.tx-runbutton > button { display: inline-flex; align-items: center; gap: 7px; padding: 0 12px 0 11px; font-size: var(--tx-size-button); font-weight: var(--tx-weight-medium); color: inherit; white-space: nowrap; background: none; border: 0; cursor: pointer; }
.tx-runbutton > button:hover { background: var(--tx-ember-14); }
.tx-runbutton > button:first-child .tx-icon { width: 11px; height: 11px; }
.tx-runbutton > button:first-child .tx-icon[data-fill] { fill: currentColor; stroke: none; }
.tx-runbutton > button:last-child { padding: 0 7px; border-left: 1px solid var(--tx-ember-30); }
.tx-runbutton > button:last-child .tx-icon { width: 13px; height: 13px; transform: rotate(90deg); }
.tx-runbutton[data-locked] { border-color: var(--tx-line-3); background: var(--tx-white-04); color: var(--tx-text); }
.tx-runbutton[data-locked] > button:last-child { border-left-color: var(--tx-line-2); }
.tx-runbutton[data-disabled] { opacity: .45; pointer-events: none; }
.tx-runprogress { display: inline-flex; align-items: center; gap: 9px; height: 28px; padding: 0 4px 0 10px; border-radius: var(--tx-radius-pill); border: 1px solid var(--tx-line-2); font-size: var(--tx-size-sm); color: var(--tx-text-2); white-space: nowrap; overflow: hidden; background: none; cursor: pointer; }
.tx-runprogress:hover { border-color: var(--tx-line-3); }
.tx-runprogress b { font-family: var(--tx-font-mono); font-weight: var(--tx-weight-medium); color: var(--tx-text); }
.tx-runprogress .tx-runstage { display: none; overflow: hidden; text-overflow: ellipsis; max-width: 180px; }
.tx-runprogress .tx-runcount { font-family: var(--tx-font-mono); font-size: var(--tx-size-xs); color: var(--tx-text-2); }
@container (min-width: 1240px) { .tx-runprogress .tx-runstage { display: inline; } .tx-runprogress .tx-runstage + .tx-runcount { display: none; } }
.tx-runprogress .tx-runbar { width: 64px; height: 3px; border-radius: 2px; background: var(--tx-line-2); overflow: hidden; flex: none; }
.tx-runprogress .tx-runbar i { display: block; height: 100%; background: var(--tx-text-2); }
.tx-runprogress .tx-runtime { font-family: var(--tx-font-mono); font-size: var(--tx-size-xs); color: var(--tx-text-3); }
.tx-runprogress .tx-runstop { display: inline-flex; align-items: center; gap: 5px; height: 22px; padding: 0 9px; border-radius: var(--tx-radius-pill); font-size: var(--tx-size-xs); color: var(--tx-text-2); }
.tx-runprogress .tx-runstop:hover { background: var(--tx-white-06); color: var(--tx-text); }
.tx-runprogress .tx-runstop .tx-icon { width: 10px; height: 10px; }
.tx-spin { width: 11px; height: 11px; border-radius: 50%; border: 1.5px solid var(--tx-line-3); border-top-color: var(--tx-text); animation: tx-spin var(--tx-spin-time) linear infinite; flex: none; display: inline-block; }
@keyframes tx-spin { to { transform: rotate(360deg); } }
.tx-runsteps { min-width: 280px; padding: 6px 4px; }
.tx-runsteps > div { display: grid; grid-template-columns: 18px minmax(0, 1fr) auto; align-items: center; gap: 8px; height: 26px; padding: 0 8px; font-size: var(--tx-size-sm); color: var(--tx-text-3); }
.tx-runsteps > div[data-state="done"] { color: var(--tx-text-2); }
.tx-runsteps > div[data-state="now"] { color: var(--tx-text); }
.tx-runsteps .tx-icon { width: 13px; height: 13px; }
.tx-runsteps .tx-steptime { font-family: var(--tx-font-mono); font-size: var(--tx-size-xs); color: var(--tx-text-3); }.tx-runstrip { min-height: 40px; flex: none; display: flex; flex-wrap: wrap; align-items: center; gap: 4px 12px; padding: 4px 12px 4px 14px; border-bottom: 1px solid var(--tx-line); font-size: var(--tx-size-sm); color: var(--tx-text-3); white-space: nowrap; min-width: 0; }
.tx-runstrip .tx-runwhen { color: var(--tx-text-2); }
.tx-runstrip .tx-runmeta { flex: 1 1 0; overflow: hidden; text-overflow: ellipsis; min-width: 0; }
.tx-runstrip .tx-runmeta span + span { margin-left: 12px; }
.tx-runstrip .tx-spacer { flex: 1; }
.tx-runpick { display: inline-flex; align-items: center; gap: 5px; height: 24px; padding: 0 6px 0 9px; border-radius: var(--tx-radius-pill); border: 1px solid var(--tx-line-2); font-family: var(--tx-font-mono); font-size: var(--tx-size-sm); font-weight: var(--tx-weight-medium); color: var(--tx-text); background: none; cursor: pointer; }
.tx-runpick:hover { border-color: var(--tx-line-3); }
.tx-runpick .tx-icon, .tx-compare .tx-icon { width: 12px; height: 12px; color: var(--tx-text-3); transform: rotate(90deg); }
.tx-compare { display: inline-flex; align-items: center; gap: 7px; height: 24px; padding: 0 6px 0 8px; border-radius: var(--tx-radius-pill); color: var(--tx-text-2); background: none; border: 0; cursor: pointer; font-size: var(--tx-size-sm); }
.tx-compare:hover { background: var(--tx-white-04); color: var(--tx-text); }
.tx-compare i { width: 16px; border-top: 1.5px dashed var(--tx-text-2); }
.tx-compare b { font-family: var(--tx-font-mono); font-weight: var(--tx-weight-medium); color: var(--tx-text); }
.tx-notes { color: var(--tx-text-3); padding: 2px 6px; border-radius: 4px; background: none; border: 0; cursor: pointer; font-size: var(--tx-size-sm); }
.tx-notes:hover { color: var(--tx-text-2); background: var(--tx-white-04); }
.tx-keynumbers { display: grid; grid-template-columns: repeat(auto-fit, minmax(max(150px, calc(100% / var(--tx-count, 6) - 1px)), 1fr)); flex: none; overflow: hidden; border-bottom: 1px solid var(--tx-line); }
.tx-keynumbers > * { display: block; text-align: left; padding: 9px 12px 10px 14px; min-width: 0; font: inherit; color: inherit; background: none; border: 0; }
.tx-keynumbers > * { box-shadow: -1px 0 0 var(--tx-line), 0 -1px 0 var(--tx-line); }
.tx-keynumbers > button { cursor: pointer; }
.tx-keynumbers > button:hover { background: var(--tx-white-02); }
.tx-keynumbers .tx-knl { font-size: var(--tx-size-xs); color: var(--tx-text-3); white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
.tx-keynumbers .tx-knv { font-family: var(--tx-font-mono); font-size: 18px; line-height: 26px; letter-spacing: var(--tx-tracking-tight); white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
.tx-keynumbers .tx-knv small { font-family: var(--tx-font); font-size: var(--tx-size-xs); color: var(--tx-text-3); margin-left: 4px; letter-spacing: var(--tx-tracking); }
.tx-keynumbers .tx-knd { font-family: var(--tx-font-mono); font-size: var(--tx-size-xs); color: var(--tx-text-3); white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
.tx-keynumbers .tx-knd em { font-style: normal; color: var(--tx-text-4); }
.tx-keynumbers [data-pending] .tx-knv { color: var(--tx-text-4); }
.tx-keynumbers [data-pending] .tx-knd { font-family: var(--tx-font); color: var(--tx-text-4); }
.tx-up { color: var(--tx-up); } .tx-down { color: var(--tx-down); }
.tx-checklist { min-width: 0; font-size: var(--tx-size-sm); }
.tx-checklist > header { display: flex; align-items: center; gap: 10px; height: 34px; padding: 0 12px 0 14px; }
.tx-checklist > header h3 { margin: 0; font-size: var(--tx-size-sm); font-weight: var(--tx-weight-strong); }
.tx-checklist > header .tx-ckfix { color: var(--tx-ember-text); }
.tx-checklist > header .tx-ckwatch { color: var(--tx-watch); }
.tx-checklist > header .tx-ckscope { margin-left: auto; color: var(--tx-text-4); font-size: var(--tx-size-xs); }
.tx-checklist .tx-ckgroup { padding: 8px 14px 3px; font-size: var(--tx-size-xs); color: var(--tx-text-4); }
/* A check's name and value keep their whole text; where the row is short, the rule ("≥ 50% of in-sample") gives way first. */
.tx-check-row { display: grid; grid-template-columns: 16px minmax(max-content, 1fr) auto minmax(0, max-content) 14px; align-items: center; column-gap: 8px; width: 100%; height: 28px; padding: 0 10px 0 14px; border: 0; border-top: 1px solid var(--tx-line); background: none; text-align: left; color: var(--tx-text-2); font: inherit; cursor: pointer; }
.tx-check-row:hover { background: var(--tx-white-02); }
.tx-check-row > span:first-child { display: flex; align-items: center; justify-content: center; }
.tx-check-row > span:first-child i { width: 6px; height: 6px; border-radius: 50%; }
.tx-check-row > span:first-child .tx-icon { width: 13px; height: 13px; color: var(--tx-text-4); }
.tx-check-row .tx-ckname { white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
.tx-check-row .tx-ckvalue { font-family: var(--tx-font-mono); letter-spacing: var(--tx-tracking-tight); text-align: right; white-space: nowrap; color: var(--tx-text); }
.tx-check-row .tx-ckneed { min-width: 0; overflow: hidden; text-overflow: ellipsis; font-family: var(--tx-font-mono); font-size: var(--tx-size-xs); letter-spacing: var(--tx-tracking-tight); text-align: right; white-space: nowrap; color: var(--tx-text-4); }
.tx-check-row > span:last-child .tx-icon { width: 12px; height: 12px; color: var(--tx-text-4); opacity: 0; }
.tx-check-row:hover > span:last-child .tx-icon { opacity: 1; }
.tx-check-row[data-state="ok"] .tx-ckvalue { color: var(--tx-text-2); }
.tx-check-row[data-state="watch"] i { background: var(--tx-watch); } .tx-check-row[data-state="watch"] .tx-ckname { color: var(--tx-text); }
.tx-check-row[data-state="fix"] i { background: var(--tx-ember); } .tx-check-row[data-state="fix"] .tx-ckname { color: var(--tx-ember-text); }
.tx-check-row[data-state="notrun"] i { width: 7px; height: 7px; box-shadow: inset 0 0 0 1px var(--tx-text-4); }
.tx-check-row[data-state="notrun"] .tx-ckname { color: var(--tx-text-3); }
.tx-check-row[data-state="notrun"] .tx-ckvalue span { font-family: var(--tx-font); font-size: var(--tx-size-xs); color: var(--tx-text-2); padding: 1px 8px; border-radius: var(--tx-radius-pill); border: 1px solid var(--tx-line-2); }.tx-params { width: var(--tx-params-width, 288px); flex: none; border-right: 1px solid var(--tx-line); display: flex; flex-direction: column; min-height: 0; position: relative; background: var(--tx-panel); }
@media (max-width: 1500px) { .tx-params { --tx-params-width: 264px; } }
.tx-params > .tx-params-scroll { flex: 1; min-height: 0; overflow: auto; scrollbar-width: none; padding-bottom: 18px; }
.tx-params > .tx-params-scroll::-webkit-scrollbar { display: none; }
.tx-params > footer { flex: none; display: flex; align-items: center; gap: 8px; padding: 10px 14px; border-top: 1px solid var(--tx-line); font-size: var(--tx-size-xs); color: var(--tx-text-3); }
.tx-params-layer { display: flex; flex-wrap: wrap; align-items: center; column-gap: 8px; row-gap: 2px; min-height: 38px; padding: 7px 12px 7px 14px; box-sizing: border-box; position: sticky; top: 0; z-index: 2; background: var(--tx-panel); border-bottom: 1px solid var(--tx-line); }
.tx-params-group + .tx-params-layer, .tx-linked + .tx-params-layer { border-top: 1px solid var(--tx-line); }
.tx-params-layer h3 { margin: 0; font-size: var(--tx-size-sm); font-weight: var(--tx-weight-strong); white-space: nowrap; }
.tx-params-layer .tx-layersource { font-size: var(--tx-size-xs); color: var(--tx-text-4); font-family: var(--tx-font-mono); white-space: nowrap; }
.tx-params-layer .tx-layernote { font-size: var(--tx-size-xs); color: var(--tx-text-4); white-space: nowrap; }
.tx-params-layer .tx-layerstate { margin-left: auto; display: inline-flex; align-items: center; gap: 8px; white-space: nowrap; }
.tx-params-layer .tx-layerchanged { font-size: var(--tx-size-xs); color: var(--tx-ember-text); white-space: nowrap; }
.tx-params-layer .tx-layerreset { font-size: var(--tx-size-xs); color: var(--tx-text-3); padding: 2px 6px; border-radius: 4px; background: none; border: 0; cursor: pointer; }
.tx-params-layer .tx-layerreset:hover { color: var(--tx-text); background: var(--tx-white-05); }
.tx-params-group { padding: 2px 0 6px; }
.tx-params-group + .tx-params-group { border-top: 1px solid var(--tx-line); }
.tx-params-group > h4 { display: flex; align-items: center; gap: 6px; height: 30px; padding: 0 12px 0 10px; margin: 0; font-size: var(--tx-size-xs); font-weight: var(--tx-weight-medium); color: var(--tx-text-3); cursor: pointer; user-select: none; white-space: nowrap; }
.tx-params-group > h4:hover { color: var(--tx-text-2); }
.tx-params-group > h4 .tx-icon { width: 11px; height: 11px; color: var(--tx-text-4); transform: rotate(90deg); transition: transform var(--tx-fast) var(--tx-ease); }
.tx-params-group > h4 .tx-groupsummary { font-weight: var(--tx-weight); color: var(--tx-text-4); overflow: hidden; text-overflow: ellipsis; min-width: 0; display: none; }
.tx-params-group > h4 .tx-groupdot { width: 6px; height: 6px; border-radius: 50%; background: var(--tx-ember); flex: none; }
.tx-params-group[data-closed] > h4 .tx-icon { transform: rotate(0deg); }
.tx-params-group[data-closed] > h4 .tx-groupsummary { display: block; }
.tx-params-group[data-closed] > :not(h4) { display: none; }
.tx-params-row { position: relative; display: grid; grid-template-columns: 112px minmax(0, 1fr); align-items: center; gap: 8px; min-height: 30px; padding: 0 12px 0 14px; font-size: var(--tx-size-sm); }
/* A label wraps rather than ending in "…": a code setting's label is often a sentence ("Largest share of one sector (0 = no limit)"). */
.tx-params-row > .tx-paramlabel { color: var(--tx-text-2); white-space: normal; overflow: hidden; line-height: 1.25; padding: var(--tx-space-3-quarter) 0; cursor: default; }
.tx-params-row > .tx-paramlabel[title] { text-decoration: underline dotted var(--tx-text-4); text-underline-offset: 3px; }
.tx-params-row[data-wide] { grid-template-columns: minmax(0, 1fr); }
.tx-params-row[data-revert] { grid-template-columns: 112px minmax(0, 1fr) auto; }
.tx-params-row[data-revert][data-wide] { grid-template-columns: minmax(0, 1fr) auto; }
.tx-params-row[data-changed]::before { content: ''; position: absolute; left: 0; top: 7px; bottom: 7px; width: 2px; border-radius: 1px; background: var(--tx-ember); }
.tx-params-row[data-changed] :where(.tx-input, .tx-select, .tx-affix) { border-color: var(--tx-ember-line); }
.tx-params-row :where(.tx-input, .tx-select) { height: 28px; font-size: var(--tx-size-sm); }
.tx-params-row .tx-affix { height: 28px; }
.tx-params-row .tx-paramvalue { font-family: var(--tx-font-mono); font-size: var(--tx-size-sm); color: var(--tx-text); display: inline-flex; align-items: center; gap: 6px; min-width: 0; }
.tx-params-row .tx-paramvalue[data-text] { font-family: var(--tx-font); color: var(--tx-text-2); }
.tx-params-row .tx-paramvalue .tx-icon { width: 12px; height: 12px; color: var(--tx-text-3); }
.tx-params-range { display: grid; grid-template-columns: minmax(0, 1fr) 10px minmax(0, 1fr); align-items: center; gap: 4px; }
.tx-params-range > span { text-align: center; color: var(--tx-text-4); }
.tx-params-range .tx-input { border-style: dashed; }
.tx-params-rangeby { grid-column: 2; display: flex; align-items: center; gap: 6px; font-size: var(--tx-size-xs); color: var(--tx-text-3); margin-top: -2px; padding-bottom: 3px; }
.tx-params-rangeby .tx-input { width: 64px; height: 22px; border-style: dashed; }
.tx-params-rangeby b { font-family: var(--tx-font-mono); font-weight: var(--tx-weight-medium); color: var(--tx-text-2); }
.tx-params-rangeby button { margin-left: auto; color: var(--tx-text-3); background: none; border: 0; cursor: pointer; display: inline-flex; }
.tx-params-rangeby button:hover { color: var(--tx-text); }
.tx-params-rangeby .tx-icon { width: 12px; height: 12px; }
.tx-params-mini { width: 100%; border-collapse: collapse; font-size: var(--tx-size-sm); }
.tx-params-mini th { height: 24px; font-weight: var(--tx-weight); font-size: var(--tx-size-xs); color: var(--tx-text-4); text-align: right; padding: 0 0 0 4px; }
.tx-params-mini th:first-child, .tx-params-mini td:first-child { text-align: left; padding-left: 0; color: var(--tx-text-2); }
.tx-params-mini td { height: 28px; padding: 0 0 0 4px; text-align: right; }
.tx-params-mini .tx-input { height: 24px; text-align: right; }
.tx-params-mini .tx-affix .tx-input { padding-left: var(--tx-space-1); padding-right: var(--tx-space-6); }
.tx-linked { padding: 2px 0 4px; }
.tx-linked-item { display: grid; grid-template-columns: minmax(0, 1fr) auto; align-items: center; gap: 1px 8px; padding: 7px 12px 7px 14px; }
.tx-linked-item .tx-linkedwarn { grid-column: 1 / -1; font-size: var(--tx-size-xs); color: var(--tx-state-watch); }
.tx-linked-item + .tx-linked-item { border-top: 1px solid var(--tx-line); }
.tx-linked-item .tx-linkedname { display: flex; align-items: center; gap: 6px; min-width: 0; font-size: var(--tx-size-sm); color: var(--tx-text); }
.tx-linked-item .tx-linkedname button { font-weight: var(--tx-weight-medium); overflow: hidden; text-overflow: ellipsis; white-space: nowrap; background: none; border: 0; padding: 0; color: inherit; cursor: pointer; }
.tx-linked-item .tx-linkedname button:hover { text-decoration: underline; text-decoration-color: var(--tx-text-4); text-underline-offset: 3px; }
.tx-linked-item .tx-linkedsource { grid-column: 1; font-size: var(--tx-size-xs); color: var(--tx-text-3); white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
.tx-linked-item .tx-linkedaction { grid-row: 1 / span 2; grid-column: 2; display: flex; align-items: center; gap: 2px; }.tx-runtable { width: 100%; border-collapse: collapse; font-size: var(--tx-size-sm); }
.tx-runtable th { height: 30px; font-weight: var(--tx-weight); font-size: var(--tx-size-xs); color: var(--tx-text-3); text-align: right; padding: 0 8px; border-bottom: 1px solid var(--tx-line-2); white-space: nowrap; position: sticky; top: 0; z-index: 1; background: var(--tx-panel); }
.tx-runtable th[data-align="left"], .tx-runtable td[data-align="left"] { text-align: left; }
.tx-runtable th[data-sortable] { cursor: pointer; } .tx-runtable th[data-sortable]:hover { color: var(--tx-text-2); } .tx-runtable th[data-sorted] { color: var(--tx-text); }
.tx-runtable td { height: 32px; padding: 0 8px; text-align: right; border-bottom: 1px solid var(--tx-line); font-family: var(--tx-font-mono); font-variant-numeric: tabular-nums; letter-spacing: var(--tx-tracking-tight); white-space: nowrap; }
.tx-runtable td[data-kind="text"], .tx-runtable td[data-kind="time"], .tx-runtable td[data-kind="change"] { font-family: var(--tx-font); letter-spacing: var(--tx-tracking); }
.tx-runtable td[data-kind="time"] { color: var(--tx-text-3); }
.tx-runtable td[data-kind="id"] { color: var(--tx-text-2); font-weight: var(--tx-weight-medium); }
.tx-runtable td[data-kind="change"] { white-space: normal; min-width: 180px; max-width: 420px; line-height: 1.35; padding-top: 6px; padding-bottom: 6px; color: var(--tx-text-3); text-align: left; }
.tx-runtable td[data-kind="change"] em { font-style: normal; color: var(--tx-text); }
.tx-runtable td[data-kind="change"] code { font-family: var(--tx-font-mono); color: var(--tx-text); font-size: 12px; }
.tx-runtable tbody tr { cursor: pointer; }
.tx-runtable tbody tr:hover td { background: var(--tx-white-02); }
.tx-runtable tr[data-selected] td { background: var(--tx-ember-select); }
.tx-runtable tr[data-selected] td:first-child { box-shadow: inset 2px 0 0 var(--tx-ember); }
.tx-runtable tr[data-compared] td:first-child { box-shadow: inset 2px 0 0 var(--tx-text-3); }
.tx-runtable tr[data-queued] td { color: var(--tx-text-3); }
.tx-runtable tr[data-group] td { height: 30px; background: var(--tx-white-015); color: var(--tx-text-2); font-family: var(--tx-font); text-align: left; letter-spacing: var(--tx-tracking); }
.tx-runtable tr[data-group] td b { color: var(--tx-text); font-weight: var(--tx-weight-medium); margin-right: 8px; }
.tx-runtable tr[data-group] .tx-icon { width: 11px; height: 11px; color: var(--tx-text-3); display: inline-block; vertical-align: -1px; margin-right: 6px; transform: rotate(90deg); transition: transform var(--tx-fast); }
.tx-runtable tr[data-group][data-closed] .tx-icon { transform: rotate(0deg); }
.tx-runtable .tx-check-box { vertical-align: -2px; }
.tx-runtable .tx-rowaction { font-family: var(--tx-font); font-size: var(--tx-size-xs); color: var(--tx-text-3); padding: 2px 6px; border-radius: 4px; background: none; border: 0; cursor: pointer; }
.tx-runtable .tx-rowaction:hover { color: var(--tx-text); background: var(--tx-white-05); }
.tx-runtable .tx-rowaction[data-primary] { color: var(--tx-text); border: 1px solid var(--tx-line-2); border-radius: var(--tx-radius-pill); padding: 2px 9px; }
.tx-live { display: inline-flex; align-items: center; gap: 8px; font-family: var(--tx-font); letter-spacing: var(--tx-tracking); color: var(--tx-text-2); }
.tx-live .tx-runbar { width: 56px; height: 3px; border-radius: 2px; background: var(--tx-line-2); overflow: hidden; }
.tx-live .tx-runbar i { display: block; height: 100%; background: var(--tx-text-2); }
.tx-tag { display: inline-flex; align-items: center; gap: 4px; height: 18px; padding: 0 6px; border-radius: 4px; border: 1px solid var(--tx-line-3); font-size: 10.5px; line-height: 1; color: var(--tx-text-2); white-space: nowrap; font-family: var(--tx-font); letter-spacing: 0; vertical-align: 1px; font-weight: var(--tx-weight); }
.tx-tag + .tx-tag { margin-left: 3px; }
button.tx-tag { cursor: pointer; background: var(--tx-white-04); }
button.tx-tag:hover { background: var(--tx-white-08); }
.tx-tag .tx-icon { width: 10px; height: 10px; }
.tx-tag[data-tone="fix"] { border-color: var(--tx-ember-line); color: var(--tx-ember-text); }
.tx-tag[data-tone="watch"] { border-color: var(--tx-watch-line); color: var(--tx-watch); }
.tx-tag[data-tone="muted"] { border-color: var(--tx-line-2); color: var(--tx-text-3); }
.tx-tag[data-tone="version"] { font-family: var(--tx-font-mono); font-size: 10px; padding: 0 5px; }
.tx-stateword { display: inline-flex; align-items: center; gap: 7px; white-space: nowrap; font-family: var(--tx-font); letter-spacing: var(--tx-tracking); }
.tx-stateword i { width: 6px; height: 6px; border-radius: 50%; flex: none; background: var(--tx-text-3); }
.tx-stateword[data-tone="fix"] { color: var(--tx-ember-text); } .tx-stateword[data-tone="fix"] i { background: var(--tx-ember); }
.tx-stateword[data-tone="watch"] { color: var(--tx-watch); } .tx-stateword[data-tone="watch"] i { background: var(--tx-watch); }
.tx-stateword[data-tone="ok"] { color: var(--tx-text-2); }
.tx-stateword[data-tone="muted"] { color: var(--tx-text-3); } .tx-stateword[data-tone="muted"] i { background: none; box-shadow: inset 0 0 0 1px var(--tx-text-4); }
.tx-periods { display: inline-flex; align-items: flex-end; gap: 2px; height: 14px; vertical-align: middle; }
.tx-periods i { width: 4px; height: 12px; border-radius: 1px; background: var(--tx-text-4); }
.tx-periods i[data-state="late"] { background: var(--tx-watch); }
.tx-periods i[data-state="miss"] { background: var(--tx-ember); }
.tx-periods i[data-state="none"] { background: var(--tx-white-07); }
.tx-periods i[data-state="pending"] { background: none; box-shadow: inset 0 0 0 1px var(--tx-text-4); }
.tx-periods[data-large] { height: 22px; gap: 3px; } .tx-periods[data-large] i { width: 10px; height: 20px; border-radius: 2px; cursor: pointer; }
.tx-periods[data-large] i:hover { outline: 1px solid var(--tx-line-3); outline-offset: 1px; }
.tx-periods[data-large] i[data-selected] { outline: 1.5px solid var(--tx-text); outline-offset: 1px; }
.tx-heatmap { --tx-heat: var(--tx-user-palette-11); display: grid; gap: 8px; min-width: 0; align-content: start; }
.tx-heatmap > header { display: flex; align-items: center; gap: 10px; font-size: var(--tx-size-sm); min-height: 26px; }
.tx-heatmap > header h3 { margin: 0; font-size: var(--tx-size-sm); font-weight: var(--tx-weight-strong); }
.tx-heatmap > header .tx-heataxes { font-size: var(--tx-size-xs); color: var(--tx-text-3); }
.tx-heatmap > header .tx-heatscale { margin-left: auto; display: flex; align-items: center; gap: 8px; font-family: var(--tx-font-mono); font-size: var(--tx-size-xs); color: var(--tx-text-3); }
.tx-heatmap > header .tx-heatscale i { width: 120px; height: 6px; border-radius: 3px; background: linear-gradient(90deg, color-mix(in srgb, var(--tx-heat) 12%, var(--tx-panel)), color-mix(in srgb, var(--tx-heat) 70%, var(--tx-panel))); }
.tx-heatmap[data-scale="updown"] > header .tx-heatscale i { background: linear-gradient(90deg, color-mix(in srgb, var(--tx-down) 58%, transparent), var(--tx-white-04), color-mix(in srgb, var(--tx-up) 58%, transparent)); }
.tx-heatmap table { border-collapse: separate; border-spacing: 2px; font-family: var(--tx-font-mono); font-size: 11.5px; letter-spacing: var(--tx-tracking-tight); }
.tx-heatmap th { font-weight: var(--tx-weight); color: var(--tx-text-3); padding: 0 6px; height: 22px; white-space: nowrap; text-align: center; }
.tx-heatmap th[scope="row"] { text-align: right; }
.tx-heatmap td { min-width: 54px; height: 30px; text-align: center; border-radius: 4px; color: var(--tx-text); cursor: pointer; position: relative; }
.tx-heatmap td:hover { box-shadow: inset 0 0 0 1px var(--tx-line-3); }
.tx-heatmap td[data-selected] { box-shadow: inset 0 0 0 1.5px var(--tx-ember); }
.tx-heatmap td[data-best]::after { content: ''; position: absolute; top: 4px; right: 4px; width: 4px; height: 4px; border-radius: 50%; background: var(--tx-text); }
.tx-heatmap td[data-state="failed"] { color: var(--tx-ember-text); background: var(--tx-ember-select); font-family: var(--tx-font); font-size: var(--tx-size-xs); }
.tx-heatmap td[data-state="notrun"] { color: var(--tx-text-4); background: var(--tx-white-02); }
.tx-failure { flex: none; margin: 12px 14px; border: 1px solid var(--tx-ember-line); border-radius: var(--tx-radius-sm); background: var(--tx-ember-select); overflow: hidden; }
.tx-failure[data-tone="watch"] { border-color: var(--tx-watch-line); background: var(--tx-watch-wash); }
.tx-failure > div:first-child { display: flex; align-items: flex-start; gap: 10px; padding: 11px 12px 8px; }
.tx-failure > div:first-child > .tx-icon { width: 15px; height: 15px; color: var(--tx-ember); margin-top: 1px; }
.tx-failure[data-tone="watch"] > div:first-child > .tx-icon { color: var(--tx-watch); }
.tx-failure h3 { margin: 0; font-size: var(--tx-size); font-weight: var(--tx-weight-strong); }
.tx-failure .tx-failmessage { margin-top: 3px; font-size: var(--tx-size-sm); color: var(--tx-text-2); white-space: pre-wrap; }
.tx-failure .tx-failmessage[data-code] { font-family: var(--tx-font-mono); font-size: 12px; color: var(--tx-text); }
.tx-failure .tx-failcontext { margin-top: 4px; font-size: var(--tx-size-sm); color: var(--tx-text-2); }
.tx-failure .tx-failcontext button { color: var(--tx-ember-text); font-family: var(--tx-font-mono); background: none; border: 0; padding: 0; cursor: pointer; margin-right: 10px; }
.tx-failure .tx-failcontext button:hover { text-decoration: underline; }
.tx-failure .tx-failactions { display: flex; gap: 6px; padding: 0 12px 11px 37px; }
.tx-failure pre { margin: 0; padding: 10px 12px 12px 37px; border-top: 1px solid var(--tx-ember-30); font: 12px/1.6 var(--tx-font-mono); color: var(--tx-text-3); white-space: pre-wrap; background: var(--tx-black-20); }
.tx-failure pre b { font-weight: var(--tx-weight); color: var(--tx-text); }
.tx-checklist[data-layout="table"] > header { height: 20px; padding: 0; margin-bottom: 2px; gap: 8px; align-items: baseline; }
.tx-checklist[data-layout="table"] > header :is(.tx-ckscope, .tx-ckwatch, .tx-ckfix) { font-size: var(--tx-size-xs); }
.tx-cktables { display: grid; grid-template-columns: minmax(0, 1fr); }
.tx-cktable { width: 100%; border-collapse: collapse; table-layout: fixed; font-size: var(--tx-size-sm); }
.tx-cktable th { height: 24px; padding: 0 6px; font-weight: var(--tx-weight); font-size: var(--tx-size-xs); color: var(--tx-text-3); text-align: right; border-bottom: 1px solid var(--tx-line-2); white-space: nowrap; }
.tx-cktable td { height: 23px; padding: 0 6px; text-align: right; border-bottom: 1px solid var(--tx-line); white-space: nowrap; overflow: hidden; text-overflow: ellipsis; font-family: var(--tx-font-mono); font-variant-numeric: tabular-nums; letter-spacing: var(--tx-tracking-tight); }
.tx-cktable [data-align="left"] { text-align: left; font-family: var(--tx-font); letter-spacing: var(--tx-tracking); }
.tx-cktable + .tx-cktable thead { display: none; }
.tx-cktable td.tx-ckname { color: var(--tx-text-2); }
.tx-cktable td.tx-ckneed { color: var(--tx-text-3); }
.tx-cktable tr:is([data-state="watch"], [data-state="fix"]) :is(td.tx-ckname, td.tx-ckvalue) { color: var(--tx-text); }
.tx-cktable td.tx-ckresult { font-size: var(--tx-size-xs); }
.tx-cktable td.tx-ckresult span { display: inline-flex; align-items: center; gap: 5px; color: var(--tx-text-3); }
.tx-cktable td.tx-ckresult .tx-icon { width: 12px; height: 12px; }
.tx-cktable tr[data-state="watch"] td.tx-ckresult span { gap: 6px; color: var(--tx-watch); }
.tx-cktable tr[data-state="fix"] td.tx-ckresult span { gap: 6px; color: var(--tx-ember-text); }
.tx-cktable td.tx-ckresult i { width: 6px; height: 6px; border-radius: 50%; background: currentColor; }
.tx-cktable tr[data-state="notrun"] td.tx-ckresult span { color: var(--tx-text-4); }
.tx-cktable tr[data-go] { cursor: pointer; }
.tx-cktable tr[data-go]:hover td { background: var(--tx-white-02); }
@container (min-width: 640px) and (max-width: 1000px) {
  .tx-cktables { grid-template-columns: repeat(2, minmax(0, 1fr)); column-gap: 20px; align-items: start; }
  .tx-cktable + .tx-cktable thead { display: table-header-group; }
}
.tx-regionhead { display: flex; align-items: center; gap: 10px; min-height: 34px; padding: 0 10px 0 14px; font-size: var(--tx-size-sm); white-space: nowrap; }
.tx-regionhead h3 { margin: 0; font-size: var(--tx-size-sm); font-weight: var(--tx-weight-strong); }
.tx-regionhead .tx-regionsub { font-size: var(--tx-size-xs); color: var(--tx-text-3); overflow: hidden; text-overflow: ellipsis; min-width: 0; }
.tx-regionhead .tx-spacer { flex: 1; }
.tx-regionhead .tx-regionkeys { display: inline-flex; align-items: center; gap: 12px; min-width: 0; }
/* a key longer than the head wraps onto a second line inside it instead of running over the next region */
.tx-regionhead .tx-regionkeys:empty { display: none; }
`;
