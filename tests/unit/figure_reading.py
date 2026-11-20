"""What each Results and Work figure reads out when hovered, from the draft's tooltips (output/quant-design/factor.js, the `tooltip: tip({formatter ...})` of each
chart): the lines of the read-out and the number formats. A quantity with a sign (return, IC, alpha, difference) reads with + or -; one without (turnover, weight,
days, names) does not. `require` are regular expressions each found in the joined `hovertemplate`s of the figure; `forbid` are found in none."""
import re

SIGNED_PERCENT = r"%\{y:\+\.1%\}"

READOUT = {
    # the groups chart of Work and of Returns: the date is the title, every group and L-S net and the compared run read +%, one decimal
    "factor.groups": {"require": [r"Q1 " + SIGNED_PERCENT, r"L–S net " + SIGNED_PERCENT]},
    "factor.results.groups": {"require": [r"Q1 " + SIGNED_PERCENT, r"L–S net " + SIGNED_PERCENT]},
    "factor.decay": {"require": [r"Holding %\{x\}", r"Rank IC %\{y:\+\.4f\}"], "forbid": [r"Click to hold"]},
    "factor.results.mean-by-group": {"require": [r"%\{x\}", r"%\{y:\+\.1f\} bps"]},
    "factor.results.violin": {"require": [r"(?i)median", r"(?i)quartile|25%|75%", r"\+\.1f\} bps|:\+\.1f"]},
    "factor.results.factor-weighted": {"require": [SIGNED_PERCENT]},
    "factor.results.spread": {"require": [r"1-month mean", r"%\{y:\+\.1f\} bps"], "daily_skipped": True},
    "factor.results.ic-daily": {"require": [r"%\{y:\+\.3f\}", r"1-month mean"]},
    "factor.results.ic-histogram": {"require": [r"Days"]},
    "factor.results.ic-qq": {"require": [r"Normal %\{x:\+\.3f\}", r"Observed %\{y:\+\.3f\}"]},
    "factor.results.ic-years": {"require": [r"Rank IC %\{y:\+\.4f\}", r"t %\{customdata(\[\d\])?:\+\.2f\}"]},
    "factor.results.ic-monthly": {"require": [r"Mean rank IC %\{z:\+\.4f\}"]},
    "factor.results.ic-by-sector": {"require": [r"Rank IC %\{x:\+\.4f\}", r"Names %\{customdata(\[\d\])?[^}]*\}"]},
    "factor.results.decay-neutral": {"require": [r"Before %\{y:\+\.4f\}|before %\{y:\+\.4f\}", r"After %\{y:\+\.4f\}|after %\{y:\+\.4f\}"]},
    "factor.results.like": {"require": [r"%\{x:\+\.3f\}"]},
    "factor.results.turnover": {"require": [r"Top", r"Bottom", r"%\{y:\.1%\}"], "forbid": [r"%\{y:\+"]},
    "factor.results.rank-autocorrelation": {"require": [r"Rank autocorrelation %\{y:\.3f\}"], "forbid": [r"%\{y:\+"]},
    "factor.results.net-by-cost": {"require": [r"%\{x\} bps a trade", r"Long–short, net %\{y:\+\.2%\}"], "forbid": [r"a year.*a year"]},
    "factor.results.event": {"require": [r"%\{x\}", r"%\{y:\+\.2%\}"]},  # percent, 2 places, as the draft and the table under it
}


def templates(figure):
    return [trace.get("hovertemplate") or "" for trace in figure["data"]]


def check_reading(key, figure):
    """The problems in how this figure reads out, as a list of strings (empty when it reads like the draft)."""
    rule = READOUT[key]
    joined = " | ".join(templates(figure))
    problems = [f"{key}: no read-out line matching {pattern}" for pattern in rule.get("require", []) if not re.search(pattern, joined)]
    problems += [f"{key}: a read-out line matching {pattern}" for pattern in rule.get("forbid", []) if re.search(pattern, joined)]
    if rule.get("daily_skipped"):
        # the daily values and the band are drawn, but not read out: only the monthly mean is
        for trace in figure["data"]:
            if trace["meta"]["role"] == "detail" and trace.get("hoverinfo") != "skip":
                problems.append(f"{key}: the daily trace {trace['meta']['key']} is read out")
    return problems
