// The words in TypeScript sources, for check_i18n.py: texts given to the translator, and English shown without it.
import { createRequire } from "node:module";
import { readFileSync } from "node:fs";
import { dirname, resolve } from "node:path";
import { fileURLToPath } from "node:url";

const ROOT = resolve(dirname(fileURLToPath(import.meta.url)), "../..");
const ts = createRequire(resolve(ROOT, "web/package.json"))("typescript");

// Where English meant for people is written: these JSX attributes and object properties, text between tags, strings
// shown as a JSX child, and tables of labels.
const ATTRIBUTES = new Set(["title", "label", "placeholder", "aria-label", "alt", "tooltip", "detail", "description", "hint",
  "text", "message", "caption", "heading", "subtitle", "emptyText", "empty", "confirmLabel", "cancelLabel", "actionLabel",
  "summary", "help", "note", "legend", "what", "unit"]);
const TABLE = /label|word|text|title|message|hint|caption|name/i;
// A text is English meant for people when it has a word in lower case; codes and identifiers in capitals are not.
const PROSE = /[a-z]{2,}/;
const IGNORE = "i18n-ignore";

function text(node) {
  if (ts.isStringLiteral(node) || ts.isNoSubstitutionTemplateLiteral(node)) return node.text;
  if (ts.isTemplateExpression(node)) return [node.head.text, ...node.templateSpans.map(span => "{…}" + span.literal.text)].join("");
  return null;
}

function translatorCall(node) {
  if (!ts.isCallExpression(node)) return null;
  const callee = node.expression;
  if (ts.isIdentifier(callee) && callee.text === "t") return "t";
  if (ts.isPropertyAccessExpression(callee) && ts.isIdentifier(callee.expression) && callee.expression.text === "t" &&
      (callee.name.text === "c" || callee.name.text === "rich")) return callee.name.text;
  return null;
}

export function messages(source, file) {
  const tree = ts.createSourceFile(file, source, ts.ScriptTarget.Latest, true, ts.ScriptKind.TSX);
  const lines = source.split("\n");
  const keys = [], shown = [], dynamic = [];
  const line = node => tree.getLineAndCharacterOfPosition(node.getStart(tree)).line + 1;
  const ignored = node => lines[line(node) - 1]?.includes(IGNORE) || lines[line(node) - 2]?.trim().startsWith("// " + IGNORE);
  const report = (node, value) => { if (value !== null && PROSE.test(value) && !ignored(node)) shown.push({ text: value.trim(), line: line(node) }); };
  // A string in a JSX child or attribute, also inside a ternary, a fallback (?? ||) or a concatenation.
  function shownValues(node, visit) {
    if (ts.isParenthesizedExpression(node)) return shownValues(node.expression, visit);
    if (ts.isConditionalExpression(node)) { visit(node.condition); shownValues(node.whenTrue, visit); shownValues(node.whenFalse, visit); return; }
    if (ts.isBinaryExpression(node) && [ts.SyntaxKind.QuestionQuestionToken, ts.SyntaxKind.BarBarToken, ts.SyntaxKind.PlusToken,
      ts.SyntaxKind.AmpersandAmpersandToken].includes(node.operatorToken.kind)) {
      if (node.operatorToken.kind === ts.SyntaxKind.AmpersandAmpersandToken) visit(node.left); else shownValues(node.left, visit);
      shownValues(node.right, visit); return;
    }
    const value = text(node);
    if (value !== null) { report(node, value); if (ts.isTemplateExpression(node)) node.templateSpans.forEach(span => visit(span.expression)); return; }
    visit(node);
  }
  function visit(node) {
    const kind = translatorCall(node);
    if (kind) {
      const [first, second] = node.arguments;
      const context = kind === "c" ? (first && ts.isStringLiteralLike(first) ? first.text : null) : null;
      const key = kind === "c" ? second : first;
      if (key && ts.isStringLiteralLike(key) && (kind !== "c" || context !== null)) keys.push({ text: key.text, context, line: line(node) });
      else dynamic.push({ line: line(node) });
      node.arguments.slice(kind === "c" ? 2 : 1).forEach(visit);
      return;
    }
    if (ts.isJsxText(node)) { report(node, node.text.replace(/\s+/g, " ")); return; }
    if (ts.isJsxExpression(node) && node.expression && (ts.isJsxElement(node.parent) || ts.isJsxFragment(node.parent))) { shownValues(node.expression, visit); return; }
    if (ts.isJsxAttribute(node) && node.initializer && ATTRIBUTES.has(node.name.getText(tree))) {
      if (ts.isStringLiteral(node.initializer)) report(node.initializer, node.initializer.text);
      else if (ts.isJsxExpression(node.initializer) && node.initializer.expression) shownValues(node.initializer.expression, visit);
      return;
    }
    if (ts.isPropertyAssignment(node) && ATTRIBUTES.has(node.name.getText(tree).replace(/^["']|["']$/g, ""))) { shownValues(node.initializer, visit); return; }
    if (ts.isVariableDeclaration(node) && node.initializer && TABLE.test(node.name.getText(tree)) &&
        (ts.isObjectLiteralExpression(node.initializer) || ts.isArrayLiteralExpression(node.initializer))) {
      const elements = ts.isArrayLiteralExpression(node.initializer) ? node.initializer.elements
        : node.initializer.properties.filter(ts.isPropertyAssignment).map(property => property.initializer);
      for (const element of elements) { if (text(element) !== null && /\s/.test(text(element))) report(element, text(element)); else visit(element); }
      return;
    }
    if (ts.isCallExpression(node) && ts.isIdentifier(node.expression) && node.expression.text === "failure" && node.arguments[1]) {
      visit(node.arguments[0]); shownValues(node.arguments[1], visit); node.arguments.slice(2).forEach(visit); return;
    }
    ts.forEachChild(node, visit);
  }
  visit(tree);
  return { keys, shown, dynamic };
}

if (process.argv[1] && resolve(process.argv[1]) === fileURLToPath(import.meta.url)) {
  const files = JSON.parse(readFileSync(0, "utf8"));
  process.stdout.write(JSON.stringify(Object.fromEntries(files.map(file => [file, messages(readFileSync(resolve(ROOT, file), "utf8"), file)]))));
}
