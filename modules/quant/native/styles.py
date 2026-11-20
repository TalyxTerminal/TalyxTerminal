"""The style factors, defined once for Quant: what a factor, a model, an allocation or a strategy is compared with.

A style is a well-known way of sorting names (momentum, size, volatility ...). Every block measures exposure to the same definitions,
so the version of this file (`VERSION`) is written into each run record that used it; a change to any definition raises the version.
The price styles are computed from the run's bars; the financial ones need point-in-time financials, which Market does not give yet,
so they are listed as unavailable with that reason rather than left out.

Windows are counted in bars of the run (days for daily bars). The market a beta is measured against is the equal-weighted mean return
of the run's own names each bar, because the run may have no index price; the text of each style says so.
"""
from __future__ import annotations

import numpy as np

VERSION = "Styles v1"
FINANCIALS = "Needs point-in-time financials, which Market does not give yet"

MOMENTUM, SKIP, REVERSAL, VOLATILITY, BETA, RESIDUAL, LIQUIDITY = 252, 21, 21, 60, 252, 60, 20


def definitions(unit="day", shares=True):
    """Each style: its name, family and how it is measured, in the run's bar unit; financial ones say why they are unavailable."""
    u = unit
    price = [
        ("Momentum", f"12-1 month return: from {MOMENTUM} to {SKIP} {u}s back"),
        ("Reversal", f"Minus the return over the last {REVERSAL} {u}s"),
        ("Volatility", f"Volatility of {u}ly returns over {VOLATILITY} {u}s" if u != "day" else f"Volatility of daily returns over {VOLATILITY} days"),
        ("Residual volatility", f"Volatility over {RESIDUAL} {u}s of the return the {BETA}-{u} beta does not explain"),
        ("Beta", f"{BETA}-{u} beta to the equal-weighted mean return of the run's names"),
        ("Liquidity", f"Log of the {LIQUIDITY}-{u} mean dollar volume (close × volume)"),
        ("Size", "Log market value (close × shares outstanding)" if shares else "Log of the 30-bar mean dollar volume (no shares outstanding for these names)"),
    ]
    financial = [("Value", "Book value to price"), ("Profitability", "Return on equity"), ("Growth", "Sales growth over a year"),
                 ("Leverage", "Debt to assets"), ("Dividend", "Dividend yield")]
    return ([{"style": name, "family": "price", "measuredAs": text} for name, text in price] +
            [{"style": name, "family": "financial", "measuredAs": text, "unavailable": FINANCIALS} for name, text in financial])


# The price styles written as formulas: the presets a new factor can start from. Same windows as the definitions above (one place for the numbers);
# the market of beta and residual volatility is the mean return across the run's names, which the formula language gets as r - CSDemean(r).
_R = "($close / Ref($close, 1) - 1)"
_M = f"({_R} - CSDemean({_R}))"
_BETA = f"Cov({_R}, {_M}, {BETA}) / Var({_M}, {BETA})"
FORMULAS = {
    "Momentum": f"Ref($close, {SKIP}) / Ref($close, {MOMENTUM}) - 1",
    "Reversal": f"-($close / Ref($close, {REVERSAL}) - 1)",
    "Volatility": f"Std({_R}, {VOLATILITY})",
    "Residual volatility": f"Std({_R} - ({_BETA}) * {_M}, {RESIDUAL})",
    "Beta": _BETA,
    "Liquidity": f"Log(Mean($amount, {LIQUIDITY}))",
    "Size": "Log($size)",
}


def preset_formulas(unit="day"):
    """Each price style as a starting factor: its name, how it is measured and its formula."""
    measured = {item["style"]: item["measuredAs"] for item in definitions(unit) if item["family"] == "price"}
    return [{"style": name, "measuredAs": measured[name], "formula": formula} for name, formula in FORMULAS.items()]


# ---------------------------------------------------------------------------------------------------------------- rolling sums
def _window_sum(values, window):
    """Sum over the last `window` rows (including this one), NaN counted as 0, and how many rows had a value."""
    have = ~np.isnan(values)
    filled = np.where(have, values, 0.0)
    total = np.cumsum(filled, axis=0)
    count = np.cumsum(have, axis=0).astype(float)
    total[window:] = total[window:] - total[:-window]
    count[window:] = count[window:] - count[:-window]
    return total, count


def rolling_std(values, window, minimum):
    s, n = _window_sum(values, window)
    s2, _ = _window_sum(values * values, window)
    with np.errstate(invalid="ignore", divide="ignore"):
        variance = (s2 - s * s / n) / (n - 1)
    out = np.sqrt(np.maximum(variance, 0.0))
    out[n < minimum] = np.nan
    return out


def rolling_mean(values, window, minimum):
    s, n = _window_sum(values, window)
    with np.errstate(invalid="ignore", divide="ignore"):
        out = s / n
    out[n < minimum] = np.nan
    return out


def rolling_beta(returns, market, window, minimum):
    """Each name's beta to `market` (one value a row) over the last `window` rows where both have a value."""
    both = ~np.isnan(returns) & ~np.isnan(market)[:, None]
    r = np.where(both, returns, np.nan)
    m = np.where(both, np.broadcast_to(market[:, None], returns.shape), np.nan)
    s_r, n = _window_sum(r, window)
    s_m, _ = _window_sum(m, window)
    s_rm, _ = _window_sum(r * m, window)
    s_mm, _ = _window_sum(m * m, window)
    with np.errstate(invalid="ignore", divide="ignore"):
        beta = (s_rm - s_r * s_m / n) / (s_mm - s_m * s_m / n)
    beta[(n < minimum) | ~np.isfinite(beta)] = np.nan
    return beta


def _shifted(values, rows):
    out = np.full_like(values, np.nan)
    if rows < len(values):
        out[rows:] = values[:len(values) - rows]
    return out


# ---------------------------------------------------------------------------------------------------------------- the styles
def price_styles(panel):
    """The seven price styles on the panel, each an array of its shape (bars × names); a name has a value where its window is full enough."""
    def compute():
        close = panel.fields["close"].copy()
        close[~panel.member] = np.nan
        returns = panel.derived("returns").copy()
        returns[~panel.member] = np.nan
        import warnings
        with warnings.catch_warnings():
            warnings.simplefilter("ignore", RuntimeWarning)
            market = np.nanmean(returns, axis=1)
        with np.errstate(invalid="ignore", divide="ignore"):
            momentum = _shifted(close, SKIP) / _shifted(close, MOMENTUM) - 1
            reversal = -(close / _shifted(close, REVERSAL) - 1)
        volatility = rolling_std(returns, VOLATILITY, VOLATILITY // 2)
        beta = rolling_beta(returns, market, BETA, BETA // 2)
        residual = rolling_std(returns - beta * market[:, None], RESIDUAL, RESIDUAL // 2)
        amount = panel.derived("amount").copy()
        amount[~panel.member] = np.nan
        dollar = rolling_mean(amount, LIQUIDITY, LIQUIDITY // 2)
        size = panel.derived("size")
        with np.errstate(invalid="ignore", divide="ignore"):
            liquidity = np.log(np.where(dollar > 0, dollar, np.nan))
            size = np.log(np.where(size > 0, size, np.nan))
        out = {"Momentum": momentum, "Reversal": reversal, "Volatility": volatility, "Residual volatility": residual, "Beta": beta,
               "Liquidity": liquidity, "Size": size}
        for value in out.values():
            value[~np.isfinite(value)] = np.nan
            value[~panel.member] = np.nan
        return out
    return panel.cached(("styles", VERSION), compute)


def _zscore_rows(x):
    import warnings
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", RuntimeWarning)
        mean = np.nanmean(x, axis=1, keepdims=True)
        std = np.nanstd(x, axis=1, keepdims=True)
    with np.errstate(invalid="ignore", divide="ignore"):
        return (x - mean) / std


def residual_of(values, styles, minimum=20):
    """Each day, what is left of `values` after a cross-sectional regression on the styles (each z-scored that day), where a name
    has the value and every style; a day with fewer than `minimum` such names, or not more names than styles, has no residual."""
    exposures = np.stack([_zscore_rows(item) for item in styles.values()], axis=2)  # bars × names × styles
    out = np.full_like(values, np.nan)
    have = ~np.isnan(values)
    for t in range(len(values)):
        # A style without values that day (a window not full yet) is left out of that day's regression, not the whole day.
        x = exposures[t][:, (~np.isnan(exposures[t]) & have[t][:, None]).sum(axis=0) >= minimum]
        ok = have[t] & ~np.isnan(x).any(axis=1)
        if ok.sum() < max(minimum, x.shape[1] + 2):
            continue
        design = np.column_stack([np.ones(int(ok.sum())), x[ok]])
        coef, *_ = np.linalg.lstsq(design, values[t, ok], rcond=None)
        out[t, ok] = values[t, ok] - design @ coef
    return out
