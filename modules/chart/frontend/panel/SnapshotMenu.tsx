/* Snapshot and export: the chart as an image (download or copy), the bars and every indicator output as a file. */
import { TxButton, TxIcon, TxMenu, useTxToast, type ModuleContext } from "@talyx/sdk";
import { post } from "../api";
import { message } from "../store";
import type { ChartBinding } from "../chart/binding";

function save(blob: Blob, filename: string): void {
  const url = URL.createObjectURL(blob), link = document.createElement("a");
  link.href = url; link.download = filename; link.click();
  setTimeout(() => URL.revokeObjectURL(url), 1000);
}

const imageOf = (binding: ChartBinding) => new Promise<Blob>((done, fail) =>
  binding.view!.screenshot().toBlob(blob => blob ? done(blob) : fail(new Error("The chart could not be drawn as an image")), "image/png"));

export function SnapshotMenu({ ctx, chartId, binding, name }: { ctx: ModuleContext; chartId: string; binding: () => ChartBinding | undefined; name: string }) {
  const toast = useTxToast();
  const fail = (error: unknown) => toast({ title: "Couldn't finish", detail: message(error), tone: "failed" });
  const stamp = () => new Date().toISOString().slice(0, 16).replace(/[:T]/g, "-");
  const exportAs = (format: "csv" | "json" | "parquet") => post(ctx, "/charts/export", { chartId, format }).then(file => {
    save(new Blob([Uint8Array.from(atob(file.data), char => char.charCodeAt(0))], { type: file.mediaType }), file.filename);
    toast({ title: `Exported ${file.rows.toLocaleString()} rows`, detail: file.filename });
  }, fail);
  const shown = binding()?.view;
  return <TxMenu label="Snapshot and export" align="end" trigger={<TxButton variant="tool" size="sm" aria-label="Snapshot and export"><TxIcon name="camera" /></TxButton>}
    items={[
      ...(shown ? [
        { id: "download", label: "Download image", icon: "download" as const, onSelect: () => void imageOf(binding()!).then(blob => save(blob, `${name}_${stamp()}.png`), fail) },
        { id: "copy", label: "Copy image", icon: "copy" as const, shortcut: "Ctrl+Shift+S", onSelect: () => void imageOf(binding()!)
          .then(blob => navigator.clipboard.write([new ClipboardItem({ "image/png": blob })])).then(() => toast({ title: "Image copied" }), fail) },
      ] : []),
      { id: "csv", label: "Export data as CSV", separated: Boolean(shown), group: "Data", onSelect: () => void exportAs("csv") },
      { id: "json", label: "Export data as JSON", onSelect: () => void exportAs("json") },
      { id: "parquet", label: "Export data as Parquet", onSelect: () => void exportAs("parquet") },
    ]} />;
}
