/* TALYX design system stylesheet.
   Kept as a plain string (not an imported .css file) because the host bundle
   that ships this module to browsers is built by esbuild, which has no CSS
   pipeline. The same string is injected into the document and adopted into
   module shadow roots, so every surface gets identical rules.

   The look: near-black solid surfaces for data, hairline white-alpha lines, quiet
   flat pill buttons, dark fields with values and units inside, one orange for the
   main action, selection, focus and what must be handled; glass only on floating
   layers. Sizes come from the density tokens, so a container with
   `data-tx-density` changes every part inside it at once.

   `data-force="hover|active|focus"` pins an interaction state on a control, so the
   sample page can show every state side by side without a pointer. */
import { talyxTokensCss } from "./tokens.css";
import { researchCss } from "./research.css";

const baseCss = `
.tx-plot { width: 100%; min-width: 0; min-height: 180px; flex: none; }
.tx-root {
  color-scheme: dark;
  color: var(--tx-text);
  font-family: var(--tx-font);
  font-size: var(--tx-size);
  font-weight: var(--tx-weight);
  line-height: var(--tx-leading);
  letter-spacing: var(--tx-tracking);
  -webkit-font-smoothing: antialiased;
}
[data-tx-density] { font-size: var(--tx-size); line-height: var(--tx-leading); }
.tx-root,.tx-root *,.tx-root *::before,.tx-root *::after { box-sizing: border-box; }
/* No resident scrollbar anywhere: regions scroll by wheel, touch and keys, and a thin dark
   hint (inject.ts) shows only while scrolling, over the content, taking no room. */
:root, :root *, .tx-root, .tx-root * { scrollbar-width: none !important; }
:root ::-webkit-scrollbar, .tx-root ::-webkit-scrollbar { display: none; width: 0; height: 0; }
.tx-scrollhint { position: fixed; left: 0; top: 0; z-index: 2147483000; pointer-events: none; border-radius: var(--tx-scroll-hint-width); background: var(--tx-scroll-hint); opacity: 0; transition: opacity var(--tx-scroll-hint-fade) var(--tx-ease); }
.tx-scrollhint[data-on] { opacity: 1; transition-duration: 60ms; }
.tx-root .sr-only { position: absolute; width: 1px; height: 1px; padding: 0; margin: -1px; overflow: hidden; clip-path: inset(50%); white-space: nowrap; border: 0; }
.tx-root :where(button,input,select,textarea) { font: inherit; color: inherit; letter-spacing: inherit; }
.tx-icon { width: var(--tx-icon); height: var(--tx-icon); flex: none; display: block; stroke-width: var(--tx-icon-stroke); }
.tx-root [hidden] { display: none !important; }
.tx-root ::selection { background: var(--tx-ember-wash); }
.tx-root :focus-visible, [data-force~="focus"] { outline: var(--tx-space-half) solid var(--tx-ember); outline-offset: 1px; }
.tx-root h1,.tx-root h2,.tx-root h3,.tx-root h4,.tx-root p,.tx-root dl,.tx-root dd { margin: 0; }

.tx-num { font-family: var(--tx-font-mono); font-variant-numeric: tabular-nums; letter-spacing: var(--tx-tracking-tight); }
.tx-up { color: var(--tx-up); }
.tx-down { color: var(--tx-down); }
.tx-muted { color: var(--tx-text-3); }
.tx-dim { color: var(--tx-text-4); }
.tx-strong { font-weight: var(--tx-weight-strong); }
.tx-row { display: flex; align-items: center; gap: var(--tx-space-2); flex-wrap: wrap; }
.tx-col { display: flex; flex-direction: column; gap: var(--tx-space-2-half); }
.tx-spacer { flex: 1; }
.tx-nowrap { flex-wrap: nowrap; }
.tx-section-error { margin-bottom: var(--tx-space-3) !important; }
.tx-kbd { display: inline-flex; align-items: center; height: 16px; padding: 0 4px; border: var(--tx-hairline) solid var(--tx-line-2); border-radius: var(--tx-radius-xs); font-family: var(--tx-font-mono); font-size: var(--tx-size-2xs); line-height: 1; color: var(--tx-text-3); white-space: nowrap; }

/* Glass for overlays and the top bar */
.tx-glass {
  background:
    linear-gradient(180deg, var(--tx-glass-sheen), var(--tx-clear) 40%),
    var(--tx-glass-fill);
  -webkit-backdrop-filter: blur(var(--tx-glass-blur)) saturate(var(--tx-glass-saturate));
  backdrop-filter: blur(var(--tx-glass-blur)) saturate(var(--tx-glass-saturate));
  border: var(--tx-hairline) solid var(--tx-glass-edge);
  box-shadow: inset 0 var(--tx-hairline) 0 var(--tx-glass-highlight), var(--tx-glass-shadow);
}

/* ── Buttons: flat pills with orange reserved for the primary action ───────────────── */
.tx-btn {
  height: var(--tx-button); padding: 0 var(--tx-space-3); border-radius: var(--tx-radius-pill);
  display: inline-flex; align-items: center; justify-content: center; gap: var(--tx-space-1-half); flex: none;
  font-size: var(--tx-size-button); font-weight: var(--tx-weight-medium); line-height: 1; white-space: nowrap;
  cursor: pointer; color: var(--tx-text);
  background: var(--tx-white-04);
  border: var(--tx-hairline) solid var(--tx-line-2);
  transition: background var(--tx-fast) var(--tx-ease), border-color var(--tx-fast) var(--tx-ease), color var(--tx-fast) var(--tx-ease);
}
.tx-btn:hover, .tx-btn[data-force~="hover"] { background: var(--tx-white-07); border-color: var(--tx-line-3); }
.tx-btn:active, .tx-btn[data-force~="active"] { background: var(--tx-white-10); }
.tx-btn:disabled { opacity: .4; cursor: default; pointer-events: none; }
.tx-btn[aria-busy="true"] { opacity: 1; color: var(--tx-text-2); }
.tx-btn[data-size="sm"] { height: var(--tx-button-sm); padding: 0 var(--tx-space-2-half); font-size: var(--tx-size-sm); gap: var(--tx-space-1-quarter); }
.tx-btn svg { width: var(--tx-icon-sm); height: var(--tx-icon-sm); flex: none; }
.tx-btn[data-size="sm"] svg { width: calc(var(--tx-icon-sm) - 1px); height: calc(var(--tx-icon-sm) - 1px); }
.tx-btn > .tx-spin { width: 11px; height: 11px; }
.tx-btn > .tx-spin ~ svg { display: none; }
.tx-btn > span:not(.tx-spin) { min-width: 0; overflow: hidden; text-overflow: ellipsis; }
.tx-btn[data-variant="primary"] {
  color: var(--tx-ember-text);
  background: linear-gradient(180deg, var(--tx-ember-20), var(--tx-ember-14));
  border-color: var(--tx-ember-40);
}
.tx-btn[data-variant="primary"]:hover, .tx-btn[data-variant="primary"][data-force~="hover"] { background: linear-gradient(180deg, var(--tx-ember-30), var(--tx-ember-20)); border-color: var(--tx-ember-55); }
.tx-btn[data-variant="primary"]:active, .tx-btn[data-variant="primary"][data-force~="active"] { background: var(--tx-ember-30); }
.tx-btn[data-variant="primary"][aria-busy="true"] { color: var(--tx-ember-text); }
.tx-btn[data-variant="primary"] .tx-spin { border-color: var(--tx-ember-30); border-top-color: var(--tx-ember-text); }
.tx-btn[data-variant="quiet"] { background: transparent; border-color: transparent; color: var(--tx-text-2); }
.tx-btn[data-variant="quiet"]:hover, .tx-btn[data-variant="quiet"][data-force~="hover"] { background: var(--tx-hover); color: var(--tx-text); }
.tx-btn[data-variant="quiet"]:active, .tx-btn[data-variant="quiet"][data-force~="active"] { background: var(--tx-press); }
/* Toolbar text button: square-ish, no frame, sits flush in a bar. */
.tx-btn[data-variant="tool"] { padding: 0 var(--tx-space-2); border-radius: var(--tx-radius-xs); background: transparent; border-color: transparent; color: var(--tx-text-2); gap: var(--tx-space-1-quarter); }
.tx-btn[data-variant="tool"]:hover, .tx-btn[data-variant="tool"][data-force~="hover"] { background: var(--tx-hover); color: var(--tx-text); border-color: transparent; }
.tx-btn[data-variant="tool"]:active, .tx-btn[data-variant="tool"][data-force~="active"] { background: var(--tx-press); }
.tx-btn[data-variant="tool"][aria-pressed="true"], .tx-btn[data-variant="tool"][aria-expanded="true"], .tx-btn[data-variant="tool"][data-state="open"] { background: var(--tx-chosen); color: var(--tx-text); }
.tx-btn[data-variant="tool"] svg { width: var(--tx-icon); height: var(--tx-icon); }
/* The one main action of a compact toolbar: the tool shape, filled orange. */
.tx-btn[data-variant="tool-primary"] { padding: 0 var(--tx-space-2-half); border-radius: var(--tx-radius-xs); gap: var(--tx-space-1-quarter); color: var(--tx-ember-text); background: var(--tx-ember-14); border-color: var(--tx-clear); }
.tx-btn[data-variant="tool-primary"]:hover, .tx-btn[data-variant="tool-primary"][data-force~="hover"] { background: var(--tx-ember-20); border-color: var(--tx-clear); }
.tx-btn[data-variant="tool-primary"]:active, .tx-btn[data-variant="tool-primary"][data-force~="active"] { background: var(--tx-ember-30); }
.tx-btn[data-variant="tool-primary"][aria-busy="true"] { color: var(--tx-ember-text); }
.tx-btn[data-variant="tool-primary"] svg { width: var(--tx-icon); height: var(--tx-icon); }
/* Row: a whole clickable line of a list. Flush and quiet; the content is the caller's (one line, or a title over a detail).
   The base rule has the specificity of .tx-btn alone, so the caller's class can set its padding, height and direction. */
.tx-btn:where([data-variant="row"]) {
  width: 100%; height: auto; min-height: var(--tx-row); justify-content: flex-start; gap: var(--tx-space-2); padding: var(--tx-space-1-half) var(--tx-space-2);
  border: 0; border-radius: var(--tx-radius-sm); background: transparent; color: var(--tx-text); text-align: left;
  font-size: var(--tx-size-sm); font-weight: var(--tx-weight); line-height: var(--tx-leading); white-space: normal;
}
.tx-btn[data-variant="row"]:hover, .tx-btn[data-variant="row"][data-force~="hover"] { background: var(--tx-hover); border-color: transparent; }
.tx-btn[data-variant="row"]:active, .tx-btn[data-variant="row"][data-force~="active"] { background: var(--tx-press); }
.tx-btn[data-variant="row"]:is([aria-current]:not([aria-current="false"]), [aria-selected="true"], [aria-pressed="true"], [aria-checked="true"], [data-state="open"]) { background: var(--tx-chosen); }
/* A row with nothing behind it (a step without detail) reads as a row, not as a disabled control. */
.tx-btn[data-variant="row"]:disabled { opacity: 1; }

.tx-icon-btn {
  width: var(--tx-button); height: var(--tx-button); flex: none; display: inline-grid; place-items: center; padding: 0;
  border: var(--tx-hairline) solid transparent; border-radius: var(--tx-radius-pill); background: transparent; color: var(--tx-text-3);
  cursor: pointer; transition: background var(--tx-fast) var(--tx-ease), color var(--tx-fast) var(--tx-ease), border-color var(--tx-fast) var(--tx-ease);
}
.tx-icon-btn:hover, .tx-icon-btn[data-force~="hover"] { background: var(--tx-hover); color: var(--tx-text); }
.tx-icon-btn:active, .tx-icon-btn[data-force~="active"] { background: var(--tx-press); }
.tx-icon-btn:disabled { opacity: .34; pointer-events: none; }
.tx-icon-btn svg { width: calc(var(--tx-icon) - 1px); height: calc(var(--tx-icon) - 1px); }
.tx-icon-btn[data-size="sm"] { width: var(--tx-button-sm); height: var(--tx-button-sm); }
.tx-icon-btn[data-size="sm"] svg { width: calc(var(--tx-icon-sm) - 1px); height: calc(var(--tx-icon-sm) - 1px); }
.tx-icon-btn[aria-pressed="true"], .tx-icon-btn[data-state="on"], .tx-icon-btn[data-state="open"], .tx-icon-btn[data-tone="active"] { background: var(--tx-chosen); color: var(--tx-text); }
/* Outline: an icon action standing among buttons. */
.tx-icon-btn[data-variant="outline"] { background: var(--tx-white-04); border-color: var(--tx-line-2); color: var(--tx-text-2); }
.tx-icon-btn[data-variant="outline"]:hover, .tx-icon-btn[data-variant="outline"][data-force~="hover"] { background: var(--tx-white-07); border-color: var(--tx-line-3); color: var(--tx-text); }
/* Tool: a frameless square in a toolbar or drawing rail; the chosen tool is the selection, so it turns orange. */
.tx-icon-btn[data-variant="tool"] { border-radius: var(--tx-radius-xs); color: var(--tx-text-2); }
.tx-icon-btn[data-variant="tool"] svg { width: var(--tx-icon); height: var(--tx-icon); }
.tx-icon-btn[data-variant="tool"]:hover, .tx-icon-btn[data-variant="tool"][data-force~="hover"] { background: var(--tx-hover); color: var(--tx-text); }
.tx-icon-btn[data-variant="tool"][aria-pressed="true"], .tx-icon-btn[data-variant="tool"][data-state="on"], .tx-icon-btn[data-variant="tool"][data-tone="active"] { background: var(--tx-ember-select); color: var(--tx-ember); }
.tx-icon-btn[data-variant="tool"][data-state="open"], .tx-icon-btn[data-variant="tool"][aria-expanded="true"] { background: var(--tx-chosen); color: var(--tx-text); }

/* ── Inputs: dark surfaces, left-aligned values and inline units ─────────────────────── */
.tx-field { display: flex; flex-direction: column; gap: var(--tx-space-1-quarter); min-width: 0; }
.tx-field > label { font-size: var(--tx-size-sm); color: var(--tx-text-2); font-weight: var(--tx-weight); }
.tx-input, .tx-select {
  width: 100%; height: var(--tx-control); padding: 0 var(--tx-space-2); border-radius: var(--tx-radius-sm);
  font-size: var(--tx-size-sm); color: var(--tx-text); background-color: var(--tx-field-fill);
  border: var(--tx-hairline) solid var(--tx-line-2); outline: none; min-width: 0;
  transition: border-color var(--tx-fast) var(--tx-ease), background-color var(--tx-fast) var(--tx-ease);
}
.tx-input::placeholder { color: var(--tx-text-4); }
.tx-textarea { height: auto; padding: var(--tx-space-1-half) var(--tx-space-2); line-height: var(--tx-leading); resize: vertical; }
.tx-input:hover, .tx-select:hover, .tx-input[data-force~="hover"], .tx-select[data-force~="hover"] { border-color: var(--tx-line-3); }
.tx-input:focus, .tx-select:focus, .tx-input[data-force~="focus"], .tx-select[data-force~="focus"] { border-color: var(--tx-ember-40); background-color: var(--tx-black-35); outline: none; }
.tx-input:focus-visible, .tx-select:focus-visible { outline: none; }
.tx-select:focus-visible { border-color: var(--tx-ember); }
.tx-input[aria-invalid="true"], .tx-select[aria-invalid="true"] { border-color: var(--tx-ember-line); background-color: var(--tx-ember-select); }
.tx-input[data-numeric="true"] { text-align: left; font-family: var(--tx-font-mono); font-variant-numeric: tabular-nums; letter-spacing: var(--tx-tracking-tight); }
.tx-input:disabled, .tx-select:disabled { opacity: .45; background-color: transparent; cursor: default; }
.tx-input[readonly] { background-color: transparent; border-style: dashed; border-color: var(--tx-line-2); }
.tx-multiselect { z-index: 52; padding: var(--tx-space-2); width: calc(var(--tx-menu-width) + var(--tx-control)); border-radius: var(--tx-radius-menu); }
.tx-multiselect-chevron { transform: rotate(90deg); }
.tx-multiselect-options { display: flex; flex-direction: column; gap: var(--tx-space-1-half); max-height: calc(var(--tx-control) * 8); overflow: auto; padding: var(--tx-space-2) var(--tx-space-1); }
.tx-select {
  color-scheme: dark;
  appearance: none; padding-right: var(--tx-space-6); cursor: pointer;
  background-image: var(--tx-select-arrow);
  background-position: right var(--tx-space-1-half) center;
  background-size: 12px;
  background-repeat: no-repeat;
}
.tx-select option, .tx-select optgroup { color: var(--tx-text); background: var(--tx-raised); }
.tx-hint { font-size: var(--tx-size-xs); color: var(--tx-text-3); }
.tx-error { font-size: var(--tx-size-xs); color: var(--tx-ember-text); }
.tx-affix { position: relative; display: flex; align-items: center; min-width: 0; }
.tx-affix .tx-input { padding-right: var(--tx-affix-padding); }
.tx-affix > span { position: absolute; right: var(--tx-space-2); font-family: var(--tx-font); font-size: var(--tx-size-xs); color: var(--tx-text-3); pointer-events: none; white-space: nowrap; }

.tx-search { position: relative; display: flex; align-items: center; min-width: 0; }
.tx-search > svg { position: absolute; left: var(--tx-space-2); width: var(--tx-icon-sm); height: var(--tx-icon-sm); color: var(--tx-text-3); pointer-events: none; }
.tx-search .tx-input { padding-left: calc(var(--tx-space-2) + var(--tx-icon-sm) + var(--tx-space-1-half)); }
.tx-search .tx-input::-webkit-search-cancel-button { appearance: none; }
.tx-search-clear { position: absolute; right: var(--tx-space-half); }

/* ── Selection ──────────────────────────────────────────────── */
.tx-check { display: inline-flex; align-items: center; gap: var(--tx-space-2); min-height: calc(var(--tx-control) - 4px); color: var(--tx-text-2); cursor: pointer; font-size: var(--tx-size-sm); }
.tx-check:has([data-state="checked"]), .tx-check:has([data-state="indeterminate"]) { color: var(--tx-text); }
.tx-check:hover .tx-check-box, .tx-check-box[data-force~="hover"] { border-color: var(--tx-text-3); }
.tx-check:has(:disabled) { opacity: .4; cursor: default; }
.tx-check-box {
  appearance: none; width: 14px; height: 14px; margin: 0; padding: 0; flex: none; cursor: pointer;
  border-radius: var(--tx-check-radius); border: var(--tx-hairline) solid var(--tx-line-3); background: transparent;
  display: grid; place-items: center; transition: background var(--tx-fast) var(--tx-ease), border-color var(--tx-fast) var(--tx-ease);
}
/* Radix renders the checkbox as a button carrying data-state, not a native input. */
.tx-check-box[data-state="checked"], .tx-check-box[data-state="indeterminate"] { background: var(--tx-ember); border-color: var(--tx-ember); }
.tx-check-box[data-state="checked"]::after { content: ''; width: 7px; height: 4px; border-left: var(--tx-check-stroke) solid var(--tx-ember-ink); border-bottom: var(--tx-check-stroke) solid var(--tx-ember-ink); transform: rotate(-45deg) translate(var(--tx-half-pixel),calc(-1 * var(--tx-hairline))); }
.tx-check-box[data-state="indeterminate"]::after { content: ''; width: 7px; height: var(--tx-check-stroke); background: var(--tx-ember-ink); }
.tx-check-box > span { display: none; }
.tx-check-box:disabled { cursor: default; opacity: .4; }

.tx-switch {
  width: var(--tx-switch-width); height: var(--tx-switch-height); flex: none; padding: 0; border-radius: var(--tx-radius-pill); cursor: pointer;
  border: 0; background: var(--tx-white-10); position: relative;
  transition: background var(--tx-switch-time) var(--tx-ease);
}
.tx-switch:hover, .tx-switch[data-force~="hover"] { background: var(--tx-white-14); }
.tx-switch::after {
  content: ''; position: absolute; top: 2px; left: 2px; width: calc(var(--tx-switch-height) - 4px); height: calc(var(--tx-switch-height) - 4px); border-radius: 50%;
  background: var(--tx-text-2); transition: transform var(--tx-thumb-time) var(--tx-ease), background var(--tx-switch-time) var(--tx-ease);
}
.tx-switch[data-state="checked"] { background: var(--tx-ember); }
.tx-switch[data-state="checked"]::after { transform: translateX(calc(var(--tx-switch-width) - var(--tx-switch-height))); background: var(--tx-ember-ink); }
.tx-switch:disabled { opacity: .4; cursor: default; }
.tx-switch-label { display: inline-flex; align-items: center; gap: var(--tx-space-2); font-size: var(--tx-size-sm); color: var(--tx-text-2); }
.tx-switch-label:has([data-state="checked"]) { color: var(--tx-text); }

.tx-seg { display: inline-flex; width: fit-content; max-width: 100%; padding: 2px; gap: 2px; border-radius: var(--tx-radius-pill); background: none; border: var(--tx-hairline) solid var(--tx-line-2); flex: none; }
.tx-seg button {
  height: calc(var(--tx-button) - 6px); padding: 0 var(--tx-space-3); border: 0; border-radius: var(--tx-radius-pill); background: transparent; color: var(--tx-text-3);
  font-size: var(--tx-size-sm); font-weight: var(--tx-weight-medium); cursor: pointer; white-space: nowrap; min-width: 0; overflow: hidden; text-overflow: ellipsis;
  transition: background var(--tx-fast) var(--tx-ease), color var(--tx-fast) var(--tx-ease);
}
.tx-seg button:hover, .tx-seg button[data-force~="hover"] { color: var(--tx-text-2); }
.tx-seg button[aria-pressed="true"] { color: var(--tx-text); background: var(--tx-chosen); }
.tx-seg button:disabled { opacity: .4; cursor: default; }
.tx-seg[data-size="sm"] button { height: calc(var(--tx-button-sm) - 6px); padding: 0 var(--tx-space-2); font-size: var(--tx-size-xs); }
/* Tool: flush text choices in a toolbar, such as timeframes. */
.tx-seg[data-variant="tool"] { border: 0; padding: 0; gap: 1px; border-radius: 0; }
.tx-seg[data-variant="tool"] button { height: var(--tx-button); padding: 0 var(--tx-space-2); border-radius: var(--tx-radius-xs); color: var(--tx-text-2); font-family: var(--tx-font); }
.tx-seg[data-variant="tool"] button:hover, .tx-seg[data-variant="tool"] button[data-force~="hover"] { background: var(--tx-hover); color: var(--tx-text); }
.tx-seg[data-variant="tool"] button[aria-pressed="true"] { background: transparent; color: var(--tx-ember); }
.tx-tab-mark { display: inline-block; width: var(--tx-space-1-half); height: var(--tx-space-1-half); margin-left: var(--tx-space-1); border-radius: 50%; background: var(--tx-ember); vertical-align: middle; }

.tx-tabs { display: flex; gap: var(--tx-space-half); border-bottom: var(--tx-hairline) solid var(--tx-line); min-width: 0; overflow-x: auto; }
.tx-tabs [role="tab"] {
  height: var(--tx-tabs-height); padding: 0 var(--tx-space-2-half); border: 0; background: none; color: var(--tx-text-3); font-size: var(--tx-size-sm);
  font-weight: var(--tx-weight-medium); cursor: pointer; position: relative; white-space: nowrap; flex: none;
  display: inline-flex; align-items: center; gap: var(--tx-space-1-half);
}
.tx-tabs [role="tab"]:hover, .tx-tabs [role="tab"][data-force~="hover"] { color: var(--tx-text-2); }
.tx-tabs [role="tab"][data-state="active"] { color: var(--tx-text); }
.tx-tabs [role="tab"][data-state="active"]::after { content: ''; position: absolute; left: var(--tx-space-2-half); right: var(--tx-space-2-half); bottom: 0; height: var(--tx-stroke); background: var(--tx-ember); }
.tx-tabs [role="tab"]:disabled { opacity: .4; cursor: default; }
/* Closable tabs: open documents. The close button shows on the current tab and under the pointer; an unsaved document
   shows a dot in its place until then. */
.tx-tab { position: relative; display: inline-flex; align-items: center; flex: none; }
.tx-tab > [role="tab"] { padding-right: var(--tx-space-1); }
.tx-tab-end { display: grid; place-items: center; width: var(--tx-button-sm); height: var(--tx-button-sm); margin-right: var(--tx-space-1); }
.tx-tab-end > * { grid-area: 1 / 1; }
.tx-tab-close { visibility: hidden; }
.tx-tab:is(:hover, :focus-within) .tx-tab-close, .tx-tab[data-state="active"]:not([data-dirty]) .tx-tab-close { visibility: visible; }
.tx-tab-dirty { width: var(--tx-space-1-half); height: var(--tx-space-1-half); border-radius: 50%; background: var(--tx-text-2); }
.tx-tab:is(:hover, :focus-within) .tx-tab-dirty { visibility: hidden; }
.tx-tab-count { font-family: var(--tx-font-mono); font-size: var(--tx-size-xs); font-weight: var(--tx-weight); color: var(--tx-text-3); letter-spacing: var(--tx-tracking-tight); }
.tx-tabs [role="tab"][data-state="active"] .tx-tab-count { color: var(--tx-text-2); }

/* ── Status: a label and dot with colors independent of rising and falling values ─────────────────── */
.tx-status { display: inline-flex; align-items: center; gap: 7px; font-size: var(--tx-size-sm); font-weight: var(--tx-weight); white-space: nowrap; color: var(--tx-state-ok); }
.tx-status::before { content: ''; flex: none; width: 6px; height: 6px; border-radius: 50%; background: var(--tx-state-ok-mark); }
.tx-status[data-tone="off"] { color: var(--tx-state-off); }
.tx-status[data-tone="off"]::before { background: none; box-shadow: inset 0 0 0 1px var(--tx-state-off-mark); }
.tx-status[data-tone="busy"] { color: var(--tx-text); }
.tx-status[data-tone="busy"]::before { box-sizing: border-box; width: 10px; height: 10px; background: none; border: var(--tx-stroke) solid var(--tx-white-14); border-top-color: var(--tx-text); animation: tx-spin var(--tx-spin-time) linear infinite; }
.tx-status[data-tone="review"] { color: var(--tx-state-watch); }
.tx-status[data-tone="review"]::before { background: var(--tx-state-watch); }
.tx-status[data-tone="failed"] { color: var(--tx-state-fix); }
.tx-status[data-tone="failed"]::before { background: var(--tx-state-fix-mark); }
@keyframes tx-spin { to { transform: rotate(360deg); } }

.tx-progress { height: 3px; border-radius: 2px; background: var(--tx-line-2); overflow: hidden; }
.tx-progress > i { display: block; height: 100%; background: var(--tx-text-2); border-radius: 2px; transition: width var(--tx-slow) var(--tx-ease); }
/* Unknown length: a travelling bar rather than a fake percentage. */
.tx-progress[data-indeterminate] > i { width: 35% !important; animation: tx-slide var(--tx-progress-time) var(--tx-ease) infinite; }
@keyframes tx-slide { from { transform: translateX(-100%); } to { transform: translateX(300%); } }
.tx-progress-label { display: flex; align-items: center; justify-content: space-between; gap: var(--tx-space-3); font-size: var(--tx-size-sm); color: var(--tx-text-2); margin-bottom: var(--tx-space-1-half); }
.tx-progress-label .tx-num { color: var(--tx-text); }

.tx-skeleton { height: var(--tx-space-2-half); border-radius: var(--tx-radius-xs); background: linear-gradient(100deg, var(--tx-white-04) 30%, var(--tx-white-07) 50%, var(--tx-white-04) 70%); background-size: 220% 100%; animation: tx-shimmer var(--tx-shimmer-time) linear infinite; }
@keyframes tx-shimmer { from { background-position: 120% 0; } to { background-position: -120% 0; } }

/* ── Glass overlays ────────────────────────────────────────── */
.tx-color-trigger { padding: 0 var(--tx-space-1-half) 0 var(--tx-space-1); gap: var(--tx-space-1); }
.tx-color-trigger svg { width: var(--tx-size-xs); transform: rotate(90deg); }
.tx-color-swatch { width: 14px; height: 14px; border: var(--tx-hairline) solid var(--tx-line-3); border-radius: var(--tx-radius-xs); display: block; background-color: var(--tx-user-color); }
.tx-color-picker { z-index: 52; width: calc(var(--tx-control) * 8); padding: var(--tx-space-3); border-radius: var(--tx-radius-menu); display: flex; flex-direction: column; gap: var(--tx-space-2-half); }
.tx-popover { z-index: 52; padding: var(--tx-space-3) var(--tx-pad); border-radius: var(--tx-radius-menu); font-size: var(--tx-size-sm); max-height: calc(var(--tx-control) * 18); max-width: min(calc(var(--tx-control) * 14), var(--radix-popover-content-available-width)); overflow: auto; }
.tx-dialog[data-size="compact"],.tx-popover[data-size="compact"] { --tx-overlay-width: var(--tx-dialog-compact-width); }
.tx-dialog[data-size="standard"],.tx-popover[data-size="standard"] { --tx-overlay-width: var(--tx-dialog-standard-width); }
.tx-dialog[data-size="wide"],.tx-popover[data-size="wide"] { --tx-overlay-width: var(--tx-dialog-wide-width); }
.tx-popover[data-size] { width: min(var(--tx-overlay-width), calc(100vw - var(--tx-space-6) * 2)); max-width: var(--radix-popover-content-available-width); max-height: min(var(--tx-dialog-max-height), var(--radix-popover-content-available-height), calc(100dvh - var(--tx-space-6) * 2)); }
.tx-color-palette { display: grid; grid-template-columns: repeat(8, 1fr); gap: var(--tx-space-1); }
.tx-color-choice { padding: 0; height: var(--tx-button-sm); min-width: 0; border-radius: var(--tx-radius-xs); }
.tx-color-choice .tx-color-swatch { width: 16px; height: 16px; }
.tx-color-choice[aria-pressed="true"] { border-color: var(--tx-ember); background: var(--tx-ember-select); }
/* Under an open menu: a press outside only closes the menu (see MenuCatcher). */
.tx-menu-catcher { position: fixed; inset: 0; z-index: 51; pointer-events: auto; }
.tx-menu {
  min-width: var(--tx-menu-width); padding: var(--tx-space-1-quarter); border-radius: var(--tx-radius-menu); z-index: 52;
  max-height: var(--radix-dropdown-menu-content-available-height, none); overflow: auto;
  transform-origin: var(--radix-dropdown-menu-content-transform-origin, center);
}
.tx-context-menu-anchor { position: fixed; left: var(--tx-context-x); top: var(--tx-context-y); width: 1px; height: 1px; pointer-events: none; opacity: 0; }
.tx-menu-item {
  display: flex; align-items: center; gap: var(--tx-space-2); min-height: calc(var(--tx-control) + 2px); padding: 0 var(--tx-space-2); border-radius: var(--tx-radius-sm);
  color: var(--tx-text); font-size: var(--tx-size-sm); cursor: default; outline: none; user-select: none;
}
.tx-menu-item > span:first-of-type, .tx-menu-text > span:first-child { min-width: 0; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.tx-menu-item svg { width: var(--tx-icon-sm); height: var(--tx-icon-sm); color: var(--tx-text-3); flex: none; }
.tx-menu-item[data-highlighted], .tx-menu-item[data-force~="hover"] { background: var(--tx-white-07); }
.tx-menu button.tx-menu-item { width: 100%; border: 0; background: transparent; text-align: left; font: inherit; font-size: var(--tx-size-sm); color: var(--tx-text); cursor: pointer; }
.tx-menu button.tx-menu-item:hover,.tx-menu button.tx-menu-item:focus-visible { background: var(--tx-white-07); outline: none; }
.tx-menu-item[data-highlighted] svg, .tx-menu-item[data-force~="hover"] svg { color: var(--tx-text-2); }
.tx-menu-item[data-disabled] { color: var(--tx-text-4); }
.tx-menu-item[data-disabled] svg { color: var(--tx-text-4); }
.tx-menu-item kbd { margin-left: auto; padding-left: var(--tx-space-3); font-family: var(--tx-font-mono); font-size: var(--tx-size-xs); color: var(--tx-text-4); }
.tx-menu-item .tx-menu-check, .tx-menu-item[data-highlighted] .tx-menu-check, .tx-menu-item[data-force~="hover"] .tx-menu-check { margin-left: auto; color: var(--tx-ember); }
.tx-menu-item kbd + .tx-menu-check, .tx-menu-hint + .tx-menu-check { margin-left: var(--tx-space-2); }
.tx-menu-item[data-tone="danger"] { color: var(--tx-ember-text); }
.tx-menu-item[data-tone="danger"] svg { color: var(--tx-ember); }
.tx-menu:has(.tx-menu-detail) { max-width: calc(var(--tx-menu-width) * 1.5); }
.tx-menu-item:has(.tx-menu-detail) { align-items: flex-start; padding-top: var(--tx-space-1-half); padding-bottom: var(--tx-space-1-half); }
.tx-menu-item:has(.tx-menu-detail) svg { margin-top: 1px; }
.tx-menu-text { flex: 1; min-width: 0; display: flex; flex-direction: column; gap: 1px; }
.tx-menu-detail { color: var(--tx-text-3); font-size: var(--tx-size-xs); line-height: var(--tx-leading); white-space: normal; }
.tx-menu-hint { margin-left: auto; padding-left: var(--tx-space-3); color: var(--tx-text-3); font-family: var(--tx-font-mono); font-size: var(--tx-size-xs); white-space: nowrap; }
.tx-menu-item[data-tone="warn"] > span:first-of-type, .tx-menu-item[data-tone="warn"] .tx-menu-text > span:first-child { color: var(--tx-watch); }
.tx-menu-sep { height: var(--tx-hairline); background: var(--tx-white-07); margin: var(--tx-space-1) var(--tx-space-half); }
.tx-menu-label { padding: var(--tx-space-1-half) var(--tx-space-2) var(--tx-space-3-quarter); color: var(--tx-text-3); font-size: var(--tx-size-xs); }

.tx-tip {
  padding: var(--tx-space-1) var(--tx-space-2); border-radius: var(--tx-radius-sm); font-size: var(--tx-size-xs); line-height: var(--tx-leading); color: var(--tx-text); z-index: 53; max-width: 320px;
  background: var(--tx-tooltip-fill); border: var(--tx-hairline) solid var(--tx-line-2); box-shadow: 0 var(--tx-space-2) var(--tx-space-5) var(--tx-black-50);
  -webkit-backdrop-filter: blur(var(--tx-button-blur)); backdrop-filter: blur(var(--tx-button-blur));
}

.tx-toast {
  display: flex; align-items: center; gap: var(--tx-space-2-half); padding: var(--tx-space-2-half) var(--tx-space-2) var(--tx-space-2-half) var(--tx-pad);
  border-radius: var(--tx-radius-menu); min-width: var(--tx-toast-min-width); max-width: var(--tx-toast-max-width);
}
.tx-toast-viewport { position: fixed; right: var(--tx-space-3); bottom: var(--tx-space-3); z-index: 60; display: flex; flex-direction: column; gap: var(--tx-space-2); outline: none; margin: 0; padding: 0; list-style: none; }
.tx-toast-body { flex: 1; min-width: 0; }
.tx-toast-content { display: contents; }
.tx-dialog-head > h2,.tx-sheet-title > h2 { font: inherit; letter-spacing: inherit; }
.tx-toast-title { font-size: var(--tx-size-sm); font-weight: var(--tx-weight-medium); color: var(--tx-text); }
.tx-toast-title[data-tone="failed"] { color: var(--tx-state-fix); }
.tx-toast-detail { font-size: var(--tx-size-xs); color: var(--tx-text-3); overflow-wrap: anywhere; margin-top: 1px; }
/* In-place notice: the toast surface, placed by the caller over what it describes. */
.tx-notice {
  display: flex; align-items: flex-start; gap: var(--tx-space-2); pointer-events: auto;
  padding: var(--tx-space-2-half) var(--tx-space-1-half) var(--tx-space-2-half) var(--tx-pad);
  border-radius: var(--tx-radius-menu); min-width: min(var(--tx-toast-min-width), 100%); max-width: var(--tx-toast-max-width);
  animation: tx-notice-in var(--tx-thumb-time) var(--tx-ease);
}
.tx-notice[data-tone="failed"] { border-color: var(--tx-ember-line); }
.tx-notice .tx-toast-title { line-height: var(--tx-leading); overflow-wrap: anywhere; }
.tx-notice .tx-toast-detail { color: var(--tx-text-2); line-height: var(--tx-leading); }
.tx-notice > .tx-btn { margin-block: calc(-1 * var(--tx-space-half)); }
.tx-notice > .tx-icon-btn { margin-block: calc(-1 * var(--tx-space-half)); }
@keyframes tx-notice-in { from { opacity: 0; } }
@media (prefers-reduced-motion: reduce) { .tx-notice { animation: none; } }

.tx-dialog-overlay { position: fixed; inset: 0; z-index: 50; background: var(--tx-scrim); }
.tx-dialog {
  position: fixed; left: 50%; top: 50%; transform: translate(-50%,-50%); z-index: 51;
  width: min(var(--tx-overlay-width, var(--tx-dialog-width)), calc(100vw - var(--tx-space-6) * 2)); max-height: min(var(--tx-dialog-max-height), calc(100dvh - var(--tx-space-6) * 2)); border-radius: var(--tx-radius-lg); overflow: hidden;
  display: flex; flex-direction: column;
}
.tx-dialog-head { flex: none; padding: var(--tx-space-4) var(--tx-space-4-half) var(--tx-space-1-half); font-size: var(--tx-size-lg); font-weight: var(--tx-weight-strong); letter-spacing: var(--tx-tracking-tight); }
.tx-dialog-body { min-width: 0; min-height: 0; overflow: auto; padding: var(--tx-space-1) var(--tx-space-4-half) var(--tx-space-4); color: var(--tx-text-2); font-size: var(--tx-size); line-height: var(--tx-leading-note); }
.tx-dialog-foot { flex: none; display: flex; justify-content: flex-end; align-items: center; gap: var(--tx-space-2); padding: var(--tx-space-3) var(--tx-space-4-half) var(--tx-space-4); }
.tx-dialog-foot:empty { display: none; }
.tx-dialog.tx-search-dialog { height: min(var(--tx-search-dialog-height), calc(100dvh - var(--tx-space-6) * 2)); }
.tx-search-dialog > .tx-dialog-body { display: flex; flex: 1; min-height: 0; overflow: hidden; }
.tx-search-dialog > .tx-dialog-body > * { flex: 1; min-width: 0; min-height: 0; }
.tx-dialog .tx-trace,.tx-popover .tx-trace,.tx-sheet .tx-trace { background: var(--tx-black-25); }
.tx-glass .tx-table th { background: transparent; }
.tx-glass .tx-table td { border-bottom-color: var(--tx-white-07); }

.tx-sheet-overlay { position: fixed; inset: var(--tx-topbar-height) 0 0; z-index: 50; background: var(--tx-scrim); }
.tx-sheet {
  position: fixed; top: var(--tx-sheet-top); right: var(--tx-space-2); bottom: var(--tx-space-2); z-index: 51;
  width: var(--tx-sheet-width); max-width: calc(100vw - var(--tx-space-4) * 2); border-radius: var(--tx-radius-lg);
  display: flex; flex-direction: column; overflow: hidden;
}
.tx-sheet-head { display: flex; align-items: center; gap: var(--tx-space-2); height: 48px; padding: 0 var(--tx-space-2-half) 0 var(--tx-space-4); flex: none; border-bottom: var(--tx-hairline) solid var(--tx-glass-edge); }
.tx-sheet-title { font-size: var(--tx-size-lg); font-weight: var(--tx-weight-strong); letter-spacing: var(--tx-tracking-tight); min-width: 0; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.tx-sheet-head .tx-icon-btn { margin-left: auto; }
.tx-sheet-actions { display: flex; align-items: center; gap: var(--tx-space-3); margin-left: auto; }
.tx-sheet-head .tx-sheet-actions ~ .tx-icon-btn { margin-left: 0; }
.tx-sheet-tabs { margin: 0 var(--tx-space-4); flex: none; }
.tx-sheet-body { flex: 1; min-height: 0; overflow: auto; padding: var(--tx-space-3) var(--tx-space-4) var(--tx-space-3); font-size: var(--tx-size-sm); }
.tx-sheet-foot { flex: none; display: flex; align-items: center; gap: var(--tx-space-2); padding: var(--tx-space-3) var(--tx-space-4); border-top: var(--tx-hairline) solid var(--tx-glass-edge); font-size: var(--tx-size-xs); color: var(--tx-text-3); }

/* ── Opaque containers ────────────────────────────────────────── */
.tx-panel { background: var(--tx-panel); border: var(--tx-hairline) solid var(--tx-line-2); border-radius: var(--tx-radius); overflow: hidden; display: flex; flex-direction: column; min-width: 0; }
.tx-panel-head { height: var(--tx-panel-head-height); flex: none; display: flex; align-items: center; gap: var(--tx-space-2); padding: 0 var(--tx-space-2-half) 0 var(--tx-pad); border-bottom: var(--tx-hairline) solid var(--tx-line); font-size: var(--tx-size-sm); color: var(--tx-text); font-weight: var(--tx-weight-strong); white-space: nowrap; min-width: 0; }
.tx-panel-body { padding: var(--tx-pad); min-height: 0; }

/* Tables: thin row lines, no zebra, no cards; numbers right-aligned in mono, words left. */
.tx-table { width: 100%; border-collapse: collapse; font-size: var(--tx-size-sm); font-variant-numeric: tabular-nums; }
.tx-table th { text-align: left; font-size: var(--tx-size-xs); font-weight: var(--tx-weight); color: var(--tx-text-3); padding: 0 var(--tx-cell-pad); height: var(--tx-head-row); border-bottom: var(--tx-hairline) solid var(--tx-line-2); white-space: nowrap; }
.tx-table td { padding: 0 var(--tx-cell-pad); height: var(--tx-row); border-bottom: var(--tx-hairline) solid var(--tx-line); vertical-align: middle; white-space: nowrap; }
.tx-table th:first-child, .tx-table td:first-child { padding-left: calc(var(--tx-cell-pad) + 4px); }
.tx-table th:last-child, .tx-table td:last-child { padding-right: calc(var(--tx-cell-pad) + 4px); }
.tx-table tbody tr:last-child td { border-bottom: 0; }
.tx-table tbody tr { transition: background var(--tx-row-time) var(--tx-ease); }
.tx-table tbody tr:hover, .tx-table tbody tr[data-force~="hover"] { background: var(--tx-white-025); }
.tx-table tr[data-selected="true"] td { background: var(--tx-ember-select); }
.tx-table tr[data-selected="true"] td:first-child { box-shadow: inset 2px 0 0 var(--tx-ember); }
.tx-table [data-align="right"] { text-align: right; }
.tx-table [data-numeric] { font-family: var(--tx-font-mono); letter-spacing: var(--tx-tracking-tight); text-align: right; }
.tx-table th[data-numeric] { font-family: var(--tx-font); letter-spacing: var(--tx-tracking); }
.tx-table[data-sticky] th { position: sticky; top: 0; z-index: 1; background: var(--tx-panel); }
.tx-table th[aria-sort] { cursor: pointer; user-select: none; }
.tx-table th[aria-sort]:hover { color: var(--tx-text-2); }
.tx-table th[aria-sort="ascending"], .tx-table th[aria-sort="descending"] { color: var(--tx-text); }
.tx-table th .tx-sort { display: inline-flex; align-items: center; gap: 3px; }
.tx-table th .tx-sort svg { width: 11px; height: 11px; }
.tx-table td.tx-cell-wrap { white-space: normal; }
.tx-cell-name { font-weight: var(--tx-weight-medium); color: var(--tx-text); }
.tx-cell-sub { font-size: var(--tx-size-xs); color: var(--tx-text-3); margin-top: var(--tx-hairline); }
.tx-cell-trunc { display: block; max-width: 100%; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.tx-search-results,.tx-search-feedback { margin-top: var(--tx-space-2) !important; }
.tx-search-loading { padding: var(--tx-space-2-half) 0; }

.tx-empty { display: flex; flex-direction: column; align-items: center; text-align: center; gap: var(--tx-space-1-half); padding: var(--tx-empty-padding) var(--tx-space-4); }
.tx-empty-title { font-size: var(--tx-size); font-weight: var(--tx-weight-strong); color: var(--tx-text); }
.tx-empty-text { font-size: var(--tx-size-sm); color: var(--tx-text-3); max-width: var(--tx-empty-line-width); }
.tx-empty .tx-btn { margin-top: var(--tx-space-2); }

/* Settings rows. */
.tx-settings-row { display: flex; align-items: center; justify-content: space-between; gap: var(--tx-space-4); padding: var(--tx-space-2-half) 0; border-bottom: var(--tx-hairline) solid var(--tx-line); }
.tx-settings-row:last-child { border-bottom: 0; }
.tx-settings-copy { min-width: 0; flex: 1; }
.tx-settings-name { font-size: var(--tx-size); font-weight: var(--tx-weight-medium); }
.tx-settings-detail { font-size: var(--tx-size-xs); color: var(--tx-text-3); margin-top: var(--tx-hairline); }
.tx-settings-row[data-tone="failed"] .tx-settings-detail { color: var(--tx-state-fix); }
.tx-settings-row[data-module] { gap: var(--tx-space-3); padding: var(--tx-space-2) 0; }
.tx-settings-row[data-module] .tx-settings-detail { margin-top: 0; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.tx-tile { width: var(--tx-control); height: var(--tx-control); border-radius: var(--tx-radius-sm); flex: none; display: grid; place-items: center; color: var(--tx-text-2); background: var(--tx-white-04); border: var(--tx-hairline) solid var(--tx-line-2); }
.tx-tile .tx-icon { width: var(--tx-icon-sm); height: var(--tx-icon-sm); }
.tx-tile[data-tone="off"] { color: var(--tx-text-4); }
.tx-tile[data-tone="failed"] { color: var(--tx-ember); border-color: var(--tx-ember-line); background: var(--tx-ember-select); }

/* Settings pages: a lede, then titled cards of rows. */
.tx-settings-page { display: flex; flex-direction: column; gap: var(--tx-space-6); min-width: 0; }
.tx-settings-lede { margin: 0; max-width: 68ch; color: var(--tx-text-3); font-size: var(--tx-size); line-height: var(--tx-leading); }
.tx-settings-lede code { font-family: var(--tx-font-mono); font-size: var(--tx-size-sm); color: var(--tx-text-2); }
.tx-settings-group { display: flex; flex-direction: column; gap: var(--tx-space-2); min-width: 0; }
.tx-settings-group-head { display: flex; align-items: flex-end; justify-content: space-between; gap: var(--tx-space-3); min-height: var(--tx-button); padding: 0 var(--tx-space-half); }
.tx-settings-group-copy { min-width: 0; }
.tx-settings-group-title { margin: 0; color: var(--tx-text); font-size: var(--tx-size-sm); font-weight: var(--tx-weight-strong); }
.tx-settings-group-detail { margin: var(--tx-space-half) 0 0; color: var(--tx-text-3); font-size: var(--tx-size-sm); line-height: var(--tx-leading); }
.tx-settings-group-action { display: flex; flex: none; align-items: center; gap: var(--tx-space-2); }
.tx-settings-card { min-width: 0; border: var(--tx-hairline) solid var(--tx-line-2); border-radius: var(--tx-radius); background: var(--tx-panel); overflow: hidden; }
.tx-settings-card > * + * { border-top: var(--tx-hairline) solid var(--tx-line); }
.tx-settings-card .tx-settings-row { min-height: calc(var(--tx-row) + var(--tx-space-4)); padding: var(--tx-space-2) var(--tx-pad); border-bottom: 0; }
.tx-settings-card .tx-settings-name { color: var(--tx-text); overflow-wrap: anywhere; }
.tx-settings-card .tx-settings-detail { margin-top: var(--tx-space-half); font-size: var(--tx-size-sm); line-height: var(--tx-leading); overflow-wrap: anywhere; }
.tx-settings-card > .tx-empty { padding: var(--tx-space-8) var(--tx-space-4); }
.tx-settings-card > .tx-empty .tx-empty-title { font-size: var(--tx-size); }
.tx-settings-note { margin: 0; padding: var(--tx-space-2-half) var(--tx-pad); color: var(--tx-text-3); font-size: var(--tx-size-sm); line-height: var(--tx-leading); }
.tx-settings-note[data-tone="error"] { color: var(--tx-state-fix); }
.tx-settings-card * + .tx-settings-note { padding-top: 0; }
.tx-settings-card > * + .tx-settings-note, .tx-settings-card > .tx-settings-note:first-child { padding-top: var(--tx-space-2-half); }

/* Alerts in place: what broke, what it means, the way forward. Orange to fix now, amber to watch, neutral for information. */
.tx-alert { display: flex; gap: var(--tx-space-2-half); padding: var(--tx-space-2-half) var(--tx-space-3); border-radius: var(--tx-radius-sm); border: var(--tx-hairline) solid var(--tx-ember-line); background: var(--tx-ember-select); }
.tx-alert > .tx-icon { width: 15px; height: 15px; margin-top: 2px; color: var(--tx-ember); }
.tx-alert[data-tone="watch"] { border-color: var(--tx-watch-line); background: var(--tx-watch-wash); }
.tx-alert[data-tone="watch"] > .tx-icon { color: var(--tx-watch); }
.tx-alert[data-tone="info"] { background: var(--tx-white-02); border-color: var(--tx-line-2); }
.tx-alert[data-tone="info"] > .tx-icon { color: var(--tx-text-3); }
.tx-alert-content { flex: 1; min-width: 0; }
.tx-alert h4 { margin: 1px 0 var(--tx-space-half); font-size: var(--tx-size); font-weight: var(--tx-weight-strong); color: var(--tx-text); }
.tx-alert p { margin: 0; color: var(--tx-text-2); font-size: var(--tx-size-sm); line-height: var(--tx-leading); max-width: var(--tx-alert-line-width); }
.tx-alert-actions { display: flex; align-items: center; gap: var(--tx-space-1-half); margin-top: var(--tx-space-2); flex-wrap: wrap; }
.tx-details { display: contents; }
.tx-disclosure { display: inline-flex; align-items: center; gap: var(--tx-space-1-quarter); height: var(--tx-button-sm); padding: 0 var(--tx-space-2) 0 var(--tx-space-1-half); border: 0; border-radius: var(--tx-radius-pill); background: none; color: var(--tx-text-3); font-size: var(--tx-size-sm); font-weight: var(--tx-weight-medium); cursor: pointer; }
.tx-disclosure:hover, .tx-disclosure[data-force~="hover"] { color: var(--tx-text); background: var(--tx-hover); }
.tx-disclosure .tx-icon { width: 12px; height: 12px; transition: transform var(--tx-slow) var(--tx-ease); }
.tx-disclosure[aria-expanded="true"] { color: var(--tx-text); }
.tx-disclosure[aria-expanded="true"] .tx-icon { transform: rotate(90deg); }
.tx-disclosure-group { min-width: 0; }
.tx-disclosure-body { padding: var(--tx-space-1) var(--tx-space-2); }
.tx-disclosure-body[hidden],.tx-disclosure-group[hidden] { display: none; }
.tx-trace { flex-basis: 100%; display: flex; align-items: flex-start; gap: var(--tx-space-2); padding: var(--tx-space-2) var(--tx-space-1-half) var(--tx-space-2) var(--tx-space-3); border-radius: var(--tx-radius-sm); background: var(--tx-black-25); border: var(--tx-hairline) solid var(--tx-line); }
.tx-trace code { flex: 1; font-family: var(--tx-font-mono); font-size: var(--tx-size-xs); color: var(--tx-text-2); line-height: var(--tx-leading-code); white-space: pre-wrap; overflow-wrap: anywhere; }
.tx-error { color: var(--tx-ember-text); }
.tx-secondary { color: var(--tx-text-2); }
.tx-title { font-size: var(--tx-size-lg); font-weight: var(--tx-weight-strong); letter-spacing: var(--tx-tracking-tight); }
.tx-btn.tx-status-action { background: none; border: 0; padding: 0; height: auto; line-height: var(--tx-leading); cursor: pointer; }
.tx-panel-tabs { height: var(--tx-panel-tabs-height); flex: none; display: flex; align-items: stretch; padding: 0 var(--tx-space-1); border-bottom: var(--tx-hairline) solid var(--tx-line); }
.tx-panel-tab { display: flex; align-items: center; gap: var(--tx-space-1-half); padding: 0 var(--tx-space-2-half); font-size: var(--tx-size-sm); font-weight: var(--tx-weight-medium); color: var(--tx-text-3); position: relative; white-space: nowrap; }
.tx-panel-tab .tx-icon { width: var(--tx-icon-sm); height: var(--tx-icon-sm); }
.tx-panel-tab[data-active] { color: var(--tx-text); }
.tx-panel-tab[data-active]::after { content: ''; position: absolute; left: var(--tx-space-2-half); right: var(--tx-space-2-half); bottom: 0; height: var(--tx-stroke); background: var(--tx-ember); }
.tx-panel-tools { margin-left: auto; display: flex; align-items: center; gap: var(--tx-space-half); }
.tx-panel-tools .tx-icon-btn { width: var(--tx-button-sm); height: var(--tx-button-sm); border-radius: var(--tx-radius-xs); }
.tx-panel-tools .tx-icon { width: var(--tx-icon-sm); height: var(--tx-icon-sm); }
.tx-sheet[data-preview],.tx-dialog[data-preview] { position: absolute; }
.tx-sheet-overlay[data-preview] { position: absolute; }
.tx-sheet[data-preview] { z-index: 5; }
.tx-sheet-overlay[data-preview] { z-index: 4; }

@media (prefers-reduced-transparency: reduce) {
  .tx-glass, .tx-tip { -webkit-backdrop-filter: none; backdrop-filter: none; }
  .tx-glass { background: var(--tx-glass-solid); }
}
@media (prefers-reduced-motion: reduce) {
  .tx-root *, .tx-root *::before, .tx-root *::after { animation: none !important; transition: none !important; }
  /* Motion is the only cue that work is in progress, so each loses its motion
     but keeps a still shape no other state uses: a three-quarter ring for
     Running, and a dashed full-width bar for an unknown-length progress. */
  .tx-status[data-tone="busy"]::before { animation: none !important; border-color: var(--tx-text); border-left-color: var(--tx-line-3); }
  .tx-progress[data-indeterminate] > i {
    animation: none !important; width: 100% !important;
    background: repeating-linear-gradient(90deg, var(--tx-text-2) 0 var(--tx-space-1-half), transparent var(--tx-space-1-half) var(--tx-space-2-half));
  }
}
`;

const workbenchCss = `
/* ── Workbench layout (workbench.tsx) ─────────────────────── */
.tx-toolbar { display: flex; align-items: center; gap: var(--tx-space-1); flex: none; min-width: 0; height: calc(var(--tx-button) + var(--tx-space-2-half)); padding: 0 var(--tx-space-1-half); border-bottom: var(--tx-hairline) solid var(--tx-line); background: var(--tx-panel); overflow: hidden; }
.tx-toolbar > * { flex: none; }
.tx-toolbar .tx-btn { margin-top: 0; }
.tx-toolbar-sep { width: var(--tx-hairline); height: calc(var(--tx-button) - var(--tx-space-2-half)); background: var(--tx-line-2); margin: 0 var(--tx-space-1); }
.tx-toolbar .tx-spacer { flex: 1 1 auto; min-width: var(--tx-space-2); }
.tx-toolbar-text { color: var(--tx-text-3); font-size: var(--tx-size-xs); white-space: nowrap; overflow: hidden; text-overflow: ellipsis; min-width: 0; flex: 0 1 auto !important; }
/* A vertical rail of tools, such as drawing tools along a chart. */
.tx-toolbar[data-orientation="vertical"] { flex-direction: column; height: auto; width: calc(var(--tx-button) + var(--tx-space-2-half)); padding: var(--tx-space-1-half) 0; border-bottom: 0; border-right: var(--tx-hairline) solid var(--tx-line); }
.tx-toolbar[data-orientation="vertical"] .tx-toolbar-sep { width: calc(var(--tx-button) - var(--tx-space-2-half)); height: var(--tx-hairline); margin: var(--tx-space-1) 0; }

.tx-split { display: flex; flex: 1 1 auto; min-height: 0; min-width: 0; }
.tx-split-side { flex: none; min-width: 0; min-height: 0; display: flex; flex-direction: column; background: var(--tx-panel); }
.tx-split-divider { flex: none; width: var(--tx-hairline); position: relative; background: var(--tx-line); cursor: col-resize; outline: none; }
.tx-split-divider::after { content: ""; position: absolute; inset: 0 calc(-1 * var(--tx-space-1)); z-index: 2; }
.tx-split-divider:hover, .tx-split-divider:focus-visible { background: var(--tx-text-3); }
.tx-split-main { flex: 1 1 auto; min-width: 0; min-height: 0; display: flex; flex-direction: column; background: var(--tx-bg); }
.tx-split[data-side="end"] > .tx-split-side { order: 2; }
.tx-split[data-side="end"] > .tx-split-divider { order: 1; }
.tx-split[data-side="end"] > .tx-split-main { order: 0; }

.tx-inspector { display: flex; flex-direction: column; min-height: 0; height: 100%; }
.tx-inspector-head { flex: none; display: flex; align-items: center; gap: var(--tx-space-2); min-height: var(--tx-panel-head-height); padding: 0 var(--tx-space-3) 0 var(--tx-pad); border-bottom: var(--tx-hairline) solid var(--tx-line); font-size: var(--tx-size-sm); font-weight: var(--tx-weight-strong); }
.tx-inspector-head .tx-btn { margin-top: 0; }
.tx-inspector-body { flex: 1 1 auto; min-height: 0; overflow: auto; padding-bottom: var(--tx-space-4); }
.tx-inspector-foot { flex: none; display: flex; align-items: center; gap: var(--tx-space-2); padding: var(--tx-space-2-half) var(--tx-pad); border-top: var(--tx-hairline) solid var(--tx-line); font-size: var(--tx-size-xs); color: var(--tx-text-3); }
.tx-inspector-foot .tx-btn { margin-top: 0; }

.tx-section { padding: var(--tx-space-2-half) var(--tx-space-3) var(--tx-space-2) var(--tx-pad); border-top: var(--tx-hairline) solid var(--tx-line); min-width: 0; }
.tx-section:first-child { border-top: 0; }
.tx-section-head { display: flex; align-items: center; gap: var(--tx-space-2); min-height: var(--tx-button-sm); margin-bottom: var(--tx-space-1); }
.tx-section-head .tx-btn { margin-top: 0; }
.tx-section-title { font-size: var(--tx-size-xs); font-weight: var(--tx-weight-medium); color: var(--tx-text-3); }
.tx-section-body { display: flex; flex-direction: column; gap: var(--tx-space-1); min-width: 0; }
.tx-section-body > p { margin: 0; color: var(--tx-text-3); font-size: var(--tx-size-xs); line-height: var(--tx-leading-note); }
.tx-section-toggle { width: 100%; background: none; border: 0; padding: 0; color: var(--tx-text-3); cursor: pointer; margin-bottom: 0; text-align: left; font: inherit; }
.tx-section-toggle .tx-section-title { color: var(--tx-text-3); }
.tx-section-toggle:hover .tx-section-title { color: var(--tx-text-2); }
.tx-section-toggle svg { width: 11px; height: 11px; color: var(--tx-text-4); transition: transform var(--tx-fast) var(--tx-ease); }
.tx-section-toggle[aria-expanded="true"] svg { transform: rotate(90deg); }
.tx-section-toggle[aria-expanded="true"] { margin-bottom: var(--tx-space-1-half); }

.tx-field[data-layout="inline"] { display: grid; grid-template-columns: minmax(0, 1fr) minmax(var(--tx-affix-padding), 58%); column-gap: var(--tx-space-2); row-gap: var(--tx-space-half); align-items: center; min-height: calc(var(--tx-control) + 2px); }
.tx-field[data-layout="inline"] > label { font-weight: var(--tx-weight); color: var(--tx-text-2); line-height: 1.25; min-width: 0; overflow-wrap: anywhere; }
.tx-field[data-layout="inline"] > :not(label) { grid-column: 2; min-width: 0; width: 100%; }
.tx-field[data-layout="inline"] > :is(.tx-switch, .tx-check-box, .tx-btn, .tx-icon-btn) { width: auto; justify-self: start; }
.tx-field[data-layout="inline"] > .tx-switch { width: var(--tx-switch-width); }
.tx-field[data-layout="inline"] > .tx-hint, .tx-field[data-layout="inline"] > .tx-error { grid-column: 1 / -1; }
.tx-field[data-layout="inline"] > textarea, .tx-field[data-layout="inline"] > .tx-textarea, .tx-field[data-layout="inline"] > table, .tx-field[data-layout="inline"] > .tx-table { grid-column: 1 / -1; }

.tx-summary { display: grid; grid-template-columns: minmax(0, 1fr) minmax(0, auto) var(--tx-size); align-items: center; gap: var(--tx-space-2); width: calc(100% + var(--tx-space-3)); min-height: var(--tx-control); margin: 0 calc(-1 * var(--tx-space-1-half)); padding: 0 var(--tx-space-1-half); border: 0; border-radius: var(--tx-radius-sm); background: none; color: inherit; font: inherit; text-align: left; cursor: pointer; }
.tx-summary:hover:not(:disabled) { background: var(--tx-white-04); }
.tx-summary:disabled { cursor: default; grid-template-columns: minmax(0, 1fr) minmax(0, auto); }
.tx-summary-label { color: var(--tx-text-2); font-size: var(--tx-size-sm); overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.tx-summary-value { color: var(--tx-text); font-size: var(--tx-size-sm); text-align: right; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.tx-summary svg { width: var(--tx-size); height: var(--tx-size); color: var(--tx-text-3); }
.tx-summary:hover svg { color: var(--tx-text); }
.tx-summary[data-tone="review"] .tx-summary-value { color: var(--tx-ember-text); }
.tx-summary-editor { display: flex; flex-direction: column; gap: var(--tx-space-3); width: calc(var(--tx-control) * 10); max-width: 100%; padding: var(--tx-space-1); }
.tx-popover[data-size] .tx-summary-editor { width: auto; }
.tx-summary-editor-title { font-size: var(--tx-size-sm); font-weight: var(--tx-weight-strong); color: var(--tx-text); }

/* Key figures: a full-width band with thin dividers, never boxed. */
.tx-metrics { display: grid; grid-template-columns: repeat(auto-fit, minmax(calc(var(--tx-control) * 4.4), 1fr)); gap: 0; margin: 0; border-top: var(--tx-hairline) solid var(--tx-line); border-bottom: var(--tx-hairline) solid var(--tx-line); overflow: hidden; }
.tx-metric { margin: 0; padding: var(--tx-space-2) var(--tx-space-3) var(--tx-space-2-half) var(--tx-pad); min-width: 0; box-shadow: -1px 0 0 var(--tx-line); }
.tx-metric dt { color: var(--tx-text-3); font-size: var(--tx-size-xs); white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
.tx-metric dd { margin: var(--tx-space-half) 0 0; display: flex; align-items: baseline; gap: var(--tx-space-1-half); min-width: 0; }
.tx-metric-value { font-family: var(--tx-font-mono); font-size: var(--tx-size-num); line-height: 1.35; font-weight: var(--tx-weight); letter-spacing: var(--tx-tracking-tight); color: var(--tx-text); white-space: nowrap; font-variant-numeric: tabular-nums; }
.tx-metric-detail { font-family: var(--tx-font-mono); font-size: var(--tx-size-xs); color: var(--tx-text-3); white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
.tx-metric[data-tone="up"] .tx-metric-value { color: var(--tx-up); }
.tx-metric[data-tone="down"] .tx-metric-value { color: var(--tx-down); }
.tx-metric[data-tone="review"] .tx-metric-value { color: var(--tx-ember-text); }

.tx-figure { display: flex; flex-direction: column; margin: 0; min-width: 0; border: var(--tx-hairline) solid var(--tx-line); border-radius: var(--tx-radius); background: var(--tx-panel); }
.tx-figure-head { display: flex; align-items: center; gap: var(--tx-space-2); min-height: var(--tx-panel-head-height); padding: 0 var(--tx-space-2) 0 var(--tx-pad); }
.tx-figure-head .tx-btn { margin-top: 0; }
.tx-figure-title { font-size: var(--tx-size-sm); font-weight: var(--tx-weight-strong); color: var(--tx-text); min-width: 0; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.tx-figure-body { padding: 0 var(--tx-space-2) var(--tx-space-1); min-width: 0; }
.tx-figure-body > img { display: block; width: 100%; height: auto; }
.tx-figure-note { margin: 0; padding: 0 var(--tx-space-3) var(--tx-space-2-half) var(--tx-pad); color: var(--tx-text-3); font-size: var(--tx-size-xs); line-height: var(--tx-leading-note); }
.tx-figure-grid { display: grid; grid-template-columns: repeat(auto-fill, minmax(min(100%, calc(var(--tx-control) * 18)), 1fr)); gap: var(--tx-space-3); min-width: 0; }
.tx-figure-grid > .tx-figure[data-span="full"] { grid-column: 1 / -1; }
.tx-num em, .tx-num .tx-num-label { font-family: var(--tx-font); font-style: normal; letter-spacing: var(--tx-tracking); }
`;

export const talyxUiCss = `${talyxTokensCss}\n${baseCss}\n${workbenchCss}\n${researchCss}`;
