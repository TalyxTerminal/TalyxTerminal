import { displayIndicator } from "../indicatorText";
import { t } from "../i18n";
/* Indicators, Metrics & Strategies, TradingView's library: categories on the left (favorites, built-in by kind, the
   user's indicators), search by full name and short name on top; a click adds to the active chart and the dialog stays.
   A row's {} on hover opens its code in the editor under the chart. */
import { useMemo, useState } from "react";
import { TxButton, TxDialog, TxEmpty, TxIconButton, TxSearch, type ModuleContext } from "@talyx/sdk";
import { commit, openOverlay, openSource, store, toggleFavorite, useStore } from "../store";
import { PRICE_PANE, type CatalogEntry, type Scalar } from "../wire";

const key = (entry: CatalogEntry) => `${entry.source}:${entry.id}`;
const fresh = (prefix: string) => `${prefix}-${crypto.randomUUID().slice(0, 8)}`;

export function addIndicator(ctx: ModuleContext, chartId: string, entry: CatalogEntry): Promise<boolean> {
  const params = Object.fromEntries(entry.inputs.map(input => [input.id, input.default as Scalar]));
  return commit(ctx, chartId, [{ type: "indicator.add", value: {
    id: fresh("ind"), definition: { source: entry.source, id: entry.id }, params, paneId: entry.overlay ? PRICE_PANE : fresh("pane"), visible: true, styles: {},
  } }]);
}

export function IndicatorsDialog({ ctx, panelId }: { ctx: ModuleContext; panelId: string }) {
  const entries = useStore(state => state.catalog);
  const catalog = useMemo(() => entries?.map(displayIndicator) ?? null, [entries]);
  const favorites = useStore(state => state.layouts[panelId].favorites.indicators);
  const [query, setQuery] = useState(""), [section, setSection] = useState("Built-in");
  const categories = useMemo(() => [...new Set((catalog ?? []).filter(item => item.source === "builtin").map(item => item.category))].sort(), [catalog]);
  const words = query.trim().toLowerCase().split(/\s+/).filter(Boolean);
  const rows = (catalog ?? []).filter(entry => {
    if (words.length) return words.every(word => `${entry.title} ${entry.shortTitle}`.toLowerCase().includes(word));
    if (section === "Favorites") return favorites.includes(key(entry));
    if (section === "My indicators") return entry.source === "user";
    if (section === "Built-in") return entry.source === "builtin";
    return entry.source === "builtin" && entry.category === section;
  }).sort((a, b) => a.title.localeCompare(b.title));
  const sections = ["Favorites", "My indicators", "Built-in", ...categories];
  return <TxDialog open onOpenChange={open => { if (!open) openOverlay(panelId, null); }} title={t("Indicators")} size="wide" footer={false}>
    <div className="chart-library">
      <TxSearch value={query} onValueChange={setQuery} placeholder={t("Search")} autoFocus />
      <div className="chart-library-body">
        <nav className="chart-library-nav" aria-label={t("Indicator categories")}>
          {sections.map(name => <TxButton key={name} variant="row" size="sm" aria-current={!words.length && section === name} onClick={() => { setSection(name); setQuery(""); }}
            className={name === "Built-in" || name === "My indicators" || name === "Favorites" ? undefined : "chart-library-sub"}>{name === "Built-in" ? t("Built-in") : name === "Favorites" ? t("Favorites") : name === "My indicators" ? t("My indicators") : name}</TxButton>)}
        </nav>
        <div className="chart-library-list" role="list">
          {catalog === null && <TxEmpty title={t("Loading indicators")} />}
          {catalog !== null && rows.length === 0 && <TxEmpty title={words.length ? t("Nothing matches") : section === "Favorites" ? t("No favorites yet") : section === "My indicators" ? t("No indicators of your own yet") : t("Nothing here")} />}
          {rows.map(entry => <div key={key(entry)} className="chart-library-row" role="listitem">
            <TxIconButton size="sm" icon="star" tone={favorites.includes(key(entry)) ? "active" : "default"}
              label={favorites.includes(key(entry)) ? t("Remove from favorites") : t("Add to favorites")} onClick={() => toggleFavorite(panelId, "indicators", key(entry))} />
            <TxButton variant="row" size="sm" disabled={Boolean(entry.error)} title={entry.error ?? entry.description}
              onClick={() => void addIndicator(ctx, store.getState().layouts[panelId].active, entry)}>
              <span>{entry.title}</span>{entry.error && <span className="chart-library-error">{entry.error}</span>}
            </TxButton>
            <TxIconButton size="sm" icon="code" label={t("Source code")}
              onClick={() => { openSource(panelId, { source: entry.source, id: entry.id }); openOverlay(panelId, null); }} />
          </div>)}
        </div>
      </div>
    </div>
  </TxDialog>;
}
