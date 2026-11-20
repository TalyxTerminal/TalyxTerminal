import { describe, expect, it, vi } from "vitest";

/** Just the DOM the peek uses: elements that hold text and children. */
class Node {
  className = ""; dataset: Record<string, string> = {}; style = { cssText: "" }; children: (Node | string)[] = [];
  set textContent(value: string) { this.children = [value]; }
  get textContent(): string { return this.children.map(child => typeof child === "string" ? child : child.textContent).join(""); }
  append(...nodes: (Node | string)[]) { this.children.push(...nodes); }
  remove() { return undefined; }
  getBoundingClientRect() { return { height: 40 }; }
}
vi.stubGlobal("document", { createElement: () => new Node() });

const set = new Map<string, unknown[]>();
vi.mock("./monaco", () => ({
  monaco: {
    MarkerSeverity: { Error: 8, Warning: 4, Info: 2 },
    Range: class { constructor(public startLineNumber: number, public startColumn: number, public endLineNumber: number, public endColumn: number) {} },
    editor: { setModelMarkers: (model: { id: string }, owner: string, list: unknown[]) => set.set(`${model.id}|${owner}`, list) },
  },
}));
import { showMarks } from "./editorMarks";

function fakeEditor() {
  const model = { id: "m", isDisposed: () => false, getLineCount: () => 20, getLineFirstNonWhitespaceColumn: () => 5, getLineMaxColumn: () => 30 };
  const zones = new Map<string, { afterLineNumber: number; heightInPx: number; domNode: Node }>();
  const decorations: unknown[][] = [];
  let next = 0;
  const editor = {
    getModel: () => model, getDomNode: () => new Node(), getLayoutInfo: () => ({ contentWidth: 600 }),
    createDecorationsCollection: () => ({ set: (list: unknown[]) => decorations.push(list), clear: () => decorations.push([]) }),
    changeViewZones: (change: (access: unknown) => void) => change({
      addZone: (zone: { afterLineNumber: number; heightInPx: number; domNode: Node }) => { const id = String(++next); zones.set(id, zone); return id; },
      removeZone: (id: string) => zones.delete(id),
    }),
  };
  return { editor, zones, decorations };
}

describe("line marks the module embedding the editor gives it", () => {
  it("marks the line under the view's own owner and shows the detail under it", async () => {
    set.clear();
    set.set("m|python", [{ message: "vol20 is not defined" }]);
    const { editor, zones, decorations } = fakeEditor();
    const marks = await showMarks(editor as never, "view:a");
    marks.show([{ line: 12, severity: "error", message: "NameError: name 'vol20' is not defined", title: "NameError", detail: "name 'vol20' is not defined", source: "run #4" }]);
    expect(set.get("m|view:a")).toEqual([expect.objectContaining({ severity: 8, startLineNumber: 12, startColumn: 5, endLineNumber: 12, endColumn: 30,
      message: "NameError: name 'vol20' is not defined (run #4)" })]);
    expect(set.get("m|python")).toHaveLength(1); // the language service's marks stay
    expect(decorations.at(-1)).toHaveLength(1);
    const [zone] = zones.values();
    expect([zone.afterLineNumber, zone.heightInPx]).toEqual([12, 48]);
    expect(zone.domNode.textContent).toBe("NameError name 'vol20' is not defined" + "run #4");
  });
  it("clears its own marks when they are taken away, leaving the others", async () => {
    set.clear();
    set.set("m|python", [{ message: "kept" }]);
    const { editor, zones } = fakeEditor();
    const marks = await showMarks(editor as never, "view:b");
    marks.show([{ line: 3, severity: "warning", message: "slow", detail: "slow" }]);
    marks.show([]);
    expect(set.get("m|view:b")).toEqual([]);
    expect(zones.size).toBe(0);
    expect(set.get("m|python")).toHaveLength(1);
    marks.show([{ line: 99, severity: "error", message: "past the end" }]);
    expect(set.get("m|view:b")).toEqual([]);
  });
});
