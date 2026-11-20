import { useState } from "react";
import { TxButton, TxDialog, TxField, TxInput, useTxToast } from "@talyx/sdk";
import { platformRuntime } from "../kernel/runtime";
import {
  disableModule, enableModule, installModule, openPanelsOf, reloadModule, retryModule, uninstallModule, type ModuleEntry,
} from "./communityData";

/* Installed's module management: install from a folder or zip file, enable, disable, reload, retry, uninstall.
   Stopping a module (disable, uninstall) asks first, saying how many of its panels are open; its frontend here
   may keep running when it has input it protects (onBeforeStop). */

const message = (reason: unknown) => reason instanceof Error ? reason.message : String((reason as { message?: unknown })?.message ?? reason);

export type Stopping = { module: ModuleEntry; how: "disable" | "uninstall"; panels: number };

export function useModuleManagement(onChanged: () => void, onPicked: (id: string | null) => void) {
  const toast = useTxToast();
  const [busy, setBusy] = useState<string | null>(null);
  const [stopping, setStopping] = useState<Stopping | null>(null);

  const run = async (item: ModuleEntry, work: () => Promise<unknown>, failed: string, done?: string) => {
    setBusy(item.id);
    try { await work(); if (done) toast({ title: done }); return true; }
    catch (reason) { toast({ title: failed, detail: message(reason), tone: "failed" }); return false; }
    finally { setBusy(null); onChanged(); }
  };
  const beforeStop = (item: ModuleEntry, reason: string) => platformRuntime().kernel.prepareStop(item.id, reason);

  return {
    busy, stopping, setStopping,
    retry: (item: ModuleEntry) => run(item, () => retryModule(item.id), `${item.name} still couldn't start`),
    enable: (item: ModuleEntry) => run(item, () => enableModule(item.id), `Couldn't enable ${item.name}`),
    reload: (item: ModuleEntry) => run(item, async () => { await beforeStop(item, "reload"); await reloadModule(item.id); },
      `Couldn't reload ${item.name}`, `Reloaded ${item.name}`),
    askStop: async (item: ModuleEntry, how: Stopping["how"]) => setStopping({ module: item, how, panels: await openPanelsOf(item.id) }),
    stop: async () => {
      const { module, how } = stopping!;
      const ok = await run(module, async () => {
        await beforeStop(module, how);
        await (how === "uninstall" ? uninstallModule(module.id) : disableModule(module.id));
      }, `Couldn't ${how} ${module.name}`, `${how === "uninstall" ? "Uninstalled" : "Disabled"} ${module.name}`);
      if (ok) { setStopping(null); if (how === "uninstall") onPicked(null); }
    },
  };
}

/** Asks before disabling or uninstalling: the module's open panels show that it isn't available until it is back. */
export function StopDialog({ stopping, busy, onCancel, onConfirm }: {
  stopping: Stopping | null; busy: boolean; onCancel: () => void; onConfirm: () => void;
}) {
  const verb = stopping?.how === "uninstall" ? "Uninstall" : "Disable";
  return <TxDialog open={stopping !== null} onOpenChange={next => { if (!next && !busy) onCancel(); }} title={stopping ? `${verb} ${stopping.module.name}?` : ""}
    footer={<>
      <TxButton disabled={busy} onClick={onCancel}>Cancel</TxButton>
      <TxButton variant="primary" loading={busy} onClick={onConfirm}>{verb}</TxButton>
    </>}>
    {stopping && stopping.panels > 0 && <div className="cm-remove">
      <p>{stopping.panels === 1 ? "Its open panel shows" : `Its ${stopping.panels} open panels show`} that the module isn't available
        {stopping.how === "uninstall" ? " until it is installed again." : " until it is enabled again."}</p>
    </div>}
  </TxDialog>;
}

/** Install from a folder holding talyx.module.json, or a zip file of one: the platform's own installation. */
export function InstallDialog({ open, onOpenChange, onInstalled }: {
  open: boolean; onOpenChange: (open: boolean) => void; onInstalled: (id: string) => void;
}) {
  const toast = useTxToast();
  const [path, setPath] = useState("");
  const [installing, setInstalling] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const install = async () => {
    setInstalling(true); setError(null);
    try {
      const installed = await installModule(path.trim());
      if (installed.state === "failed") toast({ title: `${installed.name} is installed but couldn't start`, detail: installed.problem ?? undefined, tone: "failed" });
      else toast({ title: `Installed ${installed.name} ${installed.version}` });
      setPath(""); onOpenChange(false); onInstalled(installed.id);
    } catch (reason) { setError(message(reason)); }
    finally { setInstalling(false); }
  };
  return <TxDialog open={open} onOpenChange={next => { if (!installing) onOpenChange(next); }} title="Install a module" className="talyx-shell-dialog" footer={<>
    <TxButton disabled={installing} onClick={() => onOpenChange(false)}>Cancel</TxButton>
    <TxButton variant="primary" loading={installing} disabled={!path.trim()} onClick={() => void install()}>Install</TxButton>
  </>}>
    <TxField label="Module folder or zip file" error={error ?? undefined}>
      <TxInput className="talyx-mono-input" spellCheck={false} autoFocus value={path} placeholder="D:\modules\my-module.zip"
        onChange={event => setPath(event.target.value)} onKeyDown={event => { if (event.key === "Enter" && path.trim() && !installing) void install(); }} />
    </TxField>
  </TxDialog>;
}
