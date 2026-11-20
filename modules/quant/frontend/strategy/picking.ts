/* The span of a run that a model's published values were picked on: a light band over the equity picture and a small label (#31b). */


export interface Picking { name: string; to: string | null }

/** The figure with the picking spans drawn: from the first day to the day the model was picked on the data to (the whole run when that day is unknown). */
export function withPicking(figure: PlotFigure, picking: Picking[] | undefined): PlotFigure {
  if (!picking?.length) return figure;
  const first = (figure.data as {x?: unknown[]}[]).find(trace => Array.isArray(trace.x) && trace.x.length)?.x?.[0];
  const last = (figure.data as {x?: unknown[]}[]).find(trace => Array.isArray(trace.x) && trace.x.length)?.x?.at(-1);
  if (first === undefined) return figure;
  const layout = figure.layout ?? {};
  const spans = picking.map(item => ({...item, end: item.to !== null && String(item.to) < String(last) ? item.to : last}));
  const to = spans.reduce((best, item) => String(item.end) > String(best) ? item.end : best, spans[0].end);
  return {...figure, layout: {...layout,
    shapes: [...((layout.shapes as object[]) ?? []), {type: "rect", xref: "x", yref: "paper", x0: first, x1: to, y0: 0, y1: 1, layer: "below", line: {width: 0}, fillcolor: "rgba(128,128,128,0.12)", meta: {role: "detail"}}],
    annotations: [...((layout.annotations as object[]) ?? []), {xref: "x", yref: "paper", x: first, y: 1, xanchor: "left", yanchor: "top", showarrow: false,
      text: "Used to pick " + picking.map(item => item.name).join(", "), name: "picking", font: {size: 11}}]}};
}

import type { PlotFigure } from "../chart/Plot";