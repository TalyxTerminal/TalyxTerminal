// JSON Schema -> TypeScript declarations, for scripts/contracts/generate.py.
import { compile } from "json-schema-to-typescript";
import { readFile, writeFile } from "node:fs/promises";

const [input, output] = process.argv.slice(2);
const schema = JSON.parse(await readFile(input, "utf8"));

// The compiler uses the pre-2020 tuple form; adapt schema nodes, never instance data (defaults, enums).
function adapt(node) {
  if (node === null || typeof node !== "object" || Array.isArray(node)) return node;
  const out = { ...node };
  for (const key of ["properties", "patternProperties", "$defs", "dependentSchemas"]) {
    if (node[key] && typeof node[key] === "object") out[key] = Object.fromEntries(Object.entries(node[key]).map(([k, v]) => [k, adapt(v)]));
  }
  for (const key of ["items", "additionalProperties", "propertyNames", "allOf", "anyOf", "oneOf", "not", "if", "then", "else"]) {
    if (key in node) out[key] = Array.isArray(node[key]) ? node[key].map(adapt) : adapt(node[key]);
  }
  if (Array.isArray(node.prefixItems)) {
    out.items = node.prefixItems.map(adapt);
    out.additionalItems = "items" in node ? adapt(node.items) : true;
    delete out.prefixItems;
  }
  // A schema that constrains nothing (a declared `Any`) is any JSON value; the compiler would make it an object.
  if (!SHAPING.some(key => key in node)) out.tsType = "unknown";
  return out;
}
const SHAPING = ["type", "$ref", "properties", "patternProperties", "additionalProperties", "items", "prefixItems", "allOf",
  "anyOf", "oneOf", "not", "enum", "const", "if", "tsType"];

const content = await compile(adapt(schema), "ContractTypes", {
  bannerComment: "", declareExternallyReferenced: true, unreachableDefinitions: false, additionalProperties: false,
  format: false,
});
await writeFile(output, content);
