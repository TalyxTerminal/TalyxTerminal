/** The band under the tool row when the notebook was opened from a run (the draft's): which object, which run, its period, how far its data goes, and the way back to it. */
import { t } from "../i18n";
import { TxButton, TxIcon, TxSpacer } from "@talyx/sdk";
import { hm, md, my } from "../shared/format";
import "./notebook.css";

/** When the run was made, on this computer's clock (as the run lists show it), not UTC. */
/** When the run was made, in UTC as every time in Quant. */
export const madeWhen = (when: Date) => `${md(when)}, ${hm(when)} UTC`;

export interface OpenedFrom {
  objectId: string; runId: string; objectName?: string; number?: number; deleted?: boolean;
  kind?: string; at?: number; dataFrom?: number; dataTo?: number; heldOutFrom?: number;
}

export function RunBand({ from, onOpen }: { from: OpenedFrom; onOpen(): void }) {
  const when = from.at === undefined ? null : new Date(from.at);
  const period = from.dataFrom !== undefined && from.dataTo !== undefined
    ? `${my(new Date(from.dataFrom))} – ${my(new Date((from.heldOutFrom ?? from.dataTo + 86400000) - 86400000))}${from.heldOutFrom === undefined ? "" : t(", held out from {date}", { date: my(new Date(from.heldOutFrom)) })}` : null;
  return <div className="qv-notebook-band"><TxIcon name="flask" />
    <span>{t.rich("Opened from {objectName}", { objectName: <b>{from.objectName ?? t("a run")}</b> })}{from.kind === undefined ? "" : ` · ${from.kind}`}{from.number === undefined ? "" : <>{t.rich(" · run {number}", { number: <b>#{from.number}</b> })}</>}{when === null ? "" : `, ${madeWhen(when)}`}</span>
    {period !== null && <><span className="qv-notebook-sep" /><span>{period}</span></>}
    {from.dataTo !== undefined && <><span className="qv-notebook-sep" /><span>{t.rich("data to {dataTo}", { dataTo: <b>{md(new Date(from.dataTo))}</b> })}</span></>}
    <TxSpacer />
    {from.deleted ? <span>{(from.number === undefined ? t("Run was deleted") : t("Run #{number} was deleted", { number: from.number }))}</span> : <TxButton variant="quiet" size="sm" onClick={onOpen}>{t("Open the run")}</TxButton>}
  </div>;
}
