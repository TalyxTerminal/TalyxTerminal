/* An object before its first run: the run to come, #1, with what it will use (each line opens where it is set; what it still needs in orange),
   and under it the ways to start that the object offers (a factor's styles as formulas). The same on every kind of object. */
import { t } from "../i18n";
import { type ReactNode } from "react";
import { TxButton } from "@talyx/sdk";
import "./firstrun.css";

export interface FirstLine {label: string; value: ReactNode; /** Still needed before the run can start. */ missing?: boolean; onClick?(): void}

export function FirstRun({lines, children}: {lines: FirstLine[]; children?: ReactNode}) {
  return <div className="qx-first">
    <span className="qx-first-n" aria-hidden="true">#1</span>
    <dl className="qx-first-lines" aria-label={t("The first run")}>
      {lines.map(line => <div key={line.label} className="qx-first-line" data-missing={line.missing ? "" : undefined}>
        <dt>{line.label}</dt>
        <dd>{line.onClick ? <TxButton variant="row" size="sm" onClick={line.onClick}><span>{line.value}</span></TxButton> : <span>{line.value}</span>}</dd>
      </div>)}
    </dl>
    {children && <div className="qx-first-more">{children}</div>}
  </div>;
}

/** The same lines on Results, where nothing they open is: each goes back to Work first, then opens its place there once Work is drawn. */
export const viaWork = (lines: FirstLine[], toWork: () => void): FirstLine[] =>
  lines.map(line => line.onClick ? {...line, onClick: () => {toWork(); setTimeout(line.onClick!, 0);}} : line);

/** What Results says before the first run: the run to come, and that its Results come here. */
export function NoResultsYet({lines, toWork}: {lines: FirstLine[]; toWork(): void}) {
  return <FirstRun lines={viaWork(lines, toWork)}><p className="qx-first-note">{t("The Results of #1 come here when it finishes.")}</p></FirstRun>;
}

/** Ready-made starts, a row each: the name and what it is (a formula, set in its own colours). */
export function Starters({title, items, onPick}: {title: string; items: {id: string; name: string; body: ReactNode; title?: string}[]; onPick(id: string): void}) {
  if (!items.length) return null;
  return <section className="qx-starters" aria-label={title}>
    <h3>{title}</h3>
    <ul>{items.map(item => <li key={item.id}><TxButton variant="row" size="sm" title={item.title} onClick={() => onPick(item.id)}>
      <b>{item.name}</b><code>{item.body}</code></TxButton></li>)}</ul>
  </section>;
}
