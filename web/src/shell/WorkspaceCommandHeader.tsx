import { type ReactNode, useEffect, useMemo, useRef, useState } from "react";
import { TxButton, TxDialog, TxField, TxIcon, TxInput, TxSearch, type IconName } from "@talyx/sdk";

import { ShellTopBar } from "./ShellTopBar";
import { below, ShellNameDialog, WorkspaceMenu } from "./ShellMenus";
import { ModuleIcon } from "./ModuleIcon";
import type { ShellAttention, ShellModuleChoice, ShellWorkspaceTab } from "./shellChrome";
import { stopWorkspaceAI } from "./workspaceActivityData";
import { openPlatformSettings } from "./PlatformControls";
import { platformRuntime } from "../kernel/runtime";

type NameMode = "save" | "rename" | null;

interface WorkspaceCommandHeaderProps {
  tabs: ShellWorkspaceTab[];
  attention: ShellAttention[];
  settingsAlert: boolean;
  modules: ShellModuleChoice[];
  onCommunity: () => void;
  onFault: (item: ShellAttention) => void;
  addWorkspace?: ReactNode;
  onOpenLauncher: () => void;
  workspaceId: string;
  onSelectWorkspace: (tab: ShellWorkspaceTab) => void;
  onOpenNewWindow: () => void;
  onCloseWorkspace: () => void;
  onError: (message: string) => void;
  onNotice: (message: string) => void;
  name: string;
  saving: boolean;
  nameMode: NameMode;
  nameInput: string;
  onBack: () => void;
  /** Opens the module picker under the given element (the workspace's tab when none). */
  onAddModule: (anchor?: HTMLElement | null) => void;
  onAddModuleKind: (module: ShellModuleChoice) => void;
  onSave: () => void;
  onStartRename: () => void;
  onNameInput: (value: string) => void;
  onCommitName: () => void;
  onCancelName: () => void;
}

/* ── Ctrl+K ─────────────────────────────────────────────────────────────────────────────────── */

interface PaletteCommand { id: string; group: string; label: string; icon?: IconName; module?: string; shortcut?: string; keywords?: string; run: () => void }

/** Every shell action by name, in a library dialog. Modules add theirs through declarations later; nothing here is
    written for one module. */
function CommandPalette({ commands }: { commands: PaletteCommand[] }) {
  const [open, setOpen] = useState(false);
  const [query, setQuery] = useState("");
  const [active, setActive] = useState(0);
  const list = useRef<HTMLDivElement>(null);
  useEffect(() => {
    const toggle = (event: KeyboardEvent) => {
      if ((event.ctrlKey || event.metaKey) && !event.altKey && event.key.toLowerCase() === "k") { event.preventDefault(); setOpen(value => !value); }
    };
    window.addEventListener("keydown", toggle, true);
    return () => window.removeEventListener("keydown", toggle, true);
  }, []);
  useEffect(() => { if (!open) { setQuery(""); setActive(0); } }, [open]);
  const shown = useMemo(() => {
    const words = query.trim().toLocaleLowerCase().split(/\s+/).filter(Boolean);
    return commands.filter(command => words.every(word => `${command.label} ${command.group} ${command.keywords ?? ""}`.toLocaleLowerCase().includes(word)));
  }, [commands, query]);
  useEffect(() => setActive(0), [query]);
  useEffect(() => { list.current?.querySelector<HTMLElement>("[aria-selected='true']")?.scrollIntoView({ block: "nearest" }); }, [active]);
  const run = (command: PaletteCommand | undefined) => { if (!command) return; setOpen(false); window.setTimeout(command.run, 0); };
  return <TxDialog open={open} onOpenChange={setOpen} title="" label="Commands" className="talyx-palette" footer={<></>}>
    <TxSearch value={query} onValueChange={setQuery} placeholder="Search commands and workspaces" aria-label="Search commands" autoFocus
      onKeyDown={event => {
        if (event.key === "ArrowDown" || event.key === "ArrowUp") {
          event.preventDefault();
          setActive(value => (value + (event.key === "ArrowDown" ? 1 : -1) + shown.length) % Math.max(1, shown.length));
        } else if (event.key === "Enter") { event.preventDefault(); run(shown[active]); }
      }} />
    <div ref={list} className="talyx-palette-list" role="listbox" aria-label="Commands">
      {shown.length === 0 && <div className="talyx-palette-none">Nothing matches “{query.trim()}”.</div>}
      {shown.map((command, index) => <PaletteRow key={command.id} command={command} heading={index === 0 || shown[index - 1].group !== command.group}
        active={index === active} onHover={() => setActive(index)} onRun={() => run(command)} />)}
    </div>
  </TxDialog>;
}

function PaletteRow({ command, heading, active, onHover, onRun }: { command: PaletteCommand; heading: boolean; active: boolean; onHover: () => void; onRun: () => void }) {
  return <>
    {heading && <div className="talyx-palette-group" role="presentation">{command.group}</div>}
    <TxButton variant="row" role="option" aria-selected={active} tabIndex={-1} onMouseMove={onHover} onClick={onRun}>
      {command.module ? <ModuleIcon module={command.module} /> : command.icon && <TxIcon name={command.icon} />}
      <span className="talyx-palette-name">{command.label}</span>
      {command.shortcut && <kbd>{command.shortcut}</kbd>}
    </TxButton>
  </>;
}

/* ── The workspace's folder ────────────────────────────────────────────────────────────────── */

function FolderDialog({ workspaceId, name, onClose }: { workspaceId: string; name: string; onClose: () => void }) {
  const [path, setPath] = useState<string | null>(null), [next, setNext] = useState(""), [error, setError] = useState<string | null>(null), [saving, setSaving] = useState(false);
  useEffect(() => {
    platformRuntime().platform["workspace.folder"]({ workspaceId }).then(value => { setPath(value.path); setNext(value.path); }).catch(failure => setError(String(failure)));
  }, [workspaceId]);
  const save = async () => {
    setSaving(true); setError(null);
    try { await platformRuntime().platform["workspace.set_folder"]({ workspaceId, path: next.trim() }); onClose(); }
    catch (failure) { setError(failure instanceof Error ? failure.message : String(failure)); }
    finally { setSaving(false); }
  };
  return <TxDialog open onOpenChange={value => { if (!value) onClose(); }} title={`${name} folder`} className="talyx-shell-dialog" footer={<>
    <TxButton onClick={onClose}>Cancel</TxButton>
    <TxButton variant="primary" loading={saving} disabled={!next.trim() || next.trim() === path} onClick={() => void save()}>Use this folder</TxButton>
  </>}>
    <TxField label="Folder" error={error ?? undefined}>
      <TxInput className="talyx-mono-input" spellCheck={false} value={next} disabled={path === null} onChange={event => setNext(event.target.value)} />
    </TxField>
  </TxDialog>;
}

export default function WorkspaceCommandHeader(props: WorkspaceCommandHeaderProps) {
  const { name, saving, workspaceId, onError, onNotice } = props;
  const latest = useRef(props);
  latest.current = props;
  const [folder, setFolder] = useState(false);
  const [menu, setMenu] = useState<{ x: number; y: number } | null>(null);
  const tabAnchor = () => document.querySelector<HTMLElement>(".talyx-ws-tab[aria-pressed='true']");
  const copyAiAddress = () => {
    void (async () => {
      const session = platformRuntime().bridge.getSessionId();
      if (!session) throw new Error("The workspace is disconnected");
      const response = await fetch("/api/platform/ai/connection", { headers: { "x-talyx-session": session } });
      if (!response.ok) throw new Error("The AI connection address is unavailable");
      const value = await response.json() as { url: string };
      await navigator.clipboard.writeText(value.url);
      onNotice("AI connection address copied");
    })().catch(error => onError(String(error)));
  };
  const stopAiWork = () => {
    void stopWorkspaceAI(workspaceId).then(() => onNotice("AI work in this workspace stopped")).catch(error => onError(String(error)));
  };

  const tabKey = props.tabs.map(tab => `${tab.id}:${tab.name}`).join("|");
  const moduleKey = props.modules.map(item => `${item.kind}:${item.failed}`).join("|");
  const commands = useMemo((): PaletteCommand[] => {
    const call = (pick: (value: WorkspaceCommandHeaderProps) => void) => () => pick(latest.current);
    const current = latest.current;
    return [
      { id: "save", group: "This workspace", label: "Save", icon: "download", shortcut: "Ctrl S", run: call(value => value.onSave()) },
      { id: "rename", group: "This workspace", label: "Rename…", icon: "edit", keywords: "name", run: call(value => value.onStartRename()) },
      { id: "folder", group: "This workspace", label: "Folder…", icon: "folder", run: () => setFolder(true) },
      { id: "ai", group: "This workspace", label: "Copy AI connection address", icon: "copy", keywords: "mcp", run: copyAiAddress },
      { id: "window", group: "This workspace", label: "Open in new window", icon: "external", keywords: "screen monitor", run: call(value => value.onOpenNewWindow()) },
      { id: "close", group: "This workspace", label: "Close workspace", icon: "close", run: call(value => value.onCloseWorkspace()) },
      ...current.modules.filter(item => !item.failed).map(item => ({
        id: `add.${item.kind}`, group: "Add module", label: item.name, module: item.kind, keywords: "add module panel", run: call(value => value.onAddModuleKind(item)),
      })),
      ...current.tabs.filter(tab => tab.id !== current.workspaceId).map(tab => ({
        id: `go.${tab.id}`, group: "Workspaces", label: tab.name, icon: "grid" as IconName, keywords: "switch workspace", run: call(value => value.onSelectWorkspace(tab)),
      })),
      { id: "launcher", group: "Workspaces", label: "New or saved workspace…", icon: "plus", keywords: "open saved new", run: call(value => value.onOpenLauncher()) },
      { id: "home", group: "Go to", label: "Home", icon: "arrow-left", run: call(value => value.onBack()) },
      { id: "community", group: "Go to", label: "Community", icon: "globe", keywords: "modules install discover", run: call(value => value.onCommunity()) },
      { id: "settings", group: "Go to", label: "Settings", icon: "settings", keywords: "preferences options", run: () => openPlatformSettings() },
    ];
  }, [tabKey, moduleKey]);

  return (
    <>
      <ShellTopBar
        tabs={props.tabs}
        currentId={workspaceId}
        attention={props.attention}
        settingsAlert={props.settingsAlert}
        onHome={props.onBack}
        onCommunity={props.onCommunity}
        onFault={props.onFault}
        onSelectWorkspace={props.onSelectWorkspace}
        onWorkspaceMenu={anchor => setMenu(below(anchor))}
        addWorkspace={props.addWorkspace}
      />
      <WorkspaceMenu name={name} point={menu} onOpenChange={open => { if (!open) setMenu(null); }} actions={{
        saveBusy: saving,
        aiWork: props.tabs.find(tab => tab.id === workspaceId)?.aiWork ?? 0,
        onStopAiWork: stopAiWork,
        // Opened only after the menu has closed and handed focus back to the capsule (a task the menu queues as it
        // goes); opened sooner, that focus lands outside the picker and dismisses it.
        onAddModule: () => { const anchor = tabAnchor(); window.setTimeout(() => window.setTimeout(() => latest.current.onAddModule(anchor), 0), 0); },
        onSave: props.onSave,
        onRename: props.onStartRename,
        onFolder: () => setFolder(true),
        onCopyAiAddress: copyAiAddress,
        onOpenNewWindow: props.onOpenNewWindow,
        onClose: props.onCloseWorkspace,
      }} />
      <ShellNameDialog open={props.nameMode !== null} mode={props.nameMode ?? "save"} value={props.nameInput} busy={saving}
        onValue={props.onNameInput} onCancel={props.onCancelName} onCommit={props.onCommitName} />
      {folder && <FolderDialog workspaceId={workspaceId} name={name} onClose={() => setFolder(false)} />}
      <CommandPalette commands={commands} />
    </>
  );
}
