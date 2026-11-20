import { useEffect, useMemo, useState, type ReactNode } from "react";
import { TxButton, TxIcon, TxIconButton, TxInput, TxTable, TxTd, TxTh, type ModuleContext } from "@talyx/sdk";
import { documentsClient } from "./developerClients";
import { formatAge } from "./format";
import { notebookApi, type EnvironmentName, type PackageStatus } from "./notebook";
import { pathUri, type Api } from "./objects";
import { addRequirement, packageName, removeRequirement, requirementProblem } from "./requirements";
import "./notebook.css";

const ENVIRONMENTS: {name: EnvironmentName; label: string}[] = [{name: "research", label: "Research"}, {name: "strategy", label: "Strategies"}];
const STEP: Record<string, string> = {resolving: "Checking versions", queued: "Waiting", running: "Installing", preparing: "Installing", completed: "Done", ready: "Done", failed: "Failed", cancelled: "Cancelled"};

/**
 * The user's packages: the list both environments are built from, installing it, and what could not be installed. The bundled
 * versions never move, so a package that needs another version of one is refused, naming the package and the side that refused it.
 */
export function PackagesPage({ctx, api, title, onBack}: {ctx: ModuleContext; api: Api; title: ReactNode; onBack?: () => void}) {
  const nb = useMemo(() => notebookApi(ctx), [ctx]);
  const documents = useMemo(() => documentsClient(ctx), [ctx]);
  const [status, setStatus] = useState<PackageStatus | null>(null);
  const [text, setText] = useState("");
  const [problem, setProblem] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [editing, setEditing] = useState(false);
  const fail = (reason: unknown) => setError(reason instanceof Error ? reason.message : String(reason));
  const load = () => nb.packages().then(setStatus);
  useEffect(() => {void load().catch(fail);}, []);
  const running = status !== null && status.job !== null && status.job.state === "running";
  useEffect(() => {
    if (!running) return;
    const timer = setInterval(() => void load().catch(fail), 1500);
    return () => clearInterval(timer);
  }, [running]);

  /** The list is a file in the common code folder: changed as a Developer document, so its history and VS Code see the same text. */
  const editList = async (change: (content: string) => string) => {
    setEditing(true); setError(null);
    try {
      const folder = await api.openCommonCode();
      const document = await documents.open({source: {kind: "file", uri: pathUri(status!.file), projectId: folder.projectId}});
      const edited = await documents.edit({documentId: document.documentId, expectedVersion: document.version, content: change(document.content)});
      const saved = await documents.save({documentId: edited.documentId, expectedVersion: edited.version});
      if (saved.status !== "saved" && saved.status !== "clean") throw new Error(saved.message ?? `The package list could not be saved (${saved.status}).`);
      await load();
    } finally {setEditing(false);}
  };
  const add = () => {
    const found = requirementProblem(text, status!.requirements);
    setProblem(found);
    if (found !== null) return;
    void editList(content => addRequirement(content, text)).then(() => setText(""), fail);
  };

  if (status === null) return <div className="qx-page"><div className="qx-bar">{title}</div>
    {error !== null ? <div className="qx-page-error" role="alert"><span>{error}</span></div> : <p className="qx-muted qx-pad">Loading packages…</p>}</div>;
  const job = status.job;
  const installed = (environment: EnvironmentName) => {const value = status.environments[environment]; return value === null ? [] : value.packages;};
  const installedNames = (environment: EnvironmentName) => new Set(installed(environment).map(packageName));
  const removed = installed("research").filter(item => !status.requirements.some(line => packageName(line) === packageName(item)));
  const failures = job === null ? {} : job.failures;
  const failedNames = (environment: EnvironmentName) => {
    const failure = failures[environment];
    return failure === undefined ? null : new Set((failure.details !== undefined && failure.details !== null && failure.details.requested !== undefined ? failure.details.requested : status.requirements).map(packageName));
  };
  const cell = (environment: EnvironmentName, requirement: string) => {
    const name = packageName(requirement);
    const failedHere = failedNames(environment);
    if (installedNames(environment).has(name) && installed(environment).includes(requirement)) return <span className="qx-pk-state" data-state="ok"><TxIcon name="check" />Installed</span>;
    if (failedHere !== null && failedHere.has(name) && job !== null && job.state !== "running") return <span className="qx-pk-state" data-state="failed">Not installed</span>;
    return <span className="qx-pk-state">Install to add</span>;
  };
  const pending = status.requirements.some(item => !installed("research").includes(item) || !installed("strategy").includes(item)) || removed.length > 0;
  return <div className="qx-page">
    <div className="qx-bar">
      {title}
      <span className="tx-spacer" />
      {onBack !== undefined && <TxButton variant="quiet" onClick={onBack}>Back</TxButton>}
      {running ? <TxButton onClick={() => void nb.cancelInstall().then(setStatus, fail)}><span className="qx-spin" />Installing<span className="qx-running-cancel">Cancel</span></TxButton>
        : <TxButton variant={pending ? "primary" : "default"} disabled={editing} onClick={() => void nb.install().then(setStatus, fail)}><TxIcon name="download" />Install</TxButton>}
    </div>
    {error !== null && <div className="qx-page-error" role="alert"><span>{error}</span><TxButton size="sm" variant="quiet" onClick={() => setError(null)}>Dismiss</TxButton></div>}
    <div className="qx-pk">
      <section className="qx-pk-list" aria-label="Package list">
        <TxTable className="qx-pk-table"><thead><tr><TxTh>Package</TxTh>{ENVIRONMENTS.map(item => <TxTh key={item.name}>{item.label}</TxTh>)}<TxTh /></tr></thead>
          <tbody>
            {status.requirements.map(requirement => <tr key={requirement}>
              <TxTd className="qx-pk-name">{requirement}</TxTd>
              {ENVIRONMENTS.map(item => <TxTd key={item.name}>{cell(item.name, requirement)}</TxTd>)}
              <TxTd align="right">{!running && <TxIconButton size="sm" icon="trash" label={`Remove ${requirement}`} disabled={editing}
                onClick={() => void editList(content => removeRequirement(content, requirement)).catch(fail)} />}</TxTd>
            </tr>)}
            {removed.map(requirement => <tr key={"removed:" + requirement} className="qx-pk-removed">
              <TxTd className="qx-pk-name">{requirement}</TxTd><TxTd colSpan={2}><span className="qx-pk-state">Removed · install to take it out</span></TxTd><TxTd />
            </tr>)}
            {status.requirements.length === 0 && removed.length === 0 && <tr><TxTd colSpan={4} className="qx-muted">No packages added. The research environment already has pandas, numpy, scipy, statsmodels, scikit-learn, QuantLib and more.</TxTd></tr>}
          </tbody>
        </TxTable>
        {/* the list is changed between installs: while one runs, nothing is added or removed */}
        {!running && <form className="qx-pk-add" onSubmit={event => {event.preventDefault(); add();}}>
          <TxInput aria-label="Package" placeholder="pandas-ta or ta>=0.11" value={text} aria-invalid={problem !== null}
            onChange={event => {setText(event.target.value); setProblem(null);}} />
          <TxButton type="submit" disabled={editing}><TxIcon name="plus" />Add</TxButton>
          {problem !== null && <span className="qx-error">{problem}</span>}
        </form>}
      </section>
      {job !== null && <section className="qx-pk-job" aria-label="Installation">
        <h3 className="qx-pk-heading">{job.state === "running" ? "Installing" : job.state === "completed" ? "Installed" : job.state === "cancelled" ? "Installation cancelled" : "Some packages could not be installed"}
          <span className="qx-muted">{formatAge(job.endedAt !== null ? job.endedAt : job.startedAt)}</span></h3>
        <ul className="qx-pk-steps">
          {ENVIRONMENTS.map(item => {
            const step = job.environments[item.name];
            const failure = failures[item.name];
            return <li key={item.name} data-state={failure !== undefined ? "failed" : step === undefined ? "waiting" : step.state}>
              <span className="qx-pk-env">{item.label}</span>
              {failure !== undefined ? <div className="qx-pk-failure">
                <p>{item.name === "strategy" ? "These packages cannot be used in strategies." : "These packages cannot be added to the research environment."}</p>
                <pre>{failure.details !== undefined && failure.details !== null && failure.details.cause !== undefined ? failure.details.cause : failure.message}</pre>
              </div> : <span>{step === undefined ? job.state === "running" ? "Waiting" : "—" : STEP[step.state] ?? step.state}{step !== undefined && step.message !== null ? ` · ${step.message}` : ""}
                {step !== undefined && job.state === "running" && step.state !== "ready" && step.state !== "failed" && <span className="qx-spin" />}</span>}
            </li>;
          })}
        </ul>
        {job.state === "completed" && status.restartKernels.length === 0 && <p className="qx-muted">New notebook kernels, runs and backtests use these packages.</p>}
      </section>}
      {status.restartKernels.length > 0 && <section className="qx-nb-strip" aria-label="Kernels to restart"><TxIcon name="refresh" />
        <span>Restart these notebooks' kernels to use the new packages: {status.restartKernels.map(item => item.name).join(", ")}</span></section>}
    </div>
  </div>;
}
