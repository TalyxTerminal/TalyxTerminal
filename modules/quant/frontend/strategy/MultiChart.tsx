/* Several instruments' main picture: the backend's figure of the run's equity against the benchmark (and the run drawn beside it), with the drawdown under it. */

import { t } from "../i18n";
import { withPicking, type Picking } from "./picking";
import { Plot, type PlotFigure } from "../chart/Plot";

export function MultiChart({figure, why, picking}: {figure: PlotFigure | null | undefined; why?: string; picking?: Picking[]}) {
  if (!figure) return <p className="fx-empty" role="status">{why ?? t("This chart was not built")}</p>;
  return <div className="fx-rch" data-size="l"><Plot figure={withPicking(figure, picking)} ariaLabel={t("Equity and drawdown")} /></div>;
}
