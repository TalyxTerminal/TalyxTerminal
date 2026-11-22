/* A chart the backend drew, in its box; what could not be drawn says why. */

import { t } from "../../i18n";
import type {Figure} from "./types";
import { Plot, type PlotPoint } from "../../chart/Plot";

export function Fig({fig, label, onPoint}: {fig?: Figure | null; label: string; onPoint?(point: PlotPoint): void}) {
  if (!fig) return null;
  if ("unavailable" in fig) return <p className="sv-why" role="status">{t("{label} could not be drawn: {unavailable}", { label, unavailable: fig.unavailable })}</p>;
  return <div className="sv-fig"><Plot figure={fig} ariaLabel={label} onPoint={onPoint} /></div>;
}
