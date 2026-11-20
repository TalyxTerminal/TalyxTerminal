import { useEffect, useMemo, useRef, useState } from "react";
import { TxAlert, TxButton, TxDetails, TxIcon, TxSearch, TxSkeleton, TxStatus, TxTooltip } from "@talyx/sdk";
import { openPlatformSettings } from "./PlatformControls";
import { useSavedWorkspaces, workspacesUsing, type ModuleEntry } from "./communityData";
import { InstallDialog, StopDialog, useModuleManagement } from "./communityManage";
import { Linked, Tile, failureLine, matches } from "./communityParts";

/* Installed: every module on this computer. A narrow list on the left (the ones that couldn't start first),
   the picked module on the right: what is wrong with it, its version, what it adds, and managing it. */

export function Installed({ modules, error, onChanged, focus }: {
  modules: ModuleEntry[] | null;
  error: string | null;
  onChanged: () => void;
  focus: string | null;
}) {
  const [query, setQuery] = useState("");
  const ordered = useMemo(() => [...modules ?? []].sort((a, b) =>
    Number(b.state === "failed") - Number(a.state === "failed") || a.name.localeCompare(b.name)), [modules]);
  const [picked, setPicked] = useState<string | null>(focus);
  const [installing, setInstalling] = useState(false);
  const manage = useModuleManagement(onChanged, setPicked);
  const list = useRef<HTMLDivElement>(null);
  const saved = useSavedWorkspaces(modules);
  const shown = ordered.filter(item => matches(query, item.name, item.official ? "TALYX" : "Third-party"));
  const current = ordered.find(item => item.id === picked) ?? ordered[0] ?? null;

  // The module the logo pointed at is in view in the list.
  useEffect(() => {
    list.current?.querySelector(`[data-module="${CSS.escape(current?.id ?? "")}"]`)?.scrollIntoView({ block: "nearest" });
  }, [current?.id]);

  return <div className="cm-installed">
    <aside className="cm-mods" aria-label="Installed modules">
      <div className="cm-mods-tools">
        <TxButton className="cm-mods-file" onClick={() => setInstalling(true)}><TxIcon name="download-box" />Install from file…</TxButton>
        <TxSearch value={query} onValueChange={setQuery} aria-label="Search installed" placeholder="Search installed" />
      </div>
      <div className="cm-mods-list" role="listbox" aria-label="Modules" ref={list}>
        {modules === null && !error && Array.from({ length: 6 }, (_, index) => <div key={index} className="cm-mod-row cm-mod-skeleton" aria-hidden="true">
          <TxSkeleton width="60%" />
        </div>)}
        {shown.map(item => <TxButton key={item.id} variant="row" role="option" data-module={item.id} className="cm-mod-row"
          aria-selected={item.id === current?.id} onClick={() => setPicked(item.id)}>
          <Tile module={item.id} />
          <span className="cm-mod-name">{item.name}</span>
          {item.state === "failed" && <span className="cm-dot" role="img" aria-label="Couldn't start" />}
          <span className="cm-mod-version">{item.state === "disabled" ? "Disabled" : item.version}</span>
        </TxButton>)}
        {modules !== null && !shown.length && query.trim() && <div className="cm-nothing">No module matches “{query.trim()}”</div>}
      </div>
    </aside>

    {error && !modules && <section className="cm-mod" aria-label="Modules unavailable">
      <TxAlert title="The module list couldn't be read" actions={<TxButton size="sm" onClick={onChanged}><TxIcon name="refresh" />Retry</TxButton>}>{error}</TxAlert>
    </section>}

    {current && <ModuleDetail key={current.id} item={current} used={workspacesUsing(saved, current.id)} manage={manage} />}

    <StopDialog stopping={manage.stopping} busy={manage.busy !== null} onCancel={() => manage.setStopping(null)} onConfirm={() => void manage.stop()} />
    <InstallDialog open={installing} onOpenChange={setInstalling} onInstalled={id => { onChanged(); setPicked(id); setQuery(""); }} />
  </div>;
}

/** A button that can't be used while other modules need this one; the tooltip names them. */
function NeededBy({ item, children }: { item: ModuleEntry; children: React.ReactElement }) {
  if (!item.dependents.length) return children;
  return <TxTooltip label={`Needed by ${item.dependents.join(", ")}`}><span className="talyx-tip-target" tabIndex={0}>{children}</span></TxTooltip>;
}

function ModuleDetail({ item, used, manage }: { item: ModuleEntry; used: number; manage: ReturnType<typeof useModuleManagement> }) {
  const busy = manage.busy !== null, mine = manage.busy === item.id;
  return <section className="cm-mod" aria-label={item.name}>
    {item.state === "failed" && <TxAlert title={`${item.name} couldn't start`} actions={<>
      <TxButton size="sm" variant="primary" loading={mine} disabled={busy} onClick={() => void manage.retry(item)}>Retry</TxButton>
      {item.reason && item.reason !== failureLine(item.reason) && <TxDetails label="Full output">{item.reason}</TxDetails>}</>}>
      {item.reason && failureLine(item.reason)}
    </TxAlert>}

    <header className="cm-mod-head">
      <Tile module={item.id} size="lg" />
      <div className="cm-item-title">
        <h1>{item.name}</h1>
        <div className="cm-item-meta">
          {item.official ? <span className="cm-author">TALYX<TxIcon name="verified" /></span> : <span>Third-party</span>}
          <span className="cm-mono">{item.version}</span>
          {item.state === "starting" && <TxStatus tone="busy" label="Starting" />}
          {item.state === "disabled" && <TxStatus tone="off" label="Disabled" />}
        </div>
      </div>
      <div className="cm-item-actions">
        {item.settings && item.enabled && <TxButton onClick={() => openPlatformSettings(item.settings!)}><TxIcon name="settings" />Settings</TxButton>}
        {item.readable && (item.enabled
          ? <>
            <TxButton loading={mine} disabled={busy} onClick={() => void manage.reload(item)}><TxIcon name="refresh" />Reload</TxButton>
            <NeededBy item={item}><TxButton disabled={busy || item.dependents.length > 0} onClick={() => void manage.askStop(item, "disable")}>Disable…</TxButton></NeededBy>
          </>
          : <TxButton variant="primary" loading={mine} disabled={busy} onClick={() => void manage.enable(item)}>Enable</TxButton>)}
        {!item.official && <NeededBy item={item}>
          <TxButton disabled={busy || item.dependents.length > 0} onClick={() => void manage.askStop(item, "uninstall")}><TxIcon name="trash" />Uninstall…</TxButton>
        </NeededBy>}
      </div>
    </header>
    {item.summary && <p className="cm-mod-summary"><Linked text={item.summary} /></p>}

    <section className="cm-mod-section" aria-label="Panels">
      <h2>Panels<span className="cm-count">{item.panels.length || ""}</span></h2>
      {item.panels.length
        ? <div className="cm-chips">{item.panels.map(panel => <span key={panel} className="cm-chip"><TxIcon name="columns" />{panel}</span>)}</div>
        : <p className="cm-faint">None</p>}
    </section>

    <section className="cm-mod-section" aria-label="AI actions">
      <h2>AI actions<span className="cm-count">{item.actions.length || ""}</span></h2>
      {item.actions.length
        ? <div className="cm-actions">{item.actions.map(action => <div key={action.name} className="cm-action">
          <code>{action.name}</code>
          <span>{action.title}</span>
        </div>)}</div>
        : <p className="cm-faint">None</p>}
    </section>

    <section className="cm-mod-section" aria-label="Use">
      <h2>On this computer</h2>
      <dl className="cm-facts cm-facts-wide">
        {item.requires.length > 0 && <div><dt>Needs</dt><dd>{item.requires.join(", ")}</dd></div>}
        <div><dt>Used in</dt><dd>{used ? `${used} saved workspace${used > 1 ? "s" : ""}` : "No saved workspace"}</dd></div>
      </dl>
    </section>
  </section>;
}
