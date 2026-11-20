import { type ReactNode } from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it, vi } from "vitest";
import { PublishBody, PublishFooter, publishToast, type PublishPlan, type PublishProgress } from "./PublishSheet";

const html = (node: ReactNode) => renderToStaticMarkup(<>{node}</>);
const T = Date.UTC(2026, 2, 2);
const plan = (over: Partial<PublishPlan> = {}): PublishPlan => ({runId: "r23", name: "Short-term reversal", version: 1, runNumber: 23, formula: "-($close / Ref($close, 5) - 1)", files: [], checks: [], previous: null, compare: [], users: [], closest: null, blocked: null, ...over});
const compare = [{measure: "Rank IC", previous: 0.0153, current: 0.021, change: 0.0057, unit: "ic" as const}, {measure: "Long–short net", previous: 0.05, current: 0.083, change: 0.033, unit: "pct" as const},
  {measure: "Names a day", previous: 497, current: 451, change: -46, unit: "count" as const}, {measure: "Holding", previous: "3D", current: "5D", change: null, unit: "text" as const}];
const body = (over: Partial<PublishPlan> = {}, name = "Short-term reversal", auto = true) => html(<PublishBody plan={plan(over)} name={name} onName={vi.fn()} auto={auto} onAuto={vi.fn()} busy={false} onSubmit={vi.fn()} nameField={{current: null}} nameAsked={false} />);

describe("factor publish sheet", () => {
  it("name box has focus and Enter publishes, Esc closes", () => {
    const text = body();
    expect(text).toContain("autofocus");
    expect(text).toMatch(/v1 <span[^>]*>from run #23</);  // the row is named Version: the value is the version and the run, as the draft
    expect(text).not.toContain("Version v1");
    expect(text).toContain("Every trading day, after the close");
    expect(text).not.toContain("Enter to publish");
  });
  it("first version: no 'Compared with' and no 'Using' sections", () => {
    const text = body();
    expect(text).not.toContain("Compared with");
    expect(text).not.toContain("Using v");
  });
  it("later version: compared with v1 in a table, with percentages as pt, and the users still on v1", () => {
    const text = body({version: 2, previous: {version: 1, publishedAt: T}, compare, users: [{name: "Reversal top 50", kind: "strategy"}]});
    for (const wanted of ["Compared with v1, Mar 2", "Rank IC", "+3.30 pt", ">497<", ">451<", "−46", "Using v1", "They stay on v1 until each one switches."]) expect(text.replace(/&#x27;/g, "'")).toContain(wanted);
  });
  it("closest line turns orange at 0.7", () => {
    const orange = body({closest: {name: "Reversal", version: "v2", rho: 0.83, itemId: "i2"}, version: 2, previous: {version: 1, publishedAt: T}, compare});
    expect(orange).toContain("Closest in the library");
    expect(orange).toContain("0.83");
    expect(orange).toMatch(/data-attention/);
    expect(body({closest: {name: "Reversal", version: "v2", rho: 0.4, itemId: "i2"}})).not.toMatch(/data-attention/);
  });
  it("closest line shows a negative correlation as −0.78 and orange", () => {
    const text = body({closest: {name: "Reversal", version: "v2", rho: -0.78, itemId: null}});
    expect(text).toContain("−0.78");
    expect(text).toMatch(/data-attention/);
  });
  it("no closest line without a comparable factor", () => {
    expect(body({closest: null})).not.toContain("Closest in the library");
  });
  it("checks to review are listed and do not block Publish", () => {
    const text = body({checks: [{name: "Turnover", value: 0.31, need: "under 0.25", state: "watch"}, {name: "Group size", value: 20, need: "≥ 30", state: "watch"}]});
    expect(text).toContain("Checks to review");
    expect(text).toContain("Turnover");
    expect(html(<PublishFooter plan={plan()} progress={null} onCancel={vi.fn()} onPublish={vi.fn()} />)).toMatch(/<button(?![^>]*disabled)[^>]*>Publish v1/);
  });
  it("name taken by another kind: the reason under the box and no Publish", () => {
    const blocked = {reason: "Momentum is already a model in the library; choose another name."};
    expect(body({blocked}, "Momentum")).toContain("Momentum is already a model in the library; choose another name.");
    expect(html(<PublishFooter plan={plan({blocked})} progress={null} onCancel={vi.fn()} onPublish={vi.fn()} />)).not.toContain("Publish v1");
  });
  it("progress runs Computing values, Checking for future data 1 of 3 to 3 of 3, Writing to the library, with Cancel", () => {
    const at = (progress: PublishProgress) => html(<PublishFooter plan={plan()} progress={progress} onCancel={vi.fn()} onPublish={vi.fn()} />);
    expect(at({state: "running", stage: "values"})).toContain("Computing values for the whole period");
    expect(at({state: "running", stage: "future", step: 2, of: 3})).toContain("Checking for future data 2 of 3");
    expect(at({state: "running", stage: "commit"})).toContain("Writing to the library");
    expect(at({state: "running", stage: "values"})).toContain(">Cancel<");
  });
  it("blocked publish names the time: the red text and only Close", () => {
    const text = html(<PublishFooter plan={plan()} progress={{state: "failed", stage: "future", step: 2, of: 3, message: "AAPL's value on Mar 3, 2021 changes when data after Mar 3, 2021 is removed (12 values change). The formula or code reads later data.", code: "future_data"}} onCancel={vi.fn()} onPublish={vi.fn()} />);
    expect(text.replace(/&#x27;/g, "'")).toContain("Not published: AAPL's value on Mar 3, 2021 changes when data after Mar 3, 2021 is removed (12 values change).");
    expect(text).toContain("Checking for future data 2 of 3");
    expect(text).toContain(">Close<");
    expect(text).not.toContain(">Publish v1<");
  });
  it("success closes the sheet and toasts 'Published Short-term reversal v2 from #23'", () => {
    expect(publishToast("Short-term reversal", 2, 23)).toBe("Published Short-term reversal v2 from #23");
  });
  it("a second click while publishing shows Already publishing", () => {
    const text = html(<PublishFooter plan={plan()} progress={{state: "conflict", message: "Already publishing #23"}} onCancel={vi.fn()} onPublish={vi.fn()} />);
    expect(text).toContain("Already publishing #23");
  });
});
