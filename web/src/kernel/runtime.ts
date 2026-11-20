/* The window's platform runtime: the Host connection, the module registry, and every module's frontend, loaded the
   same way whoever made the module: from the address its declaration gives. */
import { failure, Platform, type CallOptions, type Json, type ModuleContext, type ModuleDefinition, type Subscription } from "@talyx/sdk";
import { FrontendKernel } from "./registry";
import { BridgeClient } from "./bridge";

/** The platform's actions as the window itself calls them (the user, in the workspace this window shows). */
export type PlatformClient = ReturnType<typeof Platform.createClient>;

/** Whether a module's frontend should run here: it is enabled, its backend did not fail, and it has a frontend. */
const runs = (definition: ModuleDefinition) => definition.id !== "platform" && definition.enabled && definition.state !== "failed"
  && definition.state !== "stopped" && Boolean(definition.manifest.entries.frontend);

export class PlatformRuntime {
  readonly kernel = new FrontendKernel();
  readonly bridge = new BridgeClient(this.kernel);
  readonly platform: PlatformClient = Platform.createClient({ actions: { call: <T extends Json>(name: string, input: Json, options?: CallOptions) => this.kernel.call("platform", name, input, options) as Promise<T> } });
  /** Why a module's frontend could not run here, by module. */
  readonly faults = new Map<string, string>();
  unreadable: Platform.ModuleState[] = [];
  private loaded = new Map<string, string>();
  private refreshTail = Promise.resolve();
  private changes?: Subscription;
  constructor() {
    this.kernel.reportFault = (owner, error) => this.reportFault(owner, error);
    this.bridge.windowActions = {
      prepare_module_stop: async input => {
        const { id, reason } = input as { id: string; reason: string };
        await this.kernel.prepareStop(id, reason);
        return {};
      },
      open_panel: async (input, caller) => ({ panelId: (await this.kernel.openPanel(caller.module ?? "platform", input as { type: string; state?: Json })).panelId }),
      open_target: async input => ({ panelId: (await this.kernel.openTarget(input as { module: string; panelType: string; objectId: string })).panelId }),
      list_panels: () => ({ panels: this.kernel.listPanels() as unknown as Json }),
      capture_panel: async (input, caller) => {
        const panel = this.panelRef(input);
        const desktop = window.top?.talyxDesktop;
        if (!desktop || !caller.workspaceId) throw failure("unavailable", "Open the workspace in the desktop window to capture its panel.");
        try { return await desktop.capturePanel(caller.workspaceId, panel.panelId); }
        catch (error) {
          // The desktop names the cases the user can fix (host/desktop/capture.py); anything else is a fault.
          if (error instanceof Error && error.name === "PanelNotCapturable") throw failure("unavailable", error.message);
          console.error("Panel capture failed", error);
          throw error;
        }
      },
      activate_panel: input => { this.kernel.shell!.activate(this.panelRef(input)); return {}; },
      close_panel: async input => { await this.kernel.shell!.close(this.panelRef(input)); return {}; },
    };
  }
  private panelRef(input: Json) {
    const panel = this.kernel.panels.get((input as { panelId: string }).panelId);
    if (!panel) throw failure("target_gone", "The panel is not open");
    return panel.ref;
  }
  reportFault(owner: string, error: unknown): void {
    console.warn("Module unavailable", owner, error);
    this.faults.set(owner, error instanceof Error ? error.message : String((error as { message?: unknown })?.message ?? error));
    this.kernel.change.set(this.kernel.change.get() + 1);
  }
  /** Reads the modules again and brings the frontends running here in line: new ones start, a module whose code
      changed (another digest) starts again, a stopped or removed one stops. */
  async refresh(): Promise<void> {
    this.refreshTail = this.refreshTail.catch(() => undefined).then(async () => {
      const [declared, listed] = await Promise.all([this.platform["modules.definitions"]({}), this.platform["modules.list"]({})]);
      const definitions = declared as unknown as ModuleDefinition[];
      const ids = new Set(definitions.map(item => item.id));
      this.unreadable = listed.filter(item => item.state === "failed" && !ids.has(item.id));
      const wanted = new Map(definitions.filter(runs).map(definition => [definition.id, definition]));
      for (const owner of [...this.kernel.modules.keys()]) if (wanted.get(owner)?.digest !== this.loaded.get(owner)) {
        await this.kernel.disable(owner);
        this.loaded.delete(owner);
      }
      for (const owner of [...this.faults.keys()]) if (!wanted.has(owner) || wanted.get(owner)!.digest !== this.loaded.get(owner)) this.faults.delete(owner);
      this.kernel.setDefinitions(definitions);
      for (const definition of wanted.values()) {
        if (this.kernel.modules.has(definition.id) || this.loaded.get(definition.id) === definition.digest) continue;
        this.loaded.set(definition.id, definition.digest);
        try {
          const entry = await import(/* @vite-ignore */ `${definition.assetBase}${definition.manifest.entries.frontend}?digest=${definition.digest}`) as { activate?: (ctx: ModuleContext) => unknown };
          if (typeof entry.activate !== "function") throw failure("invalid_input", "Its frontend must export activate(ctx)");
          await this.kernel.activate(definition, entry.activate);
        } catch (error) { this.reportFault(definition.id, error); }
      }
      this.changes ??= await this.bridge.subscribe("platform.modules.changes", {}, async () => {
        try { await this.refresh(); }
        catch (error) { if ((error as { code?: string }).code !== "connection_lost") this.reportFault("platform", error); }
      });
    });
    return this.refreshTail;
  }
  async ready(): Promise<void> { this.bridge.open(); await this.refresh(); }
  /** Starts a module again: its process when that failed or it is disabled, then its frontend here. */
  async retry(owner: string): Promise<void> {
    const definition = this.kernel.definitions.get(owner);
    if (definition && !definition.enabled) await this.platform["modules.enable"]({ id: owner });
    else if (!definition || definition.state === "failed") await this.platform["modules.reload"]({ id: owner });
    if (this.kernel.modules.has(owner)) await this.kernel.disable(owner);
    this.loaded.delete(owner);
    await this.refresh();
  }
}
let runtime: PlatformRuntime | undefined;
export function platformRuntime(): PlatformRuntime { return runtime ??= new PlatformRuntime(); }
