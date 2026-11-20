/* TALYX platform components.
   Modules get these from `@talyx/sdk`; they must not hand-roll buttons, inputs,
   popups or tables. Behaviour: settings
   apply immediately, undo replaces confirmation where possible, errors surface
   where they happen. */
import {
  cloneElement, createContext, forwardRef, Fragment, isValidElement, useContext, useEffect, useId,
  useMemo, useRef, useState,
  type ButtonHTMLAttributes, type HTMLAttributes, type InputHTMLAttributes, type ReactElement, type ReactNode,
  type SelectHTMLAttributes, type TableHTMLAttributes, type TdHTMLAttributes, type ThHTMLAttributes,
  type KeyboardEvent, type TextareaHTMLAttributes, type CSSProperties,
} from "react";
import { createPortal } from "react-dom";
import * as CheckboxPrimitive from "@radix-ui/react-checkbox";
import * as DialogPrimitive from "@radix-ui/react-dialog";
import * as DropdownMenuPrimitive from "@radix-ui/react-dropdown-menu";
import * as PopoverPrimitive from "@radix-ui/react-popover";
import * as SwitchPrimitive from "@radix-ui/react-switch";
import * as TabsPrimitive from "@radix-ui/react-tabs";
import * as ToastPrimitive from "@radix-ui/react-toast";
import * as TooltipPrimitive from "@radix-ui/react-tooltip";
import { TxIcon } from "./icons";
import { installUiStyles } from "./inject";
import { findOverlayRoot, TxOverlayProvider, useOverlayContainer, useModalContainer } from "./overlay";
import { useDialogFocus } from "./focus";
import { tokenValue } from "./theme";
import { hexOpacity, withHexOpacity } from "./colorValue";

type Tone = "off" | "busy" | "done" | "review" | "failed";

function join(...values: (string | undefined | false)[]): string {
  return values.filter(Boolean).join(" ");
}

function usePortalTarget(): HTMLElement | undefined {
  return useOverlayContainer() ?? undefined;
}

/* ── Buttons ─────────────────────────────────────────────────── */

/** Sizes for a container and everything in it: compact for toolbars and dense tables,
    comfortable for reading (a conversation), standard everywhere else. */
export type TxDensity = "compact" | "standard" | "comfortable";

export interface TxButtonProps extends ButtonHTMLAttributes<HTMLButtonElement> {
  /** `tool` is a frameless toolbar button (a search, a timeframe menu, a menu of overlays). `row` is a whole clickable line of a list
      (a file in a tree, a problem): full width, left-aligned, its content laid out by the caller, grey while it is the
      current or chosen one (`aria-current`, `aria-selected`, `aria-pressed`, `aria-checked`). `tool-primary` is the one main
      action of a compact toolbar (Get data): the `tool` shape, filled orange. */
  variant?: "default" | "primary" | "quiet" | "tool" | "tool-primary" | "row";
  size?: "md" | "sm";
  /** The action is under way: a spinner leads the label and the button takes no clicks. */
  loading?: boolean;
}

export const TxButton = forwardRef<HTMLButtonElement, TxButtonProps>(function TxButton(
  { variant = "default", size = "md", loading, className, type = "button", children, ...rest }, ref,
) {
  return <button ref={ref} type={type} className={join("tx-btn", className)} data-variant={variant} data-size={size}
    {...rest} {...(loading ? { disabled: true, "aria-busy": true } : {})}>{loading && <span className="tx-spin" aria-hidden="true" />}{children}</button>;
});

export interface TxIconButtonProps extends ButtonHTMLAttributes<HTMLButtonElement> {
  label: string;
  icon: Parameters<typeof TxIcon>[0]["name"];
  size?: "md" | "sm";
  tone?: "default" | "active";
  /** `tool` is a frameless square for toolbars and drawing rails (the chosen tool turns orange);
      `outline` stands among buttons with the same frame. */
  variant?: "default" | "tool" | "outline";
}

export const TxIconButton = forwardRef<HTMLButtonElement, TxIconButtonProps>(function TxIconButton(
  { label, icon, size = "md", tone = "default", variant = "default", className, type = "button", ...rest }, ref,
) {
  return <button
    ref={ref} type={type} aria-label={label} title={label} className={join("tx-icon-btn", className)}
    data-size={size} data-tone={tone} data-variant={variant} {...rest}
  ><TxIcon name={icon} /></button>;
});

/* ── Inputs ─────────────────────────────────────────────────── */

export interface TxInputProps extends InputHTMLAttributes<HTMLInputElement> {
  numeric?: boolean;
  suffix?: string;
}

export const TxInput = forwardRef<HTMLInputElement, TxInputProps>(function TxInput(
  { numeric, suffix, className, ...rest }, ref,
) {
  const input = <input ref={ref} className={join("tx-input", className)} data-numeric={numeric ? "true" : undefined} {...rest} />;
  return suffix ? <div className="tx-affix">{input}<span>{suffix}</span></div> : input;
});

export const TxTextarea = forwardRef<HTMLTextAreaElement, TextareaHTMLAttributes<HTMLTextAreaElement>>(function TxTextarea(
  { className, ...rest }, ref,
) {
  return <textarea ref={ref} className={join("tx-input", "tx-textarea", className)} {...rest} />;
});

export const TxSelect = forwardRef<HTMLSelectElement, SelectHTMLAttributes<HTMLSelectElement>>(function TxSelect(
  { className, children, ...rest }, ref,
) {
  return <select ref={ref} className={join("tx-select", className)} {...rest}>{children}</select>;
});

export interface TxColorInputProps {
  value: string;
  onValueChange(value: string): void;
  "aria-label": string;
  disabled?: boolean;
  size?: "md" | "sm";
  className?: string;
}
const colorPalette = Array.from({ length: 16 }, (_, index) => tokenValue(`--tx-user-palette-${index + 1}`).toUpperCase());
/** The gap between a trigger and what it opens, read once: a menu or tip read it on every render, which made the browser work out the styles of the whole page mid-render. */
let gap: number | undefined;
const floatGap = () => gap ??= parseFloat(tokenValue("--tx-space-1-half"));
export function TxColorInput({ value, onValueChange, "aria-label": label, disabled, size = "sm", className }: TxColorInputProps) {
  const container = usePortalTarget();
  const content = useRef<HTMLDivElement>(null);
  const [open, setOpen] = useState(false);
  const [draft, setDraft] = useState(value);
  const valid = /^#(?:[a-fA-F\d]{3}|[a-fA-F\d]{4}|[a-fA-F\d]{6}|[a-fA-F\d]{8})$/.test(draft);
  useEffect(() => { if (!open) setDraft(value); }, [open, value]);
  return <PopoverPrimitive.Root open={open} onOpenChange={setOpen}>
    <PopoverPrimitive.Trigger asChild><TxButton disabled={disabled} size={size} className={join("tx-color-trigger", className)} aria-label={label} title={`${label}: ${value}`}>
      <span className="tx-color-swatch" style={{ "--tx-user-color": value } as CSSProperties} /><TxIcon name="chevron" />
    </TxButton></PopoverPrimitive.Trigger>
    <PopoverPrimitive.Portal container={container}><PopoverPrimitive.Content ref={content} className="tx-root tx-color-picker tx-glass" align="start" sideOffset={4} aria-label={label}
      onOpenAutoFocus={event => { event.preventDefault(); content.current?.querySelector<HTMLButtonElement>("button[aria-pressed='true'],button")?.focus(); }}>
      <div className="tx-color-palette" role="group" aria-label={`${label} palette`}>{colorPalette.map(color => <TxButton key={color} aria-label={`Choose ${color}`} aria-pressed={value.toUpperCase() === color} className="tx-color-choice" title={color}
        onClick={() => { onValueChange(color); setDraft(color); }}><span className="tx-color-swatch" style={{ "--tx-user-color": color } as CSSProperties} /></TxButton>)}</div>
      <TxField label="Hex color" error={valid ? undefined : "Enter a valid hex color."}><TxInput aria-label={`${label} hex color`} value={draft} spellCheck={false}
        onChange={event => { const next = event.target.value; setDraft(next); if (/^#(?:[a-fA-F\d]{3}|[a-fA-F\d]{4}|[a-fA-F\d]{6}|[a-fA-F\d]{8})$/.test(next)) onValueChange(next); }} /></TxField>
      <TxField label="Opacity"><TxInput numeric type="number" min={0} max={100} step={1} suffix="%" aria-label={`${label} opacity`} value={hexOpacity(value)}
        onChange={event => { if (event.target.value === "") return; const next = withHexOpacity(value, Number(event.target.value)); onValueChange(next); setDraft(next); }} /></TxField>
    </PopoverPrimitive.Content></PopoverPrimitive.Portal>
  </PopoverPrimitive.Root>;
}

export interface TxMultiSelectProps {
  label: string;
  options: readonly { value: string; label: string; disabled?: boolean }[];
  value: readonly string[];
  onValueChange(value: string[]): void;
  placeholder?: string;
  searchPlaceholder?: string;
  disabled?: boolean;
  className?: string;
}

export type TxOverlaySize = "compact" | "standard" | "wide";

export interface TxPopoverProps {
  open: boolean;
  onOpenChange(open: boolean): void;
  trigger?: ReactNode;
  /** Anchor an existing control when its lazy content mounts separately. */
  anchor?: HTMLElement | null;
  children: ReactNode;
  label: string;
  side?: "right" | "bottom";
  align?: "start" | "end";
  className?: string;
  /** Shared dimensions for floating forms and readouts; menus keep their content width. */
  size?: TxOverlaySize;
}
/** An interactive picker, including search and per-choice actions. Radix owns dismissal and portal placement. */
export function TxPopover({ open, onOpenChange, trigger, anchor, children, label, side = "bottom", align = "start", className, size }: TxPopoverProps) {
  const container = usePortalTarget();
  const focus = useDialogFocus();
  return <PopoverPrimitive.Root open={open} onOpenChange={onOpenChange}>
    {anchor ? <PopoverPrimitive.Anchor virtualRef={{ current: anchor }} /> : <PopoverPrimitive.Trigger asChild>{trigger}</PopoverPrimitive.Trigger>}
    <PopoverPrimitive.Portal container={container}><PopoverPrimitive.Content {...focus} className={join("tx-root tx-popover tx-glass", className)}
      aria-label={label} data-size={size} side={side} align={align} sideOffset={4} tabIndex={-1}
      onCloseAutoFocus={event => { if (anchor) { event.preventDefault(); anchor.focus(); } else focus.onCloseAutoFocus?.(event); }}>{children}</PopoverPrimitive.Content></PopoverPrimitive.Portal>
  </PopoverPrimitive.Root>;
}

export interface TxFieldProps {
  label: string;
  hint?: string;
  error?: string;
  children: ReactNode;
}

/** Label + control + one line of help or error, the only place either belongs.
    The id is handed to the single child so clicking the label focuses the
    control and assistive technology reads the right name. */
/** Inspector panels lay fields out as property rows (label left, control
    right); dialogs and wide forms keep the stacked layout. */
const FieldLayoutContext = createContext<"stack" | "inline">("stack");
export function TxFieldLayout({ layout, children }: { layout: "stack" | "inline"; children: ReactNode }) {
  return <FieldLayoutContext.Provider value={layout}>{children}</FieldLayoutContext.Provider>;
}

export function TxField({ label, hint, error, children, layout }: TxFieldProps & { layout?: "stack" | "inline" }) {
  const inherited = useContext(FieldLayoutContext);
  const generated = useId();
  const id = isValidElement(children) ? children.props.id ?? generated : generated;
  const control = isValidElement(children)
    ? cloneElement(children as ReactElement<{ id?: string; "aria-describedby"?: string; "aria-invalid"?: boolean }>, {
      id, "aria-describedby": hint || error ? `${id}-help` : undefined, "aria-invalid": error ? true : undefined,
    })
    : children;
  return <div className="tx-field" data-layout={layout ?? inherited}>
    <label htmlFor={id}>{label}</label>
    {control}
    {error ? <span id={`${id}-help`} className="tx-error" role="alert">{error}</span> : hint ? <span id={`${id}-help`} className="tx-hint">{hint}</span> : null}
  </div>;
}

export interface TxSearchProps extends Omit<InputHTMLAttributes<HTMLInputElement>, "onChange" | "value"> {
  value: string;
  onValueChange: (value: string) => void;
}

export function TxSearch({ value, onValueChange, placeholder = "Search", className, ...rest }: TxSearchProps) {
  return <div className={join("tx-search", className)}>
    <TxIcon name="search" />
    <input
      className="tx-input" type="search" value={value} placeholder={placeholder}
      onChange={event => onValueChange(event.target.value)} {...rest}
    />
    {value && <TxIconButton className="tx-search-clear" label="Clear search" icon="close" size="sm" onClick={() => onValueChange("")} />}
  </div>;
}

/* ── Selection ─────────────────────────────────────────────────── */

export interface TxCheckboxProps {
  label?: ReactNode;
  checked: boolean | "indeterminate";
  onCheckedChange: (checked: boolean) => void;
  disabled?: boolean;
  name?: string;
}

export function TxCheckbox({ label, checked, onCheckedChange, disabled, name }: TxCheckboxProps) {
  const control = <CheckboxPrimitive.Root
    className="tx-check-box" checked={checked} disabled={disabled} name={name} aria-label={!label ? name : undefined}
    onCheckedChange={value => onCheckedChange(value === true)}
  ><CheckboxPrimitive.Indicator /></CheckboxPrimitive.Root>;
  if (!label) return control;
  return <label className="tx-check">{control}<span>{label}</span></label>;
}

export interface TxSwitchProps {
  label: string;
  checked: boolean;
  onCheckedChange: (checked: boolean) => void;
  disabled?: boolean;
  /** Hide the visible label when a surrounding row already says it. */
  hideLabel?: boolean;
}

export function TxSwitch({ label, checked, onCheckedChange, disabled, hideLabel }: TxSwitchProps) {
  const id = useId();
  const control = <SwitchPrimitive.Root
    id={id} className="tx-switch" checked={checked} disabled={disabled}
    // The visible label names the control in both layouts; `aria-label` only
    // carries it when there is no label element to associate.
    aria-labelledby={hideLabel ? undefined : `${id}-label`}
    aria-label={hideLabel ? label : undefined}
    onCheckedChange={onCheckedChange}
  />;
  if (hideLabel) return control;
  return <span className="tx-switch-label">
    {control}<label id={`${id}-label`} htmlFor={id}>{label}</label>
  </span>;
}

export interface TxSegmentedProps<T extends string> {
  value: T;
  options: { value: T; label: string; disabled?: boolean }[];
  onChange: (value: T) => void;
  label?: string;
  size?: "md" | "sm";
  /** `tool` lays the choices flush in a toolbar (timeframes); the chosen one turns orange. */
  variant?: "pill" | "tool";
}

export function TxSegmented<T extends string>({ value, options, onChange, label, size = "md", variant = "pill" }: TxSegmentedProps<T>) {
  return <div className="tx-seg" role="group" aria-label={label} data-size={size} data-variant={variant}>
    {options.map(option => <button
      key={option.value} type="button" aria-pressed={option.value === value} disabled={option.disabled}
      onClick={() => onChange(option.value)}
    >{option.label}</button>)}
  </div>;
}

/** The small orange dot of a tab's `marked`. A string names it for screen readers ("Has marks"); `true` reads as "New". */
function TxMark({ marked }: { marked?: boolean | string }) {
  return marked ? <span className="tx-tab-mark" role="img" aria-label={typeof marked === "string" ? marked : "New"} /> : null;
}

export interface TxTabsProps<T extends string> {
  value: T;
  /** `count` is shown after the label in mono, such as the number of unread items. `dirty` marks a document with unsaved
      changes (a grey dot; on closable tabs it sits where the close button shows). `marked` is a small orange dot: something
      waits behind that tab. `title` is the hover text, such as a file's full path. */
  options: { value: T; label: string; count?: number; disabled?: boolean; dirty?: boolean; marked?: boolean | string; title?: string }[];
  onChange: (value: T) => void;
  /** Open documents rather than fixed views: each tab has a close button, shown on the current tab and under the pointer. */
  closable?: boolean;
  onClose?: (value: T) => void;
  label?: string;
}

export function TxTabs<T extends string>({ value, options, onChange, closable = false, onClose, label }: TxTabsProps<T>) {
  const trigger = (option: TxTabsProps<T>["options"][number]) => <TabsPrimitive.Trigger key={option.value} value={option.value} data-value={option.value}
    disabled={option.disabled} title={option.title}>
    {option.label}{option.count !== undefined && <span className="tx-tab-count">{option.count}</span>}<TxMark marked={option.marked} />
    {!closable && option.dirty && <span className="tx-tab-dirty" role="img" aria-label="Unsaved changes" />}
  </TabsPrimitive.Trigger>;
  return <TabsPrimitive.Root value={value} onValueChange={next => onChange(next as T)}>
    <TabsPrimitive.List className="tx-tabs" aria-label={label} data-closable={closable ? "" : undefined}>
      {closable ? options.map(option => <span key={option.value} className="tx-tab" data-state={option.value === value ? "active" : "inactive"} data-dirty={option.dirty ? "" : undefined}>
        {trigger(option)}
        <span className="tx-tab-end">
          {option.dirty && <span className="tx-tab-dirty" role="img" aria-label="Unsaved changes" />}
          <TxIconButton className="tx-tab-close" size="sm" icon="close" label={`Close ${option.label}`} onClick={() => onClose?.(option.value)} />
        </span>
      </span>) : options.map(trigger)}
    </TabsPrimitive.List>
  </TabsPrimitive.Root>;
}

/* ── Status ─────────────────────────────────────────────────── */

const toneLabels: Record<Tone, string> = {
  off: "Off", busy: "Running", done: "Done", review: "Needs review", failed: "Failed",
};

/** Status never uses red or green; shape and words carry the meaning. */
export const TxStatus = forwardRef<HTMLSpanElement, HTMLAttributes<HTMLSpanElement> & { tone: Tone; label?: string }>(function TxStatus({ tone, label, className, ...props }, ref) {
  const text = label ?? toneLabels[tone];
  return <span {...props} ref={ref} className={join("tx-status", className)} data-tone={tone} role="status">{text}</span>;
});

export function TxProgress({ value, label, className }: {
  /** `null` means the work is running but its length is unknown. */
  value: number | null;
  label?: string;
  className?: string;
}) {
  const known = value !== null;
  const percent = known ? Math.max(0, Math.min(100, Math.round(value))) : 0;
  return <div className={className}>
    {label && <div className="tx-progress-label">
      <span>{label}</span>
      {known && <span className="tx-num" aria-hidden="true">{percent}%</span>}
    </div>}
    <div
      className="tx-progress" role="progressbar"
      aria-valuemin={0} aria-valuemax={100} aria-label={label ?? "Progress"}
      data-indeterminate={known ? undefined : ""}
      {...(known ? { "aria-valuenow": percent, "aria-valuetext": `${percent}%` } : {})}
    >
      <i style={known ? { width: `${percent}%` } : undefined} />
    </div>
  </div>;
}

export function TxSkeleton({ width = "100%", className }: { width?: string; className?: string }) {
  return <div className={join("tx-skeleton", className)} style={{ width }} aria-hidden="true" />;
}

export interface TxEmptyProps {
  title: string;
  text?: string;
  action?: ReactNode;
}

export function TxEmpty({ title, text, action }: TxEmptyProps) {
  return <div className="tx-empty">
    <div className="tx-empty-title">{title}</div>
    {text && <div className="tx-empty-text">{text}</div>}
    {action}
  </div>;
}

/* ── Containers ─────────────────────────────────────────────────── */

export function TxPanel({ title, tabs, actions, children, className, bodyClassName, density }: {
  title?: ReactNode; tabs?: ReactNode; actions?: ReactNode; children: ReactNode; className?: string; bodyClassName?: string; density?: TxDensity;
}) {
  return <section className={join("tx-panel", className)} data-tx-density={density}>
    {tabs && <div className="tx-panel-tabs">{tabs}</div>}
    {title && <header className="tx-panel-head">{title}<span className="tx-spacer" />{actions}</header>}
    <div className={join("tx-panel-body", bodyClassName)}>{children}</div>
  </section>;
}

export function TxTable({ className, density, stickyHead, ...rest }: TableHTMLAttributes<HTMLTableElement> & {
  density?: TxDensity;
  /** Keep the header row in view while the rows scroll under it. */
  stickyHead?: boolean;
}) {
  return <table className={join("tx-table", className)} data-tx-density={density} data-sticky={stickyHead ? "" : undefined} {...rest} />;
}

export function TxTh({ align, numeric, sort, className, children, ...rest }: ThHTMLAttributes<HTMLTableCellElement> & {
  align?: "left" | "right";
  /** A column of numbers: right-aligned, like its cells. */
  numeric?: boolean;
  /** The column sorts; the arrow shows the current direction. */
  sort?: "ascending" | "descending" | "none";
}) {
  return <th className={className} data-align={align === "right" || numeric ? "right" : undefined} data-numeric={numeric ? "" : undefined} aria-sort={sort} {...rest}>
    {sort ? <span className="tx-sort">{children}{sort !== "none" && <TxIcon name={sort === "ascending" ? "arrow-up" : "arrow-down"} />}</span> : children}
  </th>;
}

export function TxTd({ align, numeric, className, ...rest }: TdHTMLAttributes<HTMLTableCellElement> & {
  align?: "left" | "right";
  /** A number: mono, tabular, right-aligned. */
  numeric?: boolean;
}) {
  return <td className={className} data-align={align === "right" || numeric ? "right" : undefined} data-numeric={numeric ? "" : undefined} {...rest} />;
}

/* ── Overlays ─────────────────────────────────────────────────── */

export interface TxMenuItem {
  id: string;
  label: string;
  icon?: Parameters<typeof TxIcon>[0]["name"];
  shortcut?: string;
  /** A second, quieter line under the label. */
  detail?: string;
  /** A short value at the row's end, such as a size. */
  hint?: string;
  tone?: "default" | "danger" | "warn";
  disabled?: boolean;
  /** Renders a separator above this item. */
  separated?: boolean;
  /** Optional heading before this row, outside keyboard item navigation. */
  group?: string;
  /** Selection state for a toggle or choice row. */
  checked?: boolean;
  onSelect: () => void;
}

/** The enabled rows of an open menu, in order. */
function menuRows(menu: HTMLElement | null): HTMLElement[] {
  return menu ? Array.from(menu.querySelectorAll<HTMLElement>("[role^=menuitem]:not([data-disabled])")) : [];
}

/** Arrow, Home and End navigation between menu rows. Radix moves menu focus by
    comparing against `document.activeElement`, which is the shadow host while
    focus is inside a module's shadow root, so there it always lands on the last
    row. Handling the keys here (and preventing Radix's handler) gives the same
    behaviour in both trees; selection, typeahead and dismissal stay with Radix.
    `index` is the focused row, or -1 when the menu itself has focus. */
function moveMenuFocus(event: KeyboardEvent<HTMLElement>, index: number): void {
  const rows = menuRows(event.currentTarget.closest<HTMLElement>("[role=menu]"));
  if (!rows.length) return;
  const last = rows.length - 1;
  const targets: Record<string, number | undefined> = {
    ArrowDown: index < 0 ? 0 : Math.min(index + 1, last),
    ArrowUp: index < 0 ? last : Math.max(index - 1, 0),
    Home: 0, PageUp: 0, End: last, PageDown: last,
  };
  const target = targets[event.key];
  if (target === undefined) return;
  event.preventDefault();
  rows[target].focus({ preventScroll: true });
}

function MenuItemContent({ item }: { item: TxMenuItem }) {
  return <>{item.icon && <TxIcon name={item.icon} />}
    {item.detail ? <span className="tx-menu-text"><span>{item.label}</span><span className="tx-menu-detail">{item.detail}</span></span> : <span>{item.label}</span>}
    {item.hint && <span className="tx-menu-hint">{item.hint}</span>}{item.shortcut && <kbd>{item.shortcut}</kbd>}{item.checked && <TxIcon className="tx-menu-check" name="check" />}</>;
}

/** While a menu is open, a press anywhere outside it only closes it, as a Radix modal menu's does, without Radix's document-wide changes (pointer events
    off on the body, scrolling locked, the page hidden from assistive technology), each of which restyles the whole page as a menu opens and again as it
    closes. The press lands on this layer under the menu; it stays until that pointer is released, so the release and its click land on it too. */
function MenuCatcher({ open, container }: { open: boolean; container?: HTMLElement }) {
  const [held, setHeld] = useState(false);
  const target = container ?? globalThis.document?.body;
  if ((!open && !held) || !target) return null;
  return createPortal(<div className="tx-menu-catcher" aria-hidden="true" onContextMenu={event => event.preventDefault()}
    onPointerDown={event => {
      event.preventDefault();
      setHeld(true);
      const view = event.currentTarget.ownerDocument.defaultView ?? window;
      const release = () => {
        view.removeEventListener("pointerup", release, true);
        view.removeEventListener("pointercancel", release, true);
        view.setTimeout(() => setHeld(false));
      };
      view.addEventListener("pointerup", release, true);
      view.addEventListener("pointercancel", release, true);
    }} />, target);
}

export function TxMenu({ label, trigger, items, align = "start", preview = false, openOnHover = false }: {
  label: string;
  trigger: ReactNode;
  items: TxMenuItem[];
  align?: "start" | "end";
  preview?: boolean;
  openOnHover?: boolean;
}) {
  const container = usePortalTarget();
  const content = useRef<HTMLDivElement>(null);
  const opener = useRef<HTMLButtonElement>(null);
  const openedByKeyboard = useRef(false);
  const [open, setOpen] = useState(false);
  const pinned = useRef(false), closeTimer = useRef<ReturnType<typeof setTimeout>>();
  const cancelClose = () => clearTimeout(closeTimer.current);
  const closeLater = () => {
    cancelClose();
    if (openOnHover && !pinned.current) closeTimer.current = setTimeout(() => setOpen(false), 160);
  };
  useEffect(() => () => clearTimeout(closeTimer.current), []);
  useEffect(() => {
    if (!openOnHover || !open) return;
    const escape = (event: globalThis.KeyboardEvent) => { if (event.key === "Escape") { event.preventDefault(); event.stopPropagation(); setOpen(false); } };
    document.addEventListener("keydown", escape, true);
    return () => document.removeEventListener("keydown", escape, true);
  }, [openOnHover, open]);
  if (preview) return <div className="tx-root tx-menu tx-glass" aria-label={label}>
    {items.map(item => <Fragment key={item.id}>
      {item.separated && <div className="tx-menu-sep" />}
      {item.group && <div className="tx-menu-label">{item.group}</div>}
      <div className="tx-menu-item" data-tone={item.tone} data-disabled={item.disabled ? "" : undefined}><MenuItemContent item={item} /></div>
    </Fragment>)}
  </div>;
  if (openOnHover) return <PopoverPrimitive.Root open={open} onOpenChange={value => { cancelClose(); setOpen(value); if (!value) pinned.current = false; }}>
    <PopoverPrimitive.Trigger asChild
      onPointerEnter={event => { if (event.pointerType !== "touch") { cancelClose(); if (!open) pinned.current = false; openedByKeyboard.current = false; setOpen(true); } }}
      onPointerLeave={closeLater}
      onPointerDown={() => { openedByKeyboard.current = false; }}
      onClick={event => { if (open && !pinned.current) { event.preventDefault(); pinned.current = true; } }}
      onKeyDown={event => { if (["Enter", " ", "ArrowDown", "ArrowUp"].includes(event.key)) { cancelClose(); openedByKeyboard.current = true; pinned.current = true; if (event.key.startsWith("Arrow")) { event.preventDefault(); setOpen(true); if (open) menuRows(content.current)[event.key === "ArrowUp" ? menuRows(content.current).length - 1 : 0]?.focus(); } } }}
    >{trigger}</PopoverPrimitive.Trigger>
    <PopoverPrimitive.Portal container={container}><PopoverPrimitive.Content ref={content} role="menu" className="tx-root tx-menu tx-glass" align={align} aria-label={label}
      sideOffset={floatGap()} onPointerEnter={cancelClose} onPointerLeave={closeLater}
      onOpenAutoFocus={event => { event.preventDefault(); if (openedByKeyboard.current) menuRows(content.current)[0]?.focus(); }}
      onCloseAutoFocus={event => { if (!pinned.current && !openedByKeyboard.current) event.preventDefault(); }}
    >{items.map(item => <Fragment key={item.id}>
      {item.separated && <div role="separator" className="tx-menu-sep" />}
      {item.group && <div className="tx-menu-label">{item.group}</div>}
      <button type="button" className="tx-menu-item" role={item.checked === undefined ? "menuitem" : "menuitemcheckbox"} aria-checked={item.checked}
        data-tone={item.tone} data-disabled={item.disabled ? "" : undefined} disabled={item.disabled}
        onKeyDown={event => moveMenuFocus(event, menuRows(content.current).indexOf(event.currentTarget))}
        onClick={() => { setOpen(false); cancelClose(); item.onSelect(); }}><MenuItemContent item={item} /></button>
    </Fragment>)}</PopoverPrimitive.Content></PopoverPrimitive.Portal>
  </PopoverPrimitive.Root>;
  return <DropdownMenuPrimitive.Root open={open} modal={false} onOpenChange={value => { cancelClose(); setOpen(value); if (!value) pinned.current = false; }}>
    <MenuCatcher open={open} container={container} />
    <DropdownMenuPrimitive.Trigger
      ref={opener}
      asChild
      onPointerEnter={event => { if (openOnHover && event.pointerType !== "touch") { cancelClose(); openedByKeyboard.current = false; setOpen(true); } }}
      onPointerLeave={closeLater}
      onPointerDown={event => { openedByKeyboard.current = false; if (openOnHover && open && !pinned.current) { event.preventDefault(); pinned.current = true; } else pinned.current = true; }}
      onKeyDown={() => { cancelClose(); openedByKeyboard.current = true; pinned.current = true; }}
    >{trigger}</DropdownMenuPrimitive.Trigger>
    <DropdownMenuPrimitive.Portal container={container}>
      <DropdownMenuPrimitive.Content
        ref={content} className="tx-root tx-menu tx-glass" align={align} sideOffset={floatGap()} aria-label={label} aria-labelledby={undefined}
        onPointerEnter={cancelClose}
        onPointerLeave={closeLater}
        // Focus goes back to the button however the menu closed, a press outside included (Radix gives it back only when
        // nothing outside was touched), unless the chosen item moved it on (a dialog it opened has its field focused).
        onCloseAutoFocus={event => {
          event.preventDefault();
          const active = (opener.current?.getRootNode() as Document | ShadowRoot | undefined)?.activeElement;
          if (!active || active === document.body || content.current?.contains(active)) opener.current?.focus({ preventScroll: true });
        }}
        onFocus={event => {
          // Radix focuses the menu on open and, when it was opened from the
          // keyboard, moves on to the first row with its shadow-unaware
          // helper. Taking that step here (and preventing Radix's) gives the
          // same result inside a shadow root.
          const first = menuRows(content.current)[0];
          if (event.target !== event.currentTarget || !openedByKeyboard.current || !first) return;
          event.preventDefault();
          first.focus({ preventScroll: true });
        }}
        onKeyDown={event => { if (event.target === event.currentTarget) moveMenuFocus(event, -1); }}
      >
        {items.map(item => <Fragment key={item.id}>
          {item.separated && <DropdownMenuPrimitive.Separator className="tx-menu-sep" />}
          {item.group && <DropdownMenuPrimitive.Label className="tx-menu-label">{item.group}</DropdownMenuPrimitive.Label>}
          {/* The Radix item is the row itself, so keyboard navigation and the
              select callback land on the element the user sees. */}
          {item.checked !== undefined ? <DropdownMenuPrimitive.CheckboxItem
            className="tx-menu-item" data-tone={item.tone} checked={item.checked} disabled={item.disabled}
            onSelect={item.onSelect}
            onKeyDown={event => moveMenuFocus(event, menuRows(content.current).indexOf(event.currentTarget))}
          ><MenuItemContent item={item} /></DropdownMenuPrimitive.CheckboxItem> : <DropdownMenuPrimitive.Item
            className="tx-menu-item" data-tone={item.tone} disabled={item.disabled}
            onSelect={item.onSelect}
            onKeyDown={event => moveMenuFocus(event, menuRows(content.current).indexOf(event.currentTarget))}
          >
            <MenuItemContent item={item} />
          </DropdownMenuPrimitive.Item>}
        </Fragment>)}
      </DropdownMenuPrimitive.Content>
    </DropdownMenuPrimitive.Portal>
  </DropdownMenuPrimitive.Root>;
}

export function TxTooltip({ label, children }: { label: string; children: ReactNode }) {
  const container = usePortalTarget();
  return <TooltipPrimitive.Root>
    <TooltipPrimitive.Trigger asChild>{children}</TooltipPrimitive.Trigger>
    <TooltipPrimitive.Portal container={container}>
      <TooltipPrimitive.Content className="tx-root tx-tip" sideOffset={floatGap()}>{label}</TooltipPrimitive.Content>
    </TooltipPrimitive.Portal>
  </TooltipPrimitive.Root>;
}

export interface TxContextMenuProps {
  open: boolean;
  onOpenChange(open: boolean): void;
  point: { x: number; y: number };
  label: string;
  items: TxMenuItem[];
}

/** A context action menu at a caller's pointer position, with the same rows,
 * keyboard navigation and overlay theme as the ordinary action menu. */
export function TxContextMenu({ open, onOpenChange, point, label, items }: TxContextMenuProps) {
  const container = usePortalTarget();
  const anchor = useRef<HTMLSpanElement | null>(null);
  const content = useRef<HTMLDivElement>(null);
  const opener = useRef<HTMLElement | null>(null);
  const wasOpen = useRef(false);
  if (open && !wasOpen.current) {
    let active = anchor.current?.ownerDocument.activeElement ?? globalThis.document?.activeElement;
    while (active?.shadowRoot?.activeElement) active = active.shadowRoot.activeElement;
    if (active && !content.current?.contains(active)) opener.current = active as HTMLElement;
  }
  wasOpen.current = open;
  useEffect(() => {
    const element = anchor.current;
    if (!element) return;
    element.style.setProperty("--tx-context-x", `${point.x}px`);
    element.style.setProperty("--tx-context-y", `${point.y}px`);
  }, [open, point.x, point.y]);
  return <DropdownMenuPrimitive.Root open={open} onOpenChange={onOpenChange} modal={false}>
    <MenuCatcher open={open} container={container} />
    <DropdownMenuPrimitive.Trigger asChild>
      <span ref={element => {
        anchor.current = element;
        element?.style.setProperty("--tx-context-x", `${point.x}px`);
        element?.style.setProperty("--tx-context-y", `${point.y}px`);
      }} className="tx-context-menu-anchor" aria-hidden="true" tabIndex={-1} />
    </DropdownMenuPrimitive.Trigger>
    <DropdownMenuPrimitive.Portal container={container}>
      <DropdownMenuPrimitive.Content ref={content} className="tx-root tx-menu tx-glass"
        aria-label={label} align="start" sideOffset={0}
        onFocus={event => {
          if (event.target !== event.currentTarget) return;
          const first = menuRows(content.current)[0];
          if (first) { event.preventDefault(); first.focus({ preventScroll: true }); }
        }}
        onKeyDown={event => { if (event.target === event.currentTarget) moveMenuFocus(event, -1); }}
        onCloseAutoFocus={event => {
          event.preventDefault();
          // As TxMenu: an item that moved focus on (into the dialog it opened) keeps it there.
          const active = (opener.current?.getRootNode() as Document | ShadowRoot | undefined)?.activeElement;
          if (opener.current?.isConnected && (!active || active === document.body || content.current?.contains(active))) opener.current.focus({ preventScroll: true });
        }}>
        {items.map(item => <Fragment key={item.id}>
          {item.separated && <DropdownMenuPrimitive.Separator className="tx-menu-sep" />}
          <DropdownMenuPrimitive.Item className="tx-menu-item" data-tone={item.tone} disabled={item.disabled}
            onSelect={item.onSelect}
            onKeyDown={event => moveMenuFocus(event, menuRows(content.current).indexOf(event.currentTarget))}>
            <MenuItemContent item={item} />
          </DropdownMenuPrimitive.Item>
        </Fragment>)}
      </DropdownMenuPrimitive.Content>
    </DropdownMenuPrimitive.Portal>
  </DropdownMenuPrimitive.Root>;
}

export function TxTooltipProvider({ children }: { children: ReactNode }) {
  return <TooltipPrimitive.Provider delayDuration={350}>{children}</TooltipPrimitive.Provider>;
}

/** The same surface is used in Radix overlays and the design specimen. */
export function TxDialogSurface({ title, children, footer }: { title: ReactNode; children?: ReactNode; footer?: ReactNode }) {
  return <>
    <div className="tx-dialog-head">{title}</div>
    <div className="tx-dialog-body">{children}</div>
    <div className="tx-dialog-foot">{footer}</div>
  </>;
}

export function TxSheetSurface({ title, actions, close, tabs, footer, children }: {
  title: ReactNode; actions?: ReactNode; close: ReactNode; tabs?: ReactNode; footer?: ReactNode; children: ReactNode;
}) {
  return <>
    <header className="tx-sheet-head"><div className="tx-sheet-title">{title}</div>{actions && <div className="tx-sheet-actions">{actions}</div>}{close}</header>
    {tabs && <div className="tx-sheet-tabs">{tabs}</div>}
    <div className="tx-sheet-body">{children}</div>
    {footer && <footer className="tx-sheet-foot">{footer}</footer>}
  </>;
}

export function TxDialog({ open, onOpenChange, title, children, footer, label, className, style, size = "compact", preview = false, dim = true }: {
  open: boolean;
  /** false: the page behind stays as it is (settings dialogs whose changes preview on the chart behind). */
  dim?: boolean;
  onOpenChange: (open: boolean) => void;
  title: string;
  children?: ReactNode;
  footer?: ReactNode;
  /** Accessible name when no visible title is rendered. */
  label?: string;
  className?: string;
  size?: TxOverlaySize;
  /** Layout properties; use size for shared dialog dimensions. */
  style?: CSSProperties;
  preview?: boolean;
}) {
  const {container, ready} = useModalContainer(open && !preview);
  const focus = useDialogFocus();
  if (preview) return open ? <div className={join("tx-root tx-dialog tx-glass", className)} style={style} data-size={size} data-preview="" aria-label={title}>
    <TxDialogSurface title={title} footer={footer}>{children}</TxDialogSurface>
  </div> : null;
  return <DialogPrimitive.Root open={open} onOpenChange={onOpenChange}>
    {ready && <DialogPrimitive.Portal container={container}>
      {dim && <DialogPrimitive.Overlay className="tx-dialog-overlay" />}
      {/* Menus and popovers opened in the dialog go into the dialog's layer: outside it, the open dialog hides them
          from assistive technology and role queries. */}
      <DialogPrimitive.Content {...focus} className={join("tx-root tx-dialog tx-glass", className)} style={style} data-size={size} aria-label={title || label} aria-describedby={undefined}>
        <TxOverlayProvider container={container ?? null}>
          <TxDialogSurface title={<DialogPrimitive.Title>{title}</DialogPrimitive.Title>}
            footer={footer ?? <DialogPrimitive.Close asChild><TxButton variant="primary" size="sm">Close</TxButton></DialogPrimitive.Close>}
          >{children}</TxDialogSurface>
        </TxOverlayProvider>
      </DialogPrimitive.Content>
    </DialogPrimitive.Portal>}
  </DialogPrimitive.Root>;
}

export function TxSheet({ open, onOpenChange, title, actions, tabs, footer, children, label = "Sheet", preview = false }: {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  title: string;
  /** Controls on the title line before the close button, such as a switch that turns on or off what the sheet edits. */
  actions?: ReactNode;
  tabs?: ReactNode;
  footer?: ReactNode;
  children: ReactNode;
  label?: string;
  /** In-page specimen, sharing the real surface. Never used by product flows. */
  preview?: boolean;
}) {
  const {container, ready} = useModalContainer(open && !preview);
  const focus = useDialogFocus();
  if (preview) return open ? <>
    <div className="tx-sheet-overlay" data-preview="" />
    <aside className="tx-root tx-sheet tx-glass" data-preview="" aria-label={title}>
      <TxSheetSurface title={title} actions={actions} close={<TxIconButton label={`Close ${label}`} icon="close" onClick={() => onOpenChange(false)} />}
        tabs={tabs} footer={footer}>{children}</TxSheetSurface>
    </aside>
  </> : null;
  return <DialogPrimitive.Root open={open} onOpenChange={onOpenChange}>
    {ready && <DialogPrimitive.Portal container={container}>
      <DialogPrimitive.Overlay className="tx-sheet-overlay" />
      {/* As TxDialog: popups opened in the sheet go into its layer. */}
      <DialogPrimitive.Content {...focus} className="tx-root tx-sheet tx-glass" aria-label={title || label} aria-describedby={undefined}>
        <TxOverlayProvider container={container ?? null}>
          <TxSheetSurface title={<DialogPrimitive.Title>{title}</DialogPrimitive.Title>} actions={actions}
            close={<DialogPrimitive.Close asChild><TxIconButton label={`Close ${label}`} icon="close" /></DialogPrimitive.Close>}
            tabs={tabs} footer={footer}>{children}</TxSheetSurface>
        </TxOverlayProvider>
      </DialogPrimitive.Content>
    </DialogPrimitive.Portal>}
  </DialogPrimitive.Root>;
}

export interface TxToastOptions {
  id?: string;
  title: string;
  detail?: string;
  tone?: "done" | "failed" | "info";
  action?: { label: string; onClick: () => void };
  duration?: number;
}

interface ToastRecord extends TxToastOptions { key: string }

const ToastContext = createContext<{ show: (options: TxToastOptions) => void } | null>(null);

/** Replaces confirmation dialogs for reversible work: do it, then offer Undo. */
export function useTxToast(): (options: TxToastOptions) => void {
  const context = useContext(ToastContext);
  if (!context) throw new Error("useTxToast needs a TxToastProvider");
  return context.show;
}

export function TxToastProvider({ children }: { children: ReactNode }) {
  const container = usePortalTarget();
  const [records, setRecords] = useState<ToastRecord[]>([]);
  const counter = useRef(0);
  const api = useMemo(() => ({
    show: (options: TxToastOptions) => {
      counter.current += 1;
      const key = options.id ?? `toast-${counter.current}`;
      setRecords(current => [...current.filter(record => record.key !== key), { ...options, key }]);
    },
  }), []);
  return <ToastContext.Provider value={api}>
    <ToastPrimitive.Provider swipeDirection="right" duration={6000} label="Notifications">
      {children}
      {records.map(record => <ToastPrimitive.Root
        key={record.key} className="tx-root tx-toast tx-glass" duration={record.duration ?? 6000}
        onOpenChange={next => { if (!next) setRecords(current => current.filter(item => item.key !== record.key)); }}
      >
        <ToastPrimitive.Description asChild>
          <div className="tx-toast-content">
            <div className="tx-toast-body">
              <div className="tx-toast-title" data-tone={record.tone ?? "done"}>{record.title}</div>
              {record.detail && <div className="tx-toast-detail">{record.detail}</div>}
            </div>
            {record.action && <TxButton size="sm" variant="quiet" onClick={() => {
              record.action!.onClick(); setRecords(current => current.filter(item => item.key !== record.key));
            }}>{record.action.label}</TxButton>}
          </div>
        </ToastPrimitive.Description>
      </ToastPrimitive.Root>)}
      {typeof document !== "undefined" && createPortal(<ToastPrimitive.Viewport className="tx-root tx-toast-viewport" />, container ?? document.body)}
    </ToastPrimitive.Provider>
  </ToastContext.Provider>;
}

export interface TxNoticeProps {
  title: string;
  detail?: string;
  tone?: "info" | "failed";
  action?: { label: string; onClick: () => void };
  /** Dismisses itself after this many milliseconds; pointer or focus inside pauses it. */
  duration?: number;
  onDismiss: () => void;
  className?: string;
}

/* ── List rows ───────────────────────────────────────────────── */

/** A settings-style row: what it is on the left, the control on the right. */
export function TxRow({ title, detail, children, tone, icon }: {
  title: ReactNode; detail?: ReactNode; children?: ReactNode; tone?: "default" | "failed" | "off";
  icon?: Parameters<typeof TxIcon>[0]["name"];
}) {
  return <div className="tx-settings-row" data-tone={tone} data-module={icon ? "" : undefined}>
    {icon && <div className="tx-tile" data-tone={tone}><TxIcon name={icon} /></div>}
    <div className="tx-settings-copy">
      <div className="tx-settings-name">{title}</div>
      {detail && <div className="tx-settings-detail">{detail}</div>}
    </div>
    {children}
  </div>;
}

/** One settings page: an optional lede, then groups with even spacing. Modules
    use it for their `platform.settings_section` views so every page reads alike. */
export function TxSettingsPage({ lede, children, ...props }: HTMLAttributes<HTMLDivElement> & { lede?: ReactNode }) {
  return <div {...props} className={join("tx-settings-page", props.className)}>
    {lede && <p className="tx-settings-lede">{lede}</p>}
    {children}
  </div>;
}

/** A titled card of rows. The action sits on the title line, where the user
    looks for "add" in Cursor, Linear and macOS settings. */
export function TxSettingsGroup({ title, detail, action, children, className, ...props }: Omit<HTMLAttributes<HTMLElement>, "title"> & {
  title?: ReactNode; detail?: ReactNode; action?: ReactNode;
}) {
  return <section {...props} className={join("tx-settings-group", className)}>
    {(title || action) && <header className="tx-settings-group-head">
      <div className="tx-settings-group-copy">
        {title && <h4 className="tx-settings-group-title">{title}</h4>}
        {detail && <p className="tx-settings-group-detail">{detail}</p>}
      </div>
      {action && <div className="tx-settings-group-action">{action}</div>}
    </header>}
    <div className="tx-settings-card">{children}</div>
  </section>;
}

/** A line of text inside a settings card: an empty list, a load error, a hint under a row. */
export function TxSettingsNote({ children, tone = "muted" }: { children: ReactNode; tone?: "muted" | "error" }) {
  return <p className="tx-settings-note" data-tone={tone} role={tone === "error" ? "alert" : undefined}>{children}</p>;
}

/** One line of error text: what broke, what was lost, what to do next.
    `failed` is to fix now (orange), `watch` is to keep an eye on (amber), `info` is a plain fact. */
export function TxAlert({ title, children, tone = "failed", actions }: {
  title: string; children?: ReactNode; tone?: "failed" | "watch" | "info"; actions?: ReactNode;
}) {
  const failed = tone === "failed";
  return <div className="tx-alert" data-tone={tone} role={failed ? "alert" : undefined}>
    <TxIcon name={tone === "info" ? "info" : "alert"} />
    <div className="tx-alert-content">
      <h4>{title}</h4>
      {children && <p>{children}</p>}
      {actions && <div className="tx-alert-actions">{actions}</div>}
    </div>
  </div>;
}

/** Structured content disclosure. Collapsing keeps local controls mounted. */
export function TxDisclosure({ label, children, open, defaultOpen = false, onOpenChange, className, ...props }:
  Omit<HTMLAttributes<HTMLDivElement>, "children"> & {
    label: ReactNode; children?: ReactNode; open?: boolean; defaultOpen?: boolean; onOpenChange?: (open: boolean) => void;
  }) {
  const [localOpen, setLocalOpen] = useState(defaultOpen);
  const expanded = open ?? localOpen;
  const id = useId();
  return <div {...props} className={join("tx-disclosure-group", className)}>
    <button type="button" className="tx-disclosure" aria-expanded={expanded} aria-controls={id}
      onClick={() => { const next = !expanded; if (open === undefined) setLocalOpen(next); onOpenChange?.(next); }}
    ><TxIcon name="chevron" />{label}</button>
    <div id={id} className="tx-disclosure-body" hidden={!expanded}>{children}</div>
  </div>;
}

/** Collapsed raw detail. The only place error text, ids or paths may appear. */
export function TxDetails({ label = "Technical details", children }: { label?: string; children: ReactNode }) {
  const [open, setOpen] = useState(false);
  const id = useId();
  return <div className="tx-details">
    <button type="button" className="tx-disclosure"
      aria-expanded={open} aria-controls={id} onClick={() => setOpen(current => !current)}
    ><TxIcon name="chevron" />{label}</button>
    {open && <div id={id} className="tx-trace">
      <code>{children}</code>
      {typeof children === "string" && <TxIconButton size="sm" label="Copy technical details" icon="copy" onClick={() => void navigator.clipboard?.writeText(children)} />}
    </div>}
  </div>;
}

/* ── Root ───────────────────────────────────────────────────── */

/** Wraps any module or platform surface. A module renders its whole tree inside
    one TxRoot; that is what installs the stylesheet into the module's own root
    node, so components work inside a shadow tree without extra wiring.
    It also routes popups into the kernel's overlay root for that container
    (see overlay.tsx); `overlay` overrides the lookup, for example with
    `ctx.overlayRoot` when the tree is not rendered into the mount element. */
export function TxRoot({ children, className, style, overlay, density, ...props }: HTMLAttributes<HTMLDivElement> & {
  children: ReactNode; overlay?: HTMLElement | null;
  /** The density of the whole surface; any element inside can set its own with `data-tx-density`. */
  density?: TxDensity;
}) {
  const ref = useRef<HTMLDivElement>(null);
  const inherited = useOverlayContainer();
  const [found, setFound] = useState<HTMLElement | null>(null);
  useEffect(() => {
    if (!ref.current) return;
    installUiStyles(ref.current);
    setFound(findOverlayRoot(ref.current));
  }, []);
  // The provider is always rendered so resolving the overlay root does not
  // remount the subtree.
  return <TooltipPrimitive.Provider delayDuration={350}><TxOverlayProvider container={overlay ?? found ?? inherited}>
    <div {...props} ref={ref} className={join("tx-root", className)} style={style} data-tx-density={density}>{children}</div>
  </TxOverlayProvider></TooltipPrimitive.Provider>;
}
