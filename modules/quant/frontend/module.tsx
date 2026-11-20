import { createRoot } from "react-dom/client";
import { ModuleContextProvider, TxRoot, type Json, type ModuleContext } from "@talyx/sdk";
import { QuantShell, readShellState, type PanelHost, type ShellState } from "./QuantShell";
import { route } from "./types";

export function activate(ctx: ModuleContext): void {
  /** A panel shows one Quant thing at a time; the result view opens a run's thing by its run id. */
  const mount = (element: HTMLElement, panel: ModuleContext) => {
    const root = createRoot(element);
    element.style.height = "100%";
    const props = panel.props as {runId?: string} | null;
    const stateRef = {current: props !== null && typeof props.runId === "string" ? {runId: props.runId} as ShellState : readShellState(panel.initialState)};
    // Only closing the panel itself may stop a notebook's kernel; a workspace being replaced or the window closing leaves it running.
    const host: PanelHost = {module: ctx, leave: {reason: "detach", confirmed: false, guard: null}};
    let disposed = false;
    const render = () => root.render(<ModuleContextProvider.Provider value={panel}><TxRoot overlay={panel.overlayRoot} style={{height: "100%", minHeight: 0}}>
      <QuantShell ctx={panel} host={host} stateRef={stateRef} register={() => {}} />
    </TxRoot></ModuleContextProvider.Provider>);
    if (stateRef.current.objectId || stateRef.current.runId) {
      void route<ShellState>(panel, "POST", "/workspace/adopt", stateRef.current).then(adopted => {
        if (!disposed) { stateRef.current = adopted; render(); }
      }, error => { if (!disposed) root.render(<p role="alert">{String(error)}</p>); });
    } else render();
    return {captureState: () => stateRef.current as unknown as Json,
      prepareLeave: (reason: string) => {
        host.leave.reason = reason === "close" ? "close" : "detach";
        const decision = reason === "close" && host.leave.guard !== null ? host.leave.guard() : {decision: "allow" as const};
        host.leave.confirmed = decision.decision === "confirm";
        return decision;
      },
      dispose: () => { disposed = true; root.unmount(); }};
  };
  ctx.panels.provide("workspace", mount);
  ctx.views.provide("result", mount);
}
