type Values = Record<string, any>;
/** JSON with the keys of every object in order, so two rows with the same content and a different key order are the same. */
const canon = (value: unknown): string => JSON.stringify(value, (_key, item) => item && typeof item === "object" && !Array.isArray(item) ? Object.fromEntries(Object.entries(item).sort(([a], [b]) => a < b ? -1 : 1)) : item);
/** The settings whose values differ from the run's. */
export const differsFrom = (values: Values, base: Values | null): Set<string> => {
  const out = new Set<string>();
  if (!base) return out;
  for (const key of Object.keys(values)) if (base[key] !== undefined && canon(values[key]) !== canon(base[key])) out.add(key);
  return out;
};
