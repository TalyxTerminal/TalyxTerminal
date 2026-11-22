import { intlLocale } from "@talyx/sdk";
/* The centred confirmation before the held-out part is used: looking at it spends it, so it is asked for once, with what the part is and whether it was seen. */
import { t } from "../../i18n";
import {TxButton, TxDialog, TxIcon} from "@talyx/sdk";
import type {HeldStatus} from "./types";

const md = (text: string) => { const [year, month, date] = text.split("-").map(Number); return new Date(Date.UTC(year, month - 1, date)).toLocaleDateString(intlLocale(), { timeZone: "UTC", month: "short", day: "numeric", year: "numeric" }); };
export const periodText = (status: HeldStatus) => `${md(status.from)} – ${md(status.to)}`;
/** The line of the box after the fixed one: which run used the held-out period before, as a fact. */
export function warnings(status: HeldStatus): string[] {
  if (!status.used) return [];
  return [status.used.same ? t("#{run} already ran on the held-out period with these values", { run: String(status.used.number) }) : t("#{run} already ran on the held-out period", { run: String(status.used.number) })];
}

export function HeldOutConfirm({open, status, onCancel, onConfirm}: {open: boolean; status: HeldStatus | null; onCancel(): void; onConfirm(): void}) {
  if (!status) return null;
  return <TxDialog open={open} onOpenChange={value => { if (!value) onCancel(); }} title={t("Validate on the held-out period")}
    footer={<><TxButton size="sm" onClick={onCancel}>{t("Cancel")}</TxButton><TxButton variant="primary" size="sm" onClick={onConfirm}><TxIcon name="lock" />{t("Validate")}</TxButton></>}>
    <p className="sv-confirm">{t("Runs #{number}'s code and values on {status}, the part every check has left out.", { number: status.number ?? "", status: periodText(status) })}</p>
    {warnings(status).map(line => <p key={line} className="sv-confirm" data-warn="">{line}</p>)}
  </TxDialog>;
}
