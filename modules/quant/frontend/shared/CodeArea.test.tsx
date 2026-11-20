import { createElement } from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it, vi } from "vitest";
import { CodeArea, TREE_WIDTH, fileUri, toMarkers } from "./CodeArea";

// The project view is Developer's: it stands in as an element that keeps the props it was given.
const given: Record<string, unknown>[] = [];
vi.mock("@talyx/sdk", async importOriginal => {
  const original = await importOriginal<Record<string, unknown>>();
  return {...original, ModuleView: (props: {id: string; props: Record<string, unknown>}) => {given.push({id: props.id, ...props.props}); return <i data-view={props.id} />;}};
});
const base = {ctx: {} as never, projectId: "p1", rootUri: "file:///d/objects/s1", entryUri: "file:///d/objects/s1/strategy.py", onTree: vi.fn(), onOpenDeveloper: vi.fn()};
const marks = [{uri: "file:///d/objects/s1/strategy.py", line: 7, message: "RuntimeError: boom", source: "Run #3"}, {uri: "file:///d/objects/s1/helpers.py", line: 2, message: "NameError"}];

describe("The code area", () => {
  it("is Developer's project view, with the tree closed until asked for", () => {
    given.length = 0;
    const closed = renderToStaticMarkup(createElement(CodeArea, {...base, tree: false}));
    expect(closed).toContain('data-view="developer.project_editor"');
    expect(given.at(-1)).toMatchObject({id: "developer.project_editor", tree: false, rootUri: base.rootUri, projectId: "p1", file: base.entryUri});
    expect(closed).toContain("Show files");
    const opened = renderToStaticMarkup(createElement(CodeArea, {...base, tree: true}));
    expect(opened).toContain("Hide files");
    expect(opened).toContain("data-tree");
    expect(given.at(-1)).toMatchObject({tree: true});
  });
  it("opening the tree asks for 150 px more, not a share of the code's room", () => {
    expect(TREE_WIDTH).toBe(150);
  });
  it("gives the errors of a failed run by file, and says how many", () => {
    given.length = 0;
    const markup = renderToStaticMarkup(createElement(CodeArea, {...base, tree: false, marks}));
    expect(markup).toContain("2 errors");
    const sent = given.at(-1)?.markers as {uri: string; line: number; severity: string}[];
    expect(sent.map(item => [item.uri.split("/").at(-1), item.line, item.severity])).toEqual([["strategy.py", 7, "error"], ["helpers.py", 2, "error"]]);
    expect(renderToStaticMarkup(createElement(CodeArea, {...base, tree: false, marks: marks.slice(0, 1)}))).toContain("1 error<");
    expect(renderToStaticMarkup(createElement(CodeArea, {...base, tree: false}))).not.toContain("error");
  });
  it("a jump to a line carries a request id so the same line can be asked for twice", () => {
    given.length = 0;
    renderToStaticMarkup(createElement(CodeArea, {...base, tree: false, line: 17, request: "r2"}));
    expect(given.at(-1)).toMatchObject({position: {line: 17}, positionRequestId: "r2"});
  });
  it("offers Open in Developer and Hide code when it can be hidden", () => {
    const markup = renderToStaticMarkup(createElement(CodeArea, {...base, tree: false, onHide: vi.fn()}));
    expect(markup).toContain("Open in Developer");
    expect(markup).toContain("Hide code");
    expect(renderToStaticMarkup(createElement(CodeArea, {...base, tree: false}))).not.toContain("Hide code");
  });
  it("names a file of the project by its uri", () => {
    expect(fileUri("file:///d/objects/s1/", "helpers.py")).toBe("file:///d/objects/s1/helpers.py");
    expect(fileUri("file:///d/objects/s1", "sub\\a.py")).toBe("file:///d/objects/s1/sub/a.py");
    expect(toMarkers([{uri: "u", line: 1, message: "m"}])).toEqual([{uri: "u", line: 1, severity: "error", message: "m"}]);
  });
});
