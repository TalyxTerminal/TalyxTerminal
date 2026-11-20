import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";
import { fileURLToPath } from "node:url";
import { createRequire } from "node:module";
import { sharedDependencies, sharedNames } from "./scripts/shared-dependencies.mjs";
import { moduleSources } from "./scripts/module-sources.mjs";

const backend = process.env.TALYX_DEV_BACKEND ?? "http://127.0.0.1:8000";
const packageDependencies = Object.keys(createRequire(import.meta.url)("./package.json").dependencies);

/** The SDK lives outside this package, so its Radix imports need an explicit
    path back into web/node_modules for both Vite and the shared bundle. */
export const designSystemPaths = fileURLToPath(new URL("./node_modules/", import.meta.url));

export default defineConfig({
  plugins: [react({ exclude: [/node_modules/, /[/\\]shared[/\\][0-5]\.js$/] }), sharedDependencies(), moduleSources()],
  build: { rollupOptions: { external: [...sharedNames, "react-dom/client"] } },
  resolve: {
    // Repository modules use the application's installed dependency tree.
    dedupe: [...new Set([...packageDependencies, "react", "react-dom"])],
    alias: [
      {
        find: "@talyx/sdk-ui",
        replacement: fileURLToPath(new URL("../sdk/ui/src/index.ts", import.meta.url)),
      },
      // The SDK sources sit outside this package, so bare Radix specifiers are
      // pointed at this package's tree. Subpaths such as
      // `@radix-ui/primitive/is-development` are deliberately left alone: they
      // must go through each package's own export map.
      { find: /^@radix-ui\/([a-z0-9-]+)$/, replacement: `${designSystemPaths}/@radix-ui/$1` },
    ],
  },
  // Loaded on demand from module code outside this package, so the first scan misses them; found late, they make the dev server
  // re-bundle and the pages already open fail with "Failed to fetch dynamically imported module".
  optimizeDeps: { include: ["monaco-editor", "plotly.js-dist-min"] },
  server: {
    host: "127.0.0.1",
    proxy: {
      "/api": backend,
      "/ws": { target: backend.replace(/^http/, "ws"), ws: true },
      "/m": { target: backend, ws: true },
    },
  },
  test: {
    // Pure logic tests (no DOM) — matches the old repo's vitest.config.ts; the
    // indicator value-mapping and chart logic never touch KLineCharts here.
    environment: "node",
    include: ["src/**/*.test.{ts,tsx}", "../modules/*/frontend/**/*.test.{ts,tsx}", "../modules/*/public/ts/**/*.test.{ts,tsx}"],
    exclude: ["node_modules/**"],
  },
});
