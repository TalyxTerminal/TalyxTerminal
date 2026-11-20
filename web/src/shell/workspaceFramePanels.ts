import { useCallback, useEffect, useRef, useState, type MutableRefObject } from "react";
import { failure, type Json, type PanelRef } from "@talyx/sdk";
import { platformRuntime } from "../kernel/runtime";
import { change, undoFailedOpen, unsaved, type ShellPanel, type WorkspaceState } from "./workspaceChanges";
import { freshPanel } from "./workspacePersistence";
import { reloading } from "./workspaceFrameState";
import { workspaceWindowChannel, type FrameRequest, type PanelNavigation } from "./workspaceWindows";
import type { ShellModuleChoice } from "./shellChrome";

type Commit = (next: (current: WorkspaceState) => WorkspaceState) => void;

/** A frame's panels: adding, closing (asking the module first), activating; and the kernel's way to do the same for
    modules and AI (platform.panels.*). */
export function useFramePanels(ref: MutableRefObject<WorkspaceState | null>, commit: Commit, ready: boolean, say: (title: string, failed?: boolean) => void) {
  const kernel = platformRuntime().kernel;
  const [panelConfirm, setPanelConfirm] = useState<{ title: string; message: string; resolve: (ok: boolean) => void } | null>(null);
  const confirmedClose = useRef<string | null>(null);

  const addPanel = useCallback((panel: ShellPanel) => commit(current => change(current, { panels: [...current.panels, panel], activePanelId: panel.id })), [commit]);
  const addModule = useCallback((module: ShellModuleChoice) => {
    try { addPanel(freshPanel(module.kind.slice("module:".length), module.name)); }
    catch (reason) { say(`Module not added: ${reason instanceof Error ? reason.message : String(reason)}`, true); }
  }, [addPanel, say]);
  const removePanel = useCallback((id: string) => {
    const decision = kernel.panels.get(id)?.instance.prepareLeave?.("close");
    if (decision?.decision === "block") {
      const reason = failure("conflict", decision.message ?? "A module is busy");
      say(reason.message);
      return reason;
    }
    const confirmed = confirmedClose.current === id;
    confirmedClose.current = null;
    if (decision?.decision === "confirm" && !confirmed && !window.confirm(decision.message ?? "Close this module?"))
      return failure("cancelled", "Panel close cancelled");
    commit(current => {
      const panels = current.panels.filter(panel => panel.id !== id);
      return change(current, { panels, activePanelId: current.activePanelId === id ? panels[0]?.id ?? null : current.activePanelId });
    });
  }, [commit, kernel, say]);
  /** The dock asks before it closes a tab: a module may block, or want the user to confirm. */
  const requestPanelClose = useCallback(async (id: string): Promise<boolean> => {
    const decision = kernel.panels.get(id)?.instance.prepareLeave?.("close");
    if (decision?.decision === "block") { say(decision.message ?? "A module is busy"); return false; }
    if (decision?.decision !== "confirm") return true;
    const title = ref.current?.panels.find(panel => panel.id === id)?.title ?? "panel";
    const ok = await new Promise<boolean>(resolve => setPanelConfirm({ title: `Close ${title}?`, message: decision.message ?? "Unsaved changes in this panel will be lost.", resolve }));
    if (ok) confirmedClose.current = id;
    return ok;
  }, [kernel, ref, say]);
  const activatePanel = useCallback((id: string) => commit(current =>
    current.activePanelId === id || !current.panels.some(panel => panel.id === id) ? current : change(current, { activePanelId: id }, { derived: true })), [commit]);

  useEffect(() => {
    if (!ready) return;
    const mounted = (ref: PanelRef) => { if (kernel.panels.get(ref.panelId)?.ref.mountId !== ref.mountId) throw failure("target_gone"); };
    kernel.shell = {
      open: async (type: string, state: Json): Promise<PanelRef> => {
        const declared = kernel.panelType(type);
        if (!declared) throw failure("unavailable");
        const id = `module-${crypto.randomUUID()}`;
        const panel: ShellPanel = { id, kind: `module:${declared.owner}`, title: declared.spec.title, state: { panelType: type, state } };
        const before = ref.current!;
        let stop = () => undefined as void;
        let timer: ReturnType<typeof setTimeout> | undefined;
        try {
          addPanel(panel);
          // Resolved with the mount that survives React's commit (StrictMode replays setup and cleanup).
          return await new Promise<PanelRef>((resolve, reject) => {
            const check = () => queueMicrotask(() => { const mounted = kernel.panels.get(id); if (mounted) resolve(mounted.ref); });
            stop = kernel.change.watch(check);
            timer = setTimeout(() => reject(failure("unavailable", "Panel did not mount")), 10000);
            check();
          });
        } catch (reason) {
          commit(current => undoFailedOpen(current, before, id));
          throw reason;
        } finally { stop(); if (timer) clearTimeout(timer); }
      },
      activate: ref => { mounted(ref); activatePanel(ref.panelId); },
      close: async ref => {
        mounted(ref);
        const denied = removePanel(ref.panelId);
        if (denied) throw denied;
      },
      settings: section => window.dispatchEvent(new CustomEvent("talyx:settings", { detail: section })),
    };
    return () => { kernel.shell = undefined; };
  }, [ready, activatePanel, addPanel, commit, kernel, ref, removePanel]);

  return { addModule, removePanel, requestPanelClose, activatePanel, panelConfirm, setPanelConfirm };
}

/** What the home window asks of this frame: whether it has unsaved changes, to save before closing, to go to a
    notification's panel, or to keep its unsaved changes in the tab before the page reloads for a restarted Host. */
export function useFrameRequests(workspaceId: string, ref: MutableRefObject<WorkspaceState | null>, ready: boolean,
  live: (current: WorkspaceState) => WorkspaceState, save: () => Promise<unknown>) {
  useEffect(() => {
    if (!ready) return;
    const kernel = platformRuntime().kernel;
    const answer = async (request: FrameRequest): Promise<boolean> => {
      if (request.notificationNavigation) {
        const target: PanelNavigation = request.notificationNavigation;
        if (target.workspaceId !== workspaceId) throw new Error("This notification belongs to another workspace.");
        kernel.shell!.activate(await kernel.openPanel("platform", { type: target.panel, state: target.state }));
        return false;
      }
      if (request.workspaceAction === "save") {
        await save();
        if (unsaved(ref.current!)) throw new Error("The workspace has newer unsaved changes. Save again before closing.");
      }
      return unsaved(ref.current!);
    };
    const receive = (event: MessageEvent) => {
      if (event.origin !== location.origin || event.source !== window.parent || event.data?.channel !== workspaceWindowChannel || !event.data.requestId || event.data.command) return;
      const { requestId, ...request } = event.data as FrameRequest & { requestId: string };
      if (request.stash) {
        try {
          window.parent.postMessage({ channel: workspaceWindowChannel, requestId, recovery: live(ref.current!) }, location.origin);
        } catch (reason) {
          window.parent.postMessage({ channel: workspaceWindowChannel, requestId, error: String(reason) }, location.origin);
        }
        return;
      }
      if (!(request.workspaceAction || request.notificationNavigation)) return;
      void answer(request).then(value => window.parent.postMessage({ channel: workspaceWindowChannel, requestId, unsaved: value }, location.origin),
        reason => window.parent.postMessage({ channel: workspaceWindowChannel, requestId, error: reason instanceof Error ? reason.message : String(reason) }, location.origin));
    };
    const allowReload = () => { reloading.forHost = true; };
    window.addEventListener("talyx:reload-stashed", allowReload);
    window.addEventListener("message", receive);
    return () => {
      window.removeEventListener("message", receive);
      window.removeEventListener("talyx:reload-stashed", allowReload);
    };
  }, [workspaceId, ready, live, ref, save]);
}
