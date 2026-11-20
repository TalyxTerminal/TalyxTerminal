/* A TradingView picker list: grouped rows (groups fold), the current one marked, a star on each row to pin it. Used by
   the timeframe and chart type menus. */
import { useState, type ReactNode } from "react";
import { TxButton, TxIcon, TxIconButton } from "@talyx/sdk";

export interface StarRow { id: string; label: string; glyph?: ReactNode; starred: boolean }

export function StarList({ groups, current, onPick, onStar }: {
  groups: { name: string | null; rows: StarRow[] }[]; current: string; onPick(id: string): void; onStar(id: string): void;
}) {
  const [folded, setFolded] = useState<Record<string, boolean>>({});
  return <div className="chart-starlist" role="listbox">
    {groups.map((group, index) => <div key={index} className="chart-starlist-group">
      {group.name && <TxButton variant="row" size="sm" className="chart-starlist-head" aria-expanded={!folded[group.name]}
        onClick={() => setFolded({ ...folded, [group.name!]: !folded[group.name!] })}>
        <span>{group.name}</span><TxIcon name={folded[group.name] ? "chevron" : "chevron-down"} />
      </TxButton>}
      {!(group.name && folded[group.name]) && group.rows.map(row => <div key={row.id} className="chart-starlist-row" data-starred={row.starred}>
        <TxButton variant="row" size="sm" role="option" aria-selected={row.id === current} onClick={() => onPick(row.id)}>
          {row.glyph}<span>{row.label}</span>
        </TxButton>
        <TxIconButton size="sm" icon="star" label={row.starred ? "Remove from favorites" : "Add to favorites"} tone={row.starred ? "active" : "default"} onClick={() => onStar(row.id)} />
      </div>)}
    </div>)}
  </div>;
}
