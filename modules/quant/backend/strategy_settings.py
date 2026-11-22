"""A strategy's run settings (the platform's layer, beside the code's own parameters): capital, exchange conditions, costs, fill timing,
shorting, benchmark, risk-free rate and the held-out end. The object keeps only what the user changed (`settings.run`); what a run uses is
that over defaults that depend on what is traded (perpetuals, spot, stocks)."""
from __future__ import annotations

import copy
import math
import re
from datetime import date, timedelta

from talyx_sdk import ContractError
from talyx_sdk.i18n import t

from ..native import metrics as M

CAPITAL = 100_000
EXCHANGE = {"leverage", "marginModel", "liquidation", "maintenancePct", "stopTrigger", "positionMode"}
COSTS = {"feePct", "makerPct", "commissionBps", "halfSpreadBps", "impact", "volumeCapPct", "funding", "borrowPct"}
TOP = {"capital", "maxPosition", "exchange", "costs", "fill", "shorting", "halted", "benchmark", "riskFree", "heldOutFrom", "limitThroughTicks", "accounts"}
BENCHMARKS = ("hold", "symbol", "equal", "none")
BALANCE = re.compile(r"^\d+(\.\d+)? [A-Za-z0-9]+$")
GIB = 1024 ** 3
FILLS = ("nextOpen", "nextClose")
RISK_FREE = ("irx", "zero", "fixed")


def number(value, label, *, low=0.0, high=None, strict=False):
    if type(value) not in (int, float) or not math.isfinite(value) or (value <= low if strict else value < low) or (high is not None and value > high):
        bound = f"above {low:g}" if strict else f"{low:g} or more"
        raise ContractError("invalid_input", (t("{label} needs a number {bound} and at most {high}.", label=label, bound=bound, high=f'{high:g}')
                if high is not None else t("{label} needs a number {bound}.", label=label, bound=bound)), {"field": label})
    return value


def check(run, limits=None):
    """What the user may set; null removes a value (the default applies again). Raises on anything else. `limits` are what Market says about this
    instrument: `{"maxLeverage": 100, "label": "BTC perpetual on OKX"}`."""
    if not isinstance(run, dict) or set(run) - TOP:
        raise ContractError("invalid_input", t("Run settings hold {settings}.", settings=', '.join(sorted(TOP))))
    out = {}
    for key, value in run.items():
        if value is None:
            continue
        if key == "capital":
            out[key] = number(value, "capital", strict=True)
        elif key == "maxPosition":
            out[key] = number(value, "maxPosition", strict=True, high=100)
        elif key == "limitThroughTicks":
            if type(value) is not int or value < 0 or value > 100:
                raise ContractError("invalid_input", t("limitThroughTicks is a whole number of price steps, 0 (fill on touch) to 100."))
            out[key] = value
        elif key == "accounts":
            out[key] = accounts_check(value)
        elif key == "exchange":
            if not isinstance(value, dict) or set(value) - EXCHANGE:
                raise ContractError("invalid_input", t("Exchange conditions hold {settings}.", settings=', '.join(sorted(EXCHANGE))))
            exchange = {}
            for name, item in value.items():
                if item is None:
                    continue
                if name == "leverage":
                    exchange[name] = number(item, "leverage", strict=True, high=200)
                    if limits and limits.get("maxLeverage") and item > limits["maxLeverage"]:
                        raise ContractError("invalid_input", t("Max {maxLeverage}× for {label} (Market)", maxLeverage=f"{limits['maxLeverage']:g}", label=limits.get('label', 'this instrument')), {"field": "leverage", **limits})
                elif name == "maintenancePct":
                    exchange[name] = number(item, "maintenancePct", strict=True, high=100)
                elif name == "liquidation":
                    if type(item) is not bool:
                        raise ContractError("invalid_input", t("liquidation is true or false."))
                    exchange[name] = item
                else:
                    allowed = {"marginModel": ("leveraged", "standard"), "stopTrigger": ("last", "mark", "index"), "positionMode": ("oneway", "hedge")}[name]
                    if item not in allowed:
                        raise ContractError("invalid_input", t("{name} is one of: {choices}.", name=name, choices=', '.join(allowed)))
                    exchange[name] = item
            out[key] = exchange
        elif key == "costs":
            if not isinstance(value, dict) or set(value) - COSTS:
                raise ContractError("invalid_input", t("Costs hold {settings}.", settings=', '.join(sorted(COSTS))))
            costs = {}
            for name, item in value.items():
                if item is None:
                    continue
                if name == "funding":
                    if type(item) is not bool:
                        raise ContractError("invalid_input", t("funding is true or false."))
                    costs[name] = item
                else:
                    costs[name] = number(item, name, high={"volumeCapPct": 100, "feePct": 5, "makerPct": 5, "commissionBps": 500, "halfSpreadBps": 1000,
                                                            "impact": 10, "borrowPct": 100}[name], strict=name == "volumeCapPct")
            out[key] = costs
        elif key == "fill":
            if value not in FILLS:
                raise ContractError("invalid_input", t("fill is nextOpen or nextClose: a signal decided on a bar's close fills on a later bar, never the same one."))
            out[key] = value
        elif key == "shorting":
            if value not in ("allowed", "off"):
                raise ContractError("invalid_input", t("shorting is allowed or off."))
            out[key] = value
        elif key == "halted":
            if value not in ("keep", "close"):
                raise ContractError("invalid_input", t("halted is keep (the position stays until the name trades again) or close."))
            out[key] = value
        elif key == "riskFree":
            if not isinstance(value, dict) or value.get("basis") not in RISK_FREE or set(value) - {"basis", "pct"}:
                raise ContractError("invalid_input", t("riskFree is {basis: irx (13-week T-bill), zero or fixed, pct}."))
            if value["basis"] == "fixed":
                out[key] = {"basis": "fixed", "pct": number(value.get("pct"), "riskFree.pct", low=-5, high=50)}
            else:
                out[key] = {"basis": value["basis"]}
        elif key == "benchmark":
            if not isinstance(value, dict) or value.get("kind") not in BENCHMARKS or set(value) - {"kind", "symbol"}:
                raise ContractError("invalid_input", t("benchmark is {kind: {kinds}, symbol}.", kinds=', '.join(BENCHMARKS)))
            if value["kind"] == "symbol":
                symbol = value.get("symbol")
                if not isinstance(symbol, str) or ":" not in symbol or len(symbol) > 120:
                    raise ContractError("invalid_input", t("A symbol benchmark names a Market symbol, e.g. US:SPY or CN:000300.SH."))
                out[key] = {"kind": "symbol", "symbol": symbol.strip()}
            else:
                out[key] = {"kind": value["kind"]}
        elif key == "heldOutFrom":
            out[key] = value
    return out


def accounts_check(value):
    """Each venue's starting balances: `[{venue, balances: ["10000 USDT", "0.5 BTC"]}]`."""
    if not isinstance(value, list) or not value or len(value) > 20:
        raise ContractError("invalid_input", t("accounts is a list of {venue, balances}, one per venue."))
    out = []
    for item in value:
        if not isinstance(item, dict) or set(item) != {"venue", "balances"} or not isinstance(item["venue"], str) or not item["venue"] \
                or not isinstance(item["balances"], list) or not item["balances"] or not all(isinstance(amount, str) and BALANCE.match(amount) and float(amount.split()[0]) > 0 for amount in item["balances"]):
            raise ContractError("invalid_input", t("An account is a venue and balances written like \"10000 USDT\"."))
        out.append({"venue": item["venue"], "balances": list(item["balances"])})
    return out


def traded(instruments):
    """What is traded: 'perpetual', 'spot' or 'stock' (the run mixes them only when the symbols do; stock rules win)."""
    kinds = {"stock" if item["assetClass"] in {"equity", "fund"} else "perpetual" if item["marketType"] == "swap" else "spot" for item in instruments}
    return "stock" if "stock" in kinds else "spot" if "spot" in kinds else "perpetual"


CURRENCY = {"perpetual": "USDT", "spot": "USDT", "stock": "USD"}
CAPITAL_BY = {"perpetual": 10_000, "spot": 10_000, "stock": 100_000}
EXCHANGE_FIELDS = {"perpetual": ["leverage", "marginModel", "liquidation", "maintenancePct", "stopTrigger", "positionMode"], "stock": ["margin"], "spot": []}


def exchange_fields(kind):
    """The exchange conditions that apply to what is traded; the others are not shown at all. A stock has one last price and one net position, so
    it has no stop trigger or position mode, and its margin is one read-only line."""
    return list(EXCHANGE_FIELDS["perpetual" if kind == "mixed" else kind])


def margin_label(kind):
    return "Reg T, 2× overnight" if kind == "stock" else None


# A stock strategy's benchmark by the market it trades, as JoinQuant and QuantConnect default theirs: the market's broad index (CSI 300 for
# A shares) or the fund that tracks it (SPY; for Hong Kong the Tracker Fund, as Market lists no Hang Seng index there).
MARKET_BENCHMARK = {"US": "US:SPY", "CN": "CN:000300.SH", "HK": "HK:02800.HK"}
# A stock strategy's risk-free rate by market: the 13-week T-bill (^IRX) where the cash is dollars (the Hong Kong dollar is pegged to it);
# A shares a fixed 4% a year, JoinQuant's default.
MARKET_RISK_FREE = {"US": {"basis": "irx"}, "CN": {"basis": "fixed", "pct": 4.0}, "HK": {"basis": "irx"}}
MARKET_CURRENCY = {"US": "USD", "CN": "CNY", "HK": "HKD"}  # the cash a stock account holds: the market's own (the venue is built in it)


def market_of(data):
    """The stock market (US, CN or HK) a strategy's data settings trade: the index's, else the first stock's or fund's; None when it trades none."""
    instruments = [data["index"]["instrument"]] if data.get("index") else [item["instrument"] for item in data.get("symbols") or []]
    return next((item["venueId"] for item in instruments if item["assetClass"] in {"equity", "fund", "index"}), None)


def benchmark_options(kind, names):
    """What the benchmark may be: any Market symbol (a stock strategy's default, its market's index), what is traded bought and held (several
    stocks: the members equally weighted), or nothing."""
    return ["symbol", "equal" if kind == "stock" and names > 1 else "hold", "none"]


def benchmark_default(kind, market):
    """Crypto holds what it trades; stocks are measured against their market's index."""
    return {"kind": "symbol", "symbol": MARKET_BENCHMARK[market]} if kind == "stock" else {"kind": "hold"}


def defaults(kind, account, fee=None, fee_source=None, commission=None, market=None):
    """What a run uses when the user set nothing, for what is traded. `fee` is Market's (or the account's) maker/taker rate, `commission` a stock
    commission, `market` the stock market traded (`market_of`): a stock strategy's benchmark and risk-free rate follow it."""
    base_kind = "perpetual" if kind == "mixed" else kind
    leverage = account.get("leverage", 1)
    base = {"capital": account.get("capital", CAPITAL_BY[base_kind]), "currency": MARKET_CURRENCY[market] if base_kind == "stock" and market else CURRENCY[base_kind], "maxPosition": 1, "fill": "nextOpen",
            "benchmark": benchmark_default(base_kind, market), "heldOutFrom": None, "limitThroughTicks": 0}
    if base_kind == "perpetual":
        return {**base, "exchange": {"leverage": leverage, "maxLeverage": None, "marginModel": "leveraged", "liquidation": True, "maintenancePct": 0.5,
                                     "stopTrigger": "last", "positionMode": "oneway"},
                "costs": {"feePct": pct(fee, "taker"), "makerPct": pct(fee, "maker"), "feeSource": fee_source or "from Market", "halfSpreadBps": 1.0,
                          "impact": M.IMPACT_COEFFICIENT, "volumeCapPct": None, "funding": True},
                "shorting": "allowed", "riskFree": {"basis": "zero"}}
    if base_kind == "spot":
        return {**base, "exchange": {"leverage": 1, "maxLeverage": 1, "marginModel": "standard", "liquidation": False, "maintenancePct": 100,
                                     "stopTrigger": "last", "positionMode": "oneway"},
                "costs": {"feePct": pct(fee, "taker"), "makerPct": pct(fee, "maker"), "feeSource": fee_source or "from Market", "halfSpreadBps": 1.0,
                          "impact": M.IMPACT_COEFFICIENT, "volumeCapPct": None},
                "shorting": "off", "riskFree": {"basis": "zero"}}
    rate = None if commission is None else float(commission.get("rate") or 0) * 1e4
    return {**base, "exchange": {"leverage": leverage, "maxLeverage": 2, "marginModel": "standard", "liquidation": True, "maintenancePct": 25,
                                 "stopTrigger": "last", "positionMode": "oneway"},
            "costs": {"commissionBps": rate, "feeSource": fee_source or "from Market", "halfSpreadBps": 2.0, "impact": M.IMPACT_COEFFICIENT,
                      "volumeCapPct": None, "borrowPct": M.DEFAULT_BORROW_PCT},
            "shorting": "allowed", "halted": "keep", "riskFree": dict(MARKET_RISK_FREE[market])}


def pct(fee, side):
    return None if not fee else round(float(fee[side]) * 100, 6)


def default_accounts(venues):
    """One account per venue with the default capital of what it trades; only when the run spans more than one venue."""
    return [{"venue": item["name"], "balances": [f"{CAPITAL_BY[item['kind']]} {CURRENCY[item['kind']]}"]} for item in venues]


def effective(kind, stored, account, fee=None, fee_source=None, commission=None, held_out=None, venues=None, market=None):
    """The run settings a run uses: the user's over the defaults. Options that do not apply to what is traded keep their default.
    `venues` ([{name, kind}]) is given when the run spans several venues: each gets its own account and the report currency is the first one's."""
    first = venues[0]["kind"] if kind == "mixed" and venues else kind
    value = copy.deepcopy(defaults(kind if kind != "mixed" else first, account, fee, fee_source, commission, market))
    stored = copy.deepcopy(stored or {})
    for key, item in stored.items():
        if key in {"exchange", "costs"}:
            value[key] = {**value[key], **item}
        else:
            value[key] = item
    if "heldOutFrom" not in stored:
        value["heldOutFrom"] = held_out
    exchange = value["exchange"]
    real = first
    if real == "spot":
        exchange.update(leverage=1, marginModel="standard", liquidation=False, stopTrigger="last", positionMode="oneway")
        value["shorting"] = "off"
    if real == "stock":
        exchange.update(stopTrigger="last", positionMode="oneway", marginModel="standard")
        value["costs"].pop("funding", None)
    if "feePct" in stored.get("costs", {}) or "makerPct" in stored.get("costs", {}) or "commissionBps" in stored.get("costs", {}):
        value["costs"]["feeSource"] = "set here"
    if venues and len(venues) > 1:
        value["accounts"] = stored.get("accounts") or default_accounts(venues)
        value["reportCurrency"] = value["accounts"][0]["balances"][0].split()[1]
    return value


def cut_data(data, run_kind):
    """The data a run reads: every run but the held-out validation stops the day before the held-out start, so nothing it computes has seen it."""
    held = data.get("heldOutFrom")
    if not held or run_kind == "heldout":
        return data
    until = (date.fromisoformat(held) - timedelta(days=1)).isoformat()
    if data.get("since") and until < data["since"]:
        # what to do, in the settings' own words (it read like a statement, with the internal key).
        def day(iso):
            found = date.fromisoformat(iso)
            return f"{found:%b} {found.day}, {found.year}"
        raise ContractError("invalid_input", t("Held out from {held} leaves no data to run on: the data start {since}. Move the held-out start after the data start, or start the data earlier.", held=day(held), since=day(data['since'])),
                            {"heldOutFrom": held, "since": data["since"]})
    return {**data, "until": until}


def memory_estimate(names, bars):
    """Bytes a run is expected to need: 0.3 GB for the process, 3 MB for each name (its instrument, sessions and state in the engine) and
    1 KB for each bar that goes into the engine (all names, fine bars included). Fitted to measured peaks (5 Hong Kong names over two years
    0.26 GB, CSI 300 members 1.4 GB, the S&P 500 over five and ten years 2.3 and 2.9 GB, the engine fed a year at a time), about a tenth
    over; a measured peak from an earlier run of the same data settings replaces this when there is one."""
    return int(.3 * GIB + names * 3 * 1024 ** 2 + bars * 1024)


def account_for(account, settings, kind):
    """The account the venue is built from: capital, leverage and (when set here) the fee come from the run settings."""
    out = {**account, "capital": settings["capital"], "leverage": settings["exchange"]["leverage"] if kind != "spot" else 1}
    costs = settings["costs"]
    if kind != "stock" and ("feePct" in costs and costs["feePct"] is not None) and costs.get("feeSource") == "set here":
        taker = costs["feePct"] / 100
        out["fee"] = {"taker": taker, "maker": (costs.get("makerPct") if costs.get("makerPct") is not None else costs["feePct"]) / 100}
    if kind == "stock" and costs.get("feeSource") == "set here" and costs.get("commissionBps") is not None:
        base = account.get("stockCommission") or {"perShare": 0, "minimum": 0, "maximumRate": None}
        out["stockCommission"] = {"perShare": base.get("perShare", 0), "minimum": base.get("minimum", 0), "maximumRate": base.get("maximumRate"),
                                  "rate": costs["commissionBps"] / 1e4}
    return out


def native_venue(venue, settings, kind):
    """Exchange conditions as the simulated venue: position mode, margin model, liquidation; a stock account that may short is a margin account."""
    exchange = settings["exchange"]
    venue = {**venue, "omsType": "HEDGING" if exchange["positionMode"] == "hedge" else "NETTING"}
    mine = next((item for item in settings.get("accounts") or [] if item["venue"] == venue.get("name")), None)
    if mine:
        # a venue's own balances; two currencies in one account make a multi-currency cash account with no base currency
        currencies = {amount.split()[1] for amount in mine["balances"]}
        venue.update(balances=list(mine["balances"]), baseCurrency=next(iter(currencies)) if len(currencies) == 1 else None)
    if kind == "perpetual":
        venue.update(marginModel=exchange["marginModel"], liquidation=bool(exchange["liquidation"]))
    if kind == "stock" and settings["shorting"] == "allowed":
        venue.update(accountType="MARGIN", marginModel="standard", liquidation=bool(exchange["liquidation"]))
        venue.setdefault("baseCurrency", venue["balances"][0].split()[1])
    return venue


def native_instrument(item, settings, kind):
    """Margin rates on the instrument: initial from the leverage, maintenance from the setting."""
    exchange = settings["exchange"]
    if kind == "perpetual" and item["type"] == "perpetual":
        return {**item, "marginInit": format(1 / exchange["leverage"], ".6f"), "marginMaint": format(exchange["maintenancePct"] / 100, ".6f")}
    if kind == "stock" and item["type"] == "equity" and settings["shorting"] == "allowed":
        return {**item, "marginInit": format(1 / exchange["leverage"], ".6f"), "marginMaint": format(exchange["maintenancePct"] / 100, ".6f")}
    return item


def invocation_settings(settings):
    """What the backtest process reads: the cost model and fill timing (`native/strategy_costs.py`), the stop trigger, the shorting rule."""
    costs = {key: value for key, value in settings["costs"].items() if key != "feeSource"}
    return {"capital": settings["capital"], "maxPosition": settings["maxPosition"], "fill": settings["fill"], "costs": costs,
            "limitThroughTicks": settings.get("limitThroughTicks", 0), "reportCurrency": settings.get("reportCurrency"), "accounts": settings.get("accounts"),
            "exchange": {key: value for key, value in settings["exchange"].items() if key != "notes"},
            "shorting": settings["shorting"], "halted": settings.get("halted", "keep")}


def changes(current, previous):
    """Which run settings differ between two runs: [{what, from, to}] over the flattened settings."""
    def flat(value, prefix=""):
        if isinstance(value, dict):
            return {key: item for name, part in value.items() if name != "notes" for key, item in flat(part, f"{prefix}.{name}" if prefix else name).items()}
        return {prefix: value}
    a, b = flat(current or {}), flat(previous or {})
    return [{"what": key, "from": b.get(key), "to": a.get(key)} for key in sorted(a.keys() | b.keys()) if a.get(key) != b.get(key)]
