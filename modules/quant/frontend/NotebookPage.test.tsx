import { readFileSync } from "node:fs";
import { createElement } from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it } from "vitest";
import { Toolbar } from "./notebook/Toolbar";

const page = readFileSync(new URL("./NotebookPage.tsx", import.meta.url), "utf8");
const parts = ["Toolbar", "SidePanel", "ExtractSheet", "RunBand"].map(name => readFileSync(new URL(`./notebook/${name}.tsx`, import.meta.url), "utf8"));

describe("The notebook page", () => {
  it("page_menu_items: Packages comes once, from Quant's own menu, and the file opens in Developer", () => {
    // The page's menu is its own items, then Quant's (...menu), which already holds Packages: a second one was a duplicate key.
    // "Open in VS Code" opened the same Developer target as "Open file in Developer", so it went too.
    const shell = readFileSync(new URL("./QuantShell.tsx", import.meta.url), "utf8");
    expect(page).toContain("...menu]");
    expect(page).not.toContain('id: "packages"');
    expect(shell.match(/id: "packages"/g)).toHaveLength(1);
    expect(page).toContain('label: t("Open file in Developer")');
    expect(page).not.toContain("Open in VS Code");
    // Save and Clear all outputs open the menu now: no separator above the first item.
    expect(page).not.toMatch(/id: "save"[^}]*separated/);
    expect(page).not.toMatch(/id: "clear"[^}]*separated/);
  });
  it("a notebook opened from a run runs its first cell once the kernel is idle, and says when the run is gone", () => {
    expect(page).toContain("autoRan");
    expect(page).toContain('current.kernel.state !== "idle"');
    expect(page).toContain("deleted: runGone");
  });
  it("the environment list always shows, one item when there is one", () => {
    expect(page).toContain("environments.length > 0");
    expect(page).toContain("environments.map(item => <option");
  });
  it("no_title_outside_definitions: the new parts carry no explanatory hover text", () => {
    // the one hover text is the draft's: which cell set a variable
    expect(parts.join("\n").match(/\btitle=\{?[^ >]*/g)).toEqual(["title={item.fromCell", "title={(count"]);
    expect(page).not.toContain("The kernel's environment");
    expect(page).not.toContain("Tick cells in the left margin first");
  });
  it("the tool row shows the selected count and Extract; the state word sits at its right end", () => {
    const html = renderToStaticMarkup(createElement(Toolbar, {status: "Python 3.12 · idle", busy: false, canRestart: true, selected: 2, onRunAll: () => undefined, onInterrupt: () => undefined, onRestart: () => undefined, onAdd: () => undefined, onExtract: () => undefined, onClear: () => undefined}));
    expect(html).toContain("2 cells selected");
    expect(html).toContain("Extract to new object");
    expect(html.indexOf("Python 3.12 · idle")).toBeGreaterThan(html.indexOf("Extract to new object"));
    expect(html).not.toContain("Interrupt");   // nothing runs: no greyed Interrupt, it is not there
  });
  it("checkboxes_on_hover_or_extract: the tick shows on hover, once something is ticked, and on ticked cells", () => {
    const css = readFileSync(new URL("./notebook/notebook.css", import.meta.url), "utf8");
    expect(css).toMatch(/\.qv-notebook-tick \{[^}]*opacity: 0/);
    expect(css).toContain(".qx-nb-cell[data-ticking] .qv-notebook-tick");
  });
});

describe("environment install flow and controls", () => {
  it("the install confirmation names the offer, offers a build choice, and installs through D's action with the chosen build", () => {
    expect(page).toContain("Install the deep-learning environment ({label}, {size}) and restart the kernel in it?");
    expect(page).toContain('"Install and restart"');
    expect(page).toContain("installDeep(deepChoice, wanted)");
    expect(page).toContain("nb.installDeep(variant)");
    expect(page).toContain('variant === "cpu" ? "CPU" : t("Intel GPU")');
  });
  it("a failed install says why and offers Retry; the kernel stays where it was", () => {
    expect(page).toContain("Deep-learning environment failed: {failure}");
    expect(page).toContain('>{t("Retry")}</TxButton>');
  });
  it("a running cell is named in the confirmation", () => {
    expect(page).toContain("is running and will be stopped");
  });
});

it("what a notebook opens (its run, a backtest's run, an extracted object) goes to a panel of its own, so the notebook's kernel and variables stay", () => {
  const shell = readFileSync(new URL("./QuantShell.tsx", import.meta.url), "utf8");
  expect(shell).toMatch(/<NotebookPage [^>]*onOpenObject=\{openBeside\}/);
  expect(shell).toMatch(/const openBeside = [^\n]*ctx\.platform\.panels\.open\(\{type: "quant\.workspace", state: \{objectId, \.\.\.\(runId \? \{runId, tab: "report"\} : \{\}\)\}\}\)/);
});
