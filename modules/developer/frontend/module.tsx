import { createRoot } from "react-dom/client";
import { ModuleContextProvider, TxRoot, failure, type Json, type ModuleContext, type PanelRef } from "@talyx/sdk";
import { DeveloperWorkbench, type WorkbenchState } from "./workbench/DeveloperWorkbench";
import { documents } from "./documents/service";
import { mountProjectEditor } from "./documents/projectEditorView";
import { mountDocumentEditor } from "./documents/editorView";
import { mountTextEditor } from "./documents/textEditorView";
import { mountCloseProtection } from "./documents/CloseProtection";
import { bindLanguage } from "./documents/language";

/** The full editor of this window: there is one; opening another (the launcher, an open target) goes to it. */
const instances = new Map<string, { ref: PanelRef; element: HTMLElement }>();
function liveInstances(ctx: ModuleContext) {
  const panels = ctx.platform.panels.list("developer.main");
  for (const [id, record] of instances) if (!record.element.isConnected || !panels.some(ref => ref.sessionId === record.ref.sessionId && ref.mountId === record.ref.mountId)) instances.delete(id);
  return [...instances.values()];
}

/** An open target: "file:<file URI>" opens that file, "folder:<path>" that folder as a project. */
async function openTarget(objectId: unknown) {
  if (typeof objectId !== "string") return;
  if (objectId.startsWith("file:")) await documents.open({ kind: "file", uri: objectId.slice("file:".length) });
  else if (objectId.startsWith("folder:")) documents.activateProject((await documents.openProject(objectId.slice("folder:".length))).projectId);
}

export function mountDeveloperPanel(element: HTMLElement, ctx: ModuleContext) {
  if (!ctx.panelRef) throw failure("invalid_target", "Developer requires a mounted panel.");
  const { objectId, ...initial } = (ctx.initialState ?? {}) as WorkbenchState & { objectId?: unknown };
  const existing = liveInstances(ctx).find(record => record.ref.panelId !== ctx.panelRef!.panelId);
  if (existing) {
    // One full editor per window: what was asked of this panel goes to it, and this panel closes.
    const self = ctx.panelRef;
    ctx.platform.panels.activate(existing.ref);
    void openTarget(objectId).catch(error => documents.report(error)).finally(() => {
      if (ctx.platform.panels.list("developer.main").some(ref => ref.mountId === self.mountId)) void ctx.platform.panels.close(self).catch(() => undefined);
    });
    return { captureState: () => null, prepareLeave: () => ({ decision: "allow" as const }), dispose() {} };
  }
  element.style.height = "100%"; element.style.minHeight = "0";
  const record = { ref: ctx.panelRef, element };
  instances.set(ctx.panelRef.panelId, record);
  const root = createRoot(element);
  let workbenchState: WorkbenchState = initial;
  const saveWorkbenchState = (state: WorkbenchState) => {
    const captured = { ...state, ...(workbenchState.checkpoint ? { checkpoint: workbenchState.checkpoint } : {}) };
    if (JSON.stringify(captured) === JSON.stringify(workbenchState)) return;
    workbenchState = captured; ctx.panel?.stateChanged();
  };
  const releaseView = documents.registerView(ctx.panelRef.mountId, null, () => ctx.platform.panels.close(ctx.panelRef!));
  root.render(<ModuleContextProvider.Provider value={ctx}><TxRoot overlay={ctx.overlayRoot} style={{ height: "100%", minHeight: 0 }}><DeveloperWorkbench ctx={ctx} onStateChange={saveWorkbenchState} /></TxRoot></ModuleContextProvider.Provider>);
  void openTarget(objectId).catch(error => documents.report(error));
  let disposed = false;
  const dispose = () => { if (disposed) return; disposed = true; releaseView(); if (instances.get(record.ref.panelId) === record) instances.delete(record.ref.panelId); root.unmount(); };
  ctx.onDispose(dispose);
  return { captureState: () => workbenchState as unknown as Json,
    prepareSave: async () => { const checkpoint = await documents.checkpointWorkbench(); workbenchState = { ...workbenchState, checkpoint }; if (!checkpoint) delete workbenchState.checkpoint; },
    prepareLeave: () => documents.prepareViewLeave(ctx.panelRef!.mountId), dispose };
}

export function activate(ctx: ModuleContext): void {
  ctx.onDispose(documents.bind(ctx));
  ctx.onDispose(mountCloseProtection(ctx));
  ctx.onDispose(bindLanguage(ctx));
  ctx.onDispose(() => instances.clear());
  ctx.panels.provide("main", mountDeveloperPanel);
  ctx.views.provide("editor", mountDocumentEditor);
  ctx.views.provide("text_editor", mountTextEditor);
  ctx.views.provide("project_editor", mountProjectEditor);
}
