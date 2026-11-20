/* The rules every module front end keeps: module CSS lays things out and
   nothing else, colours and fonts come from the tokens, no module draws its own scrollbars or hand-rolls the shared
   controls, and no module reaches into the platform's web/src. A module waiting for its whole redesign carries a
   `frontend/UI_PENDING` file and none of the per-module rules apply to it; the redesign deletes the file and then they
   must all pass. The two platform-wide rules at the end hold regardless. */
import { existsSync, readdirSync, readFileSync, statSync } from "node:fs";
import { join, relative } from "node:path";
import { fileURLToPath } from "node:url";
import { describe, expect, it } from "vitest";
import { txIconNames } from "../../../sdk/ui/src/ui/icons";
import { talyxTokensCss } from "../../../sdk/ui/src/ui/tokens.css";

const root = fileURLToPath(new URL("../../../", import.meta.url));
const posix = (path: string) => relative(root, path).split("\\").join("/");

function walk(dir: string, keep: (path: string) => boolean): string[] {
  const out: string[] = [];
  for (const name of readdirSync(dir)) {
    if (name === "node_modules") continue;
    const path = join(dir, name);
    if (statSync(path).isDirectory()) out.push(...walk(path, keep));
    else if (keep(path)) out.push(path);
  }
  return out;
}

const isTest = (path: string) => /\.test\.[^.]+$/.test(path);
const isCss = (path: string) => /\.css(\.txt)?$/.test(path);
const isScript = (path: string) => /\.tsx?$/.test(path);

/** Module name -> its front-end files (tests left out). */
const modules = new Map(readdirSync(join(root, "modules"))
  .filter(name => existsSync(join(root, "modules", name, "frontend")))
  .map(name => [name, walk(join(root, "modules", name, "frontend"), path => (isCss(path) || isScript(path)) && !isTest(path))]));
const pending = (module: string) => existsSync(join(root, "modules", module, "frontend", "UI_PENDING"));
const read = (path: string) => readFileSync(path, "utf8");

/** Files allowed to break one rule, each with the reason. */
const allowed: Record<"colour" | "font" | "native", Record<string, string>> = {
  colour: {},
  font: {},
  native: {"modules/agent/frontend/chat/Composer.tsx": "The multiline editor sits inside a shared composer surface. TxTextarea adds a second border."},
};

/* ── reading CSS ─────────────────────────────────────────────────────────── */

const stripCssComments = (css: string) => css.replace(/\/\*[\s\S]*?\*\//g, "");
/** Line and block comments out of a script; `//` right after a colon or inside a quote (URLs) is kept. */
const stripScriptComments = (code: string) => code.replace(/\/\*[\s\S]*?\*\//g, "").replace(/(^|[^:"'`\w\\])\/\/[^\n]*/g, "$1");

interface Rule { selector: string; body: string }
/** Flat rules of a stylesheet: an @media or @supports wrapper is passed through, so the rules inside are read as rules. */
function cssRules(css: string): Rule[] {
  const rules: Rule[] = [];
  const text = stripCssComments(css);
  let selector: string | null = null, start = 0;
  for (let at = 0; at < text.length; at += 1) {
    if (text[at] === "{") {
      const head = text.slice(start, at).trim();
      if (!head.startsWith("@")) selector = head;
      start = at + 1;
    } else if (text[at] === "}") {
      if (selector !== null) rules.push({ selector, body: text.slice(start, at) });
      selector = null;
      start = at + 1;
    }
  }
  return rules;
}
const declarations = (body: string) => body.split(";").map(part => part.trim()).filter(part => part.includes(":")).map(part => {
  const colon = part.indexOf(":");
  return { property: part.slice(0, colon).trim().toLowerCase(), value: part.slice(colon + 1).trim() };
});
/** Every declared value in a stylesheet (custom property values too), with its property. */
const cssValues = (css: string) => cssRules(css).flatMap(rule => declarations(rule.body));

/* ── colour ──────────────────────────────────────────────────────────────── */

const HEX = "#(?:[0-9a-fA-F]{8}|[0-9a-fA-F]{6}|[0-9a-fA-F]{3,4})";
const literalHexInValue = new RegExp(`(?:^|[\\s,(])${HEX}(?![\\w-])`);
const literalHexInScript = new RegExp(`(?:["'\`]${HEX}["'\`]|(?:[:,(]|\\s)\\s*${HEX}(?=[\\s;,)"'\`}]))`, "g");
const colourFunction = /\b(?:rgba?|hsla?)\(\s*[\d.]/gi;
/** A var() whose fallback is a colour literal or keyword; a fallback length such as `0px` is the variable rule's business. */
const literalFallback = /var\(\s*--[\w-]+\s*,\s*(?:#|rgba?\(|hsla?\(|(?:transparent|currentcolor|white|black|red|green|blue|gr[ae]y|orange|yellow|silver)\b)/gi;

/** The arguments of each color-mix() in `text`, bracket-balanced. */
function colourMixes(text: string): string[] {
  const out: string[] = [];
  for (let at = text.indexOf("color-mix("); at >= 0; at = text.indexOf("color-mix(", at + 1)) {
    let depth = 0, end = at + "color-mix".length;
    for (; end < text.length; end += 1) {
      if (text[end] === "(") depth += 1;
      else if (text[end] === ")" && --depth === 0) break;
    }
    out.push(text.slice(at + "color-mix(".length, end));
  }
  return out;
}
/** A color-mix that mixes anything but --tx-* values: a literal, a keyword or another variable. */
function foreignMix(args: string): boolean {
  const parts = args.split(/,(?![^(]*\))/).slice(1).map(part => part.trim().replace(/\s+[\d.]+%$/, "").replace(/^[\d.]+%\s+/, ""));
  return parts.some(part => !/^var\(--tx-[\w-]+\)$/.test(part));
}

function colourHits(path: string): string[] {
  const text = read(path);
  const hits: string[] = [];
  if (isCss(path)) {
    for (const { property, value } of cssValues(text)) {
      if (literalHexInValue.test(value) || /\b(?:rgba?|hsla?)\(\s*[\d.]/i.test(value) || new RegExp(literalFallback.source, "i").test(value)
        || colourMixes(value).some(foreignMix)) hits.push(`${property}: ${value}`);
    }
    return hits;
  }
  const code = stripScriptComments(text);
  for (const match of code.matchAll(literalHexInScript)) hits.push(match[0].trim());
  for (const match of code.matchAll(colourFunction)) hits.push(code.slice(match.index, match.index + 24));
  for (const match of code.matchAll(literalFallback)) hits.push(code.slice(match.index, match.index + 32));
  for (const args of colourMixes(code)) if (foreignMix(args)) hits.push(`color-mix(${args})`);
  return hits;
}

/* ── font ────────────────────────────────────────────────────────────────── */

const allowedFont = /^(?:var\(--tx-font(?:-mono)?\)|inherit)$/;
function fontHits(path: string): string[] {
  const text = read(path);
  const plain = (value: string) => value.replace(/\s*!important$/, "").trim();
  if (isCss(path)) return cssValues(text).filter(item => item.property === "font-family" && !allowedFont.test(plain(item.value))).map(item => `font-family: ${item.value}`);
  const code = stripScriptComments(text);
  const hits: string[] = [];
  for (const match of code.matchAll(/fontFamily\s*:\s*([^,}\n]+)/g)) {
    const value = match[1].trim();
    if (!/^["'`](?:var\(--tx-font(?:-mono)?\)|inherit)["'`]$/.test(value) && !/^tokenValue\(\s*["']--tx-font(?:-mono)?["']\s*\)$/.test(value)) hits.push(`fontFamily: ${value}`);
  }
  for (const match of code.matchAll(/font-family\s*:\s*([^;"'`}\n]+)/g)) {
    // A stylesheet built in code (an iframe's) may read the font token at run time: font-family:${token("--tx-font")}.
    const fromToken = match[1].trim().startsWith("${") && /^\$\{\s*\w+\(\s*["'`]--tx-font(?:-mono)?["'`]\s*\)\s*\}/.test(code.slice(match.index + match[0].indexOf("${")));
    if (!fromToken && !allowedFont.test(plain(match[1]))) hits.push(`font-family: ${match[1].trim()}`);
  }
  return hits;
}

/* ── scrollbars ──────────────────────────────────────────────────────────── */

function scrollbarHits(path: string): string[] {
  const text = isCss(path) ? stripCssComments(read(path)) : stripScriptComments(read(path));
  const pattern = isCss(path) ? /scrollbar-[\w-]+\s*:|::-webkit-scrollbar[\w-]*/g : /\bscrollbar(?:Width|Color|Gutter)\b|scrollbar-[\w-]+\s*:|::-webkit-scrollbar[\w-]*/g;
  return [...text.matchAll(pattern)].map(match => match[0]);
}

/* ── variables ───────────────────────────────────────────────────────────── */

const tokens = new Set([...talyxTokensCss.matchAll(/(--tx-[\w-]+)\s*:/g)].map(match => match[1]));
/** The custom properties a module declares itself: in its stylesheets and as inline-style keys or setProperty() calls. */
function declaredBy(files: string[]): Set<string> {
  const names = new Set<string>();
  for (const path of files) {
    const text = read(path);
    for (const match of (isCss(path) ? stripCssComments(text) : text).matchAll(/(?:^|[{;\s"'`])(--[\w-]+)\s*["'`]?\s*:/g)) names.add(match[1]);
    for (const match of text.matchAll(/setProperty\(\s*["'`](--[\w-]+)/g)) names.add(match[1]);
    for (const match of text.matchAll(/\[\s*["'`](--[\w-]+)["'`](?:\s+as\s+\w+)?\s*\]\s*:/g)) names.add(match[1]);   // style={{ ["--x" as string]: v }}
  }
  return names;
}
function variableHits(path: string, own: Set<string>): string[] {
  const text = isCss(path) ? stripCssComments(read(path)) : stripScriptComments(read(path));
  return [...new Set([...text.matchAll(/var\(\s*(--[\w-]+)(?![\w$-]*\$\{)/g)].map(match => match[1]))]
    // --radix-* are set at run time by the Radix primitives under the component library.
    .filter(name => name.startsWith("--tx-") ? !tokens.has(name) : !own.has(name) && !name.startsWith("--radix-"));
}

/* ── component library classes ───────────────────────────────────────────── */

const libraryCss = read(join(root, "sdk/ui/src/ui/styles.ts")) + read(join(root, "sdk/ui/src/ui/research.css.ts"));
const libraryClasses = new Set([...libraryCss.matchAll(/\.(tx-[\w-]+)/g)].map(match => match[1]));
const layout = /^(?:display|visibility|position|inset(?:-[\w-]+)?|top|right|bottom|left|z-index|width|height|min-width|min-height|max-width|max-height|aspect-ratio|box-sizing|flex(?:-[\w-]+)?|grid(?:-[\w-]+)?|gap|row-gap|column-gap|order|align-[\w-]+|justify-[\w-]+|place-[\w-]+|margin(?:-[\w-]+)?|padding(?:-[\w-]+)?|overflow(?:-[xy])?|isolation|contain|pointer-events|white-space|overflow-wrap|word-break|text-overflow|text-align)$/;
const iconContext = /^(?:color|fill|transform|transition)$/;
/** Rules in a module stylesheet whose subject (the last compound selector) is a component library part and that set more than layout. */
function libraryClassHits(path: string): string[] {
  if (!isCss(path)) return [];
  const hits: string[] = [];
  for (const rule of cssRules(read(path))) {
    for (const selector of rule.selector.split(",")) {
      const subject = selector.trim().split(/\s*[\s>+~]\s*/).pop() ?? "";
      const parts = [...subject.matchAll(/\.(tx-[\w-]+)/g)].map(match => match[1]).filter(name => libraryClasses.has(name));
      if (!parts.length || parts.every(name => name === "tx-num")) continue;   // tx-num only marks figures in the module's own text
      // A TxIcon is a glyph in currentColor: the part around it may colour, fill or turn it (a state, a disclosure).
      const free = parts.every(name => name === "tx-icon") ? iconContext : null;
      const looks = declarations(rule.body).filter(item => !layout.test(item.property) && !free?.test(item.property)).map(item => item.property);
      if (looks.length) hits.push(`${selector.trim()} { ${looks.join(", ")} }`);
    }
  }
  return hits;
}

/* ── native controls, imports ────────────────────────────────────────────── */

const nativeHits = (path: string) => path.endsWith(".tsx") ? [...stripScriptComments(read(path)).matchAll(/<(button|input|select|textarea)(?=[\s>/])/g)].map(match => `<${match[1]}`) : [];
const webImportHits = (path: string) => isScript(path) ? [...read(path).matchAll(/["'](?:\.\.\/)+web\/src\/[^"']*["']/g)].map(match => match[0]) : [];

/* ── the rules ───────────────────────────────────────────────────────────── */

function findings(files: string[], check: (path: string) => string[], waived: Record<string, string> = {}): string[] {
  return files.filter(path => !waived[posix(path)]).flatMap(path => {
    const text = read(path);
    let from = 0;
    return check(path).map(hit => {
      const line = lineOf(text, hit, from);
      if (line) from = line.at + 1;
      return `${posix(path)}${line ? `:${line.number}` : ""}: ${hit}`;
    });
  });
}
/** Where a finding sits, for the report: the next place after `from` that holds its distinctive part (a CSS value, a
    selector, or the matched code). Approximate by design; a finding it cannot place is reported without a line. */
function lineOf(text: string, hit: string, from: number): { number: number; at: number } | null {
  const needle = (hit.includes(" { ") ? hit.slice(0, hit.indexOf(" { ")) : hit.includes(": ") && !hit.startsWith("<") ? hit.slice(hit.indexOf(": ") + 2) : hit)
    .replace(/\s+!important$/, "").replace(/\s+\([^)]*\)$/, "").trim().slice(0, 40);
  if (!needle) return null;
  let at = text.indexOf(needle, from);
  if (at < 0) at = text.indexOf(needle);
  return at < 0 ? null : { number: text.slice(0, at).split("\n").length, at };
}
/** Names of a module's own icon set (a `type …IconName = "a" | "b"` union) that the shared TxIcon also has. */
function iconNameHits(path: string): string[] {
  return isScript(path) ? iconNamesIn(read(path)) : [];
}
function iconNamesIn(text: string): string[] {
  const shared = new Set<string>(txIconNames);
  return [...text.matchAll(/type\s+\w*IconName\s*=([^;]+);/g)]
    .flatMap(match => [...match[1].matchAll(/"([\w-]+)"/g)].map(name => name[1]))
    .filter(name => shared.has(name)).map(name => `"${name}" (also a TxIcon name)`);
}
/** No findings; the message lists every one, so a failure reads as the list of places to fix. */
const none = (hits: string[]) => expect(hits, `\n${hits.join("\n")}\n`).toEqual([]);

describe.each([...modules.keys()])("module %s front end", module => {
  const files = modules.get(module)!;
  const look = pending(module) ? it.skip : it;   // UI_PENDING: the module waits for its redesign
  look("uses token colours only: no hex, rgb()/hsl(), literal var() fallback or color-mix of anything but --tx-*", () => {
    none(findings(files, colourHits, allowed.colour));
  });
  look("sets fonts only to the interface or mono token, or inherit", () => {
    none(findings(files, fontHits, allowed.font));
  });
  look("draws no scrollbars of its own", () => {
    none(findings(files, scrollbarHits));
  });
  look("uses only declared tokens or variables it declares itself", () => {
    const own = declaredBy(files);
    none(findings(files, path => variableHits(path, own)));
  });
  look("changes nothing but the layout of a component library part", () => {
    none(findings(files, libraryClassHits));
  });
  look("hand-rolls no button, input, select or textarea", () => {
    none(findings(files, nativeHits, allowed.native));
  });
  look("imports nothing from web/src", () => {
    none(findings(files, webImportHits));
  });
  look("names no icon of its own like a TxIcon", () => {
    none(findings(files, iconNameHits));
  });
});

describe("one component library", () => {
  it("has no area that opts into another look", () => {
    const attribute = ["data", "tx", "look"].join("-");
    const code = ["web/src", "sdk", "modules"].flatMap(dir => walk(join(root, dir), path => isCss(path) || isScript(path)));
    none(code.filter(path => read(path).includes(attribute)).map(posix));
  });

  it("keeps colour literals out of the platform's design folder", () => {
    const files = walk(join(root, "web/src/design"), path => (isCss(path) || isScript(path)) && !isTest(path));
    none(files.flatMap(path => colourHits(path).map(hit => `${posix(path)}: ${hit}`)));
  });

  it("finds the look rules' targets, so a broken scan cannot pass by finding nothing", () => {
    expect(libraryClasses.has("tx-btn") && libraryClasses.has("tx-params-row")).toBe(true);
    expect(tokens.has("--tx-ember")).toBe(true);
    expect(modules.get("agent")!.length).toBeGreaterThan(5);
    expect(iconNamesIn('export type SampleIconName = "plus" | "sample-only";')).toEqual(['"plus" (also a TxIcon name)']);
  });
});
