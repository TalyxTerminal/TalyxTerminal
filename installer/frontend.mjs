// Imported from the staged web/ so Vite resolves the checkout's locked packages.
import { build } from "vite";
import { existsSync, readFileSync, readdirSync, writeFileSync } from "node:fs";
import { resolve } from "node:path";

const web = process.cwd();
const modules = resolve(web, "../modules");
const input = { index: resolve(web, "index.html") };
const manifests = [];
for (const name of readdirSync(modules)) {
  const file = resolve(modules, name, "talyx.module.json");
  if (!existsSync(file)) continue;
  const manifest = JSON.parse(readFileSync(file, "utf8"));
  if (!manifest.entries.frontend) continue;
  input[manifest.id] = resolve(modules, name, manifest.entries.frontend);
  manifests.push({ file, manifest });
}

await build({
  build: {
    // The shell loads module entries by URL. Its stylesheet must include their static CSS too.
    cssCodeSplit: false,
    rollupOptions: {
      input, preserveEntrySignatures: "strict",
      output: { entryFileNames: "entries/[name].js" },
    },
  },
});
for (const { file, manifest } of manifests) {
  const entry = "frontend/installed.js";
  writeFileSync(resolve(modules, manifest.id, entry),
    `export { activate } from "/entries/${manifest.id}.js";\n`);
  manifest.entries.frontend = entry;
  writeFileSync(file, JSON.stringify(manifest, null, 2) + "\n");
}
