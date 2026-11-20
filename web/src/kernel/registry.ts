/* The window's module registry: the modules' declarations as the Host lists them (manifest and contract, version 5),
   their running frontends, and the panels and views they draw here. Calls to public actions go to the Host as they
   are; the module that receives one checks its input. What stays in the window: panels, views, a panel's own actions
   and the status items modules report. */
import { createPanelRenderFaultGate } from "../runtime/moduleFault";
import { WorkspaceContextRuntime } from "../runtime/workspaceContext";
import { validateView } from "./viewSchemas";
import {
  Scope, signalValue, failure, installUiStyles, TX_ROOT_CLASS, createViewUnavailable,
  type Json, type Schema, type ModuleDefinition, type ModuleContext, type PanelActionHandler, type Caller,
  type PanelRef, type ViewRef, type Mount, type Instance, type CallOptions, type Contribution, type LeaveDecision,
  type PanelStatus, type BusinessView, type ViewReport, type Subscription, type StreamOptions,
} from "@talyx/sdk";

type LiveModule = { definition: ModuleDefinition; scope: Scope; ctx: ModuleContext; ready: boolean };
type Presentation = { ref: PanelRef; scope: Scope; active: ReturnType<typeof signalValue<boolean>>; visible: ReturnType<typeof signalValue<boolean>> };
type LivePanel = Presentation & { type: string; ctx: ModuleContext; instance: Instance; prepareDispose(): void; focusedAt: number };
type PanelHost = { ref: PanelRef; state: Json; active: Presentation["active"]; visible: Presentation["visible"]; notifications: PanelHostNotifications };
type HostedView = { ref: ViewRef; props: Json; on: Record<string, (payload: Json) => void>; emits: Record<string, Schema> };
export interface PanelHostNotifications { onTitleChange(title: string): void; onStateChange(change: { derived: boolean }): void }
/** A panel as the Host reads it (panels.list): what it is and what it reports it shows. */
export interface PanelListing { panelId: string; type: string; active: boolean; visible: boolean; view: ViewReport | null }
/** The Host connection: public actions and streams. */
export interface Remote {
  call(name: string, input: Json, options?: { module?: string; signal?: AbortSignal }): Promise<Json>;
  subscribe(name: string, params: Json, onItem: (item: Json) => void | Promise<void>, options?: StreamOptions & { module?: string }): Promise<Subscription>;
}
/** What the workspace frame does for the kernel: panels are its layout's. */
export interface PanelShell {
  open(type: string, state: Json): Promise<PanelRef>;
  activate(ref: PanelRef): void; close(ref: PanelRef): Promise<void>; settings(section?: string): void;
}

const identity = (ref: PanelRef) => `${ref.sessionId}/${ref.panelId}/${ref.mountId}`;
const maxStateBytes = 512 * 1024;
const copy = <T,>(value: T): T => JSON.parse(JSON.stringify(value)) as T;
/** With the module's shared types beside it, so a view's `#/$defs/...` references resolve. */
const withDefinitions = (schema: Schema | undefined, definition: ModuleDefinition): Schema =>
  ({ ...(schema ?? { type: "object" }), $defs: definition.contract.$defs });

export class FrontendKernel {
  readonly workspace = new WorkspaceContextRuntime();
  readonly scope = new Scope("frontend");
  /** Every installed module (and the platform), by id, as the Host lists them. */
  readonly definitions = new Map<string, ModuleDefinition>();
  /** Modules whose frontend runs in this window. */
  readonly modules = new Map<string, LiveModule>();
  readonly panels = new Map<string, LivePanel>();
  readonly statusValues = new Map<string, { level: "ok" | "busy" | "warn" | "error"; text?: string; tooltip?: string; visible?: boolean }>();
  readonly panelStatuses = new Map<string, PanelStatus>();
  readonly change = signalValue(0);
  readonly displayed = signalValue(true);
  sessionId: string = crypto.randomUUID();
  remote?: Remote;
  shell?: PanelShell;
  reportFault?: (owner: string, error: unknown) => void;
  private factories = new Map<string, { mount: Mount; styles?: string }>();
  private panelActions = new Map<string, PanelActionHandler>();
  private presentations = new Map<string, Presentation>();
  private views = new Map<string, { owner: string; scope: Scope; parentPanel?: PanelRef; prepareLeave?(reason: string): LeaveDecision; dispose(): void }>();
  private viewReports = new Map<string, ViewReport>();
  private viewFlushers = new Map<string, Set<() => void | Promise<void>>>();
  private panelTitles = new Map<string, string>();
  private lastBusinessPanel: PanelRef | null = null;
  private stopGuards = new Map<string, Set<(reason: string) => void | Promise<void>>>();

  bindSession(sessionId: string): void {
    this.sessionId = sessionId;
    for (const panel of this.panels.values()) panel.ref.sessionId = sessionId;
  }
  private changed(): void { this.change.set(this.change.get() + 1); }

  /* ── declarations ───────────────────────────────────────────────────────────────────────────────────────── */

  /** The Host's list of modules, replacing the last one. A module no longer listed must be stopped first. */
  setDefinitions(list: readonly ModuleDefinition[]): void {
    this.definitions.clear();
    for (const definition of list) this.definitions.set(definition.id, definition);
    this.changed();
  }
  /** A panel type (`module.panel`) and its declaration. */
  panelType(type: string): { owner: string; spec: NonNullable<ModuleDefinition["manifest"]["panels"]>[string] } | undefined {
    const [owner, local] = type.split(".", 2);
    const spec = this.definitions.get(owner)?.manifest.panels?.[local];
    return spec ? { owner, spec } : undefined;
  }
  /** The panel a module offers for opening on its own (`launcher: true` in its manifest): what adding the module opens. */
  launcherPanel(owner: string): string | undefined {
    const local = Object.entries(this.definitions.get(owner)?.manifest.panels ?? {}).find(([, spec]) => spec.launcher)?.[0];
    return local ? `${owner}.${local}` : undefined;
  }
  /** What running modules provide at a place in the window (`owner.point`), as their manifests declare it: by module,
      each module's items in its manifest's order. */
  contributions<D>(point: string): Contribution<D>[] {
    return [...this.modules.values()].filter(m => m.ready).sort((a, b) => a.definition.id.localeCompare(b.definition.id))
      .flatMap(m => (m.definition.manifest.provides?.[point] ?? []).map(({ id, descriptor }) => ({ moduleId: m.definition.id, id, descriptor: descriptor as D })));
  }
  /** Whether `owner` may use `target`'s public actions and streams: the platform's, its own, or a module it requires. */
  private reach(owner: string, name: string): void {
    const target = name.split(".", 1)[0];
    if (target !== "platform" && target !== owner && owner !== "platform" && !this.definitions.get(owner)?.manifest.requires?.[target])
      throw failure("permission_denied", `${owner} does not list ${target} in its requires`);
  }

  /* ── frontends ──────────────────────────────────────────────────────────────────────────────────────────── */

  /** Runs a module's frontend entry; it must provide every panel and view its manifest declares. */
  async activate(definition: ModuleDefinition, activate: (ctx: ModuleContext) => unknown | Promise<unknown>): Promise<void> {
    const owner = definition.id;
    if (this.modules.has(owner)) throw failure("conflict", `${owner} is already running here`);
    const scope = this.scope.child(`${owner}:${crypto.randomUUID()}`);
    const live = { definition, scope, ready: false } as LiveModule;
    live.ctx = this.context(live, scope);
    this.modules.set(owner, live);
    try {
      await activate(live.ctx);
      const declared = [...Object.keys(definition.manifest.panels ?? {}), ...Object.keys(definition.manifest.views ?? {})].map(local => `${owner}.${local}`);
      const missing = declared.filter(name => !this.factories.has(name));
      if (missing.length) throw failure("invalid_input", `The frontend does not provide ${missing.join(", ")}`);
      live.ready = true;
    } catch (error) {
      this.modules.delete(owner);
      for (const key of this.statusValues.keys()) if (key.startsWith(`${owner}.`)) this.statusValues.delete(key);
      try { await scope.dispose(); } finally { this.changed(); }
      throw error;
    }
    this.changed();
  }
  /** Stops a module's frontend: its panels and views close (their state captured first). */
  async disable(owner: string): Promise<void> {
    const live = this.modules.get(owner); if (!live) return;
    live.ready = false; this.changed();
    // Scope disposal visits child views first. Capture panel state while those views are still there.
    for (const panel of this.panels.values()) if (panel.ctx.moduleId === owner) panel.prepareDispose();
    for (const view of [...this.views.values()]) if (view.owner === owner) view.dispose();
    try { await live.scope.dispose(); }
    catch (error) { this.reportFault?.(owner, error); }
    finally {
      this.modules.delete(owner);
      for (const key of this.statusValues.keys()) if (key.startsWith(`${owner}.`)) this.statusValues.delete(key);
      this.changed();
    }
  }
  /** Before a module stops: its frontend's own protection of live input (it rejects to keep running). */
  async prepareStop(owner: string, reason: string): Promise<void> {
    for (const protect of [...this.stopGuards.get(owner) ?? []]) await protect(reason);
  }
  prepareLeave(reason: string): LeaveDecision[] { return [...this.panels.values()].map(p => p.instance.prepareLeave?.(reason) ?? { decision: "allow" }); }
  private register(scope: Scope, name: string, value: { mount: Mount; styles?: string }): void {
    if (this.factories.has(name)) throw failure("invalid_input", `${name} is provided twice`);
    this.factories.set(name, value);
    scope.track(() => { this.factories.delete(name); });
  }
  private observe(scope: Scope, release: () => void): () => void {
    const close = scope.track(release);
    return () => { void close(); };
  }
  private disposeScope(scope: Scope, owner: string): void {
    scope.disposed = true;
    // A caller may be a React effect cleanup. Finish that commit before disposing a separately mounted renderer root inside this scope.
    queueMicrotask(() => { void scope.dispose().catch(error => this.reportFault?.(owner, error)); });
  }
  releaseWorkspacePanels(): void {
    for (const panel of this.panels.values()) { panel.prepareDispose(); this.disposeScope(panel.scope, panel.ctx.moduleId); }
  }

  /* ── calls ──────────────────────────────────────────────────────────────────────────────────────────────── */

  /** A public action, through the Host; or, with `options.target`, one of that panel's own actions, here. */
  async call(owner: string, name: string, input: Json, options: CallOptions = {}): Promise<Json> {
    if (options.target) return this.panelAction(owner, name, input, options.target, options.signal);
    this.reach(owner, name);
    if (!this.remote) throw failure("connection_lost");
    return this.remote.call(name, input, { module: owner === "platform" ? undefined : owner, signal: options.signal });
  }
  private async panelAction(owner: string, name: string, input: Json, target: PanelRef, signal?: AbortSignal): Promise<Json> {
    const panel = this.panel(target);
    const [module, local] = name.split(".", 2);
    const declared = this.panelType(panel.type);
    if (!declared || declared.owner !== module || !declared.spec.actions?.[local]) throw failure("unknown_capability", `${panel.type} has no action ${name}`);
    validateView(withDefinitions(declared.spec.actions[local] as Schema, this.definitions.get(module)!), input);
    const handler = this.panelActions.get(`${name}#${panel.ref.panelId}/${panel.ref.mountId}`);
    if (!handler) throw failure("unavailable", `${name} is not handled by this panel`);
    const caller: Caller = { origin: "user", module: owner === "platform" ? null : owner };
    return await handler(copy(input), { caller, signal: signal ?? new AbortController().signal });
  }
  private panel(ref: PanelRef): LivePanel {
    const panel = this.panels.get(ref.panelId);
    if (!panel || panel.scope.disposed || identity(panel.ref) !== identity(ref)) throw failure("target_gone");
    return panel;
  }

  /* ── panels ─────────────────────────────────────────────────────────────────────────────────────────────── */

  /** Opens a panel of a running module. `owner` opening another module's panel needs it to be public. */
  async openPanel(owner: string, input: { type: string; state?: Json }): Promise<PanelRef> {
    const declared = this.panelType(input.type);
    if (!declared) throw failure("unknown_capability", `No panel type ${input.type}`);
    if (declared.owner !== owner && owner !== "platform" && declared.spec.visibility === "private") throw failure("permission_denied", `${input.type} is private to its module`);
    return this.mountable(input.type).open(input.type, input.state ?? null);
  }
  /** Opens one object in the panel its module declares for it; the caller needs no dependency on that module. A panel
      already showing it is brought forward: what a panel shows is what its last view report lists as open targets,
      or, before it has reported them, what it was opened for. */
  async openTarget(target: { module: string; panelType: string; objectId: string }): Promise<PanelRef> {
    const type = `${target.module}.${target.panelType}`;
    const declared = this.panelType(type);
    if (!declared || declared.spec.visibility === "private") throw failure("invalid_input", `${type} is not a public panel`);
    const shows = (panel: LivePanel) => {
      const reported = this.viewReports.get(panel.ref.panelId)?.openTargets;
      return reported ? reported.some(item => item.key === target.objectId)
        : (panel.ctx.initialState as { objectId?: unknown } | null)?.objectId === target.objectId;
    };
    const existing = [...this.panels.values()].find(panel => panel.type === type && shows(panel));
    if (existing) { this.shell?.activate(existing.ref); return existing.ref; }
    return this.mountable(type).open(type, { objectId: target.objectId });
  }
  private mountable(type: string): PanelShell {
    if (!this.modules.get(type.split(".", 1)[0])?.ready || !this.factories.has(type)) throw failure("unavailable", `${type} is not available`);
    if (!this.shell) throw failure("ui_required", "No workspace is shown in this window");
    return this.shell;
  }
  /** The panels as the Host reads them (panels.list). */
  listPanels(): PanelListing[] {
    return [...this.panels.values()].map(panel => ({ panelId: panel.ref.panelId, type: panel.type,
      active: panel.active.get(), visible: panel.visible.get(), view: this.viewReports.get(panel.ref.panelId) ?? null }));
  }
  private title(panel: LivePanel): string { return this.panelTitles.get(panel.ref.panelId) ?? this.panelType(panel.type)?.spec.title ?? panel.type; }

  mountPanel(type: string, panelId: string, root: HTMLElement, state: Json, onFault: (message: string) => void, beforeDispose: () => void, host: PanelHostNotifications)
    : { ref: PanelRef; instance: Instance; setPresentation(active: boolean, visible: boolean): void } {
    const declared = this.panelType(type), live = declared && this.modules.get(declared.owner), factory = this.factories.get(type);
    if (!declared || !live?.ready || !factory) throw failure("unavailable");
    if (new TextEncoder().encode(JSON.stringify(state)).length > maxStateBytes) throw failure("invalid_input", "Panel state exceeds 512 KB");
    const owner = declared.owner;
    const scope = live.scope.child(`panel:${panelId}`), ref = { sessionId: this.sessionId, panelId, mountId: crypto.randomUUID() };
    const active = signalValue(false), visible = signalValue(true);
    this.presentations.set(panelId, { ref, scope, active, visible });
    scope.track(() => { if (this.presentations.get(panelId)?.ref.mountId === ref.mountId) this.presentations.delete(panelId); });
    const { container, overlay } = this.container(root, live.definition.source === "installed");
    let instance: Instance | undefined;
    // Coalesced state changes stay derived only while every one of them is.
    let pendingTitle: string | undefined, pendingState = false, pendingDerived = true, queued = false;
    const notifyHost = () => {
      if (queued) return;
      queued = true;
      // A synchronous mount can notify before its lifecycle is returned to the host; never notify a replacement mount.
      queueMicrotask(() => {
        queued = false;
        if (scope.disposed || this.panels.get(panelId)?.ref.mountId !== ref.mountId) return;
        const title = pendingTitle, changed = pendingState, derived = pendingDerived;
        pendingTitle = undefined; pendingState = false; pendingDerived = true;
        try { if (title !== undefined) host.onTitleChange(title); if (changed) host.onStateChange({ derived }); }
        catch (error) { this.reportFault?.(owner, error); }
      });
    };
    const notifications: PanelHostNotifications = {
      onTitleChange: title => { pendingTitle = title; this.panelTitles.set(panelId, title); notifyHost(); },
      onStateChange: change => { pendingState = true; pendingDerived &&= change.derived; notifyHost(); },
    };
    let disposalPrepared = false;
    const prepareDispose = () => {
      if (disposalPrepared || !instance) return;
      disposalPrepared = true;
      try { beforeDispose(); } catch (error) { this.reportFault?.(owner, error); }
    };
    const ctx = this.context(live, scope, { ref, state, active, visible, notifications }, undefined, overlay, message => { prepareDispose(); onFault(message); });
    scope.track(() => {
      if (this.panels.get(panelId)?.ref.mountId === ref.mountId) {
        this.panels.delete(panelId);
        this.viewReports.delete(panelId); this.panelTitles.delete(panelId); this.panelStatuses.delete(panelId);
        if (this.lastBusinessPanel?.panelId === panelId) this.lastBusinessPanel = null;
      }
      active.clear(); visible.clear(); this.changed();
    });
    try {
      instance = factory.mount(container, ctx);
      if (!instance || typeof instance.dispose !== "function") throw failure("invalid_output", "Invalid panel lifecycle");
      const ownLeave = instance.prepareLeave?.bind(instance);
      instance = { ...instance, prepareLeave: reason => {
        const decision = ownLeave?.(reason) ?? { decision: "allow" as const };
        if (decision.decision !== "allow") return decision;
        for (const view of this.views.values()) {
          if (view.parentPanel?.mountId !== ref.mountId) continue;
          const child = view.prepareLeave?.(reason);
          if (child && child.decision !== "allow") return child;
        }
        return decision;
      } };
      if (scope.disposed) throw failure("unavailable", "Panel reported a render fault while mounting");
    } catch (error) {
      this.reportFault?.(owner, error);
      try { instance?.dispose(); } finally { this.disposeScope(scope, owner); container.remove(); overlay.remove(); }
      throw error;
    }
    const panel: LivePanel = { type, ref, scope, ctx, instance, prepareDispose, focusedAt: 0, active, visible };
    this.panels.set(panelId, panel);
    scope.track(() => {
      prepareDispose();
      try { instance!.dispose(); } finally { container.remove(); overlay.remove(); }
    });
    this.changed();
    return { ref, instance: { ...instance, dispose: () => { prepareDispose(); this.disposeScope(scope, owner); } }, setPresentation: (a, v) => {
      if (scope.disposed || this.presentations.get(panelId)?.ref.mountId !== ref.mountId) return;
      active.set(a); visible.set(v);
      if (a && v) { panel.focusedAt = Date.now(); this.lastBusinessPanel = { ...ref }; }
    } };
  }
  /** Where a module draws: a third-party module's in its own shadow root, with the design system installed there. */
  private container(root: HTMLElement, shadow: boolean): { container: HTMLElement; overlay: HTMLElement } {
    const parent = shadow ? root.shadowRoot ?? root.attachShadow({ mode: "open" }) : root;
    const container = document.createElement("div"), overlay = document.createElement("div");
    // The overlay root is a design-system root: popups portalled into it get the base type, colour and focus rules.
    overlay.className = TX_ROOT_CLASS;
    container.setAttribute("data-talyx-container", ""); overlay.setAttribute("data-talyx-overlays", "");
    parent.append(container);
    // Official styles live in the document: their overlay sits outside panes whose glass or overflow would contain
    // fixed popups. A third-party module's overlay stays inside its own shadow stylesheet.
    (shadow ? parent : document.body).append(overlay);
    if (shadow) installUiStyles(parent as ShadowRoot);
    return { container, overlay };
  }

  /* ── views ──────────────────────────────────────────────────────────────────────────────────────────────── */

  private mountView(caller: string, parentScope: Scope, parentPanel: PanelRef | undefined, name: string, root: HTMLElement, props: Json, on: Record<string, (payload: Json) => void> = {}): Instance & { update(props: Json): void } {
    if (parentScope.disposed) throw failure("provider_unloaded");
    if (parentPanel) {
      const presentation = this.presentations.get(parentPanel.panelId);
      if (!presentation || presentation.scope.disposed || identity(presentation.ref) !== identity(parentPanel)) throw failure("target_gone");
    }
    this.reach(caller, name);
    const [owner, local] = name.split(".", 2);
    const live = this.modules.get(owner), factory = this.factories.get(name), spec = live?.definition.manifest.views?.[local];
    if (!live?.ready || !factory || !spec) throw failure("unavailable", `${name} is not available`);
    const propsSchema = withDefinitions(spec.props, live.definition);
    validateView(propsSchema, props);
    const scope = parentScope.child(`view:${name}`);
    const kernel = this;
    const ref: ViewRef = { get sessionId() { return kernel.sessionId; }, viewInstanceId: crypto.randomUUID(), mountId: crypto.randomUUID(), parentPanel };
    const { container, overlay } = this.container(root, live.definition.source === "installed");
    let style: HTMLStyleElement | undefined, placeholder: HTMLElement | undefined;
    let releasePlaceholder: (() => void | Promise<void>) | undefined;
    const unavailable = () => {
      if (parentScope.disposed || placeholder) return;
      placeholder = createViewUnavailable(root.shadowRoot ?? root);
      (root.shadowRoot ?? root).append(placeholder);
      releasePlaceholder = parentScope.track(() => { placeholder?.remove(); placeholder = undefined; releasePlaceholder = undefined; });
    };
    const emits = Object.fromEntries(Object.entries(spec.emits ?? {}).map(([event, schema]) => [event, withDefinitions(schema, live.definition)]));
    const ctx = this.context(live, scope, undefined, { ref, props: copy(props), on, emits }, overlay, unavailable);
    let instance: Instance;
    try {
      if (factory.styles) { style = document.createElement("style"); style.textContent = factory.styles; const target = container.getRootNode(); (target instanceof Document ? target.head : target).appendChild(style); }
      instance = factory.mount(container, ctx);
      if (!instance || typeof instance.dispose !== "function") throw failure("invalid_output", "Invalid view lifecycle");
    } catch (error) { this.reportFault?.(owner, error); this.disposeScope(scope, owner); style?.remove(); container.remove(); overlay.remove(); throw error; }
    const dispose = () => { void releasePlaceholder?.(); this.disposeScope(scope, owner); };
    this.views.set(ref.viewInstanceId, { owner, scope, parentPanel, prepareLeave: instance.prepareLeave?.bind(instance), dispose: () => { dispose(); unavailable(); } });
    scope.track(() => { this.views.delete(ref.viewInstanceId); try { instance.dispose(); } finally { style?.remove(); container.remove(); overlay.remove(); } });
    return { ...instance, dispose, update: next => {
      if (scope.disposed) throw failure("provider_unloaded");
      validateView(propsSchema, next);
      try { instance.update?.(copy(next)); }
      catch (error) { ctx.reportFault({ phase: "render", message: error instanceof Error ? error.message : String(error) }); throw error; }
    } };
  }

  /** The views of the last panel the user looked at report; a flush asks them to report now. */
  async flushViews(panelId?: string): Promise<void> {
    const selected = panelId ?? this.lastBusinessPanel?.panelId;
    if (selected && !this.panels.has(selected)) throw failure("target_gone", "The observed panel has closed");
    if (selected) for (const flush of this.viewFlushers.get(selected) ?? []) await flush();
  }

  /* ── a module's context ─────────────────────────────────────────────────────────────────────────────────── */

  /** What a module, one of its panels or one of its views is given. */
  private context(live: LiveModule, scope: Scope, panel?: PanelHost, view?: HostedView, overlayRoot?: HTMLElement, onFault?: (message: string) => void): ModuleContext {
    const owner = live.definition.id;
    const faultGate = createPanelRenderFaultGate();
    scope.track(() => faultGate.dispose());
    const ensureLive = () => { if (scope.disposed || !this.modules.has(owner)) throw failure("provider_unloaded"); };
    const parentRef = view?.ref.parentPanel;
    const parent = parentRef ? this.presentations.get(parentRef.panelId) : undefined;
    if (parentRef && (!parent || parent.scope.disposed || identity(parent.ref) !== identity(parentRef))) throw failure("target_gone");
    const active = panel?.active ?? parent?.active ?? signalValue(false), visible = panel?.visible ?? parent?.visible ?? signalValue(true);
    const safe = <T,>(callback: (value: T) => void) => (value: T) => {
      if (scope.disposed) return;
      try { callback(value); } catch (error) { ctx.reportFault({ phase: "render", message: error instanceof Error ? error.message : String(error) }); }
    };
    const ownedSignal = <T,>(source: { get(): T; watch(callback: (value: T) => void): () => void }) =>
      ({ get: () => source.get(), watch: (callback: (value: T) => void) => this.observe(scope, source.watch(safe(callback))) });
    const subscribe = async (name: string, params: Json, onItem: (item: Json) => void | Promise<void>, options: StreamOptions = {}) => {
      ensureLive(); this.reach(owner, name);
      if (!this.remote) throw failure("connection_lost");
      const subscription = await this.remote.subscribe(name, params, onItem, { ...options, module: owner });
      if (scope.disposed) { await subscription.close(); return subscription; }
      const release = scope.track(() => subscription.close());
      return { get id() { return subscription.id; }, close: async () => { await release(); } };
    };
    const ctx: ModuleContext = {
      moduleId: owner, panelRef: panel?.ref, viewRef: view?.ref,
      initialState: panel?.state ?? null, props: view?.props ?? null,
      workspace: this.workspace.context(owner, ownedSignal, release => this.observe(scope, release)),
      active: ownedSignal(active), visible: ownedSignal(visible), overlayRoot: overlayRoot ?? document.createElement("div"),
      view: this.viewApi(scope, panel, ensureLive),
      panel: panel ? {
        setTitle: title => { if (scope.disposed) return; if (typeof title !== "string") throw failure("invalid_input", "Panel title must be text"); panel.notifications.onTitleChange(title); },
        stateChanged: change => { if (scope.disposed) return; panel.notifications.onStateChange({ derived: change?.derived === true }); },
      } : undefined,
      reportFault: fault => { const message = faultGate.report(fault); if (message === null) return; this.reportFault?.(owner, message); onFault?.(message); this.disposeScope(scope, owner); },
      emit: (name, payload) => {
        if (!view) throw failure("invalid_input", "Only embedded views emit");
        const schema = view.emits[name];
        if (schema === undefined) throw failure("invalid_output", `Undeclared view event ${name}`, null, "failed");
        ensureLive();
        validateView(schema, payload, true);
        view.on[name]?.(copy(payload));
      },
      actions: {
        handle: (local, handler) => {
          ensureLive();
          if (!panel) throw failure("invalid_input", "Panel actions are handled by a panel");
          const name = `${owner}.${local}`, key = `${name}#${panel.ref.panelId}/${panel.ref.mountId}`;
          const declared = Object.values(live.definition.manifest.panels ?? {}).some(spec => spec.actions?.[local]);
          if (!declared) throw failure("invalid_input", `${name} is not a declared panel action`);
          if (this.panelActions.has(key)) throw failure("invalid_input", `${name} is handled twice`);
          this.panelActions.set(key, handler);
          scope.track(() => { this.panelActions.delete(key); });
        },
        call: async <T extends Json>(name: string, input: Json, options?: CallOptions) => { ensureLive(); return await this.call(owner, name, input, options) as T; },
      },
      streams: { subscribe },
      events: { subscribe: (name, callback, options) => subscribe(name, {}, callback, options) },
      views: {
        provide: (name, mount, options) => { if (!live.definition.manifest.views?.[name]) throw failure("invalid_input", `${owner}.${name} is not a declared view`); this.register(scope, `${owner}.${name}`, { mount, ...options }); },
        mount: (name, root, props, on) => this.mountView(owner, scope, panel?.ref ?? parentRef, name, root, props, on),
      },
      panels: { provide: (name, mount) => { if (!live.definition.manifest.panels?.[name]) throw failure("invalid_input", `${owner}.${name} is not a declared panel`); this.register(scope, `${owner}.${name}`, { mount }); } },
      status: { set: (id, value) => {
        ensureLive();
        if (!live.definition.manifest.provides?.["platform.status_item"]?.some(item => item.id === id)) throw failure("invalid_input", `Status item ${id} is not declared`);
        this.statusValues.set(`${owner}.${id}`, value); this.changed();
      } },
      routes: this.routesApi(owner, scope, ensureLive),
      platform: this.platformApi(owner, ensureLive),
      onDispose: release => { scope.track(release); },
      onBeforeStop: protect => {
        ensureLive();
        const guards = this.stopGuards.get(owner) ?? new Set();
        guards.add(protect); this.stopGuards.set(owner, guards);
        return this.observe(scope, () => { guards.delete(protect); if (!guards.size) this.stopGuards.delete(owner); });
      },
      setInterval: (callback, ms) => { const timer = setInterval(callback, ms); return this.observe(scope, () => clearInterval(timer)); },
    };
    return ctx;
  }
  /** ctx.view: the business view the user last looked at, and this panel's report of what it shows. */
  private viewApi(scope: Scope, panel: PanelHost | undefined, ensureLive: () => void): ModuleContext["view"] {
    return {
      current: (): BusinessView | null => {
        const ref = this.lastBusinessPanel;
        const current = ref ? this.panels.get(ref.panelId) : undefined;
        if (!current || !ref || identity(current.ref) !== identity(ref)) return null;
        const report = this.viewReports.get(ref.panelId);
        return { panel: { ...ref }, title: this.title(current), report: report ? copy(report) : null };
      },
      report: value => {
        ensureLive();
        if (!panel) throw failure("invalid_input", "View reports require a panel");
        if (value.summary.length > 2000 || JSON.stringify(value).length > 5800) throw failure("invalid_input", "View report exceeds its summary budget");
        this.viewReports.set(panel.ref.panelId, { ...copy(value), generation: value.generation ?? panel.ref.mountId });
      },
      onFlush: callback => {
        if (!panel) throw failure("invalid_input", "View flush belongs to a panel");
        let callbacks = this.viewFlushers.get(panel.ref.panelId);
        if (!callbacks) { callbacks = new Set(); this.viewFlushers.set(panel.ref.panelId, callbacks); }
        callbacks.add(callback);
        return this.observe(scope, () => { callbacks!.delete(callback); if (!callbacks!.size) this.viewFlushers.delete(panel.ref.panelId); });
      },
    };
  }
  /** ctx.routes: the module's private HTTP routes and websockets, for this window's session and workspace. */
  private routesApi(owner: string, scope: Scope, ensureLive: () => void): ModuleContext["routes"] {
    const url = (path: string) => {
      if (path.includes("\\") || path.startsWith("//") || /^[a-z]+:/i.test(path) || path.split(/[/?#]/).some(part => decodeURIComponent(part) === ".."))
        throw failure("permission_denied", "Private path must stay inside the module");
      return `/m/${owner}/${path.replace(/^\//, "")}`;
    };
    return {
      fetch: async (path, init = {}) => {
        ensureLive();
        const headers = new Headers(init.headers);
        headers.set("x-talyx-session", this.sessionId);
        const shown = this.workspace.identity.get()?.workspaceId;
        if (shown) headers.set("x-talyx-workspace", shown);
        // Stopping the module (or closing the view or panel) aborts the request, its answer included: the caller
        // reads the body after this returns, so the request counts as finished once the body has been read.
        const controller = new AbortController(), abort = () => controller.abort();
        let finished = false;
        const release = scope.track(() => { if (!finished) abort(); });
        const finish = () => { finished = true; init.signal?.removeEventListener("abort", abort); void release(); };
        init.signal?.addEventListener("abort", abort, { once: true });
        if (init.signal?.aborted) abort();
        let response: Response;
        try { response = await fetch(url(path), { ...init, headers, signal: controller.signal }); }
        catch (error) {
          finish();
          if (error instanceof TypeError) throw failure("connection_lost", undefined, undefined, "unknown");
          throw error;
        }
        if (!response.body) { finish(); return response; }
        return new Response(response.body.pipeThrough(new TransformStream({ flush: finish })),
          { status: response.status, statusText: response.statusText, headers: response.headers });
      },
      websocket: path => {
        ensureLive();
        const address = new URL(url(path), location.href); address.protocol = location.protocol === "https:" ? "wss:" : "ws:";
        // The window's session and workspace ride as `talyx-` parameters, which the Host takes off; every other
        // parameter is the module's and reaches its route untouched. So a module cannot name one `talyx-...`.
        if ([...address.searchParams.keys()].some(name => name.startsWith("talyx-")))
          throw failure("invalid_input", "A module's socket parameters cannot start with talyx- (the window's own)");
        address.searchParams.set("talyx-session", this.sessionId);
        const shown = this.workspace.identity.get()?.workspaceId;
        if (shown) address.searchParams.set("talyx-workspace", shown);
        const socket = new WebSocket(address); scope.track(() => socket.close()); return socket;
      },
    };
  }
  /** ctx.platform: the window's panels and the settings window. */
  private platformApi(owner: string, ensureLive: () => void): ModuleContext["platform"] {
    return {
      panels: {
        open: async input => { ensureLive(); return this.openPanel(owner, input); },
        openTarget: async target => { ensureLive(); return this.openTarget(target); },
        list: type => [...this.panels.values()].filter(p => !type || p.type === type).map(p => p.ref),
        active: type => [...this.panels.values()].find(p => p.active.get() && (!type || p.type === type))?.ref ?? null,
        setStatus: (ref, value) => {
          ensureLive();
          if (this.panel(ref).type.split(".", 1)[0] !== owner) throw failure("permission_denied", "Panel status belongs to its module");
          if (!["idle", "running", "attention"].includes(value.state) || typeof value.text !== "string") throw failure("invalid_input", "Invalid panel status");
          this.panelStatuses.set(ref.panelId, copy(value)); this.changed();
        },
        activate: ref => { this.panel(ref); this.shell?.activate(ref); },
        close: async ref => { this.panel(ref); await this.shell?.close(ref); },
      },
      settings: { open: section => { this.shell?.settings(section); } },
    };
  }
}

