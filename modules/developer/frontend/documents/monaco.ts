import * as monaco from "monaco-editor";
import EditorWorker from "monaco-editor/esm/vs/editor/editor.worker?worker";
import TsWorker from "monaco-editor/esm/vs/language/typescript/ts.worker?worker";
import JsonWorker from "monaco-editor/esm/vs/language/json/json.worker?worker";
import CssWorker from "monaco-editor/esm/vs/language/css/css.worker?worker";
import HtmlWorker from "monaco-editor/esm/vs/language/html/html.worker?worker";
import { tokenValue } from "@talyx/sdk";

export type Monaco = typeof monaco;
(self as unknown as { MonacoEnvironment: monaco.Environment }).MonacoEnvironment = { getWorker(_id: string, label: string) {
  if (label === "typescript" || label === "javascript") return new TsWorker();
  if (label === "json") return new JsonWorker();
  if (label === "css" || label === "scss" || label === "less") return new CssWorker();
  if (label === "html") return new HtmlWorker();
  return new EditorWorker();
} };

const color = (token: string) => {
  const value = tokenValue(token);
  if (value.startsWith("#")) return value;
  const parts = value.match(/[\d.]+/g)?.map(Number);
  if (!parts || parts.length < 3) throw new Error(`The Developer theme token ${token} is unavailable.`);
  return "#" + [...parts.slice(0, 3), Math.round((parts[3] ?? 1) * 255)].map(part => Math.round(part).toString(16).padStart(2, "0")).join("");
};
const luminance = (hex: string) => [1, 3, 5].map(index => parseInt(hex.slice(index, index + 2), 16) / 255).reduce((sum, value, index) => sum + value * [0.2126, 0.7152, 0.0722][index], 0);

/** The editor follows TALYX tokens: neutral text tones, ember for focus and problems. */
function defineTheme() {
  const panel = color("--tx-panel");
  monaco.editor.defineTheme("talyx", { base: luminance(panel) < 0.5 ? "vs-dark" : "vs", inherit: true, rules: [
    { token: "", foreground: color("--tx-text-2").slice(1, 7) },
    { token: "keyword", foreground: color("--tx-text").slice(1, 7) },
    { token: "comment", foreground: color("--tx-text-3").slice(1, 7), fontStyle: "italic" },
    { token: "string", foreground: color("--tx-ember-text").slice(1, 7) },
    { token: "number", foreground: color("--tx-text").slice(1, 7) },
  ], colors: {
    "editor.background": panel, "editor.foreground": color("--tx-text"), "editorGutter.background": panel,
    "editorLineNumber.foreground": color("--tx-text-3"), "editorLineNumber.activeForeground": color("--tx-text-2"),
    "editor.selectionBackground": color("--tx-white-10"), "editor.lineHighlightBackground": color("--tx-white-04"),
    "editorCursor.foreground": color("--tx-ember"), "focusBorder": color("--tx-ember"), "editorWidget.background": color("--tx-card"),
    "editorError.foreground": color("--tx-state-fix-mark"), "editorWarning.foreground": color("--tx-state-watch"),
    "scrollbar.shadow": color("--tx-clear"), "scrollbarSlider.background": color("--tx-white-14"),
    "scrollbarSlider.hoverBackground": color("--tx-scroll-hint"), "scrollbarSlider.activeBackground": color("--tx-white-22"),
  } });
  monaco.editor.setTheme("talyx");
}
defineTheme();

/** Monaco draws its own scrollbars, out of reach of the global hiding: a thin dark slider that shows only while the text
    scrolls or the pointer is over it, no track, no shadow, and no overview ruler standing in as a resident bar. */
function scrollbar() {
  const thin = parseFloat(tokenValue("--tx-scroll-hint-width")) * 2;
  return { vertical: "auto", horizontal: "auto", verticalScrollbarSize: thin, horizontalScrollbarSize: thin, useShadows: false, alwaysConsumeMouseWheel: false } as const;
}
const quietRuler = { overviewRulerLanes: 0, overviewRulerBorder: false, hideCursorInOverviewRuler: true } as const;

const languages = new Set(monaco.languages.getLanguages().map(item => item.id));
export function editorLanguage(language: string) {
  const selected = { typescriptreact: "typescript", javascriptreact: "javascript" }[language] ?? language;
  return languages.has(selected) ? selected : "plaintext";
}

export function modelUri(documentId: string, name: string) {
  return monaco.Uri.from({ scheme: "talyx-document", path: `/${documentId.replace(/[^\w-]/g, "_")}/${name}` });
}

export function createEditor(host: HTMLElement, model: monaco.editor.ITextModel, options: { readOnly: boolean; onSave(): void }) {
  defineTheme();
  const editor = monaco.editor.create(host, { model, theme: "talyx", automaticLayout: true, readOnly: options.readOnly, minimap: { enabled: false },
    fontFamily: tokenValue("--tx-font-mono"), fontSize: parseFloat(tokenValue("--tx-size")), scrollBeyondLastLine: false, tabSize: 4, renderWhitespace: "selection",
    scrollbar: scrollbar(), ...quietRuler });
  editor.addCommand(monaco.KeyMod.CtrlCmd | monaco.KeyCode.KeyS, options.onSave);
  return editor;
}

/** A plain text input with the editor's look: no minimap, gutter or rulers; it can grow with its text instead of scrolling. */
export function createTextEditor(host: HTMLElement, model: monaco.editor.ITextModel, options: { readOnly: boolean; autoHeight: boolean; lineNumbers: boolean; wordSuggestions: boolean }) {
  defineTheme();
  return monaco.editor.create(host, { model, theme: "talyx", automaticLayout: true, readOnly: options.readOnly, minimap: { enabled: false },
    fontFamily: tokenValue("--tx-font-mono"), fontSize: parseFloat(tokenValue("--tx-size")), scrollBeyondLastLine: false, tabSize: 4, renderWhitespace: "selection",
    lineNumbers: options.lineNumbers ? "on" : "off", lineDecorationsWidth: options.lineNumbers ? 10 : 6, glyphMargin: false, folding: false, overviewRulerLanes: 0,
    hideCursorInOverviewRuler: true, overviewRulerBorder: false, renderLineHighlight: "none", contextmenu: true, fixedOverflowWidgets: true,
    quickSuggestions: false, suggestOnTriggerCharacters: true, wordBasedSuggestions: options.wordSuggestions ? "currentDocument" : "off",
    padding: options.autoHeight ? { top: 6, bottom: 6 } : undefined,
    scrollbar: options.autoHeight ? { ...scrollbar(), vertical: "hidden" } : scrollbar() });
}

export function createCompare(host: HTMLElement, original: { name: string; content: string; language: string }, modified: monaco.editor.ITextModel) {
  defineTheme();
  const left = monaco.editor.createModel(original.content, editorLanguage(original.language), monaco.Uri.from({ scheme: "talyx-compare", path: `/${crypto.randomUUID()}/${original.name}` }));
  const editor = monaco.editor.createDiffEditor(host, { theme: "talyx", automaticLayout: true, readOnly: true, originalEditable: false, renderSideBySide: true, minimap: { enabled: false },
    fontFamily: tokenValue("--tx-font-mono"), fontSize: parseFloat(tokenValue("--tx-size")), scrollbar: scrollbar(), ...quietRuler });
  editor.setModel({ original: left, modified });
  return { dispose() { editor.dispose(); left.dispose(); } };
}

export { monaco };
