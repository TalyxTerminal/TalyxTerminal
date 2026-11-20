/** The band under the tool row when the notebook was opened from a run (the draft's): which object, which run, its period, how far its data goes, and the way back to it. */
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
    ? `${my(new Date(from.dataFrom))} – ${my(new Date((from.heldOutFrom ?? from.dataTo + 86400000) - 86400000))}${from.heldOutFrom === undefined ? "" : `, held out from ${my(new Date(from.heldOutFrom))}`}` : null;
  return <div className="qv-notebook-band"><TxIcon name="flask" />
    <span>Opened from <b>{from.objectName ?? "a run"}</b>{from.kind === undefined ? "" : ` · ${from.kind}`}{from.number === undefined ? "" : <> · run <b>#{from.number}</b></>}{when === null ? "" : `, ${madeWhen(when)}`}</span>
    {period !== null && <><span className="qv-notebook-sep" /><span>{period}</span></>}
    {from.dataTo !== undefined && <><span className="qv-notebook-sep" /><span>data to <b>{md(new Date(from.dataTo))}</b></span></>}
    <TxSpacer />
    {from.deleted ? <span>Run {from.number === undefined ? "" : `#${from.number} `}was deleted</span> : <TxButton variant="quiet" size="sm" onClick={onOpen}>Open the run</TxButton>}
  </div>;
}
