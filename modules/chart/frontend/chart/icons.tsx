/* Chart's own glyphs (TALYX line style, 18px, TradingView's sizes): chart types, drawing tools and the few toolbar
   actions the shared TxIcon set has no glyph for. Names never repeat a TxIcon name; for those, TxIcon is used. */
import type { ReactNode, SVGProps } from "react";
import type { OverlayType } from "../drawings/registry";
import type { ChartType } from "../engine/mainSeries";

export type ChartGlyphName =
  | "layout" | "replay" | "return-live" | "step-forward" | "templates" | "unlock" | "zoom-in" | "zoom-out"
  | "brush-tools" | "cursor-cross" | "cursor-dot" | "cursor-arrow" | "remove-drawings" | "keep-drawing" | "object-tree" | "watchlist-rail" | "data-window";

const GLYPHS: Record<ChartGlyphName, ReactNode> = {
  layout: <><rect x="3" y="3" width="12" height="12" rx="1" /><path d="M9 3v12M3 9h12" /></>,
  replay: <><path d="M5 6H2.5V3.5" /><path d="M3 6a6 6 0 1 1-.2 5" /></>,
  "return-live": <><path d="M3 9h9" /><path d="m9 6 3 3-3 3" /><path d="M14.5 4.5v9" /></>,
  "step-forward": <><path d="m5 4 7 5-7 5z" /><path d="M13.5 4v10" /></>,
  templates: <><path d="M3.5 4.5h11v9h-11z" /><path d="M6 2.5h6M6 15.5h6M6.5 7h5M6.5 10h3" /></>,
  unlock: <><rect x="4" y="8" width="10" height="7" rx="1.5" /><path d="M6.5 8V6.5a2.5 2.5 0 0 1 4.7-1.2" /></>,
  "zoom-in": <><circle cx="8" cy="8" r="4.5" /><path d="m11.5 11.5 3 3M8 5.5v5M5.5 8h5" /></>,
  "zoom-out": <><circle cx="8" cy="8" r="4.5" /><path d="m11.5 11.5 3 3M5.5 8h5" /></>,
  "brush-tools": <><path d="m4 13 1-3 7-7 3 3-7 7-3 1z" /><path d="m10.5 4.5 3 3" /></>,
  "cursor-cross": <path d="M9 2v14M2 9h14" />,
  "cursor-dot": <><path d="M9 2v5M9 11v5M2 9h5M11 9h5" /><circle cx="9" cy="9" r="1" /></>,
  "cursor-arrow": <path d="m4 3 10 5-4.5 1.5L8 14z" />,
  "remove-drawings": <><path d="M4 5h10M7 5V3.5h4V5M5.5 5l.7 9.5h5.6l.7-9.5" /><path d="m2 16 4-4" /></>,
  "keep-drawing": <><path d="m4 13 1-3 7-7 3 3-7 7-3 1z" /><path d="M11 15h5M13.5 12.5v5" /></>,
  "object-tree": <><path d="M4 3v12M4 6h5M4 12h5" /><rect x="10" y="4" width="5" height="4" /><rect x="10" y="10" width="5" height="4" /></>,
  "watchlist-rail": <><path d="M3 4h12M3 9h12M3 14h12" /><path d="M13 2.5v3" /></>,
  "data-window": <><rect x="3" y="3" width="12" height="12" rx="1" /><path d="M3 7h12M8 7v8" /></>,
};

const TYPE_GLYPHS: Record<ChartType, ReactNode> = {
  bars: <path d="M6 3v12M6 6H4M6 12h2M12 4v10M12 7h-2M12 11h2" />,
  candles: <><path d="M6 2v3M6 13v3M12 4v2M12 12v2" /><rect x="4" y="5" width="4" height="8" fill="currentColor" /><rect x="10" y="6" width="4" height="6" /></>,
  hollow_candles: <><path d="M6 2v3M6 13v3M12 4v2M12 12v2" /><rect x="4" y="5" width="4" height="8" /><rect x="10" y="6" width="4" height="6" /></>,
  columns: <path d="M4 15V9M8 15V5M12 15V8M16 15V3" strokeWidth="2.4" />,
  line: <polyline points="2,13 6,8 10,11 16,4" />,
  line_markers: <><polyline points="2,13 6,8 10,11 16,4" /><circle cx="6" cy="8" r="1.2" /><circle cx="10" cy="11" r="1.2" /></>,
  step_line: <polyline points="2,13 6,13 6,8 10,8 10,11 14,11 14,4 16,4" />,
  area: <><polyline points="2,13 6,8 10,11 16,4" /><path d="M2 13 6 8l4 3 6-7v11H2z" opacity=".35" fill="currentColor" stroke="none" /></>,
  baseline: <><path d="M2 9h14" strokeDasharray="1.5 2" /><polyline points="2,12 6,6 10,11 16,4" /></>,
  high_low: <><path d="M5 4v9M9 6v8M13 3v7" strokeWidth="2.2" /></>,
  heikin_ashi: <><path d="M6 2v3M6 13v3M12 4v2M12 12v2" /><rect x="4" y="5" width="4" height="8" /><rect x="10" y="6" width="4" height="6" fill="currentColor" /></>,
  renko: <><rect x="2" y="11" width="4" height="4" /><rect x="6" y="7" width="4" height="4" /><rect x="10" y="3" width="4" height="4" fill="currentColor" /></>,
  line_break: <><rect x="2" y="9" width="3" height="6" /><rect x="6" y="5" width="3" height="6" /><rect x="10" y="3" width="3" height="4" fill="currentColor" /><rect x="14" y="6" width="2" height="5" /></>,
  kagi: <polyline points="3,14 3,7 7,7 7,11 11,11 11,3 15,3 15,9" />,
  point_figure: <><path d="m3 4 3 3M6 4 3 7M3 9l3 3M6 9l-3 3" /><circle cx="12" cy="6" r="2" /><circle cx="12" cy="11" r="2" /></>,
  range: <><rect x="3" y="9" width="3" height="5" /><rect x="7.5" y="6" width="3" height="5" fill="currentColor" /><rect x="12" y="3" width="3" height="5" /></>,
};

const TOOL_PATHS = {
  trendline: <path d="M3 14 15 4" />,
  info_line: <><path d="M3 14 15 4" /><circle cx="3" cy="14" r="1" /><path d="M10 5h5v5" /></>,
  ray: <><path d="M3 13 14 4" /><path d="M10 4h4v4" /></>,
  extended_line: <><path d="M1 16 17 2" /><circle cx="6" cy="12" r="1" /><circle cx="12" cy="7" r="1" /></>,
  trend_angle: <><path d="M3 14h12M3 14 13 5" /><path d="M7 14a4 4 0 0 0-1-3" /></>,
  hline: <path d="M3 9h12" />,
  horizontal_ray: <><path d="M3 9h12" /><path d="m12 6 3 3-3 3" /></>,
  vline: <path d="M9 3v12" />,
  cross_line: <><path d="M3 9h12M9 3v12" /><circle cx="9" cy="9" r="1" /></>,
  arrow: <><path d="M3 14 14 4" /><path d="M9 4h5v5" /></>,
  path: <polyline points="2,13 6,6 10,11 16,4" />,

  parallel_channel: <><path d="M3 12 13 4M5 15 15 7" /><path d="M4 7 14 14" strokeDasharray="1.5 2" /></>,
  price_channel: <><path d="M3 5h12M3 9h12M3 13h12" /><path d="M6 3v12" /></>,
  regression_trend: <><path d="M3 13 15 5M3 9 15 1M3 17 15 9" /><circle cx="6" cy="11" r=".8" /></>,
  flat_top_bottom: <><path d="M3 5h10l2 8H5z" /><path d="M5 13h10" /></>,
  pitchfork: <><path d="M3 9h12M5 4l8 10M5 14l8-10" /><circle cx="3" cy="9" r="1" /></>,
  schiff_pitchfork: <><path d="M5 9h11M3 5l10 9M3 14 13 5" /><circle cx="5" cy="9" r="1" /></>,

  fib: <><path d="M3 4h12M3 7h8M3 10h12M3 14h8" /></>,
  fib_extension: <><path d="M2 14 8 6 15 10" /><path d="M9 4h7M10 8h6M11 12h5" /></>,
  fib_channel: <><path d="M2 13 12 4M5 16 15 7M3 10 13 1" /></>,
  fib_time: <><path d="M3 3v12M7 5v10M12 3v12M16 7v8" /><path d="M2 15h15" /></>,
  gann_box: <><rect x="3" y="3" width="12" height="12" /><path d="m3 15 12-12M3 9h12M9 3v12" /></>,
  gann_fan: <><path d="M3 15 15 3M3 15l12-6M3 15l6-12M3 15h12" /></>,

  brush: <><path d="m4 13 8-8 2 2-8 8H4z" /><path d="M4 15h5" /></>,
  highlighter: <><path d="m4 12 7-8 3 3-7 8H4z" /><path d="M3 15h9" strokeWidth="2.8" /></>,
  rect: <rect x="3.5" y="4" width="11" height="10" />,
  rotated_rect: <path d="m6 2 10 6-5 8L1 10z" />,
  ellipse: <ellipse cx="9" cy="9" rx="6" ry="4" />,
  triangle: <path d="m9 3 6 12H3z" />,
  arc: <path d="M3 14Q9 2 15 14" />,
  polyline: <polyline points="2,14 5,5 10,11 15,3" />,

  text: <path d="M4 4h10M9 4v10M6.5 14h5" />,
  anchored_text: <><path d="M7 5h8M11 5v9M8 14h6" /><path d="M3 14 9 8" /><circle cx="3" cy="14" r="1" /></>,
  note: <><path d="M4 3h10v12H4z" /><path d="M6 6h6M6 9h6M6 12h4" /></>,
  callout: <><path d="M3 4h12v8H8l-3 3v-3H3z" /><path d="M6 7h6" /></>,
  comment: <><circle cx="9" cy="8" r="5" /><path d="m6 12-2 3 4-2M6 7h6M6 9h4" /></>,
  price_label: <><path d="M3 5h10l2 4-2 4H3z" /><path d="M6 9h6" /></>,
  arrow_marker: <><path d="M9 3v12M5 7l4-4 4 4" /><circle cx="9" cy="14" r="1" /></>,

  xabcd: <polyline points="1,6 4,14 8,5 12,12 17,3" />,
  abcd: <polyline points="2,4 6,14 11,7 16,15" />,
  cypher: <polyline points="1,12 5,4 9,10 13,2 17,14" />,
  head_shoulders: <polyline points="1,14 4,7 6,12 9,3 12,12 14,7 17,14" />,
  elliott_impulse: <polyline points="1,14 4,9 7,12 10,4 13,8 17,2" />,
  elliott_correction: <polyline points="2,4 7,13 11,8 16,15" />,
  elliott_triangle: <polyline points="1,4 4,14 7,6 10,12 13,8 17,10" />,

  measure: <><path d="M3 9h12" /><path d="m6 6-3 3 3 3M12 6l3 3-3 3" /></>,
  date_range: <><path d="M3 5v9M15 5v9M3 10h12" /><path d="m6 7-3 3 3 3m6-6 3 3-3 3" /></>,
  price_range: <><path d="M5 3h9M5 15h9M9 3v12" /><path d="m6 6 3-3 3 3m-6 6 3 3 3-3" /></>,
  date_price_range: <><rect x="3" y="3" width="12" height="12" /><path d="m3 15 12-12" /></>,
  anchored_vwap: <><path d="M3 13c3-8 6 2 12-8" /><circle cx="3" cy="13" r="1.5" /><path d="M3 16h12" strokeDasharray="1.5 2" /></>,
  forecast: <><path d="M2 14 8 8" /><path d="m7 4 9-2-2 9" strokeDasharray="2 2" /><path d="m12 2 4 0 0 4" /></>,
  long_position: <><rect x="3" y="3" width="12" height="6" /><rect x="3" y="9" width="12" height="6" /><path d="M3 9h12M9 5v8" /></>,
  short_position: <><rect x="3" y="3" width="12" height="6" /><rect x="3" y="9" width="12" height="6" /><path d="M3 9h12M9 13V5" /></>,
} satisfies Record<OverlayType, ReactNode>;

function Glyph({ children, ...props }: SVGProps<SVGSVGElement> & { children: ReactNode }) {
  return <svg aria-hidden="true" fill="none" height="18" viewBox="0 0 18 18" width="18" stroke="currentColor" strokeLinecap="round"
    strokeLinejoin="round" strokeWidth="1.4" {...props}>{children}</svg>;
}

export const ChartGlyph = ({ name }: { name: ChartGlyphName }) => <Glyph>{GLYPHS[name]}</Glyph>;
export const ChartTypeGlyph = ({ type }: { type: ChartType }) => <Glyph>{TYPE_GLYPHS[type]}</Glyph>;
export const DrawingToolGlyph = ({ type }: { type: OverlayType }) => <Glyph>{TOOL_PATHS[type]}</Glyph>;
