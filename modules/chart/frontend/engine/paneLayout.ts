/** Pane rows are resized by the chart engine independently of the chart's outer element. */
export class PaneLayout {
  private rows = new Map<string, HTMLElement>();
  private readonly observer = new ResizeObserver(() => this.changed(this.positions()));

  constructor(private readonly element: HTMLElement, private readonly changed: (tops: Map<string, number>) => void) {}

  set(rows: Map<string, HTMLElement>): void {
    if (rows.size === this.rows.size && [...rows].every(([id, row]) => this.rows.get(id) === row)) return;
    this.observer.disconnect();
    this.rows = rows;
    for (const row of rows.values()) this.observer.observe(row);
    this.changed(this.positions());
  }

  private positions(): Map<string, number> {
    const top = this.element.getBoundingClientRect().top;
    return new Map([...this.rows].map(([id, row]) => [id, row.getBoundingClientRect().top - top]));
  }

  dispose(): void { this.observer.disconnect(); }
}
