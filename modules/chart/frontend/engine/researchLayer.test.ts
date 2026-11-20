import { expect, it } from "vitest";
import { alignedMarkers, alignedPoints, layerPanes, matchesLayer } from "./researchLayer";

const day = (d: number, hour = "13:30") => `2025-06-0${d}T${hour}:00Z`;
const open = (d: number) => Date.parse(day(d, "04:00")); // Market's US daily bar opens at local midnight.
it("aligns fills to actual bar opens, including every fill within a coarser candle", () => {
  const markers = [
    { time: day(2), side: "buy" as const, price: 104.25, quantity: 3 },
    { time: day(3, "15:00"), side: "sell" as const, price: 107, quantity: 2 },
  ];
  expect(alignedMarkers([open(2), open(3)], markers, open(2), open(4)).map(row => row.open)).toEqual([open(2), open(3)]);
  expect(alignedMarkers([open(2)], markers, open(2), open(4)).map(row => [row.open, row.marker.price]))
    .toEqual([[open(2), 104.25], [open(2), 107]]);
});

it("uses the last available value in each coarse candle, including a null, without moving the daily close to tomorrow", () => {
  const points = [{ time: day(2, "20:00"), value: 42 }, { time: day(2, "18:00"), value: 40 },
    { time: day(3, "20:00"), value: null }];
  expect(alignedPoints([open(2), open(3)], points, open(2), open(4))).toEqual([{ time: open(2), value: 42 }, { time: open(3) }]);
  expect(alignedPoints([open(2)], points, open(2), open(4))).toEqual([{ time: open(2) }]);
});

it("shows a layer only with its instrument and adjustment and lays equity above price", () => {
  const layer = { instrumentId: "SPY", adjustment: "split" as const };
  expect(matchesLayer(layer, "SPY", "split")).toBe(true);
  expect(matchesLayer(layer, "AAPL", "split")).toBe(false);
  expect(matchesLayer(layer, "SPY", "raw")).toBe(false);
  const panes = layerPanes([{ id: "price", height: 3, collapsed: false }], [{ id: "r1", panes: [
    { key: "equity", title: "Equity", position: "above" }, { key: "rank", title: "Rank", position: "below" }] }]);
  expect(panes.map(pane => pane.id)).toEqual(['["r1","equity"]', "price", '["r1","rank"]']);
});
