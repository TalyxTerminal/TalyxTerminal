import { t } from "../i18n";
import Ajv2020, { type ValidateFunction } from "ajv/dist/2020";
import { failure, type Json } from "@talyx/sdk";
import vocabulary from "../../../vocabulary/schema.json";

const ajv = new Ajv2020({ strict: false, validateFormats: false });
for (const [name, schema] of Object.entries(vocabulary.$defs)) {
  ajv.addSchema({ ...schema, $defs: vocabulary.$defs }, `vocab:${name}`);
}
const validators = new WeakMap<object, ValidateFunction>();

export function validateView(schema: object | boolean, value: Json, output = false): void {
  let validate = typeof schema === "object" ? validators.get(schema) : undefined;
  if (!validate) {
    validate = ajv.compile(schema);
    if (typeof schema === "object") validators.set(schema, validate);
  }
  if (!validate(value)) throw failure(output ? "invalid_output" : "invalid_input", t("View schema validation failed"),
    { errors: JSON.parse(JSON.stringify(validate.errors)) }, output ? "failed" : "not_started");
}
