import { intlLocale } from "@talyx/sdk";
/** Cells that show an object's representative run, its key number and its checks; the objects table and Home's recent list use them. */
import { t } from "../i18n";
import { TxIcon, TxTag } from "@talyx/sdk";
import { nf, pc, sgn } from "../factor/format";
import type { ObjectRow } from "../objects";

const two = (value: number) => String(value).padStart(2, "0");
/** When an object last changed, as the draft writes it (objects-data.js whenTxt): "today 08:12", "Nov 3, 18:20" within six days, else "Jun 30, 2025". */
export function whenText(ms: number, now = Date.now()): string {
  const day = new Date(ms), clock = `${two(day.getUTCHours())}:${two(day.getUTCMinutes())} UTC`;  // UTC, as every time in Quant
  if (day.toISOString().slice(0, 10) === new Date(now).toISOString().slice(0, 10)) return t("today {clock}", { clock });
  if (now - ms < 6 * 86_400_000) return `${day.toLocaleDateString(intlLocale(), { timeZone: "UTC", month: "short", day: "numeric" })}, ${clock}`;
  return day.toLocaleDateString(intlLocale(), { timeZone: "UTC", month: "short", day: "numeric", year: "numeric" });
}
const num = (row: ObjectRow, key: string) => {const value = row.keyNumbers?.[key]; return typeof value === "number" ? value : null;};
const horizon = (row: ObjectRow) => typeof row.keyNumbers?.horizon === "string" ? row.keyNumbers.horizon : "";

export function RepCell({ o }: { o: ObjectRow }) {
  const rep = o.representative;
  if (o.kind === "notebook") return <span className="qv-objects-dim">{o.reads ?? ""}</span>;
  if (rep.how === "none") return <span><span className="qv-objects-fix">{t("No successful run")}</span>{rep.note ? <> <span className="qv-objects-dim qv-objects-why" title={rep.note}>{rep.note}</span></> : null}</span>;
  return <span className="qv-objects-rep"><span className="num">#{rep.number}</span>{rep.how === "published" ? <>{rep.version && <TxTag tone="version">{rep.version}</TxTag>}<span className="qv-objects-dim">{t("published")}</span></>
    : rep.how === "starred" ? <><TxIcon name="star" className="qv-objects-star" /><span className="qv-objects-dim">{t("starred")}</span></> : <span className="qv-objects-dim">{t("latest successful")}</span>}</span>;
}
/** The key number of the representative run (home.md): factor Rank IC, model test Rank IC, allocation Sharpe, strategy Sharpe and return a year. */
export function KeyNumberCell({ o }: { o: ObjectRow }) {
  const signed = (v: number, text: string) => <b className={v >= 0 ? "qv-objects-up" : "qv-objects-down"}>{text}</b>;
  if (o.kind === "factor") { const v = num(o, "rank_ic"); return v === null ? <span className="qv-objects-dim">—</span> : <span className="qv-objects-kn"><span>{t("Rank IC {o}", { o: horizon(o) })}</span>{signed(v, sgn(v, 4))}</span>; }
  if (o.kind === "model") { const v = num(o, "test_rank_ic"); return v === null ? <span className="qv-objects-dim">—</span> : <span className="qv-objects-kn"><span>{t("Test Rank IC {o}", { o: horizon(o) })}</span>{signed(v, sgn(v, 4))}</span>; }
  if (o.kind === "allocation") { const v = num(o, "sharpe"); return v === null ? <span className="qv-objects-dim">—</span> : <span className="qv-objects-kn"><span>{t("Sharpe")}</span><b>{nf(v, 2)}</b></span>; }
  if (o.kind === "strategy") {
    const sharpe = num(o, "sharpe"), ret = num(o, "return_a_year");
    if (sharpe === null && ret === null) return <span className="qv-objects-dim">—</span>;
    return <span className="qv-objects-kn">{sharpe !== null && <><span>{t("Sharpe")}</span><b>{nf(sharpe, 2)}</b></>}{ret !== null && <>{signed(ret, pc(ret, 1))}<span>{t("a year")}</span></>}</span>;
  }
  if (o.kind === "notebook" && typeof o.cells === "number") return <span className="qv-objects-kn"><span>{o.cells === 1 ? t("1 cell") : t("{count} cells", { count: o.cells })}</span></span>;
  return <span className="qv-objects-dim">—</span>;
}
export function ChecksCell({ o }: { o: ObjectRow }) {
  if (!o.checks) return null;
  const { passed, total } = o.checks;
  return <span className="qv-objects-checks" data-ok={passed === total ? "" : undefined} title={t("{passed} of {total} checks passed", { passed, total })}>{passed !== total && <i />}{passed}/{total}</span>;
}
