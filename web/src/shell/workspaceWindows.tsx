import { useEffect, useRef, useState, type ReactNode } from "react";
import { TxToastProvider, useTxToast, type Json } from "@talyx/sdk";
import type { Platform } from "@talyx/sdk";
import type { WorkspaceState } from "./workspaceChanges";
import { WorkspaceCloseFlow, type WorkspaceCloseHandle } from "./WorkspaceCloseFlow";
import { WorkspaceWindowController } from "./workspaceWindowController";
type OpenRecord = Platform.OpenRecord;

/* The home window holds one frame (an iframe) per open workspace it shows; frames ask it to open, select, close and
   so on (`workspaceWindowCommand`), and it answers with the open workspaces. */

/** An open workspace as the Host lists it; `presentation` is what its frame keeps there (its unsaved state). */
export type OpenWorkspace = Omit<OpenRecord, "presentation"> & { presentation?: WorkspaceState | null };
/** Where a notification leads: a panel of its sender, in a workspace. */
export interface PanelNavigation { workspaceId: string; panel: string; state: Json }
/** What the home window asks of a frame. */
export type FrameRequest = { workspaceAction?: "status" | "save"; notificationNavigation?: PanelNavigation; stash?: boolean };
export type Command = { kind: "open"; snapshotId: string | null; name: string; presentation: WorkspaceState | null }
  | { kind: "select" | "new-window" | "close" | "ready"; id: string } | { kind: "home" | "list" | "changed" };
export const workspaceWindowChannel = "talyx-workspace-window";
export const workspaceFrameId = () => new URL(location.href).searchParams.get("workspaceFrame") === "1"
  ? new URL(location.href).searchParams.get("workspaceId") : null;

/** From a frame (or the home window itself): run a command in the home window; answers with the open workspaces. */
export function workspaceWindowCommand(command: Command): Promise<OpenWorkspace[]> {
  const requestId = crypto.randomUUID();
  return new Promise((resolve, reject) => {
    const receive = (event: MessageEvent) => {
      if (event.origin !== location.origin || event.source !== window.parent || event.data?.channel !== workspaceWindowChannel || event.data.command || event.data.requestId !== requestId) return;
      window.removeEventListener("message", receive);
      if (event.data.error) reject(new Error(event.data.error));
      else resolve(event.data.opened);
    };
    window.addEventListener("message", receive);
    window.parent.postMessage({ channel: workspaceWindowChannel, requestId, command }, location.origin);
  });
}

export function WorkspaceWindows({ home }: { home: ReactNode }) {
  return <TxToastProvider><WorkspaceWindowsBody home={home} /></TxToastProvider>;
}

function WorkspaceWindowsBody({ home }: { home: ReactNode }) {
  const toast = useTxToast();
  const [view, setView] = useState<{ opened: OpenWorkspace[]; owned: string[]; active: string | null }>({ opened: [], owned: [], active: null });
  const closeFlow = useRef<WorkspaceCloseHandle>(null);
  const [controller] = useState(() => new WorkspaceWindowController({
    show: setView, close: id => closeFlow.current!.close(id),
    fail: (message, retry) => toast({ id: "workspace-error", title: message, tone: "failed",
      ...(retry ? { duration: Infinity, action: { label: "Retry", onClick: retry } } : {}) }),
  }));
  useEffect(() => controller.start(), [controller]);
  return <>
    <WorkspaceCloseFlow ref={closeFlow} onClosing={id => controller.removeFrame(id)} onClosed={() => controller.refresh().then(() => undefined)}
      onError={reason => controller.fail(reason)} onWorkspaceAction={(id, action) => controller.request(id, { workspaceAction: action })} />
    <div hidden={view.active !== null}>{home}</div>
    {view.owned.map(id => {
      const url = new URL(location.href); url.searchParams.set("workspaceId", id); url.searchParams.set("workspaceFrame", "1"); url.hash = "";
      return <iframe key={id} ref={frame => controller.frame(id, frame)}
        data-workspace-id={id} title={"Workspace " + (view.opened.find(item => item.workspaceId === id)?.name ?? "Untitled")}
        src={url.href} style={{ display: view.active === id ? "block" : "none", width: "100%", height: "100dvh", border: 0 }} />;
    })}
  </>;
}
