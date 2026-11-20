import type { JsonValue } from "@talyx/sdk-ui";
const MAX_STATE_BYTES = 512 * 1024;

function finiteJson(value: unknown): value is JsonValue {
  if (value === null || typeof value === "string" || typeof value === "boolean") return true;
  if (typeof value === "number") return Number.isFinite(value);
  if (Array.isArray(value)) return value.every(finiteJson);
  if (typeof value === "object") {
    const object = value as Record<string, unknown>;
    const prototype = Object.getPrototypeOf(object);
    if (prototype !== Object.prototype && prototype !== null) return false;
    if (typeof object.toJSON === "function") return false;
    return Object.values(object).every(finiteJson);
  }
  return false;
}

export function boundedModuleState(value: unknown): JsonValue {
  if (!finiteJson(value)) throw new Error("module state is not finite JSON");
  const text = JSON.stringify(value);
  if (typeof text !== "string") throw new Error("module state is not JSON");
  if (new TextEncoder().encode(text).length > MAX_STATE_BYTES) {
    throw new Error("module state exceeds 524288 bytes");
  }
  return JSON.parse(text) as JsonValue;
}
