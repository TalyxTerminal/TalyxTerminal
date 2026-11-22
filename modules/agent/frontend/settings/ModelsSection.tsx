import { t } from "../i18n";
import { useEffect, useMemo, useState } from "react";
import {
  TxButton, TxDialog, TxEmpty, TxField, TxIconButton, TxInput, TxMenu, TxSearch, TxSegmented, TxSelect, TxSettingsGroup, TxSettingsNote, TxSettingsPage, TxSwitch,
  type Json, type ModuleContext,
} from "@talyx/sdk";
import type { AgentClient } from "../data/client";
import type { AgentModel, AgentSettings, ModelConfiguration, ModelConnection } from "../data/types";
import { NATIVE_SEARCH, PROVIDERS, abilities, compact, modelName, priceText } from "./modelInfo";
import { SettingsData, errorText, useBusy, useTask } from "./shared";

type Kind = "official" | "local" | "relay";
/** What the user picked in the provider chooser: an official provider by id, or another kind of connection. */
type Choice = { kind: "official"; provider: string } | { kind: "local" | "relay" };

/* The chooser: the four US providers, the three Chinese ones, then this computer and anything else (0104). */
const OFFICIAL_TILES: { id: string; detail: string }[] = [
  { id: "anthropic", detail: t("Claude · its own web search") },
  { id: "openai", detail: t("GPT · its own web search") },
  { id: "google", detail: t("Gemini · its own web search") },
  { id: "xai", detail: t("Grok · its own web search") },
  { id: "deepseek", detail: t("DeepSeek's own search, same key") },
  { id: "moonshotai", detail: t("Kimi's own search, same key · China or international") },
  { id: "zhipuai", detail: t("GLM · Zhipu's own search, same key · China or international") },
];

export function ModelsSection({ ctx }: { ctx: ModuleContext }) {
  return <SettingsData ctx={ctx}>{(settings, client, refresh) => <Models settings={settings} client={client} refresh={refresh} />}</SettingsData>;
}

function Models({ settings, client, refresh }: { settings: AgentSettings; client: AgentClient; refresh: () => Promise<void> }) {
  const [adding, setAdding] = useState(false), [addModel, setAddModel] = useState<ModelConnection | null>(null);
  const [replacing, setReplacing] = useState<ModelConnection | null>(null), [editing, setEditing] = useState<AgentModel | null>(null);
  const actions = useBusy(), profiles = useTask();
  useEffect(() => { if (profiles.record?.state === "completed") void refresh(); }, [profiles.record]);
  const removeConnection = (connection: ModelConnection) => actions.run(connection.id, async () => {
    await client.settings("delete_connection", { id: connection.id });
    if (connection.secretRef) await client.settings("delete_credential", { reference: connection.secretRef });
    await refresh();
  });
  const removeModel = (model: AgentModel) => actions.run(model.id, async () => { await client.settings("delete_model", { id: model.id }); await refresh(); });
  return <TxSettingsPage data-settings-section="agent-models" lede={t("Connect the models Agent can use. Keys stay in Windows Credential Manager; each chat picks its own model.")}>
    <div className="agent-settings-toolbar">
      <TxButton size="sm" variant="primary" onClick={() => setAdding(true)}>{t("Add provider…")}</TxButton>
      <span className="agent-spacer" />
      <TxButton size="sm" variant="quiet" disabled={profiles.running} title={t("Windows, abilities, thinking levels and prices from models.dev. Also refreshed daily and when you add a provider.")}
        onClick={() => void profiles.start(signal => client.refreshProfiles(signal)).catch(error => actions.run("profiles", () => Promise.reject(error)))}>
        {profiles.running ? t("Refreshing model info…") : t("Refresh model info")}
      </TxButton>
    </div>
    {profiles.record?.state === "failed" && <p className="tx-error" role="alert">{t("Model info wasn't refreshed:")} {profiles.record.error?.message}</p>}
    {actions.error?.id === "profiles" && <p className="tx-error" role="alert">{actions.error.text}</p>}
    {!settings.connections.length && <TxSettingsGroup><TxEmpty title={t("No models yet")} text={t("Add a provider with your own key, a model on this computer or a relay.")} /></TxSettingsGroup>}
    {settings.connections.map(connection => {
      const models = settings.models.filter(model => model.connectionId === connection.id);
      return <TxSettingsGroup key={connection.id} title={connection.name} detail={connectionDetail(connection)} action={<>
        <TxButton size="sm" onClick={() => setAddModel(connection)}>{t("Add model…")}</TxButton>
        <TxMenu label={t("{name} actions", { name: connection.name })} align="end" trigger={<TxIconButton size="sm" label={t("{name} actions", { name: connection.name })} icon="more" disabled={actions.busy === connection.id} />} items={[
          ...(connection.secretRef ? [{ id: "key", label: connection.credential.configured ? t("Replace key…") : t("Add key…"), onSelect: () => setReplacing(connection) }] : []),
          { id: "remove", label: t("Remove connection"), tone: "danger" as const, disabled: models.length > 0, onSelect: () => void removeConnection(connection) },
        ]} />
      </>}>
        {actions.error?.id === connection.id && <TxSettingsNote tone="error">{actions.error.text}</TxSettingsNote>}
        {!models.length && <TxSettingsNote>{t("No models from this connection yet.")}</TxSettingsNote>}
        {models.map(model => <ModelRow key={model.id} model={model} connection={connection} client={client}
          busy={actions.busy === model.id} error={actions.error?.id === model.id ? actions.error.text : null}
          onEdit={() => setEditing(model)} onRemove={() => void removeModel(model)} />)}
      </TxSettingsGroup>;
    })}
    {adding && <ConnectionDialog settings={settings} client={client} onClose={() => setAdding(false)}
      onAdded={async connection => { setAdding(false); await refresh(); setAddModel(connection); }} />}
    {addModel && <AddModelDialog connection={addModel} settings={settings} client={client} onClose={() => setAddModel(null)}
      onAdded={async () => { setAddModel(null); await refresh(); }} />}
    {replacing && <KeyDialog connection={replacing} client={client} onClose={() => setReplacing(null)} onSaved={async () => { setReplacing(null); await refresh(); }} />}
    {editing && <EditModelDialog model={editing} connection={settings.connections.find(item => item.id === editing.connectionId)!} client={client}
      onClose={() => setEditing(null)} onSaved={async () => { setEditing(null); await refresh(); }} />}
  </TxSettingsPage>;
}

function connectionDetail(connection: ModelConnection): string {
  if (connection.kind === "local") return t("This computer · {url}", { url: connection.baseUrl });
  const key = connection.credential.configured ? t("Key {mask}", { mask: connection.credential.mask ?? "••••" }) : t("No key");
  return connection.kind === "relay" ? t("Relay · {protocol}-compatible · {url} · {key}", { protocol: connection.protocol === "anthropic" ? "Anthropic" : "OpenAI", url: connection.baseUrl, key }) : key;
}

/* ── A model and its connection test ─────────────────────── */

const CHECKS: [string, string][] = [["connection", t("Connects")], ["tools", t("Calls tools")], ["search", t("Built-in web search")]];
const CHECK_STATES: Record<string, string> = { passed: t("Yes"), failed: t("No"), not_tested: t("Not tested"), unsupported: t("Not offered") };

function ModelRow({ model, connection, client, busy, error, onEdit, onRemove }: {
  model: AgentModel; connection: ModelConnection; client: AgentClient; busy: boolean; error: string | null; onEdit: () => void; onRemove: () => void;
}) {
  const test = useTask(), [testError, setTestError] = useState<string | null>(null);
  // Without a price, spend is still counted in tokens (0106).
  const price = priceText(model) ?? t("No price · usage counted in tokens");
  const runTest = () => {
    setTestError(null);
    test.start(signal => client.testConnection(model.id, signal)).catch(failure => setTestError(errorText(failure)));
  };
  const record = test.record, checks = record?.state === "completed" ? (record.result as { checks: Record<string, { state: string; message?: string; code?: string }> }).checks : null;
  return <div className="agent-settings-item">
    <div className="agent-settings-row">
      <div className="agent-settings-copy">
        <span className="agent-settings-name">{modelName(model)}{modelName(model) !== model.model && <span className="agent-settings-mono"> {model.model}</span>}</span>
        <span className="agent-settings-detail">{[abilities(model), model.searchMode === "provider" ? t("Built-in web search") : null, price].filter(Boolean).join(" · ")}</span>
      </div>
      {test.running ? <TxButton size="sm" onClick={() => void test.cancel()}>{t("Cancel test")}</TxButton>
        : <TxButton size="sm" disabled={busy} onClick={runTest}>{t("Test")}</TxButton>}
      <TxMenu label={t("{modelName} actions", { modelName: modelName(model) })} align="end" trigger={<TxIconButton size="sm" label={t("{modelName} actions", { modelName: modelName(model) })} icon="more" disabled={busy || test.running} />} items={[
        { id: "edit", label: t("Edit…"), onSelect: onEdit },
        { id: "remove", label: t("Remove model"), tone: "danger", onSelect: onRemove },
      ]} />
    </div>
    {error && <TxSettingsNote tone="error">{error}</TxSettingsNote>}
    {testError && <TxSettingsNote tone="error">{testError}</TxSettingsNote>}
    {test.running && <TxSettingsNote>{t("Testing {name}… this sends a few short requests.", { name: connection.name })}</TxSettingsNote>}
    {record && record.state !== "running" && record.state !== "completed" && <TxSettingsNote tone={record.state === "cancelled" ? "muted" : "error"}>
      {record.state === "cancelled" ? t("Test cancelled; nothing was saved.") : record.error?.message ? t("Test failed: {reason}", { reason: record.error.message }) : t("Test failed.")}
    </TxSettingsNote>}
    {checks && <ul className="agent-checks" aria-label={t("Connection test")}>
      {CHECKS.map(([key, label]) => {
        const check = checks[key];
        return <li key={key} data-state={check.state}><span>{label}</span><span>{CHECK_STATES[check.state] ?? check.state}{check.message && check.state !== "passed" ? ` · ${check.message}` : ""}</span></li>;
      })}
    </ul>}
  </div>;
}

/* ── Dialogs ─────────────────────────────────────────────── */

function slug(text: string): string {
  return text.toLowerCase().replace(/[^a-z0-9]+/g, "-").replace(/^-|-$/g, "").slice(0, 40);
}

function uniqueId(base: string, taken: string[]): string {
  let id = base, index = 2;
  while (taken.includes(id)) id = `${base}-${index++}`;
  return id;
}

function ConnectionDialog({ settings, client, onClose, onAdded }: {
  settings: AgentSettings; client: AgentClient; onClose: () => void; onAdded: (connection: ModelConnection) => Promise<void>;
}) {
  const [choice, setChoice] = useState<Choice | null>(null);
  const form = useConnectionForm(choice ?? { kind: "relay" }, settings, client, onAdded);
  const title = !choice ? t("Add a model provider") : choice.kind === "official" ? t("Add {name}", { name: PROVIDERS.find(item => item.id === choice.provider)!.name })
    : choice.kind === "local" ? t("Add a model on this computer") : t("Add another service");
  return <TxDialog open onOpenChange={open => { if (!open) onClose(); }} title={title} size="standard"
    footer={choice ? <>
      <TxButton variant="quiet" onClick={() => setChoice(null)}>{t("Back")}</TxButton>
      <TxButton variant="primary" disabled={!form.ready || form.busy} onClick={() => void form.save()}>{form.busy ? t("Saving…") : t("Continue")}</TxButton>
    </> : <TxButton variant="quiet" onClick={onClose}>{t("Cancel")}</TxButton>}>
    {!choice ? <div className="agent-settings-form">
      <div className="agent-provider-grid" role="list" aria-label={t("Providers")}>
        {OFFICIAL_TILES.map(tile => <TxButton variant="row" key={tile.id} type="button" role="listitem" className="agent-provider"
          onClick={() => setChoice({ kind: "official", provider: tile.id })}>
          <span className="agent-provider-name">{PROVIDERS.find(item => item.id === tile.id)!.name}</span>
          <span className="agent-provider-detail">{tile.detail}</span>
        </TxButton>)}
      </div>
      <div className="agent-provider-grid" role="list" aria-label={t("Other connections")}>
        <TxButton variant="row" type="button" role="listitem" className="agent-provider" onClick={() => setChoice({ kind: "local" })}>
          <span className="agent-provider-name">{t("This computer")}</span>
          <span className="agent-provider-detail">{t("Ollama or LM Studio, found automatically")}</span>
        </TxButton>
        <TxButton variant="row" type="button" role="listitem" className="agent-provider" onClick={() => setChoice({ kind: "relay" })}>
          <span className="agent-provider-name">{t("Another service")}</span>
          <span className="agent-provider-detail">{t("A relay for official models, or any OpenAI- or Anthropic-compatible API")}</span>
        </TxButton>
      </div>
    </div> : form.body}
  </TxDialog>;
}

/** The fields for one choice; the dialog puts Back and Continue in its footer. */
function useConnectionForm(choice: Choice, settings: AgentSettings, client: AgentClient, onAdded: (connection: ModelConnection) => Promise<void>) {
  const kind: Kind = choice.kind;
  const preset = choice.kind === "official" ? PROVIDERS.find(item => item.id === choice.provider)! : null;
  const sites = preset && "sites" in preset ? preset.sites : null;
  const [site, setSite] = useState<string>(sites ? sites[0].id : ""), [key, setKey] = useState("");
  const [local, setLocal] = useState<"ollama" | "lmstudio">("ollama"), [address, setAddress] = useState(kind === "local" ? "http://127.0.0.1:11434" : "");
  const [name, setName] = useState(""), [protocol, setProtocol] = useState<"openai" | "anthropic">("openai");
  const actions = useBusy();
  // Going Back and picking something else starts that form fresh.
  const picked = choice.kind === "official" ? choice.provider : choice.kind;
  useEffect(() => {
    setSite(sites ? sites[0].id : ""); setKey(""); setLocal("ollama"); setAddress(kind === "local" ? "http://127.0.0.1:11434" : "");
    setName(""); setProtocol("openai"); actions.clear();
  }, [picked]);
  const taken = settings.connections.map(connection => connection.id);
  const ready = kind === "official" ? key.trim() !== "" : kind === "local" ? address.trim() !== "" : name.trim() !== "" && address.trim() !== "" && key.trim() !== "";
  const save = () => actions.run("save", async () => {
    let connection: Omit<ModelConnection, "credential">;
    if (preset) {
      const chosen = sites?.find(item => item.id === site);
      const id = uniqueId(chosen ? `${preset.id}-${chosen.id}` : preset.id, taken);
      connection = { id, name: chosen ? `${preset.name} · ${chosen.name}` : preset.name, providerId: preset.id, kind, protocol: preset.protocol,
        site: chosen ? chosen.id : null, baseUrl: chosen ? chosen.baseUrl : preset.baseUrl, secretRef: `model.${id}` };
    } else if (kind === "local") {
      const id = uniqueId(local, taken), origin = address.trim().replace(/\/+$/, "").replace(/\/v1$/, "");
      connection = { id, name: local === "ollama" ? "Ollama" : "LM Studio", providerId: local, kind, protocol: local === "ollama" ? "ollama" : "openai",
        site: null, baseUrl: local === "ollama" ? origin : `${origin}/v1`, secretRef: null };
    } else {
      const id = uniqueId(slug(name) || "relay", taken);
      connection = { id, name: name.trim(), providerId: protocol, kind, protocol, site: null, baseUrl: address.trim().replace(/\/+$/, ""), secretRef: `model.${id}` };
    }
    await client.settings("put_connection", connection);
    if (connection.secretRef) await client.settings("credential", { reference: connection.secretRef, value: key.trim(), connectionId: connection.id });
    await onAdded({ ...connection, credential: { configured: connection.secretRef !== null, mask: null } });
  });
  const body = <div className="agent-settings-form">
    {preset && <>
      {sites && <TxField label={t("Site")} hint={t("Keys from the China site and the international site are separate; use the one your key came from.")}>
        <TxSegmented label={t("Site")} value={site} options={sites.map(item => ({ value: item.id, label: item.name }))} onChange={setSite} />
      </TxField>}
      <TxField label={t("API key")} hint={t("Stored in Windows Credential Manager. TALYX never shows it again.")}><TxInput type="password" autoComplete="off" autoFocus value={key} onChange={event => setKey(event.target.value)} /></TxField>
    </>}
    {kind === "local" && <>
      <TxField label={t("App")}><TxSegmented label={t("App")} value={local} options={[{ value: "ollama", label: t("Ollama") }, { value: "lmstudio", label: t("LM Studio") }]}
        onChange={value => { setLocal(value); setAddress(value === "ollama" ? "http://127.0.0.1:11434" : "http://127.0.0.1:1234"); }} /></TxField>
      <TxField label={t("Address")} hint={t("Where the app listens on this computer.")}><TxInput value={address} onChange={event => setAddress(event.target.value)} /></TxField>
    </>}
    {kind === "relay" && <>
      <TxField label={t("Name")}><TxInput value={name} placeholder={t("My relay")} autoFocus onChange={event => setName(event.target.value)} /></TxField>
      <TxField label={t("Speaks")}><TxSegmented label={t("Protocol")} value={protocol} options={[{ value: "openai", label: t("OpenAI-compatible") }, { value: "anthropic", label: t("Anthropic-compatible") }]} onChange={setProtocol} /></TxField>
      <TxField label={t("Address")} hint={t("The service's base URL, for example https://relay.example.com/v1")}><TxInput value={address} onChange={event => setAddress(event.target.value)} /></TxField>
      <TxField label={t("API key")}><TxInput type="password" autoComplete="off" value={key} onChange={event => setKey(event.target.value)} /></TxField>
    </>}
    {actions.error && <p className="tx-error" role="alert">{actions.error.text}</p>}
  </div>;
  return { body, ready, busy: actions.busy !== null, save };
}

function KeyDialog({ connection, client, onClose, onSaved }: { connection: ModelConnection; client: AgentClient; onClose: () => void; onSaved: () => Promise<void> }) {
  const [key, setKey] = useState(""), actions = useBusy();
  const save = () => actions.run("save", async () => {
    await client.settings("credential", { reference: connection.secretRef, value: key.trim(), connectionId: connection.id });
    await onSaved();
  });
  return <TxDialog open onOpenChange={open => { if (!open) onClose(); }} title={t("{name} API key", { name: connection.name })} size="compact" footer={<>
    <TxButton variant="quiet" onClick={onClose}>{t("Cancel")}</TxButton>
    <TxButton variant="primary" disabled={!key.trim() || actions.busy !== null} onClick={() => void save()}>{t("Save key")}</TxButton>
  </>}>
    <div className="agent-settings-form">
      <TxField label={t("New key")} hint={connection.credential.mask ? t("Replaces the key ending {mask}. Saved tests are cleared.", { mask: connection.credential.mask }) : undefined}>
        <TxInput type="password" autoComplete="off" autoFocus value={key} onChange={event => setKey(event.target.value)} />
      </TxField>
      {actions.error && <p className="tx-error" role="alert">{actions.error.text}</p>}
    </div>
  </TxDialog>;
}

interface Profile { model: string; profile: Record<string, Json> }
interface Discovered { model: string; contextWindow: number | null; tools: boolean | null; images: boolean | null; thinking: boolean | null }

function AddModelDialog({ connection, settings, client, onClose, onAdded }: {
  connection: ModelConnection; settings: AgentSettings; client: AgentClient; onClose: () => void; onAdded: () => Promise<void>;
}) {
  const [profiles, setProfiles] = useState<Profile[] | null>(null), [discovered, setDiscovered] = useState<Discovered[] | null>(null);
  const [loadError, setLoadError] = useState<string | null>(null), [query, setQuery] = useState("");
  const [model, setModel] = useState(""), [windowSize, setWindowSize] = useState("");
  const [outputLimit, setOutputLimit] = useState("");
  const [official, setOfficial] = useState(""), [officialModel, setOfficialModel] = useState("");
  const actions = useBusy();
  const local = connection.kind === "local";
  useEffect(() => {
    if (local) {
      const kind = connection.providerId === "ollama" ? "ollama" : "lmstudio";
      client.settings<{ state: string; models: Discovered[]; reason?: { message: string } }>("discover_models", { kind, baseUrl: connection.baseUrl.replace(/\/v1$/, "") })
        .then(result => result.state === "available" ? setDiscovered(result.models) : setLoadError(`${connection.name} isn't answering. Start it, then try again.${result.reason ? ` (${result.reason.message})` : ""}`))
        .catch(error => setLoadError(errorText(error)));
      return;
    }
    const provider = connection.kind === "relay" ? official : connection.providerId;
    if (!provider) { setProfiles([]); return; }
    (async () => {
      const all: Profile[] = [];
      let offset: number | null = 0;
      while (offset !== null) {
        const page: { items: Profile[]; nextOffset: number | null } = await client.settings("profiles", { provider, offset });
        all.push(...page.items);
        offset = page.nextOffset;
      }
      setProfiles(all);
    })().catch(error => setLoadError(errorText(error)));
  }, [connection, official]);
  const existing = new Set(settings.models.filter(item => item.connectionId === connection.id).map(item => item.model));
  const chosen = local ? discovered?.find(item => item.model === model) : profiles?.find(item => item.model === (connection.kind === "relay" ? officialModel || model : model));
  const knownWindow = local ? (chosen as Discovered | undefined)?.contextWindow : (chosen as Profile | undefined)?.profile.max_input_tokens;
  const toolless = local && (chosen as Discovered | undefined)?.tools === false;
  const listed = useMemo(() => (local ? (discovered ?? []).map(item => item.model) : (profiles ?? []).map(item => item.model))
    .filter(name => name.toLowerCase().includes(query.trim().toLowerCase())), [local, discovered, profiles, query]);
  const ready = model.trim() !== "" && !existing.has(model.trim()) && !toolless && (Boolean(knownWindow) || Number(windowSize) > 0);
  const save = () => actions.run("save", async () => {
    const found = chosen as Discovered | undefined;
    const value: ModelConfiguration = {
      id: `${connection.id}/${model.trim()}`.slice(0, 160), connectionId: connection.id, model: model.trim(),
      ...(outputLimit && { maxOutputTokens: Number(outputLimit) }),
      ...(windowSize ? { contextWindow: Number(windowSize) } : local && found?.contextWindow ? { contextWindow: found.contextWindow } : {}),
      ...(local && found ? { tools: found.tools, images: found.images } : {}),
      ...(connection.kind === "relay" && official ? { profileProvider: official, profileModel: officialModel.trim() || model.trim() } : {}),
      searchMode: connection.kind === "official" && NATIVE_SEARCH.has(connection.providerId) ? "provider" : "talyx",
    };
    // What the local server reports about thinking decides whether the model offers levels.
    await client.settings("put_model", { ...value, ...(local && found ? { thinking: found.thinking } : {}) });
    await onAdded();
  });
  return <TxDialog open onOpenChange={open => { if (!open) onClose(); }} title={t("Add a model · {name}", { name: connection.name })} size="standard" footer={<>
    <TxButton variant="quiet" onClick={onClose}>{t("Cancel")}</TxButton>
    <TxButton variant="primary" disabled={!ready || actions.busy !== null} onClick={() => void save()}>{t("Add model")}</TxButton>
  </>}>
    <div className="agent-settings-form">
      {connection.kind === "relay" && <TxField label={t("Same model as")} hint={t("Window, abilities and price come from the official model. Leave as Other to fill them in yourself.")}>
        <TxSelect value={official} onChange={event => setOfficial(event.target.value)}>
          <option value="">{t("Other")}</option>
          {PROVIDERS.map(item => <option key={item.id} value={item.id}>{item.name}</option>)}
        </TxSelect>
      </TxField>}
      {(local || connection.kind === "official" || official) && <>
        <TxSearch value={query} onValueChange={setQuery} placeholder={local ? t("Search models on this computer") : t("Search models")} />
        <div className="agent-settings-pick" role="listbox" aria-label={t("Models")}>
          {loadError && <p className="tx-error" role="alert">{loadError}</p>}
          {!loadError && (local ? discovered : profiles) === null && <p className="agent-settings-muted">{t("Loading models…")}</p>}
          {listed.slice(0, 200).map(name => {
            const info = local ? discovered!.find(item => item.model === name)! : null;
            const profile = local ? null : profiles!.find(item => item.model === name)!.profile;
            const size = local ? info!.contextWindow : profile!.max_input_tokens;
            const picked = connection.kind === "relay" ? officialModel === name : model === name;
            return <TxButton variant="row" key={name} type="button" role="option" aria-selected={picked} className="agent-settings-option" disabled={!local && connection.kind !== "relay" && existing.has(name)}
              onClick={() => { if (connection.kind === "relay") { setOfficialModel(name); if (!model) setModel(name); } else setModel(name); }}>
              <span>{local ? name : typeof profile!.name === "string" ? `${profile!.name}` : name}</span>
              <span className="agent-settings-mono">{local ? "" : name}{typeof size === "number" ? ` · ${compact(size)}` : ""}{info?.tools === false ? t(" · can't use tools") : ""}{existing.has(name) ? t(" · added") : ""}</span>
            </TxButton>;
          })}
          {listed.length === 0 && query.trim() !== "" && (local ? discovered : profiles)?.length !== 0 && <p className="agent-settings-muted">{local ? t("No model matches “{query}”.", { query: query.trim() }) : t("No model matches “{query}”. Type its exact name below.", { query: query.trim() })}</p>}
          {(local ? discovered : profiles)?.length === 0 && !loadError && <p className="agent-settings-muted">{local ? t("No models found. Download one in the app first.") : t("No model info for this provider yet. Type the model name below, or refresh model info.")}</p>}
        </div>
      </>}
      {!local && <TxField label={connection.kind === "relay" ? t("Model name on the relay") : t("Model name")}>
        <TxInput className="agent-settings-mono-input" value={model} onChange={event => setModel(event.target.value)} />
      </TxField>}
      <TxField label={t("Context window (tokens)")} hint={knownWindow ? t("From model info: {compact}. Fill in only to change it.", { compact: compact(Number(knownWindow)) }) : t("Required: how many tokens this model reads at once.")}>
        <TxInput inputMode="numeric" value={windowSize} placeholder={knownWindow ? String(knownWindow) : "128000"} onChange={event => setWindowSize(event.target.value.replace(/\D/g, ""))} />
      </TxField>
      <TxField label={t("Maximum output (tokens)")}>
        <TxInput inputMode="numeric" placeholder={t("Automatic")} value={outputLimit} onChange={event => setOutputLimit(event.target.value.replace(/\D/g, ""))} />
      </TxField>
      {toolless && <p className="tx-error" role="alert">{t("This model can't call tools, so Agent can't use it.")}</p>}
      {existing.has(model.trim()) && <p className="agent-settings-muted">{t("This model is already added.")}</p>}
      {actions.error && <p className="tx-error" role="alert">{actions.error.text}</p>}
    </div>
  </TxDialog>;
}

type Tri = "" | "yes" | "no";
const tri = (value: boolean | null | undefined): Tri => value === true ? "yes" : value === false ? "no" : "";
const untri = (value: Tri): boolean | null => value === "yes" ? true : value === "no" ? false : null;

function EditModelDialog({ model, connection, client, onClose, onSaved }: {
  model: AgentModel; connection: ModelConnection; client: AgentClient; onClose: () => void; onSaved: () => Promise<void>;
}) {
  const overrides = model.overrides;
  const [windowSize, setWindowSize] = useState(overrides.contextWindow ? String(overrides.contextWindow) : "");
  const [outputLimit, setOutputLimit] = useState(overrides.maxOutputTokens ? String(overrides.maxOutputTokens) : "");
  const [tools, setTools] = useState<Tri>(tri(overrides.tools)), [images, setImages] = useState<Tri>(tri(overrides.images)), [pdf, setPdf] = useState<Tri>(tri(overrides.pdf));
  const [input, setInput] = useState(overrides.price ? overrides.price.input : ""), [output, setOutput] = useState(overrides.price ? overrides.price.output : "");
  // Providers bill cached input separately; without its price, work while you're away stops at the first cache hit (D3).
  const [cacheRead, setCacheRead] = useState(overrides.price?.cache_read ?? ""), [cacheWrite, setCacheWrite] = useState(overrides.price?.cache_write ?? "");
  const [multiplier, setMultiplier] = useState(overrides.priceMultiplier as string), [showPrice, setShowPrice] = useState(overrides.showPrice === true);
  const [search, setSearch] = useState(model.searchMode);
  const actions = useBusy();
  const nativeSearch = connection.kind === "relay" || connection.kind === "official" && NATIVE_SEARCH.has(connection.providerId);
  const save = () => actions.run("save", async () => {
    const price = input.trim() && output.trim() ? { input: input.trim(), output: output.trim(), version: "user", currency: "USD",
      ...(cacheRead.trim() ? { cache_read: cacheRead.trim() } : {}), ...(cacheWrite.trim() ? { cache_write: cacheWrite.trim() } : {}) } : null;
    await client.settings("put_model", { ...overrides, contextWindow: windowSize ? Number(windowSize) : null,
      maxOutputTokens: outputLimit ? Number(outputLimit) : null,
      tools: untri(tools), images: untri(images), pdf: untri(pdf), price, priceMultiplier: multiplier.trim() || "1", showPrice, searchMode: search });
    await onSaved();
  });
  const from = (value: boolean | null | undefined) => value === true ? t("yes") : value === false ? t("no") : t("unknown");
  const ability = (label: string, value: Tri, set: (value: Tri) => void, profile: Json | undefined) => <TxField label={label}>
    <TxSelect value={value} onChange={event => set(event.target.value as Tri)}>
      <option value="">{t("From model info ({value})", { value: from(profile as boolean | null | undefined) })}</option>
      <option value="yes">{t("Yes")}</option><option value="no">{t("No")}</option>
    </TxSelect>
  </TxField>;
  return <TxDialog open onOpenChange={open => { if (!open) onClose(); }} title={modelName(model)} size="standard" footer={<>
    <TxButton variant="quiet" onClick={onClose}>{t("Cancel")}</TxButton>
    <TxButton variant="primary" disabled={actions.busy !== null} onClick={() => void save()}>{t("Save")}</TxButton>
  </>}>
    <div className="agent-settings-form">
      <TxField label={t("Context window (tokens)")} hint={model.profile.max_input_tokens ? t("Model info says {compact}.", { compact: compact(Number(model.profile.max_input_tokens)) }) : t("Model info has no window for this model.")}>
        <TxInput inputMode="numeric" value={windowSize} placeholder={String(model.contextWindow)} onChange={event => setWindowSize(event.target.value.replace(/\D/g, ""))} />
      </TxField>
      <TxField label={t("Maximum output (tokens)")} hint={t("Currently requests {compact} tokens.", { compact: compact(model.maxOutputTokens) })}>
        <TxInput inputMode="numeric" placeholder={t("Automatic")} value={outputLimit} onChange={event => setOutputLimit(event.target.value.replace(/\D/g, ""))} />
      </TxField>
      <div className="agent-settings-columns">
        {ability(t("Calls tools"), tools, setTools, model.profile.tool_calling)}
        {ability(t("Reads images"), images, setImages, model.profile.image_inputs)}
        {ability(t("Reads PDF"), pdf, setPdf, model.profile.pdf_inputs)}
      </div>
      <div className="agent-settings-columns">
        <TxField label={t("Input price, $ per 1M tokens")}><TxInput inputMode="decimal" value={input} placeholder={model.price && !overrides.price ? model.price.input : ""} onChange={event => setInput(event.target.value)} /></TxField>
        <TxField label={t("Output price, $ per 1M tokens")}><TxInput inputMode="decimal" value={output} placeholder={model.price && !overrides.price ? model.price.output : ""} onChange={event => setOutput(event.target.value)} /></TxField>
      </div>
      <div className="agent-settings-columns">
        <TxField label={t("Cached input, $ per 1M tokens")} hint={t("Most providers charge less for repeated context. Needed for work while you're away.")}>
          <TxInput inputMode="decimal" value={cacheRead} placeholder={model.price && !overrides.price ? model.price.cache_read ?? "" : ""} onChange={event => setCacheRead(event.target.value)} />
        </TxField>
        <TxField label={t("Cache write, $ per 1M tokens")} hint={t("Only some providers charge for writing the cache.")}>
          <TxInput inputMode="decimal" value={cacheWrite} placeholder={model.price && !overrides.price ? model.price.cache_write ?? "" : ""} onChange={event => setCacheWrite(event.target.value)} />
        </TxField>
        <TxField label={t("Price multiplier")} hint={t("For relays that charge a multiple of the official price.")}><TxInput inputMode="decimal" value={multiplier} onChange={event => setMultiplier(event.target.value)} /></TxField>
      </div>
      <TxSwitch label={t("Show estimated cost in Usage")} checked={showPrice} onCheckedChange={setShowPrice} />
      {nativeSearch && <TxField label={t("Web search")} hint={connection.kind === "relay" ? t("Use the relay's built-in search only after a connection test shows it works.") : undefined}>
        <TxSegmented label={t("Web search")} value={search} options={[{ value: "provider", label: t("Model's built-in search") }, { value: "talyx", label: t("TALYX search") }]} onChange={setSearch} />
      </TxField>}
      {actions.error && <p className="tx-error" role="alert">{actions.error.text}</p>}
    </div>
  </TxDialog>;
}
