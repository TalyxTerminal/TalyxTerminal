import { createElement } from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { readFileSync } from "node:fs";
import { describe, expect, it, vi } from "vitest";
import * as Box from "./FormulaBox";

const FIELDS = [
  {name: "$close", label: "Close", group: "Prices", from: "Oct 2015", note: "adjusted for splits"},
  {name: "$volume", label: "Volume", group: "Prices", from: 1_443_657_600_000},
  {name: "$shares", label: "Shares outstanding", group: "Fundamentals", unavailable: "Market gives no shares outstanding for these names"},
  {name: "$funding", label: "Funding rate", group: "Crypto", unavailable: "Only for crypto perpetuals"},
];

describe("The formula box's field list", () => {
  it("fields list shows only what Market gives", () => {
    const FieldList = (Box as Record<string, unknown>).FieldList as (props: {fields: typeof FIELDS; onPick(name: string): void}) => JSX.Element;
    expect(FieldList).toBeTypeOf("function");
    const markup = renderToStaticMarkup(createElement(FieldList, {fields: FIELDS, onPick: vi.fn()}));
    expect(markup).toContain("$close");
    expect(markup).toContain("$volume");
    for (const gone of ["$shares", "$funding", "Shares outstanding", "Funding rate", "Market gives no shares", "Only for crypto perpetuals", "Fundamentals", "Crypto"]) expect(markup, gone).not.toContain(gone);
    expect(markup).toContain("from Oct 2015");  // the third column is only where the data starts
    expect(markup).not.toContain("adjusted for splits");  // the note is gone
    expect(markup).not.toContain("data-off");  // nothing greyed
  });
  it("a field is not listed grey: the source carries no unavailable reason in the list", () => {
    const source = readFileSync(new URL("./FormulaBox.tsx", import.meta.url), "utf8");
    expect(source).not.toContain("field.unavailable ??");
    expect(source).not.toContain("Turn this formula into a Python factor in Developer");
  });
});
