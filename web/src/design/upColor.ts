/* The up colour: which of green and red means up.
   Which way is "up" is the user's choice, not a module's: the platform's General setting `upColor`, kept by the
   Host in two layers (global, and a workspace's own). Each window applies the value of the workspace it shows (the
   home window the global one) as this attribute on the document root; `--tx-up` and `--tx-down` are re-bound by
   tokens.css, and every consumer inherits the result — modules and the platform both read the same variables. */
import { useEffect, useState } from "react";

export type UpColor = "green-up" | "red-up";

const DEFAULT: UpColor = "green-up";
const listeners = new Set<(value: UpColor) => void>();

function isUpColor(value: unknown): value is UpColor {
  return value === "green-up" || value === "red-up";
}

export function upColor(): UpColor {
  const stored = document.documentElement.dataset.upColor;
  return isUpColor(stored) ? stored : DEFAULT;
}

/** Applies a value to this window (the setting itself is written through the settings window). */
export function setUpColor(value: UpColor): void {
  document.documentElement.dataset.upColor = value;
  for (const listener of listeners) listener(value);
}

/** The default until the Host's value is read. */
export function initUpColor(): void {
  setUpColor(DEFAULT);
}

type Bridge = {
  call(name: string, input: unknown): Promise<unknown>;
  subscribe(name: string, params: unknown, callback: (item: unknown) => void): Promise<unknown>;
};

/** Follows the setting as it applies in `workspaceId` (null: the home window, the global value). */
export async function followUpColor(bridge: Bridge, workspaceId: string | null): Promise<void> {
  const layer = workspaceId ?? "global";
  const read = async () => {
    const result = await bridge.call("platform.settings.read", { module: "platform", keys: ["upColor"], workspaceId: layer }) as
      { values: { upColor: { value: UpColor } } };
    setUpColor(result.values.upColor.value);
  };
  await bridge.subscribe("platform.settings.changes", {}, item => {
    const change = item as { module: string; workspaceId: string; keys: string[] };
    if (change.module === "platform" && change.keys.includes("upColor") && (change.workspaceId === "global" || change.workspaceId === layer)) void read();
  });
  await read();
}

export function useUpColor(): UpColor {
  const [value, setValue] = useState<UpColor>(upColor);
  useEffect(() => {
    setValue(upColor());
    listeners.add(setValue);
    return () => { listeners.delete(setValue); };
  }, []);
  return value;
}
