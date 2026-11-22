/* Settings a module (or the platform) declares, drawn by the platform from the declaration: one row per setting, in
   the global layer or one workspace's. A workspace shows each setting as following global or as its own value with
   "Reset to global"; global-only settings follow global there and cannot be changed. */
import { useCallback, useEffect, useState } from "react";
import { translator, TxButton, TxInput, TxRow, TxSegmented, TxSelect, TxSettingsGroup, TxSettingsNote, TxSwitch, TxTooltip, type Json, type Translate } from "@talyx/sdk";
import { platformRuntime } from "../kernel/runtime";
import { t } from "./i18n";

export type SettingDeclaration = { title: string; description: string; globalOnly: boolean; group: string; schema: Record<string, Json>; default: Json; options?: Record<string, string> };
type Value = { value: Json; source: "default" | "global" | "workspace"; globalOnly: boolean };

/** The settings a module declares in its contract, in declaration order. */
export function declaredSettings(moduleId: string): [string, SettingDeclaration][] {
  return Object.entries(platformRuntime().kernel.definitions.get(moduleId)?.contract.settings ?? {}) as [string, SettingDeclaration][];
}

const label = (value: string) => { const words = value.replace(/[-_]+/g, " "); return words.charAt(0).toUpperCase() + words.slice(1); };

function Control({ name, spec, words, value, disabled, onChange }: {
  name: string; spec: SettingDeclaration; words: Translate; value: Json; disabled: boolean; onChange: (value: Json) => void;
}) {
  const schema = spec.schema;
  const [draft, setDraft] = useState(String(value ?? ""));
  useEffect(() => setDraft(String(value ?? "")), [value]);
  const options = Array.isArray(schema.enum) ? schema.enum as string[] : null;
  // What a value reads as: the declaration's words for it, else the value in words; in the declaring module's language.
  const optionLabel = (option: string) => words(spec.options?.[option] ?? label(option));
  if (schema.type === "boolean") return <TxSwitch hideLabel label={name} checked={value === true} disabled={disabled} onCheckedChange={onChange} />;
  if (options && options.length <= 4) {
    return <TxSegmented label={name} value={String(value)} onChange={next => { if (!disabled) onChange(next); }}
      options={options.map(option => ({ value: option, label: optionLabel(option), disabled: disabled && option !== value }))} />;
  }
  if (options) {
    return <TxSelect aria-label={name} value={String(value)} disabled={disabled} onChange={event => onChange(event.target.value)}>
      {options.map(option => <option key={option} value={option}>{optionLabel(option)}</option>)}
    </TxSelect>;
  }
  const numeric = schema.type === "integer" || schema.type === "number";
  const commit = () => {
    if (draft === String(value ?? "")) return;
    onChange(numeric ? Number(draft) : draft);
  };
  return <TxInput aria-label={name} type={numeric ? "number" : "text"} value={draft} disabled={disabled}
    onChange={event => setDraft(event.target.value)} onBlur={commit} onKeyDown={event => { if (event.key === "Enter") commit(); }} />;
}

/** One module's declared settings as they apply in `workspaceId` ("global" for the global layer), under the
    headings their declarations name. */
export function DeclaredSettings({ moduleId, workspaceId }: { moduleId: string; workspaceId: string }) {
  const runtime = platformRuntime();
  const declared = declaredSettings(moduleId);
  const words = translator(moduleId);   // the declaration is the module's English, translated with its dictionary
  const [values, setValues] = useState<Record<string, Value> | null>(null);
  const [error, setError] = useState<string | null>(null);
  const reason = (failure: unknown) => failure instanceof Error ? failure.message : String(failure);

  const read = useCallback(async () => {
    try {
      const result = await runtime.bridge.call("platform.settings.read", { module: moduleId, workspaceId }) as { values: Record<string, Value> };
      setValues(result.values); setError(null);
    } catch (failure) { setError(reason(failure)); }
  }, [runtime, moduleId, workspaceId]);

  useEffect(() => {
    void read();
    let closed = false, close: (() => Promise<void>) | null = null;
    void runtime.bridge.subscribe("platform.settings.changes", {}, item => {
      const change = item as { module: string; workspaceId: string };
      if (change.module === moduleId && (change.workspaceId === "global" || change.workspaceId === workspaceId)) void read();
    }).then(subscription => { if (closed) void subscription.close(); else close = subscription.close; }, () => undefined);
    return () => { closed = true; void close?.(); };
  }, [runtime, moduleId, workspaceId, read]);

  const write = async (key: string, value: Json) => {
    setValues(current => current && { ...current, [key]: { ...current[key], value, source: workspaceId === "global" ? "global" : "workspace" } });
    try { await runtime.bridge.call("platform.settings.write", { module: moduleId, workspaceId, values: { [key]: value } }); setError(null); }
    catch (failure) { setError(reason(failure)); }
    await read();
  };
  const reset = async (key: string) => {
    try { await runtime.bridge.call("platform.settings.reset", { module: moduleId, workspaceId, keys: [key] }); setError(null); }
    catch (failure) { setError(reason(failure)); }
    await read();
  };

  if (!declared.length) return null;
  const inWorkspace = workspaceId !== "global";
  const groups = new Map<string, [string, SettingDeclaration][]>();
  for (const entry of declared) groups.set(entry[1].group, [...groups.get(entry[1].group) ?? [], entry]);
  const row = ([key, spec]: [string, SettingDeclaration]) => {
    const current = values![key];
    const own = inWorkspace && current.source === "workspace";
    const title = words(spec.title);
    const name = spec.description
      ? <TxTooltip label={words(spec.description)}><span className="talyx-tip-target" tabIndex={0}>{title}</span></TxTooltip> : title;
    return <TxRow key={key} title={name} detail={inWorkspace ? (own ? undefined : spec.globalOnly ? t("Global only") : t("Following global")) : undefined}>
      <div className="tx-row tx-nowrap talyx-setting-control" data-setting={key} data-source={current.source}>
        {own && <TxButton size="sm" variant="quiet" onClick={() => void reset(key)}>{t("Reset to global")}</TxButton>}
        <Control name={title} spec={spec} words={words} value={current.value} disabled={inWorkspace && spec.globalOnly}
          onChange={value => void write(key, value)} />
      </div>
    </TxRow>;
  };
  if (!values) return <TxSettingsGroup>{error ? <TxSettingsNote tone="error">{error}</TxSettingsNote> : <TxSettingsNote>{t("Loading…")}</TxSettingsNote>}</TxSettingsGroup>;
  return <>
    {error && <TxSettingsNote tone="error">{error}</TxSettingsNote>}
    {[...groups].map(([group, entries]) => <TxSettingsGroup key={group} title={group ? words(group) : undefined}>{entries.map(row)}</TxSettingsGroup>)}
  </>;
}
