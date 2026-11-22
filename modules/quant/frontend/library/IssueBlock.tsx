/* Why an item is in the state it is in, under its head, only when there is something to say (library.js issBlock): what, since when, the rows that explain it and what to open. */
import { t } from "../i18n";
import { TxButton } from "@talyx/sdk";
import { at } from "./format";
import type { Issue } from "./api";

const TONE: Record<string, "fix" | "watch" | "calm"> = {fail: "fix", miss: "fix", up: "fix", weak: "watch", drift: "watch", late: "calm"};

export function IssueBlock({issue, onOpenCode, onLog}: {issue: Issue; onOpenCode(file: string, line: number): void; onLog(): void}) {
  const tone = TONE[issue.kind] ?? "calm";
  const source = issue.links.find(link => link.kind === "source" && link.file);
  return <div className="lb-iss" data-tone={tone}>
    <div className="lb-iss-head"><b>{issue.title}</b><span className="lb-t3">{issue.note ? issue.note : t("since {since}", { since: at(issue.since) })}</span></div>
    <div className="lb-why">{issue.rows.map((row, index) => [<span key={`l${index}`}>{row.label}</span>, <span key={`t${index}`}>{row.text}</span>])}</div>
    {(source || issue.kind === "fail") && <div className="lb-lnks">
      {issue.kind === "fail" && <TxButton size="sm" variant="quiet" onClick={onLog}>{t("Log of this try")}</TxButton>}
      {source && <TxButton size="sm" variant="quiet" onClick={() => onOpenCode(source.file!, source.line ?? 1)}>{t("Open {file} line {line}", { file: source.file ?? "", line: source.line ?? "" })}</TxButton>}
    </div>}
  </div>;
}
