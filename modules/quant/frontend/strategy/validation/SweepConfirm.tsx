/* Asked for when a sweep is expected to take more than ten minutes: what it is and how the machine will run it. */
import {TxButton, TxDialog} from "@talyx/sdk";
import {span} from "./format";
import type {Estimate} from "./api";

export const SLOW_SECONDS = 600;
export const isSlow = (estimate: Estimate | null | undefined) => !!estimate && estimate.seconds > SLOW_SECONDS;
export const confirmText = (estimate: Estimate) => `${estimate.atOnce} at a time on this machine, about ${span(estimate.seconds)}.`;

export function SweepConfirm({open, estimate, label = "Sweep", onCancel, onConfirm}: {open: boolean; estimate: Estimate | null; label?: string; onCancel(): void; onConfirm(): void}) {
  if (!estimate) return null;
  return <TxDialog open={open} onOpenChange={value => { if (!value) onCancel(); }} title={`${label} ${estimate.combinations} combinations`}
    footer={<><TxButton size="sm" onClick={onCancel}>Cancel</TxButton><TxButton variant="primary" size="sm" onClick={onConfirm}>{label}</TxButton></>}>
    <p className="sv-confirm">{confirmText(estimate)}</p>
  </TxDialog>;
}
