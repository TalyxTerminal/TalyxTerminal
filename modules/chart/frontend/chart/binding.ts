/* One chart on screen: its feed and document drive a ChartView, its indicator views and its drawing layers.
   React mounts it; everything that changes every frame stays out of React. */
import type { ModuleContext } from "@talyx/sdk";
import { createClient as marketClient } from "../../../market/public/ts/types";
import { ChartView } from "../engine/ChartView";
import { IndicatorView } from "../engine/indicatorView";
import { ComparisonView } from "../engine/comparisonView";
import { drawResearch, layerPanes, matchesLayer } from "../engine/researchLayer";
import type { ResearchLayer } from "../../public/ts/types";
import { chartColors, chartColorsWith, comparisonColor, watchUpColor } from "../engine/theme";
import { shownFormat } from "../engine/precision";
import type { FutureBars } from "../engine/timeMap";
import { DrawingController } from "../drawings/controller";
import type { Drawing } from "../drawings/types";
import { positionUnits } from "../drawings/positionMetrics";
import { post } from "../api";
import { chartEntry, commit, focusChart, setAtStart, setAutoScale, setExchangeZone, message, reportChartError, selectDrawing, setHover, setPricePointer, store, type PanelUi } from "../store";
import type { ChartDocument, ChartState } from "../wire";
import { isDailyOrLonger, timeframeLabel, timeframeMs } from "./timeframes";
import type { FeedEvent } from "../feed";

export interface BindingEvents {
  openDrawing(id: string, tab?: "text"): void;
  drawingMenu(id: string, x: number, y: number): void;
  /** Right click on the chart away from a drawing: the price under it in the price pane, or null in another pane. */
  chartMenu(x: number, y: number, price: number | null): void;
  /** Right click on the main price scale. */
  scaleMenu(x: number, y: number): void;
  toolDone(): void;
  /** The crosshair moved on this chart (time, or null when it left). */
  crosshair(time: number | null): void;
}

const FUTURE_BARS = 600;
const BACKFILL_BARS = 5000;

export class ChartBinding {
  view: ChartView | null = null;
  private controller: DrawingController | null = null;
  private indicators = new Map<string, { view: IndicatorView; key: string }>();
  private comparisons = new Map<string, { view: ComparisonView; key: string }>();
  private snapshots = new Map<string, ResearchLayer>();
  private researchRequest = "";
  private clearResearch: (() => void) | null = null;
  private researchDrawn = "";
  private readonly shownLayers = new Set<string>();
  private document: ChartDocument | null = null;
  private exchangeZone = "UTC";
  private future: FutureBars | null = null;
  private futureFor = "";
  private backfilling = false;
  /** The visible range as the backend has it, and one on its way after the request in flight. */
  private viewSent = "";
  private viewNext: { left: number; right: number } | { bars: number } | null = null;
  private viewPosting = false;
  /** The series the engine's bars belong to: the document's at the last replace (the stream sends a document first). */
  private barsSeries = "";
  private disposed = false;
  private readonly stops: (() => void)[] = [];

  private readonly clock = setInterval(() => this.tick(), 1000);

  /** The countdown follows the forming bar while the market is open (TradingView hides it when closed). */
  private tick(): void {
    const entry = chartEntry(this.chartId), bars = entry?.feed.bars;
    if (!this.view || !bars) return;
    const open = (entry.status?.state === "live" || entry.status?.state === "delayed") && this.document?.display.scales?.countdown !== false;
    const close = bars.closeTime[bars.closeTime.length - 1];
    this.view.setCountdown(open && close !== undefined && close > Date.now() ? close : null);
  }

  constructor(private readonly element: HTMLElement, private readonly ctx: ModuleContext, readonly chartId: string, private ui: PanelUi, private readonly events: BindingEvents,
              private readonly panePositions: (tops: Map<string, number>) => void) {
    const entry = chartEntry(chartId);
    this.stops.push(entry.feed.listen(this.feedEvent));
    element.addEventListener("contextmenu", this.contextMenu);
    this.stops.push(() => element.removeEventListener("contextmenu", this.contextMenu));
    // A crosshair shown by layout sync is not the pointer: the "+" by the price scale follows only the pointer.
    // Moving onto the "+" itself keeps it where it is (it then clears the pointer when the pointer leaves it).
    const enter = () => { this.pointerInside = true; };
    const leave = (event: PointerEvent) => {
      this.pointerInside = false;
      if (!(event.relatedTarget instanceof Element && event.relatedTarget.closest(".chart-price-plus"))) setPricePointer(chartId, null);
    };
    // Dragging the price scale turns Auto off and a double click on it turns it on again; the engine says neither.
    const scaled = () => { if (this.view) setAutoScale(chartId, this.view.autoScale()); };
    element.addEventListener("pointerenter", enter); element.addEventListener("pointerleave", leave);
    element.addEventListener("pointerup", scaled); element.addEventListener("dblclick", scaled);
    this.stops.push(() => {
      element.removeEventListener("pointerenter", enter); element.removeEventListener("pointerleave", leave);
      element.removeEventListener("pointerup", scaled); element.removeEventListener("dblclick", scaled);
    });
    this.stops.push(store.subscribe((state, previous) => {
      const now = state.charts[chartId]?.chart, before = previous.charts[chartId]?.chart;
      if (now && now !== before) { this.pendingDocument = now; this.queueFlush(); }
      if (state.charts[chartId]?.focus && state.charts[chartId]?.focus !== previous.charts[chartId]?.focus) this.showFocus();
      if (state.alerts !== previous.alerts) this.showAlerts();
    }));
    // The user's up and down colours live on the root element; the canvas holds concrete colours, so it is restyled.
    this.stops.push(watchUpColor(() => {
      if (!this.view || !this.document) return;
      this.view.restyle(chartColorsWith(this.document.display.appearance));
      for (const { view } of this.indicators.values()) view.rebuild();
      this.syncResearch(true);
    }));
    if (entry.chart) this.documentChanged(entry.chart);
  }

  private pointerInside = false;

  dispose(): void {
    this.disposed = true;
    cancelAnimationFrame(this.flushQueued);
    clearInterval(this.clock);
    for (const stop of this.stops) stop();
    this.teardown();
  }

  setUi(ui: PanelUi): void {
    this.ui = ui;
    this.controller?.arm(ui.tool);
    this.controller?.setOptions(this.drawingOptions());
    this.view?.setMagnet(ui.magnet !== "off");
    this.showDrawings();
  }

  /** A drawing's own menu is the drawing controller's (it stops the event first). */
  private readonly contextMenu = (event: MouseEvent) => {
    event.preventDefault();
    if (this.view?.onPriceScale(event.clientX)) { this.events.scaleMenu(event.clientX, event.clientY); return; }
    const hit = this.view?.paneAt(event.clientX, event.clientY) ?? null;
    this.events.chartMenu(event.clientX, event.clientY, hit?.paneId === "price" ? this.view!.priceAt(hit.y) : null);
  };

  // ── building ──────────────────────────────────────────────────────────

  private teardown(): void {
    this.clearResearch?.(); this.clearResearch = null; this.researchDrawn = "";
    for (const { view } of this.indicators.values()) view.dispose();
    this.indicators.clear();
    for (const { view } of this.comparisons.values()) view.dispose();
    this.comparisons.clear();
    this.controller?.dispose(); this.controller = null;
    this.view?.dispose(); this.view = null;
  }

  private zone(document: ChartDocument): { zone: string; dateOnly: boolean } {
    const dateOnly = isDailyOrLonger(document.series.timeframe!);
    const chosen = document.display.timeZone ?? "exchange";
    return { zone: dateOnly || chosen === "exchange" ? this.exchangeZone : chosen, dateOnly };
  }

  /** Whether the view's horizontal scale reads time in another zone or date mode than this document needs now (display
      zone, the exchange's zone once its calendar answers, daily or intraday). Every input reaches the scale through here. */
  private scaleStale(): boolean {
    return this.view !== null && this.document !== null && JSON.stringify(this.view.scale.config) !== JSON.stringify(this.zone(this.document));
  }

  /** The engine is built once bars and the exchange's zone are known, and again when the zone changes. */
  private build(): void {
    const document = this.document!, feed = chartEntry(this.chartId).feed;
    this.teardown();
    if (!this.barsSeries) this.barsSeries = document.series.seriesId;
    const view = new ChartView(this.element, {
      zone: this.zone(document), type: document.chartType, format: this.format(), colors: chartColorsWith(document.display.appearance),
      magnet: this.ui.magnet !== "off", box: feed.boxSize, panePositions: this.panePositions,
    }, feed.bars);
    this.view = view;
    view.pointValue = positionUnits(document.instrument).pointValue;
    if (this.future) view.setFuture(this.future);
    this.applyScale(document);
    this.applyDisplay(document);
    view.onCrosshair(at => {
      setHover(this.chartId, at?.index ?? null);
      if (this.pointerInside) {
        const price = at && at.paneIndex === view.pricePane ? view.priceAt(at.y) : null;
        setPricePointer(this.chartId, at && price !== null ? { y: at.y, price } : null);
      }
      this.events.crosshair(at?.time ?? null);
    });
    view.onVisibleRange(range => { if (range && range.from < 30) this.backfill(); setAtStart(this.chartId, range !== null && range.from < 1); this.sendView(); });
    this.controller = new DrawingController(view, {
      commit: commands => commit(this.ctx, this.chartId, commands).then(done => { this.showDrawings(); return done; }),
      select: id => selectDrawing(this.chartId, id),
      toolDone: () => this.events.toolDone(),
      open: (id, tab) => this.events.openDrawing(id, tab),
      menu: (id, x, y) => this.events.drawingMenu(id, x, y),
    }, this.drawingOptions());
    this.controller.arm(this.ui.tool);
    this.showAlerts();
    this.syncIndicators(true);
    this.showFocus();
    this.tick();
    setAutoScale(this.chartId, true);
    this.resendView();
  }

  /** Active alerts on this chart's symbol that compare the price with a value: their values as lines. */
  private showAlerts(): void {
    const instrumentId = this.document?.instrument.instrumentId;
    if (!this.view || !instrumentId) return;
    const prices = Object.values(store.getState().alerts)
      .filter(alert => alert.state === "active" && alert.target.kind === "series" && alert.target.instrument.instrumentId === instrumentId)
      .flatMap(alert => alert.conditions.flatMap(condition => [condition.right, condition.right2]
        .filter(operand => condition.left.kind === "price" && operand?.kind === "value").map(operand => (operand as { value: number }).value)));
    this.view.setAlertLines([...new Set(prices)]);
  }

  private format() { return shownFormat(chartEntry(this.chartId).feed.priceFormat, this.document?.display.precision); }

  /** Chart settings that are options of the built view: last price label and line, the watermark. */
  private applyDisplay(document: ChartDocument): void {
    const scales = document.display.scales ?? {};
    this.view!.setLastPrice(scales.lastPriceLabel !== false, scales.lastPriceLine !== false);
    this.view!.setAlertLook(document.display.alertLine ?? {});
    this.view!.setWatermark(document.display.appearance?.watermark ? `${document.instrument.displaySymbol}, ${timeframeLabel(document.series.timeframe!)}` : null);
  }

  private drawingOptions() {
    return { magnet: this.ui.magnet, keepDrawing: this.ui.keepDrawing };
  }

  private applyScale(document: ChartDocument): void {
    const scale = document.display.scale ?? {};
    const view = this.view!;
    view.setScale({ mode: scale.mode ?? "regular", invert: scale.invert === true, side: scale.side ?? "right" });
    // The legend starts after a left price scale; its width is known once the engine has drawn it.
    view.schedule(() => this.element.parentElement!.style.setProperty("--chart-scale-left", view.side === "left" ? `${view.axisWidth()}px` : "0px"));
  }

  // ── document ──────────────────────────────────────────────────────────

  private documentChanged(chart: ChartState): void {
    const previous = this.document, document = chart.document;
    this.document = document;
    this.loadResearch(document);
    const seriesChanged = !previous || previous.series.seriesId !== document.series.seriesId;
    if (seriesChanged) this.loadCalendar(document).catch(error => reportChartError(this.chartId, message(error)));
    if (!this.view) { if (chartEntry(this.chartId).feed.loaded && this.futureFor === document.series.seriesId) this.build(); return; }
    if (this.scaleStale()) { this.build(); return; }
    // A new main series drops what indicators hung on it (markers, price lines, bar colours): they are built again.
    const restyled = JSON.stringify(previous?.display.appearance) !== JSON.stringify(document.display.appearance);
    if (restyled) this.view.restyle(chartColorsWith(document.display.appearance));
    if (document.chartType !== previous?.chartType) this.view.setType(document.chartType, this.format());
    if (restyled || document.chartType !== previous?.chartType) for (const { view } of this.indicators.values()) view.rebuild();
    if (document.display.precision !== previous?.display.precision) this.view.setFormat(this.format());
    if (document.instrument.instrumentId !== previous?.instrument.instrumentId) {
      this.view.pointValue = positionUnits(document.instrument).pointValue;
      this.showAlerts();
    }
    this.applyDisplay(document);
    if (JSON.stringify(document.display.scale) !== JSON.stringify(previous?.display.scale)) this.applyScale(document);
    this.syncIndicators(false);
    if (restyled || document.chartType !== previous?.chartType) this.syncResearch(true);
  }

  private visibleLayers(document: ChartDocument) {
    return document.layers.filter(layer => layer.visible && matchesLayer(layer, document.instrument.instrumentId, document.series.adjustment));
  }

  private loadResearch(document: ChartDocument): void {
    const visible = this.visibleLayers(document), key = JSON.stringify(visible.map(item => item.version));
    if (key === this.researchRequest) return;
    this.researchRequest = key;
    const versions = new Set(visible.map(layer => layer.version));
    for (const version of this.snapshots.keys()) if (!versions.has(version)) this.snapshots.delete(version);
    // A layer new to this view opens on its own window; Market's older bars are read back to it like any focus.
    const added = visible.filter(layer => !this.shownLayers.has(layer.version));
    added.forEach(layer => this.shownLayers.add(layer.version));
    if (added.length) focusChart(this.chartId, { from: Math.min(...added.map(layer => Date.parse(layer.window.from))),
      to: Math.max(...added.map(layer => Date.parse(layer.window.to))), seriesId: document.series.seriesId });
    void Promise.all(visible.filter(item => !this.snapshots.has(item.version)).map(async item => {
      const layer = await post(this.ctx, "/layers/stored", { chartId: this.chartId, id: item.id });
      if (this.researchRequest === key && !this.disposed) this.snapshots.set(item.version, layer);
    })).then(() => {
      if (this.researchRequest === key && !this.disposed && this.view) this.syncResearch(true);
    }, error => { if (!this.disposed && key === this.researchRequest) reportChartError(this.chartId, message(error)); });
  }

  private syncResearch(force = false): void {
    const document = this.document!, view = this.view!, visible = this.visibleLayers(document);
    const bars = chartEntry(this.chartId).feed.bars;
    const key = JSON.stringify([this.researchRequest, bars.time.length, view.side, this.snapshots.size]);
    if (!force && key === this.researchDrawn) return;
    this.clearResearch?.(); this.clearResearch = null;
    this.researchDrawn = key;
    const loaded = visible.flatMap(item => this.snapshots.has(item.version) ? [this.snapshots.get(item.version)!] : []);
    if (loaded.length && chartEntry(this.chartId).feed.completeWindow) this.clearResearch = drawResearch(view, loaded, layerPanes(document.panes, visible), bars.time);
  }

  /** Future bar times and the exchange's zone, from Market's calendar (24/7 markets step by the timeframe). */
  private async loadCalendar(document: ChartDocument): Promise<void> {
    const seriesId = document.series.seriesId, step = timeframeMs(document.series.timeframe!);
    const bars = chartEntry(this.chartId).feed.bars, last = bars.time[bars.time.length - 1] ?? Date.now();
    let future: FutureBars, zone = "UTC";
    if (document.instrument.calendarId === "24_7") future = { kind: "continuous", step };
    else {
      const start = last + 1, end = Math.min(start + step * FUTURE_BARS * 3, start + 366 * 86_400_000);
      const page = await marketClient(this.ctx)["calendar.bar_times"]({
        calendarId: document.instrument.calendarId, timeframe: document.series.timeframe!, sessionScope: document.series.sessionScope,
        start: `${start}000000`, end: `${end - 1}999999`, limit: FUTURE_BARS,
      });
      zone = page.timezone ?? "UTC";
      future = {
        kind: "calendar", open: page.open.map(ns => Math.round(Number(ns) / 1e6)), close: page.close.map(ns => Math.round(Number(ns) / 1e6)),
        coveredUntil: page.coveredUntil === null ? end : Date.parse(`${page.coveredUntil}T00:00:00Z`) + 86_400_000,
      };
    }
    if (this.disposed || this.document?.series.seriesId !== seriesId) return;
    this.exchangeZone = zone; this.future = future; this.futureFor = seriesId;
    setExchangeZone(this.chartId, zone);
    if (!this.view || this.scaleStale()) { if (chartEntry(this.chartId).feed.loaded) this.build(); }
    else this.view.setFuture(future);
  }

  /** Indicator views follow the document: panes laid out, then each instance kept, rebuilt when its look changed, or dropped. */
  private syncIndicators(all: boolean): void {
    const view = this.view!, document = this.document!, feed = chartEntry(this.chartId).feed;
    const panes = layerPanes(document.panes, this.visibleLayers(document));
    const paneIndex = new Map(panes.map((pane, index) => [pane.id, index]));
    const layout = JSON.stringify(panes.map(pane => pane.id));
    if (all || layout !== this.paneLayout) {
      for (const { view: indicator } of this.indicators.values()) indicator.dispose();
      this.indicators.clear();
      this.clearResearch?.(); this.clearResearch = null; this.researchDrawn = "";
      view.layoutPanes(panes);
      this.paneLayout = layout;
    } else if (this.paneSizes !== JSON.stringify(panes)) view.layoutPanes(panes);
    this.paneSizes = JSON.stringify(panes);
    const palette = chartColors().series;
    for (const [id, entry] of this.indicators) if (!document.indicators.some(item => item.id === id)) { entry.view.dispose(); this.indicators.delete(id); feed.dropIndicator(id); }
    for (const indicator of document.indicators) {
      const data = feed.indicators.get(indicator.id);
      if (!data) continue;
      const labels = document.display.scales?.indicatorLabels !== false, format = this.format();
      const side = view.side, scale = view.indicatorScale(), key = JSON.stringify([indicator.paneId, indicator.visible, indicator.styles, labels, format, side, scale]);
      const existing = this.indicators.get(indicator.id);
      if (existing?.key === key) continue;
      existing?.view.dispose();
      this.indicators.set(indicator.id, { key, view: new IndicatorView(view, data, { pane: paneIndex.get(indicator.paneId)!, visible: indicator.visible, styles: indicator.styles, palette, labels, format, side, scale }) });
    }
    this.syncComparisons();
    this.syncResearch();
    view.attachDrawingLayers();
    this.showDrawings();
  }
  private paneLayout = "";
  private paneSizes = "";

  /** Comparison lines follow the document like indicators: kept, built again when their look changed, or dropped. On the
      main scale they share its side; a new scale is the left or right one. */
  private syncComparisons(): void {
    const view = this.view!, document = this.document!, feed = chartEntry(this.chartId).feed;
    for (const [id, entry] of this.comparisons) if (!document.comparisons.some(item => item.id === id)) { entry.view.dispose(); this.comparisons.delete(id); feed.comparisons.delete(id); }
    document.comparisons.forEach((comparison, index) => {
      const data = feed.comparisons.get(comparison.id);
      if (!data) return;
      const color = comparison.color ?? comparisonColor(index);
      const scaleId = comparison.scale === "same_percent" ? view.side : comparison.scale === "new_left" ? "left" : "right";
      const key = JSON.stringify([color, scaleId, comparison.visible, data.priceFormat]);
      const existing = this.comparisons.get(comparison.id);
      if (existing?.key === key) return;
      existing?.view.dispose();
      this.comparisons.set(comparison.id, { key, view: new ComparisonView(view, data, { color, scaleId, visible: comparison.visible }) });
    });
  }

  /** The drawing group shown now, split by pane; hidden when the toolbar hides all drawings. */
  showDrawings(): void {
    const view = this.view, chart = chartEntry(this.chartId)?.chart;
    if (!view || !chart) return;
    const drawings: Drawing[] = this.ui.drawingsHidden ? [] : chart.document.drawings[chart.drawingGroup] ?? [];
    layerPanes(chart.document.panes, this.visibleLayers(chart.document)).forEach((pane, index) => view.layer(index)?.show(drawings.filter(drawing => drawing.paneId === pane.id), null));
  }

  // ── data ──────────────────────────────────────────────────────────────

  /** Stream items only mark what changed; the engine is touched once per frame, never inside the stream's callback. */
  private pending = { replace: false, bars: false, format: false, indicators: new Map<string, { rebuild: boolean; drawings: boolean; from: number }>(),
    comparisons: new Map<string, { replace: boolean; from: number }>() };
  private flushQueued = 0;

  private readonly feedEvent = (event: FeedEvent) => {
    if (event.kind === "bars") { if (event.replace) this.pending.replace = true; else this.pending.bars = true; }
    else if (event.kind === "format") this.pending.format = true;
    else if (event.kind === "indicator") {
      const before = this.pending.indicators.get(event.id);
      this.pending.indicators.set(event.id, { rebuild: event.rebuild || before?.rebuild === true, drawings: event.drawings || before?.drawings === true, from: Math.min(event.from, before?.from ?? event.from) });
    } else {
      const before = this.pending.comparisons.get(event.id);
      this.pending.comparisons.set(event.id, { replace: event.replace || before?.replace === true, from: Math.min(event.from, before?.from ?? event.from) });
    }
    this.queueFlush();
  };

  private pendingDocument: ChartState | null = null;
  private queueFlush(): void { if (!this.flushQueued) this.flushQueued = requestAnimationFrame(this.flushFeed); }

  private readonly flushFeed = () => {
    this.flushQueued = 0;
    if (this.disposed) return;
    const { replace, bars, format, indicators, comparisons } = this.pending, document = this.pendingDocument;
    this.pending = { replace: false, bars: false, format: false, indicators: new Map(), comparisons: new Map() };
    this.pendingDocument = null;
    if (document) this.documentChanged(document);
    const feed = chartEntry(this.chartId).feed;
    if (!this.view) { if (replace && this.document && this.futureFor === this.document.series.seriesId) this.build(); return; }
    if (replace && this.scaleStale()) { this.build(); this.barsSeries = this.document?.series.seriesId ?? ""; return; }
    if (replace) {
      // A new window: every series is set again in this one pass (Lightweight Charts keeps per-series caches keyed by
      // bar index; a series left with the old indexes is read at positions it no longer has).
      const seriesChanged = this.barsSeries !== (this.document?.series.seriesId ?? "");
      this.view.setFormat(this.format());
      this.view.setBars(feed.bars, feed.boxSize);
      if (seriesChanged) { this.view.resetView(); setAutoScale(this.chartId, true); }
      this.barsSeries = this.document?.series.seriesId ?? "";
      this.backfilling = false;
      for (const { view } of this.indicators.values()) view.rebuild();
      for (const { view } of this.comparisons.values()) view.all();
      this.syncResearch(true);
      this.showFocus();
      this.resendView();
    } else if (bars) this.view.barsChanged();
    if (format && !replace) this.view.setFormat(this.format());
    // Price-pane plots show the symbol's format: a new one rebuilds them.
    if (format || replace) this.syncIndicators(false);
    for (const [id, change] of indicators) {
      const existing = this.indicators.get(id);
      if (!existing) { this.syncIndicators(false); continue; }
      if (replace) continue;
      if (change.rebuild) existing.view.rebuild();
      else { if (change.drawings) existing.view.drawingsChanged(); existing.view.changed(change.from); }
    }
    for (const [id, change] of comparisons) {
      if (change.replace) this.syncComparisons();
      const existing = this.comparisons.get(id);
      if (!existing || replace) continue;
      if (change.replace) existing.view.all(); else existing.view.changed(change.from);
    }
  };

  /** Brings the focus time to the middle of the view, the visible width kept; older than the window, Market's bars
      back to it come first (the replace that follows calls this again). */
  private showFocus(): void {
    const view = this.view, entry = chartEntry(this.chartId), focus = entry?.focus;
    if (!view || !focus || !entry.feed.loaded || (focus.seriesId && focus.seriesId !== this.barsSeries)) return;
    const feed = entry.feed, start = "at" in focus ? focus.at : focus.from;
    if (feed.bars.time.length && start < feed.bars.time[0] && feed.hasMoreBefore && !feed.limitReached) {
      if (this.backfilling) return;
      this.backfilling = true;
      post(this.ctx, "/charts/backfill", { chartId: this.chartId, to: start })
        .catch(error => { this.backfilling = false; focusChart(this.chartId, null); reportChartError(this.chartId, message(error)); });
      return;
    }
    focusChart(this.chartId, null);
    if ("at" in focus) view.centerOn(focus.at);
    else view.showTimes(Math.max(focus.from, feed.bars.time[0] ?? focus.from), focus.to ?? feed.bars.time[feed.bars.time.length - 1]);
  }

  /** The visible range for the indicators that read it (Pine's chart.left/right_visible_bar_time): the first and last visible bar
      times, or the newest bars while the right edge is on the newest bar (new bars then move it without a new run). One request at
      a time; while dragging, the newest range goes next. */
  private sendView(): void {
    const shown = this.view?.visibleBars(), times = chartEntry(this.chartId)?.feed.bars.time;
    if (!shown || !times) return;
    this.viewNext = shown.following ? { bars: shown.to - shown.from + 1 } : { left: times[shown.from], right: times[shown.to] };
    this.postView();
  }

  /** A new window (or a new stream behind it): the range goes again, whatever was sent before. */
  private resendView(): void {
    this.viewSent = "";
    this.sendView();
  }

  private postView(): void {
    const view = this.viewNext;
    if (this.viewPosting || this.disposed || !view) return;
    this.viewNext = null;
    const key = JSON.stringify(view);
    if (key === this.viewSent) return;
    this.viewPosting = true;
    post(this.ctx, "/charts/view", { chartId: this.chartId, ...view })
      .then(() => { this.viewSent = key; }, error => reportChartError(this.chartId, message(error)))
      .finally(() => { this.viewPosting = false; this.postView(); });
  }

  /** Scrolled near the oldest bar: Market's older bars come back as a replace of a longer window. */
  private backfill(): void {
    const feed = chartEntry(this.chartId).feed;
    if (this.backfilling || !feed.hasMoreBefore || feed.limitReached) return;
    this.backfilling = true;
    post(this.ctx, "/charts/backfill", { chartId: this.chartId, bars: BACKFILL_BARS })
      .catch(error => { this.backfilling = false; reportChartError(this.chartId, message(error)); });
  }
}
