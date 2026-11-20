"""What keeps a published allocation current: at each new bar the same period fit as the backtest's, on the data up to that bar, written as the version's next
weights. The research process that fits is the factor engine's open one (op `allocation_values`); the bars it reads are frozen fresh for each update."""
from __future__ import annotations

import asyncio
import shutil

import pandas as pd
from talyx_sdk import ContractError

from ..native.allocation_prices import daily_returns
from . import allocation_prep, library_after, research_data
from .allocation_runs import with_pool_market
from .library_produce import Made, UpdateFailure, day_of
from .library_summary import REPUBLISH
from .project_paths import filesystem_path

EVERY = {"weekly": "week", "monthly": "month", "quarterly": "quarter"}


class AllocationProducer:
    def __init__(self, ctx, library, engine):
        self.ctx, self.library, self.engine = ctx, library, engine

    async def make(self, version, *, through, after, now, attempt, due=None):
        meta = version["meta"]
        info = meta["info"]
        base = self.library.version_directory(version["id"])
        directory = base / "updates" / str(now)
        directory.mkdir(parents=True, exist_ok=True)
        try:
            if "risk_free" not in meta["allocation"]["settings"]:
                # 11-13 gave allocations a risk-free rate; a version published before then has none to fit and score with
                raise UpdateFailure("Published before allocations had a risk-free rate; publish it again from a new run.", {"code": REPUBLISH})
            data = {**meta["data"], "until": day_of(through), "since": day_of(info["since"])}
            data.pop("heldOutFrom", None)
            settings, pool = meta["allocation"]["settings"], meta["allocation"]["pool"]
            # the pool's proxies and exchange rates go into the bars as they did for the run and its publication: an update in another currency is converted too
            prepared, extras = await with_pool_market(self.ctx, data, settings, pool,
                                                      lambda wanted: research_data.prepare(self.ctx, wanted, "library-update:" + version["id"] + ":" + str(now), directory, strict=False, calendars="many"),
                                                      lambda ready: ready["research"]["info"])
            bars = pd.read_parquet(prepared["research"]["barsPath"])
            instruments = {item["column"]: item["instrumentId"] for item in info["instruments"]}
            present = {int(label) for label in bars["label"].unique()}
            labels = sorted(int(label) for label in bars["label"].unique() if (after if after is not None else -1) < int(label) <= through)
            held = await asyncio.to_thread(self.library.store.frame, version["id"], datetime=after, columns=["instrument", "field", "value"]) if after is not None else None
            by_column = {identity: column for column, identity in instruments.items()}
            last = None
            if held is not None and not held.empty:
                weights = {by_column.get(row.instrument, row.instrument): float(row.value) for row in held.itertuples() if row.field == "weight"}
                shares = {by_column.get(row.instrument, row.instrument): float(row.value) for row in held.itertuples() if row.field == "riskShare"}
                if not any(shares.values()):
                    # The publication keeps the weights at its cursor but not their risk shares (and updates made before this carried zeros forward): the shares
                    # it published belong to those weights. Zero shares read as every asset's risk gone and flagged a drift on each update until the next rebalance.
                    shares = {by_column.get(key, key): float(value) for key, value in ((meta.get("published") or {}).get("riskShares") or {}).items()}
                last = {"label": await asyncio.to_thread(self.last_rebalance, version["id"], after, weights, by_column), "weights": weights, "riskShares": shares}
            rate = await allocation_prep.risk_free(self.ctx, settings, min(present), max(present), f"library-update:{version['id']}:{now}")
            job = {"output": str(directory), "data": {"barsPath": prepared["research"]["barsPath"], "info": prepared["research"]["info"]},
                   "settings": {**settings, "pool": pool, **extras}, "labels": labels, "after": after, "last": last, "riskFree": rate,
                   "every": EVERY.get(meta["allocation"]["settings"].get("frequency", "monthly"), "month")}
            try:
                if meta["allocation"].get("mode") == "code":
                    source = str(base / "source")
                    job["code"] = {"source": source, "entry": meta["code"]["entry"], "roots": [source] + ([str(base / "common")] if meta["common"] else [])}
                    from .allocation_runs import WORKER
                    answer = await self.engine.run_once(job, directory / "engine", context={"versionId": version["id"]}, script=WORKER,
                                                        op="allocation_values", prefix="qallocation-values-", environment=meta["environment"])
                else:
                    answer = await self.engine.call("allocation_values", job, seconds=900)
            except ContractError as error:
                raise UpdateFailure("The allocation could not be fitted on the new data: " + error.message, {"code": error.code}) from error
            if not answer.get("ok"):
                raise UpdateFailure("The allocation could not be fitted on the new data: " + str((answer.get("failure") or {}).get("message") or answer.get("message")), {"code": "code_failed"})
            frame = pd.DataFrame(answer["rows"], columns=["datetime", "instrument", "field", "value"])
            frame["instrument"] = frame["instrument"].map(instruments)
            _, aligned = await asyncio.to_thread(daily_returns, bars, [row["ticker"] for row in pool], job["settings"])
            prices = aligned["closes"].rename(columns=instruments)
            prices.index = prices.index.as_unit("ms").asi8
            return Made(rows=frame[frame["instrument"].notna()], present=present, prices=prices, total=len(instruments),
                        risk_free=library_after.rate_per_bar(rate, present, info["annualization"]), solver={int(key): value for key, value in answer["solver"].items()})
        finally:
            shutil.rmtree(filesystem_path(directory), ignore_errors=True)

    def last_rebalance(self, version_id, after, weights, by_column):
        """The bar the weights the version holds took effect: the oldest of the run of bars that carry exactly them."""
        frame = self.library.store.frame(version_id, field="weight", columns=["datetime", "instrument", "value"]).sort_values("datetime")
        label = after
        for stamp, group in reversed(list(frame.groupby("datetime"))):
            current = {by_column.get(row.instrument, row.instrument): float(row.value) for row in group.itertuples()}
            if set(current) != set(weights) or any(abs(current[key] - weights[key]) > 1e-12 for key in current):
                break
            label = int(stamp)
        return label
