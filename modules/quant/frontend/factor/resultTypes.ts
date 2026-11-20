/* The shapes of a factor's Results (backend/factor_runs.py and native/factor_results.py write them); kept here, beside the
   page that reads them, since the generated contract no longer carries private route shapes. tests/fixtures/quant/factor-results.schema.json
   holds the same shapes as JSON Schema; test_quant_factor_results checks what the backend writes against it. */
export type FactorResultsTab =
  | FactorReturnsTab
  | FactorIcTab
  | FactorIndustryTab
  | FactorStyleTab
  | FactorTurnoverTab
  | FactorCostsTab
  | FactorEventTab;


export interface FactorResultsView {
  run: {
    id: string;
    number: number;
    /**
     * The Results' state, short: state, progress, tabs, message, seconds.
     */
    results: {
      [k: string]: unknown;
    };
    /**
     * 0 to 1 while the Results build in the background, else null.
     */
    resultsProgress: number | null;
    [k: string]: unknown;
  };
  results: FactorResultsState;
  tab: null | FactorResultsTab;
  cell?: number;
}
/**
 * Where a run's Results are. none: a held-out validation or an unfinished run has none; notbuilt: not built yet (a sweep combination, or an earlier run put aside for a newer one), build_factor_results builds them; queued and running: building in the background, progress is the share of tabs done; done; failed (message says why); interrupted: Quant closed while building. Tabs that are done can be read while the rest build.
 */
export interface FactorResultsState {
  state: "none" | "notbuilt" | "queued" | "running" | "done" | "failed" | "interrupted";
  progress: number;
  tabs: {
    [k: string]: "waiting" | "running" | "done" | "failed";
  };
  message?: string;
  seconds?: number;
  computeSeconds?: number;
  queuedAt?: number;
  startedAt?: number;
  endedAt?: number;
  about?: {
    styles?: string;
    versions?: {
      [k: string]: string;
    };
    seconds?: number;
    unit?: string;
    shares?: boolean;
  };
}
export interface FactorReturnsTab {
  tab: "returns";
  holding: number;
  unit: "day" | "hour" | "bar";
  periods: number[];
  periodLabels: string[];
  /**
   * Each table or figure that could not be made, by its name, and why; tab is set when the whole tab failed.
   */
  missing: {
    [k: string]: string;
  };
  seconds?: number;
  failed?: string;
  /**
   * groups: each group's cumulative return relative to all names and the long–short net (TALYX draws it; click a day, other runs dashed). meanByGroup and violin: Alphalens' return by group, bps a bar. factorWeighted: Alphalens' factor-weighted long–short, cumulative from 1. spread: top minus bottom at the holding, bps, 100% gross, 1-month mean ± 0.5 standard error.
   */
  figures: {
    groups?: FactorResultsFigure;
    meanByGroup?: FactorResultsFigure1;
    violin?: FactorResultsFigure2;
    factorWeighted?: FactorResultsFigure3;
    spread?: FactorResultsFigure4;
  };
  /**
   * Alphalens' quantile statistics: the factor values of each group pooled over the period.
   */
  groupStats?: {
    group: number;
    min: number | null;
    max: number | null;
    mean: number | null;
    std: number | null;
    count: number;
    pct: number | null;
  }[];
  /**
   * Alphalens' returns table for each period: alpha a year (a share), beta, the top and bottom group's mean and the spread (bps per period, spread at 100% gross).
   */
  returnsByPeriod?: {
    period: number;
    alpha: number | null;
    beta: number | null;
    top: number | null;
    bottom: number | null;
    spread: number | null;
  }[];
  /**
   * Mean return by group over the whole period, every name together (not by sector): bps per bar for each period, with standard errors.
   */
  meanByGroup?: {
    group: number;
    means: (number | null)[];
    errors: (number | null)[];
  }[];
  spreadPeriod?: number;
  groupCount?: number;
}
/**
 * One figure of a Results tab: the figure, or why it could not be drawn (unavailable). The figure is left out when read without figures.
 */
export interface FactorResultsFigure {
  key: string;
  title: string;
  figure?: FactorPlotlyFigure;
  unavailable?: string;
}
/**
 * A Plotly figure drawn by the backend; layout.meta.key is the figure's fixed identity (factor.results.<name>, factor.groups ...).
 */
export interface FactorPlotlyFigure {
  data: FactorPlotlyTrace[];
  layout: {
    meta: {
      key: string;
    };
    [k: string]: unknown;
  };
}
/**
 * One Plotly trace. meta.role says what it is, and the display colours it by that: main (the text colour), benchmark (thin grey, also reference lines such as zero, a mean or the 45-degree line), compare (another run, grey dashed), group (one step of a neutral ramp, meta.index of meta.of), updown (the user's up and down colours by sign), attention (orange: something to deal with). meta.key is the trace's identity inside its figure.
 */
export interface FactorPlotlyTrace {
  type?: string;
  name?: string;
  meta: {
    role: "main" | "benchmark" | "compare" | "group" | "updown" | "attention" | "detail";
    key: string;
    index?: number;
    of?: number;
    side?: "up" | "down";
  };
  [k: string]: unknown;
}
/**
 * One figure of a Results tab: the figure, or why it could not be drawn (unavailable). The figure is left out when read without figures.
 */
export interface FactorResultsFigure1 {
  key: string;
  title: string;
  figure?: FactorPlotlyFigure1;
  unavailable?: string;
}
/**
 * A Plotly figure drawn by the backend; layout.meta.key is the figure's fixed identity (factor.results.<name>, factor.groups ...).
 */
export interface FactorPlotlyFigure1 {
  data: FactorPlotlyTrace1[];
  layout: {
    meta: {
      key: string;
    };
    [k: string]: unknown;
  };
}
/**
 * One Plotly trace. meta.role says what it is, and the display colours it by that: main (the text colour), benchmark (thin grey, also reference lines such as zero, a mean or the 45-degree line), compare (another run, grey dashed), group (one step of a neutral ramp, meta.index of meta.of), updown (the user's up and down colours by sign), attention (orange: something to deal with). meta.key is the trace's identity inside its figure.
 */
export interface FactorPlotlyTrace1 {
  type?: string;
  name?: string;
  meta: {
    role: "main" | "benchmark" | "compare" | "group" | "updown" | "attention" | "detail";
    key: string;
    index?: number;
    of?: number;
    side?: "up" | "down";
  };
  [k: string]: unknown;
}
/**
 * One figure of a Results tab: the figure, or why it could not be drawn (unavailable). The figure is left out when read without figures.
 */
export interface FactorResultsFigure2 {
  key: string;
  title: string;
  figure?: FactorPlotlyFigure2;
  unavailable?: string;
}
/**
 * A Plotly figure drawn by the backend; layout.meta.key is the figure's fixed identity (factor.results.<name>, factor.groups ...).
 */
export interface FactorPlotlyFigure2 {
  data: FactorPlotlyTrace2[];
  layout: {
    meta: {
      key: string;
    };
    [k: string]: unknown;
  };
}
/**
 * One Plotly trace. meta.role says what it is, and the display colours it by that: main (the text colour), benchmark (thin grey, also reference lines such as zero, a mean or the 45-degree line), compare (another run, grey dashed), group (one step of a neutral ramp, meta.index of meta.of), updown (the user's up and down colours by sign), attention (orange: something to deal with). meta.key is the trace's identity inside its figure.
 */
export interface FactorPlotlyTrace2 {
  type?: string;
  name?: string;
  meta: {
    role: "main" | "benchmark" | "compare" | "group" | "updown" | "attention" | "detail";
    key: string;
    index?: number;
    of?: number;
    side?: "up" | "down";
  };
  [k: string]: unknown;
}
/**
 * One figure of a Results tab: the figure, or why it could not be drawn (unavailable). The figure is left out when read without figures.
 */
export interface FactorResultsFigure3 {
  key: string;
  title: string;
  figure?: FactorPlotlyFigure3;
  unavailable?: string;
}
/**
 * A Plotly figure drawn by the backend; layout.meta.key is the figure's fixed identity (factor.results.<name>, factor.groups ...).
 */
export interface FactorPlotlyFigure3 {
  data: FactorPlotlyTrace3[];
  layout: {
    meta: {
      key: string;
    };
    [k: string]: unknown;
  };
}
/**
 * One Plotly trace. meta.role says what it is, and the display colours it by that: main (the text colour), benchmark (thin grey, also reference lines such as zero, a mean or the 45-degree line), compare (another run, grey dashed), group (one step of a neutral ramp, meta.index of meta.of), updown (the user's up and down colours by sign), attention (orange: something to deal with). meta.key is the trace's identity inside its figure.
 */
export interface FactorPlotlyTrace3 {
  type?: string;
  name?: string;
  meta: {
    role: "main" | "benchmark" | "compare" | "group" | "updown" | "attention" | "detail";
    key: string;
    index?: number;
    of?: number;
    side?: "up" | "down";
  };
  [k: string]: unknown;
}
/**
 * One figure of a Results tab: the figure, or why it could not be drawn (unavailable). The figure is left out when read without figures.
 */
export interface FactorResultsFigure4 {
  key: string;
  title: string;
  figure?: FactorPlotlyFigure4;
  unavailable?: string;
}
/**
 * A Plotly figure drawn by the backend; layout.meta.key is the figure's fixed identity (factor.results.<name>, factor.groups ...).
 */
export interface FactorPlotlyFigure4 {
  data: FactorPlotlyTrace4[];
  layout: {
    meta: {
      key: string;
    };
    [k: string]: unknown;
  };
}
/**
 * One Plotly trace. meta.role says what it is, and the display colours it by that: main (the text colour), benchmark (thin grey, also reference lines such as zero, a mean or the 45-degree line), compare (another run, grey dashed), group (one step of a neutral ramp, meta.index of meta.of), updown (the user's up and down colours by sign), attention (orange: something to deal with). meta.key is the trace's identity inside its figure.
 */
export interface FactorPlotlyTrace4 {
  type?: string;
  name?: string;
  meta: {
    role: "main" | "benchmark" | "compare" | "group" | "updown" | "attention" | "detail";
    key: string;
    index?: number;
    of?: number;
    side?: "up" | "down";
  };
  [k: string]: unknown;
}
export interface FactorIcTab {
  tab: "ic";
  holding: number;
  unit: "day" | "hour" | "bar";
  periods: number[];
  periodLabels: string[];
  /**
   * Each table or figure that could not be made, by its name, and why; tab is set when the whole tab failed.
   */
  missing: {
    [k: string]: string;
  };
  seconds?: number;
  failed?: string;
  figures: {
    daily?: FactorResultsFigure5;
    histogram?: FactorResultsFigure6;
    qq?: FactorResultsFigure7;
    years?: FactorResultsFigure8;
    monthly?: FactorResultsFigure9;
  };
  /**
   * Alphalens' information table for each period, with TALYX's overlap-corrected (Newey–West) t and its p.
   */
  information?: {
    period: number;
    mean: number | null;
    std: number | null;
    riskAdjusted: number | null;
    t: number | null;
    p: number | null;
    tOverlap: number | null;
    pOverlap: number | null;
    skew: number | null;
    kurtosis: number | null;
  }[];
  daily?: {
    mean: number | null;
    std: number | null;
    days: number;
  };
  /**
   * Each calendar year in sample: rank IC, its overlap-corrected t, long–short net a year, Sharpe, turnover; whole is false for a part year.
   */
  years?: {
    year: number;
    whole: boolean;
    rankIc: number | null;
    t: number | null;
    longShortNet: number | null;
    sharpe: number | null;
    turnover: number | null;
    days: number;
  }[];
  /**
   * Where the held-out period starts (ms); not in any number here.
   */
  heldOut?: null | {
    from: number;
  };
}
/**
 * One figure of a Results tab: the figure, or why it could not be drawn (unavailable). The figure is left out when read without figures.
 */
export interface FactorResultsFigure5 {
  key: string;
  title: string;
  figure?: FactorPlotlyFigure5;
  unavailable?: string;
}
/**
 * A Plotly figure drawn by the backend; layout.meta.key is the figure's fixed identity (factor.results.<name>, factor.groups ...).
 */
export interface FactorPlotlyFigure5 {
  data: FactorPlotlyTrace5[];
  layout: {
    meta: {
      key: string;
    };
    [k: string]: unknown;
  };
}
/**
 * One Plotly trace. meta.role says what it is, and the display colours it by that: main (the text colour), benchmark (thin grey, also reference lines such as zero, a mean or the 45-degree line), compare (another run, grey dashed), group (one step of a neutral ramp, meta.index of meta.of), updown (the user's up and down colours by sign), attention (orange: something to deal with). meta.key is the trace's identity inside its figure.
 */
export interface FactorPlotlyTrace5 {
  type?: string;
  name?: string;
  meta: {
    role: "main" | "benchmark" | "compare" | "group" | "updown" | "attention" | "detail";
    key: string;
    index?: number;
    of?: number;
    side?: "up" | "down";
  };
  [k: string]: unknown;
}
/**
 * One figure of a Results tab: the figure, or why it could not be drawn (unavailable). The figure is left out when read without figures.
 */
export interface FactorResultsFigure6 {
  key: string;
  title: string;
  figure?: FactorPlotlyFigure6;
  unavailable?: string;
}
/**
 * A Plotly figure drawn by the backend; layout.meta.key is the figure's fixed identity (factor.results.<name>, factor.groups ...).
 */
export interface FactorPlotlyFigure6 {
  data: FactorPlotlyTrace6[];
  layout: {
    meta: {
      key: string;
    };
    [k: string]: unknown;
  };
}
/**
 * One Plotly trace. meta.role says what it is, and the display colours it by that: main (the text colour), benchmark (thin grey, also reference lines such as zero, a mean or the 45-degree line), compare (another run, grey dashed), group (one step of a neutral ramp, meta.index of meta.of), updown (the user's up and down colours by sign), attention (orange: something to deal with). meta.key is the trace's identity inside its figure.
 */
export interface FactorPlotlyTrace6 {
  type?: string;
  name?: string;
  meta: {
    role: "main" | "benchmark" | "compare" | "group" | "updown" | "attention" | "detail";
    key: string;
    index?: number;
    of?: number;
    side?: "up" | "down";
  };
  [k: string]: unknown;
}
/**
 * One figure of a Results tab: the figure, or why it could not be drawn (unavailable). The figure is left out when read without figures.
 */
export interface FactorResultsFigure7 {
  key: string;
  title: string;
  figure?: FactorPlotlyFigure7;
  unavailable?: string;
}
/**
 * A Plotly figure drawn by the backend; layout.meta.key is the figure's fixed identity (factor.results.<name>, factor.groups ...).
 */
export interface FactorPlotlyFigure7 {
  data: FactorPlotlyTrace7[];
  layout: {
    meta: {
      key: string;
    };
    [k: string]: unknown;
  };
}
/**
 * One Plotly trace. meta.role says what it is, and the display colours it by that: main (the text colour), benchmark (thin grey, also reference lines such as zero, a mean or the 45-degree line), compare (another run, grey dashed), group (one step of a neutral ramp, meta.index of meta.of), updown (the user's up and down colours by sign), attention (orange: something to deal with). meta.key is the trace's identity inside its figure.
 */
export interface FactorPlotlyTrace7 {
  type?: string;
  name?: string;
  meta: {
    role: "main" | "benchmark" | "compare" | "group" | "updown" | "attention" | "detail";
    key: string;
    index?: number;
    of?: number;
    side?: "up" | "down";
  };
  [k: string]: unknown;
}
/**
 * One figure of a Results tab: the figure, or why it could not be drawn (unavailable). The figure is left out when read without figures.
 */
export interface FactorResultsFigure8 {
  key: string;
  title: string;
  figure?: FactorPlotlyFigure8;
  unavailable?: string;
}
/**
 * A Plotly figure drawn by the backend; layout.meta.key is the figure's fixed identity (factor.results.<name>, factor.groups ...).
 */
export interface FactorPlotlyFigure8 {
  data: FactorPlotlyTrace8[];
  layout: {
    meta: {
      key: string;
    };
    [k: string]: unknown;
  };
}
/**
 * One Plotly trace. meta.role says what it is, and the display colours it by that: main (the text colour), benchmark (thin grey, also reference lines such as zero, a mean or the 45-degree line), compare (another run, grey dashed), group (one step of a neutral ramp, meta.index of meta.of), updown (the user's up and down colours by sign), attention (orange: something to deal with). meta.key is the trace's identity inside its figure.
 */
export interface FactorPlotlyTrace8 {
  type?: string;
  name?: string;
  meta: {
    role: "main" | "benchmark" | "compare" | "group" | "updown" | "attention" | "detail";
    key: string;
    index?: number;
    of?: number;
    side?: "up" | "down";
  };
  [k: string]: unknown;
}
/**
 * One figure of a Results tab: the figure, or why it could not be drawn (unavailable). The figure is left out when read without figures.
 */
export interface FactorResultsFigure9 {
  key: string;
  title: string;
  figure?: FactorPlotlyFigure9;
  unavailable?: string;
}
/**
 * A Plotly figure drawn by the backend; layout.meta.key is the figure's fixed identity (factor.results.<name>, factor.groups ...).
 */
export interface FactorPlotlyFigure9 {
  data: FactorPlotlyTrace9[];
  layout: {
    meta: {
      key: string;
    };
    [k: string]: unknown;
  };
}
/**
 * One Plotly trace. meta.role says what it is, and the display colours it by that: main (the text colour), benchmark (thin grey, also reference lines such as zero, a mean or the 45-degree line), compare (another run, grey dashed), group (one step of a neutral ramp, meta.index of meta.of), updown (the user's up and down colours by sign), attention (orange: something to deal with). meta.key is the trace's identity inside its figure.
 */
export interface FactorPlotlyTrace9 {
  type?: string;
  name?: string;
  meta: {
    role: "main" | "benchmark" | "compare" | "group" | "updown" | "attention" | "detail";
    key: string;
    index?: number;
    of?: number;
    side?: "up" | "down";
  };
  [k: string]: unknown;
}
export interface FactorIndustryTab {
  tab: "industry";
  holding: number;
  unit: "day" | "hour" | "bar";
  periods: number[];
  periodLabels: string[];
  /**
   * Each table or figure that could not be made, by its name, and why; tab is set when the whole tab failed.
   */
  missing: {
    [k: string]: string;
  };
  seconds?: number;
  failed?: string;
  figures: {
    sectors?: FactorResultsFigure10;
    sizeAndPeriod?: FactorResultsFigure11;
    decay?: FactorResultsFigure12;
  };
  /**
   * Alphalens' mean IC inside each sector (the industry Market gives) at the holding period, with its Newey-West t (lag = holding - 1) and the days it has an IC on.
   */
  sectors?: {
    sector: string;
    rankIc: number | null;
    t: number | null;
    days: number;
    names: number;
    observations: number;
  }[];
  /**
   * Rank IC inside each third by size (each day) and in each half of the days.
   */
  sizeAndPeriod?: {
    name: string;
    kind: "size" | "period";
    rankIc: number | null;
    note: string;
  }[];
  /**
   * Rank IC for holding 1 to 20, before taking out industry and size and after (the run's way, or industry and size when the run took out nothing).
   */
  decay?: {
    before: (number | null)[];
    after: (number | null)[];
    neutralize: string;
    run: string;
  };
  method?: {
    neutralize: string;
    extremes: string;
    scale: string;
    industry: string;
    size: string;
    notes: string[];
  };
}
/**
 * One figure of a Results tab: the figure, or why it could not be drawn (unavailable). The figure is left out when read without figures.
 */
export interface FactorResultsFigure10 {
  key: string;
  title: string;
  figure?: FactorPlotlyFigure10;
  unavailable?: string;
}
/**
 * A Plotly figure drawn by the backend; layout.meta.key is the figure's fixed identity (factor.results.<name>, factor.groups ...).
 */
export interface FactorPlotlyFigure10 {
  data: FactorPlotlyTrace10[];
  layout: {
    meta: {
      key: string;
    };
    [k: string]: unknown;
  };
}
/**
 * One Plotly trace. meta.role says what it is, and the display colours it by that: main (the text colour), benchmark (thin grey, also reference lines such as zero, a mean or the 45-degree line), compare (another run, grey dashed), group (one step of a neutral ramp, meta.index of meta.of), updown (the user's up and down colours by sign), attention (orange: something to deal with). meta.key is the trace's identity inside its figure.
 */
export interface FactorPlotlyTrace10 {
  type?: string;
  name?: string;
  meta: {
    role: "main" | "benchmark" | "compare" | "group" | "updown" | "attention" | "detail";
    key: string;
    index?: number;
    of?: number;
    side?: "up" | "down";
  };
  [k: string]: unknown;
}
/**
 * One figure of a Results tab: the figure, or why it could not be drawn (unavailable). The figure is left out when read without figures.
 */
export interface FactorResultsFigure11 {
  key: string;
  title: string;
  figure?: FactorPlotlyFigure11;
  unavailable?: string;
}
/**
 * A Plotly figure drawn by the backend; layout.meta.key is the figure's fixed identity (factor.results.<name>, factor.groups ...).
 */
export interface FactorPlotlyFigure11 {
  data: FactorPlotlyTrace11[];
  layout: {
    meta: {
      key: string;
    };
    [k: string]: unknown;
  };
}
/**
 * One Plotly trace. meta.role says what it is, and the display colours it by that: main (the text colour), benchmark (thin grey, also reference lines such as zero, a mean or the 45-degree line), compare (another run, grey dashed), group (one step of a neutral ramp, meta.index of meta.of), updown (the user's up and down colours by sign), attention (orange: something to deal with). meta.key is the trace's identity inside its figure.
 */
export interface FactorPlotlyTrace11 {
  type?: string;
  name?: string;
  meta: {
    role: "main" | "benchmark" | "compare" | "group" | "updown" | "attention" | "detail";
    key: string;
    index?: number;
    of?: number;
    side?: "up" | "down";
  };
  [k: string]: unknown;
}
/**
 * One figure of a Results tab: the figure, or why it could not be drawn (unavailable). The figure is left out when read without figures.
 */
export interface FactorResultsFigure12 {
  key: string;
  title: string;
  figure?: FactorPlotlyFigure12;
  unavailable?: string;
}
/**
 * A Plotly figure drawn by the backend; layout.meta.key is the figure's fixed identity (factor.results.<name>, factor.groups ...).
 */
export interface FactorPlotlyFigure12 {
  data: FactorPlotlyTrace12[];
  layout: {
    meta: {
      key: string;
    };
    [k: string]: unknown;
  };
}
/**
 * One Plotly trace. meta.role says what it is, and the display colours it by that: main (the text colour), benchmark (thin grey, also reference lines such as zero, a mean or the 45-degree line), compare (another run, grey dashed), group (one step of a neutral ramp, meta.index of meta.of), updown (the user's up and down colours by sign), attention (orange: something to deal with). meta.key is the trace's identity inside its figure.
 */
export interface FactorPlotlyTrace12 {
  type?: string;
  name?: string;
  meta: {
    role: "main" | "benchmark" | "compare" | "group" | "updown" | "attention" | "detail";
    key: string;
    index?: number;
    of?: number;
    side?: "up" | "down";
  };
  [k: string]: unknown;
}
export interface FactorStyleTab {
  tab: "style";
  holding: number;
  unit: "day" | "hour" | "bar";
  periods: number[];
  periodLabels: string[];
  /**
   * Each table or figure that could not be made, by its name, and why; tab is set when the whole tab failed.
   */
  missing: {
    [k: string]: string;
  };
  seconds?: number;
  failed?: string;
  figures: {
    like?: FactorResultsFigure13;
  };
  /**
   * The style definitions' version (Styles v1), also in the run record.
   */
  version?: string;
  styles?: FactorStyleCorrelation[];
  /**
   * Library factors and styles by their mean daily rank correlation with this run, the largest first.
   */
  like?: {
    name: string;
    version?: string | null;
    kind: "library" | "style";
    rho: number | null;
  }[];
  /**
   * Rank IC of what is left after taking out the price styles each day, against the run's.
   */
  residual?: {
    rankIc: number | null;
    before: number | null;
    t: number | null;
    styles: string[];
  };
}
/**
 * One figure of a Results tab: the figure, or why it could not be drawn (unavailable). The figure is left out when read without figures.
 */
export interface FactorResultsFigure13 {
  key: string;
  title: string;
  figure?: FactorPlotlyFigure13;
  unavailable?: string;
}
/**
 * A Plotly figure drawn by the backend; layout.meta.key is the figure's fixed identity (factor.results.<name>, factor.groups ...).
 */
export interface FactorPlotlyFigure13 {
  data: FactorPlotlyTrace13[];
  layout: {
    meta: {
      key: string;
    };
    [k: string]: unknown;
  };
}
/**
 * One Plotly trace. meta.role says what it is, and the display colours it by that: main (the text colour), benchmark (thin grey, also reference lines such as zero, a mean or the 45-degree line), compare (another run, grey dashed), group (one step of a neutral ramp, meta.index of meta.of), updown (the user's up and down colours by sign), attention (orange: something to deal with). meta.key is the trace's identity inside its figure.
 */
export interface FactorPlotlyTrace13 {
  type?: string;
  name?: string;
  meta: {
    role: "main" | "benchmark" | "compare" | "group" | "updown" | "attention" | "detail";
    key: string;
    index?: number;
    of?: number;
    side?: "up" | "down";
  };
  [k: string]: unknown;
}
export interface FactorStyleCorrelation {
  style: string;
  family: "price" | "financial";
  measuredAs: string;
  rho?: number | null;
  unavailable?: string;
}
export interface FactorTurnoverTab {
  tab: "turnover";
  holding: number;
  unit: "day" | "hour" | "bar";
  periods: number[];
  periodLabels: string[];
  /**
   * Each table or figure that could not be made, by its name, and why; tab is set when the whole tab failed.
   */
  missing: {
    [k: string]: string;
  };
  seconds?: number;
  failed?: string;
  figures: {
    topBottom?: FactorResultsFigure14;
    autocorrelation?: FactorResultsFigure15;
  };
  /**
   * Alphalens' turnover table: each group's mean share of names new to it, and the mean rank autocorrelation, for each period.
   */
  turnover?: {
    period: number;
    groups: (number | null)[];
    autocorrelation: number | null;
  }[];
  groups?: number[];
}
/**
 * One figure of a Results tab: the figure, or why it could not be drawn (unavailable). The figure is left out when read without figures.
 */
export interface FactorResultsFigure14 {
  key: string;
  title: string;
  figure?: FactorPlotlyFigure14;
  unavailable?: string;
}
/**
 * A Plotly figure drawn by the backend; layout.meta.key is the figure's fixed identity (factor.results.<name>, factor.groups ...).
 */
export interface FactorPlotlyFigure14 {
  data: FactorPlotlyTrace14[];
  layout: {
    meta: {
      key: string;
    };
    [k: string]: unknown;
  };
}
/**
 * One Plotly trace. meta.role says what it is, and the display colours it by that: main (the text colour), benchmark (thin grey, also reference lines such as zero, a mean or the 45-degree line), compare (another run, grey dashed), group (one step of a neutral ramp, meta.index of meta.of), updown (the user's up and down colours by sign), attention (orange: something to deal with). meta.key is the trace's identity inside its figure.
 */
export interface FactorPlotlyTrace14 {
  type?: string;
  name?: string;
  meta: {
    role: "main" | "benchmark" | "compare" | "group" | "updown" | "attention" | "detail";
    key: string;
    index?: number;
    of?: number;
    side?: "up" | "down";
  };
  [k: string]: unknown;
}
/**
 * One figure of a Results tab: the figure, or why it could not be drawn (unavailable). The figure is left out when read without figures.
 */
export interface FactorResultsFigure15 {
  key: string;
  title: string;
  figure?: FactorPlotlyFigure15;
  unavailable?: string;
}
/**
 * A Plotly figure drawn by the backend; layout.meta.key is the figure's fixed identity (factor.results.<name>, factor.groups ...).
 */
export interface FactorPlotlyFigure15 {
  data: FactorPlotlyTrace15[];
  layout: {
    meta: {
      key: string;
    };
    [k: string]: unknown;
  };
}
/**
 * One Plotly trace. meta.role says what it is, and the display colours it by that: main (the text colour), benchmark (thin grey, also reference lines such as zero, a mean or the 45-degree line), compare (another run, grey dashed), group (one step of a neutral ramp, meta.index of meta.of), updown (the user's up and down colours by sign), attention (orange: something to deal with). meta.key is the trace's identity inside its figure.
 */
export interface FactorPlotlyTrace15 {
  type?: string;
  name?: string;
  meta: {
    role: "main" | "benchmark" | "compare" | "group" | "updown" | "attention" | "detail";
    key: string;
    index?: number;
    of?: number;
    side?: "up" | "down";
  };
  [k: string]: unknown;
}
export interface FactorCostsTab {
  tab: "costs";
  holding: number;
  unit: "day" | "hour" | "bar";
  periods: number[];
  periodLabels: string[];
  /**
   * Each table or figure that could not be made, by its name, and why; tab is set when the whole tab failed.
   */
  missing: {
    [k: string]: string;
  };
  seconds?: number;
  failed?: string;
  figures: {
    byCost?: FactorResultsFigure16;
  };
  /**
   * Where the long–short's cost goes, a year as shares: gross, trading on each leg and borrowing the short leg (positive amounts taken off), net; break-even cost a trade in bps.
   */
  split?: {
    gross: number | null;
    tradingLong: number | null;
    tradingShort: number | null;
    borrow: number | null;
    net: number | null;
    breakEven: number | null;
    costBps: number;
    borrowPct: number;
    traded: number | null;
  };
  /**
   * The same factor held 1, 2, 3, 5, 10 and 20 bars: rank IC, gross, turnover, costs (negative), net a year, Sharpe, break-even bps.
   */
  holdings?: {
    holding: number;
    rankIc: number | null;
    gross: number | null;
    turnover: number | null;
    costs: number | null;
    net: number | null;
    sharpe: number | null;
    breakEven: number | null;
  }[];
}
/**
 * One figure of a Results tab: the figure, or why it could not be drawn (unavailable). The figure is left out when read without figures.
 */
export interface FactorResultsFigure16 {
  key: string;
  title: string;
  figure?: FactorPlotlyFigure16;
  unavailable?: string;
}
/**
 * A Plotly figure drawn by the backend; layout.meta.key is the figure's fixed identity (factor.results.<name>, factor.groups ...).
 */
export interface FactorPlotlyFigure16 {
  data: FactorPlotlyTrace16[];
  layout: {
    meta: {
      key: string;
    };
    [k: string]: unknown;
  };
}
/**
 * One Plotly trace. meta.role says what it is, and the display colours it by that: main (the text colour), benchmark (thin grey, also reference lines such as zero, a mean or the 45-degree line), compare (another run, grey dashed), group (one step of a neutral ramp, meta.index of meta.of), updown (the user's up and down colours by sign), attention (orange: something to deal with). meta.key is the trace's identity inside its figure.
 */
export interface FactorPlotlyTrace16 {
  type?: string;
  name?: string;
  meta: {
    role: "main" | "benchmark" | "compare" | "group" | "updown" | "attention" | "detail";
    key: string;
    index?: number;
    of?: number;
    side?: "up" | "down";
  };
  [k: string]: unknown;
}
export interface FactorEventTab {
  tab: "event";
  holding: number;
  unit: "day" | "hour" | "bar";
  periods: number[];
  periodLabels: string[];
  /**
   * Each table or figure that could not be made, by its name, and why; tab is set when the whole tab failed.
   */
  missing: {
    [k: string]: string;
  };
  seconds?: number;
  failed?: string;
  figures: {
    event?: FactorResultsFigure17;
  };
  /**
   * Bars from the value: -5 to 20.
   */
  offsets?: number[];
  /**
   * What the returns are relative to, from the run's settings: nothing (Raw), all names with a value that day (Demeaned), or the names of the same industry (Group neutral).
   */
  basis?: "raw" | "all" | "group";
  /**
   * Each group's mean return at each offset, relative to what `basis` says (as Alphalens' average_cumulative_return_by_quantile with the run's demeaned and group_adjust), its standard deviation over days, and the path added up from the day of the value (shares).
   */
  event?: {
    group: number;
    mean: (number | null)[];
    std: (number | null)[];
    cumulative: (number | null)[];
  }[];
  /**
   * The path 5 bars before and 1, 5, 10 and 20 after, relative to what `basis` says (shares).
   */
  table?: {
    group: number;
    before: number | null;
    after1: number | null;
    after5: number | null;
    after10: number | null;
    after20: number | null;
  }[];
}
/**
 * One figure of a Results tab: the figure, or why it could not be drawn (unavailable). The figure is left out when read without figures.
 */
export interface FactorResultsFigure17 {
  key: string;
  title: string;
  figure?: FactorPlotlyFigure17;
  unavailable?: string;
}
/**
 * A Plotly figure drawn by the backend; layout.meta.key is the figure's fixed identity (factor.results.<name>, factor.groups ...).
 */
export interface FactorPlotlyFigure17 {
  data: FactorPlotlyTrace17[];
  layout: {
    meta: {
      key: string;
    };
    [k: string]: unknown;
  };
}
/**
 * One Plotly trace. meta.role says what it is, and the display colours it by that: main (the text colour), benchmark (thin grey, also reference lines such as zero, a mean or the 45-degree line), compare (another run, grey dashed), group (one step of a neutral ramp, meta.index of meta.of), updown (the user's up and down colours by sign), attention (orange: something to deal with). meta.key is the trace's identity inside its figure.
 */
export interface FactorPlotlyTrace17 {
  type?: string;
  name?: string;
  meta: {
    role: "main" | "benchmark" | "compare" | "group" | "updown" | "attention" | "detail";
    key: string;
    index?: number;
    of?: number;
    side?: "up" | "down";
  };
  [k: string]: unknown;
}
export interface FactorResultsStarted {
  run: {
    id: string;
    number: number;
    /**
     * The Results' state, short: state, progress, tabs, message, seconds.
     */
    results: {
      [k: string]: unknown;
    };
    /**
     * 0 to 1 while the Results build in the background, else null.
     */
    resultsProgress: number | null;
    [k: string]: unknown;
  };
  results: FactorResultsState1;
  tab: null;
  cell?: number;
}
/**
 * Where a run's Results are. none: a held-out validation or an unfinished run has none; notbuilt: not built yet (a sweep combination, or an earlier run put aside for a newer one), build_factor_results builds them; queued and running: building in the background, progress is the share of tabs done; done; failed (message says why); interrupted: Quant closed while building. Tabs that are done can be read while the rest build.
 */
export interface FactorResultsState1 {
  state: "none" | "notbuilt" | "queued" | "running" | "done" | "failed" | "interrupted";
  progress: number;
  tabs: {
    [k: string]: "waiting" | "running" | "done" | "failed";
  };
  message?: string;
  seconds?: number;
  computeSeconds?: number;
  queuedAt?: number;
  startedAt?: number;
  endedAt?: number;
  about?: {
    styles?: string;
    versions?: {
      [k: string]: string;
    };
    seconds?: number;
    unit?: string;
    shares?: boolean;
  };
}
