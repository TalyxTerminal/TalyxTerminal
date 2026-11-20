/* Builds frame.bundle.js, the page a notebook's Jupyter widgets run in (WidgetOutput reads it as text), from
   frame.ts with the window's packages. Run after changing frame.ts: node modules/quant/frontend/notebook/widgets/build-frame.mjs */
import { dirname, resolve } from "node:path";
import { fileURLToPath } from "node:url";
import { createRequire } from "node:module";

const here = dirname(fileURLToPath(import.meta.url));
const packages = resolve(here, "../../../../../web/node_modules");
const { build } = createRequire(resolve(packages, "package.json"))("esbuild");
await build({
  entryPoints: [resolve(here, "frame.ts")], outfile: resolve(here, "frame.bundle.js"),
  bundle: true, nodePaths: [packages], minify: true, format: "iife",
  loader: { ".css": "text", ".woff": "empty", ".woff2": "empty", ".ttf": "empty", ".eot": "empty", ".svg": "empty" },
  define: { "process.env.NODE_ENV": '"production"' }, banner: { js: "var __webpack_public_path__=\"\";" }, logLevel: "error",
});
console.log("ok");
