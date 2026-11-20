import { useEffect, useState } from "react";
import { mdy } from "./format";
import type { LibraryApi } from "./api";

export interface CodeView { title: string; file: string; lines: string[]; frozen: string; from: string }

/** The frozen code of a version, read-only: its files, or for a model or an allocation its settings (there is no file). */
export function useCode(api: LibraryApi, itemId: string, versionId: string | undefined, file?: string): CodeView | null {
  const [view, setView] = useState<CodeView | null>(null);
  useEffect(() => {
    let live = true;
    setView(null);
    (async () => {
      const detail = await api.item(itemId, versionId ? {versionId} : {});
      const version = detail.versions.find(item => item.versionId === detail.versionId) ?? detail.versions[0];
      const full = await api.version(version.versionId);
      const files: string[] = await api.source(version.versionId).then(value => value.files ?? [], () => [] as string[]);
      const title = `${detail.summary.name} v${version.version}`, from = `from ${version.origin?.runNumber ? `#${version.origin.runNumber}, ` : ""}${mdy(version.publishedAt)}`, frozen = `Frozen with v${version.version}, read-only`;
      if (files.length > 0) {
        const name = file && files.includes(file) ? file : files[0];
        const source = await api.source(version.versionId, name);
        return {title, file: name, lines: (source.content ?? "").split("\n"), frozen, from};
      }
      const settings = JSON.stringify(full.settings ?? {}, null, 2).split("\n");
      return {title, file: detail.summary.kind === "allocation" ? "allocation settings" : "model settings", lines: [full.formula ? `formula: ${full.formula}` : "", ...settings].filter((line, index) => index > 0 || line), frozen, from};
    })().then(value => {if (live) setView(value);}, () => {if (live) setView({title: "Frozen code", file: "unavailable", lines: ["The frozen code could not be read."], frozen: "", from: ""});});
    return () => {live = false;};
  }, [api, itemId, versionId, file]);
  return view;
}
