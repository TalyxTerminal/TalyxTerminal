/* Watchlists: the workspace's lists (Chart's backend keeps them; the workspace stream brings every change) and Market's
   quotes for the rows shown. */
import { useEffect, useState } from "react";
import type { Json, ModuleContext } from "@talyx/sdk";
import type { Instrument } from "@talyx/vocab";
import type { ContractTypes } from "../../../market/public/ts/types";
import { post } from "../api";
import { message, store } from "../store";
import type { Section, Watchlist } from "../wire";

type QuotesItem = ContractTypes["stream:quotes"]["item"]["items"][number];
export type QuoteRow = Pick<QuotesItem, "quote" | "problem">;

/** One change to a list at its current revision (a list changed elsewhere first is refused and the refusal shown). */
export async function editWatchlist(ctx: ModuleContext, watchlistId: string, change: (list: Watchlist) => { name?: string; sections?: Section[] } | null): Promise<void> {
  const list = store.getState().watchlists[watchlistId], patch = change(list);
  if (patch) await post(ctx, "/watchlists/update", { watchlistId, revision: list.revision, ...patch });
}

/** A new symbol goes at the end of the list's first section (a list without sections gets one). */
export function addToWatchlist(ctx: ModuleContext, watchlistId: string, instrument: Instrument): Promise<void> {
  return editWatchlist(ctx, watchlistId, list => {
    if (list.sections.some(section => section.members.some(member => member.instrument.instrumentId === instrument.instrumentId))) return null;
    const [first, ...rest] = list.sections.length ? list.sections : [{ id: "main", name: "", members: [] }];
    return { sections: [{ ...first, members: [...first.members, { instrument }] }, ...rest] };
  });
}

export function removeFromWatchlist(ctx: ModuleContext, watchlistId: string, instrumentId: string): Promise<void> {
  return editWatchlist(ctx, watchlistId, list => ({
    sections: list.sections.map(section => ({ ...section, members: section.members.filter(member => member.instrument.instrumentId !== instrumentId) })),
  }));
}

/** Market's quotes for these instruments; the set is subscribed again when it changes (Market keeps the upstreams). */
export function useQuotes(ctx: ModuleContext, instruments: Instrument[]): { quotes: Record<string, QuoteRow>; error: string | null } {
  const [quotes, setQuotes] = useState<Record<string, QuoteRow>>({});
  const [error, setError] = useState<string | null>(null);
  const key = instruments.map(item => item.instrumentId).join(",");
  useEffect(() => {
    if (!instruments.length) return;
    setError(null);
    const params = { instruments } as ContractTypes["stream:quotes"]["params"];
    const subscription = ctx.streams.subscribe("market.quotes", params as unknown as Json, value => {
      const item = value as unknown as ContractTypes["stream:quotes"]["item"];
      setQuotes(current => ({ ...current, ...Object.fromEntries(item.items.map(row => [row.instrumentId, { quote: row.quote, problem: row.problem }])) }));
    }, { onEnd: failed => { if (failed) setError(failed.message); } });
    const opened = subscription.then(value => value, failed => { setError(message(failed)); return null; });
    return () => { void opened.then(value => value?.close()); };
  // The instruments are keyed by id: the same set is the same subscription.
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [ctx, key]);
  return { quotes, error };
}
