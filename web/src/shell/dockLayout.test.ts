import { describe, expect, it } from "vitest";
import type { SerializedDockview } from "dockview-react";
import { layoutSummary } from "./dockLayout";

/* The summary saved with each workspace: every panel's module and its place as
   shares of the workspace, read from the arrangement as saved. */

type Node = { type: "leaf"; data: { id: string; views: string[]; activeView: string }; size: number } | { type: "branch"; data: Node[]; size: number };
const leaf = (id: string, views: string[], size: number, activeView = views[0]): Node => ({ type: "leaf", data: { id, views, activeView }, size });
const layout = (root: Node[], width: number, height: number, orientation = "HORIZONTAL") => ({
  grid: { root: { type: "branch", data: root, size: orientation === "HORIZONTAL" ? height : width }, width, height, orientation },
  panels: {},
} as unknown as SerializedDockview);
const modules: Record<string, string> = { a: "chart", b: "agent", c: "trade", d: "market" };
const moduleOf = (id: string) => modules[id];

describe("layout summary", () => {
  it("a single panel fills the workspace", () => {
    expect(layoutSummary(layout([leaf("g", ["a"], 1600)], 1600, 900), moduleOf)).toEqual({ tiles: [{ module: "chart", x: 0, y: 0, w: 1, h: 1 }] });
  });

  it("three columns keep their widths as shares", () => {
    expect(layoutSummary(layout([leaf("g1", ["a"], 300), leaf("g2", ["b"], 500), leaf("g3", ["c"], 200)], 1000, 700), moduleOf)).toEqual({ tiles: [
      { module: "chart", x: 0, y: 0, w: 0.3, h: 1 },
      { module: "agent", x: 0.3, y: 0, w: 0.5, h: 1 },
      { module: "trade", x: 0.8, y: 0, w: 0.2, h: 1 },
    ] });
  });

  it("a nested split, a stack of tabs (the front one last), at a size other than the window's", () => {
    const nested = layout([
      leaf("g1", ["a"], 400),
      { type: "branch", size: 600, data: [leaf("g2", ["b"], 300), leaf("g3", ["c", "d"], 600, "c")] },
    ], 1000, 900);
    expect(layoutSummary(nested, moduleOf)).toEqual({ tiles: [
      { module: "chart", x: 0, y: 0, w: 0.4, h: 1 },
      { module: "agent", x: 0.4, y: 0, w: 0.6, h: 0.3333 },
      { module: "market", x: 0.4, y: 0.3333, w: 0.6, h: 0.6667 },
      { module: "trade", x: 0.4, y: 0.3333, w: 0.6, h: 0.6667 },
    ] });
  });

  it("a vertical root and at most 64 tiles", () => {
    const tall = layout([leaf("g1", ["a"], 250), leaf("g2", ["b"], 750)], 1200, 1000, "VERTICAL");
    expect(layoutSummary(tall, moduleOf).tiles).toEqual([
      { module: "chart", x: 0, y: 0, w: 1, h: 0.25 },
      { module: "agent", x: 0, y: 0.25, w: 1, h: 0.75 },
    ]);
    const many = layout(Array.from({ length: 70 }, (_, index) => leaf(`g${index}`, ["a"], 10)), 700, 100);
    expect(layoutSummary(many, moduleOf).tiles).toHaveLength(64);
  });
});
