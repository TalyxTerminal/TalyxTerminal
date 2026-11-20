/* Colours the canvas needs as concrete values, read from the design tokens. Up and down follow the user's choice. */
import { tokenValue } from "@talyx/sdk";
import type { Appearance } from "../wire";

export interface ChartColors {
  background: string; text: string; textMuted: string; grid: string; separator: string; crosshair: string; crosshairLabel: string;
  up: string; down: string; borderUp: string; borderDown: string; wickUp: string; wickDown: string;
  /** Grid lines across time and across price ("transparent" when hidden). */
  vertGrid: string; horzGrid: string;
  /** The symbol's name drawn faintly behind the bars. */
  watermark: string;
  accent: string; handleFill: string; series: string[]; font: string; fontSize: number;
}

const resolved = (name: string): string => {
  const value = tokenValue(name);
  const alias = /^var\((--[\w-]+)\)$/.exec(value);
  return alias ? resolved(alias[1]) : value;
};

export function chartColors(): ChartColors {
  return {
    background: resolved("--tx-panel"), text: resolved("--tx-text-2"), textMuted: resolved("--tx-text-3"),
    grid: resolved("--tx-chart-grid"), separator: resolved("--tx-line-2"), crosshair: resolved("--tx-chart-crosshair"),
    crosshairLabel: resolved("--tx-raised"), up: resolved("--tx-up"), down: resolved("--tx-down"), accent: resolved("--tx-ember"),
    borderUp: resolved("--tx-up"), borderDown: resolved("--tx-down"), wickUp: resolved("--tx-up"), wickDown: resolved("--tx-down"),
    vertGrid: resolved("--tx-chart-grid"), horzGrid: resolved("--tx-chart-grid"), watermark: resolved("--tx-white-06"),
    handleFill: resolved("--tx-panel"), series: [1, 2, 3, 4, 5].map(index => resolved(`--tx-series-${index}`)),
    font: resolved("--tx-font"), fontSize: parseFloat(resolved("--tx-size-xs")),
  };
}

/** A comparison symbol's colour when it names none: the data palette from its second colour (the first is the
    indicators' default), by the comparison's place on the chart. */
export function comparisonColor(index: number): string {
  const series = chartColors().series;
  return series[(index + 1) % series.length];
}

/** The colour a newly compared symbol keeps (TradingView's way): the first of those colours no comparison has yet. */
export function newComparisonColor(taken: string[]): string {
  const series = chartColors().series.slice(1);
  return series.find(color => !taken.includes(color)) ?? comparisonColor(taken.length);
}

/** The theme's colours with the chart's own settings over them (Chart settings › Symbol and Canvas). */
export function chartColorsWith(appearance: Appearance | undefined): ChartColors {
  const base = chartColors(), look = appearance ?? {};
  return {
    ...base, up: look.up ?? base.up, down: look.down ?? base.down, borderUp: look.borderUp ?? base.borderUp, borderDown: look.borderDown ?? base.borderDown,
    wickUp: look.wickUp ?? base.wickUp, wickDown: look.wickDown ?? base.wickDown, background: look.background ?? base.background,
    vertGrid: look.vertGrid === false ? "transparent" : base.vertGrid, horzGrid: look.horzGrid === false ? "transparent" : base.horzGrid,
  };
}

/** Canvas colors are concrete values. Re-read them when the workspace applies its direction setting. */
export function watchUpColor(update: () => void): () => void {
  const observer = new MutationObserver(update);
  observer.observe(document.documentElement, { attributes: true, attributeFilter: ["data-up-color"] });
  return () => observer.disconnect();
}

/** A hex colour at an opacity between 0 and 1; other CSS colours carry their own opacity and stay as they are. */
export function withAlpha(color: string, alpha: number): string {
  if (!color.startsWith("#")) return color;
  const hex = color.replace("#", "");
  const full = hex.length === 3 ? [...hex].map(channel => channel + channel).join("") : hex.slice(0, 6);
  const [red, green, blue] = [0, 2, 4].map(offset => parseInt(full.slice(offset, offset + 2), 16));
  return `rgba(${red}, ${green}, ${blue}, ${alpha})`;
}
