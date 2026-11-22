/** The held-out check as the draft's Work shows it: the in-sample and the held-out numbers side by side with 60/40 and equal weight over the same days, the growth of 1 over the held-out stretch and
    every period of it, then the checks of the run it scored (in-sample only). */
import { t } from "../i18n";
import { TxChecks, TxRegionHead } from "@talyx/sdk";
import { dayText } from "../factor/format";
import { nf, pc, pct, pp } from "../shared/format";
import type { AllocationRun, Check, Metrics, Work } from "./api";
import { Fig } from "./Results";
import { allocationChecks } from "./Work";

const cells = (m: Metrics, ownVs = true) => [pc(m.cagr, 2), pct(m.vol, 2), m.sharpe == null ? "" : nf(m.sharpe, 2), pc(m.maxDrawdown, 1), ownVs && m.vs6040 != null ? pp(m.vs6040, 2) : "", ownVs && m.vsEqualWeight != null ? pp(m.vsEqualWeight, 2) : "", ownVs ? pct(m.turnover, 0) : ""];

export function HeldView({ run, work, base, baseWork, onGo }: { run: AllocationRun; work: Work; base: AllocationRun | null; baseWork: Work | null; onGo: (where: string) => void }) {
  const hold = work.holdout!;
  const rows: { label: string; values: string[]; cls?: string }[] = [];
  if (base?.metrics) rows.push({ label: (baseWork ? t("#{number}, in-sample, {from} – {to}", { number: base.number, from: dayText(baseWork.from), to: dayText(baseWork.to) }) : t("#{number}, in-sample", { number: base.number })), values: cells(base.metrics) });
  if (run.metrics) rows.push({ label: t("#{number}, held out, {from} – {to}", { number: run.number, from: dayText(hold.from), to: dayText(hold.to) }), values: cells(run.metrics), cls: "al-selrow" });
  if (hold.rows.sixty) rows.push({ label: t("60/40, held out"), values: cells(hold.rows.sixty, false) });
  rows.push({ label: t("Equal weight, held out"), values: cells(hold.rows.equal, false) });
  return <>
    <section className="al-rsec"><TxRegionHead title={t("In-sample and held out")} sub={(base ? t("#{number}'s settings, scored once on the held-out period", { number: base.number }) : t("the settings, scored once on the held-out period"))} />
      <div className="qv-pad"><table className="qv-table"><thead><tr><th className="l" /><th>{t("CAGR")}</th><th>{t("Volatility")}</th><th>{t("Sharpe")}</th><th>{t("Max DD")}</th><th>{t("vs 60/40")}</th><th>{t("vs EW")}</th><th>{t("Turnover")}</th></tr></thead>
        <tbody>{rows.map(row => <tr key={row.label} className={row.cls}><td className="l">{row.label}</td>{row.values.map((value, index) => <td key={index}>{value}</td>)}</tr>)}</tbody></table></div></section>
    <div className="al-grid2">
      <section className="al-rsec"><TxRegionHead title={t("Growth of 1, held out")} /><div className="qv-pad"><Fig fig={hold.growth} label={t("Growth of 1, held out")} /></div></section>
      <section className="al-rsec"><TxRegionHead title={t("Every period, held out")} /><div className="qv-pad"><Fig fig={work.periodsFigure} label={t("Every period, held out")} /></div></section></div>
    {baseWork && <div className="al-checks"><TxChecks scope={t("in-sample")} groups={[{ items: allocationChecks(baseWork.checks as Check[], onGo) }]} /></div>}
  </>;
}
