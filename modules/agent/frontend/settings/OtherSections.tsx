import { useEffect, useState } from "react";
import {
  TxButton, TxInput, TxRow, TxSegmented, TxSelect, TxSettingsGroup, TxSettingsNote, TxSettingsPage,
  type ModuleContext,
} from "@talyx/sdk";
import type { AgentClient } from "../data/client";
import type { AgentSettings } from "../data/types";
import { SettingsData, useBusy } from "./shared";

/* ── Search ──────────────────────────────────────────────── */

export function SearchSection({ ctx }: { ctx: ModuleContext }) {
  return <SettingsData ctx={ctx}>{(settings, client, refresh) => <Search settings={settings} client={client} refresh={refresh} />}</SettingsData>;
}

const OFFICIAL_SEARCH: Record<string, string> = { deepseek: "DeepSeek search", moonshotai: "Kimi search", zhipuai: "Zhipu search" };
/* What each source bills per search, as the Usage ledger names it (providers/presets.py). */
const SEARCH_UNITS: Record<string, string> = {
  "exa:global": "exa_search_request", "tavily:global": "tavily_credit", "deepseek:global": "deepseek_search_request",
  "moonshotai:domestic": "kimi_search_pro_request", "moonshotai:international": "kimi_search_pro_request",
  "zhipuai:domestic": "zhipu_search_pro_request", "zhipuai:international": "zai_search_prime_request",
};

function Search({ settings, client, refresh }: { settings: AgentSettings; client: AgentClient; refresh: () => Promise<void> }) {
  const search = settings.search, actions = useBusy();
  const saved = search.provider === "exa" || search.provider === "tavily" ? search.provider : `official:${search.connectionId}`;
  const [mode, setMode] = useState(search.mode), [source, setSource] = useState(saved), [key, setKey] = useState("");
  useEffect(() => { setMode(search.mode); setSource(saved); }, [search.mode, saved]);
  const official = settings.connections.filter(connection => connection.kind === "official" && connection.providerId in OFFICIAL_SEARCH);
  const apply = (nextMode: typeof mode, nextSource: string) => actions.run("search", async () => {
    if (nextSource === "tavily" && key.trim()) await client.settings("credential", { reference: "search.tavily", value: key.trim() });
    const connection = nextSource.startsWith("official:") ? official.find(item => `official:${item.id}` === nextSource)! : null;
    await client.configureSearch({ mode: nextMode, provider: connection ? connection.providerId as typeof search.provider : nextSource as "exa" | "tavily",
      secretRef: nextSource === "tavily" ? "search.tavily" : null, connectionId: connection ? connection.id : null, prices: search.prices });
    setKey("");
    await refresh();
  });
  // Tavily waits for its key; every other choice takes effect at once.
  const choose = (nextMode: typeof mode, nextSource: string) => {
    setMode(nextMode); setSource(nextSource);
    if (nextSource !== "tavily" || search.credential.configured && search.secretRef === "search.tavily") void apply(nextMode, nextSource);
  };
  const tavilyReady = search.provider === "tavily" || search.secretRef === "search.tavily" && search.credential.configured;
  return <TxSettingsPage data-settings-section="agent-search"
    lede="Which web search the AI uses. If a search fails or runs out, the AI tells you why; it never switches to another source on its own. Reading a web page needs no key.">
    <TxSettingsGroup title="Source">
      <TxRow title="How the AI searches" detail={mode === "auto"
        ? "Anthropic, OpenAI, Google and xAI models search by themselves. DeepSeek, Kimi and Zhipu models use their own search API with the same key. Other models use the source below."
        : "DeepSeek, Kimi and Zhipu models use the source below instead of their own. Models that search by themselves still do."}>
        <TxSegmented label="How the AI searches" value={mode} options={[{ value: "auto", label: "Automatic" }, { value: "fixed", label: "One source" }]}
          onChange={next => choose(next, next === "auto" && source.startsWith("official:") ? "exa" : source)} />
      </TxRow>
      <TxRow title={mode === "auto" ? "For other models" : "Source"} detail={source === "exa" ? "Exa works without an account, within its free allowance."
        : source === "tavily" ? "Tavily needs a key. Free keys are available at tavily.com." : "Uses the same key as the model connection."}>
        <TxSelect aria-label="Search source" className="agent-settings-select" value={source} disabled={actions.busy !== null} onChange={event => choose(mode, event.target.value)}>
          <option value="exa">Exa · free, no sign-up</option>
          <option value="tavily">Tavily</option>
          {mode === "fixed" && official.map(connection => <option key={connection.id} value={`official:${connection.id}`}>{OFFICIAL_SEARCH[connection.providerId]}{connection.site ? ` · ${connection.name}` : ""}</option>)}
        </TxSelect>
      </TxRow>
      {source === "tavily" && <TxRow title="Tavily API key" detail={search.credential.configured && search.secretRef === "search.tavily" ? `A key ending ${search.credential.mask} is saved. Enter a new one to replace it.` : "Stored in Windows Credential Manager."}>
        <div className="agent-settings-inline">
          <TxInput aria-label="Tavily API key" type="password" autoComplete="off" value={key} onChange={event => setKey(event.target.value)} />
          <TxButton size="sm" variant="primary" disabled={actions.busy !== null || (!key.trim() && !tavilyReady)} onClick={() => void apply(mode, "tavily")}>
            {search.provider === "tavily" ? "Save key" : "Use Tavily"}
          </TxButton>
        </div>
      </TxRow>}
      {actions.error && <TxSettingsNote tone="error">{actions.error.text}</TxSettingsNote>}
    </TxSettingsGroup>
    <SearchPrices settings={settings} client={client} refresh={refresh} official={official} />
  </TxSettingsPage>;
}

/** Each search source needs a price for its searches to count toward the spending limit. */
function SearchPrices({ settings, client, refresh, official }: { settings: AgentSettings; client: AgentClient; refresh: () => Promise<void>; official: AgentSettings["connections"] }) {
  const search = settings.search, actions = useBusy(), currency = settings.usage.currency;
  const sources = [
    { key: "exa:global", name: "Exa", detail: "Free within its allowance; enter 0 to let unattended work use it." },
    ...(search.secretRef === "search.tavily" ? [{ key: "tavily:global", name: "Tavily", detail: "Price per credit; a basic search uses one." }] : []),
    ...official.map(connection => ({ key: `${connection.providerId}:${connection.site ?? "global"}`, name: `${OFFICIAL_SEARCH[connection.providerId]}${connection.site ? ` · ${connection.name}` : ""}`, detail: "Uses the model connection's key." })),
  ].filter((item, index, all) => all.findIndex(other => other.key === item.key) === index);
  const [values, setValues] = useState<Record<string, string>>({});
  const current = (key: string) => search.prices[key]?.search?.[SEARCH_UNITS[key]] ?? "";
  const save = (key: string) => actions.run(key, async () => {
    const value = values[key].trim();
    const prices = { ...search.prices };
    if (value) prices[key] = { input: "0", output: "0", version: "user", currency, search: { [SEARCH_UNITS[key]]: value } };
    else delete prices[key];
    await client.configureSearch({ mode: search.mode, provider: search.provider, secretRef: search.secretRef, connectionId: search.connectionId, prices });
    setValues(previous => { const next = { ...previous }; delete next[key]; return next; });
    await refresh();
  });
  return <TxSettingsGroup title="Price per search" detail="Searches count toward your spending limit at these prices.">
    {sources.map(source => {
      const value = values[source.key] ?? current(source.key);
      return <TxRow key={source.key} title={source.name} detail={current(source.key) ? source.detail : `No price yet. ${source.detail}`}>
        <div className="agent-settings-inline agent-settings-limit">
          <TxInput aria-label={`${source.name} price per search (${currency})`} inputMode="decimal" value={value} placeholder="—"
            onChange={event => setValues(previous => ({ ...previous, [source.key]: event.target.value }))} />
          <TxButton size="sm" disabled={actions.busy !== null || value === current(source.key)} onClick={() => void save(source.key)}>Save</TxButton>
        </div>
      </TxRow>;
    })}
    {actions.error && <TxSettingsNote tone="error">{actions.error.text}</TxSettingsNote>}
  </TxSettingsGroup>;
}

/* ── Skills ──────────────────────────────────────────────── */

export function SkillsSection({ ctx }: { ctx: ModuleContext }) {
  return <SettingsData ctx={ctx}>{(settings, _client, refresh) => {
    const yours = settings.skills.items.filter(skill => skill.path.startsWith("/skills/user/"));
    const workspace = settings.skills.items.filter(skill => !skill.path.startsWith("/skills/user/"));
    const row = (skill: typeof yours[number]) => <TxRow key={skill.name} title={<span className="agent-settings-mono">/{skill.name}</span>} detail={skill.description} />;
    return <TxSettingsPage data-settings-section="agent-skills"
      lede="A skill is a written way of doing something, with optional scripts. Agent reads only names and descriptions until a skill is needed. Type /name in a chat to use one directly.">
      <TxSettingsGroup title="Your skills" detail={<>Add one as <code>user/skills/&lt;name&gt;/SKILL.md</code>, then reload.</>}
        action={<TxButton size="sm" onClick={() => void refresh()}>Reload</TxButton>}>
        {yours.length ? yours.map(row) : <TxSettingsNote>No skills of your own yet.</TxSettingsNote>}
      </TxSettingsGroup>
      {workspace.length > 0 && <TxSettingsGroup title="This workspace">{workspace.map(row)}</TxSettingsGroup>}
    </TxSettingsPage>;
  }}</SettingsData>;
}
