/* Asked for only when what is about to start is expected to take more than ten minutes, saying how long; nothing else stops a sweep or a batch. */
import { t } from "../i18n";
import {TxButton, TxDialog} from "@talyx/sdk";
import {span} from "../strategy/validation/format";

export const LONG_SECONDS = 600;
export const isLong = (seconds: number | null | undefined) => seconds != null && seconds > LONG_SECONDS;

/** The seconds `count` runs are expected to take one after another, from the newest completed one of `runs` (single runs, newest first); null before there is one. */
export function sweepSeconds(runs: {state: string; seconds: number | null}[], count: number): number | null {
  const last = runs.find(run => run.state === "completed" && run.seconds != null);
  return last ? last.seconds! * count : null;
}

export function LongRunConfirm({title, seconds, label, onCancel, onConfirm}: {title: string; seconds: number; label: string; onCancel(): void; onConfirm(): void}) {
  return <TxDialog open onOpenChange={open => {if (!open) onCancel();}} title={title}
    footer={<><TxButton size="sm" onClick={onCancel}>{t("Cancel")}</TxButton><TxButton variant="primary" size="sm" onClick={onConfirm}>{label}</TxButton></>}>
    <p className="sv-confirm">{t("About {seconds} on this machine.", { seconds: span(seconds) })}</p>
  </TxDialog>;
}
