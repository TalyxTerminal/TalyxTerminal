/* Market's page in the settings window (platform.settings_section): its sources, each on or off, with its key and
   settings. Global only: sources and keys belong to this computer, not to a workspace. */
import { useCallback, useEffect, useState } from "react";
import { TxButton, TxDialog, TxRow, TxSettingsGroup, TxSettingsNote, TxSettingsPage, TxSwitch, TxTag, type ModuleContext } from "@talyx/sdk";
import { errorText, listOf, marketCall, panelCall, sourceOf, testResultOf, type Source, type SourcesAnswer, type TestResult } from "./panel/data";
import { groupsOf, SourceFields, StatusCell } from "./panel/Sources";

export function SourcesSettings({ ctx }: { ctx: ModuleContext }) {
  const [sources, setSources] = useState<Source[] | null>(null);
  const [problem, setProblem] = useState<string | null>(null);
  const [results, setResults] = useState<Record<string, TestResult>>({});
  const [testing, setTesting] = useState<Set<string>>(new Set());
  const [editing, setEditing] = useState<string | null>(null);

  const refresh = useCallback(async () => {
    try { setSources((await panelCall<SourcesAnswer>(ctx, "sources")).sources.map(sourceOf)); }
    catch (error) { setProblem(errorText(error)); }
  }, [ctx]);
  useEffect(() => { void refresh(); }, [refresh]);

  const test = async (ids: string[]) => {
    setTesting(previous => new Set([...previous, ...ids]));
    setProblem(null);
    try {
      const answer = await marketCall(ctx, "write_source", { operation: ids.length > 1 ? { op: "test_all" } : { op: "test", sources: listOf<[string, ...string[]]>(ids, 1, 1) } });
      setResults(previous => ({ ...previous, ...Object.fromEntries(answer.tests.map(item => [item.source, testResultOf(item)])) }));
    } catch (error) { setProblem(errorText(error)); }
    finally {
      setTesting(previous => { const next = new Set(previous); ids.forEach(id => next.delete(id)); return next; });
      await refresh();
    }
  };
  const use = async (id: string, on: boolean) => {
    setProblem(null);
    try { await marketCall(ctx, "write_source", { operation: { op: on ? "enable" : "disable", source: id } }); }
    catch (error) { setProblem(errorText(error)); }
    await refresh();
  };
  const configure = async (id: string, settings: Record<string, unknown>) => {
    setProblem(null);
    try { await panelCall(ctx, "configure", { source: id, settings }); }
    catch (error) { setProblem(errorText(error)); await refresh(); return; }
    await test([id]);
  };

  const open = sources?.find(source => source.id === editing);
  const testAll = sources && <TxButton size="sm" loading={testing.size > 1} onClick={() => void test(sources.map(source => source.id))}>Test all</TxButton>;
  // The panel's bands (Needs attention, In use, Not set up, ...), each a titled group with its count; Test all on the first.
  const groups = sources ? groupsOf(sources) : [];
  return <TxSettingsPage lede="Where Market gets its data. Sources and their keys belong to this computer, for every workspace. The Market panel's Sources tab shows the same sources, with what each gives and the data it keeps.">
    {(problem || !sources) && <TxSettingsGroup action={testAll}>
      {problem ? <TxSettingsNote tone="error">{problem}</TxSettingsNote> : <TxSettingsNote>Loading…</TxSettingsNote>}
    </TxSettingsGroup>}
    {groups.map((group, index) => <TxSettingsGroup key={group.id} title={<>{group.label} <span className="tx-muted tx-num">{group.sources.length}</span></>}
      action={index === 0 && !problem ? testAll : undefined}>
      {group.sources.map(source => {
        const keyed = source.key === "optional" || source.key === "required";
        return <TxRow key={source.id} tone={!source.enabled ? "off" : group.id === "attention" ? "failed" : "default"}
          title={<>{source.name}{source.paid && <> <TxTag tone="muted">Paid</TxTag></>}</>}
          detail={<StatusCell source={source} result={results[source.id]} testing={testing.has(source.id)} />}>
          <div className="tx-row tx-nowrap">
            {source.settings.schema.length > 0 && <TxButton size="sm" variant={source.key === "required" && !source.keySet && source.enabled ? "primary" : "default"}
              onClick={() => setEditing(source.id)}>
              {source.key === "local" ? "Connection" : keyed ? source.keySet ? "Change key" : "Set key" : "Settings"}</TxButton>}
            <TxSwitch hideLabel label={`Use ${source.name}`} checked={source.enabled} onCheckedChange={on => void use(source.id, on)} />
          </div>
        </TxRow>;
      })}
    </TxSettingsGroup>)}
    {open && <TxDialog open onOpenChange={next => { if (!next) setEditing(null); }} title={open.name} size="standard">
      <SourceFields source={open} result={results[open.id] ?? (open.lastTest ? testResultOf(open.lastTest) : undefined)}
        testing={testing.has(open.id)} onSave={settings => configure(open.id, settings)} />
    </TxDialog>}
  </TxSettingsPage>;
}
