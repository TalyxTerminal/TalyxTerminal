/* The centred confirmation before the held-out part is used: looking at it spends it, so it is asked for once, with what the part is and whether it was seen. */
import {TxButton, TxDialog, TxIcon} from "@talyx/sdk";
import type {HeldStatus} from "./types";

const MONTHS = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"];
const md = (text: string) => { const [year, month, date] = text.split("-").map(Number); return `${MONTHS[month - 1]} ${date}, ${year}`; };
export const periodText = (status: HeldStatus) => `${md(status.from)} – ${md(status.to)}`;
/** The lines of the box after the two fixed ones: it was seen before (with the same values or not), and the part is short. */
export function warnings(status: HeldStatus): string[] {
  const out: string[] = [];
  if (status.used) out.push(status.used.same ? `#${status.used.number} already used the held-out period with these values; a second look is no longer untouched`
    : `#${status.used.number} already used the held-out period; changes made after it are judged on a period you have seen`);
  if (status.short) out.push(`The held-out part has ${status.days} trading days; its Sharpe will be noisy`);
  return out;
}

/** `what` is the kind of object being settled, as the box names it ("strategy", "allocation"). */
export function HeldOutConfirm({open, status, what = "strategy", onCancel, onConfirm}: {open: boolean; status: HeldStatus | null; what?: string; onCancel(): void; onConfirm(): void}) {
  if (!status) return null;
  return <TxDialog open={open} onOpenChange={value => { if (!value) onCancel(); }} title="Validate on the held-out period"
    footer={<><TxButton size="sm" onClick={onCancel}>Cancel</TxButton><TxButton variant="primary" size="sm" onClick={onConfirm}><TxIcon name="lock" />Validate</TxButton></>}>
    <p className="sv-confirm">Runs #{status.number}'s code and values on {periodText(status)}, the part every check has left out.</p>
    <p className="sv-confirm">Do this once, when the {what} is settled. The run is tagged Held-out; any later change judged on this period no longer counts as untouched.</p>
    {warnings(status).map(line => <p key={line} className="sv-confirm" data-warn="">{line}</p>)}
  </TxDialog>;
}
