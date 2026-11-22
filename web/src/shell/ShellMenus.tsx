import { intlLocale } from "@talyx/sdk";
import { t } from "./i18n";
import { type ReactNode, useEffect, useMemo, useState } from "react";
import { TxButton, TxContextMenu, TxDialog, TxField, TxIcon, TxInput, TxPopover, TxSearch, TxSkeleton, type TxMenuItem } from "@talyx/sdk";
import type { ShellModuleChoice } from "./shellChrome";
import { ModuleIcon } from "./ModuleIcon";
import "./ShellMenus.css";

/* The shell's own overlays: the workspace menu, the "+" box, the module picker, the empty
   workspace and the dialogs they lead to. Every surface is a component library part; this file only fills them. */

/** Where a menu opened from a control drops: under its left edge. */
export const below = (element: HTMLElement) => {
  const rect = element.getBoundingClientRect();
  return { x: Math.round(rect.left), y: Math.round(rect.bottom + 6) };
};

/* ── The current workspace's menu: actions only ──────────────────────────────────────────────── */

export interface WorkspaceMenuActions {
  saveBusy: boolean;
  /** Work AI started here that is still going; "Stop AI work" shows only while there is some. */
  aiWork: number;
  onStopAiWork: () => void;
  onAddModule: () => void;
  onSave: () => void;
  onRename: () => void;
  onFolder: () => void;
  onCopyAiAddress: () => void;
  onOpenNewWindow: () => void;
  onClose: () => void;
}

export function WorkspaceMenu({ name, point, onOpenChange, actions }: {
  name: string;
  point: { x: number; y: number } | null;
  onOpenChange: (open: boolean) => void;
  actions: WorkspaceMenuActions;
}) {
  const items: TxMenuItem[] = [
    { id: "add", label: t("Add module…"), icon: "plus", onSelect: actions.onAddModule },
    { id: "save", label: t("Save"), icon: "download", shortcut: "Ctrl S", separated: true, disabled: actions.saveBusy, onSelect: actions.onSave },
    { id: "rename", label: t("Rename…"), icon: "edit", disabled: actions.saveBusy, onSelect: actions.onRename },
    { id: "folder", label: t("Folder…"), icon: "folder", onSelect: actions.onFolder },
    { id: "ai", label: t("Copy AI connection address"), icon: "copy", onSelect: actions.onCopyAiAddress },
    ...(actions.aiWork > 0 ? [{ id: "stop-ai", label: t("Stop AI work"), icon: "stop" as const, hint: String(actions.aiWork), onSelect: actions.onStopAiWork }] : []),
    { id: "window", label: t("Open in new window"), icon: "external", separated: true, onSelect: actions.onOpenNewWindow },
    { id: "close", label: t("Close workspace"), icon: "close", onSelect: actions.onClose },
  ];
  return <TxContextMenu open={point !== null} onOpenChange={onOpenChange} point={point ?? { x: 0, y: 0 }} label={t("{name} workspace", { name })} items={items} />;
}

/* ── Adding a module in place ────────────────────────────────────────────────────────────────── */

/** The modules that can be added, under the control that asked; never leaves the workspace. A list of rows in a
    library popover rather than the library menu, whose rows can't show a module's own picture. */
export function ModuleMenu({ anchor, modules, onOpenChange, onPick }: {
  anchor: HTMLElement | null;
  modules: ShellModuleChoice[];
  onOpenChange: (open: boolean) => void;
  onPick: (module: ShellModuleChoice) => void;
}) {
  return <TxPopover open={anchor !== null} onOpenChange={onOpenChange} anchor={anchor ?? undefined} label={t("Add module")} className="talyx-module-picker">
    <div role="group" aria-label={t("Modules")}>
      {modules.map(item => <TxButton key={item.kind} variant="row" disabled={item.failed}
        title={item.failed ? t("{name} couldn't start", { name: item.name }) : undefined} onClick={() => { onOpenChange(false); onPick(item); }}>
        <ModuleIcon module={item.kind} /><span className="talyx-launcher-name">{item.name}</span>
      </TxButton>)}
    </div>
  </TxPopover>;
}

/** A workspace with no panels: the modules it can start with, one click each. */
export function DockEmptyState({ modules, onPick }: { modules: ShellModuleChoice[]; onPick: (module: ShellModuleChoice) => void }) {
  return <div className="talyx-dock-empty">
    <div className="talyx-dock-empty-body">
      <h2 className="talyx-dock-empty-title">{t("Add a module")}</h2>
      <div className="talyx-dock-empty-modules" role="group" aria-label={t("Modules")}>
        {modules.map(item => <button key={item.kind} type="button" className="talyx-module-tile" disabled={item.failed}
          title={item.failed ? t("{name} couldn't start", { name: item.name }) : undefined} onClick={() => onPick(item)}>
          <ModuleIcon module={item.kind} />
          <span>{item.name}</span>
        </button>)}
      </div>
    </div>
  </div>;
}

/* ── "+": a new workspace, or a saved one ───────────────────────────────────────────────────── */

export interface ShellSavedWorkspace {
  id: string;
  name: string;
  savedAt: string | null;
  open: boolean;
}


function savedWhen(value: string | null): string {
  if (!value) return "";
  const date = new Date(value), minutes = (Date.now() - date.getTime()) / 60_000;
  if (minutes < 1) return t("Now");
  if (minutes < 60) return t("{minutes}m", { minutes: Math.floor(minutes) });
  if (minutes < 24 * 60) return t("{hours}h", { hours: Math.floor(minutes / 60) });
  if (minutes < 7 * 24 * 60) return date.toLocaleDateString(intlLocale(), { weekday: "short" });
  return date.toLocaleDateString(intlLocale(), { month: "short", day: "numeric" });
}

export function WorkspaceLauncher({ saved, phase, error, onRetry, onNew, onOpen, onRename, onDelete, external }: {
  saved: ShellSavedWorkspace[];
  phase: "loading" | "ready" | "error";
  error: string | null;
  onRetry: () => void;
  onNew: () => void;
  onOpen: (item: ShellSavedWorkspace) => void;
  onRename: (item: ShellSavedWorkspace) => void;
  onDelete: (item: ShellSavedWorkspace) => void;
  /** Lets the command palette open this box. */
  external?: { open: boolean; onOpenChange: (open: boolean) => void };
}) {
  const [ownOpen, setOwnOpen] = useState(false);
  const open = external?.open ?? ownOpen, setOpen = external?.onOpenChange ?? setOwnOpen;
  const [query, setQuery] = useState("");
  const [menu, setMenu] = useState<{ item: ShellSavedWorkspace; x: number; y: number } | null>(null);
  useEffect(() => { if (!open) setQuery(""); }, [open]);
  const shown = useMemo(() => {
    const words = query.trim().toLocaleLowerCase().split(/\s+/).filter(Boolean);
    return saved.filter(item => words.every(word => item.name.toLocaleLowerCase().includes(word)));
  }, [saved, query]);
  const act = (work: () => void) => { setOpen(false); work(); };
  return <>
    <TxPopover open={open} onOpenChange={setOpen} label={t("Workspaces")} className="talyx-launcher"
      trigger={<button type="button" className="talyx-ws-add" aria-label={t("New or saved workspace")} title={t("New or saved workspace")}><TxIcon name="plus" /></button>}>
      <TxSearch value={query} onValueChange={setQuery} placeholder={t("Search saved workspaces")} aria-label={t("Search saved workspaces")} autoFocus
        onKeyDown={event => { if (event.key === "Enter" && shown[0]) act(() => onOpen(shown[0])); }} />
      <div className="talyx-launcher-list" role="list" aria-label={t("Saved workspaces")}>
        {!query.trim() && <TxButton variant="row" className="talyx-launcher-new" onClick={() => act(onNew)}>
          <TxIcon name="plus" /><span>{t("New workspace")}</span>
        </TxButton>}
        {phase === "loading" && saved.length === 0 && [0, 1, 2, 3].map(index => <div key={index} className="talyx-launcher-skeleton"><TxSkeleton width={`${62 - index * 9}%`} /></div>)}
        {phase === "error" && <div className="talyx-launcher-state" role="alert">
          <span>{t("Couldn't read saved workspaces.")} {error}</span>
          <TxButton size="sm" onClick={onRetry}>{t("Retry")}</TxButton>
        </div>}
        {saved.length > 0 && shown.length === 0 && <div className="talyx-launcher-state">{t("Nothing matches “{query}”.", { query: query.trim() })}</div>}
        {shown.map(item => <TxButton key={item.id} variant="row" role="listitem" className="talyx-launcher-row"
          title={item.name}
          onClick={() => act(() => onOpen(item))}
          onContextMenu={event => { event.preventDefault(); setMenu({ item, x: event.clientX, y: event.clientY }); }}>
          <span className="talyx-launcher-name">{item.name}</span>
          <span className="talyx-launcher-when">{item.open ? t("Open") : savedWhen(item.savedAt)}</span>
        </TxButton>)}
      </div>
    </TxPopover>
    <TxContextMenu open={menu !== null} onOpenChange={value => { if (!value) setMenu(null); }} point={menu ?? { x: 0, y: 0 }}
      label={menu ? t("{name} actions", { name: menu.item.name }) : t("Workspace actions")} items={menu ? [
        { id: "open", label: menu.item.open ? t("Go to") : t("Open"), onSelect: () => act(() => onOpen(menu.item)) },
        { id: "rename", label: t("Rename…"), icon: "edit", separated: true, onSelect: () => act(() => onRename(menu.item)) },
        { id: "delete", label: t("Delete…"), icon: "trash", tone: "danger", onSelect: () => act(() => onDelete(menu.item)) },
      ] : []} />
  </>;
}

/* ── Dialogs ──────────────────────────────────────────────────────────────────────────────────── */

/** Asking before something is lost: closing a panel with unsaved work, deleting a saved workspace. */
export function ShellConfirm({ open, title, children, confirm, busy, disabled, onCancel, onConfirm }: {
  open: boolean;
  title: string;
  children?: ReactNode;
  confirm: string;
  busy?: boolean;
  disabled?: boolean;
  onCancel: () => void;
  onConfirm: () => void;
}) {
  return <TxDialog open={open} onOpenChange={value => { if (!value && !busy) onCancel(); }} title={title} className="talyx-shell-dialog"
    footer={<>
      <TxButton data-autofocus="" disabled={busy} onClick={onCancel}>{t("Cancel")}</TxButton>
      <TxButton variant="primary" loading={busy} disabled={disabled} onClick={onConfirm}>{confirm}</TxButton>
    </>}>
    {children}
  </TxDialog>;
}

/** Naming a workspace: saving it the first time ("Save as") or renaming it. */
export function ShellNameDialog({ open, mode, value, busy, onValue, onCancel, onCommit }: {
  open: boolean;
  mode: "save" | "rename";
  value: string;
  busy?: boolean;
  onValue: (value: string) => void;
  onCancel: () => void;
  onCommit: () => void;
}) {
  return <TxDialog open={open} onOpenChange={next => { if (!next && !busy) onCancel(); }} title={mode === "save" ? t("Save workspace") : t("Rename workspace")}
    className="talyx-shell-dialog" footer={<>
      <TxButton disabled={busy} onClick={onCancel}>{t("Cancel")}</TxButton>
      <TxButton variant="primary" loading={busy} disabled={!value.trim()} onClick={onCommit}>{mode === "save" ? t("Save") : t("Rename")}</TxButton>
    </>}>
    <TxField label={t("Name")}>
      <TxInput aria-label={t("Workspace name")} autoFocus autoComplete="off" spellCheck={false} disabled={busy} value={value}
        onChange={event => onValue(event.target.value)} onFocus={event => event.currentTarget.select()}
        onKeyDown={event => { if (event.key === "Enter" && value.trim()) { event.preventDefault(); onCommit(); } }} />
    </TxField>
  </TxDialog>;
}
