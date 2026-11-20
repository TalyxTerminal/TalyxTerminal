import type { ModuleRenderFault } from "@talyx/sdk-ui";

export const MAX_MODULE_RENDER_FAULT_BYTES = 4 * 1024;
export const GENERIC_MODULE_RENDER_FAULT = "module renderer fault";

function validMessage(value: unknown): value is string {
  return (
    typeof value === "string" &&
    value.length > 0 &&
    new TextEncoder().encode(value).length <= MAX_MODULE_RENDER_FAULT_BYTES
  );
}

/**
 * Convert the public JSON-shaped fault crossing into bounded panel text. A malformed
 * first call deliberately still revokes the panel, rather than leaving a broken renderer
 * with a live scoped bridge.
 */
export function renderFaultMessage(value: unknown): string {
  try {
    if (!value || typeof value !== "object") return GENERIC_MODULE_RENDER_FAULT;
    const fault = value as Partial<ModuleRenderFault>;
    return fault.phase === "render" && validMessage(fault.message)
      ? fault.message
      : GENERIC_MODULE_RENDER_FAULT;
  } catch {
    // A hostile/malformed Proxy or accessor belongs to the same generic first-fault
    // path. The panel guard has already revoked its bridge before this conversion runs.
    return GENERIC_MODULE_RENDER_FAULT;
  }
}

/** First-report-wins guard for one mounted panel; post-dispose reports are inert. */
export function createPanelRenderFaultGate() {
  let faulted = false;
  let disposed = false;
  const revoked = () => faulted || disposed;
  return {
    report(value: unknown): string | null {
      if (revoked()) return null;
      faulted = true;
      try {
        return renderFaultMessage(value);
      } catch {
        // Keep the public sink unconditionally non-throwing even if this helper is
        // changed later. The first call remains a generic, panel-local quarantine.
        return GENERIC_MODULE_RENDER_FAULT;
      }
    },
    dispose(): void {
      disposed = true;
    },
    isRevoked(): boolean {
      return revoked();
    },
    hasReportedFault(): boolean {
      return faulted;
    },
  };
}

export interface PanelFaultQuarantineActions {
  captureState: () => void;
  revokeBridge: () => void;
  dispose: () => void;
  removeRoot: () => void;
}

/**
 * Make the bounded panel transition best-effort. Capture and cleanup failures never
 * preserve a bridge or keep the executable root mounted.
 */
export function quarantinePanelFault(actions: PanelFaultQuarantineActions): void {
  try {
    actions.captureState();
  } catch {
    // The caller retains its previous valid opaque state.
  }
  try {
    actions.revokeBridge();
  } catch {
    // The local guard is already revoked before this helper is entered.
  }
  try {
    actions.dispose();
  } catch {
    // Cleanup diagnostics must not block the inert transition.
  }
  try {
    actions.removeRoot();
  } catch {
    // The shell still renders its inert diagnostic below this best-effort cleanup.
  }
}
