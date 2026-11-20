/* Views that read a chart's feed in place (legend, data window) redraw when it changes, at most once a frame. */
import { useEffect, useReducer } from "react";
import type { ChartEntry } from "../store";

export function useFeedVersion(entry: ChartEntry | undefined): number {
  const [version, bump] = useReducer((value: number) => value + 1, 0);
  useEffect(() => {
    if (!entry) return;
    let frame = 0;
    const stop = entry.feed.listen(() => { if (!frame) frame = requestAnimationFrame(() => { frame = 0; bump(); }); });
    return () => { stop(); cancelAnimationFrame(frame); };
  }, [entry?.feed]);
  return version;
}
