import { describe, expect, it } from "vitest";
import { renderToStaticMarkup } from "react-dom/server";
import { Markdown, type InlineRenderers } from "./markdown";

const renderers: InlineRenderers = {
  reference: (id, key) => <mark key={key}>{id}</mark>,
  link: (label, url, key) => <a key={key} data-url={url}>{label}</a>,
};
const html = (text: string) => renderToStaticMarkup(<Markdown text={text} renderers={renderers} />);

describe("answer markdown", () => {
  it("renders blocks and inline marks without treating text as HTML", () => {
    expect(html("**1. Oct 9** [[ref:abc]] <b>x</b>\nnext line")).toBe("<p><strong>1. Oct 9</strong> <mark>abc</mark> &lt;b&gt;x&lt;/b&gt;<br/>next line</p>");
    expect(html("- one\n- two `code`\n\n1. first")).toBe("<ul><li>one</li><li>two <code>code</code></li></ul><ol start=\"1\"><li>first</li></ol>");
    expect(html("```py\nx = 1\n```")).toBe("<pre class=\"agent-code\"><code>x = 1</code></pre>");
  });

  it("hands links to the caller and renders tables", () => {
    expect(html("See [ETF flows](https://farside.co.uk/btc) here")).toBe("<p>See <a data-url=\"https://farside.co.uk/btc\">ETF flows</a> here</p>");
    expect(html("| Day | Flow |\n|---|---:|\n| Oct 13 | −412 |")).toContain("<th>Day</th><th>Flow</th></tr></thead><tbody><tr><td>Oct 13</td><td>−412</td>");
  });

  it("drops a provider's own citation markers", () => {
    expect(html("Inflows rose[citation:2] on Monday[citation:a-1].")).toBe("<p>Inflows rose on Monday.</p>");
  });

  it("starts a table immediately after prose and keeps fenced pipes literal", () => {
    expect(html("Support levels\n| Level | Price |\n|---|---:|\n| Support | 10 |"))
      .toContain('<p>Support levels</p><div class="agent-md-table"><table>');
    expect(html("```\nText\n| a | b |\n|---|---|\n```"))
      .not.toContain("<table>");
  });
});
