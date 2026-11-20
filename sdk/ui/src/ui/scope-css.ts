/* Scopes a stylesheet to one container.
   The design tokens declare their variables on `:root`, which matches nothing
   inside a shadow tree, so the same source has to be re-rooted per container:
   the token block moves onto the container itself and every component rule is
   prefixed with the container selector. Custom properties inherit from there
   into everything the module renders. */

export function scopeCss(css: string, scope: string): string {
  const out: string[] = [];
  let index = 0;
  while (index < css.length) {
    const open = findOpen(css, index);
    if (open < 0) {
      const tail = css.slice(index).trim();
      if (tail) out.push(tail);
      break;
    }
    const prelude = css.slice(index, open).trim();
    const close = matchBrace(css, open);
    const body = css.slice(open + 1, close);
    const head = selector(prelude);
    if (/^@(media|supports|layer|container)\b/.test(head)) {
      out.push(`${head}{${scopeCss(body, scope)}}`);
    } else if (head.startsWith("@")) {
      out.push(`${head}{${body}}`);
    } else {
      out.push(`${scopePrelude(head, scope)}{${body}}`);
    }
    index = close + 1;
  }
  return out.join("\n");
}

function scopePrelude(prelude: string, scope: string): string {
  return splitSelectors(prelude)
    .map(part => {
      const trimmed = part.trim();
      if (trimmed === ":root" || trimmed === "html" || trimmed === "body") return scope;
      if (trimmed.startsWith(":root ")) return `${scope} ${trimmed.slice(6)}`;
      if (trimmed.startsWith(":root[") || trimmed.startsWith(":root:")) {
        return `${scope}${trimmed.slice(5)}`;
      }
      if (trimmed.startsWith("html[") || trimmed.startsWith("html.")) {
        return `${scope}${trimmed.slice(4)}`;
      }
      return `${scope} ${trimmed}`;
    })
    .join(", ");
}

function splitSelectors(prelude: string): string[] {
  const result: string[] = [];
  let depth = 0, start = 0, quote = "";
  for (let i = 0; i < prelude.length; i++) {
    const char = prelude[i];
    if (char === "\\") { i++; continue; }
    if (quote) { if (char === quote) quote = ""; continue; }
    if (char === '"' || char === "'") { quote = char; continue; }
    if (char === "(" || char === "[") depth++;
    if (char === ")" || char === "]") depth--;
    if (char === "," && depth === 0) { result.push(prelude.slice(start, i)); start = i + 1; }
  }
  result.push(prelude.slice(start));
  return result;
}

/** A prelude may carry the comments that preceded the rule, or comments between
    the selectors of a list. Those are not part of the selector and would
    otherwise be scoped into a selector that matches nothing. */
function selector(prelude: string): string {
  return prelude.replace(/\/\*[\s\S]*?\*\//g, " ").replace(/\s+/g, " ").trim();
}

/** Index just past the comment starting at `index`, or `index` if none starts there. */
function skipComment(css: string, index: number): number {
  if (css[index] !== "/" || css[index + 1] !== "*") return index;
  const end = css.indexOf("*/", index + 2);
  return end < 0 ? css.length : end + 2;
}

/** The next `{` outside a comment, or -1. A brace inside a comment is prose. */
function findOpen(css: string, from: number): number {
  for (let index = from; index < css.length; index += 1) {
    const skipped = skipComment(css, index);
    if (skipped !== index) { index = skipped - 1; continue; }
    if (css[index] === "{") return index;
  }
  return -1;
}

function matchBrace(css: string, open: number): number {
  let depth = 0;
  for (let index = open; index < css.length; index += 1) {
    const skipped = skipComment(css, index);
    if (skipped !== index) { index = skipped - 1; continue; }
    const character = css[index];
    if (character === "{") depth += 1;
    else if (character === "}") {
      depth -= 1;
      if (depth === 0) return index;
    }
  }
  throw new Error("Unbalanced braces in stylesheet");
}
