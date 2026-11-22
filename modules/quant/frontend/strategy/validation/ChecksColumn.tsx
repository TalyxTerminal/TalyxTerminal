/* The checks of a run: each with its value and what it needs, the ones that did not pass marked, and the ones not run with a button that runs them. */
import { t } from "../../i18n";
import {TxButton, TxIcon} from "@talyx/sdk";
import type {CheckGo, CheckRow} from "./types";

export interface CheckActions {
  validate(): void; walk(): void; sweep(): void; costs(): void; open(runId: string): void; resweep(runId: string): void; section(id: string): void;
}
/** The button label of a check that can be run, or of the one that opens what it came from. */
export function buttonFor(row: {state: string; go: CheckGo | string | null}): {label: string; run: keyof CheckActions} | null {
  const go = row.go;
  if (!go || typeof go === "string") return null;
  if (row.state === "notrun") {
    switch (go.kind) {
      case "validate": return {label: t("Validate"), run: "validate"};
      case "walk": return {label: t("Walk-forward"), run: "walk"};
      case "sweep": return {label: t("Sweep"), run: "sweep"};
      case "costs": return {label: t("Rerun at 2× costs"), run: "costs"};
      default: return null;
    }
  }
  return null;
}
function act(go: CheckGo, actions: CheckActions) {
  switch (go.kind) {
    case "validate": return actions.validate();
    case "walk": return actions.walk();
    case "sweep": return actions.sweep();
    case "costs": return actions.costs();
    case "open": return go.resweep ? actions.resweep(go.runId) : actions.open(go.runId);
    case "section": return actions.section(go.id);
  }
}
/** The "Edge of the grid" row is judged only when a sweep was read, so it is there with the others; its words are the fact. */
export function ChecksColumn({rows, actions}: {rows: CheckRow[]; actions: CheckActions}) {
  return <div className="sv-checks"><ul>{rows.map(row => {
    const button = buttonFor(row);
    return <li key={row.id} data-state={row.state}>
      <span className="sv-dot" data-state={row.state} aria-hidden="true" />
      <span className="sv-cname" title={row.title}>{row.name}</span>
      <span className="sv-cval">{row.value}{row.detail ? <span className="dim"> · {row.detail}</span> : null}</span>
      {row.need && row.state !== "notrun" && <span className="sv-cneed dim">{row.need}</span>}
      {button ? <TxButton size="sm" variant="quiet" onClick={() => act(row.go!, actions)}><TxIcon name="play" />{button.label}</TxButton>
        : row.go && row.state !== "notrun" ? <TxButton size="sm" variant="quiet" onClick={() => act(row.go!, actions)}>{row.go.kind === "open" && row.go.resweep ? t("Sweep around these") : t.c("action", "Open")}</TxButton> : null}
    </li>;
  })}</ul></div>;
}
