/* Price formats. The chart stream gives each window's precision and minimum move (Market's price increment, or the data's
   decimals when Market has none or the prices are adjusted); these turn numbers into text with it. */

export interface PriceFormat { precision: number; minMove: number }

/** A format as Lightweight Charts' priceFormat, its base given as a whole number: the base it derives (1 / minMove) is
    999999999.9999999 for a 1e-9 tick, and its price-axis tick spans throw on a base that is not made of 2s and 5s. */
export function seriesFormat(format: PriceFormat) {
  return { type: "price" as const, precision: format.precision, minMove: format.minMove, base: Math.round(1 / format.minMove) };
}

/** The format prices are shown in: Market's, or the decimals chosen in Chart settings › Symbol. */
export function shownFormat(market: PriceFormat, decimals: number | null | undefined): PriceFormat {
  return decimals === null || decimals === undefined ? market : { precision: decimals, minMove: 10 ** -decimals };
}

/** Grouped digits at a fixed precision: `67,213.7`. */
export function formatPrice(value: number, precision: number): string {
  return value.toLocaleString("en-US", { minimumFractionDigits: precision, maximumFractionDigits: precision });
}

/** Volume and other unbounded counts: `1.23K`, `45.6M`, `19.07Q`. */
export function formatCompact(value: number): string {
  const size = Math.abs(value);
  if (size >= 1e15) return `${(value / 1e15).toFixed(2)}Q`;
  if (size >= 1e12) return `${(value / 1e12).toFixed(2)}T`;
  if (size >= 1e9) return `${(value / 1e9).toFixed(2)}B`;
  if (size >= 1e6) return `${(value / 1e6).toFixed(2)}M`;
  if (size >= 1e3) return `${(value / 1e3).toFixed(2)}K`;
  return size >= 100 || Number.isInteger(value) ? value.toFixed(0) : value.toFixed(2);
}

const SUBSCRIPT = "₀₁₂₃₄₅₆₇₈₉";
/** A decimal with four or more zeros after "0." in compact notation, the zeros counted in a subscript and the digits
    kept: `-0.0000005483` → `-0.0₆5483`. Anything else is returned as it is. */
export function compactZeros(text: string): string {
  const match = /^([+-]?)0\.(0{4,})(\d+)$/.exec(text);
  if (!match) return text;
  const count = [...String(match[2].length)].map(digit => SUBSCRIPT[Number(digit)]).join("");
  return `${match[1]}0.0${count}${match[3]}`;
}

/** The largest magnitude Lightweight Charts takes in a series: it asserts every value is within ±MAX_SAFE_INTEGER / 100. */
export const ENGINE_LIMIT = Number.MAX_SAFE_INTEGER / 100;

/** The power of ten values go into the engine divided by: 1 while the largest fits, else one leaving a thousand times room
    to grow (BABYDOGE's daily volume of 1.9e16 goes in per million). */
export function valueUnit(largest: number): number {
  return largest <= ENGINE_LIMIT ? 1 : 10 ** Math.ceil(Math.log10(largest * 1000 / ENGINE_LIMIT));
}

/** The unit the series on one price scale share, since their values are drawn against each other. It grows when a
    series' values no longer fit, goes back to 1 once every series fits in it, and otherwise stays; when it changes, the
    scale's other series set their data again in it. Only what goes into the engine is divided: the legend and data
    window read the feed. */
export class ScaleUnit {
  value = 1;
  private readonly needs = new Map<object, number>();
  private readonly resets = new Map<object, () => void>();

  /** A series on the scale (`key`); `reset` sets its data again. */
  join(key: object, reset: () => void): () => void {
    this.resets.set(key, reset);
    return () => { this.resets.delete(key); this.needs.delete(key); this.settle(key); };
  }

  /** The unit for a series whose values reach `largest`: all its data (`whole`), or values it adds. */
  unitFor(key: object, largest: number, whole: boolean): number {
    const need = valueUnit(largest);
    this.needs.set(key, whole ? need : Math.max(need, this.needs.get(key) ?? 1));
    this.settle(key);
    return this.value;
  }

  private settle(key: object): void {
    const needed = Math.max(1, ...this.needs.values());
    const value = needed > this.value || needed === 1 ? needed : this.value;
    if (value === this.value) return;
    this.value = value;
    for (const [other, reset] of [...this.resets]) if (other !== key) reset();
  }
}

/** A series' price format in a unit: as it is in 1, else labels multiplied back and in compact notation (such values are
    beyond 90 trillion). */
export function unitFormat<T>(format: T, unit: number): T | { type: "custom"; minMove: number; formatter: (price: number) => string } {
  return unit === 1 ? format : { type: "custom", minMove: 1 / unit, formatter: price => formatCompact(price * unit) };
}

/** An indicator value as its script declares it: `format.volume` in compact notation, anything else at a precision. */
export function formatValue(value: number, format: string | undefined, precision: number): string {
  return format === "volume" ? formatCompact(value) : formatPrice(value, precision);
}

/** The engine's price format for a plot declared `format.volume`: axis and price labels compact too. */
export const VOLUME_FORMAT = { type: "custom" as const, minMove: 1, formatter: formatCompact };

