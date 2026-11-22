/** A DataFrame output as the table explorer (the draft's): its size, each column with its kind and missing count and a small histogram for numbers, the first rows, sortable by a column. */
import { t } from "../i18n";
import { useState } from "react";
import type { Json } from "@talyx/sdk";
import "./notebook.css";

export interface TableData { shape: [number, number]; columns: { name: string; type: "int" | "float" | "bool" | "date" | "text"; missing: number; hist?: number[] }[]; rows: Json[][]; size: number | null }

const grouped = (value: number) => value.toLocaleString("en-US");
const sizeText = (bytes: number | null) => bytes === null ? "—" : bytes < 1024 ? `${bytes} B` : bytes < 1024 ** 2 ? t("{value} KB", { value: Math.round(bytes / 1024) }) : t("{value} MB", { value: (bytes / 1024 ** 2).toFixed(1) });
/** The decimals a float column is written with: as many as its most precise shown value needs (six significant digits at most), the same on every row so the points line up. */
export const floatDigits = (values: Json[]) => Math.min(6, Math.max(0, ...values.map(value => typeof value === "number" && Number.isFinite(value) ? (Number(value.toPrecision(6)).toString().split("e")[0].split(".")[1] ?? "").length : 0)));
const cell = (value: Json, type: string, digits: number) => value === null ? "" : typeof value === "number" ? (type === "int" ? grouped(value) : Number.isFinite(value) ? value.toLocaleString("en-US", {minimumFractionDigits: digits, maximumFractionDigits: digits}) : String(value)) : typeof value === "boolean" ? (value ? "True" : "False") : type === "date" ? String(value).replace(/Z$/, "").replace(/T00:00:00(\.0+)?$/, "").replace("T", " ").replace(/\.0+$/, "") : String(value);

export function TableExplorer({ table }: { table: TableData }) {
  const [sort, setSort] = useState<{ column: number; direction: 1 | -1 } | null>(null);
  const rows = sort === null ? table.rows : [...table.rows].sort((a, b) => {
    const x = a[sort.column], y = b[sort.column];
    if (x === y) return 0;
    if (x === null) return 1;
    if (y === null) return -1;
    return (x > y ? 1 : -1) * sort.direction;
  });
  const digits = table.columns.map((column, index) => column.type === "float" ? floatDigits(table.rows.map(row => row[index])) : 0);
  return <div className="qv-notebook-explorer">
    <div className="qv-notebook-meta"><span>{t.rich("{rows} rows × {columns} columns", { rows: <b>{grouped(table.shape[0])}</b>, columns: <b>{table.shape[1]}</b> })}</span>{table.rows.length < table.shape[0] && <span>{t("first {rowsCount}", { rowsCount: table.rows.length })}</span>}<span className="tx-spacer" /><span>{sizeText(table.size)}</span></div>
    <div className="qv-notebook-explorerbody"><table className="qv-table"><thead><tr>{table.columns.map((column, index) =>
      <th key={index} className={column.type === "float" || column.type === "int" ? undefined : "l"} onClick={() => setSort({ column: index, direction: sort?.column === index ? (sort.direction === 1 ? -1 : 1) : -1 })}>
        {column.name}{sort?.column === index ? (sort.direction > 0 ? " ↑" : " ↓") : ""}<span className="qv-notebook-coltype">{t("{type}, {missing} missing", { type: column.type, missing: column.missing })}</span>
        {column.hist !== undefined && column.hist.length > 0 && <Histogram bars={column.hist} />}</th>)}</tr></thead>
      <tbody>{rows.map((row, i) => <tr key={i}>{row.map((value, j) => <td key={j} className={table.columns[j].type === "float" || table.columns[j].type === "int" ? undefined : "l"}>{cell(value, table.columns[j].type, digits[j])}</td>)}</tr>)}</tbody></table></div>
  </div>;
}

function Histogram({ bars }: { bars: number[] }) {
  const top = Math.max(...bars, 1);
  return <svg className="qv-notebook-hist" width="60" height="12" aria-hidden="true">{bars.map((count, index) => <rect key={index} x={index * 5} y={12 - Math.max(1, (count / top) * 12)} width="4" height={Math.max(1, (count / top) * 12)} />)}</svg>;
}
