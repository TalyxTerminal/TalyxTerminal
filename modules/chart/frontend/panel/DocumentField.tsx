/* Typed fields bound to the chart document. The document answers changes one at a time, so its value runs a keystroke
   or more behind the typing; while the user edits, the field shows what they typed, and the document again on leaving. */
import { useState, type ComponentProps } from "react";
import { TxInput, TxTextarea } from "@talyx/sdk";

type Bound<P> = Omit<P, "value" | "onChange"> & { value: string; onValue(text: string): void };

export function DocumentInput({ value, onValue, onBlur, ...rest }: Bound<ComponentProps<typeof TxInput>>) {
  const [draft, setDraft] = useState<string | null>(null);
  return <TxInput {...rest} value={draft ?? value} onChange={event => { setDraft(event.target.value); onValue(event.target.value); }}
    onBlur={event => { setDraft(null); onBlur?.(event); }} />;
}

export function DocumentTextarea({ value, onValue, onBlur, ...rest }: Bound<ComponentProps<typeof TxTextarea>>) {
  const [draft, setDraft] = useState<string | null>(null);
  return <TxTextarea {...rest} value={draft ?? value} onChange={event => { setDraft(event.target.value); onValue(event.target.value); }}
    onBlur={event => { setDraft(null); onBlur?.(event); }} />;
}
