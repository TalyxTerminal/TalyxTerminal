"""Measures, costs and check thresholds, defined once for Quant.

Every Quant environment imports this file: the research, strategy and advanced environments (as `native.metrics`) and the Host
(as `modules.quant.native.metrics`). It uses only the standard library, numpy and pandas: ranks are computed here, so an
environment without scipy (the strategy one) gets the same numbers. Annualizing takes the bars a year of the instruments'
trading calendar from Market (stocks about 252 a year, crypto 365); nothing here assumes either.
"""
from __future__ import annotations

import math

import numpy as np

from .i18n import mark

try:  # the research environment has bottleneck; the others fall back to pandas, with the same ranks
    import bottleneck as _bn
except ImportError:  # pragma: no cover - depends on the environment
    _bn = None

# ---------------------------------------------------------------------------------------------------------------- thresholds
# The site-wide thresholds for signals (factors, models, strategies). A check that fails is listed and never blocks publishing.
# Checks use in-sample numbers only: the held-out end of the period never enters a check. Allocation has its own set.
THRESHOLDS = {
    "significance": {"name": mark("Significance"), "op": ">=", "value": 3.0, "need": "≥ 3.00",
                     "meaning": "t of the mean daily rank IC, Newey–West for overlapping returns"},
    "library": {"name": mark("Like the library"), "op": "<", "value": 0.7, "need": "< 0.70",
                "meaning": "the highest mean daily rank correlation with a library item"},
    "group_size": {"name": mark("Group size"), "op": ">=", "value": 30, "need": "≥ 30", "meaning": "names in each group on the median day"},
    "every_year": {"name": mark("Every year"), "op": ">=", "value": 0.75, "need": "≥ 75%",
                   "meaning": "share of whole years whose rank IC has the sign of the whole period"},
    "cost_room": {"name": mark("Cost room"), "op": ">=", "value": 2.0, "need": "≥ 2× cost",
                  "meaning": "break-even cost a trade, against twice the cost assumed"},
}

# A strategy's seven checks (and the edge of the grid). The three from the approved draft (deflated Sharpe, shuffled drawdown, best five) and
# the others set with the user on 11-05: the held-out stretch keeps at least half the Sharpe of the rest; walk-forward is at least half as good
# out of sample as where the values were picked; the neighbouring cells of a sweep keep 80% of this cell's Sharpe and the cell is not on the
# grid's edge; doubled costs keep half the Sharpe. A value here is a ratio to the number it is judged against (Sharpe over Sharpe), except
# for the three that are ratios or shares already. "watch" is a failed check; nothing blocks.
STRATEGY_CHECKS = {
    "heldout": {"name": "Held-out Sharpe", "op": ">=", "value": 0.5, "need": "≥ 50% of in-sample", "meaning": "Sharpe on the held-out end over the Sharpe before it"},
    "walk": {"name": "Walk-forward", "op": ">=", "value": 0.5, "need": "≥ 0.50", "meaning": "out-of-sample Sharpe over the Sharpe the values were picked on"},
    "neighbours": {"name": "Neighbours", "op": ">=", "value": 0.8, "need": "≥ 80% of this", "meaning": "mean Sharpe of the adjacent sweep cells over this cell's"},
    "edge": {"name": "Edge of the grid", "op": "<=", "value": 0.0, "need": "inside the ranges", "meaning": "whether a value is the smallest or largest the sweep tried"},
    "costs": {"name": "Costs doubled", "op": ">=", "value": 0.5, "need": "≥ 50% of Sharpe", "meaning": "Sharpe with every cost counted twice over the Sharpe as set"},
    "shuffle": {"name": "Shuffled trades", "op": "<=", "value": 2.0, "need": "≤ 2× actual", "meaning": "95th-percentile drawdown of the trades in random order, over the actual drawdown"},
    "top": {"name": "Best five", "op": "<=", "value": 0.5, "need": "≤ 50% of net", "meaning": "the five best trades' (or names') share of the net profit"},
    "tries": {"name": "Deflated Sharpe", "op": ">=", "value": 0.95, "need": "≥ 95%", "meaning": "probability the Sharpe is above zero after discounting for the runs tried"},
}
SHUFFLES = 1000
SHUFFLE_SEED = 7
NEIGHBOUR_SHARE = STRATEGY_CHECKS["neighbours"]["value"]


def strategy_check(check, value):
    """"ok" when `value` meets the strategy check, "watch" when not, "notrun" when there is no value."""
    if value is None or not math.isfinite(value):
        return "notrun"
    rule = STRATEGY_CHECKS[check]
    need = rule["value"]
    good = {">=": value >= need, ">": value > need, "<=": value <= need, "<": value < need}[rule["op"]]
    return "ok" if good else "watch"

# Defaults of the one cost model. A trade of a factor or a model costs 5 bps unless set; borrowing the short leg 0.3% a year.
DEFAULT_TRADE_BPS = 5.0
DEFAULT_BORROW_PCT = 0.3
IMPACT_COEFFICIENT = 0.3


def passes(check, value, scale=1.0):
    """Whether `value` meets the threshold `check` (a THRESHOLDS key); `scale` multiplies the threshold (cost room: the cost assumed)."""
    if value is None or not math.isfinite(value):
        return False
    rule = THRESHOLDS[check]
    need = rule["value"] * scale
    return value >= need if rule["op"] == ">=" else value < need


# ---------------------------------------------------------------------------------------------------------------- numbers
def finite(value):
    """A float, or None for missing, infinite and NaN values."""
    if value is None:
        return None
    value = float(value)
    return value if math.isfinite(value) else None


def rank_rows(values):
    """Average ranks (1 = lowest) along each row of a 2-D array; NaN stays NaN. Ties share their mean rank, as Spearman's correlation needs."""
    values = np.asarray(values, dtype=float)
    if _bn is not None:
        return _bn.nanrankdata(values, axis=1)
    import pandas as pd
    return pd.DataFrame(values).rank(axis=1, method="average").to_numpy()


def row_correlation(left, right, minimum=3):
    """Pearson correlation of two 2-D arrays row by row, over the cells where both have a value; rows with fewer than `minimum` pairs are NaN."""
    left, right = np.asarray(left, dtype=float), np.asarray(right, dtype=float)
    both = ~np.isnan(left) & ~np.isnan(right)
    count = both.sum(axis=1).astype(float)
    a, b = np.where(both, left, 0.0), np.where(both, right, 0.0)
    with np.errstate(invalid="ignore", divide="ignore"):
        mean_a = a.sum(axis=1) / count
        mean_b = b.sum(axis=1) / count
        da = np.where(both, left - mean_a[:, None], 0.0)
        db = np.where(both, right - mean_b[:, None], 0.0)
        out = (da * db).sum(axis=1) / np.sqrt((da * da).sum(axis=1) * (db * db).sum(axis=1))
    out[count < minimum] = np.nan
    return out


def rank_correlation(left, right, minimum=3):
    """Spearman correlation row by row: ranks are taken among the names where both rows have a value, then correlated."""
    left, right = np.asarray(left, dtype=float), np.asarray(right, dtype=float)
    both = ~np.isnan(left) & ~np.isnan(right)
    return row_correlation(rank_rows(np.where(both, left, np.nan)), rank_rows(np.where(both, right, np.nan)), minimum)


class RankBasis:
    """What ranking one 2-D array within any subset of its cells needs, sorted once: each row's sort order and its runs of equal values.

    `ranks(mask)` gives the same average ranks as `rank_rows(np.where(mask, values, nan))` without sorting again, so an array compared with
    many others (a factor with each price style and each holding's returns) is sorted once; kept with the data, it serves every later run.
    Flat positions are 32-bit, and the runs of equal values are kept only when the array has any."""

    def __init__(self, values):
        values = np.asarray(values, dtype=float)
        rows, n = values.shape
        order = np.argsort(values, axis=1, kind="stable")  # missing values last
        ordered = np.take_along_axis(values, order, axis=1)
        first = np.ones(values.shape, dtype=bool)
        first[:, 1:] = ordered[:, 1:] != ordered[:, :-1]  # a missing value is never equal to another: each is its own run
        offset = (np.arange(rows, dtype=np.int64) * n)[:, None]
        self.shape = values.shape
        self.order = (order + offset).astype(np.int32).ravel()
        self.before = self.end = None
        if not first.all():
            index = np.broadcast_to(np.arange(n), values.shape)
            last = np.ones(values.shape, dtype=bool)
            last[:, :-1] = first[:, 1:]
            start = np.maximum.accumulate(np.where(first, index, 0), axis=1)
            end = np.minimum.accumulate(np.where(last, index, n - 1)[:, ::-1], axis=1)[:, ::-1]
            # For each sorted place: the place before its run of equal values (-1 at a row's start) and the end of the run.
            self.before = np.where(start > 0, start - 1 + offset, -1).astype(np.int32).ravel()
            self.end = (end + offset).astype(np.int32).ravel()

    def ranks(self, mask):
        """Average ranks (1 = lowest) among the cells of each row where `mask` is true (they must hold values); NaN elsewhere."""
        member = np.asarray(mask, dtype=bool).ravel()[self.order]
        count = np.cumsum(member.reshape(self.shape), axis=1, dtype=np.float64).ravel()
        if self.before is not None:  # ties share the mean of the places they take
            count = (np.where(self.before >= 0, count[self.before], 0.0) + 1 + count[self.end]) * .5
        out = np.empty(member.size)
        out[self.order] = np.where(member, count, np.nan)
        return out.reshape(self.shape)


def rank_correlation_on(left, left_basis, right, right_basis, minimum=3):
    """`rank_correlation(left, right)` with each side's ranks from its kept `RankBasis`."""
    both = ~np.isnan(left) & ~np.isnan(right)
    return row_correlation(left_basis.ranks(both), right_basis.ranks(both), minimum)


def held(close):
    """Prices as positions see them: a halted name keeps its last price until it trades again, and a name whose prices stop (delisted,
    or the source has none after) keeps its last close from then on: it is out at that price and earns nothing after. Before its first
    price a name has none."""
    import pandas as pd
    return pd.DataFrame(np.asarray(close, dtype=float)).ffill().to_numpy()


def forward_returns(close, holding, delay=0):
    """The return of each row's names over `holding` bars, starting `delay` bars later: close[t + delay + holding] / close[t + delay] - 1,
    on `held` prices: a name that stops trading inside the window is out at its last close (the move to it counts, nothing after), so it
    neither drops out of the day nor earns more."""
    close = held(close)
    out = np.full_like(close, np.nan)
    start, end = delay, delay + holding
    if end < len(close):
        with np.errstate(invalid="ignore", divide="ignore"):
            out[:len(close) - end] = close[end:] / close[start:len(close) - holding] - 1
    out[~np.isfinite(out)] = np.nan
    return out


def mean(series):
    series = np.asarray(series, dtype=float)
    series = series[~np.isnan(series)]
    return float(series.mean()) if len(series) else None


def newey_west_t(series, lags):
    """t of the mean of a series whose neighbours overlap (an h-day return seen every day overlaps h - 1 neighbours): Newey–West with Bartlett weights."""
    series = np.asarray(series, dtype=float)
    series = series[~np.isnan(series)]
    n = len(series)
    if n < 3:
        return None
    centred = series - series.mean()
    variance = float(centred @ centred) / n
    for lag in range(1, min(int(lags), n - 1) + 1):
        variance += 2 * (1 - lag / (lags + 1)) * float(centred[lag:] @ centred[:-lag]) / n
    if variance <= 0:
        return None
    return float(series.mean() / math.sqrt(variance / n))


def annual_mean(per_bar, annualization):
    """The mean of a per-bar series times the bars in a year."""
    value = mean(per_bar)
    return None if value is None else value * annualization


def signal_years(dates, ic, net, annualization, holding):
    """A signal's calendar years, the one "by year" of factors and models: each year's mean daily rank IC, its t (Newey–West for overlapping
    `holding`-day returns) and its long–short return net of cost a year (the mean net return a bar times the bars in a year). A year is whole when the
    days cover January through December; the "Every year" check counts whole years only."""
    ic, net = np.asarray(ic, dtype=float), np.asarray(net, dtype=float)
    years = np.asarray(dates.year)
    first, last = dates[0], dates[-1]
    out = []
    for year in sorted(set(years[np.isfinite(ic)].tolist())):
        inside = years == year
        values = ic[inside & np.isfinite(ic)]
        whole = not (year == first.year and (first.month > 1 or first.day > 7)) and not (year == last.year and (last.month < 12 or last.day < 24))
        out.append({"year": int(year), "whole": bool(whole), "days": int(len(values)), "rankIc": float(values.mean()),
                    "t": newey_west_t(ic[inside], max(holding - 1, 0)), "longShortNet": annual_mean(net[inside], annualization)})
    return out


def sharpe(per_bar, annualization, risk_free_per_bar=0.0):
    """Annualized Sharpe ratio of per-bar returns above the risk-free rate: one per-bar rate, or one for each bar (the 13-week T-bill
    of that day). Which rate a run used is written beside its Sharpe."""
    series = np.asarray(per_bar, dtype=float)
    rate = np.broadcast_to(np.asarray(risk_free_per_bar, dtype=float), series.shape)
    if not np.isfinite(rate).all():
        return None
    keep = ~np.isnan(series) & ~np.isnan(rate)
    series = series[keep] - rate[keep]
    if len(series) < 2:
        return None
    deviation = series.std(ddof=1)
    return finite(series.mean() / deviation * math.sqrt(annualization)) if deviation > 0 else None


def drawdown_path(equity, initial):
    """Drawdown at each observed equity, including the unobserved opening capital in every high."""
    values = np.asarray(equity, dtype=float)
    peaks = np.fmax.accumulate(np.r_[float(initial), values])[1:]
    with np.errstate(divide="ignore", invalid="ignore"):
        return np.where(peaks > 0, values / peaks - 1, 0.0)


def max_drawdown(per_bar):
    """The deepest fall from a high of the compounded path of per-bar returns, as a negative share."""
    series = np.nan_to_num(np.asarray(per_bar, dtype=float))
    if not len(series):
        return None
    path = np.cumprod(1 + series)
    return finite(drawdown_path(path, 1.0).min())


def compounded(per_bar):
    """Cumulative compounded return of per-bar returns; missing bars count as flat."""
    return np.cumprod(1 + np.nan_to_num(np.asarray(per_bar, dtype=float))) - 1


def replaced_share(members, holding):
    """Share of each row's members that were not members `holding` rows earlier (the turnover of a group at a rebalance)."""
    members = np.asarray(members, dtype=bool)
    before = np.zeros_like(members)
    before[holding:] = members[:-holding] if holding else members
    count = members.sum(axis=1)
    with np.errstate(invalid="ignore", divide="ignore"):
        out = (members & ~before).sum(axis=1) / count
    out[(count == 0) | (np.arange(len(members)) < holding)] = np.nan
    return out


# ---------------------------------------------------------------------------------------------------------------- costs
def borrow_per_bar(borrow_pct, annualization):
    """The yearly borrow fee on a short position, per bar."""
    return borrow_pct / 100 / annualization


def break_even_bps(gross_per_bar, traded_per_bar):
    """The cost a trade at which the gross return is used up: mean gross return over mean traded share, in bps."""
    gross, traded = mean(gross_per_bar), mean(traded_per_bar)
    if gross is None or not traded:
        return None
    return gross / traded * 1e4


# ---------------------------------------------------------------------------------------------------------------- strategy measures
def _normal_cdf(value):
    return 0.5 * (1 + math.erf(value / math.sqrt(2)))


def _normal_ppf(p):
    """Inverse of the standard normal distribution (Acklam's approximation, |error| < 1.2e-9), without scipy."""
    if not 0 < p < 1:
        return math.copysign(math.inf, p - 0.5)
    a = (-3.969683028665376e+01, 2.209460984245205e+02, -2.759285104469687e+02, 1.383577518672690e+02, -3.066479806614716e+01, 2.506628277459239e+00)
    b = (-5.447609879822406e+01, 1.615858368580409e+02, -1.556989798598866e+02, 6.680131188771972e+01, -1.328068155288572e+01)
    c = (-7.784894002430293e-03, -3.223964580411365e-01, -2.400758277161838e+00, -2.549732539343734e+00, 4.374664141464968e+00, 2.938163982698783e+00)
    d = (7.784695709041462e-03, 3.224671290700398e-01, 2.445134137142996e+00, 3.754408661907416e+00)
    low = 0.02425
    if p < low:
        q = math.sqrt(-2 * math.log(p))
        return (((((c[0] * q + c[1]) * q + c[2]) * q + c[3]) * q + c[4]) * q + c[5]) / ((((d[0] * q + d[1]) * q + d[2]) * q + d[3]) * q + 1)
    if p > 1 - low:
        q = math.sqrt(-2 * math.log(1 - p))
        return -(((((c[0] * q + c[1]) * q + c[2]) * q + c[3]) * q + c[4]) * q + c[5]) / ((((d[0] * q + d[1]) * q + d[2]) * q + d[3]) * q + 1)
    q = p - 0.5
    r = q * q
    return (((((a[0] * r + a[1]) * r + a[2]) * r + a[3]) * r + a[4]) * r + a[5]) * q / (((((b[0] * r + b[1]) * r + b[2]) * r + b[3]) * r + b[4]) * r + 1)


def probabilistic_sharpe(per_period, observations, skew, kurtosis, benchmark=0.0):
    """Probability the true per-period Sharpe is above `benchmark` (Bailey and López de Prado): `kurtosis` is the plain (not excess) one."""
    if per_period is None or skew is None or kurtosis is None or observations is None or observations < 3:
        return None
    variance = 1 - skew * per_period + (kurtosis - 1) / 4 * per_period ** 2
    if variance <= 0:
        return None
    return _normal_cdf((per_period - benchmark) * math.sqrt(observations - 1) / math.sqrt(variance))


def deflated_sharpe(per_period, observations, skew, kurtosis, tries, variance=None):
    """The probabilistic Sharpe against the best Sharpe expected by luck among `tries` runs (the deflated Sharpe ratio). `variance` is the
    variance of the per-period Sharpe across the runs; left out, that of one estimate (1 / observations)."""
    if per_period is None or observations is None or observations < 3:
        return None
    tries = max(int(tries or 1), 1)
    if tries == 1:
        return probabilistic_sharpe(per_period, observations, skew, kurtosis)
    gamma = 0.5772156649015329
    spread = math.sqrt(variance if variance else 1 / observations)
    expected = spread * ((1 - gamma) * _normal_ppf(1 - 1 / tries) + gamma * _normal_ppf(1 - 1 / (tries * math.e)))
    return probabilistic_sharpe(per_period, observations, skew, kurtosis, expected)


def path_drawdown(pnls, capital):
    """The deepest fall from a high of capital plus the running sum of trade profits, as a negative share of that high."""
    path = capital + np.cumsum(np.asarray(pnls, dtype=float))
    return float(drawdown_path(path, capital).min(initial=0.0))


def shuffled_drawdown(pnls, capital, shuffles=SHUFFLES, seed=SHUFFLE_SEED):
    """The drawdown of the trades as they happened, and the one that 95% of random orders of the same trades stay above (the 5% quantile of
    their drawdowns, a negative share): the profits are shuffled trade by trade, with a fixed seed so the same trades give the same answer."""
    pnls = np.asarray(pnls, dtype=float)
    rng = np.random.default_rng(seed)
    drawdowns = np.array([path_drawdown(rng.permutation(pnls), capital) for _ in range(shuffles)])
    return {"actual": path_drawdown(pnls, capital), "p95": float(np.quantile(drawdowns, 0.05))}


def drawdown_episodes(equity, limit=10):
    """The deepest falls of an equity series (a pandas Series by date): depth, the high before, the low, when it was made back, and days."""
    values = equity.to_numpy(dtype=float)
    falls = drawdown_path(values, values[0]) if len(values) else []
    dates = equity.index
    episodes, peak_at, trough_at, depth = [], 0, 0, 0.0
    for index in range(1, len(values)):
        if values[index] >= values[peak_at]:
            if depth < 0:
                episodes.append((depth, peak_at, trough_at, index))
            peak_at, trough_at, depth = index, index, 0.0
            continue
        fall = falls[index]
        if fall < depth:
            depth, trough_at = fall, index
    if depth < 0:
        episodes.append((depth, peak_at, trough_at, None))
    episodes.sort()
    day = lambda at: dates[at].strftime("%Y-%m-%d")
    return [{"depth": finite(depth), "peak": day(peak), "trough": day(trough), "recovered": day(back) if back is not None else None,
             "days": int((dates[back if back is not None else len(dates) - 1] - dates[peak]).days)} for depth, peak, trough, back in episodes[:limit]]


# ---------------------------------------------------------------------------------------------------------------- a run's statistics
# One definition each: a strategy's (and an allocation's) numbers come from the equity at the end of each day, led by
# the starting capital, and are computed once when the run ends; every place that shows them reads the stored numbers.
RUN_MINIMUM = 60  # daily returns a run needs before anything is annualized from it


def returns_of(equity):
    """Daily returns of an equity series whose first value is the starting capital."""
    equity = np.asarray(equity, dtype=float)
    return equity[1:] / equity[:-1] - 1 if len(equity) > 1 else np.array([], dtype=float)


def excess_of(per_bar, risk_free_per_bar):
    """Returns above the risk-free rate; None when the rate is unavailable (a statistic that uses it is then empty, not computed against zero)."""
    if risk_free_per_bar is None or not np.isfinite(risk_free_per_bar).all():
        return None
    series = np.asarray(per_bar, dtype=float)
    return series - np.broadcast_to(np.asarray(risk_free_per_bar, dtype=float), series.shape)


def sortino(per_bar, annualization, risk_free_per_bar=0.0):
    """mean(r − rf) over the root mean square of the shortfalls below the rate, taken over every day (a day above the rate counts as zero), × √A."""
    excess = excess_of(per_bar, risk_free_per_bar)
    if excess is None:
        return None
    excess = excess[~np.isnan(excess)]
    if len(excess) < 2:
        return None
    downside = math.sqrt(float((np.minimum(excess, 0.0) ** 2).mean()))
    return finite(excess.mean() / downside * math.sqrt(annualization)) if downside > 0 else None


def volatility(per_bar, annualization):
    """Sample standard deviation of the returns × √A."""
    series = np.asarray(per_bar, dtype=float)
    series = series[~np.isnan(series)]
    return finite(series.std(ddof=1) * math.sqrt(annualization)) if len(series) > 1 else None


def cagr(per_bar, annualization):
    """(final ÷ initial)^(A ÷ number of returns) − 1."""
    series = np.asarray(per_bar, dtype=float)
    series = series[~np.isnan(series)]
    if len(series) < 2:
        return None
    growth = float(np.prod(1 + series))
    return finite(growth ** (annualization / len(series)) - 1) if growth > 0 else None


def equity_drawdown(equity):
    """The deepest fall of the equity from its highest point so far (the starting capital included), a negative share."""
    equity = np.asarray(equity, dtype=float)
    if not len(equity):
        return None
    return finite(drawdown_path(equity, equity[0]).min())


def calmar(cagr_value, drawdown):
    """CAGR over the absolute maximum drawdown, both as defined here."""
    return finite(cagr_value / abs(drawdown)) if cagr_value is not None and drawdown is not None and drawdown < 0 else None


def moments(per_bar):
    """Skew and plain kurtosis: the third and fourth central moments (over n) divided by the sample standard deviation cubed and to the fourth;
    the per-period Sharpe (mean over sample standard deviation) the probabilistic and deflated Sharpe take with them."""
    series = np.asarray(per_bar, dtype=float)
    series = series[~np.isnan(series)]
    if len(series) < 3 or series.std(ddof=1) == 0:
        return {"observations": len(series), "perPeriod": None, "skew": None, "kurtosis": None}
    deviation = series.std(ddof=1)
    centred = series - series.mean()
    return {"observations": len(series), "perPeriod": finite(series.mean() / deviation), "skew": finite((centred ** 3).mean() / deviation ** 3),
            "kurtosis": finite((centred ** 4).mean() / deviation ** 4)}


def value_at_risk(per_bar, level=0.95):
    """Historical value at risk and expected shortfall of one period: the (1 − level) quantile of the returns and the mean of the returns at or below it."""
    series = np.asarray(per_bar, dtype=float)
    series = series[~np.isnan(series)]
    if not len(series):
        return None, None
    cut = float(np.quantile(series, 1 - level))
    return finite(cut), finite(series[series <= cut].mean())


def closed_trades(pnls):
    """What the closed trades made, in money: profit factor (gains over losses), win rate, the longest run of losses, the average win and loss.
    A loss is a trade whose profit is below zero; a trade at exactly zero is neither and ends a run of losses."""
    pnls = np.asarray(pnls, dtype=float)
    wins, losses = pnls[pnls > 0], pnls[pnls < 0]
    longest = run = 0
    for value in pnls:
        run = run + 1 if value < 0 else 0
        longest = max(longest, run)
    return {"closed": len(pnls), "winRate": finite(len(wins) / len(pnls)) if len(pnls) else None,
            "profitFactor": finite(wins.sum() / -losses.sum()) if len(losses) else None, "longestLosing": longest,
            "averageWin": finite(wins.mean()) if len(wins) else None, "averageLoss": finite(losses.mean()) if len(losses) else None}


def run_statistics(equity, annualization, risk_free_per_bar, *, pnls, trades, traded=0.0, costs=None, benchmark=None):
    """A run's key numbers from its daily equity (led by the starting capital). `risk_free_per_bar` is a rate a day for each return, or None when
    it is unavailable; `pnls` the profits of the closed trades and `trades` how many there were in all; `traded` the value bought and sold;
    `costs` every cost item added up; `benchmark` the benchmark's daily returns over the same days, its first day included. Under RUN_MINIMUM
    daily returns nothing is annualized, and without trades there is no Sharpe."""
    equity = np.asarray(equity, dtype=float)
    returns = returns_of(equity)
    days = len(returns)
    years = days / annualization if days else None
    short, idle = days < RUN_MINIMUM, not trades
    excess = excess_of(returns, risk_free_per_bar)
    grow = cagr(returns, annualization)
    drawdown = equity_drawdown(equity)
    bench = cagr(benchmark, annualization) if benchmark is not None else None
    mean_equity = float(equity[1:].mean()) if days else None
    shape = moments(excess) if excess is not None else moments(returns)
    out = {"days": days, "totalReturn": finite(equity[-1] / equity[0] - 1) if len(equity) > 1 and equity[0] else None,
           "cagr": grow, "volatility": volatility(returns, annualization), "maxDrawdown": drawdown, "calmar": calmar(grow, drawdown),
           "sharpe": sharpe(returns, annualization, risk_free_per_bar) if excess is not None else None,
           "sortino": sortino(returns, annualization, risk_free_per_bar) if excess is not None else None,
           "probabilisticSharpe": probabilistic_sharpe(shape["perPeriod"], shape["observations"], shape["skew"], shape["kurtosis"]) if excess is not None else None,
           "benchmarkCagr": bench, "overBenchmark": finite(grow - bench) if grow is not None and bench is not None else None,
           "turnover": finite(traded / 2 / mean_equity / years) if mean_equity and years else None,
           "costsAYear": finite(costs / mean_equity / years) if costs is not None and mean_equity and years else None,
           "trades": trades, "openAtEnd": trades - len(pnls), **{key: value for key, value in closed_trades(pnls).items() if key != "closed"}}
    if not np.isfinite(equity).all():
        for key in ("volatility", "maxDrawdown", "calmar", "sharpe", "sortino", "probabilisticSharpe", "turnover", "costsAYear", "overBenchmark", "cagr"):
            out[key] = None
    if short:
        for key in ("cagr", "volatility", "calmar", "sharpe", "sortino", "probabilisticSharpe", "benchmarkCagr", "overBenchmark", "turnover", "costsAYear"):
            out[key] = None
    if idle:
        for key in ("sharpe", "sortino", "probabilisticSharpe"):
            out[key] = None
    return out
