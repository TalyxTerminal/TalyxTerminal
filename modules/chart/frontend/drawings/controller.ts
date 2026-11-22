import { t } from "../i18n";
/* The pointer on one chart's drawings: hover, select, make, drag.
   It listens on the chart element in the capture phase: a press on a drawing (or with a tool armed) is stopped so the engine
   does not pan; moves are never stopped, so the crosshair keeps following. A drag is a local preview; only the release
   commits one command, which goes through the chart document like every other change. Magnet and Shift work in pixels. */
import type { DrawingLayer } from "../engine/drawingLayer";
import { constrainDrawingPointer, positionPixels } from "./interactionGeometry";
import { overlayInteraction, overlayTool, type OverlayType } from "./registry";
import type { Drawing, Point } from "./types";

export type Magnet = "off" | "weak" | "strong";
export type DrawingCommand =
  | { type: "drawing.add"; value: Drawing }
  | { type: "drawing.update"; id: string; patch: { points: Point[] } }
  | { type: "drawing.delete"; id: string };

export interface PaneHit { pane: number; paneId: string; x: number; y: number }

export interface DrawingSurface {
  element: HTMLElement;
  /** The pane under a client point, with pane-local plot coordinates. */
  paneAt(clientX: number, clientY: number): PaneHit | null;
  /** A client point in one pane's plot coordinates, wherever the pointer is. */
  local(pane: number, clientX: number, clientY: number): PaneHit;
  layer(pane: number): DrawingLayer | null;
  layers(): DrawingLayer[];
}

export interface DrawingControllerEvents {
  /** Resolves true once the chart took the commands. */
  commit(commands: DrawingCommand[]): Promise<boolean>;
  select(id: string | null): void;
  /** A new drawing is done; the tool stays armed only when "keep drawing" is on. */
  toolDone(): void;
  /** Double click on a drawing: its settings; on the Text tab right after placing a drawing whose text is its content. */
  open(id: string, tab?: "text"): void;
  /** Right click on a drawing (selected first). */
  menu(id: string, clientX: number, clientY: number): void;
}

export interface DrawingOptions { magnet: Magnet; keepDrawing: boolean }

type Drag = { layer: DrawingLayer; pane: number; id: string; handle: number | null; x0: number; y0: number; original: Drawing; pixels: { x: number; y: number }[]; moved: boolean };
type Making = { layer: DrawingLayer; pane: PaneHit; drawing: Drawing; placed: number; pressed: boolean };

const DEFAULT_TEXT = t("Text");
let sequence = 0;
const newId = () => `d${Date.now().toString(36)}${(sequence++).toString(36)}`;

export class DrawingController {
  tool: OverlayType | null = null;
  private making: Making | null = null;
  private drag: Drag | null = null;
  private hovered: DrawingLayer | null = null;

  constructor(private readonly surface: DrawingSurface, private readonly events: DrawingControllerEvents, private options: DrawingOptions) {
    const element = surface.element;
    element.addEventListener("mousedown", this.down, true);
    element.addEventListener("mousemove", this.hover, true);
    element.addEventListener("dblclick", this.doubleClick, true);
    element.addEventListener("contextmenu", this.contextMenu, true);
    element.addEventListener("keydown", this.key);
  }

  dispose(): void {
    const element = this.surface.element;
    element.removeEventListener("mousedown", this.down, true);
    element.removeEventListener("mousemove", this.hover, true);
    element.removeEventListener("dblclick", this.doubleClick, true);
    element.removeEventListener("contextmenu", this.contextMenu, true);
    element.removeEventListener("keydown", this.key);
    this.stopDocument();
  }

  setOptions(options: DrawingOptions): void { this.options = options; }

  arm(tool: OverlayType | null): void {
    this.cancel();
    this.tool = tool;
    this.surface.element.style.cursor = tool ? "crosshair" : "";
  }

  /** Esc: a half-made drawing goes away, otherwise the selection does. */
  cancel(): void {
    if (this.making) { this.making.layer.setPreview(null); this.making = null; this.stopDocument(); }
    if (this.drag) { this.drag.layer.setPreview(null); this.drag = null; this.stopDocument(); }
  }

  select(id: string | null): void {
    for (const layer of this.surface.layers()) { layer.focus.selected = layer.drawing(id ?? "") ? id : null; layer.redraw(); }
  }

  get selected(): string | null {
    for (const layer of this.surface.layers()) if (layer.focus.selected) return layer.focus.selected;
    return null;
  }

  // ── pointer ───────────────────────────────────────────────────────────

  /** Magnet on the bar under the pointer: weak only within 30 px of its open, high, low or close; strong always. */
  private snap(layer: DrawingLayer, x: number, y: number, magnet = this.options.magnet): Point | null {
    const point = layer.fromPixel(x, y);
    if (!point || magnet === "off") return point;
    const bar = layer.barAt(x);
    if (!bar) return point;
    let best = point.price, distance = Infinity;
    for (const value of [bar.open, bar.high, bar.low, bar.close]) {
      const at = layer.priceToY(value);
      if (at !== null && Math.abs(at - y) < distance) { distance = Math.abs(at - y); best = value; }
    }
    return magnet === "strong" || distance <= 30 ? { time: bar.time, price: best } : point;
  }

  private readonly down = (event: MouseEvent) => {
    if (event.button !== 0) return;
    const hit = this.surface.paneAt(event.clientX, event.clientY);
    if (!hit) return;
    this.surface.element.focus({ preventScroll: true });
    const layer = this.surface.layer(hit.pane);
    if (!layer) return;
    if (this.tool || this.making) { event.stopPropagation(); event.preventDefault(); this.place(layer, hit, event.shiftKey, true); return; }
    const found = layer.hit(hit.x, hit.y);
    if (!found) { if (this.selected) { this.select(null); this.events.select(null); } return; }
    event.stopPropagation(); event.preventDefault();
    const { drawing } = found;
    if (this.selected !== drawing.id) { this.select(drawing.id); this.events.select(drawing.id); }
    if (drawing.locked) return;
    const handle = found.hit.kind === "handle" && found.hit.handle.kind === "anchor" ? Number(found.hit.handle.id.split(":")[1]) : null;
    const pixels = drawing.points.map(point => layer.toPixel(point)!);
    this.drag = { layer, pane: hit.pane, id: drawing.id, handle, x0: hit.x, y0: hit.y, original: drawing, pixels, moved: false };
    layer.focus.dragging = true;
    this.startDocument();
  };

  private place(layer: DrawingLayer, hit: PaneHit, shift: boolean, pressed: boolean): void {
    const making = this.making;
    if (!making) {
      const tool = this.tool!;
      const point = this.snap(layer, hit.x, hit.y);
      if (!point) return;
      const contract = overlayTool(tool);
      const count = contract.points.kind === "minimum" ? 2 : contract.points.count;
      let points = Array.from({ length: count }, () => ({ ...point }));
      // A position is placed whole by one click (TradingView); its target and stop are then dragged where wanted.
      const position = tool === "long_position" || tool === "short_position";
      if (position) {
        const [target, stop] = positionPixels(tool, layer.toPixel(point)!, layer.height, layer.barSpacing()).map(pixel => layer.fromPixel(pixel.x, pixel.y));
        if (!target || !stop) return;
        points = [point, target, stop];
      }
      const drawing: Drawing = {
        id: newId(), tool, points, paneId: hit.paneId,
        text: contract.textMode === "required" ? DEFAULT_TEXT : null, style: {}, settings: {}, visibility: {}, locked: false, visible: true,
      };
      this.making = { layer, pane: hit, drawing, placed: 1, pressed };
      if (count === 1 || position) { this.finish(); return; }
      layer.setPreview(drawing);
      this.select(drawing.id);
      this.startDocument();
      return;
    }
    if (making.layer !== layer) return;
    if (overlayInteraction(making.drawing.tool) === "multi_click") {
      making.drawing = { ...making.drawing, points: [...making.drawing.points.slice(0, making.placed), ...making.drawing.points.slice(making.placed - 1, making.placed)] };
      making.placed += 1;
      return;
    }
    this.follow(hit, shift);
    making.placed += 1;
    if (making.placed >= making.drawing.points.length) this.finish();
  }

  /** The unplaced points follow the pointer while making. */
  private follow(hit: PaneHit, shift: boolean): void {
    const making = this.making!, layer = making.layer, tool = making.drawing.tool;
    const anchor = layer.toPixel(making.drawing.points[making.placed - 1]) ?? undefined;
    const pointer = constrainDrawingPointer(tool, anchor, { x: hit.x, y: hit.y }, shift);
    const point = this.snap(layer, pointer.x, pointer.y, shift ? "off" : this.options.magnet);
    if (!point) return;
    const points = overlayInteraction(tool) === "continuous"
      ? [...making.drawing.points.slice(0, making.placed), point]
      : making.drawing.points.map((existing, index) => (index >= making.placed ? { ...point } : existing));
    if (overlayInteraction(tool) === "continuous") making.placed = points.length - 1;
    making.drawing = { ...making.drawing, points };
    layer.setPreview(making.drawing);
  }

  private finish(): void {
    const making = this.making!;
    this.making = null;
    this.stopDocument();
    const minimum = overlayTool(making.drawing.tool).points.count;
    const points = overlayInteraction(making.drawing.tool) === "multi_click" ? making.drawing.points.slice(0, making.placed) : making.drawing.points;
    if (points.length < minimum) { making.layer.setPreview(null); return; }
    const drawing = { ...making.drawing, points };
    making.layer.setPreview(drawing);
    const added = this.events.commit([{ type: "drawing.add", value: drawing }]);
    // TradingView: a text tool is typed into as soon as it is down.
    if (overlayTool(drawing.tool).textMode === "required") void added.then(done => { if (done) this.events.open(drawing.id, "text"); });
    this.events.select(drawing.id);
    if (!this.options.keepDrawing) { this.tool = null; this.surface.element.style.cursor = ""; }
    this.events.toolDone();
  }

  private readonly move = (event: MouseEvent) => {
    const making = this.making;
    if (making) {
      const hit = this.surface.local(making.pane.pane, event.clientX, event.clientY);
      if (overlayInteraction(making.drawing.tool) === "continuous" && !(event.buttons & 1)) return;
      this.follow(hit, event.shiftKey);
      return;
    }
    const drag = this.drag;
    if (!drag) return;
    const hit = this.surface.local(drag.pane, event.clientX, event.clientY);
    let dx = hit.x - drag.x0, dy = hit.y - drag.y0;
    if (!drag.moved && Math.hypot(dx, dy) < 2) return;
    drag.moved = true;
    const { original, layer } = drag;
    let points: Point[];
    if (drag.handle === null) {
      if (event.shiftKey) { if (Math.abs(dx) > Math.abs(dy)) dy = 0; else dx = 0; }
      // Whole-bar steps sideways; vertical moves in pixels so a log scale moves by percent.
      const spacing = layer.barSpacing(), stepX = Math.round(dx / spacing) * spacing;
      points = drag.pixels.map((pixel, index) => {
        const moved = layer.fromPixel(pixel.x + stepX, pixel.y + dy);
        if (!moved) return original.points[index];
        return { time: original.tool === "hline" || original.tool === "horizontal_ray" ? original.points[index].time : moved.time, price: original.tool === "vline" ? original.points[index].price : moved.price };
      });
    } else {
      const other = drag.pixels[drag.handle === 0 ? 1 : 0];
      const pointer = constrainDrawingPointer(original.tool, other, { x: hit.x, y: hit.y }, event.shiftKey);
      const snapped = this.snap(layer, pointer.x, pointer.y, event.shiftKey ? "off" : this.options.magnet);
      if (!snapped) return;
      points = original.points.map((point, index) => index !== drag.handle ? point
        : original.tool === "hline" ? { time: point.time, price: snapped.price } : original.tool === "vline" ? { time: snapped.time, price: point.price } : snapped);
    }
    layer.setPreview({ ...original, points });
  };

  private readonly up = () => {
    if (this.making) {
      const making = this.making;
      // Press, drag and release makes a two-point drawing in one gesture, as in TradingView.
      if (making.pressed && making.placed === 1 && overlayInteraction(making.drawing.tool) === "step" && this.draggedApart(making)) { making.placed = making.drawing.points.length; this.finish(); }
      else if (overlayInteraction(making.drawing.tool) === "continuous") { making.placed = making.drawing.points.length; this.finish(); }
      else making.pressed = false;
      return;
    }
    const drag = this.drag;
    if (!drag) return;
    this.drag = null;
    this.stopDocument();
    drag.layer.focus.dragging = false;
    const preview = drag.layer.previewDrawing;
    if (drag.moved && preview) this.events.commit([{ type: "drawing.update", id: drag.id, patch: { points: preview.points } }]);
    else drag.layer.setPreview(null);
  };

  private draggedApart(making: Making): boolean {
    const [a, b] = making.drawing.points;
    const pa = making.layer.toPixel(a), pb = making.layer.toPixel(b);
    return !!pa && !!pb && Math.hypot(pa.x - pb.x, pa.y - pb.y) > 6;
  }

  private readonly hover = (event: MouseEvent) => {
    if (this.drag || this.making) return;
    const hit = this.surface.paneAt(event.clientX, event.clientY);
    const layer = hit ? this.surface.layer(hit.pane) : null;
    const found = hit && layer ? layer.hit(hit.x, hit.y) : null;
    const id = found?.drawing.id ?? null, handle = found?.hit.kind === "handle" ? found.hit.handle.id : null;
    if (this.hovered && this.hovered !== layer) { this.hovered.focus.hovered = null; this.hovered.focus.hoveredHandle = null; this.hovered.redraw(); }
    this.hovered = layer;
    if (layer && (layer.focus.hovered !== id || layer.focus.hoveredHandle !== handle)) { layer.focus.hovered = id; layer.focus.hoveredHandle = handle; layer.redraw(); }
  };

  private readonly doubleClick = (event: MouseEvent) => {
    if (this.making && overlayInteraction(this.making.drawing.tool) === "multi_click") { event.stopPropagation(); this.finish(); return; }
    const hit = this.surface.paneAt(event.clientX, event.clientY);
    const found = hit ? this.surface.layer(hit.pane)?.hit(hit.x, hit.y) : null;
    if (found) { event.stopPropagation(); event.preventDefault(); this.events.open(found.drawing.id); }
  };

  private readonly contextMenu = (event: MouseEvent) => {
    const hit = this.surface.paneAt(event.clientX, event.clientY);
    const found = hit ? this.surface.layer(hit.pane)?.hit(hit.x, hit.y) : null;
    if (!found) return;
    event.stopPropagation(); event.preventDefault();
    this.select(found.drawing.id); this.events.select(found.drawing.id);
    this.events.menu(found.drawing.id, event.clientX, event.clientY);
  };

  private readonly key = (event: KeyboardEvent) => {
    if (event.key === "Escape") {
      if (this.making || this.drag) { this.cancel(); event.stopPropagation(); return; }
      if (this.selected) { this.select(null); this.events.select(null); event.stopPropagation(); }
      return;
    }
    if (event.key === "Enter" && this.making && overlayInteraction(this.making.drawing.tool) === "multi_click") { this.finish(); return; }
    if ((event.key === "Delete" || event.key === "Backspace") && this.selected) {
      const id = this.selected;
      this.select(null); this.events.select(null);
      this.events.commit([{ type: "drawing.delete", id }]);
      event.stopPropagation();
    }
  };

  // ── helpers ───────────────────────────────────────────────────────────

  private startDocument(): void {
    document.addEventListener("mousemove", this.move, true);
    document.addEventListener("mouseup", this.up, true);
  }
  private stopDocument(): void {
    document.removeEventListener("mousemove", this.move, true);
    document.removeEventListener("mouseup", this.up, true);
  }
}
