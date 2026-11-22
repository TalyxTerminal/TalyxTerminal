import { t } from "../i18n";
import { useEffect, useState } from "react";
import { TxAlert, TxButton, TxPanel, TxRow, translator, type ModuleContext } from "@talyx/sdk";
import { privateRequest } from "../api";
import { documents, type DocumentSource } from "../documents/service";
import { message } from "./errors";

const greetAction = "greet", backendEntry = "backend/entry.py", frontendEntry = "frontend/module.js";

interface Support { moduleId: string; name: string; documentation: { name: string; source: DocumentSource }[] }

export function DeveloperHelp({ ctx, onOpened }: { ctx: ModuleContext; onOpened(): void }) {
  const [error, setError] = useState<string | null>(null), [working, setWorking] = useState(false);
  const [supports, setSupports] = useState<Support[]>([]);
  useEffect(() => { void privateRequest<{ supports: Support[] }>(ctx, "/help").then(value => setSupports(value.supports), reason => setError(message(reason))); }, [ctx]);
  const open = async (source: DocumentSource) => { setWorking(true); setError(null); try { await documents.open(source); onOpened(); } catch (reason) { setError(message(reason)); } finally { setWorking(false); } };
  return <TxPanel title={t("Developer help")}>
    {error && <TxAlert title={t("Reference could not be opened")}>{error}</TxAlert>}
    <ol className="developer-form-options">
      <li><strong>{t("Create a module.")}</strong> {t.rich("New TALYX module asks only for a name. It starts with one action, {action}, and a panel that calls it.", { action: <code>{greetAction}</code> })}</li>
      <li><strong>{t("Use it in TALYX.")}</strong> {t("Use in TALYX starts it like any installed module: its panel is in the add-panel list, its actions are among AI's tools.")}</li>
      <li><strong>{t("Change it.")}</strong> {t.rich("Declare actions in {backend}; the panel is {frontend}. Reload saves your files, regenerates the contract and restarts only this module.", { backend: <code>{backendEntry}</code>, frontend: <code>{frontendEntry}</code> })}</li>
      <li><strong>{t("Debug it.")}</strong> {t("Debug in VS Code writes an attach configuration into the project and opens it in VS Code. Start it from Run and Debug to attach to the running module. After a module restart, click Debug in VS Code again to update the process ID.")}</li>
      <li><strong>{t("Stop using it.")}</strong> {t("Disable keeps it installed; Uninstall removes it from TALYX.")}</li>
    </ol>
    {supports.map(support => <section key={support.name}>
      <h3>{translator(support.moduleId)(support.name)}</h3>
      {support.documentation.map(item => <TxRow key={item.name} title={translator(support.moduleId)(item.name)}><TxButton disabled={working} onClick={() => void open(item.source)}>{t("Open")}</TxButton></TxRow>)}
    </section>)}
  </TxPanel>;
}
