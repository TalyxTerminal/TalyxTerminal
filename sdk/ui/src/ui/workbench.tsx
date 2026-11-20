/* Workbench layout parts: one toolbar row, a resizable inspector beside the
   main view and property-row sections. Modules compose these instead of hand-building layout. */
import { useRef, useState, type ReactNode } from "react";
import { TxIcon } from "./icons";
import { TxFieldLayout, type TxDensity } from "./components";

function join(...values: (string | undefined | false)[]): string {
  return values.filter(Boolean).join(" ");
}

/** The single action row on top of a workbench, or a vertical rail of tools beside a view. */
export function TxToolbar({ label, children, className, density, orientation = "horizontal" }: {
  label: string; children: ReactNode; className?: string;
  /** Toolbars over charts are usually compact. */
  density?: TxDensity;
  orientation?: "horizontal" | "vertical";
}) {
  return <div className={join("tx-toolbar", className)} role="toolbar" aria-label={label} aria-orientation={orientation}
    data-orientation={orientation} data-tx-density={density}>{children}</div>;
}

export function TxToolbarDivider() {
  return <span className="tx-toolbar-sep" aria-hidden="true" />;
}

/** Side pane plus main view with a draggable divider. Width is owned by the
    caller so it can be remembered with the panel state. */
export function TxSplit({ side, children, width, onWidth, min = 280, max = 840, mainMin = 420, sideLabel, sideHidden = false, sidePosition = "start" }: {
  side: ReactNode; children: ReactNode; width: number; onWidth(width: number): void;
  min?: number; max?: number; mainMin?: number; sideLabel: string; sideHidden?: boolean;
  sidePosition?: "start" | "end";
}) {
  const container = useRef<HTMLDivElement>(null);
  const clamp = (value: number) => {
    const total = container.current?.getBoundingClientRect().width ?? Infinity;
    return Math.round(Math.max(min, Math.min(max, total - mainMin, value)));
  };
  return <div className="tx-split" ref={container} data-side={sidePosition}>
    {!sideHidden && <>
      <aside className="tx-split-side" aria-label={sideLabel} style={{ width }}>{side}</aside>
      <div
        className="tx-split-divider" role="separator" tabIndex={0} aria-label={`${sideLabel} width`} aria-orientation="vertical"
        aria-valuemin={min} aria-valuemax={max} aria-valuenow={width}
        onPointerDown={event => { event.preventDefault(); event.currentTarget.setPointerCapture(event.pointerId); }}
        onPointerMove={event => {
          if (!event.currentTarget.hasPointerCapture(event.pointerId) || !container.current) return;
          const bounds = container.current.getBoundingClientRect();
          onWidth(clamp(sidePosition === "end" ? bounds.right - event.clientX : event.clientX - bounds.left));
        }}
        onPointerUp={event => event.currentTarget.releasePointerCapture(event.pointerId)}
        onKeyDown={event => {
          if (!["ArrowLeft", "ArrowRight"].includes(event.key)) return;
          event.preventDefault();
          onWidth(clamp(width + (event.key === "ArrowRight" ? 24 : -24) * (sidePosition === "end" ? -1 : 1)));
        }}
      />
    </>}
    <div className="tx-split-main">{children}</div>
  </div>;
}

/** Inspector column: optional header, scrolling property sections, footer.
    Fields inside render as property rows. */
export function TxInspector({ header, children, footer, layout = "inline" }: {
  header?: ReactNode; children: ReactNode; footer?: ReactNode; layout?: "inline" | "stack";
}) {
  return <div className="tx-inspector">
    {header && <div className="tx-inspector-head">{header}</div>}
    <div className="tx-inspector-body"><TxFieldLayout layout={layout}>{children}</TxFieldLayout></div>
    {footer && <div className="tx-inspector-foot">{footer}</div>}
  </div>;
}

/** A titled group of settings. Only the "Advanced" group collapses; every
    other group stays open so what decides a result is always visible. */
export function TxSection({ title, actions, children, collapsible = false, defaultOpen = false, className, layout }: {
  title?: ReactNode; actions?: ReactNode; children: ReactNode; collapsible?: boolean; defaultOpen?: boolean; className?: string;
  layout?: "inline" | "stack";
}) {
  const [open, setOpen] = useState(defaultOpen);
  const body = <div className="tx-section-body">{layout ? <TxFieldLayout layout={layout}>{children}</TxFieldLayout> : children}</div>;
  if (collapsible) return <section className={join("tx-section", className)} data-collapsible="">
    <button type="button" className="tx-section-head tx-section-toggle" aria-expanded={open} onClick={() => setOpen(!open)}>
      <TxIcon name="chevron" /><span className="tx-section-title">{title}</span>
    </button>
    {open && body}
  </section>;
  return <section className={join("tx-section", className)}>
    {(title || actions) && <div className="tx-section-head"><span className="tx-section-title">{title}</span><span className="tx-spacer" />{actions}</div>}
    {body}
  </section>;
}

