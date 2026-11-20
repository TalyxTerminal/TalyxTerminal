// Use the compiler parser and resolver already installed by the web workspace.
import { createRequire } from "node:module";
import { readFileSync, readdirSync, existsSync } from "node:fs";
import { dirname, resolve, relative } from "node:path";
import { fileURLToPath } from "node:url";

const ROOT = resolve(dirname(fileURLToPath(import.meta.url)), "../..");
const ts = createRequire(resolve(ROOT, "web/package.json"))("typescript");

export function references(source, file) {
  const tree = ts.createSourceFile(file, source, ts.ScriptTarget.Latest, true);
  const result = [];
  function visit(node) {
    let expression;
    if (ts.isImportDeclaration(node) || ts.isExportDeclaration(node)) expression = node.moduleSpecifier;
    if (ts.isImportTypeNode(node) && ts.isLiteralTypeNode(node.argument)) expression = node.argument.literal;
    if (ts.isCallExpression(node) && (node.expression.kind === ts.SyntaxKind.ImportKeyword ||
        (ts.isIdentifier(node.expression) && node.expression.text === "require"))) expression = node.arguments[0];
    if (expression) result.push({
      target: ts.isStringLiteralLike(expression) ? expression.text : null,
      expression: expression.getText(tree),
      line: tree.getLineAndCharacterOfPosition(expression.getStart(tree)).line + 1,
      types: ts.isImportDeclaration(node) && node.importClause?.isTypeOnly && ts.isNamedImports(node.importClause.namedBindings) ? node.importClause.namedBindings.elements.map(item => (item.propertyName ?? item.name).text) : undefined,
    });
    ts.forEachChild(node, visit);
  }
  visit(tree);
  return result;
}

function sourceFiles(dir) {
  if (!existsSync(dir)) return [];
  return readdirSync(dir, { withFileTypes: true }).flatMap(entry => {
    if (["node_modules", ".git", "__pycache__", "tests"].includes(entry.name)) return [];
    // Vendored native materials under modules/<id>/runtime are not TALYX source.
    if (entry.name === "runtime" && entry.isDirectory() && /[\\/]modules[\\/][^\\/]+$/.test(dir)) return [];
    const path = resolve(dir, entry.name);
    return entry.isDirectory() ? sourceFiles(path) : /\.(tsx?|mts|jsx?|mjs)$/.test(entry.name) &&
      !/\.(test|spec)\./.test(entry.name) ? [path] : [];
  });
}

export function collect(root) {
  const configPath = resolve(root, "web/tsconfig.json");
  const config = existsSync(configPath) ? ts.readConfigFile(configPath, ts.sys.readFile).config : {};
  const options = ts.convertCompilerOptionsFromJson(config.compilerOptions ?? {}, resolve(root, "web")).options;
  const files = ["web/src", "sdk/ui", "modules", "platform", "vocabulary", "app", "examples"]
    .flatMap(dir => sourceFiles(resolve(root, dir)));
  const imports = [];
  const dynamic = [];
  function typeOrigins(file, names, depth = 0) {
    if (!names?.length || depth > 8) return [file];
    const tree = ts.createSourceFile(file, readFileSync(file, "utf8"), ts.ScriptTarget.Latest, true);
    return names.flatMap(name => {
      const declaration = tree.statements.find(node => ts.isExportDeclaration(node) && node.isTypeOnly && node.moduleSpecifier && node.exportClause && ts.isNamedExports(node.exportClause) && node.exportClause.elements.some(item => item.name.text === name));
      if (!declaration) return [file];
      const item = declaration.exportClause.elements.find(item => item.name.text === name);
      const target = ts.resolveModuleName(declaration.moduleSpecifier.text, file, options, ts.sys).resolvedModule?.resolvedFileName;
      return target && !target.includes("node_modules") ? typeOrigins(target, [(item.propertyName ?? item.name).text], depth + 1) : [file];
    });
  }
  for (const file of files) {
    const source = relative(root, file).replaceAll("\\", "/");
    for (const ref of references(readFileSync(file, "utf8"), file)) {
      if (ref.target === null) { dynamic.push({ source, ...ref }); continue; }
      const target = ts.resolveModuleName(ref.target, file, options, ts.sys).resolvedModule?.resolvedFileName;
      const internal = target && !target.includes("node_modules") && !relative(root, target).startsWith("..");
      if (internal) {
        // A type-only re-export of the public SDK/vocabulary belongs to that
        // public declaration. Runtime imports still depend on the wrapper.
        // SDK imports are checked separately, so this cannot conceal a module
        // implementation imported by an SDK barrel.
        const origins = typeOrigins(target, ref.types);
        const publicTypes = origins.every(origin => /^(sdk|vocabulary)\//.test(relative(root, origin).replaceAll("\\", "/")));
        for (const origin of new Set(publicTypes ? origins : [target])) imports.push({ source, target: relative(root, origin).replaceAll("\\", "/"), line: ref.line });
      }
      else if (ref.target.startsWith(".")) {
        const asset = resolve(dirname(file), ref.target.split("?")[0]);
        imports.push({ source, target: relative(root, asset).replaceAll("\\", "/"), line: ref.line, missing: !existsSync(asset) });
      }
    }
  }
  return { files: files.length, imports, dynamic };
}

if (process.argv[1] && resolve(process.argv[1]) === fileURLToPath(import.meta.url)) {
  process.stdout.write(JSON.stringify(collect(resolve(process.argv[2] ?? ROOT))));
}
