/* TradingView's countdown under the last price label: time left until the forming bar closes (Market's closeTime). */

const two = (value: number) => (value < 10 ? "0" : "") + value;

export function countdownText(left: number): string {
  const seconds = Math.max(0, Math.floor(left / 1000)), days = Math.floor(seconds / 86_400), hours = Math.floor(seconds / 3600) % 24;
  const clock = `${two(Math.floor(seconds / 60) % 60)}:${two(seconds % 60)}`;
  if (days > 0) return `${days}d ${hours}h`;
  return hours > 0 ? `${two(hours)}:${clock}` : clock;
}
