/* A version's production: one cell a period (click one to see it), what that period is, and the production queue popover (library.js production, perDetail, prodPop). */
import { t } from "../i18n";
import { TxButton, TxPeriods, type TxPeriodState } from "@talyx/sdk";
import { at, dur, hm, md, mdy, nf } from "./format";
import type { Period, Production } from "./api";

const state = (result: string): TxPeriodState => result === "ok" ? "ok" : result === "late" ? "late" : "miss";
const WORD: Record<string, {tone: "fix" | "watch" | "ok" | "muted"; text: string}> = {ok: {tone: "ok", text: t("On time")}, late: {tone: "watch", text: t("Late")}, fail: {tone: "fix", text: t("Failed")}, miss: {tone: "fix", text: t("Missing")}, up: {tone: "fix", text: t("Upstream missing")}};

export function periodTiming(period: Period): string {
  if (period.ready_at == null || period.due_at == null) return "";
  const elapsed = period.ready_at - period.due_at;
  return elapsed === 0 ? t("at due time") : elapsed > 0 ? t("{time} late", { time: dur(elapsed) }) : t("{time} early", { time: dur(-elapsed) });
}

export function ProductionStrip({periods, selected, onSelect}: {periods: Period[]; selected: number; onSelect(index: number): void}) {
  return <TxPeriods large items={periods.map(period => state(period.result))} selected={selected} onSelect={onSelect}
    titles={periods.map(period => `${md(period.label)}: ${WORD[period.result]?.text ?? period.result}`)} />;
}
/** What a production cell stands for, as the caption counts them (library.js): trading days for a daily item, weeks, months, else periods. */
export const periodUnit = (freq: string | null | undefined, count = 2) => {
  switch ((freq ?? "").toLowerCase()) {
    case "daily": return count === 1 ? t("trading day") : t("trading days");
    case "weekly": return count === 1 ? t("week") : t("weeks");
    case "monthly": return count === 1 ? t("month") : t("months");
    default: return count === 1 ? t("period") : t("periods");
  }
};
export const counts = (periods: Period[]) => ({ok: periods.filter(p => p.result === "ok").length, late: periods.filter(p => p.result === "late").length, failed: periods.filter(p => p.result === "fail").length,
  missing: periods.filter(p => p.result === "miss" || p.result === "up").length});

/** One period: when it was due and ready, how many names have a value (the day's universe size is not recorded, so no "of N"), why not. */
export function PeriodDetail({period, onValues, onLog, onRetry}: {period: Period; onValues(): void; onLog(): void; onRetry: (() => void) | null}) {
  const word = WORD[period.result] ?? WORD.ok;
  const made = period.result === "ok" || period.result === "late";
  const ready = period.ready_at ? `${at(period.ready_at)}${period.due_at ? `, ${periodTiming(period)}` : ""}` : period.result === "fail" ? (period.tries === 1 ? t("{count} try", { count: period.tries }) : t("{count} tries", { count: period.tries })) : "—";
  return <div className="lb-pdet">
    <b>{mdy(period.label)}</b>
    <div><div className="lb-kv"><span><span className="lb-state" data-tone={word.tone}>{word.text}</span></span><span><span>{t("due")}</span>{period.due_at ? at(period.due_at) : "—"}</span><span><span>{t("ready")}</span>{ready}</span>
      <span><span>{t("names with a value")}</span>{made ? nf(period.names ?? 0, 0) : "0"}</span></div>
      {period.why && <div className="lb-why3">{period.why}</div>}</div>
    <div className="lb-acts">{made && <TxButton size="sm" variant="quiet" onClick={onValues}>{t("Values")}</TxButton>}<TxButton size="sm" variant="quiet" onClick={onLog}>{t("Log")}</TxButton>
      {(period.result === "fail" || period.result === "miss") && onRetry && <TxButton size="sm" onClick={onRetry}>{t("Retry")}</TxButton>}</div>
  </div>;
}

/** The popover under "Next round": what ran last, what comes next, in what order. */
export function ProductionQueue({production}: {production: Production | null}) {
  if (production === null || (production.producing === 0 && production.lastRound === null && production.next === null)) return <div className="lb-queue"><div className="lb-t3">{t("Nothing producing yet")}</div></div>;
  const retry = production.retries[0];
  return <div className="lb-queue">
    {production.lastRound && <div className="lb-r"><span>{t("Last round")}</span><span>{t("{at}, {at2}: {versions} versions, {took} s", { at: md(production.lastRound.at), at2: hm(production.lastRound.at), versions: production.lastRound.versions, took: production.lastRound.took })}</span></div>}
    <div className="lb-r"><span>{t("Next")}</span><span>{[retry ? t("{name} v{version} retry {time}", { name: retry.name, version: retry.version, time: hm(retry.at) }) : null, production.next ? (production.next.versions === 1 ? t("{date}, {time}, {count} version", { date: md(production.next.at), time: hm(production.next.at), count: production.next.versions }) : t("{date}, {time}, {count} versions", { date: md(production.next.at), time: hm(production.next.at), count: production.next.versions })) : null].filter(Boolean).join("; ") || "—"}</span></div>
    {production.order.length > 0 && <div className="lb-r"><span>{t.c("sequence", "Order")}</span><span>{t("factors, then models and allocations")}</span></div>}
  </div>;
}
