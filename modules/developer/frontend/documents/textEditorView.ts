import { t } from "../i18n";
import type { Json, ModuleContext } from "@talyx/sdk";
import type { ContractTypes } from "../../public/ts/types";

type Props = ContractTypes["view:text_editor"];
type Key = NonNullable<Props["keys"]>[number];
type Answer = NonNullable<Props["answer"]>;
type Monaco = typeof import("./monaco")["monaco"];
type Editor = ReturnType<typeof import("./monaco")["createTextEditor"]>;
type Language = typeof import("./monaco")["editorLanguage"];

/** What one mounted editor answers when Monaco asks for completions or hover text on its model. */
interface Assist { complete(offset: number): Promise<Extract<Answer, {kind: "complete"}> | null>; inspect(offset: number): Promise<string | null> }
const assists = new Map<string, Assist>();
const registered = new Set<string>();
const WAIT_MS = 4000;

/** One completion and one hover provider per language, shared by every text editor: Monaco's providers are global. */
function registerAssist(monaco: Monaco, language: string) {
  if (registered.has(language)) return;
  registered.add(language);
  const kinds: Record<string, number> = { function: monaco.languages.CompletionItemKind.Function, class: monaco.languages.CompletionItemKind.Class,
    module: monaco.languages.CompletionItemKind.Module, variable: monaco.languages.CompletionItemKind.Variable, keyword: monaco.languages.CompletionItemKind.Keyword,
    property: monaco.languages.CompletionItemKind.Property, file: monaco.languages.CompletionItemKind.File, text: monaco.languages.CompletionItemKind.Text };
  monaco.languages.registerCompletionItemProvider(language, {
    triggerCharacters: ["."],
    async provideCompletionItems(model, position) {
      const assist = assists.get(model.uri.toString());
      if (!assist) return undefined;
      const answer = await assist.complete(model.getOffsetAt(position));
      if (!answer || model.isDisposed()) return { suggestions: [] };
      const start = model.getPositionAt(answer.start), end = model.getPositionAt(answer.end);
      const range = { startLineNumber: start.lineNumber, startColumn: start.column, endLineNumber: end.lineNumber, endColumn: end.column };
      return { suggestions: answer.items.map((item, index) => ({ label: item.label, insertText: item.label, range, detail: item.detail,
        kind: item.kind ? kinds[item.kind] : monaco.languages.CompletionItemKind.Text, sortText: String(index).padStart(5, "0") })) };
    },
  });
  monaco.languages.registerHoverProvider(language, {
    async provideHover(model, position) {
      const assist = assists.get(model.uri.toString());
      const word = model.getWordAtPosition(position);
      if (!assist || !word) return undefined;
      const text = await assist.inspect(model.getOffsetAt({ lineNumber: position.lineNumber, column: word.endColumn }));
      if (!text || model.isDisposed()) return undefined;
      return { range: { startLineNumber: position.lineNumber, startColumn: word.startColumn, endLineNumber: position.lineNumber, endColumn: word.endColumn },
        contents: [{ value: "```\n" + text.replace(/```/g, "`​``") + "\n```" }] };
    },
  });
}

/**
 * A controlled text editor other modules embed: it shows `value` in `language`, reports every edit as `changed`, and leaves
 * keeping the text to its owner. It belongs to no document. With `assist`, completions and hover text are asked of the
 * owner (`complete`, `inspect`) and answered through `answer`.
 */
export function mountTextEditor(node: HTMLElement, ctx: ModuleContext) {
  let props = ctx.props as unknown as Props;
  let disposed = false, editor: Editor | undefined, monaco: Monaco | undefined, language: Language | undefined;
  let focused = "";
  // Values this editor reported that its owner has not echoed back yet: an older one arriving late is not an outside change.
  let reported: string[] = [];
  const waiting = new Map<string, (answer: Answer | null) => void>();
  let sequence = 0;
  const disposers: { dispose(): void }[] = [];
  node.style.minHeight = "0";
  if (!props.autoHeight) node.style.height = "100%";

  const ask = (kind: "complete" | "inspect", offset: number) => new Promise<Answer | null>(resolve => {
    if (disposed || !editor || !props.assist) { resolve(null); return; }
    const requestId = `${kind}-${++sequence}`;
    const timer = setTimeout(() => { waiting.delete(requestId); resolve(null); }, WAIT_MS);
    waiting.set(requestId, answer => { clearTimeout(timer); resolve(answer); });
    ctx.emit(kind, { requestId, value: editor.getValue(), offset });
  });
  const assist: Assist = {
    complete: async offset => { const answer = await ask("complete", offset); return answer && answer.kind === "complete" ? answer : null; },
    inspect: async offset => { const answer = await ask("inspect", offset); return answer && answer.kind === "inspect" ? answer.text : null; },
  };

  const apply = () => {
    if (!editor || !monaco || !language) return;
    const model = editor.getModel()!;
    editor.updateOptions({ readOnly: Boolean(props.readOnly) });
    monaco.editor.setModelLanguage(model, language(props.language));
    if (props.assist) registerAssist(monaco, model.getLanguageId());
    const value = props.value.replace(/\r\n?/g, "\n");
    if (value !== model.getValue()) {
      const index = reported.indexOf(value);
      if (index >= 0) reported = reported.slice(index + 1);
      else {
        reported = [];
        model.pushEditOperations([], [{ range: model.getFullModelRange(), text: value }], () => null);
      }
    } else reported = [];
    if (props.answer && waiting.has(props.answer.requestId)) {
      const resolve = waiting.get(props.answer.requestId)!;
      waiting.delete(props.answer.requestId);
      resolve(props.answer);
    }
    if (props.focusRequestId && props.focusRequestId !== focused) { focused = props.focusRequestId; editor.focus(); }
  };

  void import("./monaco").then(({ monaco: loaded, createTextEditor, editorLanguage }) => {
    if (disposed) return;
    monaco = loaded; language = editorLanguage;
    const model = loaded.editor.createModel(props.value, editorLanguage(props.language), loaded.Uri.from({ scheme: "talyx-text", path: `/${crypto.randomUUID()}` }));
    model.setEOL(loaded.editor.EndOfLineSequence.LF);
    editor = createTextEditor(node, model, { readOnly: Boolean(props.readOnly), autoHeight: Boolean(props.autoHeight), lineNumbers: Boolean(props.lineNumbers), wordSuggestions: !props.assist });
    const created = editor;
    assists.set(model.uri.toString(), assist);
    disposers.push(model.onDidChangeContent(() => {
      const value = model.getValue();
      reported.push(value);
      if (reported.length > 50) reported.shift();
      ctx.emit("changed", { value });
    }));
    disposers.push(created.onDidFocusEditorText(() => ctx.emit("focus", { focused: true })), created.onDidBlurEditorText(() => ctx.emit("focus", { focused: false })));
    if (props.autoHeight) {
      const fit = () => { node.style.height = `${created.getContentHeight()}px`; created.layout(); };
      disposers.push(created.onDidContentSizeChange(fit));
      fit();
    }
    // Tab completes after a name or a dot, as in Jupyter; elsewhere it indents.
    const completes = created.createContextKey<boolean>("talyxTextCompletes", false);
    disposers.push(created.onDidChangeCursorPosition(event => {
      const line = model.getLineContent(event.position.lineNumber).slice(0, event.position.column - 1);
      completes.set(Boolean(props.assist) && /[\w.]$/.test(line));
    }));
    created.addAction({ id: "talyx.text.complete", label: t("Complete"), keybindings: [loaded.KeyCode.Tab], precondition: "talyxTextCompletes && !suggestWidgetVisible && !inSnippetMode",
      run: () => { created.trigger("keyboard", "editor.action.triggerSuggest", {}); } });
    const codes: Record<Key, number> = { "Shift+Enter": loaded.KeyMod.Shift | loaded.KeyCode.Enter, "Ctrl+Enter": loaded.KeyMod.CtrlCmd | loaded.KeyCode.Enter,
      "Alt+Enter": loaded.KeyMod.Alt | loaded.KeyCode.Enter, "Ctrl+S": loaded.KeyMod.CtrlCmd | loaded.KeyCode.KeyS, "Escape": loaded.KeyCode.Escape };
    for (const key of props.keys ?? []) created.addAction({ id: `talyx.text.key.${key}`, label: key, keybindings: [codes[key]],
      precondition: key === "Escape" ? "!suggestWidgetVisible && !findWidgetVisible" : undefined, run: () => ctx.emit("key", { key }) });
    apply();
  }).catch(error => { if (!disposed) node.textContent = error instanceof Error ? error.message : String(error); });

  const dispose = () => {
    if (disposed) return;
    disposed = true;
    for (const resolve of waiting.values()) resolve(null);
    waiting.clear();
    for (const item of disposers) item.dispose();
    const model = editor?.getModel();
    if (model) assists.delete(model.uri.toString());
    editor?.dispose(); model?.dispose();
  };
  ctx.onDispose(dispose);
  return { update(value: Json) { props = value as unknown as Props; apply(); }, dispose };
}
