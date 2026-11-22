/* The formula box on top of a factor's Work: one Qlib expression, coloured, with its first mistake marked in place. */
import { t } from "../i18n";
import { useLayoutEffect, useRef, useState, type ReactNode } from "react";
import { TxButton, TxIcon, TxPopover } from "@talyx/sdk";
import type { FactorFields, FormulaError } from "./api";
import { my } from "./format";

const TOKEN = /(\$[A-Za-z_][\w.]*)|("[^"]*"?)|\b([A-Za-z_]\w*)(?=\s*\()|(\d+(?:\.\d+)?)/g;

/** The formula as coloured spans; the mistake's characters are underlined, a missing bracket marks the end. */
export function highlight(text: string, error: FormulaError | null): ReactNode[] {
  const out: ReactNode[] = [];
  const mark = error === null ? null : {at: Math.min(error.at, text.length), end: Math.min(text.length, error.at + Math.max(error.length, 0))};
  let index = 0, key = 0;
  const plain = (from: number, to: number) => {
    // Plain text, with the part inside the mistake underlined.
    if (to <= from) return;
    if (mark && mark.end > mark.at && from < mark.end && to > mark.at) {
      const a = Math.max(from, mark.at), b = Math.min(to, mark.end);
      if (a > from) out.push(text.slice(from, a));
      out.push(<span key={key++} className="fx-sq">{text.slice(a, b)}</span>);
      if (to > b) out.push(text.slice(b, to));
    } else out.push(text.slice(from, to));
  };
  for (const match of text.matchAll(TOKEN)) {
    const start = match.index ?? 0, end = start + match[0].length;
    plain(index, start);
    const kind = match[1] ? "fx-tq" : match[2] ? "fx-ts" : match[3] ? "fx-tf" : "fx-tn";
    const inside = mark !== null && mark.end > mark.at && start < mark.end && end > mark.at;
    out.push(<span key={key++} className={inside ? `${kind} fx-sq` : kind}>{match[0]}</span>);
    index = end;
  }
  plain(index, text.length);
  if (mark && mark.end <= mark.at) out.push(<span key={key++} className="fx-sq fx-miss">{" "}</span>);
  return out;
}

const fromText = (from: string | number) => typeof from === "number" || /^\d{10,}$/.test(String(from)) ? t("from {date}", { date: my(Number(from)) }) : t("from {date}", { date: from });

/** The fields a formula can read for these names: only what Market gives (one it does not give is named where a formula uses it), by group, with where its data start. */
export function FieldList({fields, onPick}: {fields: FactorFields["fields"]; onPick(name: string): void}) {
  const groups = new Map<string, FactorFields["fields"]>();
  for (const field of fields.filter(item => !item.unavailable)) groups.set(field.group, [...(groups.get(field.group) ?? []), field]);
  return <div className="fx-fields">{[...groups.entries()].map(([group, items]) => <div key={group}><h5>{group}</h5><table className="fx-g"><tbody>
    {items.map(field => <tr key={field.name} onClick={() => onPick(field.name)}><td className="l fx-fname">{field.name}</td><td className="l">{field.label}</td>
      <td className="l fx-t3 fx-fnote">{field.from !== undefined ? fromText(field.from) : ""}</td></tr>)}</tbody></table></div>)}</div>;
}

export function FormulaBox({value, error, fields, onChange, onRun, onToCode, focusKey, label = t("Formula"), placeholder = t("Formula, e.g. -Ref($close, 5) / $close")}: {
  value: string; error: FormulaError | null; fields: FactorFields | null;
  onChange(value: string): void; onRun(): void; /** Without it there is no To code (a label or a weight is not turned into code). */ onToCode?(): void;
  /** Changes when the main field should take focus (a new, empty factor). */
  focusKey?: string; label?: string; placeholder?: string;
}) {
  const input = useRef<HTMLTextAreaElement>(null);
  const [functions, setFunctions] = useState(false), [fieldsOpen, setFieldsOpen] = useState(false);
  useLayoutEffect(() => {
    const element = input.current;
    if (!element) return;
    element.style.height = "0px";
    element.style.height = `${element.scrollHeight}px`;
  }, [value]);
  useLayoutEffect(() => {if (focusKey) input.current?.focus();}, [focusKey]);
  const insert = (text: string) => {
    const element = input.current;
    const at = element ? element.selectionStart : value.length, to = element ? element.selectionEnd : value.length;
    const next = value.slice(0, at) + text + value.slice(to);
    onChange(next);
    setFunctions(false); setFieldsOpen(false);
    requestAnimationFrame(() => {element?.focus(); element?.setSelectionRange(at + text.length, at + text.length);});
  };
  const groups = new Map<string, {name: string; title: string; insert: string; off?: boolean}[]>();
  for (const operator of fields?.operators ?? []) {
    const list = groups.get(operator.group) ?? [];
    list.push({name: operator.name, title: `${operator.signature}: ${operator.help}`, insert: operator.name + "("});
    groups.set(operator.group, list);
  }
  return <div className="fx-fbox" data-bad={error ? "" : undefined}>
    <div className="fx-fline">
      <div className="fx-fcode">
        <pre aria-hidden="true">{highlight(value, error)}{"​"}</pre>
        <textarea ref={input} value={value} spellCheck={false} rows={1} aria-label={label} placeholder={placeholder}
          onChange={event => onChange(event.target.value.replace(/\r?\n/g, " "))}
          onKeyDown={event => {if (event.key === "Enter") {event.preventDefault(); onRun();}}} />
      </div>
      <TxPopover open={fieldsOpen} onOpenChange={setFieldsOpen} label={t("Fields")} size="wide" align="end" trigger={<TxButton size="sm" variant="quiet">{t("Fields")}</TxButton>}>
        <FieldList fields={fields?.fields ?? []} onPick={insert} />
      </TxPopover>
      <TxPopover open={functions} onOpenChange={setFunctions} label={t("Functions")} className="fx-fns"
        trigger={<TxButton size="sm" variant="quiet">{t("Functions")}</TxButton>}>
        {[...groups.entries()].map(([group, items]) => <div key={group}><h5>{group}</h5><div className="fx-fnl">
          {items.map(item => <button key={item.name} type="button" className="fx-fn" data-off={item.off ? "" : undefined} title={item.title} onClick={() => insert(item.insert)}>{item.name}</button>)}
        </div></div>)}
      </TxPopover>
      {onToCode && value.trim() && error === null && <TxButton size="sm" variant="quiet" onClick={onToCode}><TxIcon name="code" />{t("To code")}</TxButton>}
    </div>
    <div className="fx-ferr" role={error ? "alert" : undefined}>{error && <>
      <TxIcon name="alert" /><b>{error.message}</b><span className="fx-t3">{t("at character {at}", { at: error.at + 1 })}</span>
      {error.fix && <TxButton size="sm" onClick={() => onChange(value.slice(0, error.at) + error.fix + value.slice(error.at + error.length))}>{t("Use {fix}", { fix: error.fix })}</TxButton>}
      {error.kind === "field" && <TxButton size="sm" variant="quiet" onClick={() => setFieldsOpen(true)}>{t("Fields")}</TxButton>}
    </>}</div>
  </div>;
}
