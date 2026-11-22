/* The code area of every Quant object page: Developer's project view (file tree, tabs, editor) with Quant's marks on it. The text is Developer's;
   Quant keeps none of it. The tree is closed until asked for, and opening it widens the panel by its own width, so the code keeps its room. */
import { t } from "../i18n";
import "./codearea.css";
import { useMemo, useRef, type ReactNode } from "react";
import { ModuleView, TxButton, TxIcon, TxIconButton, type ModuleContext } from "@talyx/sdk";

export const TREE_WIDTH = 150;

/** A line of a run's failure, as the project view takes it: on a file, with the error's own words. */
export interface CodeMark { uri: string; line: number; message: string; title?: string; detail?: string; source?: string }

/** Where a failure is on disk: the project folder's file URI of the file the run named. */
export function fileUri(rootUri: string, file: string): string {
  return rootUri.replace(/\/+$/, "") + "/" + file.split("\\").join("/").replace(/^\/+/, "");
}

/** The view's marks: every mark an error, by file. */
export function toMarkers(marks: CodeMark[]) {
  return marks.map(mark => ({uri: mark.uri, line: mark.line, severity: "error" as const, message: mark.message, ...(mark.title ? {title: mark.title} : {}),
    ...(mark.detail ? {detail: mark.detail} : {}), ...(mark.source ? {source: mark.source} : {})}));
}

export function CodeArea({ctx, projectId, rootUri, treeTitle, entryUri, marks = [], line, request, tree, onTree, readOnly, onChanged, onHide, onOpenDeveloper, status}: {
  ctx: ModuleContext; projectId: string | null; rootUri: string; treeTitle?: string; entryUri: string; marks?: CodeMark[];
  /** Jump to this line of the entry file (a new `request` each time, so the same line can be asked for again). */
  line?: number; request?: string; tree: boolean; onTree(open: boolean): void; readOnly?: boolean;
  onChanged?(change: {uri: string; documentId: string; version: number; dirty: boolean}): void; onHide?(): void; onOpenDeveloper(): void;
  /** What the foot of the area says (which run the code is, a line that failed, the key that runs it). */
  status?: ReactNode;
}) {
  const count = marks.length;
  // One `on` for the life of the area: a new object each render would remount the view (and close its tree) every time.
  const latest = useRef(onChanged); latest.current = onChanged;
  const listeners = useMemo(() => ({changed: (value: unknown) => latest.current?.(value as never)}), []);
  return <section className="qx-codearea" aria-label={t("Code")} data-tree={tree ? "" : undefined}>
    <div className="qx-codearea-bar">
      <TxIconButton size="sm" label={tree ? t("Hide files") : t("Show files")} icon="list" aria-pressed={tree} onClick={() => onTree(!tree)} />
      {count > 0 && <span className="qx-codearea-count" role="status">{count} {count === 1 ? t("error") : t("errors")}</span>}
      <span className="tx-spacer" />
      <TxButton size="sm" variant="quiet" onClick={onOpenDeveloper}><TxIcon name="external" />{t("Open in Developer")}</TxButton>
      {onHide && <TxIconButton size="sm" label={t("Hide code")} icon="close" onClick={onHide} />}
    </div>
    <div className="qx-codearea-view">
      <ModuleView id="developer.project_editor" context={ctx} props={{rootUri, ...(treeTitle ? {treeTitle} : {}), ...(projectId ? {projectId} : {}), file: entryUri, tree, ...(readOnly ? {readOnly} : {}),
        ...(line ? {position: {line}, positionRequestId: request ?? String(line)} : {}), markers: toMarkers(marks)}}
        on={listeners} />
    </div>
    {status && <div className="qx-codearea-foot">{status}</div>}
  </section>;
}
