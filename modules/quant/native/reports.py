"""The standard reports of the three research templates, written from what the author's code hands over."""
from __future__ import annotations

import pandas as pd


def index_pairs(value, name):
    """The author's factor as one value per (date, asset): a Series with that index, or a table of dates by assets."""
    if isinstance(value, pd.DataFrame):
        if isinstance(value.index, pd.MultiIndex):
            raise ValueError("Give the factor as a table of dates by assets, or as one column per factor in a Series indexed by date and asset.")
        value = value.rename_axis(index="date", columns="asset").stack(future_stack=True)
    if not isinstance(value, pd.Series) or not isinstance(value.index, pd.MultiIndex) or value.index.nlevels != 2:
        raise ValueError("The factor must be a table of dates by assets, or a Series indexed by date and asset.")
    value = pd.to_numeric(value, errors="raise").rename(name)
    value.index = value.index.set_names(["date", "asset"])
    if value.index.has_duplicates:
        raise ValueError("Each factor value needs one unique date and asset.")
    return value.sort_index()


def check_prices(prices):
    if not isinstance(prices, pd.DataFrame) or not isinstance(prices.index, pd.DatetimeIndex) or prices.index.tz is None:
        raise ValueError("Prices must be a table of dates (with a time zone) by assets, such as q.data().close.")
    if prices.index.has_duplicates:
        raise ValueError("Prices need one row per date.")
    return prices.rename_axis(index="date", columns="asset").sort_index()


def panel_of(source=None, prices=None, annualization=None):
    """The bars a notebook's factor report is measured on, as the object page's Work reads them: the research data's bars (every price, inside and outside
    index membership; membership by day), or a prices table of dates by assets that has closes only."""
    import numpy as np

    from . import factor_work as W
    if source is not None:
        return W.Panel.from_bars(source.bars, source.info)
    prices = check_prices(prices)
    labels = prices.index.tz_convert("UTC").as_unit("ms").asi8
    close = prices.to_numpy(dtype=float)
    none = np.full(close.shape, np.nan)
    fields = {"open": close, "high": close, "low": close, "close": close, "rawClose": close, "volume": none, "rawVolume": none}
    have = ~np.isnan(close)
    first = np.where(have.any(axis=0), have.argmax(axis=0), len(labels))
    last = np.where(have.any(axis=0), len(labels) - 1 - have[::-1].argmax(axis=0), -1)
    rows = np.arange(len(labels))[:, None]
    columns = [str(column) for column in prices.columns]
    info = {"annualization": annualization, "instruments": [{"column": column, "instrumentId": column} for column in columns]}
    return W.Panel(labels, columns, fields, (rows >= first[None, :]) & (rows <= last[None, :]), info)


def factor(publisher, table, panel, settings, *, volumes=True):
    """The standard factor report, measured as the object page's Work measures a run: the run settings' delay, holding, leave-outs,
    preprocessing and groups, rank IC of the names with a value on both sides, its Newey–West t, the long–short book net of costs.
    `table` is the factor as dates by the panel's columns. Returns the headline numbers."""
    import numpy as np

    from . import factor_work as W
    settings = W.settings_of(settings)
    if not volumes:
        needs = [what for what, used in (("leaving out halted names ('leaveOut': {'halted': False})", settings["leaveOut"]["halted"]),
                                          ("leaving out illiquid names ('leaveOut': {'illiquid': False})", settings["leaveOut"]["illiquid"]),
                                          ("neutralizing by size ('neutralize': 'none' or 'industry')", settings["neutralize"] == "industry+size")) if used]
        if needs:
            raise ValueError("A prices table has no volumes, which " + "; ".join(needs) + " need. Leave prices out to use q.data(), or pass settings "
                             "that turn these off.")
    dates = table.index if table.index.tz is not None else table.index.tz_localize("UTC")
    table = table.set_axis(dates.tz_convert("UTC"), axis=0).reindex(index=panel.dates, columns=panel.columns)
    raw = table.to_numpy(dtype=float, copy=True)
    if not np.isfinite(raw).any():
        raise ValueError("The factor has no value on the prices' dates and assets: give it as a table of dates by the same assets.")
    evaluation = W.Evaluation(panel, raw, settings, formula=False, with_similar=False)
    numbers = evaluation.numbers
    headline = {key: numbers[key] for key in ("rankIc", "t", "longShortNet", "longShortGross", "sharpe", "turnover", "breakEven", "maxDrawdown", "names", "groupSize",
                                              "days", "holding", "groups")}
    publisher.index["evaluationConditions"] = {"report": "work", "settings": settings, "annualization": panel.annualization, "names": len(panel.columns),
                                               "sample": [panel.dates[0], panel.dates[-1]], "riskFree": numbers["riskFree"]["label"]}
    years = pd.DataFrame(evaluation.details["years"])
    groups = pd.DataFrame({"group": range(1, len(evaluation.details["groupMeans"]) + 1), "meanReturn": evaluation.details["groupMeans"]})
    checks = pd.DataFrame([{key: item.get(key) for key in ("name", "value", "need", "state", "title")} for item in evaluation.checks])
    sections = [publisher.table(pd.DataFrame([headline]), "factor/numbers", "Factor numbers"),
                publisher.table(groups, "factor/groups", "Mean return by group, a bar"),
                publisher.table(years, "factor/years", "By year"),
                publisher.table(checks, "factor/checks", "Checks")]
    if evaluation.decay is not None:
        sections.append(publisher.table(pd.DataFrame({"holding": range(1, len(evaluation.decay) + 1), "rankIc": evaluation.decay}), "factor/decay", "Rank IC by holding"))
    returns = pd.DataFrame({"longShortNet": evaluation.returns["net"], "longShortGross": evaluation.returns["gross"], "rankIc": evaluation.ic}, index=panel.dates.rename("date"))
    sections.append(publisher.table(returns, "factor/daily", "Long–short return and rank IC, a bar"))
    publisher.section("factor", "Factor", sections)
    publisher.index["metrics"] = headline
    publisher.index["units"].append({"id": "summary", "title": "Factor", "state": "completed", "metrics": {"input_samples": int(np.isfinite(raw).sum()), "valid_samples": int(np.isfinite(evaluation.after).sum())}})
    publisher.write()
    return headline


def model(publisher, recorder, task, prices=None):
    """The Qlib analysis of a trained, predicted recorder: signal quality, learning curves, importances and the research backtest."""
    from .models import publish_recorder
    publish_recorder(recorder, task, publisher, "result", "Model result")
    unit = next(item for item in publisher.index["units"] if item["id"] == "result")
    publisher.index["metrics"] = {key: value for key, value in unit["metrics"].items() if isinstance(value, (int, float))}
    publisher.write()
    return publisher.index["metrics"]


def allocation(publisher, portfolio):
    """The skfolio analysis of a Portfolio, MultiPeriodPortfolio or Population."""
    from . import portfolio_results
    portfolio_results.publish(publisher, portfolio, "portfolio", "Allocation")
    publisher.write()
    return publisher.index.get("metrics", {})
