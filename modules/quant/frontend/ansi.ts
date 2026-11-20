/* Terminal colour codes in kernel text (tracebacks, coloured logs) as styled runs, the way Jupyter shows them. */

export interface AnsiRun { text: string; fg: string | null; bg: string | null; bold: boolean; dim: boolean; italic: boolean; underline: boolean }

/** The 16 terminal colours as CSS classes (`qx-ansi-<n>`), or an rgb() for 256-colour and true-colour codes. */
const CUBE = [0, 95, 135, 175, 215, 255];
function extended(codes: number[], index: number): {color: string | null; used: number} {
  if (codes[index] === 5 && index + 1 < codes.length) {
    const n = codes[index + 1];
    if (n < 16) return {color: String(n), used: 2};
    if (n < 232) {
      const value = n - 16;
      return {color: `rgb(${CUBE[Math.floor(value / 36)]},${CUBE[Math.floor(value / 6) % 6]},${CUBE[value % 6]})`, used: 2};
    }
    const grey = 8 + (n - 232) * 10;
    return {color: `rgb(${grey},${grey},${grey})`, used: 2};
  }
  if (codes[index] === 2 && index + 3 < codes.length) return {color: `rgb(${codes[index + 1]},${codes[index + 2]},${codes[index + 3]})`, used: 4};
  return {color: null, used: codes.length - index};
}

const SEQUENCE = /\x1b\[([0-9;]*)([A-Za-z])/g;

/** Splits text into runs with the style set by SGR codes; other escape sequences are dropped. */
export function ansiRuns(text: string): AnsiRun[] {
  const runs: AnsiRun[] = [];
  let style = {fg: null as string | null, bg: null as string | null, bold: false, dim: false, italic: false, underline: false};
  let last = 0;
  const push = (part: string) => {
    if (!part) return;
    const previous = runs[runs.length - 1];
    if (previous && previous.fg === style.fg && previous.bg === style.bg && previous.bold === style.bold && previous.dim === style.dim
      && previous.italic === style.italic && previous.underline === style.underline) previous.text += part;
    else runs.push({text: part, ...style});
  };
  for (const match of text.matchAll(SEQUENCE)) {
    push(text.slice(last, match.index));
    last = match.index! + match[0].length;
    if (match[2] !== "m") continue;
    const codes = match[1] === "" ? [0] : match[1].split(";").map(part => part === "" ? 0 : Number(part));
    for (let index = 0; index < codes.length; index++) {
      const code = codes[index];
      if (code === 0) style = {fg: null, bg: null, bold: false, dim: false, italic: false, underline: false};
      else if (code === 1) style = {...style, bold: true};
      else if (code === 2) style = {...style, dim: true};
      else if (code === 3) style = {...style, italic: true};
      else if (code === 4) style = {...style, underline: true};
      else if (code === 22) style = {...style, bold: false, dim: false};
      else if (code === 23) style = {...style, italic: false};
      else if (code === 24) style = {...style, underline: false};
      else if (code >= 30 && code <= 37) style = {...style, fg: String(code - 30)};
      else if (code >= 90 && code <= 97) style = {...style, fg: String(code - 90 + 8)};
      else if (code >= 40 && code <= 47) style = {...style, bg: String(code - 40)};
      else if (code >= 100 && code <= 107) style = {...style, bg: String(code - 100 + 8)};
      else if (code === 39) style = {...style, fg: null};
      else if (code === 49) style = {...style, bg: null};
      else if (code === 38 || code === 48) {
        const {color, used} = extended(codes, index + 1);
        style = code === 38 ? {...style, fg: color} : {...style, bg: color};
        index += used;
      }
    }
  }
  push(text.slice(last));
  return runs;
}

/** Text without colour codes, for copying and for measuring. */
export const stripAnsi = (text: string) => text.replace(SEQUENCE, "");

/**
 * Stream text as a terminal shows it: a carriage return starts its line over, so progress bars redraw in place.
 * A carriage return at the very end is kept, so the next piece of output still replaces that line.
 */
export function carriage(text: string): string {
  if (!text.includes("\r")) return text;
  const lines = text.replace(/\r+\n/g, "\n").split("\n");
  return lines.map((line, index) => {
    const trimmed = line.replace(/\r+$/, "");
    const cut = trimmed.lastIndexOf("\r");
    const shown = cut >= 0 ? trimmed.slice(cut + 1) : trimmed;
    return index === lines.length - 1 && trimmed !== line ? shown + "\r" : shown;
  }).join("\n");
}
