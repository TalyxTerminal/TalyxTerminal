import { t } from "../i18n";
import { useEffect, useRef, useState, type ReactNode } from "react";
import { TxAlert, TxButton, TxSkeleton, type Json, type ModuleContext } from "@talyx/sdk";
import { useAgentSettings } from "../data/useAgent";
import type { AgentClient } from "../data/client";
import type { AgentSettings } from "../data/types";

export function errorText(error: unknown): string {
  return error instanceof Error ? error.message : String(error);
}

/** Loads Agent settings for one section and shows loading and load failure the same way everywhere. */
export function SettingsData({ ctx, children }: {
  ctx: ModuleContext;
  children: (settings: AgentSettings, client: AgentClient, refresh: () => Promise<void>) => ReactNode;
}) {
  const { client, value, error, refresh } = useAgentSettings(ctx);
  if (error) return <TxAlert title={t("Agent settings couldn't be read")} actions={<TxButton size="sm" onClick={() => void refresh()}>{t("Try again")}</TxButton>}>{errorText(error)}</TxAlert>;
  if (!value) return <div className="agent-settings-loading"><TxSkeleton width="60%" /><TxSkeleton width="80%" /><TxSkeleton width="40%" /></div>;
  return <>{children(value, client, refresh)}</>;
}

/** Runs one action at a time for a section and keeps its last error next to it. */
export function useBusy() {
  const [busy, setBusy] = useState<string | null>(null), [error, setError] = useState<{ id: string; text: string } | null>(null);
  const run = async (id: string, work: () => Promise<unknown>) => {
    setBusy(id); setError(null);
    try { await work(); return true; }
    catch (failure) { setError({ id, text: errorText(failure) }); return false; }
    finally { setBusy(null); }
  };
  return { busy, error, run, clear: () => setError(null) };
}

export interface TaskRecord { state: "running" | "completed" | "failed" | "cancelled"; result: Json; error: { message?: string } | null }

/** Waits for a longer request made from settings (a connection test, a model info refresh); cancelling stops waiting. */
export function useTask() {
  const [record, setRecord] = useState<TaskRecord | null>(null);
  const controller = useRef<AbortController | null>(null), alive = useRef(true);
  useEffect(() => () => { alive.current = false; controller.current?.abort(); }, []);
  return {
    record, running: record?.state === "running",
    start: async (work: (signal: AbortSignal) => Promise<Json>) => {
      const current = new AbortController();
      controller.current = current;
      setRecord({ state: "running", result: null, error: null });
      try {
        const result = await work(current.signal);
        if (alive.current) setRecord({ state: "completed", result, error: null });
      } catch (failure) {
        if (alive.current) setRecord(current.signal.aborted ? { state: "cancelled", result: null, error: null } : { state: "failed", result: null, error: { message: errorText(failure) } });
      }
    },
    cancel: async () => { controller.current?.abort(); },
  };
}
