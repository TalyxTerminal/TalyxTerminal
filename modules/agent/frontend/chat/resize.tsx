import { useState, type CSSProperties, type KeyboardEvent, type PointerEvent } from "react";

/* Widths the user drags and that stay put sidebar and
   side panel: an edge to drag, the width remembered. Mockup: the width is kept
   in localStorage; the build keeps it in the panel's state. */

const KEY = "talyx.agent.width.";

/** A remembered width, or null for the stylesheet's own; `style(name)` sets it as the custom property the stylesheet reads. */
export function useDragWidth(key: string, min: number, max: number) {
  const [width, setWidth] = useState<number | null>(() => {
    const saved = Number(localStorage.getItem(KEY + key));
    return saved ? clamp(saved, min, max) : null;
  });
  const set = (value: number) => { const next = Math.round(clamp(value, min, max)); setWidth(next); localStorage.setItem(KEY + key, String(next)); };
  const reset = () => { setWidth(null); localStorage.removeItem(KEY + key); };
  return { width, set, reset, style: (name: string): CSSProperties | undefined => width === null ? undefined : { [name as string]: `${width}px` } as CSSProperties };
}

const clamp = (value: number, min: number, max: number) => Math.min(max, Math.max(min, value));

/** The draggable edge of a column: on its right side for a sidebar, its left for a rail. Double-click puts the width back. */
export function DragEdge({ side, label, drag }: { side: "left" | "right"; label: string; drag: ReturnType<typeof useDragWidth> }) {
  const down = (event: PointerEvent<HTMLDivElement>) => {
    const edge = event.currentTarget;
    const start = event.clientX, from = edge.parentElement!.getBoundingClientRect().width;
    edge.setPointerCapture(event.pointerId);
    edge.dataset.dragging = "";
    const move = (next: globalThis.PointerEvent) => drag.set(from + (side === "right" ? next.clientX - start : start - next.clientX));
    const up = () => { delete edge.dataset.dragging; edge.removeEventListener("pointermove", move); edge.removeEventListener("pointerup", up); edge.removeEventListener("pointercancel", up); };
    edge.addEventListener("pointermove", move);
    edge.addEventListener("pointerup", up);
    edge.addEventListener("pointercancel", up);
    event.preventDefault();
  };
  const keys = (event: KeyboardEvent<HTMLDivElement>) => {
    if (event.key !== "ArrowLeft" && event.key !== "ArrowRight") return;
    event.preventDefault();
    const now = event.currentTarget.parentElement!.getBoundingClientRect().width;
    drag.set(now + ((event.key === "ArrowRight") === (side === "right") ? 16 : -16));
  };
  return <div className="agent-edge" data-side={side} role="separator" aria-orientation="vertical" aria-label={label} tabIndex={0}
    onPointerDown={down} onKeyDown={keys} onDoubleClick={drag.reset} />;
}
