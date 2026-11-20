/* Theme bridge.
   The platform publishes its design tokens through the kernel's theme channel;
   modules can also override individual values. Only `--tx-` names are
   forwarded, which is what `applyThemeVariables` filters on. */
import type { CSSProperties } from "react";
import { talyxTokensCss } from "./tokens.css";

export const THEME_PREFIX = "--tx-";

export type ThemeVariables = Record<string, string>;

/** Each token's declared value in the `:root` block of the single source stylesheet. */
let declared: Map<string, string> | null = null;
function declaredTokens(): Map<string, string> {
  if (declared) return declared;
  declared = new Map();
  const root = /:root\s*\{([^}]*)\}/.exec(talyxTokensCss.replace(/\/\*[\s\S]*?\*\//g, ""));
  for (const match of (root ? root[1] : "").matchAll(/(--[\w-]+)\s*:\s*([^;]+);/g)) declared.set(match[1], match[2].trim());
  return declared;
}

/** A declared value with every `var(--…)` in it replaced by that token's own declared value, level by level
    (`--tx-up` → `var(--tx-green)` → `#20C08A`). */
function resolveDeclared(name: string): string {
  const value = declaredTokens().get(name);
  if (value === undefined) return "";
  return value.replace(/var\((--[\w-]+)\)/g, (_, inner: string) => resolveDeclared(inner));
}

/**
 * One token's value: the custom property the browser has resolved, or, before the stylesheet is in (tests, start-up),
 * its declared value in the single source stylesheet with every `var()` resolved to a real value, so no value is ever
 * restated in TypeScript.
 */
export function tokenValue(name: string, styles: CSSStyleDeclaration | null = typeof getComputedStyle === "function" ? getComputedStyle(document.documentElement) : null): string {
  if (styles) {
    const resolved = styles.getPropertyValue(name).trim();
    if (resolved) return resolved;
  }
  return resolveDeclared(name);
}

/** The variables the shell publishes to modules at boot. */
export const baseThemeVariables: ThemeVariables = {};

export function isThemeVariable(name: string): boolean {
  return name.startsWith(THEME_PREFIX);
}

/** Copies only `--tx-` entries out of an untrusted theme payload. */
export function pickThemeVariables(value: unknown): ThemeVariables {
  if (!value || typeof value !== "object" || Array.isArray(value)) return {};
  const picked: ThemeVariables = {};
  for (const [name, entry] of Object.entries(value as Record<string, unknown>)) {
    if (isThemeVariable(name) && typeof entry === "string") picked[name] = entry;
  }
  return picked;
}

export function applyThemeVariables(element: HTMLElement, variables: unknown): void {
  for (const [name, value] of Object.entries(pickThemeVariables(variables))) {
    element.style.setProperty(name, value);
  }
}

export function themeVariablesStyle(variables: ThemeVariables): CSSProperties {
  return variables as CSSProperties;
}
