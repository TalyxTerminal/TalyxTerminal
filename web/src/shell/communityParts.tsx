import type { CSSProperties, ReactNode } from "react";
import { TxIcon, type IconName } from "@talyx/sdk";
import { ModuleIcon } from "./ModuleIcon";
import logoMark from "./assets/talyx-logo-round.png";

/* Pieces Discover and Installed share. */

export const matches = (query: string, ...texts: string[]) => {
  const needle = query.trim().toLowerCase();
  return !needle || texts.some(text => text.toLowerCase().includes(needle));
};

/** The line that says why a module couldn't start: a Python traceback's last exception line (the output around it is
    the import machinery and paths), else the first line. */
export function failureLine(text: string): string {
  const lines = text.split(/\r?\n/).map(line => line.trimEnd()).filter(Boolean);
  return [...lines].reverse().find(line => /^[A-Za-z_][\w.]*(Error|Exception|Exit|Interrupt)\b/.test(line)) ?? lines[0] ?? text;
}

/** A module's icon on a small tile: the monochrome icon its panel declares. */
export function Tile({ module, size }: { module: string; size?: "lg" }) {
  return <span className="cm-tile" data-size={size} aria-hidden="true"><ModuleIcon module={module} /></span>;
}

/* ── Generated looks: covers come from ids, so they stay the same everywhere ── */

/** A stable number for a text (FNV-1a), then a small generator seeded by it. */
function generator(text: string) {
  let state = 0x811C9DC5;
  for (let index = 0; index < text.length; index += 1) state = Math.imul(state ^ text.charCodeAt(index), 0x01000193);
  return () => {
    state = state + 0x6D2B79F5 | 0;
    let value = Math.imul(state ^ state >>> 15, 1 | state);
    value = value + Math.imul(value ^ value >>> 7, 61 | value) ^ value;
    return ((value ^ value >>> 14) >>> 0) / 4294967296;
  };
}
const token = (name: string) => `var(--tx-${name})`;
const choose = <T,>(random: () => number, list: readonly T[]) => list[Math.floor(random() * list.length)];
const percent = (random: () => number, low: number, high: number) => `${Math.round(low + random() * (high - low))}%`;

/* Covers stay in the page's colours: dark greys lit by an orange or a white glow. A module is its own icon on a
   quieter ground; an empty section is its kind's icon on the livelier one. */
const BASES = [["raised", "bg"], ["card", "bg"], ["raised", "panel"], ["card", "panel"]] as const;
const WARM = ["ember-55", "ember-40", "ember-30", "ember-20"];
const COOL = ["white-14", "white-10", "white-08"];
const QUIET = ["white-10", "white-08", "white-06", "ember-14", "ember-20"];
const SECOND = ["white-08", "white-06", "ember-14", "ember-20", "white-04"];

function coverStyle(seed: string, quiet: boolean): CSSProperties {
  const random = generator(seed);
  const [base, base2] = choose(random, BASES);
  const glow = quiet ? choose(random, QUIET) : random() < 0.62 ? choose(random, WARM) : choose(random, COOL);
  return {
    "--cm-base": token(base), "--cm-base-2": token(base2),
    "--cm-glow": token(glow), "--cm-glow-2": token(quiet ? "white-04" : choose(random, SECOND)),
    "--cm-x1": percent(random, 0, 100), "--cm-y1": percent(random, 0, 100),
    "--cm-x2": percent(random, 0, 100), "--cm-y2": percent(random, 0, 100),
    "--cm-angle": `${Math.round(random() * 360)}deg`,
  } as CSSProperties;
}

/** A cover. `card` in grids, `feature` for the featured one, `thumb` beside a smaller pick, `banner` across the top
    of a module's page. A module shows its icon in an app tile; a section shows its kind's icon. */
export function Cover({ seed, module, icon, size = "card", children }: {
  seed: string;
  module?: string;
  icon?: IconName;
  size?: "card" | "feature" | "thumb" | "banner";
  children?: ReactNode;
}) {
  return <span className="cm-cover" data-size={size} data-app={module ? true : undefined} style={coverStyle(seed, module !== undefined)}>
    {module ? <span className="cm-cover-app" aria-hidden="true"><ModuleIcon module={module} /></span>
      : <TxIcon name={icon ?? "blocks"} className="cm-cover-icon" aria-hidden="true" />}
    {children}
  </span>;
}

/** The official publisher: TALYX's mark and the verified sign. */
/** A module's intro with its URLs as links, opened outside TALYX. */
export function Linked({ text }: { text: string }) {
  return <>{text.split(/(https?:\/\/\S*[^\s.,;:!?)])/).map((part, index) => index % 2
    ? <a key={index} className="cm-link" href={part} target="_blank" rel="noreferrer">{part}</a>
    : part)}</>;
}

export function Byline({ size = "xs" }: { size?: "xs" | "sm" }) {
  return <span className="cm-byline" data-size={size}>
    <img className="cm-avatar" data-size={size} src={logoMark} alt="" />
    <span className="cm-byline-name">TALYX</span>
    <TxIcon name="verified" className="cm-byline-verified" />
  </span>;
}

/** The glass capsule of the top bar around a library switch. */
export function Glass({ children }: { children: ReactNode }) {
  return <div className="cm-glass">{children}</div>;
}
