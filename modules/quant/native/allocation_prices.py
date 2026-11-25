"""Aligned pool closes and returns used in research and published allocation tracking."""
import pandas as pd


def money(code):
    """A currency as the pool compares it: the dollar stablecoins a crypto pair is quoted in count as dollars."""
    code = str(code or "").strip().upper()
    return "USD" if code in ("USDT", "USDC", "BUSD") else code


def splice_proxy(closes, name, proxy):
    """The asset's prices before its first day, taken from the proxy's: the proxy's closes scaled so the two agree on the asset's first day (the joining day is the
    first day both have a price). Returns (the day joined, the correlation of the two over their common days) or None when there is nothing to join."""
    own, other = closes[name], closes[proxy]
    first = own.first_valid_index()
    if first is None or other.first_valid_index() is None or other.first_valid_index() >= first:
        return None
    at = other.loc[:first].dropna()
    if at.empty:
        return None
    scale = own[first] / at.iloc[-1]
    before = other.loc[other.index < first].dropna() * scale
    closes.loc[before.index, name] = before
    both = pd.concat([own.pct_change(), other.pct_change()], axis=1).dropna()
    both = both.loc[both.index >= first]
    corr = float(both.iloc[:, 0].corr(both.iloc[:, 1])) if len(both) > 2 else float("nan")
    if not corr >= 0.8:
        raise ValueError(f"{proxy} and {name} correlate {corr:.2f} over their common {len(both) / 252:.0f} years; pick another proxy")
    return first, corr


def risk_free_per_bar(spec, index, annual):
    """The risk-free rate an observation on each day of `index` (days, as the pool's returns are indexed) from the run's rate (`{basis: zero}`, `{basis: fixed,
    pct}` or `{basis: irx, rows: [[ms, percent a year]], unavailable}`): the fixed percent, or the newest ^IRX close not later than the day, over `annual`. 0.0 for
    zero; None when Market has no rate, so what uses it is empty. The backtest and the library's figures after publication both read it."""
    if spec["basis"] == "zero":
        return 0.0
    if spec["basis"] == "fixed":
        return float(spec["pct"]) / 100 / annual
    if spec.get("unavailable") or not spec.get("rows"):
        return None
    days = pd.to_datetime([row[0] for row in spec["rows"]], unit="ms", utc=True).tz_localize(None).normalize()
    pct = pd.Series([float(row[1]) for row in spec["rows"]], index=days)
    pct = pct[~pct.index.duplicated(keep="last")].sort_index()
    return pct.reindex(pct.index.union(index)).ffill().reindex(index) / 100 / annual


ALIGN_RULE = {"weekly": ("W-FRI", 52, 5), "monthly": ("M", 12, 21)}


def observations_a_year(settings, calendar):
    """Observations a year of the pool's returns: 52 or 12 when they are aligned weekly or monthly, else `calendar` (Market's count of the data's bars a year)."""
    rule = ALIGN_RULE.get(settings.get("align") or "common")
    return rule[1] if rule else calendar


def aligned_lookback(settings):
    """The lookback is written in days; on weekly or monthly returns it counts that many days' worth of rows."""
    rule = ALIGN_RULE.get(settings.get("align") or "common")
    lookback = int(settings.get("lookback", 252))
    return lookback if rule is None else max(8 if rule[1] == 52 else 6, round(lookback / rule[2]))


def period_returns(closes):
    """Close-to-close returns of aligned closes: a period without a close has no return (the holding stays at its last close), and the period the close comes back in
    carries the whole move from the last close before the gap (a halt that spans a whole week or month on weekly or monthly returns)."""
    return closes / closes.ffill().shift(1) - 1


def period_closes(closes, period):
    """Each asset's last close in each week or month (`period`: W-FRI or M), on the period's last day with a close: its
    last trading day, a Thursday when Good Friday closes the market, never a day without a bar."""
    traded = closes.dropna(how="all")
    periods = traded.index.to_period(period)
    last = traded.groupby(periods).last()
    last.index = pd.DatetimeIndex(traded.index.to_series().groupby(periods).max().to_numpy(), name=closes.index.name)
    return last


def daily_returns(bars, pool, settings=None, zones=None):
    """The one definition of the pool's returns, used by the backtest worker and by `q.allocate` (so the frozen run and the original run agree to the bit):
    prices in another currency are converted with the rate columns the Host froze next to them (`settings.fxColumns`); an asset's missing early history is taken from
    its proxy (`settings.proxyColumns`); then from the common start (the latest first price, a proxied asset counting from its proxy's), on the days every pool asset
    has a close (`align` common days), or the last of each week or month; a missing close is crossed (`period_returns`: the return runs across the gap), never filled with 0. Only the pool's
    columns count (a benchmark's missing day does not remove a day from the pool)."""
    settings = settings or {}
    ordered = bars.sort_values("label")
    dates = pd.to_datetime(ordered["label"], unit="ms", utc=True)
    session = dates.dt.tz_localize(None).dt.normalize()
    for name, zone in (zones or {}).items():
        selected = ordered["column"] == name
        session.loc[selected] = dates.loc[selected].dt.tz_convert(zone).dt.tz_localize(None).dt.normalize()
    closes = ordered.groupby([session, "column"])["close"].last().unstack("column")
    pool = [name for name in pool if name in closes.columns] or list(closes.columns)
    fx_columns, proxy_columns = settings.get("fxColumns"), settings.get("proxyColumns")
    if fx_columns is None or proxy_columns is None:   # a frozen run carries only the declared settings: the pool rows say which column is whose proxy and rate
        derived_fx, derived_proxy = {}, {}
        base = money(settings.get("currency") or "USD")
        for row in settings.get("pool") or []:
            proxy = str(row.get("proxy") or "").split(":")[-1].strip()
            if proxy and proxy in closes.columns:
                derived_proxy[row["ticker"]] = proxy
            code = money(row.get("currency"))
            if code and code != base and row["ticker"] in closes.columns:
                for invert, pair in ((False, (code, base)), (True, (base, code))):
                    found = next((text for text in (f"{pair[0]}{pair[1]}=X", f"{pair[0]}{pair[1]}", f"{pair[0]}/{pair[1]}") if text in closes.columns), None)
                    if found:
                        derived_fx[row["ticker"]] = {"column": found, "invert": invert}
                        break
        fx_columns = derived_fx if fx_columns is None else fx_columns
        proxy_columns = derived_proxy if proxy_columns is None else proxy_columns
    for name, rate in (fx_columns or {}).items():
        if name in closes.columns and rate["column"] in closes.columns:
            factor = closes[rate["column"]].ffill()
            closes[name] = closes[name] * (1 / factor if rate.get("invert") else factor)
    spliced = {}
    for name, proxy in (proxy_columns or {}).items():
        if name in pool and proxy in closes.columns:
            found = splice_proxy(closes, name, proxy)
            if found:
                spliced[name] = {"proxy": proxy, "to": found[0], "correlation": found[1]}
    closes = closes[pool]
    first = {name: closes[name].first_valid_index() for name in pool}
    start = max(first.values())
    earliest = min(first.values())
    cut_short = [f"{name} starts {first[name]:%b %Y}; {sum(1 for other in pool if first[other] < first[name])} assets lose {(first[name] - earliest).days / 365.25:.1f} years"
                 for name in pool if (first[name] - earliest).days > 365]
    sliced = closes.loc[start:]
    last = {name: closes[name].last_valid_index() for name in pool}
    ended = {name: day for name, day in last.items() if (max(last.values()) - day).days > 7}   # stopped trading: kept to its last price, then out of the allocation
    gaps = {name: int(sliced[name].loc[:ended.get(name, sliced.index[-1])].isna().sum()) for name in pool if sliced[name].loc[:ended.get(name, sliced.index[-1])].isna().any()}
    drop = sliced.isna() & pd.DataFrame({name: sliced.index <= ended.get(name, sliced.index[-1]) for name in pool}, index=sliced.index)
    common = sliced.loc[~drop.any(axis=1)]
    rule = ALIGN_RULE.get(settings.get("align") or "common")
    if rule:
        common = period_closes(sliced, rule[0])
    returns = period_returns(common).iloc[1:]
    return returns, {"start": start, "cutShort": cut_short, "missing": gaps, "closes": common, "spliced": spliced, "ended": ended, "annualization": rule[1] if rule else None}
