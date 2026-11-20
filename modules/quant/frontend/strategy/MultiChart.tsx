/* Several instruments' main picture: the backend's figure of the run's equity against the benchmark (and the run drawn beside it), with the drawdown under it. */

import { withPicking, type Picking } from "./picking";
import { Plot, type PlotFigure } from "../chart/Plot";

export function MultiChart({figure, why, picking}: {figure: PlotFigure | null | undefined; why?: string; picking?: Picking[]}) {
  if (!figure) return <p className="fx-empty" role="status">{why ?? "This chart was not built"}</p>;
  return <div className="fx-rch" data-size="l"><Plot figure={withPicking(figure, picking)} ariaLabel="Equity and drawdown" /></div>;
}
