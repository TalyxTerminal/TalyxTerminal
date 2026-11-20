export class Scope {
  readonly children = new Set<Scope>();
  private releases: (() => void | Promise<void>)[] = [];
  private closing: Promise<void> | null = null;
  disposed = false;
  constructor(readonly name: string, readonly parent?: Scope) {
    if (parent?.disposed) throw new Error("scope has been disposed");
    parent?.children.add(this);
  }
  child(name: string): Scope { return new Scope(name, this); }
  track(release: () => void | Promise<void>): () => void | Promise<void> {
    if (this.disposed) { void release(); return () => undefined; }
    let live = true;
    const once = () => { if (!live) return; live = false; const index = this.releases.indexOf(once); if (index >= 0) this.releases.splice(index, 1); return release(); };
    this.releases.push(once);
    return once;
  }
  dispose(): Promise<void> {
    if (this.closing) return this.closing;
    this.disposed = true;
    this.closing = (async () => {
      const failures: unknown[] = [];
      for (const child of [...this.children].reverse()) {
        try { await child.dispose(); } catch (e) { failures.push(e); }
      }
      for (const release of [...this.releases].reverse()) {
        try { await release(); } catch (e) { failures.push(e); }
      }
      this.releases = []; this.children.clear(); this.parent?.children.delete(this);
      if (failures.length) throw new AggregateError(failures, `scope ${this.name} cleanup failed`);
    })();
    return this.closing;
  }
  count(): number { return this.releases.length + [...this.children].reduce((n, child) => n + child.count(), 0); }
}
export function signalValue<T>(initial: T) {
  let value = initial;
  const listeners = new Set<(value: T) => void>();
  return {
    get: () => value,
    set: (next: T) => { value = next; for (const listener of listeners) listener(value); },
    watch: (listener: (value: T) => void) => { listeners.add(listener); listener(value); return () => { listeners.delete(listener); }; },
    clear: () => { listeners.clear(); },
  };
}
