import { platformRuntime } from "../kernel/runtime";
import type { Subscription } from "@talyx/sdk";
import { workspaceWindowChannel as channel, type Command, type FrameRequest, type OpenWorkspace, type PanelNavigation } from "./workspaceWindows";
import type {} from "../../../host/desktop/preload";

import { stashUnsaved, type WorkspaceState } from "./workspaceChanges";

interface View { opened: OpenWorkspace[]; owned: string[]; active: string | null }

/** The home window's side of its workspace frames: which open workspaces it holds (a workspace is held by one window
    at a time: a Web Lock per workspace), which one shows, the frames' commands, and requests to a frame (save before
    closing, go to a notification's panel, keep unsaved work before the page reloads for a restarted Host). */
export class WorkspaceWindowController {
  private view: View = { opened: [], owned: [], active: null };
  private frames = new Map<string, HTMLIFrameElement>();
  private frameRemoval = new Map<string, () => void>();
  private releases = new Map<string, () => void>();
  private readyFrames = new Set<string>();
  private waiting = new Map<string, () => void>();
  private tail = Promise.resolve();
  private disposed = false;
  private restarting = false;
  /** Other tabs of this browser, for a workspace one of them holds. */
  private navigation = new BroadcastChannel("talyx-workspace-frames");
  constructor(private readonly ui: { show(view: View): void; close(id: string): Promise<void>; fail(message: string, retry?: () => void): void }) {}

  private set(patch: Partial<View>): void { this.view = { ...this.view, ...patch }; this.ui.show(this.view); }
  fail(reason: unknown): void { this.ui.fail(reason instanceof Error ? reason.message : String((reason as { message?: unknown })?.message ?? reason)); }
  private post(target: Window | null | undefined, message: object): void { target?.postMessage({ channel, ...message }, location.origin); }

  frame(id: string, element: HTMLIFrameElement | null): void {
    if (element) { this.frames.set(id, element); return; }
    this.frames.delete(id); this.frameRemoval.get(id)?.(); this.frameRemoval.delete(id);
  }
  /** The open workspaces, again; each frame and the home window are told. */
  async refresh(): Promise<OpenWorkspace[]> {
    const records = await platformRuntime().platform["workspace.list_open"]({}) as OpenWorkspace[];
    if (this.disposed) return records;
    const ids = new Set(records.map(record => record.workspaceId));
    for (const [id, release] of this.releases) if (!ids.has(id)) { release(); this.releases.delete(id); this.readyFrames.delete(id); }
    this.set({ opened: records, owned: this.view.owned.filter(id => ids.has(id)) });
    if (this.view.active && !ids.has(this.view.active)) this.select(null);
    this.post(window, { opened: records });
    for (const frame of this.frames.values()) this.post(frame.contentWindow, { opened: records });
    return records;
  }
  private select(id: string | null): void {
    this.set({ active: id });
    for (const [frameId, frame] of this.frames) this.post(frame.contentWindow, { displayed: frameId === id });
    const url = new URL(location.href);
    if (id) url.searchParams.set("workspaceId", id); else url.searchParams.delete("workspaceId");
    history.replaceState(null, "", url);
    if (!id) window.dispatchEvent(new Event("talyx:workspace-manager"));
  }
  /** Shows a workspace here, unless another window holds it: then that window comes to the front. */
  private async claim(id: string): Promise<void> {
    if (this.releases.has(id)) { this.select(id); return; }
    const acquired = await new Promise<boolean>((resolve, reject) => {
      void navigator.locks.request("talyx-workspace:" + id, { ifAvailable: true }, lock => {
        if (!lock || this.disposed) { resolve(false); return; }
        return new Promise<void>(release => { this.releases.set(id, release); resolve(true); });
      }).catch(reject);
    });
    if (!acquired) {
      if (window.talyxDesktop) { await window.talyxDesktop.openWorkspaceWindow(id); return; }
      throw new Error("This workspace is open in another window.");
    }
    if (!this.view.owned.includes(id)) this.set({ owned: [...this.view.owned, id] });
    this.select(id);
  }
  private letGo(id: string): void {
    this.readyFrames.delete(id);
    this.releases.get(id)?.(); this.releases.delete(id);
    if (this.view.active === id) this.select(null);
  }
  /** Lets a workspace go and waits until its frame is gone: before it shows in another window, and before it closes
      (the Host refuses a closed workspace's calls, and a frame still running would go on making them). */
  async removeFrame(id: string): Promise<void> {
    this.letGo(id);
    if (this.frames.has(id)) await new Promise<void>(resolve => {
      this.frameRemoval.set(id, resolve);
      this.set({ owned: this.view.owned.filter(item => item !== id) });
    });
  }
  private async execute(command: Command): Promise<OpenWorkspace[]> {
    const platform = platformRuntime().platform;
    if (command.kind === "open") {
      const existing = (await this.refresh()).find(record => record.workspaceId === command.presentation?.workspaceId ||
        Boolean(command.snapshotId && record.snapshotId === command.snapshotId));
      if (existing) { await this.claim(existing.workspaceId); return this.refresh(); }
      const record = await platform["workspace.open"]({
        ...(command.presentation ? { workspaceId: command.presentation.workspaceId, presentation: JSON.parse(JSON.stringify(command.presentation)) } : {}),
        snapshotId: command.snapshotId, name: command.name,
      });
      await this.claim(record.workspaceId);
    } else if (command.kind === "select") await this.claim(command.id);
    else if (command.kind === "ready") {
      this.readyFrames.add(command.id); this.waiting.get(command.id)?.(); this.waiting.delete(command.id);
      this.post(this.frames.get(command.id)!.contentWindow, { displayed: this.view.active === command.id });
    } else if (command.kind === "close") await this.ui.close(command.id);
    else if (command.kind === "home") this.select(null);
    else if (command.kind === "new-window") {
      await this.removeFrame(command.id);
      const url = new URL(location.href); url.searchParams.set("workspaceId", command.id);
      if (window.talyxDesktop) await window.talyxDesktop.openWorkspaceWindow(command.id);
      else window.open(url.href, "_blank");
    }
    return this.refresh();
  }
  /** Asks the frame of a workspace this window holds; answers whether it has unsaved changes. */
  private async reveal(id: string, request: FrameRequest): Promise<boolean> {
    if (!this.readyFrames.has(id)) await new Promise<void>(resolve => this.waiting.set(id, resolve));
    const frame = this.frames.get(id)!;
    const requestId = crypto.randomUUID();
    return new Promise<boolean>((resolve, reject) => {
      const receive = (event: MessageEvent) => {
        if (event.origin !== location.origin || event.source !== frame.contentWindow || event.data?.channel !== channel || event.data.requestId !== requestId) return;
        window.removeEventListener("message", receive);
        if (event.data.error) reject(new Error(event.data.error)); else resolve(event.data.unsaved);
      };
      window.addEventListener("message", receive);
      this.post(frame.contentWindow, { requestId, ...request });
    });
  }
  /** Asks a workspace's frame, here or in the tab that holds it. */
  async request(id: string, request: FrameRequest): Promise<boolean> {
    await this.claim(id);
    if (this.releases.has(id)) return this.reveal(id, request);
    return new Promise<boolean>((resolve, reject) => {
      const requestId = crypto.randomUUID();
      const receive = (event: MessageEvent) => {
        if (event.data.requestId !== requestId || event.data.request) return;
        this.navigation.removeEventListener("message", receive);
        if (event.data.error) reject(new Error(event.data.error)); else resolve(event.data.unsaved);
      };
      this.navigation.addEventListener("message", receive);
      this.navigation.postMessage({ id, request, requestId });
    });
  }
  private async navigate(target: PanelNavigation): Promise<void> {
    await platformRuntime().platform["workspace.open"]({ workspaceId: target.workspaceId });
    await this.refresh();
    await this.request(target.workspaceId, { notificationNavigation: target });
    if (window.talyxDesktop) await window.talyxDesktop.openWorkspaceWindow(target.workspaceId);
  }
  /** The Host restarted: each frame keeps its unsaved changes in this tab, then the page reloads and every open
      workspace comes back as it was (WorkspaceFrame takes the stash back as unsaved). */
  async hostRestarted(): Promise<void> {
    if (this.restarting) return;
    this.restarting = true;
    const frames = [...this.frames.entries()];
    for (const [, frame] of frames) frame.inert = true;
    try {
      const states = await Promise.all(frames.map(([id, frame]) => new Promise<WorkspaceState>((resolve, reject) => {
        const requestId = crypto.randomUUID();
        const finish = () => {
          clearTimeout(timer);
          window.removeEventListener("message", receive);
        };
        const receive = (event: MessageEvent) => {
          if (event.origin !== location.origin || event.source !== frame.contentWindow || event.data?.channel !== channel ||
              event.data.requestId !== requestId) return;
          if (event.data.error) { finish(); reject(new Error(event.data.error)); }
          else if (event.data.recovery?.workspaceId === id) { finish(); resolve(event.data.recovery); }
        };
        const timer = setTimeout(() => {
          finish(); reject(new Error("A workspace did not return its recovery state."));
        }, 10000);
        window.addEventListener("message", receive);
        this.post(frame.contentWindow, { requestId, stash: true });
      })));
      if (states.length) await stashUnsaved(states);
      for (const [, frame] of frames) frame.contentWindow?.dispatchEvent(new Event("talyx:reload-stashed"));
      location.reload();
    } catch (error) {
      this.restarting = false;
      for (const [, frame] of frames) frame.inert = false;
      this.ui.fail(`Workspaces kept open: ${error instanceof Error ? error.message : String(error)}`, () => void this.hostRestarted());
    }
  }

  /** Starts following frames, the Host and the desktop app; returns the cleanup. */
  start(): () => void {
    this.disposed = false;
    const runtime = platformRuntime();
    // This window owns navigation; workspace panels live in its frames.
    runtime.kernel.displayed.set(false);
    runtime.bridge.onHostRestart = () => this.hostRestarted();
    this.navigation.onmessage = event => {
      const { id, request, requestId } = event.data as { id: string; request?: FrameRequest; requestId: string };
      if (!request || !this.releases.has(id)) return;
      if (request.notificationNavigation) this.select(id);
      void this.reveal(id, request).then(unsaved => this.navigation.postMessage({ requestId, unsaved }), reason => this.navigation.postMessage({ requestId, error: String(reason) }));
    };
    const receive = (event: MessageEvent) => {
      if (event.origin !== location.origin || event.data?.channel !== channel ||
          event.source !== window && ![...this.frames.values()].some(frame => frame.contentWindow === event.source)) return;
      if (event.data.restart) { void this.hostRestarted(); return; }
      if (!event.data.command) return;
      const { command, requestId } = event.data as { command: Command; requestId: string };
      this.tail = this.tail.then(async () => {
        try { this.post(event.source as Window, { requestId, opened: await this.execute(command) }); }
        catch (reason) { this.fail(reason); this.post(event.source as Window, { requestId, error: reason instanceof Error ? reason.message : String(reason) }); }
      });
    };
    window.addEventListener("message", receive);
    let releaseDesktop: (() => void) | undefined;
    const desktopReady = () => { releaseDesktop = window.talyxDesktop?.onWorkspaceRequested(id => { void this.claim(id).catch(reason => this.fail(reason)); }); };
    window.addEventListener("talyx-desktop-ready", desktopReady);
    desktopReady();
    const subscriptions: Promise<Subscription>[] = [];
    // This start's own flag: a start stopped and started again (React's StrictMode does) must not go on to claim the
    // workspace beside the new one, whose claim would then find the lock taken.
    let stopped = false;
    void (async () => {
      await runtime.ready();
      if (stopped) return;
      const records = await this.refresh();
      if (stopped) return;
      subscriptions.push(runtime.bridge.subscribe("platform.workspace.changes", {}, () => { void this.refresh().catch(reason => this.fail(reason)); }));
      subscriptions.push(runtime.bridge.subscribe("platform.notifications.activated", {}, value => {
        void this.navigate((value as unknown as { navigation: PanelNavigation }).navigation).catch(reason => this.fail(reason));
      }));
      const requested = new URL(location.href).searchParams.get("workspaceId");
      if (requested && !records.some(record => record.workspaceId === requested)) {
        await runtime.platform["workspace.open"]({ workspaceId: requested });
        await this.refresh();
      }
      const id = requested ?? records.at(-1)?.workspaceId;
      if (!stopped && id) await this.claim(id);
    })().catch(reason => { if (!stopped) this.fail(reason); });
    return () => {
      stopped = true;
      this.disposed = true;
      window.removeEventListener("message", receive);
      window.removeEventListener("talyx-desktop-ready", desktopReady); releaseDesktop?.();
      this.navigation.onmessage = null;
      for (const subscription of subscriptions) void subscription.then(value => value.close());
      for (const release of this.releases.values()) release();
      this.releases.clear();
    };
  }
}
