/* Type-checks the window and the module frontends the platform can load: tsconfig.json, without the frontends of
   modules whose manifest isn't the version the platform reads (they can't run until they move to it). Which modules
   those are comes from their manifests, not from names. */
import { readdirSync, readFileSync, existsSync } from "node:fs";
import { dirname, join, resolve, sep } from "node:path";
import { fileURLToPath } from "node:url";
import ts from "typescript";

const web = resolve(dirname(fileURLToPath(import.meta.url)), "..");
const modules = resolve(web, "../modules");
const version = JSON.parse(readFileSync(resolve(web, "../contracts/manifest_v5.schema.json"), "utf8")).properties.manifestVersion.const;

const unloadable = readdirSync(modules, { withFileTypes: true })
  .filter(entry => entry.isDirectory() && existsSync(join(modules, entry.name, "talyx.module.json")))
  .filter(entry => JSON.parse(readFileSync(join(modules, entry.name, "talyx.module.json"), "utf8")).manifestVersion !== version)
  .map(entry => join(modules, entry.name, "frontend") + sep);

const config = ts.getParsedCommandLineOfConfigFile(join(web, "tsconfig.json"), {}, {
  ...ts.sys, onUnRecoverableConfigFileDiagnostic: diagnostic => { throw new Error(ts.flattenDiagnosticMessageText(diagnostic.messageText, "\n")); },
});
const files = config.fileNames.filter(file => !unloadable.some(folder => resolve(file).startsWith(folder)));
const program = ts.createProgram(files, config.options);
const diagnostics = ts.getPreEmitDiagnostics(program);
const host = { getCanonicalFileName: name => name, getCurrentDirectory: () => web, getNewLine: () => "\n" };
if (diagnostics.length) {
  process.stderr.write(ts.formatDiagnostics(diagnostics, host));
  process.exit(1);
}
if (unloadable.length) console.log(`Not checked (manifest isn't version ${version}): ${unloadable.map(folder => folder.slice(modules.length + 1, -1).replaceAll(sep, "/")).join(", ")}`);
