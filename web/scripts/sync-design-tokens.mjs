// Keeps the SDK copy of the design tokens identical to the single source,
// docs/design/tokens.css. Run it after editing that file:
//   node scripts/sync-design-tokens.mjs
import { readFile, writeFile } from "node:fs/promises";
import { fileURLToPath } from "node:url";

const source = fileURLToPath(new URL("../../docs/design/tokens.css", import.meta.url));
const target = fileURLToPath(new URL("../../sdk/ui/src/ui/tokens.css.ts", import.meta.url));

const css = (await readFile(source, "utf8")).replace(/^﻿/, "").replace(/\r\n/g, "\n");
const banner = `// Generated from docs/design/tokens.css by web/scripts/sync-design-tokens.mjs.
// Do not edit by hand; edit the source file and re-run the script.
`;

await writeFile(target, `${banner}\nexport const talyxTokensCss = ${JSON.stringify(css)};\n`);
process.stdout.write(`design tokens synced (${css.length} bytes)\n`);
