import { expect, it } from "vitest";
import type { ModuleContext } from "@talyx/sdk";
import { ChartFeed } from "./feed";
import { chartEntry, commit, store, type ChartEntry } from "./store";
import type { ChartState, Command } from "./wire";

it("folds a typed field's keystrokes that wait behind another change into its newest value", async () => {
  const document = { series: { seriesId: "series" } } as unknown as ChartState["document"];
  const chart = (revision: number) => ({ chartId: "chart", revision, document, drawingGroup: "group", canUndo: true, canRedo: false }) as ChartState;
  store.setState({ charts: { chart: { chart: chart(1), feed: new ChartFeed() } as ChartEntry } });
  const sent: { baseRevision: number; commands: Command[] }[] = [];
  const answers: (() => void)[] = [];
  const ctx = { routes: { fetch: (_path: string, init: { body: string }) => new Promise(resolve => {
    const body = JSON.parse(init.body);
    sent.push(body);
    answers.push(() => resolve({ ok: true, json: async () => chart(body.baseRevision + 1) }));
  }) } } as unknown as ModuleContext;
  const text = (value: string): Command[] => [{ type: "drawing.update", id: "d", patch: { text: value } }];
  const first = commit(ctx, "chart", [{ type: "drawing.update", id: "d", patch: { locked: true } }]);
  const typed = ["H", "He", "Hel", "Hell", "Hello"].map(value => commit(ctx, "chart", text(value), "d.text"));
  const other = commit(ctx, "chart", [{ type: "drawing.update", id: "e", patch: { locked: true } }]);
  await new Promise(resolve => setTimeout(resolve));
  expect(sent).toHaveLength(1);
  answers[0]();
  await new Promise(resolve => setTimeout(resolve));
  expect(sent).toHaveLength(2);
  expect(sent[1]).toMatchObject({ baseRevision: 2, commands: text("Hello") });
  answers[1]();
  await new Promise(resolve => setTimeout(resolve));
  answers[2]();
  expect(await Promise.all([first, ...typed, other])).toEqual(Array(7).fill(true));
  expect(sent.map(item => item.baseRevision)).toEqual([1, 2, 3]);
  expect(chartEntry("chart").chart!.revision).toBe(4);
});
