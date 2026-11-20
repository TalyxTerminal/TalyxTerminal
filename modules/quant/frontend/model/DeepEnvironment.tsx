/* The block under the algorithm list when a neural network is chosen and the deep learning environment is not installed: what will be installed, Install,
   then progress with Cancel; a failure says why with Try again (and the CPU build when a card could not be used). */
import { useState } from "react";
import { TxButton, TxIcon, TxProgress } from "@talyx/sdk";
import type { DeepEnvironment as Deep } from "./api";

const BUILDS: Record<string, string> = {cpu: "CPU", xpu: "Intel graphics (XPU)"};
const SIZES: Record<string, string> = {cpu: "about 1.5 GB", xpu: "about 4 GB"};

const elapsed = (since: number) => {const seconds = Math.max(0, Math.round((Date.now() - since) / 1000)); return seconds < 60 ? `${seconds} s` : `${Math.floor(seconds / 60)} min ${seconds % 60} s`;};

export function DeepEnvironment({deep, onInstall, onCancel}: {deep: Deep; onInstall(variant?: string): void; onCancel(): void}) {
  const [choice, setChoice] = useState("cpu");
  if (deep.installed && !deep.newCard) return null;
  const offer = deep.offer;
  const working = deep.installing?.state === "running";
  return <div className="mx-deepenv">
    <div className="mx-deephead"><TxIcon name="package" /><b>{deep.installed ? `${deep.devices[0]?.label ?? "A new card"} found` : "Deep learning environment not installed"}</b></div>
    {!working && !deep.failure && <>
      {offer.choices ? <div className="mx-deepchoices" role="radiogroup" aria-label="Build">{offer.choices.map(variant => <label key={variant}>
        <input type="radio" name="deep-build" checked={choice === variant} onChange={() => setChoice(variant)} /> {BUILDS[variant]} · {SIZES[variant]}</label>)}</div>
        : <div className="mx-t3">{offer.label} · {offer.size}</div>}
      {offer.reason && <div className="mx-t3">{offer.reason}</div>}
      <TxButton size="sm" variant="primary" onClick={() => onInstall(offer.choices ? choice : offer.variant)}>{deep.installed ? "Install the CUDA build" : "Install"}</TxButton></>}
    {working && <div className="mx-deepwork"><TxProgress label="Installing the deep learning environment" value={typeof deep.installing?.fraction === "number" ? deep.installing.fraction * 100 : null} />
      <span className="mx-t3">{deep.installing!.label} · {deep.installing!.size} to download · {elapsed(deep.installing!.startedAt)}{typeof deep.installing?.fraction === "number" ? ` · ${Math.round(deep.installing.fraction * 100)}%` : ""}</span><TxButton size="sm" variant="quiet" onClick={onCancel}>Cancel</TxButton></div>}
    {!working && deep.failure && <><div className="mx-rowerror" role="alert">{deep.failure}</div>
      <div className="mx-deepactions"><TxButton size="sm" onClick={() => onInstall(offer.variant)}>Try again</TxButton>
        {offer.variant !== "cpu" && <TxButton size="sm" variant="quiet" onClick={() => onInstall("cpu")}>Install the CPU build</TxButton>}</div></>}
  </div>;
}
