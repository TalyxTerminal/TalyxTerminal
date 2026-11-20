import assert from "node:assert/strict";
import { execFileSync } from "node:child_process";
import { mkdtemp, rm, writeFile } from "node:fs/promises";
import { tmpdir } from "node:os";
import { dirname, resolve } from "node:path";
import { fileURLToPath } from "node:url";
import { test } from "node:test";

const root = resolve(dirname(fileURLToPath(import.meta.url)), "../..");

test("Draft 2020 tuples retain positions and length in generated TypeScript", async (t) => {
  const probe = await mkdtemp(resolve(tmpdir(), "talyx-tuple-"));
  t.after(() => rm(probe, { recursive: true, force: true }));
  const number = { type: ["number", "null"] };
  const color = { type: ["string", "null"] };
  const schema = {
    $schema: "https://json-schema.org/draft/2020-12/schema",
    title: "Vocabulary",
    type: "object",
    properties: {
      gradients: {
        type: "array",
        items: { anyOf: [
          { type: "null" },
          { type: "array", minItems: 4, maxItems: 4,
            prefixItems: [number, number, color, color], items: false },
        ] },
      },
      ordinary: { type: "array", items: { type: "number" } },
    },
    required: ["gradients", "ordinary"],
    additionalProperties: false,
  };
  await writeFile(resolve(probe, "schema.json"), JSON.stringify(schema), "utf8");
  execFileSync(process.execPath, [resolve(root, "web/scripts/generate-public.mjs"),
    resolve(probe, "schema.json"), resolve(probe, "types.ts")], { cwd: root, timeout: 30000 });
  await writeFile(resolve(probe, "type-cases.ts"), `
import type { Vocabulary } from "./types";
const valid: Vocabulary = { gradients: [null, [1, null, "#112233ff", null]], ordinary: [1, 2] };
// @ts-expect-error A color position cannot contain a number.
const wrongColor: Vocabulary["gradients"] = [[1, 2, 3, null]];
// @ts-expect-error All four tuple positions are required.
const missingColor: Vocabulary["gradients"] = [[1, 2, "#112233ff"]];
// @ts-expect-error A fifth position is forbidden.
const extraColor: Vocabulary["gradients"] = [[1, 2, null, null, null]];
// @ts-expect-error Ordinary array item types remain enforced.
const wrongOrdinary: Vocabulary["ordinary"] = ["text"];
void [valid, wrongColor, missingColor, extraColor, wrongOrdinary];
`, "utf8");
  const diagnostics = execFileSync(process.execPath, [
    resolve(root, "web/node_modules/typescript/bin/tsc"), "--noEmit", "--strict",
    "--skipLibCheck", "--target", "ES2022", resolve(probe, "type-cases.ts"),
  ], { cwd: root, timeout: 30000, encoding: "utf8" });
  assert.equal(diagnostics, "");
});
