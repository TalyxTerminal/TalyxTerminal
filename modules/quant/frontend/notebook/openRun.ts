import { createContext } from "react";

/** Opens a run of an object in its own page (the backtest block's "Open as a run of the strategy"). */
export const OpenRunContext = createContext<((objectId: string, runId: string) => void) | null>(null);

/** Reads the state of a run by id: a backtest block that was left running when its panel closed asks it, to say how the backtest ended. */
export const RunStateContext = createContext<((runId: string) => Promise<{state: string; objectId: string}>) | null>(null);
