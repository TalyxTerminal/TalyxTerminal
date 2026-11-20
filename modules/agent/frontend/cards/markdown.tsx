import { Fragment, type ReactNode } from "react";

/* The markdown a model writes, rendered as React elements: paragraphs,
   headings, lists, quotes, tables, code, emphasis and links. Nothing from the
   text is ever treated as HTML. Reference markers ([[ref:id]]) and links are
   handed to the caller, which decides what a click may open. */

export interface InlineRenderers {
  reference: (id: string, key: string) => ReactNode;
  link: (label: string, url: string, key: string) => ReactNode;
}

const INLINE = /\[\[ref:([A-Za-z0-9_-]+)\]\]|`([^`\n]+)`|\*\*([^*\n]+?)\*\*|__([^_\n]+?)__|\[([^\]\n]+)\]\((https?:\/\/[^)\s]+)\)|(?<![\w*])\*(?![\s*])([^*\n]+?)\*(?![\w*])|(?<![\w_])_(?![\s_])([^_\n]+?)_(?![\w_])/g;

export function inline(text: string, renderers: InlineRenderers, prefix = "i"): ReactNode[] {
  const out: ReactNode[] = [];
  let last = 0, index = 0;
  for (const match of text.matchAll(INLINE)) {
    const at = match.index!;
    if (at > last) out.push(text.slice(last, at));
    const key = `${prefix}-${index++}`;
    if (match[1]) out.push(renderers.reference(match[1], key));
    else if (match[2]) out.push(<code key={key}>{match[2]}</code>);
    else if (match[3] || match[4]) out.push(<strong key={key}>{inline(match[3] ?? match[4], renderers, key)}</strong>);
    else if (match[5]) out.push(renderers.link(match[5], match[6], key));
    else out.push(<em key={key}>{inline(match[7] ?? match[8], renderers, key)}</em>);
    last = at + match[0].length;
  }
  if (last < text.length) out.push(text.slice(last));
  return out;
}

function lines(text: string, renderers: InlineRenderers, prefix: string): ReactNode[] {
  return text.split("\n").flatMap((line, index) => index ? [<br key={`${prefix}-br${index}`} />, ...inline(line, renderers, `${prefix}-${index}`)] : inline(line, renderers, `${prefix}-${index}`));
}

const LIST = /^(\s*)([-*+]|\d+[.)])\s+(.*)$/;
const TABLE_RULE = /^\s*\|?\s*:?-{2,}:?\s*(\|\s*:?-{2,}:?\s*)*\|?\s*$/;

function cells(row: string): string[] {
  return row.trim().replace(/^\|/, "").replace(/\|$/, "").split("|").map(cell => cell.trim());
}

function tableAt(source: string[], index: number): boolean {
  return source[index].includes("|") && index + 1 < source.length && TABLE_RULE.test(source[index + 1]);
}

export function Markdown({ text, renderers }: { text: string; renderers: InlineRenderers }) {
  // Some providers' own search leaves "[citation:3]" markers; the answer's sources are listed under it instead.
  const source = text.replace(/\r\n?/g, "\n").replace(/\[citation:[^\]\n]*\]/g, "").split("\n");
  const blocks: ReactNode[] = [];
  let index = 0;
  while (index < source.length) {
    const line = source[index], key = `b${index}`;
    if (!line.trim()) { index += 1; continue; }
    const fence = line.match(/^\s*(```|~~~)/);
    if (fence) {
      const body: string[] = [];
      index += 1;
      while (index < source.length && !source[index].trim().startsWith(fence[1])) body.push(source[index++]);
      index += 1;
      blocks.push(<pre key={key} className="agent-code"><code>{body.join("\n")}</code></pre>);
      continue;
    }
    const heading = line.match(/^\s*#{1,6}\s+(.*)$/);
    if (heading) {
      blocks.push(<p key={key} className="agent-md-heading">{inline(heading[1], renderers, key)}</p>);
      index += 1;
      continue;
    }
    if (/^\s*(?:(?:-\s*){3,}|(?:\*\s*){3,}|(?:_\s*){3,})$/.test(line)) { blocks.push(<hr key={key} />); index += 1; continue; }
    if (tableAt(source, index)) {
      const head = cells(line), rows: string[][] = [];
      index += 2;
      while (index < source.length && source[index].includes("|") && source[index].trim()) rows.push(cells(source[index++]));
      blocks.push(<div key={key} className="agent-md-table"><table>
        <thead><tr>{head.map((cell, column) => <th key={column}>{inline(cell, renderers, `${key}-h${column}`)}</th>)}</tr></thead>
        <tbody>{rows.map((row, at) => <tr key={at}>{head.map((_, column) => <td key={column}>{inline(row[column] ?? "", renderers, `${key}-${at}-${column}`)}</td>)}</tr>)}</tbody>
      </table></div>);
      continue;
    }
    if (/^\s*>/.test(line)) {
      const body: string[] = [];
      while (index < source.length && /^\s*>/.test(source[index])) body.push(source[index++].replace(/^\s*>\s?/, ""));
      blocks.push(<blockquote key={key}>{lines(body.join("\n"), renderers, key)}</blockquote>);
      continue;
    }
    const item = line.match(LIST);
    if (item) {
      const ordered = /\d/.test(item[2]), entries: string[] = [];
      while (index < source.length) {
        const next = source[index].match(LIST);
        if (next && /\d/.test(next[2]) === ordered && !next[1]) entries.push(next[3]);
        else if (source[index].trim() && /^\s+/.test(source[index]) && entries.length) entries[entries.length - 1] += "\n" + source[index].trim();
        else break;
        index += 1;
      }
      const children = entries.map((entry, at) => <li key={at}>{lines(entry, renderers, `${key}-${at}`)}</li>);
      blocks.push(ordered ? <ol key={key} start={Number.parseInt(item[2], 10)}>{children}</ol> : <ul key={key}>{children}</ul>);
      continue;
    }
    const body: string[] = [];
    while (index < source.length && source[index].trim() && !LIST.test(source[index]) && !tableAt(source, index) && !/^\s*(```|~~~|#{1,6}\s|>)/.test(source[index])) body.push(source[index++]);
    blocks.push(<p key={key}>{lines(body.join("\n"), renderers, key)}</p>);
  }
  return <Fragment>{blocks}</Fragment>;
}
