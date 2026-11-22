import { t } from "./i18n";
import { createRoot } from "react-dom/client";
import { ModuleContextProvider, TxRoot, type Json, type LeaveDecision, type ModuleContext } from "@talyx/sdk";
import { QuantShell, readShellState, type PanelHost, type ShellState } from "./QuantShell";
import { notebookApi } from "./notebook";
import { route } from "./types";

/** Closing the panel: the notebook shown asks when cells run (its guard), and so does one left earlier with cells running then. */
function closing(host: PanelHost): LeaveDecision {
  const shown = host.leave.guard !== null ? host.leave.guard() : {decision: "allow" as const};
  const left = [...host.detached.values()].filter(item => item.running > 0);
  if (left.length === 0) return shown;
  const lines = left.map(item => item.running === 1 ? t("{name} was running a cell when it was left.", { name: item.name })
    : t("{name} was running {count} cells when it was left.", { name: item.name, count: item.running }));
  return {decision: "confirm", message: [...(shown.decision === "confirm" && shown.message ? [shown.message] : []), ...lines,
    t("Closing the panel stops their kernels; their variables are lost.")].join("\n\n")};
}

/** A kernel this panel left running stops with the panel, unless another panel shows its notebook. */
async function stopUnshown(ctx: ModuleContext, objectId: string) {
  const api = notebookApi(ctx);
  const kernel = await api.kernel(objectId);
  if (kernel.panels <= 0) await api.stop(objectId);
}

export function activate(ctx: ModuleContext): void {
  /** A panel shows one Quant thing at a time; the result view opens a run's thing by its run id. */
  const mount = (element: HTMLElement, panel: ModuleContext) => {
    const root = createRoot(element);
    element.style.height = "100%";
    const props = panel.props as {runId?: string} | null;
    const stateRef = {current: props !== null && typeof props.runId === "string" ? {runId: props.runId} as ShellState : readShellState(panel.initialState)};
    // Only closing the panel itself stops its notebooks' kernels; going to another page of the panel, a workspace being replaced or the
    // window closing leaves them running (closing the workspace stops them with its other work).
    const host: PanelHost = {module: ctx, leave: {reason: "detach", confirmed: false, guard: null}, detached: new Map()};
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
        const decision = reason === "close" ? closing(host) : {decision: "allow" as const};
        host.leave.confirmed = decision.decision === "confirm";
        return decision;
      },
      dispose: () => {
        disposed = true;
        const closed = host.leave.reason === "close";
        root.unmount();
        if (closed) for (const objectId of host.detached.keys()) void stopUnshown(ctx, objectId);
      }};
  };
  ctx.panels.provide("workspace", mount);
  ctx.views.provide("result", mount);
}
