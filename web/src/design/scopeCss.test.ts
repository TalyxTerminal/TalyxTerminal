import { describe, expect, it } from "vitest";
import { scopeCss } from "../../../sdk/ui/src/ui/scope-css";

describe("scopeCss", () => {
  it("preserves selector lists nested in functional selectors and attributes", () => {
    expect(scopeCss('.tx-root :where(button,input),[data-label="a,b"] { color: inherit; }', ':host'))
      .toBe(':host .tx-root :where(button,input), :host [data-label="a,b"]{ color: inherit; }');
  });
  it("re-roots :root, html and body selectors onto the scope", () => {
    expect(scopeCss(":root { --tx-bg: #000; }", "#app")).toBe("#app{ --tx-bg: #000; }");
    expect(scopeCss("html { color: red; }", "#app")).toBe("#app{ color: red; }");
    expect(scopeCss("body { margin: 0; }", "#app")).toBe("#app{ margin: 0; }");
  });

  it("moves :root custom properties onto the scope and prefixes component rules", () => {
    const css = ":root { --tx-up: #0a0; }\n.talyx-chart { color: var(--tx-up); }";
    expect(scopeCss(css, "talyx-shell")).toBe([
      "talyx-shell{ --tx-up: #0a0; }",
      "talyx-shell .talyx-chart{ color: var(--tx-up); }",
    ].join("\n"));
  });

  it("rewrites whole-selector forms and prefixes the rest of a compound", () => {
    expect(scopeCss(":root::before { content: ''; }", "#app")).toBe("#app::before{ content: ''; }");
    expect(scopeCss(":root:hover { color: red; }", "#app")).toBe("#app:hover{ color: red; }");
    expect(scopeCss("html[data-theme='dark'] { color: red; }", "#app")).toBe("#app[data-theme='dark']{ color: red; }");
    expect(scopeCss("html.dark { color: red; }", "#app")).toBe("#app.dark{ color: red; }");
    expect(scopeCss(":root .panel { color: red; }", "#app")).toBe("#app .panel{ color: red; }");
  });

  it("prepends the scope to every selector in a comma-separated list", () => {
    expect(scopeCss(".a, .b { color: red; }", "#app")).toBe("#app .a, #app .b{ color: red; }");
    expect(scopeCss(":root, html, .a { color: red; }", "#app")).toBe("#app, #app, #app .a{ color: red; }");
  });

  it("recurses into conditional at-rules", () => {
    expect(scopeCss("@media (min-width: 40em) { .a { color: red; } }", "#app"))
      .toBe("@media (min-width: 40em){#app .a{ color: red; }}");
    expect(scopeCss("@supports (display: grid) { @media (prefers-color-scheme: dark) { .a { color: red; } } }", "#app"))
      .toBe("@supports (display: grid){@media (prefers-color-scheme: dark){#app .a{ color: red; }}}");
  });

  it("recurses into @layer and @container", () => {
    expect(scopeCss("@layer base { .a { color: red; } }", "#app"))
      .toBe("@layer base{#app .a{ color: red; }}");
    expect(scopeCss("@container sidebar (min-width: 200px) { .a { color: red; } }", "#app"))
      .toBe("@container sidebar (min-width: 200px){#app .a{ color: red; }}");
  });

  it("passes unknown at-rules through untouched", () => {
    expect(scopeCss("@font-face { font-family: X; src: url(x.woff2); }", "#app"))
      .toBe("@font-face{ font-family: X; src: url(x.woff2); }");
    expect(scopeCss("@keyframes spin { from { opacity: 0; } to { opacity: 1; } }", "#app"))
      .toBe("@keyframes spin{ from { opacity: 0; } to { opacity: 1; } }");
  });

  it("leaves selectors inside an unknown at-rule unprefixed", () => {
    expect(scopeCss("@keyframes spin { from { opacity: 0; } }", "#app")).not.toContain("#app .from");
    expect(scopeCss("@page { margin: 1cm; }", "#app")).toBe("@page{ margin: 1cm; }");
  });

  it("handles empty input and whitespace-only input", () => {
    expect(scopeCss("", "#app")).toBe("");
    expect(scopeCss("   \n  ", "#app")).toBe("");
  });

  it("drops a trailing whitespace-only tail after the last rule", () => {
    expect(scopeCss(".a { color: red; }\n   ", "#app")).toBe("#app .a{ color: red; }");
  });

  it("keeps a trailing at-rule-less tail", () => {
    expect(scopeCss(".a { color: red; }\n/* keep */", "#app")).toBe("#app .a{ color: red; }\n/* keep */");
  });

  it("throws on unbalanced braces", () => {
    expect(() => scopeCss(".a { color: red;", "#app")).toThrow("Unbalanced braces in stylesheet");
  });
});
