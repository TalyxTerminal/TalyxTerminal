/* JSON a cell displayed, as a tree: the top level open, everything under it folded to a one-line summary ({3 keys}, [120 items]) that opens on a click. */
import { t } from "../i18n";
import { useState } from "react";
import { TxIcon, type Json } from "@talyx/sdk";

const SHOWN = 100;   // items of one object or array drawn before "N more"

const summary = (value: Json) => Array.isArray(value) ? (value.length === 1 ? t("[{count} item]", { count: value.length }) : t("[{count} items]", { count: value.length }))
  : (Object.keys(value as object).length === 1 ? t("{{count} key}", { count: 1 }) : t("{{count} keys}", { count: Object.keys(value as object).length }));

function Leaf({value}: {value: Json}) {
  if (value === null) return <span data-kind="null">{t("null")}</span>;
  if (typeof value === "string") return <span data-kind="string">{JSON.stringify(value)}</span>;
  return <span data-kind={typeof value}>{String(value)}</span>;
}

function Node({name, value, open: openAtFirst}: {name: string | null; value: Json; open: boolean}) {
  const [open, setOpen] = useState(openAtFirst);
  const [all, setAll] = useState(false);
  const label = name === null ? null : <span className="qx-nb-jkey">{name}: </span>;
  if (value === null || typeof value !== "object") return <div className="qx-nb-jrow">{label}<Leaf value={value} /></div>;
  const entries: [string, Json][] = Array.isArray(value) ? value.map((item, index) => [String(index), item]) : Object.entries(value);
  if (entries.length === 0) return <div className="qx-nb-jrow">{label}<span>{Array.isArray(value) ? "[]" : "{}"}</span></div>;
  const shown = all ? entries : entries.slice(0, SHOWN);
  return <div>
    <button type="button" className="qx-nb-jrow qx-nb-jtoggle" aria-expanded={open} onClick={() => setOpen(!open)}>
      <TxIcon name="chevron" className="qx-nb-jarrow" data-open={open ? "" : undefined} />{label}<span className="qx-nb-jsum">{summary(value)}</span></button>
    {open && <div className="qx-nb-jkids">
      {shown.map(([key, item]) => <Node key={key} name={key} value={item} open={false} />)}
      {shown.length < entries.length && <button type="button" className="qx-nb-jrow qx-nb-jtoggle" onClick={() => setAll(true)}>{t("{entriesCount} more", { entriesCount: (entries.length - shown.length).toLocaleString("en-US") })}</button>}
    </div>}
  </div>;
}

export function JsonTree({value}: {value: Json}) {
  return <div className="qx-nb-json"><Node name={null} value={value} open /></div>;
}
