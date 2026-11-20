/** Generic JSON and render fault values shared with the shell. */
export type JsonPrimitive = string | number | boolean | null;
export type JsonValue = JsonPrimitive | JsonValue[] | { [key: string]: JsonValue };
export interface ModuleRenderFault { phase: "render"; message: string }
