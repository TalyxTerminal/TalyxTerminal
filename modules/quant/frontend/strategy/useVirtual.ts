/* Rows of one height in a scrolling box: only those in view (and a few around them) are drawn; the rest is empty space of the same size. */
import { useCallback, useEffect, useRef, useState } from "react";

const OVERSCAN = 12;

export function windowOf(count: number, rowHeight: number, scrollTop: number, height: number) {
  const start = Math.max(0, Math.floor(scrollTop / rowHeight) - OVERSCAN);
  const end = Math.min(count, Math.ceil((scrollTop + height) / rowHeight) + OVERSCAN);
  return {start, end, before: start * rowHeight, after: Math.max(0, count - end) * rowHeight};
}

/** `ref` goes on the scrolling box; `scrollTo(index)` brings a row to the top; `end` tells how far the reader has got (to read the next page). */
export function useVirtual(count: number, rowHeight: number, fallbackHeight = 600) {
  const ref = useRef<HTMLDivElement>(null);
  const [view, setView] = useState({top: 0, height: fallbackHeight});
  const measure = useCallback(() => {
    const box = ref.current;
    if (box) setView(previous => previous.top === box.scrollTop && previous.height === (box.clientHeight || fallbackHeight) ? previous : {top: box.scrollTop, height: box.clientHeight || fallbackHeight});
  }, [fallbackHeight]);
  useEffect(() => {
    const box = ref.current;
    if (!box) return;
    measure();
    box.addEventListener("scroll", measure, {passive: true});
    const observer = typeof ResizeObserver === "undefined" ? null : new ResizeObserver(measure);
    observer?.observe(box);
    return () => {box.removeEventListener("scroll", measure); observer?.disconnect();};
  }, [measure]);
  const scrollTo = useCallback((index: number) => {if (ref.current) ref.current.scrollTop = Math.max(0, index * rowHeight - rowHeight * 2);}, [rowHeight]);
  return {ref, scrollTo, ...windowOf(count, rowHeight, view.top, view.height)};
}
