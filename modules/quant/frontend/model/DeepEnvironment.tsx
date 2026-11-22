/* The block under the algorithm list when a neural network is chosen and the deep learning environment is not installed: what will be installed, Install,
   then progress with Cancel; a failure says why with Try again (and the CPU build when a card could not be used). */
import { t } from "../i18n";
import { useState } from "react";
import { TxButton, TxIcon, TxProgress } from "@talyx/sdk";
import type { DeepEnvironment as Deep } from "./api";

const BUILDS: Record<string, string> = {cpu: "CPU", xpu: t("Intel graphics (XPU)")};
const SIZES: Record<string, string> = {cpu: t("about 1.5 GB"), xpu: t("about 4 GB")};

const elapsed = (since: number) => {const seconds = Math.max(0, Math.round((Date.now() - since) / 1000)); return seconds < 60 ? `${seconds} s` : t("{minutes} min {seconds} s", { minutes: Math.floor(seconds / 60), seconds: seconds % 60 });};

export function DeepEnvironment({deep, onInstall, onCancel}: {deep: Deep; onInstall(variant?: string): void; onCancel(): void}) {
  const [choice, setChoice] = useState("cpu");
  if (deep.installed && !deep.newCard) return null;
  const offer = deep.offer;
  const working = deep.installing?.state === "running";
  return <div className="mx-deepenv">
    <div className="mx-deephead"><TxIcon name="package" /><b>{deep.installed ? t("{label} found", { label: deep.devices[0]?.label ?? t("A new card") }) : t("Deep learning environment not installed")}</b></div>
    {!working && !deep.failure && <>
      {offer.choices ? <div className="mx-deepchoices" role="radiogroup" aria-label={t("Build")}>{offer.choices.map(variant => <label key={variant}>
        <input type="radio" name="deep-build" checked={choice === variant} onChange={() => setChoice(variant)} /> {BUILDS[variant]} · {SIZES[variant]}</label>)}</div>
        : <div className="mx-t3">{offer.label} · {offer.size}</div>}
      {offer.reason && <div className="mx-t3">{offer.reason}</div>}
      <TxButton size="sm" variant="primary" onClick={() => onInstall(offer.choices ? choice : offer.variant)}>{deep.installed ? t("Install the CUDA build") : t("Install")}</TxButton></>}
    {working && <div className="mx-deepwork"><TxProgress label={t("Installing the deep learning environment")} value={typeof deep.installing?.fraction === "number" ? deep.installing.fraction * 100 : null} />
      <span className="mx-t3">{(typeof deep.installing?.fraction === "number" ? t("{label} · {size} to download · {startedAt} · {percent}%", { label: deep.installing!.label, size: deep.installing!.size, startedAt: elapsed(deep.installing!.startedAt), percent: Math.round(deep.installing.fraction * 100) }) : t("{label} · {size} to download · {startedAt}", { label: deep.installing!.label, size: deep.installing!.size, startedAt: elapsed(deep.installing!.startedAt) }))}</span><TxButton size="sm" variant="quiet" onClick={onCancel}>{t("Cancel")}</TxButton></div>}
    {!working && deep.failure && <><div className="mx-rowerror" role="alert">{deep.failure}</div>
      <div className="mx-deepactions"><TxButton size="sm" onClick={() => onInstall(offer.variant)}>{t("Try again")}</TxButton>
        {offer.variant !== "cpu" && <TxButton size="sm" variant="quiet" onClick={() => onInstall("cpu")}>{t("Install the CPU build")}</TxButton>}</div></>}
  </div>;
}
