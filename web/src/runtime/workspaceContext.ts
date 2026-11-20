import { signalValue, type Json, type ModuleWorkspaceContext, type WorkspaceIdentity, type WorkspaceSaved } from "@talyx/sdk";

const copy = <T,>(value: T): T => JSON.parse(JSON.stringify(value)) as T;
type SignalSource<T> = { get(): T; watch(callback: (value: T) => void): () => void };

/** The open workspace this window shows, and the modules' hooks run after it is saved. */
export class WorkspaceContextRuntime {
  readonly identity = signalValue<WorkspaceIdentity | null>(null);
  private savedHooks = new Map<string, Set<(saved: WorkspaceSaved) => Promise<void>>>();

  bind(identity: WorkspaceIdentity): void { this.identity.set(copy(identity)); }
  onSaved(owner: string, hook: (saved: WorkspaceSaved) => Promise<void>): () => void {
    let hooks = this.savedHooks.get(owner);
    if (!hooks) { hooks = new Set(); this.savedHooks.set(owner, hooks); }
    hooks.add(hook);
    return () => { hooks!.delete(hook); };
  }
  /** After a Save each module persists what it captured: each of its panels' own state, as its captureState gave it
      (the saved panels are `module:<owner>` envelopes around that state). */
  async saved(input: { workspaceId: string; snapshotId: string; panels: ReadonlyArray<{ kind: string; state: unknown }> }): Promise<void> {
    for (const [owner, hooks] of this.savedHooks) {
      const panels = input.panels.filter(panel => panel.kind === `module:${owner}`).map(panel => copy((panel.state as { state: Json }).state));
      for (const hook of hooks) await hook({ workspaceId: input.workspaceId, snapshotId: input.snapshotId, panels });
    }
  }
  context(owner: string, own: <T>(source: SignalSource<T>) => SignalSource<T>, observe: (release: () => void) => () => void): ModuleWorkspaceContext {
    return {
      identity: own({ get: () => copy(this.identity.get()), watch: callback => this.identity.watch(value => callback(copy(value))) }),
      onSaved: hook => observe(this.onSaved(owner, hook)),
    };
  }
}
