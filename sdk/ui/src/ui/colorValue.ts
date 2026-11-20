/** User color data accepts every CSS hex form; opacity changes retain RGB exactly. */
export function hexOpacity(value: string): number {
  if (!/^#(?:[\da-f]{3}|[\da-f]{4}|[\da-f]{6}|[\da-f]{8})$/i.test(value)) return 100;
  const hex = value.slice(1);
  const alpha = hex.length === 4 ? parseInt(hex[3]! + hex[3]!, 16)
    : hex.length === 8 ? parseInt(hex.slice(6), 16) : 255;
  return Math.round(alpha / 255 * 100);
}
export function withHexOpacity(value: string, percent: number): string {
  if (!/^#(?:[\da-f]{3}|[\da-f]{4}|[\da-f]{6}|[\da-f]{8})$/i.test(value) || !Number.isFinite(percent)) return value;
  const hex = value.slice(1);
  const rgb = hex.length <= 4 ? [...hex.slice(0, 3)].map(channel => channel + channel).join("") : hex.slice(0, 6);
  const alpha = Math.round(Math.max(0, Math.min(100, percent)) / 100 * 255).toString(16).padStart(2, "0");
  return `#${rgb}${alpha}`;
}
