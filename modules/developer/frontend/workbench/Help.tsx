import { useEffect, useState } from "react";
import { TxAlert, TxButton, TxPanel, TxRow, type ModuleContext } from "@talyx/sdk";
import { privateRequest } from "../api";
import { documents, type DocumentSource } from "../documents/service";
import { message } from "./errors";

interface Support { name: string; documentation: { name: string; source: DocumentSource }[] }

export function DeveloperHelp({ ctx, onOpened }: { ctx: ModuleContext; onOpened(): void }) {
  const [error, setError] = useState<string | null>(null), [working, setWorking] = useState(false);
  const [supports, setSupports] = useState<Support[]>([]);
  useEffect(() => { void privateRequest<{ supports: Support[] }>(ctx, "/help").then(value => setSupports(value.supports), reason => setError(message(reason))); }, [ctx]);
  const open = async (source: DocumentSource) => { setWorking(true); setError(null); try { await documents.open(source); onOpened(); } catch (reason) { setError(message(reason)); } finally { setWorking(false); } };
  return <TxPanel title="Developer help">
    {error && <TxAlert title="Reference could not be opened">{error}</TxAlert>}
    <ol className="developer-form-options">
      <li><strong>Create a module.</strong> New TALYX module asks only for a name. It starts with one action, <code>greet</code>, and a panel that calls it.</li>
      <li><strong>Use it in TALYX.</strong> Use in TALYX starts it like any installed module: its panel is in the add-panel list, its actions are among AI's tools.</li>
      <li><strong>Change it.</strong> Declare actions in <code>backend/entry.py</code>; the panel is <code>frontend/module.js</code>. Reload saves your files, regenerates the contract and restarts only this module.</li>
      <li><strong>Debug it.</strong> Debug in VS Code writes an attach configuration into the project and opens it in VS Code. Start it from Run and Debug to attach to the running module. After a module restart, click Debug in VS Code again to update the process ID.</li>
      <li><strong>Stop using it.</strong> Disable keeps it installed; Uninstall removes it from TALYX.</li>
    </ol>
    {supports.map(support => <section key={support.name}>
      <h3>{support.name}</h3>
      {support.documentation.map(item => <TxRow key={item.name} title={item.name}><TxButton disabled={working} onClick={() => void open(item.source)}>Open</TxButton></TxRow>)}
    </section>)}
  </TxPanel>;
}
