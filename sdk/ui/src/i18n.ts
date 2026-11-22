/* Words people read, in the language they chose (the platform's General setting `language`).
   English is the source: code writes the English text and `t()` looks it up in its owner's dictionary; text the
   dictionary does not have stays English. A module makes its own translator once and uses it everywhere:

     export const t = translator("my_module");       // frontend/i18n.ts
     t("Fetching prices"); t("{done} of {total}", {done, total}); t.c("price", "Close");
     t.rich("{count} from {source}", {count: <b>{count}</b>, source});

   The window reads the language and every owner's dictionary before any module's frontend is loaded, and reloads
   when the language changes, so a translation made at import (a table of labels) is in the right language. Dates and
   month names follow the language through `intlLocale()`; numbers keep one format. The dictionary format and the
   checks are in docs/i18n.md. */
import { createElement, Fragment, type ReactNode } from "react";

export type Language = "en" | "zh-CN";
export type Dictionary = Record<string, string | Record<string, Record<string, string>>>;
type Params = Record<string, string | number>;
export interface Translate {
  (text: string, params?: Params): string;
  /** A text whose translation depends on where it is used: `t.c("price", "Close")` beside `t("Close")` on a button. */
  c(context: string, text: string, params?: Params): string;
  /** The text with elements in its placeholders. */
  rich(text: string, params: Record<string, ReactNode>): ReactNode;
}

const CONTEXT = "@context";
const PLACEHOLDER = /\{([A-Za-z_][A-Za-z0-9_]*)\}/g;
let current: Language = "en";
const dictionaries = new Map<string, Dictionary>();

export function language(): Language { return current; }
/** The locale for `Intl` dates and month names in the current language. */
export function intlLocale(): string { return current === "zh-CN" ? "zh-CN" : "en-US"; }

/** The window's language and owners' dictionaries, set by the platform before modules load. */
export function installLanguage(value: Language, byOwner: Record<string, Dictionary>): void {
  current = value;
  dictionaries.clear();
  for (const [owner, dictionary] of Object.entries(byOwner)) dictionaries.set(owner, dictionary);
}

function lookup(owner: string, context: string | null, text: string): string {
  const dictionary = dictionaries.get(owner);
  if (!dictionary) return text;
  const found = context === null ? dictionary[text] : (dictionary[CONTEXT] as Record<string, Record<string, string>> | undefined)?.[context]?.[text];
  return typeof found === "string" ? found : text;
}

const fill = (text: string, params: Params = {}) => text.replace(PLACEHOLDER, (whole, name: string) => name in params ? String(params[name]) : whole);

/** Translates with `owner`'s dictionary ("platform" for the shell and the SDK's components). */
export function translator(owner: string): Translate {
  const t = ((text: string, params?: Params) => fill(lookup(owner, null, text), params)) as Translate;
  t.c = (context, text, params) => fill(lookup(owner, context, text), params);
  t.rich = (text, params) => {
    const parts: ReactNode[] = [];
    let last = 0, index = 0;
    const translated = lookup(owner, null, text);
    for (const match of translated.matchAll(PLACEHOLDER)) {
      if (!(match[1] in params)) continue;
      parts.push(translated.slice(last, match.index), createElement(Fragment, { key: index++ }, params[match[1]]));
      last = match.index! + match[0].length;
    }
    parts.push(translated.slice(last));
    return createElement(Fragment, null, ...parts);
  };
  return t;
}
