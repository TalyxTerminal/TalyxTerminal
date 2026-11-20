import { useEffect, useRef, useState } from "react";
import { TxButton, TxEmpty, type Json } from "@talyx/sdk";
import type { JsonValue } from "@talyx/sdk-ui";
import { boundedModuleState } from "../runtime/moduleState";
import { platformRuntime } from "./runtime";

interface RegisteredPanelHostProps {
  id: string;
  owner: string;
  state: unknown;
  active: boolean;
  visible: boolean;
  /** The panel's title as the workspace keeps it, for a module that isn't installed. */
  title?: string;
  onActivate(): void;
  onTitleChange?(title: string): void;
  onStateChange?(state: JsonValue, change?: { derived: boolean }): void;
  /** Lets the workspace read the panel's current state when it saves. */
  registerState(id: string, read: (() => JsonValue) | null): void;
}

/** One workspace panel: mounts the module's panel and keeps its latest state with the panel type it belongs to. */
export function RegisteredPanelHost(props: RegisteredPanelHostProps) {
  const kernel = platformRuntime().kernel;
  const [, redraw] = useState(0);
  useEffect(() => kernel.change.watch(redraw), [kernel]);
  const root = useRef<HTMLDivElement>(null);
  const mounted = useRef<ReturnType<typeof kernel.mountPanel> | null>(null);
  const saved = useRef(boundedModuleState(props.state ?? null));
  const callbacks = useRef(props);
  callbacks.current = props;
  const [error, setError] = useState<string | null>(null);
  const [attempt, setAttempt] = useState(0);
  const [title, setTitle] = useState(kernel.definitions.get(props.owner)?.name ?? props.owner);
  const live = kernel.modules.get(props.owner);

  useEffect(() => {
    let current: ReturnType<typeof kernel.mountPanel> | null = null;
    const envelope = saved.current as { panelType?: string; state?: Json } | null;
    const type = envelope?.panelType ?? "";
    const capture = (): JsonValue => {
      if (current) saved.current = boundedModuleState({ panelType: type, state: current.instance.captureState?.() ?? envelope?.state ?? null });
      return saved.current;
    };
    const dispose = () => {
      capture();
      const value = current;
      current = null; mounted.current = null;
      value?.instance.dispose();
    };
    let retired = false;
    // StrictMode replays this effect before child renderer effects have run.
    // Only mount after that synchronous replay, while this host is still live.
    queueMicrotask(() => {
      if (retired) return;
      callbacks.current.registerState(props.id, capture);
      // Until the module is running there is nothing to mount; the panel says why below (or stays empty while loading).
      if (!live?.ready) return;
      try {
        if (!root.current || !type) throw new Error("Module is unavailable");
        current = kernel.mountPanel(
          type, props.id, root.current, envelope?.state ?? null,
          message => { try { capture(); } finally { setError(message); } },
          () => { capture(); current = null; mounted.current = null; },
          {
            onTitleChange: value => { setTitle(value); callbacks.current.onTitleChange?.(value); },
            onStateChange: change => callbacks.current.onStateChange?.(capture(), change),
          },
        );
        mounted.current = current;
        current.setPresentation(callbacks.current.active, callbacks.current.visible);
        setError(null);
      } catch (reason) {
        setError(reason instanceof Error ? reason.message : String(reason));
      }
    });
    return () => { retired = true; dispose(); callbacks.current.registerState(props.id, null); };
  }, [kernel, props.owner, props.id, live, live?.ready, attempt]);

  useEffect(() => mounted.current?.setPresentation(props.active, props.visible), [props.active, props.visible]);
  const runtime = platformRuntime();
  const installed = kernel.definitions.get(props.owner);
  // The module is gone (uninstalled, or never installed here): the panel keeps its place and its saved state.
  const missing = !installed && kernel.definitions.has("platform");
  // The module couldn't start or is stopped: its reason, and a retry that starts it again.
  const notStarted = installed && (installed.state === "failed" || !installed.enabled || runtime.faults.has(props.owner) && !live?.ready)
    ? runtime.faults.get(props.owner) ?? installed.problem ?? (installed.enabled ? "The module couldn't start." : "The module is stopped.") : null;
  const content = <div data-talyx-module={props.owner} data-talyx-panel={props.id} onMouseDown={props.onActivate}
    style={{ height: "100%", minHeight: 0, overflow: "hidden" }}>
    <div ref={root} style={{ height: "100%", minHeight: 0, display: error || missing || notStarted ? "none" : undefined }} />
    {missing ? <ModuleMissing title={props.title ?? title} />
      : notStarted ? <PanelFailure owner={props.owner} reason={notStarted} onRetry={() => runtime.retry(props.owner).then(() => setAttempt(value => value + 1))} />
      : error && <PanelFailure owner={props.owner} reason={error} onRetry={() => { setError(null); setAttempt(value => value + 1); }} />}
  </div>;
  return content;
}

/** A panel whose module couldn't open: what failed and a retry, filling the panel instead of leaving it blank.
    The panel's saved state is kept, so retrying (or reopening the workspace) picks up where it was. */
export function PanelFailure({ owner, reason, onRetry }: { owner: string; reason: string; onRetry: () => void | Promise<void> }) {
  const installed = platformRuntime().kernel.definitions.get(owner);
  const [retrying, setRetrying] = useState(false);
  const [failed, setFailed] = useState<string | null>(null);
  return <div role="alert" style={{ display: "grid", placeItems: "center", height: "100%", minHeight: 0, background: "var(--tx-panel)" }}>
    <TxEmpty title={`${installed?.name ?? owner} couldn't open`} text={failed ?? reason}
      action={<TxButton size="sm" loading={retrying} onClick={() => {
        setRetrying(true); setFailed(null);
        void Promise.resolve(onRetry()).catch(error => setFailed(error instanceof Error ? error.message : String((error as { message?: string })?.message ?? error)))
          .finally(() => setRetrying(false));
      }}>Retry</TxButton>} />
  </div>;
}

/** A panel whose module isn't installed: it keeps its place and its content, which come back with the module. */
function ModuleMissing({ title }: { title: string }) {
  return <div role="status" style={{ display: "grid", placeItems: "center", height: "100%", minHeight: 0, background: "var(--tx-panel)" }}>
    <TxEmpty title={`${title} isn't installed`} text="This panel and its content stay in the workspace and come back when the module is installed again." />
  </div>;
}
