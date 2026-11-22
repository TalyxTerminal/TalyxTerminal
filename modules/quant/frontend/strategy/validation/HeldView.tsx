/* The held-out check: the strategy's own equity with the held-out part shaded, and the in-sample and held-out numbers side by side. */
import { t } from "../../i18n";
import {TxRegionHead, TxTag, TxIcon} from "@talyx/sdk";
import {Fig} from "./Fig";
import type {Figure} from "./types";

export interface HeldRow {name: string; inSample: string; heldOut: string}
export function HeldView({number, of, figure, rows}: {number: number; of: number; figure?: Figure | null; rows: HeldRow[]}) {
  return <div className="sv-held-view">
    <div className="sv-mid">
      <div className="sv-chart"><TxRegionHead title={t("#{of}, then held out #{number}", { of, number })} sub={t("same code and values; the held-out part shaded")} /><Fig fig={figure} label={t("In-sample then held-out equity")} /></div>
      <div className="sv-side"><TxRegionHead title={t("Side by side")}><TxTag><TxIcon name="lock" />{t("Held-out")}</TxTag></TxRegionHead>
        <table className="sv-table"><thead><tr><th className="l" /><th>{t("In-sample #{of}", { of })}</th><th>{t("Held out #{number}", { number })}</th></tr></thead>
          <tbody>{rows.map(row => <tr key={row.name}><td className="l">{row.name}</td><td className="dim">{row.inSample}</td><td>{row.heldOut}</td></tr>)}</tbody></table></div>
    </div>
  </div>;
}
