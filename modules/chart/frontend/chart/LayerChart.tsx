import { t } from "../i18n";
import { useEffect, useRef, useState, type CSSProperties } from "react";
import { TxButton, TxEmpty, TxFailure, type ModuleContext } from "@talyx/sdk";
import { createClient, type ContractTypes, type ResearchLayer } from "../../public/ts/types";
import { ChartView } from "../engine/ChartView";
import { checkLayerSize, drawResearch, layerPanes } from "../engine/researchLayer";
import { chartColors, watchUpColor } from "../engine/theme";
import { upperBound } from "../engine/timeMap";
import { post } from "../api";
import { message } from "../store";
import { isDailyOrLonger } from "./timeframes";

export type LayerProps = ContractTypes["view:layer_chart"];

function focus(view: ChartView, times: number[], time: string | undefined) {
  if (!time) return;
  const index = upperBound(times, Date.parse(time)) - 1;
  if (index >= 0) {
    // When the whole run fits, zoom just enough to keep candles on either side of the fill.
    const range = view.visibleRange();
    const half = Math.min(range ? (range.to - range.from) / 2 : times.length / 2,
      Math.max(1, index + 0.5), Math.max(1, times.length - index - 0.5));
    view.chart.timeScale().setVisibleLogicalRange({ from: index - half, to: index + half });
    view.schedule(() => view.showCrosshairAt(times[index]));
  }
}

export function LayerChart({ ctx, layer, focusTime }: LayerProps & { ctx: ModuleContext }) {
  const canvas = useRef<HTMLDivElement>(null), focused = useRef(focusTime);
  const active = useRef<{ view: ChartView; times: number[] } | null>(null);
  const [error, setError] = useState(""), [ready, setReady] = useState(false), [busy, setBusy] = useState(false);
  const [empty, setEmpty] = useState(false), [axis, setAxis] = useState(0);
  focused.current = focusTime;
  useEffect(() => {
    let disposed = false, release: (() => void) | undefined;
    setReady(false); setError(""); setEmpty(false);
    async function render() {
      const data: ResearchLayer = "path" in layer ? await post(ctx, "/layers/file", layer) : layer;
      if (disposed) return;
      checkLayerSize(data);
      const { instrumentId, timeframe, adjustment, window } = data;
      const { bars, zone, priceFormat } = await post(ctx, "/layers/history", { instrumentId, timeframe, adjustment, window });
      if (disposed) return;
      if (!bars.time.length) { setEmpty(true); return; }
      const view = new ChartView(canvas.current!, { zone: { zone, dateOnly: isDailyOrLonger(timeframe) }, type: "candles",
        format: priceFormat, colors: chartColors(), magnet: false, box: null, snapshot: true }, bars);
      const panes = layerPanes([{ id: "price", height: 3, collapsed: false }], [data]);
      view.layoutPanes(panes);
      view.fitSnapshot();
      let clear = drawResearch(view, [data], panes, bars.time);
      const stop = watchUpColor(() => { clear(); view.restyle(chartColors()); clear = drawResearch(view, [data], panes, bars.time); });
      active.current = { view, times: bars.time };
      view.schedule(() => { focus(view, bars.time, focused.current); setAxis(view.axisWidth()); });
      release = () => { active.current = null; stop(); clear(); view.dispose(); };
      setReady(true);
    }
    void render().catch(cause => { if (!disposed) setError(message(cause)); });
    return () => { disposed = true; release?.(); };
  }, [ctx, "path" in layer ? layer.sha256 : layer]);
  useEffect(() => { if (active.current) focus(active.current.view, active.current.times, focusTime); }, [focusTime]);
  async function open() {
    setBusy(true); setError("");
    try {
      const result = await createClient(ctx).write_chart({ operation: { op: "create", layers: [layer] } });
      await ctx.platform.panels.openTarget({ module: "chart", panelType: "main", objectId: `chart:${result.chartId}` });
    } catch (cause) { setError(message(cause)); } finally { setBusy(false); }
  }
  return <section className="chart-research" aria-label={t("Research chart")} style={{ "--chart-research-axis": `${axis}px` } as CSSProperties}>
    <div className="chart-research-head"><TxButton size="sm" disabled={busy} onClick={() => void open()}>{t("Open in Chart")}</TxButton></div>
    {error && <TxFailure title={t("Chart unavailable")} message={error} />}
    {!ready && !error && <TxEmpty title={empty ? t("No bars in this period") : t("Loading chart…")} />}
    <div className="chart-research-canvas" ref={canvas} />
  </section>;
}
