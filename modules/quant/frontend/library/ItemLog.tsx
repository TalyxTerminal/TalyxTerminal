/* The Production log tab: every period, newest first; a failed one opens its traceback, the last five failed tries keep their log (library.js logTab). */
import { t } from "../i18n";
import { periodTiming } from "./Production";
import { useEffect, useState } from "react";
import { TxButton, TxFailure, TxSkeleton, TxTable, TxTd, TxTh } from "@talyx/sdk";
import { at, mdy } from "./format";
import type { ItemVersion, LibraryApi, LogView } from "./api";

/** A period's state as plain coloured text, as the library's table writes it. */
const WORD: Record<string, {tone: "fix" | "watch" | "ok"; text: string}> = {ok: {tone: "ok", text: t("On time")}, late: {tone: "watch", text: t("Late")}, fail: {tone: "fix", text: t("Failed")}, miss: {tone: "fix", text: t("Missing")}, up: {tone: "fix", text: t("Upstream missing")}};

/** `onRetry` is null where a retry cannot work (the version is stopped, auto-update is off, or its failure says to publish again): no Retry then. */
export function ItemLog({api, version, auto, onRetry, onOpenCode}: {api: LibraryApi; version: ItemVersion; auto: boolean; onRetry: (() => void) | null; onOpenCode(file: string, line: number): void}) {
  const [log, setLog] = useState<LogView | null>(null);
  const [open, setOpen] = useState<Set<number>>(new Set());
  const [problem, setProblem] = useState<string | null>(null);
  const [reads, setReads] = useState(0);
  useEffect(() => {
    setProblem(null);
    void api.log(version.versionId).then(setLog, reason => {setLog(null); setProblem(reason instanceof Error ? reason.message : String(reason));});
  }, [version.versionId, version.cursor, version.count, reads]);
  if (problem !== null) return <div className="lb-pad"><TxFailure title={t("The production log could not be read")} message={problem} actions={<TxButton size="sm" onClick={() => setReads(count => count + 1)}>{t("Read again")}</TxButton>} /></div>;
  if (log === null) return <div className="lb-pad"><TxSkeleton width="40%" /></div>;
  if (log.periods.length === 0) return <div className="lb-calm">{version.state === "stopped" || version.state === "archived" ? t("Stopped before its first period") : auto ? t("The first period comes on the next close") : t("Auto-update is off; no periods yet")}</div>;
  const rows = [...log.periods].sort((a, b) => b.label - a.label);
  const failure = (label: number) => log.failures.find(item => item.label === label) ?? log.failures[0];
  return <div className="lb-pad lb-vtab">
    <TxTable className="lb-log"><colgroup><col style={{width: 104}} /><col style={{width: 136}} /><col style={{width: 136}} /><col style={{width: 136}} /><col style={{width: 60}} /><col /></colgroup>
      <thead><tr><TxTh>{t("Period")}</TxTh><TxTh>{t("Due")}</TxTh><TxTh>{t("Ready")}</TxTh><TxTh>{t("State")}</TxTh><TxTh align="right">{t("Took")}</TxTh><TxTh>{t("Note")}</TxTh></tr></thead>
      <tbody>{rows.map(period => {
        const word = WORD[period.result] ?? WORD.ok, tries = failure(period.label), opened = open.has(period.label), failed = period.result === "fail";
        return [<tr key={period.label} className={failed ? "lb-click" : undefined} onClick={failed ? () => setOpen(current => {const next = new Set(current); if (!next.delete(period.label)) next.add(period.label); return next;}) : undefined}>
          <TxTd className="lb-t2">{mdy(period.label)}</TxTd><TxTd className="lb-t3">{period.due_at ? at(period.due_at) : ""}</TxTd><TxTd className="lb-t2">{period.ready_at ? at(period.ready_at) : failed ? t("{tries} tries", { tries: period.tries }) : ""}</TxTd>
          <TxTd><span className="lb-state" data-tone={word.tone}>{word.text}</span></TxTd><TxTd align="right" className="lb-num">{period.took != null ? `${period.took < 10 ? period.took.toFixed(1) : Math.round(period.took)} s` : ""}</TxTd>
          <TxTd className="lb-why-cell">{period.ready_at && period.due_at ? `${periodTiming(period)}. ` : ""}{period.why ?? ""}</TxTd></tr>,
          opened && tries ? <tr key={`${period.label}-tb`} className="lb-tb"><td colSpan={6}><pre>{[tries.traceback, tries.output ? t("\nOutput before the error\n{output}", { output: tries.output }) : ""].join("")}</pre>
            <div className="lb-lnks">{onRetry && <TxButton size="sm" variant="primary" onClick={onRetry}>{t("Retry now")}</TxButton>}{tries.location && <TxButton size="sm" onClick={() => onOpenCode(tries.location!.file, tries.location!.line)}>{t("Open {file} line {line}", { file: tries.location.file, line: tries.location.line })}</TxButton>}</div></td></tr> : null];
      })}</tbody></TxTable></div>;
}
