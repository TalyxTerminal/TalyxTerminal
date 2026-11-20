/* A number box that also takes several values ("24, 48, 96"): one value is the setting, several are a range to sweep. */
import {TxInput} from "@talyx/sdk";

export function RangeBox({label, value, unit, range, error, onValue, onRange}: {label: string; value: number; unit?: string; range?: string; error?: string;
  onValue(value: number): void; onRange(text: string | null): void}) {
  return <span className="sv-rangebox" data-range={range ? "" : undefined} data-error={error ? "" : undefined}>
    <TxInput aria-label={label} aria-invalid={error ? true : undefined} value={range ?? String(value)} suffix={unit}
      onChange={event => {
        const text = event.target.value;
        if (text.includes(",")) {onRange(text); return;}
        onRange(null);
        const number = Number(text.trim());
        if (text.trim() !== "" && Number.isFinite(number)) onValue(number);
      }} />
    {error && <span className="sv-rangeerror" role="alert">{error}</span>}
  </span>;
}
