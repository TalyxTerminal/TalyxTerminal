import { lazy, Suspense, useCallback, useEffect, useState } from "react";
import { TxIcon, useTxToast } from "@talyx/sdk";
import { ExperienceShellStyles } from "./ExperienceShellStyles";
import { HomeStone } from "./HomeStone";
import { ModuleIcon } from "./ModuleIcon";
import { ShellHomeBar, type HomeBarTarget } from "./ShellTopBar";
import { useShellAlerts, type ShellAttention } from "./shellChrome";
import { SavedWorkspaceDialogs, useSavedWorkspaces, type SavedWorkspace } from "./savedWorkspaces";
import { workspaceWindowCommand } from "./workspaceWindows";
import { moduleLine, moduleName, savedWhen, type LayoutTile } from "./workspaceSummary";
import { freshPanel } from "./workspacePersistence";
import type { WorkspaceState } from "./workspaceChanges";
import { platformRuntime } from "../kernel/runtime";
import "./HomeScreen.css";

const ManagerScreen = lazy(() => import("./ManagerScreen"));
const CommunityScreen = lazy(() => import("./CommunityScreen"));

type Screen = "home" | "manager" | "community";

/** The modules on this computer that offer a panel to open: Community's plane and its count. */
function moduleTiles(): { id: string; name: string }[] {
  return [...platformRuntime().kernel.definitions.values()]
    .filter(item => item.id !== "platform" && Object.values(item.manifest.panels ?? {}).some(panel => panel.launcher))
    .map(item => ({ id: item.id, name: item.name })).sort((a, b) => a.name.localeCompare(b.name));
}

/** The saved layout drawn the way a workspace looks: 1px lines between panels, a tab with each panel's module. */
function Miniature({ tiles }: { tiles: LayoutTile[] | null }) {
  return <span className="talyx-home-mini" aria-hidden="true">
    {tiles?.map((tile, index) => {
      const left = tile.x > 0 ? 1 : 0, top = tile.y > 0 ? 1 : 0;
      return <span className="talyx-home-mini-panel" key={index} style={{
        left: `calc(${tile.x * 100}% + ${left}px)`, top: `calc(${tile.y * 100}% + ${top}px)`,
        width: `calc(${tile.w * 100}% - ${left}px)`, height: `calc(${tile.h * 100}% - ${top}px)`,
      }}>
        <span className="talyx-home-mini-tab"><span><ModuleIcon module={tile.module} />{moduleName(tile.module)}</span></span>
        <span className="talyx-home-mini-body"><ModuleIcon module={tile.module} /></span>
      </span>;
    })}
  </span>;
}

const cities = [["New York", "America/New_York"], ["London", "Europe/London"], ["Frankfurt", "Europe/Berlin"],
  ["Hong Kong", "Asia/Hong_Kong"], ["Tokyo", "Asia/Tokyo"], ["Sydney", "Australia/Sydney"]] as const;

/** Minutes the zone is ahead of UTC at this instant. */
function offsetMinutes(zone: string, at: Date) {
  const parts = Object.fromEntries(new Intl.DateTimeFormat("en-US", { timeZone: zone, hourCycle: "h23", year: "numeric", month: "2-digit",
    day: "2-digit", hour: "2-digit", minute: "2-digit" }).formatToParts(at).map(part => [part.type, part.value]));
  return Math.round((Date.UTC(+parts.year, +parts.month - 1, +parts.day, +parts.hour, +parts.minute) - at.getTime()) / 60_000);
}
const signed = (minutes: number) => {
  const size = Math.abs(minutes), rest = size % 60;
  return `${minutes < 0 ? "−" : "+"}${Math.floor(size / 60)}${rest ? `:${String(rest).padStart(2, "0")}` : ""}`;
};

/** Local time with small seconds, the date and zone, and six cities' times with their offset from here. */
function Clock() {
  const [now, setNow] = useState(() => new Date());
  useEffect(() => {
    let timer = 0;
    const next = () => { setNow(new Date()); timer = window.setTimeout(next, 1000 - Date.now() % 1000); };
    timer = window.setTimeout(next, 1000 - Date.now() % 1000);
    return () => window.clearTimeout(timer);
  }, []);
  const local = Intl.DateTimeFormat().resolvedOptions().timeZone;
  const minute = new Date(Math.floor(now.getTime() / 60_000) * 60_000), here = offsetMinutes(local, minute);
  const [hours, minutes, seconds] = new Intl.DateTimeFormat("en-GB", { hour: "2-digit", minute: "2-digit", second: "2-digit", hourCycle: "h23" }).format(now).split(":");
  const today = now.toLocaleDateString("en-CA", { timeZone: local });
  return <section className="talyx-home-clock" aria-label="Time">
    <div className="talyx-home-now"><span className="talyx-home-hm">{hours}:{minutes}</span><span className="talyx-home-sec">{seconds}</span></div>
    <div className="talyx-home-today">
      <span className="talyx-home-date">{now.toLocaleDateString("en-GB", { weekday: "long", day: "numeric", month: "long" }).replace(" ", ", ")}</span>
      <span className="talyx-home-zone">{local.split("/").pop()!.replace(/_/g, " ")} · UTC{signed(here)}</span>
    </div>
    <div className="talyx-home-zones">{cities.map(([name, zone]) => {
      const day = now.toLocaleDateString("en-CA", { timeZone: zone }), ahead = offsetMinutes(zone, minute) - here;
      const note = day !== today ? (day < today ? "Yesterday" : "Tomorrow") : ahead === 0 ? "Same time" : `${signed(ahead)}h`;
      return <div className="talyx-home-city" key={zone}>
        <span className="talyx-home-city-name">{name}</span>
        <span className="talyx-home-city-time">
          <span>{new Intl.DateTimeFormat("en-GB", { timeZone: zone, hour: "2-digit", minute: "2-digit", hourCycle: "h23" }).format(now)}</span>
          <span className="talyx-home-city-note">{note}</span>
        </span>
      </div>;
    })}</div>
  </section>;
}

function HomeScreen({ recent, onOpenRecent, onNew, onOpenLayouts, onOpenCommunity }: {
  recent: SavedWorkspace | null; onOpenRecent: () => void; onNew: () => void; onOpenLayouts: () => void; onOpenCommunity: () => void;
}) {
  const modules = moduleTiles();
  const tiles = recent?.summary?.tiles ?? null;
  const saved = recent && savedWhen(recent.savedAt);
  return <div className="talyx-home">
    <HomeStone />
    <Clock />
    <div className="talyx-home-cards">
      <section className="talyx-home-card" aria-label={recent ? "Recent workspace" : "Workspaces"}>
        <button type="button" className="talyx-home-head" onClick={onOpenLayouts}>
          <span className="talyx-home-head-title">{recent ? "Recent workspace" : "Workspaces"}</span>
          <span className="talyx-home-all">All workspaces<TxIcon name="chevron" /></span>
        </button>
        {recent && saved
          ? <button type="button" className="talyx-home-body" aria-label={`Open ${recent.name}`} onClick={onOpenRecent}>
            <Miniature tiles={tiles} />
            <span>
              <span className="talyx-home-foot"><span className="talyx-home-name">{recent.name}</span><span className="talyx-home-saved" title={saved.full}>{saved.label}</span></span>
              <span className="talyx-home-modules">{tiles ? moduleLine(tiles) : ""}</span>
            </span>
          </button>
          : <button type="button" className="talyx-home-body" data-new="" onClick={onNew}>
            <span className="talyx-home-new">
              <span className="talyx-home-plus"><TxIcon name="plus" /></span>
              <b>New workspace</b><span>Start with an empty layout</span>
            </span>
          </button>}
      </section>
      <button type="button" className="talyx-home-card talyx-home-community" aria-label="Open Community" onClick={onOpenCommunity}>
        <span className="talyx-home-plane-clip" aria-hidden="true"><span className="talyx-home-plane">
          {modules.length > 0 && Array.from({ length: 80 }, (_, index) =>
            <span className="talyx-home-tile" key={index}><ModuleIcon module={modules[(index * 3 + Math.floor(index / 10)) % modules.length].id} /></span>)}
        </span></span>
        <span className="talyx-home-community-text">
          <span className="talyx-home-community-title">Community<TxIcon name="arrow-up-right" /></span>
          <span className="talyx-home-community-sub">Discover modules · {modules.length} installed</span>
        </span>
      </button>
    </div>
  </div>;
}

/** The home window: Home, all workspaces and Community. Each open workspace is a frame beside it (workspaceWindows). */
export function HomeWindow() {
  const toast = useTxToast();
  const say = useCallback((title: string, failed = false) => toast({ title, tone: failed ? "failed" : "info" }), [toast]);
  const [screen, setScreen] = useState<Screen>("home");
  const catalog = useSavedWorkspaces();
  const [renaming, setRenaming] = useState<SavedWorkspace | null>(null);
  const [deleting, setDeleting] = useState<SavedWorkspace | null>(null);
  const alerts = useShellAlerts();

  const goHome = useCallback(() => setScreen("home"), []);
  const goManager = useCallback(() => { setScreen("manager"); void catalog.refresh(); }, [catalog.refresh]);
  const [communityFocus, setCommunityFocus] = useState<string | null>(null);
  const openCommunity = useCallback((id: string | null) => { setCommunityFocus(id); setScreen("community"); }, []);
  const goCommunity = () => openCommunity(null);
  useEffect(() => {
    document.title = "TALYX";
    // Leaving the last workspace lands on Workspaces; a workspace's TALYX menu leads to Home or Community.
    window.addEventListener("talyx:workspace-manager", goManager);
    const community = (event: Event) => openCommunity((event as CustomEvent<string | null>).detail);
    window.addEventListener("talyx:shell-community", community);
    window.addEventListener("talyx:shell-home", goHome);
    return () => {
      window.removeEventListener("talyx:workspace-manager", goManager);
      window.removeEventListener("talyx:shell-community", community);
      window.removeEventListener("talyx:shell-home", goHome);
    };
  }, [openCommunity, goHome, goManager]);

  const open = (snapshotId: string | null, name: string, presentation: WorkspaceState | null) =>
    workspaceWindowCommand({ kind: "open", snapshotId, name, presentation }).catch(reason => say(String(reason), true));
  const openSaved = (item: SavedWorkspace) => void open(item.snapshotId, item.name, null);
  const openNew = (name: string, panels: WorkspaceState["panels"] = []) => void open(null, name, {
    workspaceId: crypto.randomUUID(), snapshotId: null, snapshotRevision: null, name, panels, activePanelId: panels[0]?.id ?? null,
    dockLayout: null, changes: panels.length ? 1 : 0, savedChanges: 0,
  });
  const openModule = (id: string) => {
    try {
      const panel = freshPanel(id);
      openNew(`${platformRuntime().kernel.definitions.get(id)!.name} workspace`, [panel]);
    } catch (reason) { say(`Module not opened: ${reason instanceof Error ? reason.message : String(reason)}`, true); }
  };
  const openFault = (item: ShellAttention) => openCommunity(item.id);
  const navigate = (target: HomeBarTarget) => {
    if (target === "home") goHome();
    if (target === "workspace") goManager();
    if (target === "community") goCommunity();
  };

  return <div data-screen={screen} id="talyx-layout-manager-candidate">
    <ExperienceShellStyles />
    <div className="product">
      <ShellHomeBar active={screen === "manager" ? "workspace" : screen} attention={alerts.faults} settingsAlert={alerts.settings}
        onNavigate={navigate} onFault={openFault} />
      <SavedWorkspaceDialogs rename={renaming} remove={deleting} say={say}
        onClose={() => { setRenaming(null); setDeleting(null); }}
        onRenamed={() => { say("Workspace renamed."); void catalog.refresh(); }}
        onDeleted={() => { say("Workspace deleted."); void catalog.refresh(); }} />
      <main>
        <section className={`screen${screen === "home" ? " is-active" : ""}`}>
          <HomeScreen recent={catalog.saved[0] ?? null} onOpenLayouts={goManager} onOpenCommunity={goCommunity}
            onOpenRecent={() => openSaved(catalog.saved[0])} onNew={() => openNew("Untitled")} />
        </section>
        <section className={`screen${screen === "manager" ? " is-active" : ""}`}>
          {screen === "manager" && <Suspense fallback={null}>
            <ManagerScreen saved={catalog.saved} phase={catalog.phase} error={catalog.error} onRetry={() => void catalog.refresh()}
              onOpen={openSaved} onCreate={() => openNew("Untitled")} onRename={setRenaming} onDelete={setDeleting} />
          </Suspense>}
        </section>
        <section className={`screen${screen === "community" ? " is-active" : ""}`}>
          {screen === "community" && <Suspense fallback={null}><CommunityScreen key={communityFocus} initialFocus={communityFocus} onOpenModule={openModule} /></Suspense>}
        </section>
      </main>
    </div>
  </div>;
}
